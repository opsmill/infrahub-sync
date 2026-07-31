"""Unit tests for the packaged Prefect flow, its log bridge, and serve-start validation.

Covers the flow's parameter contract (DBR-003), the run-scoped log bridge
including its level ownership (DBR-012 / E4), the asdict-shaped return value,
and the serve entrypoint's configuration-directory refusal (DBA-002). The final
two sections cover the canary-redaction scan (DBA-008, SC-005) and the
flow-level refusals and execution faults (DBA-006, DBA-010; SC-004, SC-008).

Tests that actually EXECUTE the flow run inside `prefect_test_harness()`, so
they never write to the developer's real `~/.prefect` and never contact a real
Prefect server. Pure contract assertions stay harness-free.
"""

from __future__ import annotations

import dataclasses
import functools
import inspect
import json
import logging
import os
import traceback
from contextlib import ExitStack
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import pytest

pytest.importorskip("prefect")

from prefect.client.orchestration import get_client
from prefect.testing.utilities import prefect_test_harness

from infrahub_sync.cache.parquet_io import write_plan
from infrahub_sync.execution import (
    REDACTED,
    RunExecutionError,
    RunResult,
    RunValidationError,
    run_remote_request,
)
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

    from prefect.client.schemas.objects import FlowRun
    from prefect.states import State

SYNC_NAME = "custom-example"
RUN_ID = "20260731T1200-abcdef12"
ARTIFACT_PATH = f"/tmp/cache/{SYNC_NAME}/{RUN_ID}"  # noqa: S108 - never touched on disk
CHILD_LOGGER_NAME = f"{SOURCE_LOGGER_NAME}.potenda"

# Canary fixture for the DBA-008 scan. Deliberately unmistakable strings, so an
# assertion can only pass through real redaction, never by coincidence — and one
# canary per collection rule the redaction contract names.
CANARY_SYNC_NAME = "canary-flow-example"
CANARY_RUN_ID = "20260731T1300-beefcafe"
ENV_TOKEN_CANARY = "ZZ-FLOW-ENV-INFRAHUB-TOKEN-0001"  # noqa: S105 - a canary, not a credential
ENV_PATTERN_CANARY = "ZZ-FLOW-ENV-NETBOX-TOKEN-0002"
SOURCE_TOKEN_CANARY = "ZZ-FLOW-SETTINGS-SOURCE-TOKEN-0003"  # noqa: S105 - a canary, not a credential
DEST_PASSWORD_CANARY = "ZZ-FLOW-SETTINGS-DEST-PASSWORD-0004"  # noqa: S105 - a canary, not a credential
CANARIES = (ENV_TOKEN_CANARY, ENV_PATTERN_CANARY, SOURCE_TOKEN_CANARY, DEST_PASSWORD_CANARY)


# --------------------------------------------------------------------------- #
# Fixtures and stubs
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="session")
def prefect_harness(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Run flows against a throwaway SQLite backend with PREFECT_HOME in tmp_path.

    All three parts matter: the harness isolates the database, PREFECT_HOME keeps
    profile/state files out of the developer's `~/.prefect`, and
    PREFECT_LOCAL_STORAGE_PATH redirects persisted run RESULTS — which do NOT
    follow PREFECT_HOME (measured: with PREFECT_HOME in tmp_path,
    `settings.results.local_storage_path` still resolved to
    `~/.prefect/storage`), so without it a flow-executing test would leave
    pickled result artifacts in the developer's home directory.
    """
    home = tmp_path_factory.mktemp("prefect-home")
    with ExitStack() as stack:
        patcher = stack.enter_context(pytest.MonkeyPatch.context())
        patcher.setenv("PREFECT_HOME", str(home))
        patcher.setenv("PREFECT_LOCAL_STORAGE_PATH", str(home / "storage"))
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


def test_bridge_swallows_a_bad_format_record_and_keeps_forwarding(
    source_logger: logging.Logger,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`logging.Handler.emit` must never propagate — `Handler.handle` does not shield it.

    A `%`-format mismatch in any `infrahub_sync.*` log call would otherwise raise at
    that call site. `propagate` is disabled because pytest's own capture handler
    deliberately re-raises bad records, which would mask what this test measures.
    """
    monkeypatch.setattr(source_logger, "propagate", False)
    run_logger = _StubRunLogger()
    source_logger.addHandler(RunLoggerBridge(run_logger))
    source_logger.setLevel(BRIDGED_LEVEL)
    child = logging.getLogger(CHILD_LOGGER_NAME)

    child.info("Sync: Completed in %s sec", 1.5, "one argument too many")
    child.info("Sync run %s at %s", RUN_ID, ARTIFACT_PATH)

    assert run_logger.rendered == [f"{CHILD_LOGGER_NAME} | Sync run {RUN_ID} at {ARTIFACT_PATH}"]
    # `handleError` reported it the way the CLI's StreamHandler already does.
    assert "--- Logging error ---" in capsys.readouterr().err


@pytest.mark.usefixtures("prefect_harness")
def test_a_bad_format_record_does_not_fail_the_run(
    monkeypatch: pytest.MonkeyPatch,
    source_logger: logging.Logger,
    tmp_path: Path,
) -> None:
    """The lifecycle logs AFTER writing to the destination: a bad record must not fail it."""
    monkeypatch.setattr(source_logger, "propagate", False)
    run_logger = _StubRunLogger()
    monkeypatch.setattr("infrahub_sync.orchestration.flow.get_run_logger", lambda: run_logger)
    monkeypatch.setenv(CONFIG_DIR_ENV, str(tmp_path))

    def _log_badly_then_return(*_args: object, **_kwargs: object) -> RunResult:

        logging.getLogger(CHILD_LOGGER_NAME).info(  # noqa: PLE1205
            "Sync: Completed in %s sec",
            1.5,
            "one argument too many",
        )
        return _result(operation="sync", status="applied")

    monkeypatch.setattr("infrahub_sync.orchestration.flow.run_remote_request", _log_badly_then_return)

    out = infrahub_sync_run(SYNC_NAME, "sync", confirm_writes=True)

    assert out["status"] == "applied"
    assert any("finished: status=applied" in line for line in run_logger.rendered)


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


# --------------------------------------------------------------------------- #
# The real surface behind the flow — injected engine, real redaction
# --------------------------------------------------------------------------- #
#
# The tests below drive `infrahub_sync_run` over the REAL composition
# (`run_remote_request` -> `resolve_sync_instance` -> `execute_run` -> the
# redaction path). Only two things are substituted: the engine, through
# `run_remote_request`'s sanctioned private `_potenda_factory` seam, and the
# configuration directory. Substituting a stub for `run_remote_request` itself
# would take the boundary under test out of the test.
#
# The flow never sets the seams (they are not part of the remote contract), so
# the seam is pre-bound onto the real function at the flow module's own
# `run_remote_request` reference.


def _canary_config() -> str:
    """A valid configuration whose adapter settings carry secret-keyed canaries."""
    return f"""
name: {CANARY_SYNC_NAME}
source:
  name: mockdb
  settings:
    url: http://localhost:9999
    token: {SOURCE_TOKEN_CANARY}
destination:
  name: infrahub
  settings:
    url: http://localhost:8000
    password: {DEST_PASSWORD_CANARY}
schema_mapping:
  - name: InfraDevice
    mapping: device
    identifiers: [name]
    fields:
      - name: name
        mapping: name
"""


@pytest.fixture
def canary_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> str:
    """Seed every secret-value source the redaction contract collects from.

    Both environment rules (the exact `INFRAHUB_API_TOKEN` name and the
    `*_TOKEN` name pattern) and both resolved-settings keys (`token` on the
    source, `password` on the destination). The cache root — and therefore the
    pipeline lock — is redirected into `tmp_path`.

    Returns the configuration directory, already exported for the flow.
    """
    monkeypatch.setenv("INFRAHUB_API_TOKEN", ENV_TOKEN_CANARY)
    monkeypatch.setenv("NETBOX_TOKEN", ENV_PATTERN_CANARY)
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path / "cache"))
    config_root = tmp_path / "configs"
    (config_root / "canary").mkdir(parents=True)
    (config_root / "canary" / "config.yml").write_text(_canary_config(), encoding="utf-8")
    monkeypatch.setenv(CONFIG_DIR_ENV, str(config_root))
    return str(config_root)


PLAN_ROW_DEFAULTS = {
    "dest_id": None,
    "attribute": None,
    "old_value": None,
    "new_value": None,
    "owner": None,
    "skip_reason": None,
    "conflict_class": None,
}


def _plan_row(source_id: str) -> dict[str, Any]:
    return {"action": "create", "resource": "InfraDevice", "source_id": source_id, **PLAN_ROW_DEFAULTS}


class _InjectedDiff:
    def __init__(self, rows: list[dict[str, Any]]) -> None:  # ty: ignore[invalid-type-form]
        self.rows = rows

    def has_diffs(self) -> bool:
        return bool(self.rows)

    def str(self) -> str:  # ty: ignore[invalid-type-form]
        return f"injected-diff({len(self.rows)} rows)"


class _InjectedEngine:
    """The engine surface `execute_run` touches — nothing more.

    Logs through the `infrahub_sync` hierarchy exactly as the real engine does,
    so the bridged-record scan has real records to scan.
    """

    def __init__(self, *, run_dir: Path, rows: list[dict[str, Any]], fault: BaseException | None) -> None:
        self.run_dir = run_dir
        self.run_id = run_dir.name
        self.top_level = ["InfraDevice"]
        self.force_full_extract = False
        self.rows = rows
        self.fault = fault

    def load_both_sides(self) -> None:
        """Log a lifecycle line, then raise the injected fault if there is one."""
        logging.getLogger(CHILD_LOGGER_NAME).info("Load: Importing data from MockDB")
        if self.fault is not None:
            raise self.fault

    def diff(self) -> _InjectedDiff:
        return _InjectedDiff(list(self.rows))

    def _diff_to_rows(self, diff: _InjectedDiff) -> list[dict[str, Any]]:  # noqa: PLR6301 - mirrors Potenda's API
        return list(diff.rows)

    def write_plan(self, diff: _InjectedDiff) -> None:
        write_plan(run_dir=self.run_dir, rows=self._diff_to_rows(diff))


class _InjectedFactory:
    """Records every factory call and builds an `_InjectedEngine`.

    `fault_factory` is a callable rather than a prebuilt exception so the fault's
    cause chain is constructed at raise time, the way a real one is.
    """

    def __init__(
        self,
        *,
        cache_root: Path,
        rows: list[dict[str, Any]] | None = None,
        fault_factory: Any = None,  # noqa: ANN401 - Callable[[], BaseException] | None
    ) -> None:
        self.calls: list[dict[str, object]] = []
        self.cache_root = cache_root
        self.rows = rows if rows is not None else []
        self.fault_factory = fault_factory

    def __call__(self, **kwargs: object) -> Any:  # noqa: ANN401 - a fake engine, not a real Potenda
        self.calls.append(kwargs)
        run_dir = self.cache_root / CANARY_RUN_ID
        run_dir.mkdir(parents=True, exist_ok=True)
        fault = self.fault_factory() if self.fault_factory is not None else None
        return _InjectedEngine(run_dir=run_dir, rows=list(self.rows), fault=fault)


def _bind_seam(monkeypatch: pytest.MonkeyPatch, factory: _InjectedFactory) -> None:
    """Pre-bind the `_potenda_factory` seam onto the flow's `run_remote_request`."""
    monkeypatch.setattr(
        "infrahub_sync.orchestration.flow.run_remote_request",
        functools.partial(run_remote_request, _potenda_factory=factory),
    )


def _canary_fault() -> BaseException:
    """A two-link fault chain with a canary in EVERY link (E5).

    Redacting only the wrapper message would leave the inner link's canary
    visible in a traceback rendering, which is exactly what this fault detects.
    """
    cause = RuntimeError(f"upstream auth rejected token {ENV_PATTERN_CANARY}")
    fault = ConnectionError(
        f"failed to reach https://netbox.internal - env token {ENV_TOKEN_CANARY}, "
        f"source setting {SOURCE_TOKEN_CANARY}, destination setting {DEST_PASSWORD_CANARY}"
    )
    fault.__cause__ = cause
    return fault


def _read_flow_run(state: State) -> FlowRun:
    """Read the persisted flow run — Prefect-visible parameters and state message."""
    flow_run_id = state.state_details.flow_run_id
    assert flow_run_id is not None
    with get_client(sync_client=True) as client:
        return client.read_flow_run(flow_run_id)


def _assert_canary_free(surfaces: dict[str, str]) -> None:
    """Assert no canary value appears in any named Prefect-visible surface."""
    for label, text in surfaces.items():
        for canary in CANARIES:
            assert canary not in text, f"canary {canary} leaked into {label}: {text}"


# --------------------------------------------------------------------------- #
# T022 — canary redaction scan (DBA-008, SC-005)
# --------------------------------------------------------------------------- #


@pytest.mark.usefixtures("prefect_harness", "canary_environment")
def test_no_canary_reaches_prefect_visible_state_on_a_successful_run(
    monkeypatch: pytest.MonkeyPatch,
    source_logger: logging.Logger,  # noqa: ARG001 - restores the bridged logger's handlers and level
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    """A run whose configuration and environment are full of secrets leaks none of them."""
    factory = _InjectedFactory(
        cache_root=tmp_path / "cache" / CANARY_SYNC_NAME,
        rows=[_plan_row(f"dev0{index}") for index in range(1, 6)],
    )
    run_logger = _StubRunLogger()
    monkeypatch.setattr("infrahub_sync.orchestration.flow.get_run_logger", lambda: run_logger)
    _bind_seam(monkeypatch, factory)
    caplog.set_level(logging.DEBUG, logger=SOURCE_LOGGER_NAME)

    state = infrahub_sync_run(CANARY_SYNC_NAME, "plan", return_state=True)  # ty: ignore[no-matching-overload] - TODO: ty cannot select prefect's ParamSpec return_state overload
    out = state.result()
    flow_run = _read_flow_run(state)

    # The run really did go through the whole surface: the seam was used once,
    # and the plan lifecycle produced a real five-create result.
    assert state.is_completed()
    assert len(factory.calls) == 1
    assert out["status"] == "planned"
    assert out["summary"] == {"create": 5, "update": 0, "delete": 0}
    assert any("Load: Importing data from MockDB" in line for line in run_logger.rendered)

    _assert_canary_free(
        {
            "flow parameters": repr(flow_run.parameters),
            "returned result dict": repr(out),
            "forwarded log records": "\n".join(run_logger.rendered),
            "emitted infrahub_sync log records": "\n".join(record.getMessage() for record in caplog.records),
            "flow-run state message": str(flow_run.state and flow_run.state.message),
        }
    )


@pytest.mark.usefixtures("prefect_harness", "canary_environment")
def test_no_canary_reaches_prefect_visible_state_on_a_failing_run(
    monkeypatch: pytest.MonkeyPatch,
    source_logger: logging.Logger,  # noqa: ARG001 - restores the bridged logger's handlers and level
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    """Every link of the fault's cause chain is redacted, traceback rendering included (E5)."""
    factory = _InjectedFactory(cache_root=tmp_path / "cache" / CANARY_SYNC_NAME, fault_factory=_canary_fault)
    run_logger = _StubRunLogger()
    monkeypatch.setattr("infrahub_sync.orchestration.flow.get_run_logger", lambda: run_logger)
    _bind_seam(monkeypatch, factory)
    caplog.set_level(logging.DEBUG, logger=SOURCE_LOGGER_NAME)

    state = infrahub_sync_run(CANARY_SYNC_NAME, "plan", return_state=True)  # ty: ignore[no-matching-overload] - TODO: ty cannot select prefect's ParamSpec return_state overload
    error = state.result(raise_on_failure=False)
    flow_run = _read_flow_run(state)

    assert state.is_failed()
    assert isinstance(error, RunExecutionError)
    # Redaction happened rather than the canaries never having been there: the
    # wrapper message names the fault and carries the redaction marker.
    assert REDACTED in str(error)
    assert "ConnectionError" in str(error)
    # No successful result exists for a failed run — the summary line is absent.
    assert not any("finished: status=" in line for line in run_logger.rendered)

    rendered_chain = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    assert "RuntimeError" in rendered_chain  # the inner link IS present, in sanitized form
    _assert_canary_free(
        {
            "flow parameters": repr(flow_run.parameters),
            "raised exception message": str(error),
            "traceback rendering of the whole cause chain": rendered_chain,
            "forwarded log records": "\n".join(run_logger.rendered),
            "emitted infrahub_sync log records": "\n".join(record.getMessage() for record in caplog.records),
            "flow-run state message": str(flow_run.state and flow_run.state.message),
        }
    )


# --------------------------------------------------------------------------- #
# T023 — flow-level refusals and execution faults (DBA-006, DBA-010)
# --------------------------------------------------------------------------- #


@pytest.mark.usefixtures("prefect_harness", "canary_environment")
def test_flow_refuses_an_unconfirmed_sync_before_any_engine_is_built(
    monkeypatch: pytest.MonkeyPatch,
    source_logger: logging.Logger,  # noqa: ARG001 - restores the bridged logger's handlers and level
    tmp_path: Path,
) -> None:
    """Prefect records FAILED, and the spy factory proves no adapter was constructed."""
    factory = _InjectedFactory(cache_root=tmp_path / "cache" / CANARY_SYNC_NAME, rows=[_plan_row("dev01")])
    _bind_seam(monkeypatch, factory)

    state = infrahub_sync_run(CANARY_SYNC_NAME, "sync", return_state=True)  # ty: ignore[no-matching-overload] - TODO: ty cannot select prefect's ParamSpec return_state overload
    error = state.result(raise_on_failure=False)

    assert state.is_failed()
    assert isinstance(error, RunValidationError)
    assert str(error) == "confirm_writes=true is required to run operation=sync"
    assert "confirm_writes=true is required to run operation=sync" in str(state.message)
    assert factory.calls == []


@pytest.mark.usefixtures("prefect_harness", "canary_environment")
def test_flow_refuses_an_unknown_sync_name(
    monkeypatch: pytest.MonkeyPatch,
    source_logger: logging.Logger,  # noqa: ARG001 - restores the bridged logger's handlers and level
    tmp_path: Path,
) -> None:
    """Resolution fails by logical name; no engine is built and the value is never a path."""
    factory = _InjectedFactory(cache_root=tmp_path / "cache" / CANARY_SYNC_NAME, rows=[_plan_row("dev01")])
    _bind_seam(monkeypatch, factory)

    with pytest.raises(RunValidationError) as raised:
        infrahub_sync_run("no-such-configuration", "plan")

    assert "'no-such-configuration'" in str(raised.value)
    assert factory.calls == []


@pytest.mark.usefixtures("prefect_harness", "canary_environment")
def test_flow_wraps_an_execution_fault_at_the_remote_boundary(
    monkeypatch: pytest.MonkeyPatch,
    source_logger: logging.Logger,  # noqa: ARG001 - restores the bridged logger's handlers and level
    tmp_path: Path,
) -> None:
    """An unreachable system arrives as `RunExecutionError`, not the original type (D009)."""
    factory = _InjectedFactory(
        cache_root=tmp_path / "cache" / CANARY_SYNC_NAME,
        fault_factory=lambda: ConnectionError("destination https://infrahub.internal is unreachable"),
    )
    run_logger = _StubRunLogger()
    monkeypatch.setattr("infrahub_sync.orchestration.flow.get_run_logger", lambda: run_logger)
    _bind_seam(monkeypatch, factory)

    with pytest.raises(RunExecutionError) as raised:
        infrahub_sync_run(CANARY_SYNC_NAME, "plan")

    message = str(raised.value)
    assert f"Sync '{CANARY_SYNC_NAME}' failed during operation=plan" in message
    assert "ConnectionError: destination https://infrahub.internal is unreachable" in message
    assert len(factory.calls) == 1
    # No successful result was produced: no summary line, and the run sidecar the
    # lifecycle created is left marked failed.
    assert not any("finished: status=" in line for line in run_logger.rendered)
    run_json = tmp_path / "cache" / CANARY_SYNC_NAME / CANARY_RUN_ID / "run.json"
    assert json.loads(run_json.read_text(encoding="utf-8"))["status"] == "failed"
