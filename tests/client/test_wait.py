"""Accepted-resource run waiting tests."""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

import infrahub_sync.client.client as client_module
from infrahub_sync.client import ProtocolError, RunResource, RunTerminalError, RunWaitTimeoutError, SyncClient

NOW = datetime(2026, 8, 30, 12, tzinfo=timezone.utc).isoformat()


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, duration: float) -> None:
        self.sleeps.append(duration)
        self.now += duration


def _execution(
    flow_run_id: str,
    *,
    state: str | None = "pending",
    terminal_state: str | None = None,
    terminal_outcome: str | None = None,
) -> dict[str, object]:
    terminal_at = NOW if terminal_state is not None or terminal_outcome is not None else None
    return {
        "flow_run_id": flow_run_id,
        "purpose": "apply",
        "attempt": 1,
        "state": state,
        "detail_available": True,
        "unavailable_reason": None,
        "submitted_at": NOW,
        "claimed_at": None,
        "stalled_at": None,
        "cancellation_requested_at": None,
        "cancellation_recovery_deadline_at": None,
        "cancellation_acknowledged_at": None,
        "terminal_at": terminal_at,
        "terminal_state": terminal_state,
        "terminal_outcome": terminal_outcome,
    }


def _resource(*executions: dict[str, object], phase: str = "accepted", outcome: str | None = None) -> dict[str, object]:
    return {
        "run": {
            "run_id": "run-1",
            "operation": "plan",
            "configuration_reference": "cfg@1",
            "config_id": "cfg",
            "registry_version": 1,
            "package_checksum": "a" * 64,
            "actor": "operator",
            "audit_links": [],
            "started_at": NOW,
            "finished_at": None,
            "phase": phase,
            "outcome": outcome,
            "summary": {},
            "results": {},
            "artifact_refs": [],
            "prefect_executions": [
                {"flow_run_id": item["flow_run_id"], "purpose": item["purpose"], "attempt": item["attempt"]}
                for item in executions
            ],
        },
        "orchestration": list(executions),
    }


def _client(responses: list[dict[str, object] | BaseException]) -> tuple[SyncClient, list[str]]:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/version":
            return httpx.Response(
                200,
                json={"server_version": "3", "api_versions": ["v3-unstable"], "stability": "unstable"},
            )
        response = responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return httpx.Response(200, json=response)

    return SyncClient("https://example.test", "token", transport=httpx.MockTransport(handler)), calls


@pytest.mark.parametrize(
    ("terminal_state", "terminal_outcome", "raises"),
    [
        ("completed", "succeeded", None),
        ("failed", "failed", RunTerminalError),
        ("cancelled", "cancelled", RunTerminalError),
        ("abandoned", "abandoned", RunTerminalError),
        ("interrupted", "ambiguous", RunTerminalError),
    ],
)
def test_wait_applies_every_terminal_verdict(
    terminal_state: str, terminal_outcome: str, raises: type[Exception] | None
) -> None:
    accepted = RunResource.model_validate(_resource(_execution("flow-1")))
    terminal = _resource(
        _execution("flow-1", state=terminal_state, terminal_state=terminal_state, terminal_outcome=terminal_outcome),
        phase="finished",
        outcome=terminal_outcome,
    )
    client, _calls = _client([terminal])

    if raises is None:
        assert client.wait_for_run(accepted, timeout=10, poll_interval=0.001).run.phase == "finished"
    else:
        with pytest.raises(raises) as raised:
            client.wait_for_run(accepted, timeout=10, poll_interval=0.001)
        assert isinstance(raised.value, RunTerminalError)
        assert raised.value.run_id == "run-1"


def test_wait_pins_the_new_execution_after_an_earlier_terminal_plan() -> None:
    earlier = _execution("flow-plan", state="completed", terminal_state="completed", terminal_outcome="succeeded")
    accepted = RunResource.model_validate(_resource(earlier, _execution("flow-apply")))
    completed = _resource(
        earlier,
        _execution("flow-apply", state="completed", terminal_state="completed", terminal_outcome="succeeded"),
        phase="applied",
        outcome="applied",
    )
    client, calls = _client([completed])

    result = client.wait_for_run(accepted, timeout=10, poll_interval=0.001)

    assert result.run.phase == "applied"
    assert calls == ["/version", "/runs/run-1"]


def test_unknown_state_polls_to_the_exact_deadline_without_a_final_request(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = FakeClock()
    monkeypatch.setattr(client_module, "monotonic", clock.monotonic)
    monkeypatch.setattr(client_module, "sleep", clock.sleep)
    accepted = RunResource.model_validate(_resource(_execution("flow-1", state="future-state")))
    client, calls = _client(
        [
            _resource(_execution("flow-1", state="future-state")),
            _resource(_execution("flow-1", state=None)),
        ]
    )

    with pytest.raises(RunWaitTimeoutError) as raised:
        client.wait_for_run(accepted, timeout=5, poll_interval=2)

    assert clock.sleeps == [2, 2, 1]
    assert calls == ["/version", "/runs/run-1", "/runs/run-1"]
    assert raised.value.run_id == "run-1"
    assert raised.value.execution_state is None


def test_wait_refuses_disappearing_execution_history() -> None:
    accepted = RunResource.model_validate(_resource(_execution("flow-1")))
    client, _calls = _client([_resource(_execution("different-flow"))])

    with pytest.raises(ProtocolError):
        client.wait_for_run(accepted, timeout=10, poll_interval=0.001)


def test_wait_refuses_an_accepted_resource_without_an_execution() -> None:
    client, _calls = _client([])

    with pytest.raises(ProtocolError):
        client.wait_for_run(RunResource.model_validate(_resource()), timeout=10, poll_interval=0.001)


@pytest.mark.parametrize(("timeout", "poll_interval", "argument"), [(0, 1, "timeout"), (1, 0, "poll_interval")])
def test_wait_inputs_are_positive_and_finite(timeout: float, poll_interval: float, argument: str) -> None:
    from infrahub_sync.client import ClientInputError

    client, _calls = _client([])
    accepted = RunResource.model_validate(_resource(_execution("flow-1")))

    with pytest.raises(ClientInputError) as raised:
        client.wait_for_run(accepted, timeout=timeout, poll_interval=poll_interval)
    assert raised.value.argument == argument


def test_keyboard_interrupt_crosses_wait_without_cancelling() -> None:
    accepted = RunResource.model_validate(_resource(_execution("flow-1")))
    client, calls = _client([KeyboardInterrupt()])

    with pytest.raises(KeyboardInterrupt):
        client.wait_for_run(accepted, timeout=10, poll_interval=0.001)
    assert "/cancel" not in calls
