"""Publish which credential variables a worker holds, by name only (spec 009 FR-033).

`configs validate --branch` runs in `sync-api`, which does not hold the source credentials a
run reads. The worker does, so it writes the names of its set, non-empty credential
variables (the `ENV_CREDENTIAL_PREFIX` namespace) to the product store when it starts and on
each poll.
Validation compares a package's `$credential` references with the most recent record of a
live worker. The record holds names only: a value never leaves the worker process.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import psycopg

from infrahub_sync.configuration.credentials import ENV_CREDENTIAL_PREFIX
from infrahub_sync.product_store import ProductStoreProviderError, WorkerEnvironment

from .storage import ServiceStorageStartupError, service_product_projection

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from infrahub_sync.product_store import ProductProjection

logger = logging.getLogger(__name__)

_CREDENTIAL_NAME = re.compile(rf"{ENV_CREDENTIAL_PREFIX}[A-Za-z0-9_]+")

# A worker publishes on every poll (`PREFECT_WORKER_QUERY_SECONDS`, 10 seconds in the
# operator deployment, 15 by default). A record older than this belongs to a worker that
# stopped polling. Ninety seconds is the age at which Prefect itself marks a worker offline
# with its default heartbeat.
LIVE_WORKER_MAX_AGE = timedelta(seconds=90)

_PUBLICATION_FAILED = "The worker could not publish its credential variable names (%s); it retries on the next poll."

# A driver error that reaches the publisher untranslated (SQLite, or a Psycopg error raised
# outside the service connection wrapper) is a store failure like any other: it must not end
# the poll loop or stop the worker at start.
_STORE_FAILURES = (ServiceStorageStartupError, ProductStoreProviderError, ValueError, sqlite3.Error, psycopg.Error)


def credential_names(environ: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """Return the sorted names of the set, non-empty credential variables of `environ`.

    Compose passes an unset variable as an empty string, so an empty value counts as unset.
    """
    values: Mapping[str, str] = os.environ if environ is None else environ
    return tuple(sorted(name for name, value in values.items() if value and _CREDENTIAL_NAME.fullmatch(name)))


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class WorkerEnvironmentPublisher:
    """Write one worker's credential variable names to the product store.

    A publication that cannot reach the store is logged and skipped: the worker keeps
    running, and the next poll tries again with a new connection.
    """

    def __init__(
        self,
        worker_id: str,
        *,
        projection_factory: Callable[[], ProductProjection] = service_product_projection,
        environ: Mapping[str, str] | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._worker_id = worker_id
        self._projection_factory = projection_factory
        self._environ = environ
        self._clock = clock
        self._projection: ProductProjection | None = None

    def publish(self) -> bool:
        """Publish the current names; return whether the store recorded them."""
        record = WorkerEnvironment(
            worker_id=self._worker_id,
            credential_names=credential_names(self._environ),
            observed_at=self._clock(),
        )
        try:
            if self._projection is None:
                self._projection = self._projection_factory()
            self._projection.record_worker_environment(record)
        except _STORE_FAILURES as error:
            # Only the error class is logged: a storage setting or a driver message can hold
            # a secret (docs/docs/development/guidelines/secret-redaction.md).
            self._projection = None
            logger.warning(_PUBLICATION_FAILED, type(error).__name__)
            return False
        return True


def latest_live_worker_environment(
    projection: ProductProjection,
    *,
    now: datetime | None = None,
    max_age: timedelta = LIVE_WORKER_MAX_AGE,
) -> WorkerEnvironment | None:
    """Return the most recent record of a live worker, or `None` when no live worker published one."""
    reference = _utc_now() if now is None else now
    return projection.latest_worker_environment(observed_after=reference - max_age)
