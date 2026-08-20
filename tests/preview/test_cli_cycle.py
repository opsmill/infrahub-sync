"""CLI surface: plan, offline review, checksum-gated apply, refusal, convergence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from infrahub_sync.cli import app
from tasks.preview import SMOKE_BRANCH

pytestmark = pytest.mark.preview

WRONG_CHECKSUM = "0" * 64


def _run_ids(cache_dir: Path) -> set[str]:
    sync_root = cache_dir / "custom-example"
    if not sync_root.is_dir():
        return set()
    return {entry.name for entry in sync_root.iterdir() if entry.is_dir()}


def _manifest_checksum(cache_dir: Path, run_id: str) -> str:
    run_dir = cache_dir / "custom-example" / run_id
    manifests = sorted(run_dir.rglob("*manifest*.json"))
    assert manifests, f"no plan manifest under {run_dir}"
    return json.loads(manifests[0].read_text(encoding="utf-8"))["plan_checksum"]


def test_cli_plan_review_apply_refusal_and_convergence(cli_environment: dict[str, Any]) -> None:
    runner = CliRunner()
    cache_dir: Path = cli_environment["cache_dir"]
    examples = cli_environment["examples_dir"]

    before = _run_ids(cache_dir)
    planned = runner.invoke(
        app,
        ["diff", "--name", "custom-example", "--directory", examples, "--branch", SMOKE_BRANCH],
    )
    assert planned.exit_code == 0, planned.output
    created = _run_ids(cache_dir) - before
    assert len(created) == 1, f"expected exactly one new run, got {created}"
    run_id = created.pop()
    checksum = _manifest_checksum(cache_dir, run_id)

    review = runner.invoke(
        app,
        ["diff", "--name", "custom-example", "--directory", examples, "--from-plan", run_id, "--detail"],
    )
    assert review.exit_code == 0, review.output
    assert checksum in review.output, "offline review must print the manifest's plan checksum"

    refused = runner.invoke(
        app,
        [
            "apply",
            "--name",
            "custom-example",
            "--directory",
            examples,
            "--run-id",
            run_id,
            "--expected-checksum",
            WRONG_CHECKSUM,
            "--branch",
            SMOKE_BRANCH,
        ],
    )
    assert refused.exit_code != 0, "apply must refuse a checksum that does not match the reviewed plan"

    applied = runner.invoke(
        app,
        [
            "apply",
            "--name",
            "custom-example",
            "--directory",
            examples,
            "--run-id",
            run_id,
            "--expected-checksum",
            checksum,
            "--branch",
            SMOKE_BRANCH,
        ],
    )
    assert applied.exit_code == 0, applied.output

    convergence = runner.invoke(
        app,
        ["diff", "--name", "custom-example", "--directory", examples, "--branch", SMOKE_BRANCH],
    )
    assert convergence.exit_code == 0, convergence.output
    new_run = (_run_ids(cache_dir) - before) - {run_id}
    assert len(new_run) == 1
    plan_parquet = cache_dir / "custom-example" / new_run.pop() / "plan.parquet"
    assert plan_parquet.exists(), "convergence re-plan must produce a plan artifact"
    import pyarrow.parquet as pq

    assert pq.read_table(plan_parquet).num_rows == 0, "a converged destination must re-plan to zero operations"
