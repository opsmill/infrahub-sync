"""Refuse to start a Sync API or a worker before the bootstrap of its version has run.

The bootstrap records, as its last step, the Sync version and the work pool it set up
(`bootstrap.py`). The Sync API and the worker read that record when they start, before
anything else touches the product database, and refuse to start when it is missing or names
another version or another work pool (spec 018 FR-007b). Under Kubernetes the refused process
exits non-zero and is restarted with backoff until the bootstrap Job has written the record;
under Compose, `depends_on` runs the bootstrap first, so the record is already there.

The read uses its own plain connection and runs no schema initialization and no column
migration, so a refused start changes nothing in the database. Nothing here logs a
connection string, a password or a driver message: the settings are named, never their
values, and the database's own errors are reduced to a fixed line.
"""

from __future__ import annotations

import contextlib
import logging
import os
from typing import TYPE_CHECKING

from infrahub_sync.product_store import ProductStoreProviderError, read_bootstrap_record

from .compatibility import installed_server_version
from .storage import DATABASE_URL_ENV, service_database_connection

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from infrahub_sync.product_store import BootstrapRecord, DBAPIConnection

logger = logging.getLogger(__name__)

# Every refusal ends with this line, whatever its cause: a missing or different record, a
# database that cannot be reached or read, or an unknown installed version. The line logged
# before it names the cause.
START_REFUSED_MESSAGE = (
    "refusing to start: the bootstrap record for this Sync version and work pool could not be confirmed"
)


class StartRefusedError(RuntimeError):
    """The process must not start: its bootstrap record is missing, different or unreadable.

    The message is fixed. The details, which never include a credential, are logged once
    before this is raised.
    """

    def __init__(self) -> None:
        super().__init__(START_REFUSED_MESSAGE)


def check_bootstrap_record(
    work_pool: str,
    *,
    environ: Mapping[str, str] | None = None,
    connect: Callable[[Mapping[str, str]], DBAPIConnection] = service_database_connection,
    sync_version: Callable[[], str] = installed_server_version,
) -> None:
    """Return when the bootstrap record names this Sync version and `work_pool`; refuse otherwise.

    Raises:
        StartRefusedError: when the record is missing or names another version or work pool,
            when the database setting is missing or invalid, when the database cannot be
            reached or read, or when this process cannot tell its own version.
    """
    values: Mapping[str, str] = os.environ if environ is None else environ
    try:
        expected_version = sync_version()
    except RuntimeError:
        logger.error("refusing to start: the installed Sync version is unknown")  # noqa: TRY400 - fixed line.
        raise StartRefusedError from None
    record = _read(values, connect)
    if record is None:
        logger.error(
            "refusing to start: no bootstrap record found; expected Sync version %s and work pool %s. "
            "Run the bootstrap for this version.",
            expected_version,
            work_pool,
        )
        raise StartRefusedError
    if record.sync_version != expected_version or record.work_pool != work_pool:
        logger.error(
            "refusing to start: the bootstrap record names Sync version %s and work pool %s; "
            "expected Sync version %s and work pool %s. Run the bootstrap for this version.",
            record.sync_version,
            record.work_pool,
            expected_version,
            work_pool,
        )
        raise StartRefusedError
    logger.info("bootstrap record matches Sync version %s and work pool %s", expected_version, work_pool)


def _read(values: Mapping[str, str], connect: Callable[[Mapping[str, str]], DBAPIConnection]) -> BootstrapRecord | None:
    """Read the record on one plain connection, reducing every failure to a fixed line."""
    try:
        connection = connect(values)
    except ValueError as error:
        # `storage._database_url` names the setting and the rule, never the value.
        logger.error("refusing to start: %s", error)  # noqa: TRY400 - a fixed message; no traceback.
        raise StartRefusedError from None
    except ProductStoreProviderError:
        logger.error("refusing to start: the product database (%s) cannot be reached", DATABASE_URL_ENV)  # noqa: TRY400
        raise StartRefusedError from None
    try:
        return read_bootstrap_record(connection)
    except (ProductStoreProviderError, ValueError):
        # ValueError: a stored row the record model refuses; its text is not logged.
        logger.error("refusing to start: the bootstrap record cannot be read from the product database")  # noqa: TRY400
        raise StartRefusedError from None
    finally:
        with contextlib.suppress(ProductStoreProviderError):
            connection.close()
