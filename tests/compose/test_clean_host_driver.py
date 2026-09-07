"""What the clean-host driver must be, read from the driver itself.

The driver runs on a host this suite cannot reach, so what is checkable here is
its shape: that it makes a claim about every mandatory row, that it cannot report
a row it did not run, and that it carries the guards the gate rests on. None of
that is evidence the driver runs — only that it cannot quietly run less than it
says.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DRIVER = REPO_ROOT / "tests" / "compose" / "clean_host" / "clean-host.sh"
CHECKS = REPO_ROOT / "tests" / "compose" / "clean_host" / "checks"

# Every row the accepted matrix requires. Written out rather than read from the
# driver, so a row deleted from the driver fails here instead of narrowing the
# list it is checked against.
MANDATORY_ROWS = (
    "artifact_identity",
    "cold_start_and_idempotence",
    "managed_execution",
    "keyed_write_policy",
    "schema_change",
    "status",
    "restart",
    "recovery",
    "ownership_and_reset",
    "alpha_replacement",
    "secrets",
)

# The host tools the gate refuses to reach, and the guards it cannot run without.
REFUSED_HOST_TOOLS = ("python", "python3", "uv", "uvx", "pip", "pytest", "infrahubctl")


def driver() -> str:
    return DRIVER.read_text(encoding="utf-8")


def test_the_driver_is_executable() -> None:
    """A bare host runs this file directly; a mode lost in transit stops the gate."""
    assert DRIVER.stat().st_mode & 0o111


def declared_matrix() -> tuple[str, ...]:
    """Return the rows the driver's own matrix names."""
    declared = re.search(r"^MATRIX='([^']*)'", driver(), re.MULTILINE)
    assert declared is not None, "the driver declares no matrix"
    return tuple(declared.group(1).split())


@pytest.mark.parametrize("row", MANDATORY_ROWS)
def test_the_driver_defines_and_runs_every_mandatory_row(row: str) -> None:
    """A row absent from the matrix is a claim the gate silently stops making."""
    assert f"row_{row}()" in driver(), f"the driver defines no row_{row}"
    assert row in declared_matrix(), f"the driver's matrix omits {row}"


def test_the_matrix_holds_nothing_but_the_mandatory_rows() -> None:
    """Read from the driver, so a row added without a decision fails here."""
    assert declared_matrix() == MANDATORY_ROWS


def executable_lines() -> str:
    """Return the driver with its comments removed, so prose cannot satisfy a check."""
    return "\n".join(line for line in driver().splitlines() if not line.lstrip().startswith("#"))


def test_the_driver_expresses_no_way_to_skip_a_row() -> None:
    """A skip is a failed gate, so unreachability is not something the driver can say.

    Every row either returns or ends the run. There is no continue, no early exit
    reporting success, and no marker a row could set to be passed over.
    """
    body = executable_lines()

    assert "exit 0" not in body
    assert not re.search(r"^\s*continue\b", body, re.MULTILINE)
    assert "skip" not in body.lower()


@pytest.mark.parametrize("tool", REFUSED_HOST_TOOLS)
def test_the_driver_refuses_every_host_tool_by_recording_its_use(tool: str) -> None:
    """Absence is a claim about the host; an invocation marker is a claim about the run."""
    body = driver()

    assert re.search(rf"HOST_TOOLS=.*\b{re.escape(tool)}\b", body, re.DOTALL)
    assert "host-tool-used" in body
    assert "require_no_host_tool_was_used" in body


def test_the_driver_verifies_the_bundle_before_it_extracts_or_edits_it() -> None:
    """The checksum is a claim about the archive, never about the tree afterwards."""
    body = driver()
    verified = body.index("sha256sum -c")
    extracted = body.index("tar -xzf")
    edited = body.index("point_configuration_at_destination")

    assert verified < extracted < edited


def test_every_check_the_driver_runs_is_in_the_kit() -> None:
    """A named check that does not exist fails the row at the host, not here."""
    named = set(re.findall(r"^\s*check ([a-z_]+)", driver(), re.MULTILINE))
    named |= set(re.findall(r"\$\(check ([a-z_]+)", driver()))

    assert named, "the driver runs no checks"
    for name in sorted(named):
        assert (CHECKS / f"{name}.py").is_file(), f"the driver runs {name}, which the kit does not carry"
