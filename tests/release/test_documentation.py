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


def test_the_page_names_the_bill_of_materials_where_the_task_writes_it() -> None:
    """An operator following a stale path finds nothing, and the rename already happened.

    Derived from the task rather than repeated here: the file is named for the
    release it describes, so what the page has to carry is that shape either side
    of the version.
    """
    from tasks import image

    identity = release.release_identity(version="3.0.0a1", revision="0" * 40, created="2026-09-04T10:15:00+02:00")
    written = image.sbom_file(identity, "linux/amd64").name
    before, _, after = written.partition(identity.version)
    page = PAGE.read_text(encoding="utf-8")

    assert before in page, f"the page does not name the {before!r} the task writes"
    assert after in page, f"the page does not name the {after!r} the task writes"
