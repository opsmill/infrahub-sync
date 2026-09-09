"""The internal tutorial, against the workflow and the bundle it describes.

A procedure is wrong in the one way that matters when it names an artifact, a
window or a job that the thing it describes no longer has — and its reader is a
teammate on a clean host with no way to tell the difference. So every name here
is read off the candidate workflow and the bundle entry point rather than
written down twice.

It also has to stay internal. The artifacts are unpublished pre-release bytes,
so a page explaining how to download and run them belongs in `dev/`, not on the
Docusaurus site.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_workflow_contracts import (
    CANDIDATE_WINDOW_DAYS,
    CANDIDATE_WINDOW_NAME,
    CANDIDATE_WORKFLOW,
    CLEAN_HOST_JOB,
    candidates,
    jobs,
    load,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
GUIDE = REPO_ROOT / "dev" / "guides" / "qualifying-an-internal-candidate.md"
INDEX = REPO_ROOT / "dev" / "guides" / "README.md"
DOCS_SITE = REPO_ROOT / "docs" / "docs"
SIDEBAR = REPO_ROOT / "docs" / "sidebars.ts"
ENTRY_POINT = REPO_ROOT / "deploy" / "compose" / "infrahub-sync-compose"

# The lifecycle a reader is taken through. Every verb, because the tutorial's
# whole purpose is that its reader reaches a working deployment and then takes
# it down again.
LIFECYCLE = ("init", "preflight", "start", "status", "logs", "restart", "stop", "reset")

# What the reader has to be told they cannot do. An operator who believes the
# window can be extended will discover otherwise on the day it lapses.
EXPIRY = ("cannot be extended", "same exact commit")


def guide() -> str:
    return GUIDE.read_text(encoding="utf-8")


def test_the_guide_is_where_the_index_says_it_is() -> None:
    """A guide nobody can find is a guide nobody follows."""
    assert GUIDE.is_file(), GUIDE
    assert GUIDE.name in INDEX.read_text(encoding="utf-8"), f"{INDEX} does not link {GUIDE.name}"


def test_the_guide_is_not_on_the_public_documentation_site() -> None:
    """The artifacts are unpublished and unadvertised; a public page would advertise them."""
    assert not (DOCS_SITE / GUIDE.name).exists(), "the internal tutorial is on the docs site"
    assert not list(DOCS_SITE.rglob(GUIDE.name)), "the internal tutorial is under the docs site tree"
    assert GUIDE.stem not in SIDEBAR.read_text(encoding="utf-8"), "the internal tutorial is in the site sidebar"


def test_the_guide_names_every_artifact_group_the_run_retains() -> None:
    """Derived from the uploads, so a renamed or added group fails here.

    A reader downloading by name cannot discover that a group was renamed; they
    get an empty directory and a procedure that carries on regardless.
    """
    retained = {str(declared["name"]) for path, _job, _step, declared in candidates() if path == CANDIDATE_WORKFLOW}
    body = guide()

    assert retained, f"{CANDIDATE_WORKFLOW.name} retains nothing, so this proves nothing"
    missing = sorted(name for name in retained if name not in body)
    assert not missing, f"{GUIDE.name} never names {missing}"


def test_the_guide_states_the_window_the_workflow_really_asks_for() -> None:
    """A number written twice is a number that will disagree with itself."""
    declared = load(CANDIDATE_WORKFLOW)["env"][CANDIDATE_WINDOW_NAME]

    assert declared == CANDIDATE_WINDOW_DAYS
    assert f"{declared} days" in guide(), f"{GUIDE.name} does not state a {declared}-day window"


def test_the_guide_requires_both_jobs_of_the_run_to_have_succeeded() -> None:
    """Retained uploads are not a qualification: the downstream job is what qualified them.

    Both job names are read off the workflow, so splitting or renaming either one
    fails here rather than leaving a reader accepting a run that built bytes
    nothing ever ran.
    """
    defined = set(jobs(CANDIDATE_WORKFLOW))
    body = guide()

    assert CLEAN_HOST_JOB in defined
    missing = sorted(name for name in defined if f"`{name}`" not in body)
    assert not missing, f"{GUIDE.name} never names the {missing} job"


@pytest.mark.parametrize("verb", LIFECYCLE)
def test_the_guide_takes_its_reader_through_the_whole_lifecycle(verb: str) -> None:
    """Compared against the entry point, so a verb it no longer has is not documented here."""
    assert f"    {verb})" in ENTRY_POINT.read_text(encoding="utf-8"), f"the bundle has no {verb}"
    assert f"infrahub-sync-compose {verb}" in guide(), f"{GUIDE.name} never runs {verb}"


@pytest.mark.parametrize("stated", EXPIRY)
def test_the_guide_says_expiry_is_answered_by_rebuilding_the_same_commit(stated: str) -> None:
    """The window is not renewable, and the replacement is bound to the commit, not the bytes."""
    assert stated in guide(), f"{GUIDE.name} does not say {stated!r}"


def test_the_guide_sends_its_reader_to_a_destination_they_may_write_to() -> None:
    """The procedure applies a real write, so consent is part of the instruction."""
    body = guide()

    assert "disposable" in body, f"{GUIDE.name} does not say the destination must be disposable"
    assert "authorised to write to" in body, f"{GUIDE.name} does not require an authorised destination"
