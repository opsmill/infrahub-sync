"""Regression tests for the release bump label contract."""

from __future__ import annotations

import json
import re
import subprocess  # noqa: S404
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[1]
CONFIG_PATH = ROOT / ".github" / "version-drafter.yml"
LABELS_PATH = ROOT / ".github" / "labels.yml"
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "release-label-check.yml"
CHECKER_PATH = ROOT / "scripts" / "check_release_labels.py"

BUMP_LABELS = ("changes/major", "changes/minor", "changes/patch")
REPOSITORY = "opsmill/example"
RELEASE_PR = {
    "title": "chore(release): 1.2.3",
    "head_ref": "release/1.2.3",
    "author_login": "opsmill-bot",
    "head_repository": REPOSITORY,
}


CONTRIBUTOR_PR = {
    "title": "fix: example",
    "head_ref": "feature/example",
    "author_login": "contributor",
    "head_repository": "contributor/example",
}


def run_checker(tmp_path: Path, labels: list[str], **overrides: str) -> subprocess.CompletedProcess[str]:
    """Run the label checker against a `pull_request_target` payload, as the workflow does."""
    fields = {**CONTRIBUTOR_PR, **overrides}
    event = {
        "repository": {"full_name": REPOSITORY},
        "pull_request": {
            "title": fields["title"],
            "user": {"login": fields["author_login"]},
            "labels": [{"name": label} for label in labels],
            "head": {"ref": fields["head_ref"], "repo": {"full_name": fields["head_repository"]}},
        },
    }
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(event), encoding="utf-8")
    return subprocess.run(  # noqa: S603
        [sys.executable, str(CHECKER_PATH), "--event-path", str(event_path)],
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


def test_dependabot_pull_requests_carry_patch_label() -> None:
    config = yaml.safe_load((ROOT / ".github" / "dependabot.yml").read_text())
    for update in config["updates"]:
        assert "changes/patch" in update["labels"], update["package-ecosystem"]


def test_sdk_update_pull_requests_carry_patch_label() -> None:
    workflow = (ROOT / ".github" / "workflows" / "update-infrahub-sdk.yml").read_text()
    assert '--label "changes/patch"' in workflow


def test_exemption_mirrors_release_pull_request_naming() -> None:
    workflow = (ROOT / ".github" / "workflows" / "trigger-push-stable.yml").read_text()
    assert 'BRANCH="release/${VERSION}"' in workflow
    assert '--title "chore(release): ${VERSION}"' in workflow

    version_check = re.search(r"grep -Eq '\^(?P<pattern>[^']+)\$'; then", workflow)
    assert version_check is not None
    checker = CHECKER_PATH.read_text()
    expected = "release/" + version_check.group("pattern").replace("(", "(?:")
    assert f'RELEASE_BRANCH_PATTERN = re.compile(r"{expected}")' in checker


def test_workflow_never_checks_out_pull_request_code() -> None:
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text())
    # PyYAML reads the bare `on` key as boolean True.
    assert set(workflow[True]) == {"pull_request_target"}
    steps = [step for job in workflow["jobs"].values() for step in job["steps"]]
    checkouts = [step for step in steps if str(step.get("uses", "")).startswith("actions/checkout@")]
    assert checkouts
    for checkout in checkouts:
        assert checkout["with"]["ref"] == "${{ github.event.pull_request.base.sha }}"
        assert checkout["with"]["persist-credentials"] is False
    assert "pull_request.head" not in WORKFLOW_PATH.read_text()


@pytest.mark.parametrize("label", BUMP_LABELS)
def test_single_bump_label_is_accepted(tmp_path: Path, label: str) -> None:
    result = run_checker(tmp_path, [label, "type/housekeeping"])
    assert result.returncode == 0, result.stdout
    assert label in result.stdout


@pytest.mark.parametrize(
    ("labels", "author_login"),
    [
        ([], "contributor"),
        (["type/bug"], "contributor"),
        (["changes/patch", "changes/minor"], "contributor"),
        ([], "dependabot[bot]"),
    ],
    ids=["no-labels", "type-only", "conflicting", "unlabelled-dependabot"],
)
def test_missing_or_conflicting_bump_label_is_rejected(tmp_path: Path, labels: list[str], author_login: str) -> None:
    result = run_checker(tmp_path, labels, author_login=author_login)
    assert result.returncode != 0
    assert "::error::" in result.stdout
    assert "exactly one" in result.stdout


@pytest.mark.parametrize(
    "title",
    ["chore(release): 1.2.3", "chore(release): 1.2.3 (edited)"],
    ids=["generated-title", "edited-title"],
)
def test_generated_release_pull_request_is_exempt(tmp_path: Path, title: str) -> None:
    result = run_checker(tmp_path, [], **{**RELEASE_PR, "title": title})
    assert result.returncode == 0, result.stdout
    assert "generated release pull request" in result.stdout


@pytest.mark.parametrize(
    "override",
    [
        {"author_login": "contributor", "head_repository": "contributor/example"},
        {"author_login": "opsmill-bot[bot]"},
        {"head_repository": "contributor/example"},
        {"title": "fix: example"},
        {"head_ref": "release/arbitrary"},
        {"head_ref": "release/v1.2.3"},
        {"title": "chore(release): 9.9.9"},
        {"title": "chore(release): 1.2.30"},
        {"title": "chore(release): 1.2.3-rc1"},
        {"title": "chore(release):1.2.3"},
    ],
    ids=[
        "untrusted-author",
        "app-bot-login",
        "forked-bot",
        "non-release-title",
        "non-version-ref",
        "v-prefixed-ref",
        "mismatched-title-version",
        "title-version-shares-prefix",
        "title-version-has-prerelease",
        "title-without-space",
    ],
)
def test_lookalike_release_pull_request_is_not_exempt(tmp_path: Path, override: dict[str, str]) -> None:
    result = run_checker(tmp_path, [], **{**RELEASE_PR, **override})
    assert result.returncode != 0
    assert "exactly one" in result.stdout


def test_missing_event_payload_fails(tmp_path: Path) -> None:
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(CHECKER_PATH), "--event-path", str(tmp_path / "missing.json")],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "Cannot read event payload" in result.stderr
