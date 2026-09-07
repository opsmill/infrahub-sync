"""What the kit hands the product's client, checked against the client itself.

The clean-host checks run inside the candidate image and this suite reaches
neither that image nor a deployment. What it can do is build the requests the kit
builds and offer them to the real `SyncClient`, whose own guards decide what is
acceptable — so a change to those guards fails the kit here rather than on a
host, twenty-five minutes into a matrix.

Asserting instead that a request carries `confirm_writes=True` would restate the
kit's implementation and agree with it however wrong both became.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from infrahub_sync.client import ClientInputError, SyncClient

CHECKS = Path(__file__).resolve().parents[2] / "tests" / "compose" / "clean_host" / "checks"

CONFIG_ID = "clean-host-config"
REGISTRY_VERSION = 3


@pytest.fixture(scope="module")
def kit() -> Iterator[ModuleType]:
    """Load the shared kit module the way a check inside the image imports it."""
    sys.path.insert(0, str(CHECKS))
    try:
        specification = importlib.util.spec_from_file_location("clean_host_kit", CHECKS / "kit.py")
        assert specification is not None
        loader = specification.loader
        assert loader is not None
        module = importlib.util.module_from_spec(specification)
        loader.exec_module(module)
        yield module
    finally:
        sys.path.remove(str(CHECKS))


@pytest.fixture
def offered(monkeypatch: pytest.MonkeyPatch) -> tuple[SyncClient, list[Any]]:
    """A real client whose one network call is replaced, so only its guards run."""
    accepted: list[Any] = []

    def record(self: SyncClient, request: Any, idempotency_key: str) -> Any:  # noqa: ANN401 -- whatever it is given
        del self, idempotency_key
        accepted.append(request)
        return request

    monkeypatch.setattr(SyncClient, "create_run", record)
    return SyncClient("http://sync.invalid:8000", "a-token"), accepted


@pytest.mark.parametrize(("operation", "route"), [("plan", "plan"), ("sync", "sync")])
def test_the_client_accepts_the_request_the_kit_builds_for_that_operation(
    kit: ModuleType, offered: tuple[SyncClient, list[Any]], operation: str, route: str
) -> None:
    """The pairing rule lives in the client, and this is the kit obeying it."""
    client, accepted = offered
    request = kit.create_run(operation, config_id=CONFIG_ID, registry_version=REGISTRY_VERSION, reason="a reason")

    getattr(client, route)(request, "an-idempotency-key")

    assert accepted == [request]


@pytest.mark.parametrize(("operation", "route"), [("plan", "sync"), ("sync", "plan")])
def test_the_client_refuses_the_request_the_kit_builds_for_the_other_operation(
    kit: ModuleType, offered: tuple[SyncClient, list[Any]], operation: str, route: str
) -> None:
    """Confirmation is not a free choice: each route refuses the other's request.

    This is what makes the test above mean something. Without it, a builder that
    confirmed everything would satisfy both routes and pass.
    """
    client, accepted = offered
    request = kit.create_run(operation, config_id=CONFIG_ID, registry_version=REGISTRY_VERSION, reason="a reason")

    with pytest.raises(ClientInputError):
        getattr(client, route)(request, "an-idempotency-key")

    assert accepted == []


def test_the_request_carries_the_identifiers_it_was_given(kit: ModuleType) -> None:
    """A row that registers its own configuration runs against that one, not the bundled one."""
    request = kit.create_run("plan", config_id=CONFIG_ID, registry_version=REGISTRY_VERSION, reason="a reason")

    assert request.config_id == CONFIG_ID
    assert request.registry_version == REGISTRY_VERSION
    assert request.reason == "a reason"
