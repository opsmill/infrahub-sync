"""The release page states what a caller cannot guess about naming a release."""

from __future__ import annotations

from pathlib import Path

import pytest
from invoke import Collection

from tasks import release

REPO_ROOT = Path(__file__).resolve().parents[2]
PAGE = REPO_ROOT / "docs" / "docs" / "container-image.mdx"


@pytest.mark.parametrize("task_name", sorted(Collection.from_module(release).task_names))
def test_the_page_documents_every_release_task(task_name: str) -> None:
    """Read off the collection, so a task cannot ship without the page that names it."""
    assert f"invoke release.{task_name}" in PAGE.read_text(encoding="utf-8")


def test_the_page_names_the_record_a_later_phase_reads() -> None:
    """An approval with no checkout learns the identity from that file and nowhere else."""
    recorded = str(release.RECORD_FILE.relative_to(REPO_ROOT))

    assert recorded in PAGE.read_text(encoding="utf-8")
