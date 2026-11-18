"""Branch review of a scheduled run (spec 021, research R11, contracts/branch-review.md).

The service part: scenarios 16 and 17, the derived branch and its `pending` row. The worker part
(scenarios 3 to 6, 19 and 20): the flow of a scheduled start against a fake Infrahub client that
keeps branches and proposed changes. Only the source read, the engine's plan and write, the live
schema read and the guard's PostgreSQL session are doubles, as in
`tests/service/test_sync_left_out_stop.py`; the admission, the stage, the commit and the product
store are the real ones.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest

pytest.importorskip("prefect")
pytest.importorskip("fastapi")
pytest.importorskip("opsmill_prefect_extras")
pytest.importorskip("psycopg")

from infrahub_sdk.exceptions import GraphQLError, ServerNotReachableError

from infrahub_sync.execution import RunResult
from infrahub_sync.plan.config_version import resolve_config_version
from infrahub_sync.plan.identity import canonical_identity, operation_id
from infrahub_sync.plan.models import LeftOutRecord, PlannedOperation, PlanProblem, PlanReport, SourceRecordReference
from infrahub_sync.plan.review import read_saved_plan
from infrahub_sync.plan.writer import write_plan_artifact
from infrahub_sync.product_store import AuditEvent, ProductRun, ProductStoreProviderError, RunFilters, ScheduleRecord
from infrahub_sync.runtime_schema import RuntimeModelPlan, RuntimeSideModels
from infrahub_sync.service import flow as service_flow
from infrahub_sync.service.schedule_admission import branch_review_branch, decide_start
from tests.service.execution_fixtures import stage_root, write_applied_sidecar
from tests.service.schedule_fixtures import OWNER_TOKEN, WORKER_ID, ScheduleApi, headers, package, schedule_api
from tests.service.test_managed_write_guard import SCHEMA_FINGERPRINT, _FakeGuardSession

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

T0 = datetime(2026, 3, 2, 2, tzinfo=timezone.utc)
SCHEDULE_ID = "s-0123456789abcdef0123456789abcdef"
CHECKSUM = "a" * 64


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ScheduleApi:
    return schedule_api(monkeypatch, tmp_path)


def _schedule(api: ScheduleApi, config_id: str | None = None) -> ScheduleRecord:
    """Store an active schedule named ``nightly`` with branch review, created just before ``T0``."""
    created = T0 - timedelta(minutes=30)
    record = ScheduleRecord.model_validate(
        {
            "schedule_id": SCHEDULE_ID,
            "name": "nightly",
            "config_id": config_id or api.config_id,
            "registry_version": 1,
            "cron": "0 2 * * *",
            "timezone": "UTC",
            "write_target": "branch-review",
            "state": "active",
            "owner": "owner",
            "created_at": created,
            "updated_at": created,
        }
    )
    api.projection.create_schedule(record)
    return record


def _start(monkeypatch: pytest.MonkeyPatch, api: ScheduleApi, start: datetime) -> object:
    """Run the service flow for the flow run of ``start``, with the real `sync` stage."""
    monkeypatch.setenv("INFRAHUB_SYNC_CREDENTIAL_TOKEN", "branch-review-token-canary")
    monkeypatch.setattr(service_flow, "_runtime", lambda: api.projection)
    monkeypatch.setattr(service_flow, "_run_logger", lambda: (logging.getLogger("test-branch-review"), False))
    monkeypatch.setenv("PREFECT__WORKER_ID", WORKER_ID)
    flow_run_id = str(uuid4())
    monkeypatch.setattr(service_flow, "_prefect_flow_run_id", lambda: flow_run_id)
    monkeypatch.setattr(
        service_flow,
        "_scheduled_flow_run",
        lambda *_args: service_flow.ScheduledFlowRun(auto_scheduled=True, expected_start_time=start),
    )
    monkeypatch.setattr(service_flow, "_now", lambda: start + timedelta(seconds=5))
    monkeypatch.setattr(service_flow, "collect_secret_values", lambda *_args, **_kwargs: [])
    return service_flow.service_sync_run.fn(stage="sync", schedule_id=SCHEDULE_ID)


def test_branch_review_is_refused_for_a_version_that_declares_a_destination_branch(api: ScheduleApi) -> None:
    """Scenario 16: the run would write to the declared branch with no review."""
    declared = api.projection.create_configuration(package("declared-branch", branch="sync-target")).config_id

    response = api.client.post(
        "/schedules",
        json=api.body(config_id=declared, write_target="branch-review"),
        headers=headers(OWNER_TOKEN),
    )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "schedule-branch-declared"


def test_a_start_of_branch_review_on_a_version_that_declares_a_branch_records_a_failed_start(
    monkeypatch: pytest.MonkeyPatch, api: ScheduleApi
) -> None:
    """A stored schedule that the API would refuse today creates no run either."""
    declared = api.projection.create_configuration(package("declared-branch", branch="sync-target")).config_id
    _schedule(api, declared)

    _start(monkeypatch, api, T0)

    assert api.projection.list_runs(RunFilters(schedule_id=SCHEDULE_ID)).items == ()
    (failed,) = [
        event for event in api.projection.audit_events() if event.event_type == "infrahub_sync.schedule.start_failed"
    ]
    assert failed.details is not None
    assert failed.details.cause == "schedule-branch-declared"


def test_the_branch_name_is_derived_from_the_schedule_and_the_start() -> None:
    """``sync/<name>-<YYYYMMDD>-<first 6 characters of sha256(schedule_id:start)>``, in UTC."""
    expected = "sync/nightly-20260302-adcace"

    assert branch_review_branch("nightly", SCHEDULE_ID, T0) == expected
    assert branch_review_branch("nightly", SCHEDULE_ID, T0.astimezone(timezone(timedelta(hours=1)))) == expected
    assert branch_review_branch("nightly", SCHEDULE_ID, T0 + timedelta(days=1)) != expected


def test_two_attempts_of_one_start_derive_the_same_branch(api: ScheduleApi) -> None:
    schedule = _schedule(api)
    now = T0 + timedelta(seconds=5)

    first = decide_start(api.projection, schedule, T0, now, timedelta(seconds=300))
    second = decide_start(api.projection, schedule, T0, now, timedelta(seconds=300))

    assert first.kind == second.kind == "started"
    assert first.run.destination_branch == second.run.destination_branch == "sync/nightly-20260302-adcace"  # ty: ignore[unresolved-attribute]
    assert first.run.run_id != second.run.run_id  # ty: ignore[unresolved-attribute]


@pytest.mark.usefixtures("infrahub")
def test_the_run_writes_to_the_derived_branch_and_has_a_branch_row(
    monkeypatch: pytest.MonkeyPatch, api: ScheduleApi
) -> None:
    _schedule(api)

    _start(monkeypatch, api, T0)

    (run,) = api.projection.list_runs(RunFilters(schedule_id=SCHEDULE_ID)).items
    assert run.destination_branch == "sync/nightly-20260302-adcace"
    row = api.projection.lookup_schedule_branch(run.run_id).value
    assert row is not None
    assert (row.schedule_id, row.branch) == (SCHEDULE_ID, "sync/nightly-20260302-adcace")


@pytest.mark.usefixtures("infrahub")
def test_the_sync_stage_of_a_run_with_branch_review_runs_the_branch_steps(
    monkeypatch: pytest.MonkeyPatch, api: ScheduleApi
) -> None:
    """The stage no longer refuses a run with branch review: it ends applied, with no failure record."""
    _schedule(api)

    _start(monkeypatch, api, T0)

    run = _only_run(api)
    assert (run.phase, run.outcome) == ("applied", "applied")
    assert "sync_failure" not in run.results
    assert not run.reconciliation_required


@pytest.mark.parametrize(
    ("action", "body"),
    [
        ("verify", {"reason": "check it"}),
        ("apply", {"expected_checksum": CHECKSUM, "confirm_writes": True}),
        ("resume", {"expected_checksum": CHECKSUM, "confirm_writes": True}),
    ],
)
def test_a_person_cannot_verify_apply_or_resume_a_run_with_branch_review(
    api: ScheduleApi, action: str, body: dict[str, Any]
) -> None:
    """Scenario 17: the next run of the schedule replaces the run."""
    schedule_id = api.create()["schedule_id"]
    run_id = api.scheduled_run(schedule_id, T0 + timedelta(days=400))

    response = api.client.post(f"/runs/{run_id}/{action}", json=body, headers=headers(OWNER_TOKEN))

    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "scheduled-run-not-appliable"
    assert "next run of its schedule replaces it" in error["message"]
    refused = [
        event
        for event in api.projection.audit_events()
        if event.run_id == run_id and event.outcome == "refused-scheduled-run-not-appliable"
    ]
    assert [event.operation for event in refused] == [action]


def test_a_scheduled_run_that_writes_to_the_default_branch_is_not_refused_as_a_branch_review_run(
    api: ScheduleApi,
) -> None:
    """The refusal follows the run's branch row: a run with no row goes on to the usual checks."""
    schedule_id = api.create(write_target="default-branch")["schedule_id"]
    run_id = api.scheduled_run(schedule_id, T0 + timedelta(days=400))

    response = api.client.post(f"/runs/{run_id}/verify", json={}, headers=headers(OWNER_TOKEN))

    assert response.json().get("error", {}).get("code") != "scheduled-run-not-appliable"


# --- The worker part (T031): the branch steps of a scheduled start against a fake Infrahub ------

_OPERATION = re.compile(r"^\s*(?:query|mutation)\s+(\w+)")
KIND = "BuiltinTag"
SITE = SourceRecordReference(kind="LocationSite", identity={"name": "paris"}, source_id="1")
DEVICE = SourceRecordReference(kind="DcimDevice", identity={"name": "core-1"}, source_id="7")


def _left_out_report() -> PlanReport:
    """Two records left out: one with a problem and one that depends on it."""
    return PlanReport(
        problems=(
            PlanProblem(
                problem_type="wrong_value_type",
                record=SITE,
                field="position",
                value=2.5,
                reason="LocationSite paris: position 2.5 is not an integer",
            ),
        ),
        left_out=(
            LeftOutRecord(record=SITE, cause="has_problem", problem_types=["wrong_value_type"], loaded=True),
            LeftOutRecord(
                record=DEVICE, cause="depends_on_left_out", depends_on=SITE, relationship="location", loaded=True
            ),
        ),
    )


def _operation(index: int) -> PlannedOperation:
    identity = canonical_identity({"name": f"tag-{index}"}, kind=KIND)
    return PlannedOperation(
        operation_id=operation_id("create", KIND, identity),
        action="create",
        kind=KIND,
        identity=identity,
        tier=0,
        payload={"name": f"tag-{index}"},
    )


@dataclass
class ProposedChange:
    """One proposed change that the fake Infrahub holds."""

    source_branch: str
    name: str
    description: str
    state: str = "open"
    approvers: int = 0
    comments: list[str] = field(default_factory=list)


@dataclass
class FakeInfrahub:  # pylint: disable=too-many-instance-attributes
    """A fake Infrahub client that keeps branches and proposed changes, and the plan and write doubles.

    ``events`` is one list for the guard, the Infrahub requests, the plan and the write, so a
    test reads the order of the steps from it. ``fail`` makes one GraphQL operation raise.
    """

    projection: Any
    events: list[str] = field(default_factory=list)
    branches: set[str] = field(default_factory=lambda: {"main"})
    default_branch: str = "main"
    proposed_changes: dict[str, ProposedChange] = field(default_factory=dict)
    fail: dict[str, BaseException] = field(default_factory=dict)
    requests: list[tuple[str, str | None]] = field(default_factory=list)
    operations: int = 2
    report: PlanReport | None = None
    apply_failure: BaseException | None = None
    confirm_left_out: list[object] = field(default_factory=list)
    phase_when_opened: list[tuple[str | None, str | None]] = field(default_factory=list)

    def execute_graphql(
        self, query: str, variables: dict | None = None, branch_name: str | None = None, **_: str | None
    ) -> dict[str, Any]:
        match = _OPERATION.match(query)
        assert match is not None, query
        operation = match.group(1)
        self.events.append(operation)
        self.requests.append((operation, branch_name))
        if operation in self.fail:
            raise self.fail[operation]
        handler: Callable[[dict[str, Any]], dict[str, Any]] = getattr(self, f"_{operation}")
        return handler(dict(variables or {}))

    def _SyncBranchList(self, _variables: dict[str, Any]) -> dict[str, Any]:  # noqa: N802 - named after the operation.
        return {"Branch": [{"name": name, "is_default": name == self.default_branch} for name in sorted(self.branches)]}

    def _SyncBranchCreate(self, variables: dict[str, Any]) -> dict[str, Any]:  # noqa: N802
        self.branches.add(variables["name"])
        return {"BranchCreate": {"ok": True}}

    def _SyncBranchDelete(self, variables: dict[str, Any]) -> dict[str, Any]:  # noqa: N802
        self.branches.discard(variables["name"])
        for change in self.proposed_changes.values():
            if change.source_branch == variables["name"] and change.state in {"open", "closed"}:
                change.state = "canceled"
        return {"BranchDelete": {"ok": True}}

    def _SyncProposedChangeCreate(self, variables: dict[str, Any]) -> dict[str, Any]:  # noqa: N802
        identifier = f"pc-{len(self.proposed_changes) + 1}"
        self.proposed_changes[identifier] = ProposedChange(
            source_branch=variables["source_branch"], name=variables["name"], description=variables["description"]
        )
        (run,) = [
            candidate
            for candidate in self.projection.list_runs(RunFilters(schedule_id=SCHEDULE_ID)).items
            if candidate.destination_branch == variables["source_branch"]
        ]
        stored = self.projection.lookup_run(run.run_id).value
        self.phase_when_opened.append((stored.phase, stored.prefect_executions[-1].terminal_state))
        return {"CoreProposedChangeCreate": {"ok": True, "object": {"id": identifier}}}

    def _SyncProposedChangeRead(self, variables: dict[str, Any]) -> dict[str, Any]:  # noqa: N802
        change = self.proposed_changes.get(variables["id"])
        if change is None:
            # A person deleted the proposed change: Infrahub answers with no edge.
            return {"CoreProposedChange": {"edges": []}}
        approvers = [{"node": {"id": f"account-{n}"}} for n in range(change.approvers)]
        node = {"id": variables["id"], "state": {"value": change.state}, "approved_by": {"edges": approvers}}
        return {"CoreProposedChange": {"edges": [{"node": node}]}}

    def _SyncChangeCommentCreate(self, variables: dict[str, Any]) -> dict[str, Any]:  # noqa: N802
        self.proposed_changes[variables["change"]].comments.append(variables["text"])
        return {"CoreChangeCommentCreate": {"ok": True}}

    def _SyncProposedChangeClose(self, variables: dict[str, Any]) -> dict[str, Any]:  # noqa: N802
        self.proposed_changes[variables["id"]].state = "closed"
        return {"CoreProposedChangeUpdate": {"ok": True}}


@pytest.fixture
def infrahub(monkeypatch: pytest.MonkeyPatch, api: ScheduleApi) -> FakeInfrahub:
    """Bind the fake Infrahub client and the plan and write doubles to the service flow."""
    return bind_fake_infrahub(monkeypatch, api)


def bind_fake_infrahub(monkeypatch: pytest.MonkeyPatch, api: ScheduleApi) -> FakeInfrahub:
    """Bind the fake Infrahub client and the plan and write doubles to the service flow, and return it."""
    fake = FakeInfrahub(api.projection)

    def build_models(**_kwargs: object) -> RuntimeModelPlan:
        fake.events.append("live-schema-read")
        return RuntimeModelPlan(
            branch="main",
            schema_fingerprint=SCHEMA_FINGERPRINT,
            destination=RuntimeSideModels(adapter_class=object, models={}),
            source=None,
        )

    def saved_plan(instance: Any, run_id: str, base: Path) -> Any:  # noqa: ANN401 - the engine's SavedPlan.
        return read_saved_plan(sync_name=instance.name, run_id=run_id, config=instance, base_directory=base)

    def fake_plan(instance: Any, **kwargs: Any) -> Any:  # noqa: ANN401 - the engine's SavedPlan.
        fake.events.append("plan")
        run_id, base = kwargs["run_id"], stage_root(kwargs)
        write_plan_artifact(
            run_dir=base / instance.name / run_id,
            run_id=run_id,
            config_version=resolve_config_version(instance),
            source_snapshot=[],
            deletes_computed=True,
            operations=[_operation(index) for index in range(fake.operations)],
            configuration_binding=instance._configuration_binding,
            schema_fingerprint=SCHEMA_FINGERPRINT,
            report=fake.report,
        )
        return saved_plan(instance, run_id, base)

    def fake_execute_run(instance: Any, **kwargs: Any) -> Any:  # noqa: ANN401 - the result follows the stage.
        operation = kwargs["operation"]
        fake.events.append(f"execute-run:{operation}")
        run_id, base = kwargs["run_id"], stage_root(kwargs)
        if operation == "verify":
            return saved_plan(instance, run_id, base)
        fake.confirm_left_out.append(kwargs.get("confirm_left_out"))
        if fake.apply_failure is not None:
            raise fake.apply_failure
        write_applied_sidecar(base / instance.name / run_id)
        return RunResult(
            sync_name=instance.name,
            operation="apply",
            run_id=run_id,
            status="applied",
            changed=True,
            summary={"create": fake.operations, "update": 0, "delete": 0},
            artifact_path=str(base / instance.name / run_id),
        )

    monkeypatch.setattr(service_flow, "build_runtime_model_plan", build_models)
    monkeypatch.setattr(service_flow, "_plan", fake_plan)
    monkeypatch.setattr(service_flow, "execute_run", fake_execute_run)
    monkeypatch.setattr(service_flow, "_publish_plan", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service_flow, "service_guard_session", lambda: _FakeGuardSession(fake.events))
    monkeypatch.setattr(service_flow, "service_guard_secrets", lambda: ())
    monkeypatch.setattr(service_flow.review_ops, "build_client", lambda _settings, _branch: fake)
    return fake


def _runs(api: ScheduleApi) -> list[ProductRun]:
    """The runs of the schedule, oldest first."""
    return sorted(api.projection.list_runs(RunFilters(schedule_id=SCHEDULE_ID)).items, key=lambda run: run.started_at)


def _only_run(api: ScheduleApi) -> ProductRun:
    (run,) = _runs(api)
    stored = api.projection.lookup_run(run.run_id).value
    assert stored is not None
    return stored


def _events(api: ScheduleApi, suffix: str) -> list[AuditEvent]:
    return [event for event in api.projection.audit_events() if event.event_type == f"infrahub_sync.schedule.{suffix}"]


def _row_state(api: ScheduleApi, run_id: str) -> str:
    row = api.projection.lookup_schedule_branch(run_id).value
    assert row is not None
    return row.state


def _two_starts(monkeypatch: pytest.MonkeyPatch, api: ScheduleApi) -> tuple[ProductRun, ProductRun]:
    """Run the starts of two nights; return both runs as stored."""
    _schedule(api)
    _start(monkeypatch, api, T0)
    _start(monkeypatch, api, T0 + timedelta(days=1))
    first, second = _runs(api)
    return _stored(api, first.run_id), _stored(api, second.run_id)


def _stored(api: ScheduleApi, run_id: str) -> ProductRun:
    stored = api.projection.lookup_run(run_id).value
    assert stored is not None
    return stored


def test_the_client_of_the_branch_steps_is_built_from_the_resolved_destination_settings(
    monkeypatch: pytest.MonkeyPatch, api: ScheduleApi
) -> None:
    """The real `build_client`, with no request: the address and token of the version, the run's branch."""
    monkeypatch.setenv("INFRAHUB_SYNC_CREDENTIAL_TOKEN", "branch-review-token-canary")
    schedule = _schedule(api)

    settings = service_flow._destination_settings(api.projection, schedule)
    client = service_flow.review_ops.build_client(settings, "sync/nightly-20260302-adcace")

    assert client.address == "https://infrahub.example"
    assert client.default_branch == "sync/nightly-20260302-adcace"
    assert client.config.api_token == "branch-review-token-canary"  # noqa: S105 - a test value.


def test_the_branch_is_created_before_the_plan_and_the_write_goes_to_it(
    monkeypatch: pytest.MonkeyPatch, api: ScheduleApi, infrahub: FakeInfrahub
) -> None:
    """Rules 1 and 6: the default branch is checked and the branch created under the guard, before the plan."""
    _schedule(api)

    _start(monkeypatch, api, T0)

    order = [
        "guard-acquired",
        "SyncBranchList",
        "SyncBranchCreate",
        "live-schema-read",
        "plan",
        "execute-run:verify",
        "execute-run:apply",
    ]
    positions = [infrahub.events.index(step) for step in order]
    assert positions == sorted(positions), infrahub.events
    assert "sync/nightly-20260302-adcace" in infrahub.branches
    assert ("SyncBranchCreate", "main") in infrahub.requests, "A branch request goes to main."
    run = _only_run(api)
    assert run.results["branch_review"]["branch"] == "sync/nightly-20260302-adcace"
    assert run.results["branch_review"]["branch_state"] == "written"


def test_the_proposed_change_is_opened_after_the_write_is_committed(
    monkeypatch: pytest.MonkeyPatch, api: ScheduleApi, infrahub: FakeInfrahub
) -> None:
    """Scenario 3: a branch named from the schedule holds the changes and an open proposed change targets main."""
    _schedule(api)

    _start(monkeypatch, api, T0)

    assert infrahub.phase_when_opened == [("applied", "completed")], "The proposed change follows the commit."
    run = _only_run(api)
    (change_id,) = infrahub.proposed_changes
    change = infrahub.proposed_changes[change_id]
    assert change.source_branch == "sync/nightly-20260302-adcace"
    assert change.name == "Scheduled sync nightly 2026-03-02 02:00 UTC"
    assert run.run_id in change.description
    assert f"configuration {api.config_id}, version 1" in change.description
    assert "Records to create: 2. Records to update: 0. Records left out: 0." in change.description
    assert run.results["branch_review"] == {
        "branch": "sync/nightly-20260302-adcace",
        "branch_state": "written",
        "proposed_change_id": change_id,
        "replaced": [],
        "kept": [],
        "failure": None,
    }
    row = api.projection.lookup_schedule_branch(run.run_id).value
    assert row is not None
    assert (row.state, row.proposed_change_id) == ("open", change_id)
    (opened,) = _events(api, "proposed_change_opened")
    assert (opened.run_id, opened.outcome, opened.operation, opened.actor) == (run.run_id, "opened", "sync", "owner")
    assert opened.details is not None
    assert (opened.details.branch, opened.details.proposed_change_id) == (row.branch, change_id)


def test_the_schedule_returns_its_open_branch(
    monkeypatch: pytest.MonkeyPatch, api: ScheduleApi, infrahub: FakeInfrahub
) -> None:
    """T035: ``open_branch`` of the schedule names the branch, the run and the proposed change."""
    _schedule(api)
    _start(monkeypatch, api, T0)
    run = _only_run(api)

    response = api.client.get(f"/schedules/{SCHEDULE_ID}", headers=headers(OWNER_TOKEN))

    assert response.status_code == 200, response.text
    assert response.json()["open_branch"] == {
        "run_id": run.run_id,
        "branch": "sync/nightly-20260302-adcace",
        "proposed_change_id": next(iter(infrahub.proposed_changes)),
    }


def test_a_plan_with_no_operation_deletes_the_branch_and_ends_with_no_change(
    monkeypatch: pytest.MonkeyPatch, api: ScheduleApi, infrahub: FakeInfrahub
) -> None:
    """Scenario 6 and FR-022: no branch and no proposed change of the run remain."""
    infrahub.operations = 0
    _schedule(api)

    _start(monkeypatch, api, T0)

    run = _only_run(api)
    assert (run.phase, run.outcome) == ("applied", "no-change")
    assert run.results["branch_review"] == {"branch": "sync/nightly-20260302-adcace", "branch_state": "removed"}
    assert infrahub.branches == {"main"}
    assert infrahub.proposed_changes == {}
    assert "execute-run:apply" not in infrahub.events
    assert infrahub.events.index("SyncBranchDelete") > infrahub.events.index("plan")
    assert _row_state(api, run.run_id) == "removed"


def test_a_plan_that_leaves_out_records_still_writes_the_others_and_says_so(
    monkeypatch: pytest.MonkeyPatch, api: ScheduleApi, infrahub: FakeInfrahub
) -> None:
    """Scenario 20: the left-out stop applies only to the default branch; the description states the count."""
    infrahub.report = _left_out_report()
    _schedule(api)

    _start(monkeypatch, api, T0)

    run = _only_run(api)
    assert (run.phase, run.outcome) == ("applied", "applied")
    assert infrahub.confirm_left_out == [True]
    (change,) = infrahub.proposed_changes.values()
    assert "Records left out: 2." in change.description
    assert f"infrahub-sync runs plan {run.run_id} --left-out" in change.description


def test_an_earlier_unapproved_proposed_change_is_closed_with_a_comment_and_its_branch_deleted(
    monkeypatch: pytest.MonkeyPatch, api: ScheduleApi, infrahub: FakeInfrahub
) -> None:
    """Scenario 4 and rule 7: the close, with the link to the new one, comes before the delete."""
    first, second = _two_starts(monkeypatch, api)

    old, new = infrahub.proposed_changes["pc-1"], infrahub.proposed_changes["pc-2"]
    # Closed, then canceled by the delete of its branch, as Infrahub does; the comment stays.
    assert old.state == "canceled"
    assert ("SyncProposedChangeClose", None) in infrahub.requests
    assert old.comments == [f"Replaced by pc-2 (run {second.run_id})"]
    assert new.state == "open"
    assert first.destination_branch not in infrahub.branches
    assert second.destination_branch in infrahub.branches
    after_open = infrahub.events[infrahub.events.index("SyncProposedChangeCreate", infrahub.events.index("plan", 5)) :]
    assert after_open.index("SyncProposedChangeClose") < after_open.index("SyncBranchDelete")
    row = api.projection.lookup_schedule_branch(first.run_id).value
    assert row is not None
    assert (row.state, row.replaced_by_run_id) == ("replaced", second.run_id)
    assert second.results["branch_review"]["replaced"] == [first.run_id]
    (replaced,) = _events(api, "proposed_change_replaced")
    assert replaced.run_id == second.run_id
    assert replaced.details is not None
    assert (replaced.details.replaced_run_id, replaced.details.proposed_change_id) == (first.run_id, "pc-1")
    assert [branch.run_id for branch in api.projection.schedule_branches(SCHEDULE_ID, states=("open",))] == [
        second.run_id
    ]


def test_an_approved_open_proposed_change_skips_the_start(
    monkeypatch: pytest.MonkeyPatch, api: ScheduleApi, infrahub: FakeInfrahub
) -> None:
    """Scenario 5 and FR-031: no run starts, and the proposed change and its branch stay."""
    _schedule(api)
    _start(monkeypatch, api, T0)
    first = _only_run(api)
    infrahub.proposed_changes["pc-1"].approvers = 1

    _start(monkeypatch, api, T0 + timedelta(days=1))

    assert [run.run_id for run in _runs(api)] == [first.run_id]
    (skipped,) = _events(api, "start_skipped")
    assert (skipped.outcome, skipped.run_id) == ("skipped-approved-change-open", first.run_id)
    assert skipped.details is not None
    assert skipped.details.start_at == T0 + timedelta(days=1)
    assert infrahub.proposed_changes["pc-1"].state == "open"
    assert first.destination_branch in infrahub.branches
    assert _row_state(api, first.run_id) == "open"
    assert ("SyncProposedChangeRead", "main") in infrahub.requests, "The read before the run's branch goes to main."


@pytest.mark.parametrize(("state", "outcome"), [("merged", "kept-merged"), ("closed", "kept-closed")])
def test_a_proposed_change_that_a_person_merged_or_closed_is_kept_and_the_start_runs(
    monkeypatch: pytest.MonkeyPatch, api: ScheduleApi, infrahub: FakeInfrahub, state: str, outcome: str
) -> None:
    """Scenario 19: a run starts and the schedule does not touch the merged or closed proposed change."""
    _schedule(api)
    _start(monkeypatch, api, T0)
    first = _only_run(api)
    infrahub.proposed_changes["pc-1"].state = state
    infrahub.events.clear()

    _start(monkeypatch, api, T0 + timedelta(days=1))

    _, second = _runs(api)
    assert _row_state(api, first.run_id) == "kept"
    assert _row_state(api, second.run_id) == "open"
    assert infrahub.proposed_changes["pc-1"].state == state
    assert infrahub.proposed_changes["pc-1"].comments == []
    assert first.destination_branch in infrahub.branches
    assert "SyncProposedChangeClose" not in infrahub.events
    assert "SyncBranchDelete" not in infrahub.events
    (kept,) = _events(api, "proposed_change_kept")
    assert (kept.outcome, kept.run_id) == (outcome, second.run_id)


def test_a_proposed_change_approved_while_the_run_was_writing_is_kept(
    monkeypatch: pytest.MonkeyPatch, api: ScheduleApi, infrahub: FakeInfrahub
) -> None:
    """FR-021: an approval that arrives after the admission read leaves the proposed change and its branch."""
    _schedule(api)
    _start(monkeypatch, api, T0)
    first = _only_run(api)

    def approve_then_create(variables: dict[str, Any]) -> dict[str, Any]:
        infrahub.proposed_changes["pc-1"].approvers = 1
        return FakeInfrahub._SyncBranchCreate(infrahub, variables)

    monkeypatch.setattr(infrahub, "_SyncBranchCreate", approve_then_create)

    _start(monkeypatch, api, T0 + timedelta(days=1))

    _, second = _runs(api)
    stored = _stored(api, second.run_id)
    assert stored.results["branch_review"]["kept"] == [{"run_id": first.run_id, "cause": "approved"}]
    assert _row_state(api, first.run_id) == "open"
    assert infrahub.proposed_changes["pc-1"].state == "open"
    assert first.destination_branch in infrahub.branches
    (kept,) = _events(api, "proposed_change_kept")
    assert kept.outcome == "kept-approved"


def _delete_proposed_change(infrahub: FakeInfrahub, identifier: str) -> None:
    """Delete a proposed change as a person does; the fake keeps counting, so no identifier is used twice."""
    infrahub.proposed_changes[f"deleted-{identifier}"] = infrahub.proposed_changes.pop(identifier)


def test_a_proposed_change_that_a_person_deleted_is_kept_and_the_start_runs(
    monkeypatch: pytest.MonkeyPatch, api: ScheduleApi, infrahub: FakeInfrahub
) -> None:
    """A missing proposed change must not fail every later start of the schedule."""
    _schedule(api)
    _start(monkeypatch, api, T0)
    first = _only_run(api)
    _delete_proposed_change(infrahub, "pc-1")
    infrahub.events.clear()

    _start(monkeypatch, api, T0 + timedelta(days=1))

    _, second = _runs(api)
    assert _row_state(api, first.run_id) == "kept"
    assert _row_state(api, second.run_id) == "open"
    assert first.destination_branch in infrahub.branches
    assert "SyncBranchDelete" not in infrahub.events
    assert _events(api, "start_failed") == []
    (kept,) = _events(api, "proposed_change_kept")
    assert (kept.outcome, kept.run_id) == ("kept-missing", second.run_id)


def test_a_proposed_change_deleted_while_the_run_was_writing_is_kept(
    monkeypatch: pytest.MonkeyPatch, api: ScheduleApi, infrahub: FakeInfrahub
) -> None:
    _schedule(api)
    _start(monkeypatch, api, T0)
    first = _only_run(api)

    def delete_then_create(variables: dict[str, Any]) -> dict[str, Any]:
        _delete_proposed_change(infrahub, "pc-1")
        return FakeInfrahub._SyncBranchCreate(infrahub, variables)

    monkeypatch.setattr(infrahub, "_SyncBranchCreate", delete_then_create)

    _start(monkeypatch, api, T0 + timedelta(days=1))

    _, second = _runs(api)
    stored = _stored(api, second.run_id)
    assert stored.results["branch_review"]["kept"] == [{"run_id": first.run_id, "cause": "missing"}]
    assert stored.results["branch_review"]["failure"] is None
    assert _row_state(api, first.run_id) == "kept"
    assert first.destination_branch in infrahub.branches
    (kept,) = _events(api, "proposed_change_kept")
    assert kept.outcome == "kept-missing"


@pytest.mark.usefixtures("infrahub")
def test_a_store_error_after_the_write_still_ends_the_run_and_publishes_its_summary(
    monkeypatch: pytest.MonkeyPatch, api: ScheduleApi, caplog: pytest.LogCaptureFixture
) -> None:
    """Steps 5 and 6 follow the commit of the write: no failure there may fail the flow run."""
    _schedule(api)
    published: list[str] = []
    publish = service_flow._publish_run_summary

    def recorded(projection: Any, run_id: str, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401
        published.append(run_id)
        publish(projection, run_id, *args, **kwargs)

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise ProductStoreProviderError

    monkeypatch.setattr(service_flow, "_publish_run_summary", recorded)
    monkeypatch.setattr(api.projection, "record_branch_review_outcome", refuse)

    with caplog.at_level(logging.ERROR, logger=service_flow.__name__):
        result = _start(monkeypatch, api, T0)

    run = _only_run(api)
    assert isinstance(result, dict)
    assert (run.phase, run.outcome) == ("applied", "applied")
    assert published == [run.run_id]
    (logged,) = [record for record in caplog.records if "branch_review.outcome_not_recorded" in record.getMessage()]
    assert logged.levelno == logging.ERROR
    assert "proposed_change_id=pc-1" in logged.getMessage()
    assert "error_class=ProductStoreProviderError" in logged.getMessage()


def test_an_unexpected_error_of_the_proposed_change_step_is_recorded_and_ends_the_run(
    monkeypatch: pytest.MonkeyPatch, api: ScheduleApi, infrahub: FakeInfrahub
) -> None:
    _schedule(api)
    infrahub.fail["SyncProposedChangeCreate"] = KeyError("Branch")

    result = _start(monkeypatch, api, T0)

    run = _only_run(api)
    assert isinstance(result, dict)
    assert (run.phase, run.outcome) == ("applied", "applied")
    assert run.results["branch_review"]["failure"] == "internal_error"
    (failed,) = _events(api, "proposed_change_failed")
    assert failed.details is not None
    assert failed.details.cause == "internal_error"


def test_the_pending_branch_of_a_failed_run_is_deleted_by_the_next_successful_run(
    monkeypatch: pytest.MonkeyPatch, api: ScheduleApi, infrahub: FakeInfrahub
) -> None:
    """A failed run replaces nothing; its branch stays `pending` until a later run succeeds."""
    _schedule(api)
    infrahub.apply_failure = RuntimeError("the engine stopped before its first write")
    with pytest.raises(RuntimeError):
        _start(monkeypatch, api, T0)
    failed = _only_run(api)
    assert (failed.phase, failed.outcome) == ("sync-failed", "failed")
    assert _row_state(api, failed.run_id) == "pending"
    assert failed.destination_branch in infrahub.branches
    assert infrahub.proposed_changes == {}

    infrahub.apply_failure = None
    _start(monkeypatch, api, T0 + timedelta(days=1))

    _, second = _runs(api)
    assert failed.destination_branch not in infrahub.branches
    row = api.projection.lookup_schedule_branch(failed.run_id).value
    assert row is not None
    assert (row.state, row.replaced_by_run_id) == ("replaced", second.run_id)
    (replaced,) = _events(api, "proposed_change_replaced")
    assert replaced.details is not None
    assert (replaced.details.replaced_run_id, replaced.details.proposed_change_id) == (failed.run_id, None)


def _graphql_error() -> GraphQLError:
    return GraphQLError(errors=[{"message": "infrahub-said-this"}], query="mutation { x }", variables={})


def test_a_failure_to_open_the_proposed_change_keeps_the_write_and_replaces_nothing(
    monkeypatch: pytest.MonkeyPatch, api: ScheduleApi, infrahub: FakeInfrahub
) -> None:
    _schedule(api)
    _start(monkeypatch, api, T0)
    first = _only_run(api)
    infrahub.fail["SyncProposedChangeCreate"] = _graphql_error()

    _start(monkeypatch, api, T0 + timedelta(days=1))

    _, second = _runs(api)
    stored = _stored(api, second.run_id)
    assert (stored.phase, stored.outcome) == ("applied", "applied"), "The write verdict does not change."
    review = stored.results["branch_review"]
    assert (review["proposed_change_id"], review["failure"], review["replaced"]) == (
        None,
        "proposed_change_open_failed",
        [],
    )
    assert _row_state(api, second.run_id) == "pending"
    assert _row_state(api, first.run_id) == "open"
    assert infrahub.proposed_changes["pc-1"].state == "open"
    (failed,) = _events(api, "proposed_change_failed")
    assert (failed.outcome, failed.run_id) == ("failed", second.run_id)
    assert failed.details is not None
    assert failed.details.cause == "proposed_change_open_failed"
    assert "infrahub-said-this" not in api.stored_text() + str(stored.results)


def test_a_failure_to_replace_an_earlier_branch_keeps_the_write_and_records_the_class(
    monkeypatch: pytest.MonkeyPatch, api: ScheduleApi, infrahub: FakeInfrahub
) -> None:
    _schedule(api)
    infrahub.operations = 2
    _start(monkeypatch, api, T0)
    first = _only_run(api)
    infrahub.fail["SyncProposedChangeClose"] = ServerNotReachableError(address="https://infrahub.example")

    _start(monkeypatch, api, T0 + timedelta(days=1))

    _, second = _runs(api)
    stored = _stored(api, second.run_id)
    assert (stored.phase, stored.outcome) == ("applied", "applied")
    assert stored.results["branch_review"]["failure"] == "system_unreachable"
    assert stored.results["branch_review"]["proposed_change_id"] == "pc-2"
    assert _row_state(api, second.run_id) == "open"
    assert _row_state(api, first.run_id) == "open", "The next successful run replaces it."
    assert first.destination_branch in infrahub.branches
    (failed,) = _events(api, "proposed_change_failed")
    assert failed.details is not None
    assert (failed.details.cause, failed.details.branch) == ("system_unreachable", first.destination_branch)


def test_infrahub_out_of_reach_before_the_admission_records_a_failed_start(
    monkeypatch: pytest.MonkeyPatch, api: ScheduleApi, infrahub: FakeInfrahub
) -> None:
    """R11: the earlier proposed changes cannot be read, so the start is recorded as failed with no run."""
    _schedule(api)
    _start(monkeypatch, api, T0)
    first = _only_run(api)
    infrahub.fail["SyncProposedChangeRead"] = ServerNotReachableError(address="https://infrahub.example")

    _start(monkeypatch, api, T0 + timedelta(days=1))

    assert [run.run_id for run in _runs(api)] == [first.run_id]
    (failed,) = _events(api, "start_failed")
    assert (failed.outcome, failed.run_id) == ("failed", None)
    assert failed.details is not None
    assert (failed.details.cause, failed.details.start_at) == ("system_unreachable", T0 + timedelta(days=1))
    assert _row_state(api, first.run_id) == "open"


def test_a_destination_whose_default_branch_is_not_main_fails_before_the_branch_is_created(
    monkeypatch: pytest.MonkeyPatch, api: ScheduleApi, infrahub: FakeInfrahub
) -> None:
    """Rule 6: Infrahub accepts a proposed change only to main."""
    infrahub.default_branch = "develop"
    _schedule(api)

    with pytest.raises(RuntimeError):
        _start(monkeypatch, api, T0)

    run = _only_run(api)
    assert (run.phase, run.outcome) == ("sync-failed", "failed")
    assert run.results["sync_failure"]["failure_type"] == "destination_default_branch_not_main"
    assert "SyncBranchCreate" not in infrahub.events
    assert "plan" not in infrahub.events
    assert _row_state(api, run.run_id) == "pending"
