"""Unit tests for the packaged Prefect flow, its log bridge, and serve-start validation.

Covers the flow's parameter contract (DBR-003), the run-scoped log bridge
including its level ownership (DBR-012 / E4), the asdict-shaped return value,
and the serve entrypoint's configuration-directory refusal (DBA-002).

Tests that actually EXECUTE the flow run inside `prefect_test_harness()`, so
they never write to the developer's real `~/.prefect` and never contact a real
Prefect server. Pure contract assertions stay harness-free.
"""

from __future__ import annotations

import dataclasses
import inspect
import logging
import os
from contextlib import ExitStack
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import pytest

pytest.importorskip("prefect")

from prefect.testing.utilities import prefect_test_harness

from infrahub_sync.execution import RunExecutionError, RunResult
from infrahub_sync.orchestration import serve
from infrahub_sync.orchestration.flow import (
    BRIDGED_LEVEL,
    CONFIG_DIR_ENV,
    DEPLOYMENT_NAME,
    FLOW_NAME,
    SOURCE_LOGGER_NAME,
    RunLoggerBridge,
    infrahub_sync_run,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

SYNC_NAME = "custom-example"
RUN_ID = "20260731T1200-abcdef12"
ARTIFACT_PATH = f"/tmp/cache/{SYNC_NAME}/{RUN_ID}"  # noqa: S108 - never touched on disk
CHILD_LOGGER_NAME = f"{SOURCE_LOGGER_NAME}.potenda"


# --------------------------------------------------------------------------- #
# Fixtures and stubs
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="session")
def prefect_harness(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Run flows against a throwaway SQLite backend with PREFECT_HOME in tmp_path.

    Both halves matter: the harness isolates the database, and PREFECT_HOME
    keeps profile/state files out of the developer's `~/.prefect`.
    """
    home = tmp_path_factory.mktemp("prefect-home")
    with ExitStack() as stack:
        patcher = stack.enter_context(pytest.MonkeyPatch.context())
        patcher.setenv("PREFECT_HOME", str(home))
        stack.enter_context(prefect_test_harness())
        yield


@pytest.fixture
def source_logger() -> Iterator[logging.Logger]:
    """Restore the `infrahub_sync` logger's handlers and level around each test."""
    logger = logging.getLogger(SOURCE_LOGGER_NAME)
    handlers = list(logger.handlers)
    level = logger.level
    try:
        yield logger
    finally:
        logger.handlers = handlers
        logger.setLevel(level)


class _StubRunLogger:
    """Stands in for the Prefect run logger and records what it was told to log."""

    def __init__(self) -> None:
        self.calls: list[tuple[int, str, tuple[object, ...]]] = []

    def log(self, level: int, msg: str, *args: object) -> None:
        self.calls.append((level, msg, args))

    def info(self, msg: str, *args: object) -> None:
        self.log(logging.INFO, msg, *args)

    @property
    def rendered(self) -> list[str]:
        return [msg % args for _level, msg, args in self.calls]

    @property
    def origin_logger_names(self) -> list[object]:
        """First substitution of every bridged line — the origin logger name."""
        return [args[0] for _level, _msg, args in self.calls if args]


def _result(**overrides: Any) -> RunResult:  # noqa: ANN401 - heterogeneous RunResult field values
    payload: dict[str, Any] = {
        "sync_name": SYNC_NAME,
        "operation": "plan",
        "run_id": RUN_ID,
        "status": "planned",
        "changed": True,
        "summary": {"create": 5, "update": 0, "delete": 0},
        "artifact_path": ARTIFACT_PATH,
    }
    payload.update(overrides)
    return RunResult(**payload)


class _FakeRemoteRequest:
    """Records the surface call the flow makes, then returns or raises."""

    def __init__(self, *, result: RunResult | None = None, error: BaseException | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self.result = result
        self.error = error

    def __call__(
        self,
        sync_name: str,
        operation: str = "plan",
        confirm_writes: bool = False,  # noqa: FBT001, FBT002 - mirrors run_remote_request's real shape
        branch: str | None = None,
        *,
        config_directory: str,
    ) -> RunResult:
        self.calls.append(
            {
                "sync_name": sync_name,
                "operation": operation,
                "confirm_writes": confirm_writes,
                "branch": branch,
                "config_directory": config_directory,
            }
        )
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


# --------------------------------------------------------------------------- #
# Parameter contract (harness-free)
# --------------------------------------------------------------------------- #


def test_flow_exposes_exactly_the_four_contract_parameters() -> None:
    """No fifth parameter: nothing accepts a path, CLI fragment, or credential."""
    parameters = inspect.signature(infrahub_sync_run.fn).parameters
    assert list(parameters) == ["sync_name", "operation", "confirm_writes", "branch"]


@pytest.mark.parametrize(
    ("name", "default"),
    [
        ("sync_name", inspect.Parameter.empty),
        ("operation", "plan"),
        ("confirm_writes", False),
        ("branch", None),
    ],
)
def test_flow_parameter_default(name: str, default: object) -> None:
    assert inspect.signature(infrahub_sync_run.fn).parameters[name].default == default


def test_flow_operation_annotation_resolves_to_the_plan_sync_literal() -> None:
    """Non-deferred annotations (research F3): the Literal is a real object, not a string."""
    annotation = inspect.signature(infrahub_sync_run.fn).parameters["operation"].annotation
    assert annotation == Literal["plan", "sync"]


def test_flow_and_deployment_names_are_the_contracted_lookup_path() -> None:
    """`GET /api/deployments/name/infrahub-sync/run` — renaming breaks every caller."""
    assert (FLOW_NAME, DEPLOYMENT_NAME) == ("infrahub-sync", "run")
    assert infrahub_sync_run.name == FLOW_NAME


# --------------------------------------------------------------------------- #
# Log bridge (harness-free)
# --------------------------------------------------------------------------- #


def test_bridge_forwards_a_child_logger_record_preserving_level_and_name(
    source_logger: logging.Logger,
) -> None:
    run_logger = _StubRunLogger()
    source_logger.addHandler(RunLoggerBridge(run_logger))
    source_logger.setLevel(BRIDGED_LEVEL)

    logging.getLogger(CHILD_LOGGER_NAME).warning("loaded %d devices", 5)

    assert len(run_logger.calls) == 1
    level, _msg, args = run_logger.calls[0]
    assert level == logging.WARNING
    assert args[0] == CHILD_LOGGER_NAME
    assert run_logger.rendered == [f"{CHILD_LOGGER_NAME} | loaded 5 devices"]


def test_bridge_forwards_every_record_at_the_effective_level(source_logger: logging.Logger) -> None:
    """SC-002's denominator: records emitted at the effective level == records forwarded."""
    run_logger = _StubRunLogger()
    source_logger.addHandler(RunLoggerBridge(run_logger))
    source_logger.setLevel(BRIDGED_LEVEL)

    emitted_at_effective_level = [
        (SOURCE_LOGGER_NAME, logging.INFO),
        (f"{SOURCE_LOGGER_NAME}.potenda", logging.INFO),
        (f"{SOURCE_LOGGER_NAME}.cache.sidecars", logging.WARNING),
        (f"{SOURCE_LOGGER_NAME}.adapters.infrahub", logging.ERROR),
    ]
    for name, level in emitted_at_effective_level:
        logging.getLogger(name).log(level, "record from %s", name)
    # Below the effective level — deliberately excluded from the denominator.
    logging.getLogger(f"{SOURCE_LOGGER_NAME}.potenda").debug("not forwarded")

    assert len(run_logger.calls) == len(emitted_at_effective_level)
    assert run_logger.origin_logger_names == [name for name, _level in emitted_at_effective_level]
    assert [level for level, _msg, _args in run_logger.calls] == [level for _name, level in emitted_at_effective_level]


def test_bridge_ignores_records_from_outside_the_infrahub_sync_hierarchy(
    source_logger: logging.Logger,
) -> None:
    run_logger = _StubRunLogger()
    source_logger.addHandler(RunLoggerBridge(run_logger))
    source_logger.setLevel(BRIDGED_LEVEL)

    logging.getLogger("some_other_package").error("not ours")

    assert run_logger.calls == []


# --------------------------------------------------------------------------- #
# Flow execution (isolated Prefect state)
# --------------------------------------------------------------------------- #


@pytest.mark.usefixtures("prefect_harness")
def test_flow_run_returns_the_asdict_shaped_seven_key_dict(
    monkeypatch: pytest.MonkeyPatch,
    source_logger: logging.Logger,
    tmp_path: Path,
) -> None:
    """`summary` is a plain dict — a mappingproxy would make `asdict` fail at return time."""
    fake = _FakeRemoteRequest(result=_result())
    monkeypatch.setattr("infrahub_sync.orchestration.flow.run_remote_request", fake)
    monkeypatch.setenv(CONFIG_DIR_ENV, str(tmp_path))

    out = infrahub_sync_run(SYNC_NAME, "plan")

    assert set(out) == {field.name for field in dataclasses.fields(RunResult)}
    assert len(out) == 7
    assert type(out["summary"]) is dict
    assert out["summary"] == {"create": 5, "update": 0, "delete": 0}
    assert out["status"] == "planned"
    assert fake.calls == [
        {
            "sync_name": SYNC_NAME,
            "operation": "plan",
            "confirm_writes": False,
            "branch": None,
            "config_directory": str(tmp_path),
        }
    ]
    # The bridge is gone and the captured level is restored once the body returns.
    assert not any(isinstance(handler, RunLoggerBridge) for handler in source_logger.handlers)


@pytest.mark.usefixtures("prefect_harness")
def test_flow_makes_info_effective_regardless_of_ambient_logging_configuration(
    monkeypatch: pytest.MonkeyPatch,
    source_logger: logging.Logger,
    tmp_path: Path,
) -> None:
    """E4: the flow owns the source logger's LEVEL, not just the handler.

    With the root logger at WARNING and `infrahub_sync` left NOTSET, a handler
    alone would never see an INFO record — `isEnabledFor` would reject it first.
    """
    root = logging.getLogger()
    previous_root_level = root.level
    root.setLevel(logging.WARNING)
    source_logger.setLevel(logging.NOTSET)

    run_logger = _StubRunLogger()
    monkeypatch.setattr("infrahub_sync.orchestration.flow.get_run_logger", lambda: run_logger)
    monkeypatch.setenv(CONFIG_DIR_ENV, str(tmp_path))

    def _log_then_return(*_args: object, **_kwargs: object) -> RunResult:
        logging.getLogger(CHILD_LOGGER_NAME).info("bridged INFO line")
        return _result()

    monkeypatch.setattr("infrahub_sync.orchestration.flow.run_remote_request", _log_then_return)

    try:
        infrahub_sync_run(SYNC_NAME, "plan")
    finally:
        root.setLevel(previous_root_level)

    assert f"{CHILD_LOGGER_NAME} | bridged INFO line" in run_logger.rendered
    # The contractual summary line is the supported remote observation surface.
    assert any(
        line.startswith(f"run {RUN_ID} finished: status=planned changed=True summary=create:5,update:0,delete:0")
        for line in run_logger.rendered
    )
    assert source_logger.level == logging.NOTSET


@pytest.mark.usefixtures("prefect_harness")
def test_flow_restores_logging_state_and_reports_a_missing_config_directory(
    monkeypatch: pytest.MonkeyPatch,
    source_logger: logging.Logger,
) -> None:
    """The failure path restores the bridge and the level in the same `finally`."""
    monkeypatch.delenv(CONFIG_DIR_ENV, raising=False)
    source_logger.setLevel(logging.CRITICAL)

    with pytest.raises(RunExecutionError, match=CONFIG_DIR_ENV):
        infrahub_sync_run(SYNC_NAME, "plan")

    assert not any(isinstance(handler, RunLoggerBridge) for handler in source_logger.handlers)
    assert source_logger.level == logging.CRITICAL


# --------------------------------------------------------------------------- #
# Serve-start validation (harness-free)
# --------------------------------------------------------------------------- #


def test_serve_refuses_when_the_config_directory_is_unset(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv(CONFIG_DIR_ENV, raising=False)

    assert serve.resolve_config_directory() is None
    assert serve.main() == 1
    assert capsys.readouterr().err.count(CONFIG_DIR_ENV) == 2


def test_serve_refuses_when_the_config_directory_is_empty(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv(CONFIG_DIR_ENV, "")

    assert serve.resolve_config_directory() is None
    assert CONFIG_DIR_ENV in capsys.readouterr().err


def test_serve_refuses_when_the_config_directory_is_a_file(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    not_a_directory = tmp_path / "config.yml"
    not_a_directory.write_text("name: x\n", encoding="utf-8")
    monkeypatch.setenv(CONFIG_DIR_ENV, str(not_a_directory))

    assert serve.resolve_config_directory() is None
    assert serve.main() == 1
    err = capsys.readouterr().err
    assert CONFIG_DIR_ENV in err
    assert "is not an existing directory" in err


def test_serve_accepts_an_existing_directory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Positive control: validation passes without serving anything."""
    monkeypatch.setenv(CONFIG_DIR_ENV, str(tmp_path))

    assert serve.resolve_config_directory() == str(tmp_path)


def test_serve_reports_the_missing_extra_without_a_traceback() -> None:
    """The ImportError guard names the extra and the install command in one line."""
    assert "prefect is not installed" in serve.MISSING_EXTRA_MESSAGE
    assert ".[prefect]" in serve.MISSING_EXTRA_MESSAGE
    assert os.linesep not in serve.MISSING_EXTRA_MESSAGE
