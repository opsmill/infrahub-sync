"""The order in which `ensure_smoke_branch` seeds the smoke device and forks the smoke branch.

`dev.seed` and `dev.smoke` call it against the stack's Infrahub. Every other test stubs it
out, so these are the only checks of its order before the nightly smoke run.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import infrahub_sdk

from tasks.smoke_dataset import (
    SHARED_DEVICE_NAME,
    SHARED_DEVICE_SEED_TYPE,
    SMOKE_BRANCH,
    SMOKE_KIND,
    ensure_smoke_branch,
)
from tasks.utils import REPO_BASE

if TYPE_CHECKING:
    import pytest

ADDRESS = "http://127.0.0.1:8000"
TOKEN = "local-token"  # noqa: S105 -- never sent anywhere; the client is a fake
# The source the CLI-cycle smoke plans against the same branch.
CLI_SMOKE_SOURCE = REPO_BASE / "examples" / "custom_adapter" / "custom_adapter_src" / "mock_db.json"


class _SmokeUpsert:
    """Stand-in for the node `create` returns; only `save` is called on it."""

    def __init__(self, events: list[tuple[object, ...]]) -> None:
        self._events = events

    def save(self, *, allow_upsert: bool) -> None:
        self._events.append(("save", allow_upsert))


class _SmokeBranchManager:
    def __init__(self, events: list[tuple[object, ...]], branches: dict[str, object]) -> None:
        self._events = events
        self.branches = dict(branches)

    def all(self) -> dict[str, object]:
        return self.branches

    def create(self, branch_name: str, *, sync_with_git: bool) -> None:
        self._events.append(("branch", branch_name, sync_with_git))
        self.branches[branch_name] = object()


class _SmokeSetupClient:
    """Record what `ensure_smoke_branch` does, in order, against a fake Infrahub.

    Order is the property under test, not merely which calls happen, so every write is
    appended to one shared list rather than to per-call counters.
    """

    def __init__(self, branches: dict[str, object] | None = None) -> None:
        self.events: list[tuple[object, ...]] = []
        self.branch = _SmokeBranchManager(self.events, branches or {})

    def create(self, *, kind: str, branch: str, data: dict[str, str]) -> _SmokeUpsert:
        self.events.append(("create", kind, branch, dict(data)))
        return _SmokeUpsert(self.events)


def test_the_shared_device_is_seeded_on_main_before_the_smoke_branch_forks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A branch inherits what `main` held when it forked, so the seed has to come first.

    Seeded afterwards the device would exist on `main` alone, and the registered smoke
    would plan a create. Seeded before, both branches carry it and that smoke has a real
    update to plan and apply.
    """
    client = _SmokeSetupClient()
    monkeypatch.setattr(infrahub_sdk, "InfrahubClientSync", lambda **_kwargs: client)

    ensure_smoke_branch(ADDRESS, TOKEN)

    assert client.events == [
        ("create", SMOKE_KIND, "main", {"name": SHARED_DEVICE_NAME, "type": SHARED_DEVICE_SEED_TYPE}),
        ("save", True),
        ("branch", SMOKE_BRANCH, False),
    ]


def test_smoke_branch_setup_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """A second seed neither reforks the branch nor reseeds `main`."""
    client = _SmokeSetupClient()
    monkeypatch.setattr(infrahub_sdk, "InfrahubClientSync", lambda **_kwargs: client)

    ensure_smoke_branch(ADDRESS, TOKEN)
    after_first_pass = list(client.events)
    ensure_smoke_branch(ADDRESS, TOKEN)

    assert client.events == after_first_pass


def test_an_existing_smoke_branch_is_left_entirely_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """Seeding after the fork cannot reach the branch, so it is not attempted at all.

    Re-forking would be worse than useless: it would discard the destination both smokes
    have been writing to.
    """
    client = _SmokeSetupClient({SMOKE_BRANCH: object()})
    monkeypatch.setattr(infrahub_sdk, "InfrahubClientSync", lambda **_kwargs: client)

    ensure_smoke_branch(ADDRESS, TOKEN)

    assert not client.events, client.events


def test_the_seeded_device_is_one_the_cli_smoke_source_already_owns() -> None:
    """The seed must not leave the CLI-cycle smoke a delete it can never converge.

    That smoke plans `custom-example`'s source against this same branch and asserts the
    re-plan holds zero operations. A device on the branch its source does not own is a
    recorded, never-executed delete — permanently non-zero. Reusing a name the source
    already owns keeps the two smokes independent of each other in both orders.
    """
    devices = json.loads(CLI_SMOKE_SOURCE.read_text(encoding="utf-8"))["nodes"]["devices"]

    assert SHARED_DEVICE_NAME in {device["name"] for device in devices}
