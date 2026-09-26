"""No user guide names one private candidate by version, name, or commit.

Each new candidate would otherwise make every page naming the previous one wrong until
someone edited all of them. `develop/` and `release-notes/` are a historical and internal
record, not a user guide, and `container-image.mdx` documents version parsing with example
strings that are not a candidate reference; all three are excluded by name below.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS_ROOT = REPO_ROOT / "docs" / "docs"
EXCLUDED_DIRS = (DOCS_ROOT / "release-notes", DOCS_ROOT / "develop")
EXCLUDED_FILES = (DOCS_ROOT / "container-image.mdx",)

CANDIDATE_VERSION = re.compile(r"\b\d+\.\d+\.\d+a\d+\b")
CANDIDATE_NUMBER = re.compile(r"\bCandidate\s+\d+\b")


def guide_pages() -> list[Path]:
    """Every user-facing docs page plus the bundled skills README, not excluded above."""
    pages = [
        path
        for path in DOCS_ROOT.rglob("*")
        if path.suffix in (".mdx", ".md")
        and path not in EXCLUDED_FILES
        and not any(excluded in path.parents for excluded in EXCLUDED_DIRS)
    ]
    pages.append(REPO_ROOT / "deploy" / "compose" / "skills" / "README.md")
    return pages


@pytest.mark.parametrize("page", guide_pages(), ids=lambda page: str(page.relative_to(REPO_ROOT)))
def test_no_guide_page_names_a_pinned_candidate(page: Path) -> None:
    """A page naming one candidate's version or number goes stale at the next candidate."""
    text = page.read_text(encoding="utf-8")

    version_match = CANDIDATE_VERSION.search(text)
    assert version_match is None, f"{page.relative_to(REPO_ROOT)} names a candidate version: {version_match.group(0)!r}"

    number_match = CANDIDATE_NUMBER.search(text)
    assert number_match is None, f"{page.relative_to(REPO_ROOT)} names a candidate number: {number_match.group(0)!r}"
