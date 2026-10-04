"""Per-run tags that make Sync flow runs visible in Infrahub's task views."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import httpx
import pytest
from prefect.exceptions import ObjectNotFound

from infrahub_sync.service.orchestration import SERVICE_DEFINITION, PrefectOrchestration, run_tags

FLOW_RUN_ID = uuid4()


class _Handle:
    id = str(FLOW_RUN_ID)

    @staticmethod
    async def status() -> str:
        return "scheduled"


class _Executor:
    def __init__(self) -> None:
        self.calls: list[tuple[dict[str, object], str]] = []

    async def submit(self, _definition: object, parameters: dict[str, object], *, idempotency_key: str) -> _Handle:
        self.calls.append((parameters, idempotency_key))
        return _Handle()


class _Client:
    def __init__(self, failure: Exception | None = None, current: list[str] | None = None) -> None:
        self.failure = failure
        self.current = current if current is not None else list(SERVICE_DEFINITION.tags)
        self.updates: list[tuple[UUID, list[str]]] = []

    async def read_flow_run(self, flow_run_id: UUID) -> SimpleNamespace:
        assert flow_run_id == FLOW_RUN_ID
        return SimpleNamespace(tags=self.current)

    async def update_flow_run(self, flow_run_id: UUID, *, tags: list[str]) -> None:
        if self.failure is not None:
            raise self.failure
        self.updates.append((flow_run_id, tags))


@pytest.mark.parametrize(
    ("stage", "branch", "expected"),
    [
        ("plan", "main", ("infrahub.app", "infrahub.app/workflow-type/sync-plan", "infrahub.app/branch/main")),
        ("verify", "main", ("infrahub.app", "infrahub.app/workflow-type/sync-verify", "infrahub.app/branch/main")),
        (
            "apply",
            "feature-x",
            ("infrahub.app", "infrahub.app/workflow-type/sync-apply", "infrahub.app/branch/feature-x"),
        ),
        ("sync", None, ("infrahub.app", "infrahub.app/workflow-type/sync-sync")),
        ("plan", "", ("infrahub.app", "infrahub.app/workflow-type/sync-plan")),
        (None, "main", ("infrahub.app", "infrahub.app/branch/main")),
    ],
)
def test_run_tags(stage: str | None, branch: str | None, expected: tuple[str, ...]) -> None:
    assert run_tags(stage, branch) == expected


async def test_submit_adds_the_tags_and_keeps_every_tag_the_run_already_has() -> None:
    client = _Client(current=["infrahub-sync", "service", "added-by-an-automation"])
    executor = _Executor()
    gateway = PrefectOrchestration(cast("Any", client), cast("Any", executor))

    submission = await gateway.submit({"stage": "plan", "branch": "main"}, idempotency_key="key-1")

    assert submission.flow_run_id == str(FLOW_RUN_ID)
    assert executor.calls == [({"stage": "plan", "branch": "main"}, "key-1")]
    assert client.updates == [
        (
            FLOW_RUN_ID,
            [
                "infrahub-sync",
                "service",
                "added-by-an-automation",
                "infrahub.app",
                "infrahub.app/workflow-type/sync-plan",
                "infrahub.app/branch/main",
            ],
        )
    ]


async def test_a_replayed_submission_does_not_duplicate_tags() -> None:
    tagged = [*SERVICE_DEFINITION.tags, *run_tags("plan", "main")]
    client = _Client(current=tagged)
    gateway = PrefectOrchestration(cast("Any", client), cast("Any", _Executor()))

    await gateway.submit({"stage": "plan", "branch": "main"}, idempotency_key="key-1")

    assert client.updates == [(FLOW_RUN_ID, tagged)]


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ConnectError("refused"),
        ObjectNotFound(http_exc=Exception()),
        RuntimeError("client closed"),
        ValueError("unexpected answer"),
    ],
    ids=["connect", "not-found", "runtime", "value"],
)
async def test_a_tagging_failure_never_fails_the_accepted_submission(
    failure: Exception, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING)
    gateway = PrefectOrchestration(cast("Any", _Client(failure)), cast("Any", _Executor()))

    submission = await gateway.submit({"stage": "plan", "branch": "main"}, idempotency_key="key-1")

    assert submission.flow_run_id == str(FLOW_RUN_ID)
    assert submission.state == "scheduled"
    assert str(FLOW_RUN_ID) in caplog.text
    assert type(failure).__name__ in caplog.text
    assert "still runs" in caplog.text


def test_the_api_orchestration_offers_every_prefect_operation_the_service_uses() -> None:
    """The API wraps Prefect per call; a missing method is silently skipped by the service."""
    from infrahub_sync.service.serve import _ClientPerCallOrchestration  # noqa: PLC2701

    public = {name for name in dir(PrefectOrchestration) if not name.startswith("_")}

    assert public <= set(dir(_ClientPerCallOrchestration)), public - set(dir(_ClientPerCallOrchestration))
