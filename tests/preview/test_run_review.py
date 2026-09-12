"""Review parity: what an operator can read about a run before approving its writes.

Everything here stops short of an apply. The saved plan is reviewed through the CLI in
summary and in detail, the bounded wait is proved to expire without touching the remote
run, and the two review resources the CLI does not ship — verification, and the saved-plan
artifact with its digest — are driven through the typed client and over raw HTTP.

Each test registers its own configuration and creates its own run, because a review row
has to observe a plan whose operations it set up: the shared smoke branch converges as the
other modules apply to it, and a plan with nothing in it renders no operation to filter.
"""

from __future__ import annotations

import json
import time
from hashlib import sha256
from typing import TYPE_CHECKING, Any

import pytest

from infrahub_sync.client import SyncClient
from infrahub_sync.client.models import ConfigMutationRequest, CreateRunRequest, VerifyRunRequest
from tasks.preview import SHARED_DEVICE_NAME, SMOKE_BRANCH, SMOKE_KIND
from tests.preview.evidence import canary_leaks
from tests.preview.test_cli_client import ANSI, fields, package_file, run_cli, run_cli_command
from tests.preview.test_service_api import (
    authenticated_client,
    create_run_request,
    idempotency_headers,
    register_request,
    seed_source_branch,
    smoke_package,
    unwritten_plan_reasons,
    wait_for_phase,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.preview

WAIT_TIMEOUT_SECONDS = 240.0
POLL_INTERVAL_SECONDS = 3.0
PLAN_ARTIFACT_ID = "plan-review"
REASON = "preview qualification: review a saved plan"
# A kind the destination schema does not define, so `--kind` filters the plan to nothing.
ABSENT_KIND = "InfraNotAKind"


def _operation_lines(output: str) -> list[str]:
    """The `runs plan --detail` operation lines, which are the only ones naming an action."""
    return [line for line in ANSI.sub("", output).splitlines() if f" {SMOKE_KIND} " in line]


def _await_verification(client: Any, run_id: str) -> dict[str, Any]:  # noqa: ANN401 — the raw httpx client
    """Poll the results resource until the verification stage has recorded itself.

    Verification merges into a run that is already planned instead of moving it to a new
    phase, so the phase poll the other stages use has nothing here to observe.
    """
    deadline = time.monotonic() + WAIT_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        results = client.get(f"/runs/{run_id}/results")
        assert results.status_code == 200, results.text
        verification = results.json()["results"].get("verification")
        if verification is not None:
            return dict(verification)
        time.sleep(POLL_INTERVAL_SECONDS)
    pytest.fail(f"run {run_id} recorded no verification result within {WAIT_TIMEOUT_SECONDS}s")


def test_the_cli_admits_a_run_without_waiting_then_reviews_its_saved_plan(
    preview_env: dict[str, Any], tmp_path: Path, evidence_dir: Path
) -> None:
    """`--no-wait`, then `runs plan` in summary, in detail, filtered, and refused."""
    seed_source_branch(preview_env)
    artifacts: dict[str, object] = {}
    registered = run_cli(
        preview_env,
        "configs",
        "register",
        str(package_file(preview_env, tmp_path)),
        "--reason",
        REASON,
        artifacts=artifacts,
        artifact_name="run review configs register",
    )

    accepted = run_cli(
        preview_env,
        "diff",
        "--config-id",
        registered["config_id"],
        "--version",
        registered["registry_version"],
        "--branch",
        SMOKE_BRANCH,
        "--reason",
        REASON,
        "--no-wait",
        artifacts=artifacts,
        artifact_name="run review diff no-wait",
    )
    run_id = accepted["run_id"]
    # `--no-wait` renders the accepted run and stops. The plan review the waiting form
    # prints afterwards is the field that separates the two, so its absence is the row.
    assert "plan_checksum" not in accepted, accepted
    assert accepted["operation"] == "plan"

    oracle_transcript = evidence_dir / "run-review-cli-oracle-http.jsonl"
    with authenticated_client(preview_env, transcript=oracle_transcript) as client:
        planned = wait_for_phase(client, run_id, "planned")
        assert planned["run"]["outcome"] is not None, planned["run"]
        summary = client.get(f"/runs/{run_id}/plan").json()["summary"]
        assert unwritten_plan_reasons(summary) == [], summary

    reviewed = run_cli(
        preview_env,
        "runs",
        "plan",
        run_id,
        artifacts=artifacts,
        artifact_name="run review summary",
    )
    assert reviewed["checksum_ok"] == "true"
    assert reviewed["operations"] == str(summary["total"])

    detailed = run_cli_command(
        preview_env,
        "runs",
        "plan",
        run_id,
        "--detail",
        artifacts=artifacts,
        artifact_name="run review detail",
    )
    assert detailed.returncode == 0, detailed.stderr
    assert _operation_lines(detailed.stdout), detailed.stdout
    assert any(f"update {SMOKE_KIND} name={SHARED_DEVICE_NAME}" in line for line in _operation_lines(detailed.stdout))

    filtered = run_cli_command(
        preview_env,
        "runs",
        "plan",
        run_id,
        "--detail",
        "--kind",
        SMOKE_KIND,
        artifacts=artifacts,
        artifact_name="run review filtered detail",
    )
    assert filtered.returncode == 0, filtered.stderr
    assert _operation_lines(filtered.stdout) == _operation_lines(detailed.stdout)

    # `--kind` is a filter over the detailed list, so it is refused without it rather than
    # silently ignored; a kind that matches nothing is refused the same way.
    for index, arguments in enumerate((("--kind", SMOKE_KIND), ("--detail", "--kind", ABSENT_KIND)), start=1):
        refused = run_cli_command(
            preview_env,
            "runs",
            "plan",
            run_id,
            *arguments,
            artifacts=artifacts,
            artifact_name=f"run review refusal {index}",
        )
        assert refused.returncode == 2, refused.stdout
        assert "error: client-input" in ANSI.sub("", refused.stderr)
        assert "argument: kind" in ANSI.sub("", refused.stderr)

    artifacts[str(oracle_transcript)] = oracle_transcript.read_text(encoding="utf-8")
    assert canary_leaks(preview_env["infrahub_token"], artifacts) == []


def test_the_cli_bounded_wait_expires_without_cancelling_the_run(
    preview_env: dict[str, Any], tmp_path: Path, evidence_dir: Path
) -> None:
    """An expired local wait is a non-zero exit; the remote run keeps going and completes."""
    seed_source_branch(preview_env)
    artifacts: dict[str, object] = {}
    registered = run_cli(
        preview_env,
        "configs",
        "register",
        str(package_file(preview_env, tmp_path)),
        "--reason",
        REASON,
        artifacts=artifacts,
        artifact_name="wait timeout configs register",
    )

    # Shorter than the worker's query interval, so the run cannot have finished: what the
    # command reports is the wait expiring, never the run failing.
    expired = run_cli_command(
        preview_env,
        "diff",
        "--config-id",
        registered["config_id"],
        "--version",
        registered["registry_version"],
        "--branch",
        SMOKE_BRANCH,
        "--reason",
        REASON,
        "--wait-timeout",
        "1",
        "--poll-interval",
        "1",
        artifacts=artifacts,
        artifact_name="wait timeout diff",
    )
    assert expired.returncode == 1, expired.stdout
    assert "error: run-wait-timeout" in ANSI.sub("", expired.stderr)
    run_id = fields(expired.stdout)["run_id"]

    oracle_transcript = evidence_dir / "wait-timeout-cli-oracle-http.jsonl"
    with authenticated_client(preview_env, transcript=oracle_transcript) as client:
        # The bound is local. Nothing was cancelled, so the service still completes it.
        planned = wait_for_phase(client, run_id, "planned")

    assert planned["run"]["outcome"] is not None, planned["run"]
    artifacts[str(oracle_transcript)] = oracle_transcript.read_text(encoding="utf-8")
    assert canary_leaks(preview_env["infrahub_token"], artifacts) == []


def test_the_python_client_verifies_a_plan_and_reads_its_artifact_and_results(
    preview_env: dict[str, Any],
) -> None:
    """`verify`, `list_artifacts`, `get_artifact` with its digest, and `get_results`."""
    seed_source_branch(preview_env)

    with SyncClient(preview_env["urls"]["sync_api"], preview_env["bearer_token"], timeout=30.0) as client:
        registered = client.register_config(
            ConfigMutationRequest(package=smoke_package(preview_env["urls"]["infrahub"]), reason=REASON),
            idempotency_headers("preview-review")["Idempotency-Key"],
        )
        accepted = client.plan(
            CreateRunRequest(
                operation="plan",
                config_id=registered.version.config_id,
                registry_version=registered.version.registry_version,
                branch=SMOKE_BRANCH,
                reason=REASON,
            ),
            idempotency_headers("preview-review")["Idempotency-Key"],
        )
        run_id = accepted.run.run_id
        planned = client.wait_for_run(accepted, timeout=WAIT_TIMEOUT_SECONDS, poll_interval=POLL_INTERVAL_SECONDS)
        assert planned.run.phase == "planned"
        plan = client.get_plan(run_id)

        verified = client.verify_run(
            run_id,
            VerifyRunRequest(reason=REASON),
            idempotency_headers("preview-review")["Idempotency-Key"],
        )
        verification_completed = client.wait_for_run(
            verified, timeout=WAIT_TIMEOUT_SECONDS, poll_interval=POLL_INTERVAL_SECONDS
        )

        results = client.get_results(run_id)
        verification = results.results["verification"]
        assert verification["outcome"] == "verified"
        assert verification["checksum"] == plan.checksum
        assert verification["checksum_ok"] is True

        artifacts = client.list_artifacts(run_id)
        assert [reference.artifact_id for reference in artifacts.artifacts] == [PLAN_ARTIFACT_ID]
        reference = artifacts.artifacts[0]
        content = client.get_artifact(run_id, PLAN_ARTIFACT_ID)
        # The client verifies the declared digest itself; recomputing here is what proves
        # the bytes it returned are the bytes that digest describes.
        assert content.digest == reference.digest
        assert sha256(content.data).hexdigest() == reference.digest
        assert json.loads(content.data)["checksum"] == plan.checksum

    assert (
        canary_leaks(
            preview_env["infrahub_token"],
            {
                "get_plan resource": plan,
                "register_config resource": registered,
                "plan accepted resource": accepted,
                "planned resource": planned,
                "verify accepted resource": verified,
                "verification completed resource": verification_completed,
                "get_results resource": results,
                "list_artifacts resource": artifacts,
                "plan-review artifact bytes": content.data,
            },
        )
        == []
    )


def test_raw_http_verifies_a_plan_and_reads_its_artifact_and_results(
    preview_env: dict[str, Any], evidence_dir: Path
) -> None:
    """The same three review resources over the wire, with the exchange captured."""
    seed_source_branch(preview_env)
    transcript = evidence_dir / "run-review-http.jsonl"

    with authenticated_client(preview_env, transcript=transcript) as client:
        registered = client.post(
            "/configs",
            headers=idempotency_headers("preview-review"),
            json=register_request(preview_env["urls"]["infrahub"]),
        )
        assert registered.status_code == 201, registered.text
        version = registered.json()["version"]

        created = client.post(
            "/runs",
            headers=idempotency_headers("preview-review"),
            json=create_run_request(version["config_id"], version["registry_version"]),
        )
        assert created.status_code == 202, created.text
        run_id = created.json()["run"]["run_id"]
        wait_for_phase(client, run_id, "planned")
        checksum = client.get(f"/runs/{run_id}/plan").json()["checksum"]

        verify = client.post(
            f"/runs/{run_id}/verify",
            headers=idempotency_headers("preview-review"),
            json={"reason": REASON},
        )
        assert verify.status_code == 202, verify.text
        verification = _await_verification(client, run_id)
        assert verification["outcome"] == "verified"
        assert verification["checksum"] == checksum

        listed = client.get(f"/runs/{run_id}/artifacts")
        assert listed.status_code == 200, listed.text
        reference = listed.json()["artifacts"][0]
        assert reference["artifact_id"] == PLAN_ARTIFACT_ID

        artifact = client.get(f"/runs/{run_id}/artifacts/{PLAN_ARTIFACT_ID}")
        assert artifact.status_code == 200, artifact.text
        # The digest is a response header on this route, and it is what a client without
        # the typed helper has to check the bytes against.
        assert artifact.headers["Digest"] == f"sha-256={reference['digest']}"
        assert sha256(artifact.content).hexdigest() == reference["digest"]
        assert json.loads(artifact.content)["checksum"] == checksum

    captured = transcript.read_text(encoding="utf-8")
    recorded = {(json.loads(line)["method"], json.loads(line)["path"]) for line in captured.splitlines()}
    assert recorded >= {
        ("POST", f"/runs/{run_id}/verify"),
        ("GET", f"/runs/{run_id}/results"),
        ("GET", f"/runs/{run_id}/artifacts"),
        ("GET", f"/runs/{run_id}/artifacts/{PLAN_ARTIFACT_ID}"),
    }
    assert (
        canary_leaks(
            preview_env["infrahub_token"],
            {str(transcript): captured, "raw plan-review artifact bytes": artifact.content},
        )
        == []
    )
