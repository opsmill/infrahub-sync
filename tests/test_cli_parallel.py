"""End-to-end CLI tests for `infrahub-sync sync --parallel`."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from infrahub_sync.cli import app

if TYPE_CHECKING:
    import pytest

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"


def _make_fake_potenda(tiers: list | None, run_dir: Path) -> MagicMock:
    """Build a MagicMock Potenda with the attrs sync_cmd touches."""
    ptd = MagicMock()
    ptd.tiers = tiers
    ptd.run_id = "test-run"
    ptd.run_dir = run_dir
    ptd.top_level = ["BuiltinTag"]
    # diff() result must expose has_diffs() -> False so serial path exits cleanly.
    fake_diff = MagicMock()
    fake_diff.has_diffs.return_value = False
    fake_diff.str.return_value = ""
    ptd.diff.return_value = fake_diff
    ptd.sync_in_tiers.return_value = {"create": 0, "update": 0, "delete": 0}
    return ptd


def test_parallel_flag_invokes_sync_in_tiers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """--parallel with auto-tiers delegates to sync_in_tiers(parallel=True)."""
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path))
    run_dir = tmp_path / "from-netbox" / "test-run"
    run_dir.mkdir(parents=True, exist_ok=True)
    fake_ptd = _make_fake_potenda(tiers=[{"BuiltinTag"}, {"RoleGeneric"}], run_dir=run_dir)
    runner = CliRunner()
    with patch("infrahub_sync.cli.get_potenda_from_instance", return_value=fake_ptd):
        result = runner.invoke(
            app,
            ["sync", "--parallel", "--name", "from-netbox", "--directory", str(EXAMPLES_DIR)],
        )
    assert result.exit_code == 0, result.output
    fake_ptd.sync_in_tiers.assert_called_once_with(parallel=True, allow_rowcount_drop=False)
    # Eager source/destination load is skipped when delegating to sync_in_tiers.
    fake_ptd.source_load.assert_not_called()
    fake_ptd.destination_load.assert_not_called()


def test_parallel_is_the_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Locks the new default: invoking `sync` without flags still invokes sync_in_tiers."""
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path))
    run_dir = tmp_path / "from-netbox" / "test-run"
    run_dir.mkdir(parents=True, exist_ok=True)
    fake_ptd = _make_fake_potenda(tiers=[{"BuiltinTag"}, {"RoleGeneric"}], run_dir=run_dir)
    runner = CliRunner()
    with patch("infrahub_sync.cli.get_potenda_from_instance", return_value=fake_ptd):
        result = runner.invoke(
            app,
            ["sync", "--name", "from-netbox", "--directory", str(EXAMPLES_DIR)],
        )
    assert result.exit_code == 0, result.output
    fake_ptd.sync_in_tiers.assert_called_once_with(parallel=True, allow_rowcount_drop=False)


def test_no_parallel_runs_serial(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`--no-parallel` opts out of sync_in_tiers and runs the serial load+diff+sync."""
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path))
    run_dir = tmp_path / "from-netbox" / "test-run"
    run_dir.mkdir(parents=True, exist_ok=True)
    fake_ptd = _make_fake_potenda(tiers=[{"BuiltinTag"}], run_dir=run_dir)
    runner = CliRunner()
    with patch("infrahub_sync.cli.get_potenda_from_instance", return_value=fake_ptd):
        result = runner.invoke(
            app,
            ["sync", "--no-parallel", "--name", "from-netbox", "--directory", str(EXAMPLES_DIR)],
        )
    assert result.exit_code == 0, result.output
    fake_ptd.sync_in_tiers.assert_not_called()
    fake_ptd.load_both_sides.assert_called_once()
    fake_ptd.diff.assert_called_once()


def test_parallel_flag_warns_when_order_explicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """--parallel with explicit order: in config warns and falls back to serial."""
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path))
    run_dir = tmp_path / "from-netbox" / "test-run"
    run_dir.mkdir(parents=True, exist_ok=True)
    fake_ptd = _make_fake_potenda(tiers=None, run_dir=run_dir)  # operator set order: explicitly
    runner = CliRunner()
    caplog.set_level(logging.WARNING, logger="infrahub_sync.cli")
    with patch("infrahub_sync.cli.get_potenda_from_instance", return_value=fake_ptd):
        result = runner.invoke(
            app,
            ["sync", "--parallel", "--name", "from-netbox", "--directory", str(EXAMPLES_DIR)],
        )
    assert result.exit_code == 0, result.output
    fake_ptd.sync_in_tiers.assert_not_called()
    # Serial path ran: source/destination loaded and diff computed.
    fake_ptd.load_both_sides.assert_called_once()
    fake_ptd.diff.assert_called_once()
    msgs = [r.getMessage() for r in caplog.records]
    assert any("--parallel ignored" in m for m in msgs), msgs
