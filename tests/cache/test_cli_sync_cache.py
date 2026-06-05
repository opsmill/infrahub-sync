"""End-to-end via Typer CliRunner: `sync` (serial + parallel) produces
run.json and plan.parquet under the cache."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from infrahub_sync.cli import app

EXAMPLES_DIR = Path(__file__).resolve().parent.parent.parent / "examples"


def _make_fake_potenda(tmp_path: Path, tiers) -> MagicMock:
    ptd = MagicMock()
    ptd.tiers = tiers
    ptd.run_id = "test-run"
    ptd.run_dir = tmp_path
    ptd.top_level = ["BuiltinTag"]
    ptd.diff.return_value = MagicMock(has_diffs=MagicMock(return_value=False), str=MagicMock(return_value=""))
    return ptd


def test_sync_serial_writes_run_json(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path))
    fake_ptd = _make_fake_potenda(tmp_path / "from-netbox" / "test-run", tiers=None)
    fake_ptd.run_dir.mkdir(parents=True, exist_ok=True)

    runner = CliRunner()
    with patch("infrahub_sync.cli.get_potenda_from_instance", return_value=fake_ptd):
        result = runner.invoke(
            app,
            ["sync", "--no-parallel", "--name", "from-netbox", "--directory", str(EXAMPLES_DIR)],
        )
    assert result.exit_code == 0, result.output
    run_json = fake_ptd.run_dir / "run.json"
    assert run_json.exists()
    data = json.loads(run_json.read_text())
    assert data["status"] == "applied"
    assert data["mode"] == "sync"
    fake_ptd.write_plan.assert_called_once()
    fake_ptd.persist_baseline_counts.assert_called_once()


def test_sync_parallel_delegates_with_allow_drop(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path))
    fake_ptd = _make_fake_potenda(tmp_path / "from-netbox" / "test-run", tiers=[{"BuiltinTag"}])
    fake_ptd.run_dir.mkdir(parents=True, exist_ok=True)

    runner = CliRunner()
    with patch("infrahub_sync.cli.get_potenda_from_instance", return_value=fake_ptd):
        result = runner.invoke(
            app,
            [
                "sync",
                "--parallel",
                "--allow-rowcount-drop",
                "--name",
                "from-netbox",
                "--directory",
                str(EXAMPLES_DIR),
            ],
        )
    assert result.exit_code == 0, result.output
    fake_ptd.sync_in_tiers.assert_called_once_with(parallel=True, allow_rowcount_drop=True)
    run_json = fake_ptd.run_dir / "run.json"
    assert run_json.exists()
    assert json.loads(run_json.read_text())["status"] == "applied"
