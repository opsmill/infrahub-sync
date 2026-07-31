"""Serve the packaged Infrahub Sync deployment: `python -m infrahub_sync.orchestration.serve`.

A locally served deployment — no work pool and no separate worker process. The
Prefect server itself is a prerequisite (`prefect server start`, with
`PREFECT_API_URL` pointing at it), as is starting this process from the
REPOSITORY ROOT: a configuration's relative paths and the run cache resolve
against this process's working directory.

`INFRAHUB_SYNC_CONFIG_DIRECTORY` is validated once here, before any deployment is
served. Configuration CONTENT under that directory is re-resolved per run, so
editing or adding a configuration needs no restart.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

MISSING_EXTRA_MESSAGE = "prefect is not installed - install the optional integration: pip install -e '.[prefect]'"

try:
    from infrahub_sync.orchestration.flow import (
        CONFIG_DIR_ENV,
        DEPLOYMENT_NAME,
        FLOW_NAME,
        infrahub_sync_run,
    )
except ImportError:
    # The optional extra is missing: exactly one actionable line naming it,
    # never a traceback. `from None` keeps the ImportError out of the output.
    # `sys.stderr.write` rather than `print()` (which the package's AST test
    # rejects) and rather than `logging`, so a startup refusal does not depend
    # on logging configuration that is not in place yet.
    sys.stderr.write(f"{MISSING_EXTRA_MESSAGE}\n")
    raise SystemExit(1) from None

logger = logging.getLogger(__name__)


def _error(message: str) -> None:
    """Emit exactly one error line, independently of logging configuration."""
    sys.stderr.write(f"{message}\n")


def resolve_config_directory() -> str | None:
    """Return the validated configuration directory, or None after reporting why.

    Invalid when `INFRAHUB_SYNC_CONFIG_DIRECTORY` is unset, empty, or does not
    name an existing directory. Every refusal names the variable.
    """
    value = os.environ.get(CONFIG_DIR_ENV, "")
    if not value:
        _error(f"{CONFIG_DIR_ENV} is not set: point it at the directory holding your sync configurations")
        return None
    if not Path(value).is_dir():
        _error(f"{CONFIG_DIR_ENV}={value!r} is not an existing directory")
        return None
    return value


def main() -> int:
    """Validate the environment, then serve the deployment until interrupted."""
    config_directory = resolve_config_directory()
    if config_directory is None:
        return 1
    logger.info(
        "Serving deployment %s/%s with configurations from %s",
        FLOW_NAME,
        DEPLOYMENT_NAME,
        config_directory,
    )
    infrahub_sync_run.serve(name=DEPLOYMENT_NAME)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
