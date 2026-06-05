from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from infrahub_sync.cli import app

if TYPE_CHECKING:
    import pytest

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"
runner = CliRunner()


def test_full_extract_flag_accepted() -> None:
    result = runner.invoke(app, ["sync", "--help"])
    assert result.exit_code == 0
    assert "--full-extract" in result.output


def test_full_extract_flag_diff() -> None:
    result = runner.invoke(app, ["diff", "--help"])
    assert result.exit_code == 0
    assert "--full-extract" in result.output


def _make_fake_potenda(run_dir: Path) -> MagicMock:
    ptd = MagicMock()
    ptd.tiers = None
    ptd.run_id = "test-run"
    ptd.run_dir = run_dir
    ptd.top_level = ["BuiltinTag"]
    fake_diff = MagicMock()
    fake_diff.has_diffs.return_value = False
    fake_diff.str.return_value = ""
    ptd.diff.return_value = fake_diff
    return ptd


def test_full_extract_is_the_default_on_sync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Locks the new default: bare `sync` sets `ptd.force_full_extract = True`."""
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path))
    run_dir = tmp_path / "from-netbox" / "test-run"
    run_dir.mkdir(parents=True, exist_ok=True)
    fake_ptd = _make_fake_potenda(run_dir)
    with patch("infrahub_sync.cli.get_potenda_from_instance", return_value=fake_ptd):
        result = runner.invoke(
            app,
            ["sync", "--no-parallel", "--name", "from-netbox", "--directory", str(EXAMPLES_DIR)],
        )
    assert result.exit_code == 0, result.output
    assert fake_ptd.force_full_extract is True


def test_no_full_extract_engages_incremental(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`--no-full-extract` sets `ptd.force_full_extract = False` so the cursor path is enabled."""
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path))
    run_dir = tmp_path / "from-netbox" / "test-run"
    run_dir.mkdir(parents=True, exist_ok=True)
    fake_ptd = _make_fake_potenda(run_dir)
    with patch("infrahub_sync.cli.get_potenda_from_instance", return_value=fake_ptd):
        result = runner.invoke(
            app,
            [
                "sync",
                "--no-parallel",
                "--no-full-extract",
                "--name",
                "from-netbox",
                "--directory",
                str(EXAMPLES_DIR),
            ],
        )
    assert result.exit_code == 0, result.output
    assert fake_ptd.force_full_extract is False
