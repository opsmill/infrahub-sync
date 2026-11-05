"""Titles that replace the generated names of Sync flow runs."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import httpx
import pytest

from infrahub_sync.service import flow as service_flow
from infrahub_sync.service.orchestration import PrefectOrchestration, Submission, run_name

FLOW_RUN_ID = uuid4()
DEPLOYMENT_ID = uuid4()
# A tag an operator added to the deployment in Prefect, next to the tags Sync deploys with.
OPERATOR_TAG = "team:network"
DEPLOYMENT_TAGS = ["infrahub-sync", "service", OPERATOR_TAG]


class _Client:
    """Prefect's flow-run creation: the run gets the deployment's tags and the requested ones."""

    def __init__(self, failure: Exception | None = None) -> None:
        self.failure = failure
        self.created: list[dict[str, Any]] = []
        self.runs: dict[str, SimpleNamespace] = {}

    async def read_deployment_by_name(self, _name: str) -> SimpleNamespace:  # noqa: PLR6301 - protocol fake.
        return SimpleNamespace(id=DEPLOYMENT_ID, tags=list(DEPLOYMENT_TAGS))

    async def create_flow_run_from_deployment(
        self,
        deployment_id: UUID,
        *,
        parameters: dict[str, Any],
        idempotency_key: str | None = None,
        name: str | None = None,
        tags: list[str] | None = None,
    ) -> SimpleNamespace:
        assert deployment_id == DEPLOYMENT_ID
        if self.failure is not None:
            raise self.failure
        self.created.append({"parameters": parameters, "key": idempotency_key, "name": name, "tags": tags})
        if idempotency_key in self.runs:
            return self.runs[idempotency_key]
        run = SimpleNamespace(id=FLOW_RUN_ID, name=name, tags=sorted({*DEPLOYMENT_TAGS, *(tags or [])}), state=None)
        self.runs[str(idempotency_key)] = run
        return run

    async def read_flow_run(self, flow_run_id: UUID) -> SimpleNamespace:
        assert flow_run_id == FLOW_RUN_ID
        return next(iter(self.runs.values()))


class _Handle:
    id = str(FLOW_RUN_ID)

    @staticmethod
    async def status() -> str:
        return "scheduled"


class _Executor:
    """The vendored executor's submission, without its flow-parameter validation."""

    def __init__(self, client: Any) -> None:  # noqa: ANN401 - the labelling client under test.
        self.client = client

    async def submit(self, definition: Any, parameters: dict[str, object], *, idempotency_key: str) -> _Handle:  # noqa: ANN401
        deployment = await self.client.read_deployment_by_name(definition.key)
        await self.client.create_flow_run_from_deployment(
            deployment.id, parameters=parameters, idempotency_key=idempotency_key
        )
        return _Handle()


def _gateway(client: _Client) -> PrefectOrchestration:
    return PrefectOrchestration(cast("Any", client), cast("Any", _Executor))


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


async def test_submit_creates_the_run_under_its_title_before_a_worker_starts_it() -> None:
    client = _Client()

    submission = await _gateway(client).submit(
        {"stage": "apply", "branch": "main", "config_id": "20261005T0750-23c35857", "configuration_name": "netbox"},
        idempotency_key="key-1",
    )

    assert submission == Submission(flow_run_id=str(FLOW_RUN_ID), state="scheduled")
    assert [created["name"] for created in client.created] == ["Apply sync plan of netbox"], (
        "the title names the configuration, never its ID"
    )
    assert client.runs["key-1"].name == "Apply sync plan of netbox"


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
REGISTERED_RUN_TAGS = ["config-version:cfg-7@3", "config:cfg-7", "run:run-42", "stage:apply"]


async def test_submit_creates_the_run_with_its_run_stage_and_configuration_version_tags() -> None:
    """Prefect adds the deployment's tags, so a tag an operator put on the deployment stays."""
    client = _Client()

    await _gateway(client).submit(dict(REGISTERED_PARAMETERS), idempotency_key="key-1")

    (created,) = client.created
    assert created["tags"] == REGISTERED_RUN_TAGS
    assert created["key"] == "key-1"
    assert created["parameters"] == REGISTERED_PARAMETERS
    assert client.runs["key-1"].tags == sorted({*DEPLOYMENT_TAGS, *REGISTERED_RUN_TAGS})
    assert OPERATOR_TAG in client.runs["key-1"].tags


async def test_a_replayed_submission_keeps_the_first_run_and_its_labels() -> None:
    client = _Client()
    gateway = _gateway(client)

    first = await gateway.submit(dict(REGISTERED_PARAMETERS), idempotency_key="key-1")
    replay = await gateway.submit(dict(REGISTERED_PARAMETERS), idempotency_key="key-1")

    assert first == replay
    assert len(client.runs) == 1
    assert client.runs["key-1"].tags == sorted({*DEPLOYMENT_TAGS, *REGISTERED_RUN_TAGS})


async def test_a_legacy_run_gets_no_configuration_tags() -> None:
    client = _Client()

    await _gateway(client).submit(
        {"run_id": "run-1", "stage": "plan", "config_id": "legacy-config", "registry_version": None},
        idempotency_key="key-1",
    )

    assert client.created[0]["tags"] == ["run:run-1", "stage:plan"]


async def test_no_tag_carries_the_actor_the_branch_or_the_work_pool() -> None:
    client = _Client()

    await _gateway(client).submit(dict(REGISTERED_PARAMETERS), idempotency_key="key-1")

    tags = client.runs["key-1"].tags
    for forbidden in ("alice", "feature-branch", "sync-pool"):
        assert not any(forbidden in tag for tag in tags), forbidden


async def test_a_creation_failure_fails_the_submission() -> None:
    """Title and tags travel with the creation request, so there is no separate labelling step to fail."""
    client = _Client(failure=httpx.ConnectError("refused"))

    with pytest.raises(httpx.ConnectError):
        await _gateway(client).submit(dict(REGISTERED_PARAMETERS), idempotency_key="key-1")


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
