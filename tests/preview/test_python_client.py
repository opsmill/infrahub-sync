"""Python client surface: `SyncClient` drives one registered lifecycle live.

The typed client can regress independently of the wire the Sync API smoke covers: its
response models, its compatibility handshake, its bearer plumbing, and its bounded
`wait_for_run` all interpret what the service sends rather than repeat it. So this
module drives the documented cycle — register, validate, plan, wait, apply, wait,
`get_results` — through the shipped client against the running stack.

It shares the Sync API smoke's setup and package, so the two converge on the same
branch in either order: the source is mirrored from the destination first and only the
shared device is left differing, leaving exactly one update to plan.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from infrahub_sync.client import SyncClient
from infrahub_sync.client.models import ApplyRunRequest, ConfigMutationRequest, CreateRunRequest
from tasks.preview import SHARED_DEVICE_NAME, SMOKE_BRANCH
from tests.preview.evidence import canary_leaks
from tests.preview.test_service_api import (
    device_types,
    infrahub_client,
    seed_source_branch,
    smoke_package,
    unwritten_plan_reasons,
)

pytestmark = pytest.mark.preview

WAIT_TIMEOUT_SECONDS = 240.0
POLL_INTERVAL_SECONDS = 3.0


def _key() -> str:
    """A fresh key per mutation, so a re-run never replays an earlier smoke's response."""
    return f"preview-python-{uuid.uuid4()}"


def test_sync_client_registers_plans_and_applies_against_the_service(preview_env: dict[str, Any]) -> None:
    """The typed client completes register → validate → plan → apply → results live."""
    mutated_type = seed_source_branch(preview_env)
    assert device_types(infrahub_client(preview_env), SMOKE_BRANCH)[SHARED_DEVICE_NAME] != mutated_type

    with SyncClient(preview_env["urls"]["sync_api"], preview_env["bearer_token"], timeout=30.0) as client:
        registered = client.register_config(
            ConfigMutationRequest(
                package=smoke_package(preview_env["urls"]["infrahub"]),
                reason="preview Python client smoke: register the smoke configuration",
            ),
            _key(),
        )
        config_id = registered.version.config_id
        registry_version = registered.version.registry_version

        report = client.validate_config(config_id, registry_version)
        assert report.findings == (), report

        accepted = client.plan(
            CreateRunRequest(
                operation="plan",
                config_id=config_id,
                registry_version=registry_version,
                branch=SMOKE_BRANCH,
                reason="preview Python client smoke: create a service plan",
            ),
            _key(),
        )
        run_id = accepted.run.run_id
        planned = client.wait_for_run(accepted, timeout=WAIT_TIMEOUT_SECONDS, poll_interval=POLL_INTERVAL_SECONDS)
        assert planned.run.phase == "planned", planned.run

        plan = client.get_plan(run_id)
        assert plan.checksum_ok is True, plan
        # The same guard the Sync API smoke applies: a delete-only plan applies cleanly
        # and writes nothing, so an apply over one would prove nothing about the client.
        assert unwritten_plan_reasons(plan.summary.model_dump()) == [], plan.summary
        assert plan.summary.by_action.get("update", 0) == 1, plan.summary

        apply_accepted = client.apply(
            run_id,
            ApplyRunRequest(
                expected_checksum=plan.checksum,
                confirm_writes=True,
                branch=SMOKE_BRANCH,
                reason="preview Python client smoke: apply the reviewed plan",
            ),
            _key(),
        )
        applied = client.wait_for_run(apply_accepted, timeout=WAIT_TIMEOUT_SECONDS, poll_interval=POLL_INTERVAL_SECONDS)
        assert applied.run.phase == "applied", applied.run

        results = client.get_results(run_id)
        assert results.run_id == run_id
        applied_summary = results.results["summary"]
        assert applied_summary.get("update", 0) > 0, applied_summary
        assert applied_summary.get("delete", 0) == 0, applied_summary

    assert (
        canary_leaks(
            preview_env["infrahub_token"],
            {
                "Python lifecycle register resource": registered,
                "Python lifecycle validation resource": report,
                "Python lifecycle plan accepted resource": accepted,
                "Python lifecycle planned resource": planned,
                "Python lifecycle plan resource": plan,
                "Python lifecycle apply accepted resource": apply_accepted,
                "Python lifecycle applied resource": applied,
                "Python lifecycle results resource": results,
            },
        )
        == []
    )

    assert device_types(infrahub_client(preview_env), SMOKE_BRANCH)[SHARED_DEVICE_NAME] == mutated_type
