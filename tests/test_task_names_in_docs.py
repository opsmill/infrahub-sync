"""No workflow, page or agent guide runs a task name the Invoke rename removed.

`dev/specs/010-prefect-from-sync-image/contracts/invoke-tasks.md` lists the removed names.
The versioned 2.x documentation describes the earlier release and is not searched.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

SEARCHED = (
    *sorted((ROOT / ".github").rglob("*.yml")),
    *sorted((ROOT / ".github").rglob("*.py")),
    *sorted((ROOT / "docs" / "docs").rglob("*.md")),
    *sorted((ROOT / "docs" / "docs").rglob("*.mdx")),
    ROOT / "docs" / "sidebars.ts",
    ROOT / "AGENTS.md",
    ROOT / "README.md",
    ROOT / "development" / "README.md",
)

# Each pattern names a removed task, and leaves its replacement alone: `tests.tests-unit`
# keeps `tests-unit` after a dot, and `tests.check-310` keeps `check-310` after one.
REMOVED = {
    "invoke docusaurus": re.compile(r"invoke docusaurus\b"),
    "generate-doc": re.compile(r"(?<![\w.-])generate-doc\b"),
    "tests-unit, tests-integration or tests-all": re.compile(r"(?<![\w.-])tests-(?:unit|integration|all)\b"),
    "check-310": re.compile(r"(?<![\w./-])check-310\b"),
    "preview tasks and files": re.compile(
        r"\bpreview\.(?:up|down|seed|smoke|status|logs|env|local\.env)\b|docker-compose\.preview|tasks/preview|tests/preview"
    ),
    "netbox.up or netbox.down": re.compile(r"\bnetbox\.(?:up|down)\b"),
    "top-level build, start or destroy": re.compile(r"invoke (?:build|start|destroy)\b(?![.\w-])"),
}


def test_the_search_covers_files() -> None:
    """A search over no files would pass every case below."""
    assert len(SEARCHED) > 50
    assert all(path.exists() for path in SEARCHED)


@pytest.mark.parametrize("label", sorted(REMOVED))
def test_no_file_runs_a_removed_task(label: str) -> None:
    pattern = REMOVED[label]
    found = [
        f"{path.relative_to(ROOT)}:{number}"
        for path in SEARCHED
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if pattern.search(line)
    ]

    assert found == [], f"{label} still appears in {found}"
