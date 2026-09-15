"""Invariants of the SDK update workflow.

`update-infrahub-sdk.yml` runs unattended against `main`: it checks the branch
out, moves the locked infrahub-sdk version and opens a pull request back to that
same branch. Each of those three parts reads `matrix.branch-name`, so they have
to keep agreeing with each other, and the update itself has to stay lock-only so
the declared version range in `pyproject.toml` is never rewritten by a bot.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

WORKFLOW = Path(__file__).parent.parent / ".github" / "workflows" / "update-infrahub-sdk.yml"


@pytest.fixture(name="job")
def job_fixture() -> dict[str, Any]:
    """The single job in the SDK update workflow."""

    workflow = yaml.safe_load(WORKFLOW.read_text())
    return workflow["jobs"]["update-dependencies"]


def _step(job: dict[str, Any], name_startswith: str) -> dict[str, Any]:
    """The one step whose name starts with ``name_startswith``."""

    steps = [step for step in job["steps"] if str(step.get("name", "")).startswith(name_startswith)]
    assert len(steps) == 1, f"expected one step named {name_startswith!r}, found {len(steps)}"
    return steps[0]


def test_the_matrix_targets_main_only(job: dict[str, Any]) -> None:
    assert job["strategy"]["matrix"]["branch-name"] == ["main"]


def test_the_checkout_takes_the_matrix_branch(job: dict[str, Any]) -> None:
    """Without an explicit ref the run would update whatever the dispatch default is."""

    checkout = _step(job, "Check out code")

    assert checkout["with"]["ref"] == "${{ matrix.branch-name }}"
    assert checkout["with"]["persist-credentials"] is False


def test_the_update_only_moves_the_lockfile(job: dict[str, Any]) -> None:
    """`uv add` would pin the declared range; `uv lock` leaves pyproject.toml alone."""

    run = _step(job, "Update infrahub-sdk to version")["run"]

    assert 'uv lock --upgrade-package "infrahub-sdk==${INFRAHUB_SDK_VERSION}"' in run
    assert "uv add" not in run


def test_the_pull_request_targets_the_matrix_branch(job: dict[str, Any]) -> None:
    create_pr = _step(job, "Create a pull request")

    assert create_pr["env"]["MATRIX_BRANCH"] == "${{ matrix.branch-name }}"
    assert '--base "${MATRIX_BRANCH}"' in create_pr["run"]
