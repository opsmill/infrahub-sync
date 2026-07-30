"""MIN-023 — nothing in the tree still points at the pre-archive spec path.

The completed feature's design artifacts moved to `dev/specs/archive/…` (OQ-1). Two doc
links and three module docstrings were left pointing at the old location: the links broke
(rumdl `MD057`) and the docstrings sent a reader looking for a contract to a path that does
not exist. Both are the same one-line mistake, and both come back the next time a spec is
archived — so the sweep is a test rather than a one-time grep.

`dev/specs/` is exempt: the archived `plan.md` records the path the spec was written at, which
is history rather than a pointer to follow. Everything a reader might actually navigate — code,
user docs, developer docs, tests — is in scope.
"""

from __future__ import annotations

import subprocess  # noqa: S404 — `git ls-files` is the file list, so untracked scratch is out of scope
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# The pre-archive prefix. Written in two pieces so this file does not match its own sweep.
STALE_PREFIX = "dev/specs/" + "001-plan-artifact-saved-apply"

# The relative form the docs use, which resolves to the same place and breaks the same way.
STALE_RELATIVE_PREFIX = "../specs/" + "001-plan-artifact-saved-apply"

# History, not pointers — see the module docstring.
EXEMPT_PREFIXES = ("dev/specs/",)


def _tracked_files() -> list[str]:
    """Every file git tracks, so untracked scratch files and `.venv` are out of scope."""
    completed = subprocess.run(  # noqa: S603 — a fixed argv, no shell
        ["git", "-C", str(REPO_ROOT), "ls-files"],  # noqa: S607 — resolved from PATH by design
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in completed.stdout.splitlines() if line]


def _hits(needle: str) -> dict[str, list[int]]:
    """Tracked, non-exempt files containing `needle`, with the line numbers."""
    found: dict[str, list[int]] = {}
    for name in _tracked_files():
        if name.startswith(EXEMPT_PREFIXES):
            continue
        path = REPO_ROOT / name
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        lines = [number for number, line in enumerate(text.splitlines(), start=1) if needle in line]
        if lines:
            found[name] = lines
    return found


def test_the_sweep_can_see_the_stale_prefix_at_all() -> None:
    """Precondition: the archive really does still carry the historical reference.

    Without this, both assertions below would pass against a sweep that reads nothing —
    a mistyped prefix, an exemption that swallowed the tree, or a `git ls-files` that
    returned an empty list.
    """
    tracked = _tracked_files()
    assert tracked, "the sweep found no tracked files"

    archived = [
        name
        for name in tracked
        if name.startswith(EXEMPT_PREFIXES) and STALE_PREFIX in (REPO_ROOT / name).read_text(encoding="utf-8")
    ]
    assert archived, "the exempt spec tree carries no historical reference, so the exemption proves nothing"


def test_no_file_outside_the_archive_points_at_the_pre_archive_spec_path() -> None:
    """The absolute form, in docstrings and prose."""
    hits = _hits(STALE_PREFIX)
    assert hits == {}, f"stale pre-archive spec references remain: {hits}"


def test_no_document_links_to_the_pre_archive_spec_path_relatively() -> None:
    """The relative form the two broken `MD057` links used."""
    hits = _hits(STALE_RELATIVE_PREFIX)
    assert hits == {}, f"stale relative links to the pre-archive spec path remain: {hits}"
