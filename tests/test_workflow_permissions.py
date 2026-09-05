"""A caller workflow grants every permission the workflows it calls request.

GitHub only lets a called workflow keep or reduce the caller's `GITHUB_TOKEN`
permissions, never raise them. Asking for more is not an ordinary job failure
with a log to read: the whole run ends in `startup_failure` with no jobs and
nothing to inspect, which is why it is worth catching in the repository.

Only explicit permission mappings are compared. A workflow that states no
`permissions`, or states one of the shorthand strings, is left to GitHub.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
CALLERS = ("trigger-pr-develop.yml", "trigger-pr-stable.yml", "trigger-push-stable.yml", "trigger-release.yml")

# GitHub's three access levels, ordered so "grants at least" is a comparison.
ACCESS = {"none": 0, "read": 1, "write": 2}


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def permissions(path: Path) -> dict[str, str]:
    """Return one workflow's explicit top-level permission mapping, if it states one."""
    declared = load(path).get("permissions")
    return declared if isinstance(declared, dict) else {}


def calls() -> list[tuple[Path, Path]]:
    """Return every (caller, called) pair of workflows inside this repository."""
    pairs = []
    for name in CALLERS:
        caller = WORKFLOWS / name
        if not caller.is_file():
            continue
        for job in load(caller).get("jobs", {}).values():
            uses = job.get("uses")
            if isinstance(uses, str) and uses.startswith("./"):
                pairs.append((caller, REPO_ROOT / uses.removeprefix("./")))
    return pairs


@pytest.mark.parametrize(("caller", "called"), calls(), ids=lambda path: path.name)
def test_a_caller_grants_every_permission_the_workflow_it_calls_requests(caller: Path, called: Path) -> None:
    granted = permissions(caller)
    if not granted:
        return

    for scope, level in permissions(called).items():
        assert ACCESS[level] <= ACCESS[granted.get(scope, "none")], (
            f"{called.name} requests {scope}: {level}, which {caller.name} does not grant"
        )
