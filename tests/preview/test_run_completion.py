"""Completion parity: the one-run synchronization, and cancelling a run that is in flight.

`sync` plans, verifies and applies inside a single admitted run, so it is the one shipped
operation whose writes are never gated on a separate reviewed checksum — each interface
that offers it drives it here against the running service, with the destination read back
through the Infrahub SDK.

Cancellation races the worker by nature: a small plan can finish before the request lands.
The rows below assert the two outcomes the service defines for that race and record which
one happened, rather than retrying until one of them appears.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import TYPE_CHECKING, Any

import pytest

from infrahub_sync.client import APIError, SyncClient
from infrahub_sync.client.models import CancelRunRequest, ConfigMutationRequest, CreateRunRequest
from tasks.preview import SHARED_DEVICE_NAME, SMOKE_BRANCH
from tests.preview.evidence import canary_leaks
from tests.preview.test_cli_client import package_file, run_cli
from tests.preview.test_service_api import (
    authenticated_client,
    create_run_request,
    device_types,
    infrahub_client,
    register_request,
    seed_source_branch,
    smoke_package,
    wait_for_phase,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

pytestmark = pytest.mark.preview

WAIT_TIMEOUT_SECONDS = 240.0
POLL_INTERVAL_SECONDS = 3.0
SYNC_REASON = "preview qualification: synchronize in one run"
CANCEL_REASON = "preview qualification: cancel a run in flight"
CLAIM_TIMEOUT_SECONDS = 120.0
# The states Prefect reports once an execution can no longer be cancelled.
TERMINAL_EXECUTION_STATES = frozenset({"cancelled", "completed", "crashed", "failed"})


def _key() -> str:
    """A fresh key per mutation, so a re-run never replays an earlier session's response."""
    return f"preview-completion-{uuid.uuid4()}"


def _sync_request(config_id: str, registry_version: int) -> dict[str, Any]:
    """The `POST /runs` body for the confirmed one-run synchronization."""
    return {
        **create_run_request(config_id, registry_version),
        "operation": "sync",
        "confirm_writes": True,
        "reason": SYNC_REASON,
    }


def _record_cancellation(evidence_dir: Path, interface: str, outcome: str) -> None:
    """Write which of the two defined cancellation outcomes this run produced."""
    (evidence_dir / f"cancellation-{interface}.txt").write_text(f"{outcome}\n", encoding="utf-8")


def _await_claimed_execution(read_state: Callable[[], str | None], run_id: str) -> str | None:
    """Poll until the admitted execution is running, or has already finished without us.

    Cancellation is defined against a claimed execution. Prefect refuses the
    scheduled-to-cancelling transition outright, and the service reports that refusal as an
    unavailable orchestration — a third answer neither documented outcome covers. Waiting
    for the worker to claim the run is what puts the request inside the window the two
    outcomes describe; the run finishing first is the second of them.
    """
    deadline = time.monotonic() + CLAIM_TIMEOUT_SECONDS
    state: str | None = None
    while time.monotonic() < deadline:
        state = read_state()
        if state == "running" or state in TERMINAL_EXECUTION_STATES:
            return state
        time.sleep(0.25)
    return pytest.fail(f"run {run_id} was not claimed within {CLAIM_TIMEOUT_SECONDS}s (last state {state!r})")


def test_the_cli_synchronizes_in_one_run(preview_env: dict[str, Any], tmp_path: Path) -> None:
    """`sync` plans and applies under one run id, and the destination carries the value."""
    mutated_type = seed_source_branch(preview_env)
    registered = run_cli(
        preview_env,
        "configs",
        "register",
        str(package_file(preview_env, tmp_path)),
        "--reason",
        SYNC_REASON,
    )

    completed = run_cli(
        preview_env,
        "sync",
        "--config-id",
        registered["config_id"],
        "--version",
        registered["registry_version"],
        "--branch",
        SMOKE_BRANCH,
        "--reason",
        SYNC_REASON,
        "--wait-timeout",
        str(int(WAIT_TIMEOUT_SECONDS)),
        "--poll-interval",
        str(int(POLL_INTERVAL_SECONDS)),
    )
    assert completed["operation"] == "sync"
    assert completed["phase"] == "applied"

    with authenticated_client(preview_env) as client:
        results = client.get(f"/runs/{completed['run_id']}/results")
        assert results.status_code == 200, results.text
        recorded = results.json()["results"]
        assert recorded["operation"] == "sync"
        assert recorded["summary"]["update"] > 0, recorded

    assert device_types(infrahub_client(preview_env), SMOKE_BRANCH)[SHARED_DEVICE_NAME] == mutated_type
    assert canary_leaks(preview_env["infrahub_token"], {"sync stdout": json.dumps(completed)}) == []


def test_the_python_client_synchronizes_in_one_run(preview_env: dict[str, Any]) -> None:
    """`SyncClient.sync` completes the confirmed one-run synchronization and records it."""
    mutated_type = seed_source_branch(preview_env)

    with SyncClient(preview_env["urls"]["sync_api"], preview_env["bearer_token"], timeout=30.0) as client:
        registered = client.register_config(
            ConfigMutationRequest(package=smoke_package(preview_env["urls"]["infrahub"]), reason=SYNC_REASON),
            _key(),
        )
        accepted = client.sync(
            CreateRunRequest(
                operation="sync",
                config_id=registered.version.config_id,
                registry_version=registered.version.registry_version,
                branch=SMOKE_BRANCH,
                confirm_writes=True,
                reason=SYNC_REASON,
            ),
            _key(),
        )
        applied = client.wait_for_run(accepted, timeout=WAIT_TIMEOUT_SECONDS, poll_interval=POLL_INTERVAL_SECONDS)
        assert applied.run.phase == "applied", applied.run

        results = client.get_results(accepted.run.run_id)
        assert results.results["operation"] == "sync"
        assert results.results["summary"]["update"] > 0, results.results

    assert device_types(infrahub_client(preview_env), SMOKE_BRANCH)[SHARED_DEVICE_NAME] == mutated_type
    assert canary_leaks(preview_env["infrahub_token"], {"sync run resource": applied, "results": results}) == []


def test_raw_http_synchronizes_in_one_run(preview_env: dict[str, Any], evidence_dir: Path) -> None:
    """The confirmed one-run synchronization over the wire, with the exchange captured."""
    mutated_type = seed_source_branch(preview_env)
    transcript = evidence_dir / "run-sync-http.jsonl"

    with authenticated_client(preview_env, transcript=transcript) as client:
        registered = client.post(
            "/configs",
            headers={"Idempotency-Key": _key()},
            json=register_request(preview_env["urls"]["infrahub"]),
        )
        assert registered.status_code == 201, registered.text
        version = registered.json()["version"]

        created = client.post(
            "/runs",
            headers={"Idempotency-Key": _key()},
            json=_sync_request(version["config_id"], version["registry_version"]),
        )
        assert created.status_code == 202, created.text
        run_id = created.json()["run"]["run_id"]

        applied = wait_for_phase(client, run_id, "applied")
        assert applied["run"]["summary"]["update"] > 0, applied["run"]
        assert applied["run"]["summary"]["delete"] == 0, applied["run"]

    assert device_types(infrahub_client(preview_env), SMOKE_BRANCH)[SHARED_DEVICE_NAME] == mutated_type
    captured = transcript.read_text(encoding="utf-8")
    assert ("POST", "/runs", 202) in {
        (json.loads(line)["method"], json.loads(line)["path"], json.loads(line)["status"])
        for line in captured.splitlines()
    }
    assert canary_leaks(preview_env["infrahub_token"], {str(transcript): captured}) == []


def test_the_python_client_cancels_a_run_it_has_just_admitted(
    preview_env: dict[str, Any], evidence_dir: Path, deliberate_terminal_flow_runs: dict[str, str]
) -> None:
    """Either the run reaches the cancelled terminal state, or the cancel is refused as late."""
    seed_source_branch(preview_env)

    with SyncClient(preview_env["urls"]["sync_api"], preview_env["bearer_token"], timeout=30.0) as client:
        registered = client.register_config(
            ConfigMutationRequest(package=smoke_package(preview_env["urls"]["infrahub"]), reason=CANCEL_REASON),
            _key(),
        )
        accepted = client.plan(
            CreateRunRequest(
                operation="plan",
                config_id=registered.version.config_id,
                registry_version=registered.version.registry_version,
                branch=SMOKE_BRANCH,
                reason=CANCEL_REASON,
            ),
            _key(),
        )
        run_id = accepted.run.run_id
        flow_run_id = accepted.orchestration[-1].flow_run_id
        _await_claimed_execution(lambda: client.get_run(run_id).orchestration[-1].state, run_id)

        refusal: APIError | None = None
        try:
            client.cancel_run(run_id, CancelRunRequest(reason=CANCEL_REASON), _key())
        except APIError as error:
            refusal = error

    if refusal is not None:
        # The execution finished between admission and this request. The refusal, not a
        # retry, is the contract for that: the service will not cancel a terminal run.
        assert refusal.status == 409, refusal
        assert refusal.code == "execution-terminal", refusal
        _record_cancellation(evidence_dir, "python", "refused: execution-terminal")
        return

    with authenticated_client(preview_env) as api:
        cancelled = wait_for_phase(api, run_id, "cancelled")
    assert cancelled["run"]["outcome"] == "cancelled", cancelled["run"]
    deliberate_terminal_flow_runs[flow_run_id] = "CANCELLED"
    _record_cancellation(evidence_dir, "python", "accepted: run cancelled")


def test_raw_http_cancels_a_run_it_has_just_admitted(
    preview_env: dict[str, Any], evidence_dir: Path, deliberate_terminal_flow_runs: dict[str, str]
) -> None:
    """The same two outcomes over the wire: 202 then cancelled, or 409 `execution-terminal`."""
    seed_source_branch(preview_env)
    transcript = evidence_dir / "run-cancel-http.jsonl"

    with authenticated_client(preview_env, transcript=transcript) as client:
        registered = client.post(
            "/configs",
            headers={"Idempotency-Key": _key()},
            json=register_request(preview_env["urls"]["infrahub"]),
        )
        assert registered.status_code == 201, registered.text
        version = registered.json()["version"]

        created = client.post(
            "/runs",
            headers={"Idempotency-Key": _key()},
            json=create_run_request(version["config_id"], version["registry_version"]),
        )
        assert created.status_code == 202, created.text
        admitted = created.json()
        run_id = admitted["run"]["run_id"]
        flow_run_id = admitted["orchestration"][-1]["flow_run_id"]
        _await_claimed_execution(lambda: client.get(f"/runs/{run_id}").json()["orchestration"][-1]["state"], run_id)

        cancel = client.post(
            f"/runs/{run_id}/cancel",
            headers={"Idempotency-Key": _key()},
            json={"reason": CANCEL_REASON},
        )
        assert cancel.status_code in {202, 409}, cancel.text
        if cancel.status_code == 409:
            assert cancel.json()["error"]["code"] == "execution-terminal", cancel.text
            _record_cancellation(evidence_dir, "http", "refused: execution-terminal")
        else:
            cancelled = wait_for_phase(client, run_id, "cancelled")
            assert cancelled["run"]["outcome"] == "cancelled", cancelled["run"]
            deliberate_terminal_flow_runs[flow_run_id] = "CANCELLED"
            _record_cancellation(evidence_dir, "http", "accepted: run cancelled")

    captured = transcript.read_text(encoding="utf-8")
    assert ("POST", f"/runs/{run_id}/cancel", cancel.status_code) in {
        (json.loads(line)["method"], json.loads(line)["path"], json.loads(line)["status"])
        for line in captured.splitlines()
    }
    assert canary_leaks(preview_env["infrahub_token"], {str(transcript): captured}) == []
