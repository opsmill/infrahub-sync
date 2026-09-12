"""The candidate workflow's own refusals, executed rather than read.

Reading that a comparison is written is not evidence that it refuses. A test
that searches a workflow for the text `git rev-parse HEAD` passes just as well
against a script that prints it, and the failure it was written to prevent —
a candidate built from source nobody merged — costs a whole hosted run and a
retained artifact to discover.

So these cases run the workflow's *own script text* against real repositories:
one where the checkout landed on a different commit than the run was told to
build, one where the commit named never merged, and one where both are right.
Passing the commit through an environment value instead of interpolating it into
the script is what makes that possible, so the thing under test here is the same
text the runner executes.
"""

from __future__ import annotations

import os
import shutil
import subprocess  # noqa: S404 -- these cases drive real `git` and the workflow's own shell
from pathlib import Path
from typing import NamedTuple

import pytest

from tests.test_workflow_contracts import V3_BRANCH, refusal_script

# Resolved once, because the runner's own `run` block finds both on its PATH and
# a partial path here is the same lookup done less explicitly.
GIT = shutil.which("git") or "git"
# The runner's default shell for a `run` block without an explicit `shell:`.
SHELL = shutil.which("bash") or "bash"
# A well-formed object name that names nothing in any repository here.
ABSENT = "0" * 40
# A branch on the remote pinned behind the V3 tip. Fetching it is what leaves
# `FETCH_HEAD` stale without removing anything from the local object store.
STALE_BRANCH = "behind"


class Line:
    """A remote holding the V3 branch, and a working copy cloned from it."""

    def __init__(self, work: Path, tip: str, earlier: str, unmerged: str) -> None:
        self.work = work
        self.tip = tip
        self.earlier = earlier
        self.unmerged = unmerged


class Refusal(NamedTuple):
    """What the workflow's script did when it was run."""

    code: int
    said: str


def git(cwd: Path, *arguments: str) -> str:
    """Run one `git` command in a fixture repository and return its output."""
    finished = subprocess.run(  # noqa: S603 -- fixed argv, no shell
        [GIT, *arguments],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        env=environment(),
    )
    return finished.stdout.strip()


def environment(**extra: str) -> dict[str, str]:
    """Return an environment where Git reads no developer configuration.

    A global `init.defaultBranch`, or a commit signature the machine happens to
    require, would otherwise decide what these repositories look like.
    """
    return {
        **os.environ,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_AUTHOR_NAME": "Candidate Fixture",
        "GIT_AUTHOR_EMAIL": "candidate@example.invalid",
        "GIT_COMMITTER_NAME": "Candidate Fixture",
        "GIT_COMMITTER_EMAIL": "candidate@example.invalid",
        **extra,
    }


def commit(repository: Path, name: str) -> str:
    """Add one file and return the commit that carries it."""
    (repository / name).write_text(f"{name}\n", encoding="utf-8")
    git(repository, "add", name)
    git(repository, "commit", "--quiet", "-m", f"add {name}")
    return git(repository, "rev-parse", "HEAD")


@pytest.fixture
def line(tmp_path: Path) -> Line:
    """Build a remote with two merged commits, and a clone holding a third that never merged.

    The remote also carries a branch pinned at the earlier commit. It exists so a
    later case can leave `FETCH_HEAD` pointing at something stale while the
    commit under test is present locally — which is the only arrangement where
    fetching, and not fetching, give different answers.
    """
    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "--quiet", f"--initial-branch={V3_BRANCH}")
    earlier = commit(origin, "base.md")
    git(origin, "branch", STALE_BRANCH, earlier)
    tip = commit(origin, "landed.md")

    work = tmp_path / "work"
    git(tmp_path, "clone", "--quiet", str(origin), str(work))
    unmerged = commit(work, "never-landed.md")
    return Line(work=work, tip=tip, earlier=earlier, unmerged=unmerged)


def refuse(line: Line, *, checkout: str, told: str) -> Refusal:
    """Check out one commit, tell the script to build another, and run it."""
    git(line.work, "checkout", "--quiet", checkout)
    finished = subprocess.run(  # noqa: S603 -- the workflow's own script, fixed argv
        [SHELL, "-c", refusal_script()],
        cwd=line.work,
        check=False,
        capture_output=True,
        text=True,
        env=environment(CANDIDATE_SHA=told),
    )
    return Refusal(code=finished.returncode, said=finished.stdout + finished.stderr)


def test_it_refuses_a_checkout_that_did_not_land_where_it_was_told(line: Line) -> None:
    """`actions/checkout` reports success for any ref it resolved, including a moved one.

    Both commits here are merged, so nothing but the comparison itself can catch
    this: the run was told to build the tip and is standing on its parent.
    """
    refusal = refuse(line, checkout=line.earlier, told=line.tip)

    assert refusal.code != 0, f"the script accepted a checkout on {line.earlier} while told {line.tip}"
    assert line.earlier in refusal.said, refusal.said
    assert "not the" in refusal.said, refusal.said


def test_it_refuses_a_commit_that_never_merged(line: Line) -> None:
    """The checkout is exactly where it was told to be, and the commit is still not a candidate.

    This is the case a read-back alone cannot see. The commit exists, it is
    checked out, its object name matches — and no branch anyone ships contains it.
    """
    refusal = refuse(line, checkout=line.unmerged, told=line.unmerged)

    assert refusal.code != 0, f"the script accepted {line.unmerged}, which is merged nowhere"
    assert "not merged" in refusal.said, refusal.said
    assert V3_BRANCH in refusal.said, refusal.said


def test_it_accepts_the_merged_commit_it_is_standing_on(line: Line) -> None:
    """The control. Without it the two refusals above are satisfied by a script that always fails."""
    accepted = refuse(line, checkout=line.tip, told=line.tip)

    assert accepted.code == 0, accepted.said
    assert line.tip in accepted.said, accepted.said


def test_it_refuses_an_abbreviated_commit(line: Line) -> None:
    """A short name resolves locally and cannot be compared to what a record will carry."""
    refusal = refuse(line, checkout=line.tip, told=line.tip[:12])

    assert refusal.code != 0, "the script accepted an abbreviated commit"
    assert "abbreviated" in refusal.said, refusal.said


@pytest.mark.parametrize("told", ["", "feature/v3-develop", "z" * 40], ids=["empty", "branch", "not-hexadecimal"])
def test_it_refuses_anything_that_is_not_an_object_name(line: Line, told: str) -> None:
    """A branch name is the input this workflow exists to stop being given."""
    refusal = refuse(line, checkout=line.tip, told=told)

    assert refusal.code != 0, f"the script accepted {told!r}"


def test_it_reads_the_branch_it_compares_against_from_the_remote(line: Line) -> None:
    """A stale local ref would admit a commit merged nowhere but into yesterday's copy.

    Arranged so that fetching and not fetching disagree, which the obvious setup
    does not do. The commit is made on the remote after the clone and then
    brought into the local object store, so it can be checked out — and
    `FETCH_HEAD` is then left pointing at a branch pinned *behind* it. A script
    that refreshes the remote branch sees the commit merged; one that trusts
    whatever `FETCH_HEAD` already held sees a commit merged nowhere and refuses
    a candidate that is real.

    Without the second fetch below, the first one leaves `FETCH_HEAD` already
    correct and this case passes against a script that never fetches at all.
    """
    origin = line.work.parent / "origin"
    git(origin, "checkout", "--quiet", V3_BRANCH)
    landed = commit(origin, "landed-later.md")
    git(line.work, "fetch", "--quiet", "origin", V3_BRANCH)
    git(line.work, "fetch", "--quiet", "origin", STALE_BRANCH)

    stale = git(line.work, "rev-parse", "FETCH_HEAD")
    assert stale == line.earlier, f"FETCH_HEAD is {stale}, so this proves nothing about fetching"

    accepted = refuse(line, checkout=landed, told=landed)

    assert accepted.code == 0, accepted.said
    assert landed in accepted.said, accepted.said


def test_it_refuses_a_commit_no_repository_holds(line: Line) -> None:
    """A well-formed name for nothing has to fail, rather than pass an ancestry check vacuously."""
    refusal = refuse(line, checkout=line.tip, told=ABSENT)

    assert refusal.code != 0, f"the script accepted {ABSENT}, which names no object"
