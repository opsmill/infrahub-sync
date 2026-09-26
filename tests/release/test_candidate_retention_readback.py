"""The candidate run's granted-window check, executed rather than read.

The window is compared by arithmetic on two timestamps the service records
independently: `created_at`, which GitHub reports when an artifact's upload
finishes, and `expires_at`. The upload of a large or slow artifact can take
tens of seconds, so a window read back this way is short by roughly that
duration even though the service granted the full window from the moment the
upload began. Treating that as drift and refusing it fails a candidate the
service retained correctly.

The tolerance has to be wide enough to absorb an upload's duration without
going so wide it stops catching a real cap. Rounding to the nearest day looks
reasonable and accepts a window almost twelve hours short of the one an
approval is bound to, so what is allowed here is a fixed number of seconds
(`RETENTION_DRIFT_TOLERANCE_SECONDS`, documented next to it in the workflow)
generous enough for any upload this job produces, and nothing wider: a window
short by a day or more still fails.

These cases run the workflow's own script text against a stubbed inventory. The
step passes everything through the environment rather than interpolating it, so
the text under test here is the text the runner executes. `gh` and `date` are
stubbed because the point is the script's arithmetic, not the service's API or
the host's date implementation — the stub converts ISO instants exactly, so any
disagreement is the script's.

The workflow writes this read-back once per producing job -- `candidate`,
which retains seven groups, and `packet`, which retains its own one -- because
the packet's own group does not exist yet when the first script runs. Every
case below is parametrized over both scripts so each job's tolerance is
actually exercised here, not just asserted as text in
`tests/test_workflow_contracts.py`.
"""

from __future__ import annotations

import os
import shutil
import subprocess  # noqa: S404 -- these cases drive the workflow's own shell
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CANDIDATE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "workflow-candidate.yml"

SHELL = shutil.which("bash") or "bash"
WINDOW = 30
DAY = 86400
# Kept in sync with RETENTION_DRIFT_TOLERANCE_SECONDS in the workflow itself;
# these cases pin the workflow's value indirectly by asserting the boundary.
TOLERANCE = 3600

# The step is found by what it does, not by its name.
READBACK_MARKERS = ("actions/runs", "expires_at")

CANDIDATE_GROUPS: tuple[str, ...] = (
    "infrahub-sync-candidate-image",
    "infrahub-sync-candidate-identity",
    "infrahub-sync-candidate-distributions",
    "infrahub-sync-candidate-bundle",
    "infrahub-sync-candidate-sboms",
    "infrahub-sync-qualification-kit",
    "infrahub-sync-qualification-record",
)
PACKET_GROUPS: tuple[str, ...] = ("infrahub-sync-candidate-packet",)
GROUPS_BY_JOB: dict[str, tuple[str, ...]] = {"candidate": CANDIDATE_GROUPS, "packet": PACKET_GROUPS}
JOBS = tuple(GROUPS_BY_JOB)

# A `gh` that prints whatever the case put in a file, and a `date` that converts
# an ISO instant the way GNU coreutils does. Both are argv-shaped stubs rather
# than mocks: the script calls them exactly as it calls the real ones.
GH_STUB = """#!/bin/sh
cat "$RETENTION_FIXTURE"
"""

DATE_STUB = """#!{python}
import sys
from datetime import datetime

# The script calls `date -u -d <instant> +%s`.
arguments = sys.argv[1:]
instant = arguments[arguments.index("-d") + 1]
parsed = datetime.fromisoformat(instant.replace("Z", "+00:00"))
print(int(parsed.timestamp()))
"""


def readback_script(job: str) -> str:
    """Return the one step of the named job that reads the granted window back."""
    document = yaml.safe_load(CANDIDATE_WORKFLOW.read_text(encoding="utf-8"))
    reading = [
        step
        for step in document["jobs"][job]["steps"]
        if all(marker in str(step.get("run", "")) for marker in READBACK_MARKERS)
    ]
    assert len(reading) == 1, f"{len(reading)} steps of {job!r} read the granted retention back"
    return str(reading[0]["run"])


def inventory(groups: tuple[str, ...], rows: dict[str, int], *, absent: tuple[str, ...] = ()) -> str:
    """Render a service inventory where each group's window is the given seconds."""
    lines = []
    for name in groups:
        if name in absent:
            continue
        elapsed = rows.get(name, WINDOW * DAY)
        lines.append(f"{name}\t2026-09-09T03:18:51Z\t{_shifted(elapsed)}")
    return "".join(f"{line}\n" for line in lines)


def _shifted(elapsed: int) -> str:
    from datetime import datetime, timedelta, timezone

    created = datetime(2026, 9, 9, 3, 18, 51, tzinfo=timezone.utc)
    return (created + timedelta(seconds=elapsed)).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def stubs(tmp_path: Path) -> Path:
    """Put an argv-shaped `gh` and `date` first on PATH."""
    binaries = tmp_path / "bin"
    binaries.mkdir()
    (binaries / "gh").write_text(GH_STUB, encoding="utf-8")
    (binaries / "date").write_text(DATE_STUB.format(python=sys.executable), encoding="utf-8")
    for name in ("gh", "date"):
        (binaries / name).chmod(0o755)
    return binaries


def read_back(tmp_path: Path, binaries: Path, job: str, body: str) -> subprocess.CompletedProcess[str]:
    """Run the named job's own read-back against one rendered inventory."""
    fixture = tmp_path / "inventory.tsv"
    fixture.write_text(body, encoding="utf-8")
    return subprocess.run(  # noqa: S603 -- the workflow's own script, fixed argv
        [SHELL, "-c", readback_script(job)],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{binaries}{os.pathsep}{os.environ['PATH']}",
            "RETENTION_FIXTURE": str(fixture),
            "RUNNER_TEMP": str(tmp_path),
            "REPOSITORY": "opsmill/infrahub-sync",
            "RUN_ID": "1",
            "CANDIDATE_RETENTION_DAYS": str(WINDOW),
            "RETENTION_DRIFT_TOLERANCE_SECONDS": str(TOLERANCE),
        },
    )


@pytest.mark.parametrize("job", JOBS)
def test_it_accepts_the_window_the_service_nominally_granted(tmp_path: Path, stubs: Path, job: str) -> None:
    """The control. Exactly thirty days, to the second."""
    finished = read_back(tmp_path, stubs, job, inventory(GROUPS_BY_JOB[job], {}))

    assert finished.returncode == 0, finished.stdout + finished.stderr


@pytest.mark.parametrize("job", JOBS)
@pytest.mark.parametrize(
    "drift",
    [-TOLERANCE, -DAY // 48, -50, -1, 1, DAY // 48, TOLERANCE],
    ids=["-1h", "-30m", "-50s", "-1s", "+1s", "+30m", "+1h"],
)
def test_it_accepts_a_window_within_the_drift_tolerance(tmp_path: Path, stubs: Path, job: str, drift: int) -> None:
    """`created_at` trails the start of the upload it belongs to.

    A 50-second-short window is exactly what an 890 MB image upload produces,
    and the service still granted the full thirty days from the moment that
    upload began. The tolerance has to cover it -- and, since the timestamps
    are independent, cover the same drift in the other direction too.
    """
    groups = GROUPS_BY_JOB[job]
    finished = read_back(tmp_path, stubs, job, inventory(groups, {groups[0]: WINDOW * DAY + drift}))

    assert finished.returncode == 0, finished.stdout + finished.stderr


@pytest.mark.parametrize("job", JOBS)
@pytest.mark.parametrize(
    "shortfall",
    [TOLERANCE + 1, DAY // 2, DAY],
    ids=["just-over-an-hour", "half-a-day", "a-day"],
)
def test_it_refuses_a_window_short_of_the_one_asked_for(tmp_path: Path, stubs: Path, job: str, shortfall: int) -> None:
    """Beyond the tolerance, short is short. A day or more must still refuse.

    Rounding to the nearest day accepted almost twelve hours less than the
    window -- the defect this bound replaces: an approval bound to thirty days
    of retention would have been taken against bytes the service was keeping
    for twenty-nine and a half.
    """
    groups = GROUPS_BY_JOB[job]
    finished = read_back(tmp_path, stubs, job, inventory(groups, {groups[0]: WINDOW * DAY - shortfall}))

    assert finished.returncode != 0, f"a window {shortfall}s short was accepted"


@pytest.mark.parametrize("job", JOBS)
@pytest.mark.parametrize("granted", [7, 29, 31, 90], ids=lambda granted: f"{granted}d")
def test_it_refuses_a_window_that_is_not_the_one_asked_for(tmp_path: Path, stubs: Path, job: str, granted: int) -> None:
    """Rounding to the nearest day is not rounding to the nearest acceptable answer."""
    groups = GROUPS_BY_JOB[job]
    finished = read_back(tmp_path, stubs, job, inventory(groups, {groups[0]: granted * DAY}))

    assert finished.returncode != 0, f"a {granted}-day window was accepted"
    assert groups[0] in finished.stdout + finished.stderr


def test_it_refuses_a_run_that_is_missing_a_group(tmp_path: Path, stubs: Path) -> None:
    """A window nothing was granted is not a window that passed."""
    finished = read_back(tmp_path, stubs, "candidate", inventory(CANDIDATE_GROUPS, {}, absent=(CANDIDATE_GROUPS[4],)))

    assert finished.returncode != 0, "a run holding six of seven groups was accepted"
    assert CANDIDATE_GROUPS[4] in finished.stdout + finished.stderr


def test_it_refuses_a_run_missing_its_only_group(tmp_path: Path, stubs: Path) -> None:
    """The packet job retains exactly one group, so missing it is missing all of them."""
    finished = read_back(tmp_path, stubs, "packet", inventory(PACKET_GROUPS, {}, absent=(PACKET_GROUPS[0],)))

    assert finished.returncode != 0, "a run holding none of its groups was accepted"
    assert PACKET_GROUPS[0] in finished.stdout + finished.stderr


@pytest.mark.parametrize("job", JOBS)
@pytest.mark.parametrize("excess", [TOLERANCE + 1, DAY // 2, DAY], ids=["just-over-an-hour", "half-a-day", "a-day"])
def test_it_refuses_a_window_longer_than_the_one_asked_for(tmp_path: Path, stubs: Path, job: str, excess: int) -> None:
    """Longer is not safer. The record binds an approval to a stated window, not a minimum."""
    groups = GROUPS_BY_JOB[job]
    finished = read_back(tmp_path, stubs, job, inventory(groups, {groups[0]: WINDOW * DAY + excess}))

    assert finished.returncode != 0, f"a window {excess}s long was accepted"
