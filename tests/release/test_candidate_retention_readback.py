"""The candidate run's granted-window check, executed rather than read.

The window is compared by arithmetic on two timestamps the service records
independently, so it is exactly the kind of check that reads as correct and is
off by one. Truncating the elapsed seconds rejects a nominal thirty-day window
that came back a second short — a candidate the service retained correctly,
failed by the step that was meant to confirm it.

These cases run the workflow's own script text against a stubbed inventory. The
step passes everything through the environment rather than interpolating it, so
the text under test here is the text the runner executes. `gh` and `date` are
stubbed because the point is the script's arithmetic, not the service's API or
the host's date implementation — the stub converts ISO instants exactly, so any
disagreement is the script's.
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

# The step is found by what it does, not by its name.
READBACK_MARKERS = ("actions/runs", "expires_at")

GROUPS = (
    "infrahub-sync-candidate-image",
    "infrahub-sync-candidate-identity",
    "infrahub-sync-candidate-distributions",
    "infrahub-sync-candidate-bundle",
    "infrahub-sync-candidate-sboms",
    "infrahub-sync-qualification-kit",
    "infrahub-sync-qualification-record",
)

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


def readback_script() -> str:
    """Return the one step of the candidate job that reads the granted window back."""
    document = yaml.safe_load(CANDIDATE_WORKFLOW.read_text(encoding="utf-8"))
    reading = [
        step
        for step in document["jobs"]["candidate"]["steps"]
        if all(marker in str(step.get("run", "")) for marker in READBACK_MARKERS)
    ]
    assert len(reading) == 1, f"{len(reading)} steps read the granted retention back"
    return str(reading[0]["run"])


def inventory(rows: dict[str, int], *, absent: tuple[str, ...] = ()) -> str:
    """Render a service inventory where each group's window is the given seconds."""
    lines = []
    for name in GROUPS:
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


def read_back(tmp_path: Path, binaries: Path, body: str) -> subprocess.CompletedProcess[str]:
    """Run the workflow's own read-back against one rendered inventory."""
    fixture = tmp_path / "inventory.tsv"
    fixture.write_text(body, encoding="utf-8")
    return subprocess.run(  # noqa: S603 -- the workflow's own script, fixed argv
        [SHELL, "-c", readback_script()],
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
        },
    )


def test_it_accepts_the_window_the_service_nominally_granted(tmp_path: Path, stubs: Path) -> None:
    """The control. Exactly thirty days, to the second."""
    finished = read_back(tmp_path, stubs, inventory({}))

    assert finished.returncode == 0, finished.stdout + finished.stderr


@pytest.mark.parametrize("drift", [-1, -60, -3600, 1, 60, 3600], ids=lambda drift: f"{drift:+d}s")
def test_it_accepts_a_nominal_window_the_service_recorded_slightly_off(tmp_path: Path, stubs: Path, drift: int) -> None:
    """Two timestamps recorded independently need not differ by a whole number of days.

    Truncating the elapsed seconds turns a window one second short into
    twenty-nine days and fails the run that built a correctly retained candidate.
    """
    finished = read_back(tmp_path, stubs, inventory({GROUPS[0]: WINDOW * DAY + drift}))

    assert finished.returncode == 0, finished.stdout + finished.stderr


@pytest.mark.parametrize("granted", [7, 29, 31, 90], ids=lambda granted: f"{granted}d")
def test_it_refuses_a_window_that_is_not_the_one_asked_for(tmp_path: Path, stubs: Path, granted: int) -> None:
    """Rounding to the nearest day is not rounding to the nearest acceptable answer."""
    finished = read_back(tmp_path, stubs, inventory({GROUPS[0]: granted * DAY}))

    assert finished.returncode != 0, f"a {granted}-day window was accepted"
    assert "not the 30" in finished.stdout + finished.stderr


def test_it_refuses_a_run_that_is_missing_a_group(tmp_path: Path, stubs: Path) -> None:
    """A window nothing was granted is not a window that passed."""
    finished = read_back(tmp_path, stubs, inventory({}, absent=(GROUPS[4],)))

    assert finished.returncode != 0, "a run holding six of seven groups was accepted"
    assert GROUPS[4] in finished.stdout + finished.stderr


def test_it_refuses_a_window_more_than_half_a_day_over(tmp_path: Path, stubs: Path) -> None:
    """Rounding to the nearest day is a tolerance for recording drift, not a free day.

    What it accepts is a window within twelve hours of the one asked for; ties
    round up, so exactly half a day over becomes thirty-one and is refused. That
    boundary is the whole tolerance, and widening it is what the mutation for
    this case does.
    """
    finished = read_back(tmp_path, stubs, inventory({GROUPS[0]: WINDOW * DAY + DAY // 2}))

    assert finished.returncode != 0, "a window half a day over was accepted"


@pytest.mark.parametrize("elapsed", [29 * DAY, 31 * DAY], ids=["a-day-short", "a-day-long"])
def test_a_widened_comparison_stops_being_about_the_window(tmp_path: Path, stubs: Path, elapsed: int) -> None:
    """The equality is the claim. A range around it accepts windows nobody asked for.

    Separate from the case above because the two failures are different: one
    loosens the arithmetic that produces the number, this one loosens the
    comparison that judges it.
    """
    finished = read_back(tmp_path, stubs, inventory({GROUPS[0]: elapsed}))

    assert finished.returncode != 0, f"a {elapsed // DAY}-day window was accepted"
    assert "not the 30" in finished.stdout + finished.stderr
