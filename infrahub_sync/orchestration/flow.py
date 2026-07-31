"""Packaged Prefect flow that runs one Infrahub Sync plan or confirmed sync.

Requires the optional `prefect` extra — from the repository checkout,
`pip install -e '.[prefect]'` (or `uv pip install -e '.[prefect]'`). A
programmatic importer without the extra gets `ImportError` on the `prefect`
import below; `infrahub_sync.orchestration.serve` turns that into one actionable
line instead of a traceback.

The flow calls the shared execution surface IN-PROCESS; it never spawns the CLI.
"""

# NO `from __future__ import annotations` in this module, deliberately, against
# the repository's convention: with deferred (stringified) annotations, prefect's
# run-time parameter validation (`Flow.validate_parameters` ->
# `ValidatedFunction.model_rebuild`) fails with
# `PydanticUndefinedAnnotation: name 'Literal' is not defined` and the flow run
# ends FAILED before the body runs. Observed on prefect 3.5.0; the mechanism is
# version-generic, so the omission is kept at 3.8.1, where the parameter-contract
# tests in tests/orchestration/test_flow.py are what confirm it.

import dataclasses
import logging
import os
from typing import Literal, Protocol

from prefect import flow
from prefect.logging import get_run_logger

from infrahub_sync.execution import RunExecutionError, run_remote_request

logger = logging.getLogger(__name__)

FLOW_NAME = "infrahub-sync"
# Deployment named "run", NOT a repeat of the flow name: the lookup path reads
# /api/deployments/name/infrahub-sync/run instead of the stuttering
# .../infrahub-sync/infrahub-sync, and future orchestration work gets sibling
# deployments as .../infrahub-sync/<verb> for free. Renaming after the preview
# would break every remote caller's lookup.
DEPLOYMENT_NAME = "run"
CONFIG_DIR_ENV = "INFRAHUB_SYNC_CONFIG_DIRECTORY"

# The logger hierarchy the bridge forwards, and the level the flow makes
# effective for the duration of one run.
SOURCE_LOGGER_NAME = "infrahub_sync"
BRIDGED_LEVEL = logging.INFO

# Contractual format of the one summary line a remote caller may parse. Carries
# five RunResult fields (run_id, status, changed, summary, artifact_path);
# sync_name and operation deliberately do not appear. Changing it is a breaking
# change for consumers of this preview.
SUMMARY_LINE_FORMAT = "run %s finished: status=%s changed=%s summary=create:%d,update:%d,delete:%d artifact=%s"


class RunLogger(Protocol):
    """The run-logger surface this module uses.

    Structural, not nominal: `get_run_logger()` returns a `LoggerAdapter` inside
    a run context and a plain `Logger` outside one, and tests substitute a
    recording stub.
    """

    def log(self, level: int, msg: str, *args: object) -> None: ...

    def info(self, msg: str, *args: object) -> None: ...


class RunLoggerBridge(logging.Handler):
    """Forward `infrahub_sync` hierarchy records into the Prefect run logger.

    Attached to `logging.getLogger("infrahub_sync")` for the duration of one
    flow run, so the engine's existing lifecycle logging becomes remotely
    observable without any change to the engine.
    """

    def __init__(self, run_logger: RunLogger) -> None:
        super().__init__(level=BRIDGED_LEVEL)
        self._run_logger = run_logger

    def emit(self, record: logging.LogRecord) -> None:
        """Re-log the record through the run logger, preserving level and origin name."""
        try:
            self._run_logger.log(record.levelno, "%s | %s", record.name, record.getMessage())
        except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            # `logging.Handler.emit` must never propagate: `Handler.handle` does not
            # shield it, so anything raised here escapes at the unrelated logging
            # call site. A single bad `%`-format call in any `infrahub_sync.*` logger
            # — including a user-written custom adapter — would otherwise fail a run
            # that has already written to the destination. `handleError` is the
            # stdlib contract for exactly this, and it is what today's plain CLI
            # StreamHandler already does with the same bad call.
            self.handleError(record)


@flow(name=FLOW_NAME)
def infrahub_sync_run(
    sync_name: str,
    operation: Literal["plan", "sync"] = "plan",
    confirm_writes: bool = False,
    branch: str | None = None,
) -> dict:
    """Run one Infrahub Sync plan or explicitly confirmed sync via the shared surface.

    EXACTLY these four parameters; none accepts paths, CLI fragments,
    credentials, or environment overrides. The configuration directory comes
    from `INFRAHUB_SYNC_CONFIG_DIRECTORY`, validated once at serve start.

    Returns:
        An asdict-shaped seven-key dict of the `RunResult` (`summary` a plain
        `dict`), built explicitly — see the construction comment below.

    Raises:
        RunValidationError: request or configuration refusal.
        RunExecutionError: `INFRAHUB_SYNC_CONFIG_DIRECTORY` missing from the
            serving environment, or any adapter/engine failure (sanitized).
    """
    run_logger = get_run_logger()
    source_logger = logging.getLogger(SOURCE_LOGGER_NAME)
    bridge = RunLoggerBridge(run_logger)
    # The flow owns the source logger's LEVEL as well as the handler: attaching a
    # handler never defeats `Logger.isEnabledFor`, and the `infrahub_sync`
    # hierarchy is level-NOTSET by default (the CLI makes INFO effective through
    # `_setup_logging`, which this flow never calls). Without owning the level,
    # forwarding would silently depend on ambient root/Prefect configuration.
    previous_level = source_logger.level
    source_logger.addHandler(bridge)
    source_logger.setLevel(BRIDGED_LEVEL)
    try:
        config_directory = os.environ.get(CONFIG_DIR_ENV)
        if not config_directory:
            # Validated at serve start; checked again here so a flow run started
            # some other way fails with the actionable reason.
            msg = f"{CONFIG_DIR_ENV} is not set in the serving process environment"
            raise RunExecutionError(msg)

        result = run_remote_request(
            sync_name,
            operation,
            confirm_writes,
            branch,
            config_directory=config_directory,
        )
        run_logger.info(
            SUMMARY_LINE_FORMAT,
            result.run_id,
            result.status,
            result.changed,
            result.summary["create"],
            result.summary["update"],
            result.summary["delete"],
            result.artifact_path,
        )
        # NOT `dataclasses.asdict(result)`: asdict() deep-copies field values, and
        # `RunResult.summary` is a `types.MappingProxyType`, which is not
        # deep-copyable — it raises `TypeError: cannot pickle 'mappingproxy'
        # object`, so every successful run would fail at return time. Do not
        # "simplify" this back to asdict().
        out = {field.name: getattr(result, field.name) for field in dataclasses.fields(result)}
        out["summary"] = dict(result.summary)
        return out
    finally:
        source_logger.removeHandler(bridge)
        source_logger.setLevel(previous_level)
