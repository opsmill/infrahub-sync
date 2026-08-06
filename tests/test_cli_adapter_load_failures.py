"""An adapter that cannot be loaded is reported, not raised, by every command that loads one.

`import_adapter` answers `None` for an adapter it cannot import — a missing optional dependency
(`pynetbox`, `pynautobot`), a custom adapter path that no longer resolves — and both assembly
seams turn that into `ImportError`. Every one of `diff`, `sync` and `apply` used to let it escape
as a raw traceback while an adapter *initialization* failure one line away was reported as a
single line, so the three cases below are one finding rather than three.

No adapter is ever really constructed here: the replacement returns `None` before anything is
built, which is the condition under test.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from typer.testing import CliRunner

from infrahub_sync.cli import app
from tests.plan.artifact_fixtures import operation_record, write_artifact

if TYPE_CHECKING:
    from collections.abc import Callable

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLES_DIR = REPO_ROOT / "examples"
SYNC_NAME = "from-netbox"
RUN_ID = "20260729T1200-a1b2c3d4"

runner = CliRunner()


@pytest.fixture(autouse=True)
def _unloadable_adapters(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Every adapter import answers `None`, and the cache root stays inside `tmp_path`."""
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr("infrahub_sync.utils.import_adapter", lambda **_kwargs: None)


def _appliable_run(tmp_path: Path) -> Path:
    """A stored run holding a plan, so `apply` reaches the assembly seam at all."""
    directory = tmp_path / SYNC_NAME / RUN_ID
    directory.mkdir(parents=True, exist_ok=True)
    write_artifact(directory, [operation_record()], run_id=RUN_ID, source_snapshot=[])
    return directory


def _invoke(*args: str) -> Any:  # noqa: ANN401 — click's Result type is not exported for annotation
    return runner.invoke(app, [*args, "--name", SYNC_NAME, "--directory", str(EXAMPLES_DIR)])


COMMANDS: dict[str, Callable[[], Any]] = {
    "diff": lambda: _invoke("diff"),
    "sync": lambda: _invoke("sync"),
    "apply": lambda: _invoke("apply", "--run-id", RUN_ID),
}


@pytest.mark.parametrize("command", list(COMMANDS), ids=list(COMMANDS))
def test_an_unloadable_adapter_is_reported_as_one_line(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, command: str
) -> None:
    """The refusal names what could not be loaded and carries no stack trace."""
    _appliable_run(tmp_path)

    with caplog.at_level(logging.ERROR, logger="infrahub_sync.cli"):
        result = COMMANDS[command]()

    assert result.exit_code != 0
    assert result.exception is None or isinstance(result.exception, SystemExit), (
        f"the refusal escaped as a raw {type(result.exception).__name__} traceback"
    )
    message = " ".join(record.getMessage() for record in caplog.records if record.levelno >= logging.ERROR)
    assert "Failed to initialize" in message, message
    assert "adapter" in message, message
