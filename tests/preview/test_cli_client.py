"""CLI surface: the shipped console script drives one registered lifecycle.

The Sync API smoke proves the wire; this proves the entrypoint a developer actually
types. It runs `infrahub-sync` as the installed console script in its own process, so
what is exercised is the packaged command, its environment settings, and its exit
codes — not an in-process Typer invocation that would skip all three.

The CLI has no results command, so final state is read back through the HTTP API and
Infrahub itself. The assertions are outcomes: the run the service recorded, the counts
it applied, and the value the destination branch ends up carrying. Field text is parsed
only to carry the service-issued run id and reviewed checksum from one command to the
next, which is what the documented review cycle asks an operator to do by hand.

The lifecycle is the same shape as the Sync API smoke and shares its setup, so the two
converge on the same branch in either order: the source is mirrored from the destination
first and only the shared device is left differing, leaving exactly one update to plan.
"""

from __future__ import annotations

import json
import os
import re
import subprocess  # noqa: S404 -- fixed argv invocation of this repository's own console script
import uuid
from typing import TYPE_CHECKING, Any

import pytest

from tasks.preview import REPO_ROOT, SHARED_DEVICE_NAME, SMOKE_BRANCH
from tests.preview.evidence import canary_leaks
from tests.preview.test_service_api import (
    authenticated_client,
    device_types,
    infrahub_client,
    seed_source_branch,
    smoke_package,
    unwritten_plan_reasons,
)

if TYPE_CHECKING:
    from collections.abc import MutableMapping
    from pathlib import Path

pytestmark = pytest.mark.preview

# The CLI waits for its own run; bound it to the same budget the Sync API smoke polls
# against, and give the subprocess room to exit after that wait expires.
WAIT_TIMEOUT_SECONDS = 240
POLL_INTERVAL_SECONDS = 3
PROCESS_TIMEOUT_SECONDS = WAIT_TIMEOUT_SECONDS + 60

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def cli_environment(preview_env: dict[str, Any]) -> dict[str, str]:
    """The settings one CLI invocation needs, with the renderer pinned.

    `NO_COLOR` and a fixed `COLUMNS` remove the only two things that make the shipped
    output depend on the terminal it happens to run in: colour escapes and the width
    Rich wraps a long field value at. A wrapped run id is not a run id.
    """
    return {
        **os.environ,
        "INFRAHUB_SYNC_API_URL": preview_env["urls"]["sync_api"],
        "INFRAHUB_SYNC_API_TOKEN": preview_env["bearer_token"],
        "NO_COLOR": "1",
        "COLUMNS": "200",
    }


def fields(output: str) -> dict[str, str]:
    """Parse the CLI's `name: value` field lines, ignoring anything else it prints."""
    fields: dict[str, str] = {}
    for line in ANSI.sub("", output).splitlines():
        name, separator, value = line.partition(": ")
        if separator and name.isidentifier():
            fields[name] = value.strip()
    return fields


def run_cli_command(
    preview_env: dict[str, Any],
    *arguments: str,
    artifacts: MutableMapping[str, object],
    artifact_name: str,
) -> subprocess.CompletedProcess[str]:
    """Run the installed console script and return the completed process, whatever it did.

    The exit code is part of the shipped contract for several rows — a refused `--kind`
    filter, an expired bounded wait — so the process is returned rather than asserted on.
    """
    completed = subprocess.run(  # noqa: S603
        ["uv", "run", "infrahub-sync", *arguments],  # noqa: S607 — resolved from PATH by design
        cwd=REPO_ROOT,
        env=cli_environment(preview_env),
        capture_output=True,
        text=True,
        timeout=PROCESS_TIMEOUT_SECONDS,
        check=False,
    )
    artifacts[f"{artifact_name} stdout"] = completed.stdout
    artifacts[f"{artifact_name} stderr"] = completed.stderr
    return completed


def run_cli(
    preview_env: dict[str, Any],
    *arguments: str,
    artifacts: MutableMapping[str, object],
    artifact_name: str,
) -> dict[str, str]:
    """Run the installed console script to success and return its parsed fields."""
    completed = run_cli_command(
        preview_env,
        *arguments,
        artifacts=artifacts,
        artifact_name=artifact_name,
    )
    assert completed.returncode == 0, (
        f"`infrahub-sync {' '.join(arguments)}` exited {completed.returncode}\n"
        f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )
    return fields(completed.stdout)


def package_file(preview_env: dict[str, Any], directory: Path) -> Path:
    """Write the smoke package where `configs register` can read it as an argument."""
    path = directory / "preview-smoke-package.json"
    path.write_text(json.dumps(smoke_package(preview_env["urls"]["infrahub"])), encoding="utf-8")
    return path


def test_cli_registers_plans_reviews_and_applies_against_the_service(  # noqa: PLR0914
    preview_env: dict[str, Any], tmp_path: Path, evidence_dir: Path
) -> None:
    """`configs register` → `diff` → `runs plan` → `apply`, proved through the API."""
    mutated_type = seed_source_branch(preview_env)
    assert device_types(infrahub_client(preview_env), SMOKE_BRANCH)[SHARED_DEVICE_NAME] != mutated_type
    artifacts: dict[str, object] = {}

    registered = run_cli(
        preview_env,
        "configs",
        "register",
        str(package_file(preview_env, tmp_path)),
        "--reason",
        "preview CLI smoke: register the smoke configuration",
        "--idempotency-key",
        f"preview-cli-{uuid.uuid4()}",
        artifacts=artifacts,
        artifact_name="CLI lifecycle configs register",
    )
    config_id, registry_version = registered["config_id"], registered["registry_version"]

    planned = run_cli(
        preview_env,
        "diff",
        "--config-id",
        config_id,
        "--version",
        registry_version,
        "--branch",
        SMOKE_BRANCH,
        "--reason",
        "preview CLI smoke: create a service plan",
        "--idempotency-key",
        f"preview-cli-{uuid.uuid4()}",
        "--wait-timeout",
        str(WAIT_TIMEOUT_SECONDS),
        "--poll-interval",
        str(POLL_INTERVAL_SECONDS),
        artifacts=artifacts,
        artifact_name="CLI lifecycle diff",
    )
    run_id, checksum = planned["run_id"], planned["plan_checksum"]

    oracle_transcript = evidence_dir / "cli-lifecycle-oracle-http.jsonl"
    with authenticated_client(preview_env, transcript=oracle_transcript) as client:
        plan = client.get(f"/runs/{run_id}/plan")
        assert plan.status_code == 200, plan.text
        summary = plan.json()["summary"]
        # The same guard the Sync API smoke applies: a delete-only plan applies cleanly
        # and writes nothing, so an apply over one would prove nothing about the CLI.
        assert unwritten_plan_reasons(summary) == [], summary
        assert summary["by_action"].get("update", 0) == 1, summary

    reviewed = run_cli(
        preview_env,
        "runs",
        "plan",
        run_id,
        artifacts=artifacts,
        artifact_name="CLI lifecycle runs plan",
    )
    assert reviewed["run_id"] == run_id
    assert reviewed["plan_checksum"] == checksum
    assert reviewed["checksum_ok"] == "true"

    run_cli(
        preview_env,
        "apply",
        run_id,
        "--expected-checksum",
        checksum,
        "--branch",
        SMOKE_BRANCH,
        "--reason",
        "preview CLI smoke: apply the reviewed plan",
        "--idempotency-key",
        f"preview-cli-{uuid.uuid4()}",
        "--wait-timeout",
        str(WAIT_TIMEOUT_SECONDS),
        "--poll-interval",
        str(POLL_INTERVAL_SECONDS),
        artifacts=artifacts,
        artifact_name="CLI lifecycle apply",
    )

    results_transcript = evidence_dir / "cli-lifecycle-results-oracle-http.jsonl"
    with authenticated_client(preview_env, transcript=results_transcript) as client:
        recorded = client.get(f"/runs/{run_id}")
        assert recorded.status_code == 200, recorded.text
        assert recorded.json()["run"]["phase"] == "applied", recorded.text
        results = client.get(f"/runs/{run_id}/results")
        assert results.status_code == 200, results.text
        applied_summary = results.json()["results"]["summary"]
        assert applied_summary.get("update", 0) > 0, applied_summary
        assert applied_summary.get("delete", 0) == 0, applied_summary

    artifacts.update(
        {
            str(oracle_transcript): oracle_transcript.read_text(encoding="utf-8"),
            "CLI lifecycle results body": results.content,
        }
    )
    artifacts[str(results_transcript)] = results_transcript.read_text(encoding="utf-8")
    assert canary_leaks(preview_env["infrahub_token"], artifacts) == []

    assert device_types(infrahub_client(preview_env), SMOKE_BRANCH)[SHARED_DEVICE_NAME] == mutated_type
