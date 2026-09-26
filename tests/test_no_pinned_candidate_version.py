"""No user guide names one private candidate by version or name.

Each new candidate would otherwise make every page naming the previous one wrong until
someone edited all of them. `develop/` and `release-notes/` are a historical and internal
record, not a user guide, and are excluded by name below. `container-image.mdx` documents
PEP 440 version-string parsing with literal example versions that are not a candidate
reference; only those documented examples are allowed there, so a real candidate pin
anywhere else on that page still fails.

A page may still legitimately name a specific commit sha, for example to pin a copy
procedure to a known-good revision of a file that ships before its own release; that is
a deliberate reproducibility pin, not the stale "Candidate 4" style reference this test
guards against, so this test does not flag commit shas.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS_ROOT = REPO_ROOT / "docs" / "docs"
EXCLUDED_DIRS = (DOCS_ROOT / "release-notes", DOCS_ROOT / "develop")
GUIDE_SUFFIXES = frozenset({".mdx", ".md"})

CANDIDATE_VERSION = re.compile(
    r"""\b
    v?\d+\.\d+\.\d+                # major.minor.patch, optional leading v
    [-.]?                          # optional separator before the pre-release segment
    (?:a|b|rc|alpha|beta|candidate)
    \d+                            # pre-release number
    \b""",
    re.IGNORECASE | re.VERBOSE,
)
CANDIDATE_NUMBER = re.compile(r"\bCandidate\s+\d+\b", re.IGNORECASE)

# Literal PEP 440 parsing examples in container-image.mdx, not a candidate reference.
CONTAINER_IMAGE_DOC = DOCS_ROOT / "container-image.mdx"
CONTAINER_IMAGE_ALLOWED_VERSIONS = ("3.0.0-a1", "3.0.0alpha1", "v3.0.0a1", "3.0.0a1")


def guide_pages() -> list[Path]:
    """Every user-facing docs page plus the bundled skills README, not excluded above."""
    pages = [
        path
        for path in DOCS_ROOT.rglob("*")
        if path.suffix in GUIDE_SUFFIXES and not any(excluded in path.parents for excluded in EXCLUDED_DIRS)
    ]
    pages.append(REPO_ROOT / "deploy" / "compose" / "skills" / "README.md")
    return pages


def _text_to_check(page: Path) -> str:
    """The page text, with container-image.mdx's documented parsing examples masked out."""
    text = page.read_text(encoding="utf-8")
    if page == CONTAINER_IMAGE_DOC:
        for allowed in CONTAINER_IMAGE_ALLOWED_VERSIONS:
            pattern = re.compile(r"\b" + re.escape(allowed) + r"\b")
            text = pattern.sub("x" * len(allowed), text)
    return text


@pytest.mark.parametrize("page", guide_pages(), ids=lambda page: str(page.relative_to(REPO_ROOT)))
def test_no_guide_page_names_a_pinned_candidate(page: Path) -> None:
    """A page naming one candidate's version or number goes stale at the next candidate."""
    text = _text_to_check(page)

    version_match = CANDIDATE_VERSION.search(text)
    assert version_match is None, f"{page.relative_to(REPO_ROOT)} names a candidate version: {version_match.group(0)!r}"

    number_match = CANDIDATE_NUMBER.search(text)
    assert number_match is None, f"{page.relative_to(REPO_ROOT)} names a candidate number: {number_match.group(0)!r}"
