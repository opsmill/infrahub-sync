"""Per-stage CLI failure mapping across the shared execution surface (D009).

`diff_cmd` and the serial branch of `sync_cmd` delegate to
`infrahub_sync.execution.execute_run`, and DBR-009 requires the observable CLI
behavior to stay what it was at commit `9edc1bc`: three distinct failure shapes
(prefixed abort, unprefixed abort, uncaught original-type traceback) plus
identical `run.json` contents and log lines. One test per stage pins each.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest
from filelock import Timeout
from typer.testing import CliRunner

from infrahub_sync.cache.guardrails import RowcountGuardrailError
from infrahub_sync.cache.locks import pipeline_lock
from infrahub_sync.cli import app

if TYPE_CHECKING:
    from collections.abc import Iterator

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"
SYNC_NAME = "from-netbox"
FACTORY = "infrahub_sync.cli.get_potenda_from_instance"

runner = CliRunner()


@pytest.fixture
def run_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A cache root under `tmp_path` plus the run directory the fake engine reports."""
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path))
    target = tmp_path / SYNC_NAME / "test-run"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _fake_potenda(run_dir: Path, *, tiers: list | None = None) -> MagicMock:
    """A MagicMock engine exposing exactly the attributes the lifecycles touch."""
    ptd = MagicMock()
    ptd.tiers = tiers
    ptd.run_id = run_dir.name
    ptd.run_dir = run_dir
    ptd.top_level = ["BuiltinTag"]
    fake_diff = MagicMock()
    fake_diff.has_diffs.return_value = False
    fake_diff.str.return_value = ""
    ptd.diff.return_value = fake_diff
    return ptd


def _invoke(command: str, *extra: str) -> Any:  # noqa: ANN401 - click's Result type is private-ish
    return runner.invoke(app, [command, *extra, "--name", SYNC_NAME, "--directory", str(EXAMPLES_DIR)])


def _run_json(run_dir: Path) -> dict[str, Any]:
    return json.loads((run_dir / "run.json").read_text(encoding="utf-8"))


def _messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [record.getMessage() for record in caplog.records]


@pytest.fixture
def cli_logs(caplog: pytest.LogCaptureFixture) -> Iterator[pytest.LogCaptureFixture]:
    """Capture `infrahub_sync.cli` records without leaking the level to later tests."""
    with caplog.at_level(logging.INFO, logger="infrahub_sync.cli"):
        yield caplog


# --------------------------------------------------------------------------- #
# diff — T025
# --------------------------------------------------------------------------- #


def test_diff_success_writes_todays_run_json_and_log_line(run_dir: Path, cli_logs: pytest.LogCaptureFixture) -> None:
    """The happy path is unchanged: run.json `dry-run` plus the `Cached run` line."""
    fake_ptd = _fake_potenda(run_dir)
    with patch(FACTORY, return_value=fake_ptd):
        result = _invoke("diff")

    assert result.exit_code == 0, result.output
    data = _run_json(run_dir)
    assert data["status"] == "dry-run"
    assert data["mode"] == "diff"
    assert data["summary"] == {"resources": 1}
    assert data["finished_at"] is not None
    assert f"Cached run test-run at {run_dir}" in _messages(cli_logs)
    fake_ptd.write_plan.assert_called_once()
    assert fake_ptd.force_full_extract is True


def test_diff_factory_value_error_aborts_with_the_prefixed_message(
    run_dir: Path, cli_logs: pytest.LogCaptureFixture
) -> None:
    """Factory `ValueError` → today's prefixed abort: exit code 1, `Aborted`, no run.json."""
    with patch(FACTORY, side_effect=ValueError("adapter settings are invalid")):
        result = _invoke("diff")

    assert result.exit_code == 1
    assert "Aborted" in result.output
    assert "Failed to initialize the Sync Instance: adapter settings are invalid" in _messages(cli_logs)
    assert not (run_dir / "run.json").exists()


def test_diff_lifecycle_value_error_reraises_the_original_type(
    run_dir: Path, cli_logs: pytest.LogCaptureFixture
) -> None:
    """A load-phase `ValueError` on the diff path stays an uncaught `ValueError`."""
    fake_ptd = _fake_potenda(run_dir)
    fake_ptd.load_both_sides.side_effect = ValueError("source load failed")
    with patch(FACTORY, return_value=fake_ptd):
        result = _invoke("diff")

    assert result.exit_code == 1
    assert isinstance(result.exception, ValueError)
    assert str(result.exception) == "source load failed"
    assert _run_json(run_dir)["status"] == "failed"
    # The diff path has no narrow load handler — nothing is logged and no abort fires.
    assert not any("Failed to initialize the Sync Instance" in msg for msg in _messages(cli_logs))


def test_diff_factory_import_error_is_reported_as_one_line(run_dir: Path, cli_logs: pytest.LogCaptureFixture) -> None:
    """A factory `ImportError` is reported as a one-line CLI refusal."""
    with patch(FACTORY, side_effect=ImportError("Could not load the following adapter(s): source adapter 'nope'")):
        result = _invoke("diff")

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert (
        "Failed to initialize the Sync Instance: Could not load the following adapter(s): source adapter 'nope'"
        in _messages(cli_logs)
    )
    assert not (run_dir / "run.json").exists()


def test_diff_lock_contention_surfaces_filelock_timeout(run_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A held pipeline lock surfaces as an uncaught `filelock.Timeout`, engine unbuilt."""

    def short_lock(sync_name: str, *, timeout: float = 60.0) -> Any:  # noqa: ANN401, ARG001 - drop-in for the real one
        return pipeline_lock(sync_name, timeout=0.05)

    # Shortened wherever the lock is taken, so this test is a valid oracle for both
    # the pre-refactor command body and the surface that now owns the acquisition.
    monkeypatch.setattr("infrahub_sync.execution.pipeline_lock", short_lock)
    monkeypatch.setattr("infrahub_sync.cli.pipeline_lock", short_lock)
    factory = MagicMock(return_value=_fake_potenda(run_dir))
    with pipeline_lock(SYNC_NAME), patch(FACTORY, factory):
        result = _invoke("diff")

    assert result.exit_code == 1
    assert isinstance(result.exception, Timeout)
    factory.assert_not_called()
    assert not (run_dir / "run.json").exists()


# --------------------------------------------------------------------------- #
# sync (serial branch) — T026
# --------------------------------------------------------------------------- #


def test_sync_factory_value_error_aborts_with_the_prefixed_message(
    run_dir: Path, cli_logs: pytest.LogCaptureFixture
) -> None:
    """Factory `ValueError` → the same prefixed abort as today, before any branch."""
    with patch(FACTORY, side_effect=ValueError("adapter settings are invalid")):
        result = _invoke("sync", "--no-parallel")

    assert result.exit_code == 1
    assert "Aborted" in result.output
    assert "Failed to initialize the Sync Instance: adapter settings are invalid" in _messages(cli_logs)
    assert not (run_dir / "run.json").exists()


def test_sync_serial_load_value_error_aborts_without_a_prefix(
    run_dir: Path, cli_logs: pytest.LogCaptureFixture
) -> None:
    """Serial-load `ValueError` → the UNPREFIXED abort, with run.json marked failed."""
    fake_ptd = _fake_potenda(run_dir)
    fake_ptd.load_both_sides.side_effect = ValueError("destination load failed")
    with patch(FACTORY, return_value=fake_ptd):
        result = _invoke("sync", "--no-parallel")

    assert result.exit_code == 1
    assert "Aborted" in result.output
    messages = _messages(cli_logs)
    assert "destination load failed" in messages
    assert not any("Failed to initialize the Sync Instance" in msg for msg in messages)
    assert _run_json(run_dir)["status"] == "failed"


def test_sync_serial_guardrail_failure_reraises_the_original_type(
    run_dir: Path, cli_logs: pytest.LogCaptureFixture
) -> None:
    """A post-load guardrail failure stays an uncaught `RowcountGuardrailError`."""
    fake_ptd = _fake_potenda(run_dir)
    fake_ptd.check_rowcount_guardrail.side_effect = RowcountGuardrailError("rowcount dropped by 90%")
    with patch(FACTORY, return_value=fake_ptd):
        result = _invoke("sync", "--no-parallel")

    assert result.exit_code == 1
    assert isinstance(result.exception, RowcountGuardrailError)
    assert str(result.exception) == "rowcount dropped by 90%"
    assert _run_json(run_dir)["status"] == "failed"
    fake_ptd.sync.assert_not_called()
    assert not any("Failed to initialize the Sync Instance" in msg for msg in _messages(cli_logs))


def test_sync_serial_builds_the_engine_exactly_once(run_dir: Path, cli_logs: pytest.LogCaptureFixture) -> None:
    """The serial branch must not re-enter the factory — a second engine would
    allocate a second run_dir/run_id and re-emit the tier log lines."""
    factory = MagicMock(return_value=_fake_potenda(run_dir))
    with patch(FACTORY, factory):
        result = _invoke("sync", "--no-parallel")

    assert result.exit_code == 0, result.output
    factory.assert_called_once()
    data = _run_json(run_dir)
    assert data["status"] == "applied"
    assert data["mode"] == "sync"
    assert data["summary"] == {"resources": 1, "mode": "serial"}
    # Exactly one closing line, emitted by the surface rather than the command body.
    assert _messages(cli_logs).count(f"Sync run test-run at {run_dir}") == 1
