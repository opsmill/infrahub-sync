"""Deterministic liveness policy boundaries."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest


def test_admission_ttl_and_prefect_query_define_liveness_formulae(monkeypatch: pytest.MonkeyPatch) -> None:
    """The two accepted environment values define all derived timing."""
    from infrahub_sync.managed.liveness import LivenessPolicy

    monkeypatch.setenv("INFRAHUB_SYNC_RUN_ADMISSION_TTL_SECONDS", "300")
    policy = LivenessPolicy.from_environment(worker_query_seconds="10")

    assert policy.admission_ttl_seconds == 300
    assert policy.stall_threshold_seconds == 30
    assert policy.cadence_seconds == 5
