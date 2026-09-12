"""How a run that does not reach its phase is reported.

Two outcomes reach the same place and must not be described the same way. A run
that fails carries a payload saying why, and that is the richest evidence a
failure produces. A run that never arrives carries no such thing, and what it was
instead is the only clue there is.
"""

from __future__ import annotations

import httpx
import pytest

from tests.compose.lifecycle import RUN_TIMEOUT_SECONDS, await_phase

RUN_ID = "20260907T0354-8aad1574"
FAILING_PHASE = "apply_failed"
STALLED_PHASE = "planned"


def client_reporting(phase: str) -> httpx.Client:
    """A Sync API whose one run sits in a fixed phase."""

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json={"run": {"run_id": RUN_ID, "phase": phase, "reconciliation_required": False}})

    return httpx.Client(base_url="http://sync-api", transport=httpx.MockTransport(handler))


def failure_from(phase: str, *, timeout: int = RUN_TIMEOUT_SECONDS) -> str:
    with client_reporting(phase) as client, pytest.raises(pytest.fail.Exception) as caught:
        await_phase(client, RUN_ID, "applied", timeout=timeout)
    return str(caught.value)


def test_a_failed_run_is_reported_as_a_failure_rather_than_a_timeout() -> None:
    """The probe's own message carries the payload; a timeout message would discard it."""
    reported = failure_from(FAILING_PHASE)

    assert "failed while waiting for 'applied'" in reported
    assert FAILING_PHASE in reported
    assert "did not happen within" not in reported


def test_a_stalled_run_is_reported_with_the_phase_it_last_held() -> None:
    """A run that never arrives is diagnosed by what it was instead."""
    reported = failure_from(STALLED_PHASE, timeout=1)

    assert "did not happen within 1s" in reported
    assert f"last observed phase: {STALLED_PHASE!r}" in reported
