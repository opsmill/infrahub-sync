"""Titles that replace the generated names of Sync flow runs."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import httpx
import pytest

from infrahub_sync.service import flow as service_flow
from infrahub_sync.service.orchestration import PrefectOrchestration, run_name

FLOW_RUN_ID = uuid4()


class _Handle:
    id = str(FLOW_RUN_ID)

    @staticmethod
    async def status() -> str:
        return "scheduled"


class _Executor:
    def __init__(self) -> None:
        self.keys: list[str] = []

    async def submit(self, _definition: object, _parameters: dict[str, object], *, idempotency_key: str) -> _Handle:
        self.keys.append(idempotency_key)
        return _Handle()


OPERATOR_TAG = "team:network"
EXISTING_TAGS = ["infrahub-sync", "service", OPERATOR_TAG]


class _Client:
    def __init__(self, failure: Exception | None = None, read_failure: Exception | None = None) -> None:
        self.failure = failure
        self.read_failure = read_failure
        self.names: list[tuple[UUID, str]] = []
        self.tags: list[tuple[UUID, list[str] | None]] = []

    async def read_flow_run(self, flow_run_id: UUID) -> SimpleNamespace:
        if self.read_failure is not None:
            raise self.read_failure
        assert flow_run_id == FLOW_RUN_ID
        return SimpleNamespace(tags=list(EXISTING_TAGS))

    async def update_flow_run(self, flow_run_id: UUID, *, name: str, tags: list[str] | None = None) -> None:
        if self.failure is not None:
            raise self.failure
        self.names.append((flow_run_id, name))
        self.tags.append((flow_run_id, tags))


@pytest.mark.parametrize(
    ("stage", "configuration_name", "expected"),
    [
        ("plan", "netbox-to-infrahub", "Plan sync of netbox-to-infrahub"),
        ("verify", "netbox-to-infrahub", "Verify sync plan of netbox-to-infrahub"),
        ("apply", "netbox-to-infrahub", "Apply sync plan of netbox-to-infrahub"),
        ("sync", "netbox-to-infrahub", "Sync netbox-to-infrahub"),
        ("plan", None, "Plan sync"),
        ("apply", "", "Apply sync plan"),
        ("rollback", "netbox-to-infrahub", "Sync run"),
        (None, "netbox-to-infrahub", "Sync run"),
    ],
)
def test_run_name_says_what_the_stage_does_and_to_which_configuration(
    stage: object, configuration_name: object, expected: str
) -> None:
    assert run_name(stage, configuration_name) == expected


def test_a_run_name_carries_no_branch_and_no_id() -> None:
    """The run's page already shows the branch and the ID."""
    name = run_name("plan", "netbox-to-infrahub")

    assert "main" not in name
    assert not any(character.isdigit() for character in name)


async def test_submit_gives_the_run_its_title_before_a_worker_starts_it() -> None:
    client = _Client()
    gateway = PrefectOrchestration(cast("Any", client), cast("Any", _Executor()))

    await gateway.submit(
        {"stage": "apply", "branch": "main", "config_id": "20261005T0750-23c35857", "configuration_name": "netbox"},
        idempotency_key="key-1",
    )

    assert client.names == [(FLOW_RUN_ID, "Apply sync plan of netbox")], (
        "the title names the configuration, never its ID"
    )


@pytest.mark.parametrize(
    "failure",
    [httpx.ConnectError("refused"), RuntimeError("client closed"), ValueError("unexpected answer")],
    ids=["connect", "runtime", "value"],
)
async def test_a_title_failure_never_fails_the_accepted_submission(
    failure: Exception, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING)
    gateway = PrefectOrchestration(cast("Any", _Client(failure)), cast("Any", _Executor()))

    submission = await gateway.submit({"stage": "plan", "configuration_name": "netbox"}, idempotency_key="key-1")

    assert submission.flow_run_id == str(FLOW_RUN_ID)
    assert submission.state == "scheduled"
    assert type(failure).__name__ in caplog.text
    assert "still runs" in caplog.text


REGISTERED_PARAMETERS: dict[str, object] = {
    "run_id": "run-42",
    "stage": "apply",
    "config_id": "cfg-7",
    "registry_version": 3,
    "configuration_name": "netbox",
    "branch": "feature-branch",
    "actor": "alice",
    "work_pool": "sync-pool",
}


async def test_submit_tags_the_run_with_its_run_stage_and_configuration_version() -> None:
    """Prefect replaces the tag list, so the existing tags are written back with the run tags."""
    client = _Client()
    gateway = PrefectOrchestration(cast("Any", client), cast("Any", _Executor()))

    await gateway.submit(dict(REGISTERED_PARAMETERS), idempotency_key="key-1")

    assert client.tags == [
        (
            FLOW_RUN_ID,
            sorted(
                {
                    "infrahub-sync",
                    "service",
                    OPERATOR_TAG,
                    "run:run-42",
                    "stage:apply",
                    "config:cfg-7",
                    "config-version:cfg-7@3",
                }
            ),
        )
    ]
    assert client.names == [(FLOW_RUN_ID, "Apply sync plan of netbox")], "the name and tags go in one update"


async def test_a_legacy_run_gets_no_configuration_tags() -> None:
    client = _Client()
    gateway = PrefectOrchestration(cast("Any", client), cast("Any", _Executor()))

    await gateway.submit(
        {"run_id": "run-1", "stage": "plan", "config_id": "legacy-config", "registry_version": None},
        idempotency_key="key-1",
    )

    (_, tags) = client.tags[0]
    assert tags == sorted({*EXISTING_TAGS, "run:run-1", "stage:plan"})
    assert not any(tag.startswith("config") for tag in tags or [])


async def test_no_tag_carries_the_actor_the_branch_or_the_work_pool() -> None:
    client = _Client()
    gateway = PrefectOrchestration(cast("Any", client), cast("Any", _Executor()))

    await gateway.submit(dict(REGISTERED_PARAMETERS), idempotency_key="key-1")

    (_, tags) = client.tags[0]
    for forbidden in ("alice", "feature-branch", "sync-pool"):
        assert not any(forbidden in tag for tag in tags or []), forbidden


@pytest.mark.parametrize(
    "failure",
    [httpx.ConnectError("refused"), RuntimeError("client closed"), ValueError("unexpected answer")],
    ids=["connect", "runtime", "value"],
)
async def test_a_tag_read_failure_still_names_and_tags_the_run_from_the_deployment_tags(
    failure: Exception, caplog: pytest.LogCaptureFixture
) -> None:
    """Without the current tags, Sync falls back to the deployment's tags, so the run is still filterable."""
    caplog.set_level(logging.WARNING)
    client = _Client(read_failure=failure)
    gateway = PrefectOrchestration(cast("Any", client), cast("Any", _Executor()))

    submission = await gateway.submit(dict(REGISTERED_PARAMETERS), idempotency_key="key-1")

    assert submission.flow_run_id == str(FLOW_RUN_ID)
    assert submission.state == "scheduled"
    assert client.names == [(FLOW_RUN_ID, "Apply sync plan of netbox")]
    assert client.tags == [
        (
            FLOW_RUN_ID,
            sorted(
                {
                    "infrahub-sync",
                    "service",
                    "run:run-42",
                    "stage:apply",
                    "config:cfg-7",
                    "config-version:cfg-7@3",
                }
            ),
        )
    ]
    assert type(failure).__name__ in caplog.text
    assert "current tags" in caplog.text


async def test_a_tag_update_failure_never_fails_the_accepted_submission(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    client = _Client(failure=httpx.ConnectError("refused"))
    gateway = PrefectOrchestration(cast("Any", client), cast("Any", _Executor()))

    submission = await gateway.submit(dict(REGISTERED_PARAMETERS), idempotency_key="key-1")

    assert submission.flow_run_id == str(FLOW_RUN_ID)
    assert submission.state == "scheduled"
    assert client.tags == []
    assert "ConnectError" in caplog.text
    assert "still runs" in caplog.text


def test_the_flow_gives_the_run_the_same_title_when_it_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prefect calls the flow's `flow_run_name` when the run starts, as Infrahub's own flows use it."""
    parameters = {
        "run_id": "run-1",
        "stage": "verify",
        "config_id": "20261005T0750-23c35857",
        "configuration_name": "netbox",
        "branch": "main",
    }
    monkeypatch.setattr(service_flow, "current_flow_run", SimpleNamespace(parameters=parameters))

    assert service_flow.service_sync_run.flow_run_name is service_flow._service_run_name
    assert service_flow._service_run_name() == "Verify sync plan of netbox"
