"""Neutral HTTP contract and server projection tests."""

from __future__ import annotations

import ast
from datetime import datetime, timezone
from importlib.util import resolve_name
from pathlib import Path

import pytest
from pydantic import ValidationError

from infrahub_sync.client.models import (
    ApplyRunRequest,
    ApproveRunRequest,
    CancelRunRequest,
    ChangeScheduleRequest,
    ConfigMutationRequest,
    CreateRunRequest,
    CreateScheduleRequest,
    EventDetailsResource,
    EventPage,
    EventPrimaryResource,
    EventRelatedResource,
    EventResource,
    OrchestrationSummary,
    PackageCheckRequest,
    PublicRunResource,
    RunApprovalResource,
    RunResource,
    ScheduleActionRequest,
    ScheduleOpenBranchResource,
    SchedulePage,
    ScheduleResource,
    VerifyRunRequest,
)
from infrahub_sync.product_store.models import PrefectExecutionLink, ProductRun, RunApproval
from infrahub_sync.service.models import public_run_resource


def _package_imports(root: Path, package: str) -> set[str]:
    imports: set[str] = set()
    for path in root.rglob("*.py"):
        relative = path.relative_to(root).with_suffix("")
        module_parts = [*package.split("."), *relative.parts]
        if module_parts[-1] == "__init__":
            module_parts.pop()
        current_package = ".".join(module_parts if path.stem == "__init__" else module_parts[:-1])
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported = node.module or ""
                if node.level:
                    imported = resolve_name(f"{'.' * node.level}{imported}", current_package)
                imports.add(imported)
    return imports


def test_client_package_imports_no_product_or_service_module() -> None:
    imports = _package_imports(Path("infrahub_sync/client"), "infrahub_sync.client")

    assert not {name for name in imports if name.startswith("infrahub_sync.product_store")}
    assert not {name for name in imports if name.startswith("infrahub_sync.service")}
    assert not {name for name in imports if name.startswith("infrahub_sync.adapters")}
    assert not {name for name in imports if name.startswith("infrahub_sync.execution")}
    assert not {
        name for name in imports if name.partition(".")[0] in {"boto3", "fastapi", "prefect", "psycopg", "uvicorn"}
    }


def test_client_import_scan_resolves_nested_relative_imports(tmp_path: Path) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "probe.py").write_text(
        "from ...product_store.models import ProductRun\n",
        encoding="utf-8",
    )

    assert "infrahub_sync.product_store.models" in _package_imports(tmp_path, "infrahub_sync.client")


@pytest.mark.parametrize(
    "field",
    [
        "submitted_at",
        "claimed_at",
        "stalled_at",
        "cancellation_requested_at",
        "cancellation_recovery_deadline_at",
        "cancellation_acknowledged_at",
        "terminal_at",
    ],
)
def test_orchestration_timestamps_require_a_timezone(field: str) -> None:
    payload: dict[str, object] = {
        "flow_run_id": "flow-1",
        "purpose": "plan",
        "attempt": 1,
        "state": "pending",
        "detail_available": True,
        "unavailable_reason": None,
        "submitted_at": None,
        "claimed_at": None,
        "stalled_at": None,
        "cancellation_requested_at": None,
        "cancellation_recovery_deadline_at": None,
        "cancellation_acknowledged_at": None,
        "terminal_at": None,
        "terminal_state": None,
        "terminal_outcome": None,
    }
    payload[field] = "2026-08-30T12:00:00"
    if field == "terminal_at":
        payload["terminal_state"] = "completed"
        payload["terminal_outcome"] = "succeeded"

    with pytest.raises(ValidationError, match="execution timestamps must include a timezone"):
        OrchestrationSummary.model_validate(payload)


def test_server_projects_store_run_into_a_self_contained_resource() -> None:
    now = datetime.now(timezone.utc)
    stored = ProductRun(
        run_id="run-1",
        operation="plan",
        configuration_reference="cfg@1",
        config_id="cfg",
        registry_version=1,
        package_checksum="a" * 64,
        started_at=now,
        phase="accepted",
        prefect_executions=(
            PrefectExecutionLink(
                flow_run_id="flow-1",
                purpose="plan",
                attempt=1,
                submitted_at=now,
                claiming_worker_id=None,
            ),
        ),
    )

    projected = public_run_resource(stored)

    assert isinstance(projected, PublicRunResource)
    assert not issubclass(PublicRunResource, ProductRun)
    assert projected.run_id == stored.run_id
    assert projected.prefect_executions[0].model_dump() == {
        "flow_run_id": "flow-1",
        "deployment_id": None,
        "purpose": "plan",
        "attempt": 1,
        "last_observed_state": None,
        "last_observed_at": None,
    }


_REQUEST_FIELDS: dict[type, dict[str, object]] = {
    CreateRunRequest: {"config_id": "cfg", "registry_version": 1},
    VerifyRunRequest: {},
    ApplyRunRequest: {"expected_checksum": "a" * 64, "confirm_writes": True},
    ApproveRunRequest: {"expected_checksum": "a" * 64},
    CancelRunRequest: {},
    ConfigMutationRequest: {"package": {"format_version": 1}},
    CreateScheduleRequest: {"name": "nightly", "config_id": "cfg", "registry_version": 1, "cron": "0 2 * * *"},
    ChangeScheduleRequest: {"cron": "0 3 * * *"},
    ScheduleActionRequest: {},
}


@pytest.mark.parametrize("model", list(_REQUEST_FIELDS), ids=lambda model: model.__name__)
def test_a_request_without_a_reason_sends_null(model: type) -> None:
    """The audit reason is optional; an omitted reason is sent as null, never as made-up text."""
    omitted = model(**_REQUEST_FIELDS[model])
    explicit_null = model(**_REQUEST_FIELDS[model], reason=None)

    assert omitted.reason is None
    assert omitted.model_dump(mode="json")["reason"] is None
    assert explicit_null == omitted


@pytest.mark.parametrize("model", list(_REQUEST_FIELDS), ids=lambda model: model.__name__)
def test_a_present_reason_must_not_be_empty(model: type) -> None:
    with pytest.raises(ValidationError):
        model(**_REQUEST_FIELDS[model], reason="")


@pytest.mark.parametrize(
    "reason",
    [
        pytest.param(" leading space", id="untrimmed"),
        pytest.param("line\nbreak", id="unprintable"),
        pytest.param("x" * 513, id="too-long"),
    ],
)
def test_a_present_configuration_reason_keeps_its_validation(reason: str) -> None:
    with pytest.raises(ValidationError):
        ConfigMutationRequest(package={"format_version": 1}, reason=reason)


def test_a_valid_configuration_reason_is_kept_unchanged() -> None:
    request = ConfigMutationRequest(package={"format_version": 1}, reason="register inventory")

    assert request.reason == "register inventory"


def test_a_configuration_request_carries_the_unvalidated_mark_and_defaults_it_to_false() -> None:
    """The web interface sets the mark when it registers without validation (spec 025, US4)."""
    default = ConfigMutationRequest(package={"format_version": 1})
    marked = ConfigMutationRequest(package={"format_version": 1}, registered_without_validation=True)

    assert default.registered_without_validation is False
    # An unmarked request keeps the body a Sync API without the field accepts.
    assert "registered_without_validation" not in default.model_dump(mode="json")
    assert marked.model_dump(mode="json")["registered_without_validation"] is True
    assert (
        ConfigMutationRequest.model_validate({"package": {"format_version": 1}, "registered_without_validation": True})
        == marked
    )
    with pytest.raises(ValidationError):
        ConfigMutationRequest(package={"format_version": 1}, registered_without_validation="yes")  # ty: ignore[invalid-argument-type]


def test_a_configuration_request_with_a_package_keeps_its_body() -> None:
    """The CLI sends `package`; the body has no `document` key, so a Sync API without it accepts it."""
    request = ConfigMutationRequest(package={"format_version": 1}, reason="register inventory")

    assert request.model_dump(mode="json") == {"package": {"format_version": 1}, "reason": "register inventory"}


def test_a_configuration_request_sends_the_text_of_a_package_file_unchanged() -> None:
    """The web interface sends the file's text (spec 025, D-12); whitespace and numbers are kept."""
    document = "  format_version: 1\nconfiguration:\n  timeout: 30.0\n"

    request = ConfigMutationRequest(document=document, registered_without_validation=True)

    assert request.model_dump(mode="json") == {
        "document": document,
        "reason": None,
        "registered_without_validation": True,
    }


def test_the_configuration_request_leaves_the_exactly_one_rule_to_the_sync_api() -> None:
    """The model is shared with the Sync API, which refuses both or neither with `400 request`."""
    assert ConfigMutationRequest().model_dump(mode="json") == {"reason": None}
    both = ConfigMutationRequest(package={"format_version": 1}, document="format_version: 1\n")
    assert set(both.model_dump(mode="json")) == {"package", "document", "reason"}


def _public_run(**extra: object) -> dict[str, object]:
    return {
        "run_id": "run-1",
        "operation": "plan",
        "configuration_reference": "cfg@1",
        "started_at": datetime(2026, 11, 10, 12, tzinfo=timezone.utc).isoformat(),
        "phase": "planned",
        **extra,
    }


_APPROVAL = {
    "approved_by": "alice",
    "approved_at": "2026-11-10T14:03:11Z",
    "plan_checksum": "a" * 64,
    "reason": "reviewed by the network team",
}


def test_a_run_resource_without_an_approval_reads_none() -> None:
    resource = RunResource.model_validate({"run": _public_run(), "orchestration": []})

    assert resource.run.approval is None
    assert PublicRunResource.model_validate(_public_run(approval=None)).approval is None


def test_a_run_resource_carries_its_approval() -> None:
    resource = RunResource.model_validate({"run": _public_run(approval=_APPROVAL), "orchestration": []})

    approval = resource.run.approval
    assert isinstance(approval, RunApprovalResource)
    assert approval.approved_by == "alice"
    assert approval.approved_at == datetime(2026, 11, 10, 14, 3, 11, tzinfo=timezone.utc)
    assert approval.plan_checksum == "a" * 64
    assert approval.reason == "reviewed by the network team"


@pytest.mark.parametrize(
    "override",
    [{"approved_by": ""}, {"approved_at": "2026-11-10T14:03:11"}, {"plan_checksum": "A" * 64}],
    ids=["empty-actor", "naive-timestamp", "uppercase-checksum"],
)
def test_an_invalid_approval_is_refused(override: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        PublicRunResource.model_validate(_public_run(approval={**_APPROVAL, **override}))


def test_the_server_projects_a_stored_approval_into_the_run_resource() -> None:
    stored = ProductRun(
        run_id="run-1",
        operation="plan",
        configuration_reference="cfg@1",
        started_at=datetime(2026, 11, 10, 12, tzinfo=timezone.utc),
        phase="planned",
        approval=RunApproval(
            approved_by="alice",
            approved_at=datetime(2026, 11, 10, 14, 3, 11, tzinfo=timezone.utc),
            plan_checksum="a" * 64,
        ),
    )

    projected = public_run_resource(stored)

    assert projected.approval is not None
    assert projected.approval.model_dump(mode="json") == {
        "approved_by": "alice",
        "approved_at": "2026-11-10T14:03:11Z",
        "plan_checksum": "a" * 64,
        "reason": None,
    }


def test_approve_request_refuses_unknown_fields_and_a_malformed_checksum() -> None:
    assert ApproveRunRequest(expected_checksum="a" * 64).model_dump(mode="json") == {
        "expected_checksum": "a" * 64,
        "reason": None,
    }
    with pytest.raises(ValidationError):
        ApproveRunRequest.model_validate({"expected_checksum": "a" * 64, "confirm_writes": True})
    with pytest.raises(ValidationError):
        ApproveRunRequest(expected_checksum="not-a-checksum")


def test_an_event_page_reads_a_newer_server_and_keeps_its_paging_fields() -> None:
    """The client tolerates fields and resource kinds a newer server adds to `GET /events`."""
    page = EventPage.model_validate(
        {
            "items": [
                {
                    "event_id": "e-1",
                    "event_type": "infrahub_sync.config.registered",
                    "occurred_at": "2026-11-06T10:15:02Z",
                    "actor": "alice",
                    "operation": "register",
                    "outcome": "accepted",
                    "reason": None,
                    "primary_resource": {"kind": "config", "id": "cfg"},
                    "related": [{"kind": "account", "id": "alice"}, {"kind": "team", "id": "network"}],
                    "added_later": True,
                }
            ],
            "limit": 50,
            "offset": 0,
            "has_more": False,
        }
    )

    (event,) = page.items
    assert event.occurred_at == datetime(2026, 11, 6, 10, 15, 2, tzinfo=timezone.utc)
    assert event.primary_resource == EventPrimaryResource(kind="config", id="cfg")
    assert event.related[1] == EventRelatedResource(kind="team", id="network")
    assert (page.limit, page.offset, page.has_more) == (50, 0, False)


def test_an_event_timestamp_requires_a_timezone() -> None:
    with pytest.raises(ValidationError):
        EventResource.model_validate(
            {
                "event_id": "e-1",
                "event_type": "infrahub_sync.run.planned",
                "occurred_at": "2026-11-06T10:15:02",
                "actor": "alice",
                "operation": "plan",
                "outcome": "succeeded",
                "reason": None,
                "primary_resource": None,
                "related": [],
            }
        )


def test_a_run_resource_of_an_older_sync_api_reads_as_a_manual_run() -> None:
    """A Sync API that predates schedules returns no trigger fields; the run then reads as manual."""
    run = PublicRunResource.model_validate(_public_run())

    assert run.trigger_type == "manual"
    assert run.schedule_id is None
    assert run.scheduled_start_at is None


def test_a_run_resource_carries_the_schedule_and_the_start_time_of_a_scheduled_run() -> None:
    run = PublicRunResource.model_validate(
        _public_run(trigger_type="scheduled", schedule_id="s-" + "3f" * 16, scheduled_start_at="2026-11-18T01:00:00Z")
    )

    assert run.trigger_type == "scheduled"
    assert run.schedule_id == "s-" + "3f" * 16
    assert run.scheduled_start_at == datetime(2026, 11, 18, 1, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "override",
    [
        {"trigger_type": "webhook"},
        {"trigger_type": "scheduled", "schedule_id": "s-" + "3f" * 16, "scheduled_start_at": "2026-11-18T01:00:00"},
        {"trigger_type": "scheduled", "schedule_id": "s-" + "3f" * 16},
        {"trigger_type": "scheduled", "scheduled_start_at": "2026-11-18T01:00:00Z"},
        {"trigger_type": "manual", "schedule_id": "s-" + "3f" * 16},
        {"trigger_type": "manual", "scheduled_start_at": "2026-11-18T01:00:00Z"},
    ],
    ids=[
        "unknown-trigger-type",
        "naive-start-time",
        "scheduled-without-start-time",
        "scheduled-without-schedule",
        "manual-with-schedule",
        "manual-with-start-time",
    ],
)
def test_an_invalid_trigger_field_of_a_run_is_refused(override: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        PublicRunResource.model_validate(_public_run(**override))


def _event(**extra: object) -> dict[str, object]:
    return {
        "event_id": "e-1",
        "event_type": "infrahub_sync.schedule.start_missed",
        "occurred_at": "2026-11-18T01:05:00Z",
        "actor": "operator",
        "operation": "schedule-start",
        "outcome": "missed-skipped",
        **extra,
    }


def test_an_event_without_details_reads_none() -> None:
    assert EventResource.model_validate(_event()).details is None
    assert EventResource.model_validate(_event(details=None)).details is None


def test_an_event_carries_its_details() -> None:
    event = EventResource.model_validate(
        _event(
            details={
                "start_at": "2026-11-18T01:00:00Z",
                "starts": 600,
                "first_start_at": "2026-11-10T01:00:00Z",
                "last_start_at": "2026-11-17T01:00:00Z",
                "left_out": 3,
                "branch": "sync/nightly-20261117-a1b2c3",
                "proposed_change_id": "pc-1",
                "replaced_run_id": "run-0",
                "cause": "configuration-version-not-found",
            }
        )
    )

    details = event.details
    assert isinstance(details, EventDetailsResource)
    assert details.start_at == datetime(2026, 11, 18, 1, tzinfo=timezone.utc)
    assert (details.starts, details.left_out) == (600, 3)
    assert details.first_start_at == datetime(2026, 11, 10, 1, tzinfo=timezone.utc)
    assert details.last_start_at == datetime(2026, 11, 17, 1, tzinfo=timezone.utc)
    assert details.branch == "sync/nightly-20261117-a1b2c3"
    assert (details.proposed_change_id, details.replaced_run_id) == ("pc-1", "run-0")
    assert details.cause == "configuration-version-not-found"


def test_the_details_of_an_event_declare_only_the_closed_keys() -> None:
    assert set(EventDetailsResource.model_fields) == {
        "start_at",
        "starts",
        "first_start_at",
        "last_start_at",
        "left_out",
        "branch",
        "proposed_change_id",
        "replaced_run_id",
        "cause",
    }
    assert EventDetailsResource().model_dump() == dict.fromkeys(EventDetailsResource.model_fields)


@pytest.mark.parametrize(
    "details",
    [{"start_at": "2026-11-18T01:00:00"}, {"first_start_at": "2026-11-18T01:00:00"}, {"starts": -1}, {"left_out": -1}],
    ids=["naive-start", "naive-first-start", "negative-starts", "negative-left-out"],
)
def test_invalid_details_of_an_event_are_refused(details: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        EventResource.model_validate(_event(details=details))


def test_a_create_schedule_request_applies_the_defaults_of_the_contract() -> None:
    request = CreateScheduleRequest(name="nightly", config_id="cfg", registry_version=3, cron="0 2 * * *")

    assert request.model_dump(mode="json") == {
        "name": "nightly",
        "config_id": "cfg",
        "registry_version": 3,
        "cron": "0 2 * * *",
        "timezone": "UTC",
        "write_target": "branch-review",
        "write_when_left_out": False,
        "overlap_policy": "reject",
        "start_late_by_at_most_seconds": None,
        "reason": None,
    }


def test_a_create_schedule_request_accepts_every_setting_of_the_contract() -> None:
    request = CreateScheduleRequest(
        name="nightly",
        config_id="cfg",
        registry_version=3,
        cron="0 2 * * *",
        timezone="Europe/Paris",
        write_target="default-branch",
        write_when_left_out=True,
        overlap_policy="queue",
        start_late_by_at_most_seconds=604800,
        reason="nightly import",
    )

    assert (request.write_target, request.overlap_policy) == ("default-branch", "queue")
    assert request.write_when_left_out is True
    assert request.start_late_by_at_most_seconds == 604800


@pytest.mark.parametrize(
    ("cron", "zone"),
    [("not a cron expression", "UTC"), ("0 2 * * *", "Mars/Olympus"), ("", "")],
    ids=["cron", "timezone", "empty"],
)
def test_a_schedule_request_does_not_validate_the_cron_expression_or_the_time_zone(cron: str, zone: str) -> None:
    """The Sync API validates both, and answers with its own refusal codes."""
    created = CreateScheduleRequest(name="nightly", config_id="cfg", registry_version=1, cron=cron, timezone=zone)
    changed = ChangeScheduleRequest(cron=cron, timezone=zone)

    assert (created.cron, created.timezone) == (cron, zone)
    assert (changed.cron, changed.timezone) == (cron, zone)


def test_a_create_schedule_request_accepts_left_out_writes_with_branch_review() -> None:
    """The Sync API refuses this pair with `schedule-left-out-not-applicable`, not the model."""
    request = CreateScheduleRequest(
        name="nightly", config_id="cfg", registry_version=1, cron="0 2 * * *", write_when_left_out=True
    )

    assert (request.write_target, request.write_when_left_out) == ("branch-review", True)


@pytest.mark.parametrize(
    "override",
    [
        {"name": "not a name"},
        {"config_id": "not a name"},
        {"registry_version": 0},
        {"registry_version": "3"},
        {"registry_version": True},
        {"write_target": "main"},
        {"overlap_policy": "replace"},
        {"write_when_left_out": "yes"},
        {"start_late_by_at_most_seconds": 59},
        {"start_late_by_at_most_seconds": 604801},
        {"owner": "alice"},
    ],
    ids=lambda override: "-".join(f"{key}={value}" for key, value in override.items()),
)
def test_an_invalid_create_schedule_request_is_refused(override: dict[str, object]) -> None:
    payload = {"name": "nightly", "config_id": "cfg", "registry_version": 1, "cron": "0 2 * * *", **override}

    with pytest.raises(ValidationError):
        CreateScheduleRequest.model_validate(payload)


@pytest.mark.parametrize("field", ["name", "config_id", "registry_version", "cron"])
def test_a_create_schedule_request_requires_its_four_fields(field: str) -> None:
    payload: dict[str, object] = {"name": "nightly", "config_id": "cfg", "registry_version": 1, "cron": "0 2 * * *"}
    del payload[field]

    with pytest.raises(ValidationError):
        CreateScheduleRequest.model_validate(payload)


@pytest.mark.parametrize(
    "change",
    [
        {"registry_version": 4},
        {"cron": "0 3 * * *"},
        {"timezone": "Europe/Paris"},
        {"write_target": "default-branch"},
        {"write_when_left_out": False},
        {"overlap_policy": "queue"},
        {"start_late_by_at_most_seconds": 60},
    ],
    ids=lambda change: next(iter(change)),
)
def test_a_change_schedule_request_keeps_every_field_it_does_not_name(change: dict[str, object]) -> None:
    request = ChangeScheduleRequest.model_validate(change)

    (field,) = change
    assert getattr(request, field) == change[field]
    assert request.model_dump(exclude={field, "reason"}) == dict.fromkeys(
        set(ChangeScheduleRequest.model_fields) - {field, "reason"}
    )


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"reason": "no change"},
        {"cron": None},
        {"name": "other", "cron": "0 3 * * *"},
        {"config_id": "other", "cron": "0 3 * * *"},
        {"owner": "alice", "cron": "0 3 * * *"},
        {"registry_version": 0},
        {"start_late_by_at_most_seconds": 59},
    ],
    ids=[
        "empty",
        "reason-only",
        "null-only",
        "names-name",
        "names-config-id",
        "names-owner",
        "version-zero",
        "late-start-too-short",
    ],
)
def test_a_change_schedule_request_that_changes_nothing_or_names_a_fixed_field_is_refused(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ChangeScheduleRequest.model_validate(payload)


def test_a_schedule_action_request_holds_only_a_reason() -> None:
    assert ScheduleActionRequest().model_dump(mode="json") == {"reason": None}
    assert ScheduleActionRequest(reason="maintenance window").reason == "maintenance window"
    with pytest.raises(ValidationError):
        ScheduleActionRequest.model_validate({"state": "paused"})


def _schedule(**extra: object) -> dict[str, object]:
    return {
        "schedule_id": "s-" + "3f" * 16,
        "name": "from-netbox-nightly",
        "config_id": "from-netbox",
        "registry_version": 3,
        "cron": "0 2 * * *",
        "timezone": "Europe/Paris",
        "write_target": "branch-review",
        "write_when_left_out": False,
        "left_out_set_by": None,
        "left_out_set_at": None,
        "overlap_policy": "reject",
        "state": "active",
        "state_reason": None,
        "owner": "operator",
        "next_start_at": "2026-11-18T01:00:00Z",
        "last_run_id": None,
        "prefect_synced": True,
        "consecutive_failures": 0,
        "open_branch": None,
        "created_at": "2026-11-17T09:12:00Z",
        "updated_at": "2026-11-17T09:12:00Z",
        **extra,
    }


def test_a_schedule_resource_reads_without_the_fields_of_user_story_3() -> None:
    """`newer_version`, `start_late_by_at_most_seconds` and `waiting_start_at` are absent before user story 3."""
    schedule = ScheduleResource.model_validate(_schedule())

    assert schedule.newer_version is None
    assert schedule.start_late_by_at_most_seconds is None
    assert schedule.waiting_start_at is None
    assert schedule.next_start_at == datetime(2026, 11, 18, 1, tzinfo=timezone.utc)
    assert schedule.created_at == datetime(2026, 11, 17, 9, 12, tzinfo=timezone.utc)
    assert (schedule.prefect_synced, schedule.consecutive_failures) == (True, 0)
    assert (schedule.state, schedule.state_reason, schedule.owner) == ("active", None, "operator")


def test_a_schedule_resource_reads_every_field_of_the_contract() -> None:
    schedule = ScheduleResource.model_validate(
        _schedule(
            newer_version=4,
            start_late_by_at_most_seconds=3600,
            waiting_start_at="2026-11-18T01:00:00Z",
            write_target="default-branch",
            write_when_left_out=True,
            left_out_set_by="alice",
            left_out_set_at="2026-11-17T10:00:00Z",
            state="stopped",
            state_reason="repeated-failures",
            consecutive_failures=5,
            prefect_synced=False,
            last_run_id="run-9",
            open_branch={
                "run_id": "run-8",
                "branch": "sync/from-netbox-nightly-20261117-a1b2c3",
                "proposed_change_id": "pc-1",
            },
        )
    )

    assert (schedule.newer_version, schedule.start_late_by_at_most_seconds) == (4, 3600)
    assert schedule.waiting_start_at == datetime(2026, 11, 18, 1, tzinfo=timezone.utc)
    assert (schedule.left_out_set_by, schedule.left_out_set_at) == (
        "alice",
        datetime(2026, 11, 17, 10, tzinfo=timezone.utc),
    )
    assert (schedule.state, schedule.state_reason) == ("stopped", "repeated-failures")
    assert (schedule.consecutive_failures, schedule.prefect_synced, schedule.last_run_id) == (5, False, "run-9")
    assert schedule.open_branch == ScheduleOpenBranchResource(
        run_id="run-8", branch="sync/from-netbox-nightly-20261117-a1b2c3", proposed_change_id="pc-1"
    )


def test_a_schedule_resource_declares_the_fields_of_the_contract() -> None:
    assert set(ScheduleResource.model_fields) == {
        "schedule_id",
        "name",
        "config_id",
        "registry_version",
        "newer_version",
        "cron",
        "timezone",
        "write_target",
        "write_when_left_out",
        "left_out_set_by",
        "left_out_set_at",
        "overlap_policy",
        "start_late_by_at_most_seconds",
        "state",
        "state_reason",
        "owner",
        "next_start_at",
        "waiting_start_at",
        "last_run_id",
        "prefect_synced",
        "consecutive_failures",
        "open_branch",
        "created_at",
        "updated_at",
    }


def test_a_schedule_resource_reads_a_newer_sync_api() -> None:
    """The client tolerates the fields, states and settings that a newer Sync API adds."""
    schedule = ScheduleResource.model_validate(
        _schedule(state="expired", write_target="named-branch", overlap_policy="replace", added_later=True)
    )

    assert (schedule.state, schedule.write_target, schedule.overlap_policy) == ("expired", "named-branch", "replace")


@pytest.mark.parametrize("field", ["next_start_at", "waiting_start_at", "left_out_set_at", "created_at", "updated_at"])
def test_a_schedule_timestamp_requires_a_timezone(field: str) -> None:
    with pytest.raises(ValidationError, match="schedule timestamps must include a timezone"):
        ScheduleResource.model_validate(_schedule(**{field: "2026-11-18T01:00:00"}))


@pytest.mark.parametrize(
    "override",
    [{"registry_version": 0}, {"consecutive_failures": -1}, {"owner": ""}, {"schedule_id": ""}, {"state": ""}],
    ids=["version-zero", "negative-failures", "empty-owner", "empty-identifier", "empty-state"],
)
def test_an_invalid_schedule_resource_is_refused(override: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ScheduleResource.model_validate(_schedule(**override))


def test_a_schedule_page_reads_its_items_and_its_cursor() -> None:
    page = SchedulePage.model_validate({"items": [_schedule()], "next_cursor": "opaque"})
    last = SchedulePage.model_validate({"items": []})

    assert [item.name for item in page.items] == ["from-netbox-nightly"]
    assert page.next_cursor == "opaque"
    assert (last.items, last.next_cursor) == ((), None)


def test_the_service_re_exports_the_schedule_models_and_bounds_what_it_emits() -> None:
    from infrahub_sync.service import models as service_models

    for name in (
        "CreateScheduleRequest",
        "ChangeScheduleRequest",
        "ScheduleActionRequest",
        "ScheduleResource",
        "SchedulePage",
    ):
        assert name in service_models.__all__
        assert getattr(service_models, name) is globals()[name]
    emitted = service_models.EmittedScheduleResource
    page = service_models.EmittedSchedulePage
    assert issubclass(emitted, ScheduleResource)
    assert issubclass(page, SchedulePage)
    assert {emitted, page} <= set(service_models.EMITTED_RESOURCES)

    schedule = emitted.model_validate(
        _schedule(
            prefect_schedule_id="internal",
            open_branch={"run_id": "run-8", "branch": "sync/nightly", "proposed_change_id": None, "state": "open"},
        )
    )

    assert "prefect_schedule_id" not in schedule.model_dump()
    assert schedule.open_branch is not None
    assert "state" not in schedule.open_branch.model_dump()


def test_the_service_bounds_the_details_of_an_event_it_emits() -> None:
    from infrahub_sync.service.models import EmittedEventPage

    page = EmittedEventPage.model_validate(
        {
            "items": [_event(details={"start_at": "2026-11-18T01:00:00Z", "infrahub_message": "free text"})],
            "limit": 50,
            "offset": 0,
            "has_more": False,
        }
    )

    details = page.items[0].details
    assert details is not None
    assert "infrahub_message" not in details.model_dump()
    assert details.start_at == datetime(2026, 11, 18, 1, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param({}, id="neither"),
        pytest.param({"package": {"format_version": 1}, "document": "format_version: 1\n"}, id="both"),
        pytest.param({"package": None, "document": None}, id="both-null"),
    ],
)
def test_a_package_check_request_needs_exactly_one_of_package_and_document(fields: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        PackageCheckRequest(**fields)  # ty: ignore[invalid-argument-type]


@pytest.mark.parametrize(
    "package",
    [
        pytest.param({"format_version": float("nan")}, id="nan"),
        pytest.param({"values": (1, 2)}, id="tuple"),
        pytest.param({"nested": {1: "integer key"}}, id="integer-key"),
    ],
)
def test_a_package_check_request_keeps_the_json_native_package_rule(package: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        PackageCheckRequest(package=package)
    with pytest.raises(ValidationError):
        ConfigMutationRequest(package=package)


def test_a_package_check_request_serializes_only_the_field_it_carries() -> None:
    assert PackageCheckRequest(package={"format_version": 1}).model_dump(mode="json") == {
        "package": {"format_version": 1}
    }
    assert PackageCheckRequest(document=" format_version: 1\n").model_dump(mode="json") == {
        "document": " format_version: 1\n"
    }


def test_a_package_check_request_refuses_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        PackageCheckRequest(package={"format_version": 1}, reason="no reason field")  # ty: ignore[unknown-argument]
