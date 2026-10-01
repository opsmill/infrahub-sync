"""Compatibility tests for the diffsync Redis store under the declared redis client.

`redis` is a DIRECT base dependency (it used to arrive through the
`diffsync[redis]` extra, whose `redis<5.0` cap is unsatisfiable next to the
optional `prefect` extra). `infrahub_sync/utils.py` imports
`diffsync.store.redis.RedisStore` unconditionally, so *import* and API
compatibility with whatever redis client resolves is what matters for every
user — not whether any shipped configuration enables the store (none does;
`utils.get_potenda_from_instance` defaults to `LocalStore`).

These assertions require no server. V3 refuses configured sync stores; the
unconditional import still needs to work under the declared redis dependency.
"""

from __future__ import annotations

import socket

import pytest
import redis
from diffsync.exceptions import ObjectStoreException
from diffsync.store.redis import RedisStore

# The client API surface `diffsync/store/redis.py` actually calls.
REQUIRED_CLIENT_METHODS = ("ping", "get", "set", "exists", "delete", "scan_iter")


def _closed_port() -> int:
    """Return a port that was bound and released, so connecting to it is refused."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_redis_store_imports_cleanly() -> None:
    """The unconditional `utils.py` import resolves under the installed client."""
    assert RedisStore.__name__ == "RedisStore"


def test_redis_client_module_exposes_the_constructors_the_store_uses() -> None:
    assert callable(redis.Redis)
    assert callable(redis.Redis.from_url)


@pytest.mark.parametrize("method", REQUIRED_CLIENT_METHODS)
def test_redis_client_exposes_required_method(method: str) -> None:
    """A moved/renamed client method would break the store silently at run time."""
    assert callable(getattr(redis.Redis, method, None)), f"redis.Redis.{method} is missing"


def test_redis_store_translates_a_connection_failure() -> None:
    """The store↔client error contract: the client's ConnectionError is still caught.

    `RedisStore` catches `redis.exceptions.ConnectionError` and re-raises
    `ObjectStoreException`. If the client's exception hierarchy moved, this
    raises the raw client error instead and the test fails loudly.
    """
    with pytest.raises(ObjectStoreException):
        RedisStore(host="127.0.0.1", port=_closed_port())


def test_redis_store_rejects_url_and_host_together() -> None:
    """The store's own guard, reachable without a server."""
    with pytest.raises(ValueError, match="can't be specified together"):
        RedisStore(url="redis://127.0.0.1:6379", host="127.0.0.1")
