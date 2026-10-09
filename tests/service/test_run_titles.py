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


class _Client:
    def __init__(self, failure: Exception | None = None) -> None:
        self.failure = failure
        self.names: list[tuple[UUID, str]] = []

    async def update_flow_run(self, flow_run_id: UUID, *, name: str) -> None:
        if self.failure is not None:
            raise self.failure
        self.names.append((flow_run_id, name))


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
