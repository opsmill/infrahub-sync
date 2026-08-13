"""End-to-end CLI tests for `infrahub-sync sync --parallel`."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import typer
from typer.testing import CliRunner

from infrahub_sync.adapters.infrahub import ConvergenceIdentityError
from infrahub_sync.cli import app
from infrahub_sync.utils import get_instance

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


def test_parallel_load_refusal_redacts_resolved_configuration_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Tier-parallel load refusals cannot leak inline credentials through their error text."""
    sentinel = "db004-parallel-load-config-secret"
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path))
    run_dir = tmp_path / "from-netbox" / "test-run"
    run_dir.mkdir(parents=True, exist_ok=True)
    sync_instance = get_instance(name="from-netbox", directory=str(EXAMPLES_DIR))
    assert sync_instance is not None
    sync_instance = sync_instance.model_copy(deep=True)
    assert sync_instance.destination.settings is not None
    sync_instance.destination.settings["api_token"] = sentinel
    fake_ptd = _make_fake_potenda(tiers=[{"BuiltinTag"}], run_dir=run_dir)
    fake_ptd.sync_in_tiers.side_effect = ValueError(f"tier load rejected {sentinel}")
    runner = CliRunner()
    caplog.set_level(logging.INFO, logger="infrahub_sync.cli")

    with (
        patch("infrahub_sync.cli.get_instance", return_value=sync_instance),
        patch("infrahub_sync.cli.get_potenda_from_instance", return_value=fake_ptd),
    ):
        result = runner.invoke(
            app,
            ["sync", "--parallel", "--name", "from-netbox", "--directory", str(EXAMPLES_DIR)],
        )

    assert result.exit_code == 1
    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert sentinel not in messages
    assert "***" in messages


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


def test_serial_sync_presents_convergence_refusal_without_raw_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Serial sync must present the pre-write refusal through the normal CLI error path."""
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path))
    run_dir = tmp_path / "from-netbox" / "test-run"
    run_dir.mkdir(parents=True, exist_ok=True)
    fake_ptd = _make_fake_potenda(tiers=None, run_dir=run_dir)
    fake_ptd.diff.return_value.has_diffs.return_value = True
    refusal = "Refusing to sync destination kind LocationRack: uncovered mapping identifier(s): site."
    error = ConvergenceIdentityError(refusal)
    fake_ptd.sync.side_effect = error
    runner = CliRunner()

    with (
        patch("infrahub_sync.cli.get_potenda_from_instance", return_value=fake_ptd),
        patch("infrahub_sync.cli.print_error_and_abort", side_effect=typer.Abort) as abort,
    ):
        result = runner.invoke(
            app,
            ["sync", "--no-parallel", "--name", "from-netbox", "--directory", str(EXAMPLES_DIR)],
        )

    assert result.exit_code == 1
    abort.assert_called_once_with(str(error))


def test_serial_sync_does_not_present_unrelated_value_error_as_refusal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only convergence refusals use the serial sync presentation path."""
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path))
    run_dir = tmp_path / "from-netbox" / "test-run"
    run_dir.mkdir(parents=True, exist_ok=True)
    fake_ptd = _make_fake_potenda(tiers=None, run_dir=run_dir)
    unrelated_error = ValueError("unexpected diff failure")
    fake_ptd.diff.side_effect = unrelated_error
    runner = CliRunner()

    with (
        patch("infrahub_sync.cli.get_potenda_from_instance", return_value=fake_ptd),
        patch("infrahub_sync.cli.print_error_and_abort", side_effect=typer.Abort) as abort,
    ):
        result = runner.invoke(
            app,
            ["sync", "--no-parallel", "--name", "from-netbox", "--directory", str(EXAMPLES_DIR)],
        )

    assert result.exit_code == 1
    assert result.exception is unrelated_error
    abort.assert_not_called()


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


def test_parallel_ignored_for_explicit_order_does_not_flatten_a_later_serial_value_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only real tier-parallel ValueErrors take the CLI's one-line refusal path."""
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path))
    run_dir = tmp_path / "from-netbox" / "test-run"
    run_dir.mkdir(parents=True, exist_ok=True)
    fake_ptd = _make_fake_potenda(tiers=None, run_dir=run_dir)
    fake_ptd.diff.side_effect = ValueError("serial-path defect after --parallel was ignored")
    runner = CliRunner()

    with patch("infrahub_sync.cli.get_potenda_from_instance", return_value=fake_ptd):
        result = runner.invoke(
            app,
            ["sync", "--parallel", "--name", "from-netbox", "--directory", str(EXAMPLES_DIR)],
        )

    assert result.exit_code == 1
    assert isinstance(result.exception, ValueError)
    assert str(result.exception) == "serial-path defect after --parallel was ignored"
