"""Validate the explicit release bump label selected for a pull request.

Reads only the `pull_request_target` event payload: the workflow never checks
out or runs code from the pull request head.
"""

# ruff: noqa: INP001

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

BUMP_LABELS = frozenset({"changes/major", "changes/minor", "changes/patch"})
RELEASE_PR_PREFIX = "chore(release):"
# The PAT user trigger-push-stable.yml opens the release pull request as.
RELEASE_PR_AUTHOR = "opsmill-bot"
# Mirrors the version check in trigger-push-stable.yml, which names the branch
# `release/${VERSION}` with a bare (not `v`-prefixed) version.
RELEASE_BRANCH_PATTERN = re.compile(r"release/[0-9]+\.[0-9]+\.[0-9]+(?:[.-][0-9A-Za-z.-]+)?")
# A character that would extend the version, so the title names a different one.
VERSION_CONTINUATION = re.compile(r"[0-9A-Za-z.-]")


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--event-path",
        type=Path,
        default=os.environ.get("GITHUB_EVENT_PATH"),
        help="Path to the GitHub event payload (defaults to $GITHUB_EVENT_PATH).",
    )
    return parser


def title_names_version(title: str, version: str) -> bool:
    """Return whether the title is `chore(release): <version>`, optionally with a suffix.

    trigger-push-stable.yml sets the title to exactly `chore(release): ${VERSION}`.
    A maintainer may append text after the version (for example ` (edited)`), but
    the version must end there: `1.2.30` or `1.2.3-rc1` does not name `1.2.3`.
    """
    expected = f"{RELEASE_PR_PREFIX} {version}"
    if not title.startswith(expected):
        return False
    suffix = title[len(expected) :]
    return not suffix or VERSION_CONTINUATION.match(suffix) is None


def is_generated_release_pr(pull_request: dict[str, Any], repository: str) -> bool:
    """Return whether the pull request is the one trigger-push-stable.yml opens.

    The title must name the same version as the `release/<version>` head ref.
    """
    head = pull_request.get("head") or {}
    ref = str(head.get("ref", ""))
    return (
        RELEASE_BRANCH_PATTERN.fullmatch(ref) is not None
        and title_names_version(str(pull_request.get("title", "")), ref.removeprefix("release/"))
        and (pull_request.get("user") or {}).get("login") == RELEASE_PR_AUTHOR
        and (head.get("repo") or {}).get("full_name") == repository
    )


def main() -> int:
    """Validate that a normal pull request has exactly one release label."""
    args = build_parser().parse_args()
    if args.event_path is None:
        sys.stderr.write("No event payload: pass --event-path or set GITHUB_EVENT_PATH.\n")
        return 1

    try:
        event = json.loads(args.event_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"Cannot read event payload {args.event_path}: {exc}\n")
        return 1

    pull_request = event.get("pull_request")
    repository = (event.get("repository") or {}).get("full_name")
    if not isinstance(pull_request, dict) or not repository:
        sys.stderr.write("Event payload has no pull_request or repository.\n")
        return 1

    if is_generated_release_pr(pull_request, repository):
        sys.stdout.write("Skipping label check for generated release pull request.\n")
        return 0

    labels = {label.get("name") for label in pull_request.get("labels", []) if isinstance(label, dict)}
    selected = sorted(BUMP_LABELS.intersection(labels))
    if len(selected) != 1:
        choices = ", ".join(sorted(BUMP_LABELS))
        found = ", ".join(selected) if selected else "none"
        sys.stdout.write(
            f"::error::Pull requests must have exactly one release bump label ({choices}); found: {found}.\n"
        )
        return 1

    sys.stdout.write(f"Release bump label: {selected[0]}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
