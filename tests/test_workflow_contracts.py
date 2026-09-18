"""Invariants of the SDK update workflow.

`update-infrahub-sdk.yml` runs unattended against `main`: it checks the branch out,
moves the locked infrahub-sdk version and opens a pull request back to that same
branch. Each of those three parts reads `matrix.branch-name`, so they have to keep
agreeing with each other, and the update itself has to stay lock-only so the declared
version range in `pyproject.toml` is never rewritten by a bot.

Steps are located by what they do — the action they use, the command they run — not by
their display names, so renaming a step does not break these tests.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

WORKFLOW = Path(__file__).parent.parent / ".github" / "workflows" / "update-infrahub-sdk.yml"

LOCK_COMMAND = 'uv lock --upgrade-package "infrahub-sdk==${INFRAHUB_SDK_VERSION}"'


@pytest.fixture(name="steps")
def steps_fixture(job: dict[str, Any]) -> list[dict[str, Any]]:
    """The steps of the update job."""

    return job["steps"]


@pytest.fixture(name="job")
def job_fixture() -> dict[str, Any]:
    """The single job in the SDK update workflow."""

    workflow = yaml.safe_load(WORKFLOW.read_text())
    return workflow["jobs"]["update-dependencies"]


def _only(candidates: list[dict[str, Any]], what: str) -> dict[str, Any]:
    """The one step matching a description, or a readable failure."""

    assert len(candidates) == 1, f"expected exactly one step that {what}, found {len(candidates)}"
    return candidates[0]


def _checkout_step(steps: list[dict[str, Any]]) -> dict[str, Any]:
    return _only(
        [step for step in steps if str(step.get("uses", "")).startswith("actions/checkout")], "checks out code"
    )


def _lock_step(steps: list[dict[str, Any]]) -> dict[str, Any]:
    return _only([step for step in steps if "uv lock" in str(step.get("run", ""))], "locks the SDK version")


def _pull_request_step(steps: list[dict[str, Any]]) -> dict[str, Any]:
    return _only([step for step in steps if "gh pr create" in str(step.get("run", ""))], "opens the pull request")


def test_the_matrix_targets_main_only(job: dict[str, Any]) -> None:
    assert job["strategy"]["matrix"]["branch-name"] == ["main"]


def test_the_checkout_takes_the_matrix_branch(steps: list[dict[str, Any]]) -> None:
    """Without an explicit ref the run would update whatever the dispatch default is."""

    checkout = _checkout_step(steps)["with"]

    assert checkout["ref"] == "${{ matrix.branch-name }}"
    assert checkout["persist-credentials"] is False


def test_the_update_only_moves_the_lockfile(steps: list[dict[str, Any]]) -> None:
    """`uv add` would pin the declared range; `uv lock` leaves pyproject.toml alone."""

    assert LOCK_COMMAND in _lock_step(steps)["run"]
    assert not [step for step in steps if "uv add" in str(step.get("run", ""))]


def test_the_pull_request_targets_the_matrix_branch(steps: list[dict[str, Any]]) -> None:
    create_pr = _pull_request_step(steps)

    assert create_pr["env"]["MATRIX_BRANCH"] == "${{ matrix.branch-name }}"
    assert '--base "${MATRIX_BRANCH}"' in create_pr["run"]
