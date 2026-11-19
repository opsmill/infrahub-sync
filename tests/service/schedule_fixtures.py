"""Shared fakes and builders for the schedule tests of the Sync API (spec 021)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from fastapi.testclient import TestClient

from infrahub_sync.configuration import ConfigurationPackage
from infrahub_sync.product_store import (
    ProductProjection,
    ProductRun,
    ScheduledStartRun,
    ScheduleFilters,
    ScheduleRecord,
    local_product_projection,
)
from infrahub_sync.service import flow as service_flow
from infrahub_sync.service.app import create_app
from infrahub_sync.service.auth import ADMIN_ACCOUNT_NAME, StoreAccountResolver
from infrahub_sync.service.credentials import digest
from infrahub_sync.service.orchestration import (
    CancellationResult,
    DeploymentSchedule,
    Observation,
    ObservedDeploymentSchedule,
    OrchestrationUnavailableError,
    PoolStatus,
    RunLogRead,
    Submission,
)
from infrahub_sync.service.schedules import ScheduleService
from infrahub_sync.service.service import RunService
from tests.service.accounts import IDLE_SECONDS, TOKEN_NAME, AccountSpec, seed_accounts

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping, Sequence
    from pathlib import Path

    import pytest
    from httpx import Response

OWNER_TOKEN = "schedule-owner-token-canary-0001"  # noqa: S105 - deliberate non-secret boundary canary.
OTHER_TOKEN = "schedule-other-token-canary-0002"  # noqa: S105 - deliberate non-secret boundary canary.
ADMIN_TOKEN = "schedule-admin-token-canary-0003"  # noqa: S105 - deliberate non-secret boundary canary.
# The accounts of the schedule tests, by name; each test store holds them with these API tokens.
# The token of `admin` is the one that the environment defines, so the Sync API lists it as a secret.
ACCOUNTS: Mapping[str, AccountSpec] = {
    "owner": {"token": OWNER_TOKEN},
    "other": {"token": OTHER_TOKEN},
    "admin": {"token": ADMIN_TOKEN, "administrator": True},
}
BOTH_WRITE_TARGETS = frozenset({"branch-review", "default-branch"})
WORKER_ID = "0f4b8a8e-5d0c-4f0e-9b6a-3c1d2e4f5a6b"


class NoDeploymentSchedules:
    """The schedule methods of the orchestration, for a fake of a test that uses no schedule."""

    async def read_schedules(self) -> tuple[ObservedDeploymentSchedule, ...]:  # noqa: PLR6301
        return ()

    async def create_schedule(self, schedule: DeploymentSchedule) -> str:  # noqa: PLR6301
        del schedule
        return str(uuid4())

    async def update_schedule(self, orchestrator_id: str, schedule: DeploymentSchedule) -> None:  # noqa: PLR6301
        del orchestrator_id, schedule

    async def delete_schedule(self, orchestrator_id: str) -> None:  # noqa: PLR6301
        del orchestrator_id

    async def create_scheduled_runs(self) -> None:  # noqa: PLR6301
        return


class FakeOrchestration:
    """Hold the schedules of the service deployment in memory, and record each call that changes one.

    ``unavailable`` makes every schedule call fail, as when the task manager cannot be reached.
    ``run_creations`` counts the requests to create the flow runs of the schedules now, and
    ``runs_unavailable`` makes only that request fail.
    """

    def __init__(self) -> None:
        self.schedules: dict[str, DeploymentSchedule] = {}
        self.foreign: list[ObservedDeploymentSchedule] = []
        self.calls: list[tuple[str, str]] = []
        self.unavailable = False
        self.run_creations = 0
        self.runs_unavailable = False

    def _require_available(self) -> None:
        if self.unavailable:
            message = "the orchestrator could not be reached"
            raise OrchestrationUnavailableError(message, error_class="ConnectError")

    def by_slug(self, slug: str) -> DeploymentSchedule:
        """Return the one schedule of the deployment that has the slug."""
        (schedule,) = (item for item in self.schedules.values() if item.slug == slug)
        return schedule

    def slugs(self) -> set[str]:
        """Return the slugs of the schedules that the deployment holds."""
        return {schedule.slug for schedule in self.schedules.values()}

    async def read_schedules(self) -> tuple[ObservedDeploymentSchedule, ...]:
        self._require_available()
        held = tuple(
            ObservedDeploymentSchedule(orchestrator_id=identifier, slug=schedule.slug, schedule=schedule)
            for identifier, schedule in self.schedules.items()
        )
        return (*held, *self.foreign)

    async def create_schedule(self, schedule: DeploymentSchedule) -> str:
        self._require_available()
        identifier = str(uuid4())
        self.schedules[identifier] = replace(schedule, orchestrator_id=identifier)
        self.calls.append(("create", schedule.slug))
        return identifier

    async def update_schedule(self, orchestrator_id: str, schedule: DeploymentSchedule) -> None:
        self._require_available()
        self.schedules[orchestrator_id] = replace(schedule, orchestrator_id=orchestrator_id)
        self.calls.append(("update", schedule.slug))

    async def delete_schedule(self, orchestrator_id: str) -> None:
        self._require_available()
        removed = self.schedules.pop(orchestrator_id, None)
        self.foreign = [item for item in self.foreign if item.orchestrator_id != orchestrator_id]
        self.calls.append(("delete", orchestrator_id if removed is None else removed.slug))

    async def create_scheduled_runs(self) -> None:
        self._require_available()
        if self.runs_unavailable:
            message = "the orchestrator could not be reached"
            raise OrchestrationUnavailableError(message, error_class="ReadTimeout")
        self.run_creations += 1

    async def submit(self, parameters: dict[str, object], *, idempotency_key: str) -> Submission:  # noqa: PLR6301
        del parameters
        return Submission(flow_run_id=str(uuid5(NAMESPACE_URL, idempotency_key)), state="pending")

    async def observe(self, flow_run_id: str) -> Observation:  # noqa: PLR6301
        del flow_run_id
        return Observation(available=True, state="running")

    async def pool_status(self, work_pool_name: str, now: datetime) -> PoolStatus:  # noqa: PLR6301
        del work_pool_name, now
        return PoolStatus(detail_available=False, queue_depth=None, observed_at=None)

    async def cancel(self, flow_run_id: str) -> CancellationResult:  # noqa: PLR6301
        del flow_run_id
        return CancellationResult(acknowledged=True)

    async def read_logs(self, flow_run_ids: Sequence[str]) -> RunLogRead:  # noqa: PLR6301
        del flow_run_ids
        return RunLogRead(expired=False)


def package(name: str = "from-netbox", *, branch: str | None = None) -> ConfigurationPackage:
    """Return a configuration package; with ``branch`` it declares its own destination branch."""
    settings: dict[str, Any] = {"url": "https://infrahub.example", "token": {"$credential": "token"}}
    if branch is not None:
        settings["branch"] = branch
    return ConfigurationPackage.model_validate(
        {
            "format_version": 1,
            "configuration": {
                "name": name,
                "source": {
                    "name": "netbox",
                    "settings": {"url": "https://netbox.example", "token": {"$credential": "token"}},
                },
                "destination": {"name": "infrahub", "settings": settings},
                "order": [],
                "schema_mapping": [],
                "diffsync_flags": [],
                "incremental": None,
            },
            "credentials": {"token": {"provider": "env", "identifier": "INFRAHUB_SYNC_CREDENTIAL_TOKEN"}},
        }
    )


def headers(token: str = OWNER_TOKEN, key: str | None = None) -> dict[str, str]:
    """Return the bearer header of an actor and, for a ``POST``, a new idempotency key."""
    return {"Authorization": f"Bearer {token}", "Idempotency-Key": key or uuid4().hex}


@dataclass
class ScheduleApi:
    """One Sync API over a local product store and a fake orchestration."""

    projection: ProductProjection
    orchestration: FakeOrchestration
    schedules: ScheduleService
    app: Any
    client: TestClient
    config_id: str

    def body(self, **changes: object) -> dict[str, Any]:
        """Return the body of a create request for the registered configuration."""
        return {
            "name": "from-netbox-nightly",
            "config_id": self.config_id,
            "registry_version": 1,
            "cron": "0 2 * * *",
            "timezone": "Europe/Paris",
        } | changes

    def create(self, token: str = OWNER_TOKEN, **changes: object) -> dict[str, Any]:
        """Create a schedule and return its resource."""
        response = self.client.post("/schedules", json=self.body(**changes), headers=headers(token))
        assert response.status_code == 201, response.text
        return response.json()

    def post(self, schedule_id: str, action: str, token: str = OWNER_TOKEN, **body: object) -> Response:
        """Send one action of a schedule and return the response."""
        return self.client.post(f"/schedules/{schedule_id}/{action}", json=body, headers=headers(token))

    def get(self, schedule_id: str) -> dict[str, Any]:
        """Return the resource of a schedule."""
        response = self.client.get(f"/schedules/{schedule_id}", headers={"Authorization": f"Bearer {OWNER_TOKEN}"})
        assert response.status_code == 200, response.text
        return response.json()

    def account_id(self, name: str) -> str:
        """Return the identifier that the store records for the account with this name."""
        account = self.projection.lookup_account_by_name(name)
        assert account is not None, name
        return account.account_id

    def disable(self, name: str) -> None:
        """Disable the account with this name, as an administrator does while the Sync API runs."""
        assert self.projection.disable_account(self.account_id(name))

    def enable(self, name: str) -> None:
        """Enable the account with this name again, with its API token of ``ACCOUNTS``.

        A disabled account loses its tokens, so the token is created again.
        """
        account_id = self.account_id(name)
        assert self.projection.enable_account(account_id)
        self.projection.create_account_token(account_id, name=TOKEN_NAME, digest=digest(ACCOUNTS[name]["token"]))

    def stored(self, schedule_id: str) -> ScheduleRecord:
        """Return the stored record of a schedule."""
        record = self.projection.lookup_schedule(schedule_id).value
        assert record is not None
        return record

    def scheduled_run(self, schedule_id: str, start: datetime, *, branch: str | None = None) -> str:
        """Store the run of one start of a schedule, as the worker does, and return its identifier.

        A run of a schedule with branch review writes to ``branch``, and its branch row is in the
        state ``pending``.
        """
        schedule = self.stored(schedule_id)
        review = schedule.write_target == "branch-review"
        version = self.projection.lookup_configuration_version(schedule.config_id, schedule.registry_version).value
        assert version is not None
        run = ProductRun.model_validate(
            {
                "run_id": f"run-{uuid4().hex[:12]}",
                "operation": "sync",
                "configuration_reference": f"{schedule.config_id}@{schedule.registry_version}",
                "config_id": schedule.config_id,
                "registry_version": schedule.registry_version,
                "package_checksum": version.package_checksum,
                "actor": schedule.owner,
                "started_at": start + timedelta(seconds=1),
                "phase": "accepted",
                "trigger_type": "scheduled",
                "schedule_id": schedule_id,
                "scheduled_start_at": start,
                "destination_branch": (branch or f"sync/{schedule.name}-{start:%Y%m%d}-000000") if review else None,
            }
        )
        result = self.projection.admit_scheduled_start(
            schedule_id,
            start,
            f"flow-{run.run_id}",
            WORKER_ID,
            start + timedelta(seconds=1),
            ScheduledStartRun(run=run, branch_review=review, last_start_at=schedule.last_start_at),
        )
        assert result.started
        return run.run_id

    def stored_text(self) -> str:
        """Return every stored event and every stored schedule as one text, for a search of a credential."""
        events = "".join(event.model_dump_json() for event in self.projection.audit_events())
        records = "".join(
            record.model_dump_json()
            for state in ("active", "paused", "stopped", "deleted")
            for record in self.projection.list_schedules(ScheduleFilters.model_validate({"state": state})).items
        )
        return events + records

    def event_types(self) -> list[str]:
        """Return the types of the stored events, oldest first; an event with no type is left out."""
        return [event.event_type for event in self.projection.audit_events() if event.event_type is not None]


def schedule_api(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    delivered: Collection[str] = BOTH_WRITE_TARGETS,
    accounts: Mapping[str, AccountSpec] = ACCOUNTS,
) -> ScheduleApi:
    """Build a Sync API with one registered configuration and the given set of delivered write targets.

    The destination of every scheduled start answers: the worker's check before admission
    (FR-035) is replaced, so no test reads a destination unless it asks for that check.
    """
    monkeypatch.setattr(service_flow, "_destination_not_ready", lambda *_args: None)
    projection = local_product_projection(tmp_path.resolve())
    seed_accounts(projection, accounts, environment_token=ADMIN_ACCOUNT_NAME in accounts)
    config_id = projection.create_configuration(package()).config_id
    return restarted(ScheduleApi(projection, FakeOrchestration(), None, None, None, config_id), delivered=delivered)  # ty: ignore[invalid-argument-type]


def restarted(api: ScheduleApi, *, delivered: Collection[str] = BOTH_WRITE_TARGETS) -> ScheduleApi:
    """Return the API as a new API process builds it: same store and task manager, settings read again.

    The lifespan of the application has not run; a test runs it with ``with api.client:``.
    """
    resolver = StoreAccountResolver(api.projection, idle_seconds=IDLE_SECONDS, admin_token=ADMIN_TOKEN)
    schedules = ScheduleService(
        api.projection,
        api.orchestration,
        resolver,
        delivered_write_targets=delivered,
        secrets=resolver.secret_values,
    )
    service = RunService(api.projection, api.orchestration, secrets=resolver.secret_values)
    app = create_app(service, resolver, schedules=schedules)
    return ScheduleApi(api.projection, api.orchestration, schedules, app, TestClient(app), api.config_id)
