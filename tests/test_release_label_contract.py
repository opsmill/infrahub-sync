"""Regression tests for the release bump label contract."""

from __future__ import annotations

import json
import subprocess  # noqa: S404
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[1]
CONFIG_PATH = ROOT / ".github" / "version-drafter.yml"
LABELS_PATH = ROOT / ".github" / "labels.yml"
CHECKER_PATH = ROOT / "scripts" / "check_release_labels.py"

BUMP_LABELS = ("changes/major", "changes/minor", "changes/patch")
RELEASE_PR = {
    "title": "chore(release): 1.2.3",
    "head_ref": "release/1.2.3",
    "author_login": "opsmill-bot",
    "head_repository": "opsmill/example",
}


def run_checker(
    labels: list[str],
    *,
    title: str = "fix: example",
    head_ref: str = "feature/example",
    author_login: str = "contributor",
    head_repository: str = "contributor/example",
) -> subprocess.CompletedProcess[str]:
    """Run the label checker as the workflow does."""
    return subprocess.run(  # noqa: S603
        [
            sys.executable,
            str(CHECKER_PATH),
            "--labels-json",
            json.dumps(labels),
            "--title",
            title,
            "--head-ref",
            head_ref,
            "--author-login",
            author_login,
            "--head-repository",
            head_repository,
            "--repository",
            "opsmill/example",
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def test_version_drafter_only_uses_bump_labels() -> None:
    config = yaml.safe_load(CONFIG_PATH.read_text())
    assert config == {
        "major-labels": ["changes/major"],
        "minor-labels": ["changes/minor"],
        "patch-labels": ["changes/patch"],
    }


@pytest.mark.parametrize("label", BUMP_LABELS)
def test_bump_label_is_declared(label: str) -> None:
    declared = {entry["name"] for entry in yaml.safe_load(LABELS_PATH.read_text())}
    assert label in declared


@pytest.mark.parametrize("label", BUMP_LABELS)
def test_single_bump_label_is_accepted(label: str) -> None:
    result = run_checker([label, "type/housekeeping"])
    assert result.returncode == 0, result.stderr
    assert label in result.stdout


@pytest.mark.parametrize(
    "labels",
    [[], ["type/bug"], ["changes/patch", "changes/minor"]],
    ids=["no-labels", "type-only", "conflicting"],
)
def test_missing_or_conflicting_bump_label_is_rejected(labels: list[str]) -> None:
    result = run_checker(labels)
    assert result.returncode != 0
    assert "exactly one" in result.stderr


def test_generated_release_pull_request_is_exempt() -> None:
    result = run_checker([], **RELEASE_PR)
    assert result.returncode == 0, result.stderr
    assert "generated release pull request" in result.stdout


@pytest.mark.parametrize(
    "override",
    [
        {"author_login": "contributor", "head_repository": "contributor/example"},
        {"head_repository": "contributor/example"},
        {"title": "fix: example"},
    ],
    ids=["untrusted-author", "forked-bot", "non-release-title"],
)
def test_lookalike_release_pull_request_is_not_exempt(override: dict[str, str]) -> None:
    result = run_checker([], **{**RELEASE_PR, **override})
    assert result.returncode != 0
    assert "exactly one" in result.stderr
