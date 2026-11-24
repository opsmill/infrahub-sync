"""A stand-in for the Sync API client the development tasks store credentials with.

`dev.start` stores the development credentials through the Sync API after the stack starts.
No test of the development tasks may reach a Sync API, so every test in this directory gets
this stand-in in place of the real client. It keeps a digest of each value, never the value.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from infrahub_sync.client import APIError
from tasks import dev

if TYPE_CHECKING:
    from types import TracebackType

    from typing_extensions import Self


def digest(value: str) -> str:
    """The SHA-256 digest a test compares instead of a credential value."""
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass
class CredentialStore:
    """What the stand-in recorded, and how it answers.

    ``existing`` names the credentials the Sync API already holds. ``refusal`` is the status
    and code every request is refused with, when set.
    """

    opened: list[tuple[str, str]] = field(default_factory=list)
    calls: list[tuple[str, str, str]] = field(default_factory=list)
    existing: set[str] = field(default_factory=set)
    refusal: tuple[int, str] | None = None

    def open(self, service_url: str, token: str) -> CredentialClient:
        """Stand in for `dev._open_client`; records the address and the digest of the API token."""
        self.opened.append((service_url, digest(token)))
        return CredentialClient(self)


class CredentialClient:
    """The two calls of `SyncClient` the development tasks use."""

    def __init__(self, store: CredentialStore) -> None:
        self._store = store

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        return None

    def _refuse(self) -> None:
        if self._store.refusal is not None:
            raise APIError(*self._store.refusal)

    def create_credential(self, name: str, *, token: str, description: str | None = None) -> None:
        assert description, "a development credential says what it is for"
        self._refuse()
        if name in self._store.existing:
            raise APIError(409, "credential-exists")
        self._store.existing.add(name)
        self._store.calls.append(("create", name, digest(token)))

    def set_credential_value(self, name: str, *, token: str) -> None:
        self._refuse()
        assert name in self._store.existing
        self._store.calls.append(("set", name, digest(token)))


@pytest.fixture(autouse=True)
def credential_store(monkeypatch: pytest.MonkeyPatch) -> CredentialStore:
    """Replace the Sync API client of the development tasks for every test in this directory."""
    store = CredentialStore()
    monkeypatch.setattr(dev, "_open_client", store.open, raising=False)
    return store
