"""The candidate run's granted-window check, executed rather than read.

The window is compared by arithmetic on two timestamps the service records
independently, so it is exactly the kind of check that reads as correct and is
off by one. Truncating the elapsed seconds rejects a nominal thirty-day window
that came back a second short — a candidate the service retained correctly,
failed by the step that was meant to confirm it.

The tolerance has to be narrow. Rounding to the nearest day looks reasonable and
accepts a window almost twelve hours short of the one an approval is bound to,
so what is allowed here is the second of resolution the API's own timestamps
carry, and nothing wider.

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


@pytest.mark.parametrize("drift", [-1, 1], ids=lambda drift: f"{drift:+d}s")
def test_it_accepts_one_second_of_timestamp_drift(tmp_path: Path, stubs: Path, drift: int) -> None:
    """The API reports both instants at second resolution and records them independently.

    A pair that differs by a second does not mean the window differed, so the
    check tolerates exactly that and truncating the elapsed seconds -- which
    turns one second short into twenty-nine days -- does not.
    """
    finished = read_back(tmp_path, stubs, inventory({GROUPS[0]: WINDOW * DAY + drift}))

    assert finished.returncode == 0, finished.stdout + finished.stderr


@pytest.mark.parametrize("drift", [-2, 2], ids=lambda drift: f"{drift:+d}s")
def test_it_refuses_more_than_one_second_of_drift(tmp_path: Path, stubs: Path, drift: int) -> None:
    """One second is the resolution of the timestamps; two is a different window.

    The bound has to be narrow or it stops being about the window at all. This is
    the pair that pins it: a second is tolerated and two are not.
    """
    finished = read_back(tmp_path, stubs, inventory({GROUPS[0]: WINDOW * DAY + drift}))

    assert finished.returncode != 0, f"a window {drift:+d}s from the one asked for was accepted"
    assert GROUPS[0] in finished.stdout + finished.stderr


@pytest.mark.parametrize(
    "shortfall",
    [DAY // 2 - 1, DAY // 2, DAY, 3600],
    ids=["just-under-half-a-day", "half-a-day", "a-day", "an-hour"],
)
def test_it_refuses_a_window_short_of_the_one_asked_for(tmp_path: Path, stubs: Path, shortfall: int) -> None:
    """Rounding to the nearest day accepted almost twelve hours less than the window.

    That is the defect this bound replaces: an approval bound to thirty days of
    retention would have been taken against bytes the service was keeping for
    twenty-nine and a half.
    """
    finished = read_back(tmp_path, stubs, inventory({GROUPS[0]: WINDOW * DAY - shortfall}))

    assert finished.returncode != 0, f"a window {shortfall}s short was accepted"


@pytest.mark.parametrize("granted", [7, 29, 31, 90], ids=lambda granted: f"{granted}d")
def test_it_refuses_a_window_that_is_not_the_one_asked_for(tmp_path: Path, stubs: Path, granted: int) -> None:
    """Rounding to the nearest day is not rounding to the nearest acceptable answer."""
    finished = read_back(tmp_path, stubs, inventory({GROUPS[0]: granted * DAY}))

    assert finished.returncode != 0, f"a {granted}-day window was accepted"
    assert GROUPS[0] in finished.stdout + finished.stderr


def test_it_refuses_a_run_that_is_missing_a_group(tmp_path: Path, stubs: Path) -> None:
    """A window nothing was granted is not a window that passed."""
    finished = read_back(tmp_path, stubs, inventory({}, absent=(GROUPS[4],)))

    assert finished.returncode != 0, "a run holding six of seven groups was accepted"
    assert GROUPS[4] in finished.stdout + finished.stderr


@pytest.mark.parametrize("excess", [3600, DAY // 2, DAY], ids=["an-hour", "half-a-day", "a-day"])
def test_it_refuses_a_window_longer_than_the_one_asked_for(tmp_path: Path, stubs: Path, excess: int) -> None:
    """Longer is not safer. The record binds an approval to a stated window, not a minimum."""
    finished = read_back(tmp_path, stubs, inventory({GROUPS[0]: WINDOW * DAY + excess}))

    assert finished.returncode != 0, f"a window {excess}s long was accepted"
