"""A Sync API over a SQLite store for the tests of the account routes.

``build_account_api`` gives a test the API with three accounts:

- ``admin``, the account that the environment manages, with its environment token;
- ``root``, an administrator that a person created;
- ``alice``, an account that is not an administrator.

The gate of password checks is replaced with one that compares texts, so no scrypt hash is
computed: a test that needs a real hash asks for the real gate. ``AccountApi.call`` sends a request
and checks that the answer shows no account identifier, no password hash and no digest.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, cast

from fastapi.testclient import TestClient

from infrahub_sync.product_store import local_product_projection
from infrahub_sync.service import credentials
from infrahub_sync.service.app import create_app
from infrahub_sync.service.auth import ADMIN_ACCOUNT_NAME, ADMIN_ACCOUNT_TOKEN_NAME, StoreAccountResolver
from infrahub_sync.service.config_routes import ConfigurationRoutes
from infrahub_sync.service.orchestration import CancellationResult, Observation, PoolStatus, RunLogRead, Submission
from infrahub_sync.service.service import RunService
from infrahub_sync.service.settings import SESSION_IDLE_SECONDS_ENV
from tests.configuration.validation_packages import package_data

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    import pytest
    from httpx import Response

    from infrahub_sync.product_store import Account, AuditEvent, ProductProjection

PASSWORD = "correct-horse-battery"  # noqa: S105 - a test value, not a secret.
NEW_PASSWORD = "another-horse-battery"  # noqa: S105 - a test value, not a secret.
WRONG_PASSWORD = "wrong-horse-battery"  # noqa: S105 - a test value, not a secret.
ADMIN_PASSWORD = "first-admin-password"  # noqa: S105 - a test value, not a secret.
ADMIN_TOKEN = "admin-token-canary-0022"  # noqa: S105 - a test value, not a secret.
T0 = datetime(2026, 11, 18, 9, 0, tzinfo=timezone.utc)
SECOND = timedelta(seconds=1)
IDLE_SECONDS = 600
TEXT_HASH_PREFIX = "text-hash-of:"
TEXT_GATE_NOT_ADMITTED = "the text gate did not check the password"

_DIGEST = re.compile(r"[0-9a-f]{64}")


def text_hash(password: str) -> str:
    """Return the text that stands for the hash of a password under ``TextGate``."""
    return f"{TEXT_HASH_PREFIX}{password}"


class TextGate:
    """A gate that compares texts and counts the hashes it was asked for, never a password.

    ``while_waiting`` runs where a request waits for a place, before ``admit``; ``while_checking``
    runs after the comparison, while the place is held. A test uses them to make another request
    act at those moments.
    """

    def __init__(self) -> None:
        self.hashes = 0
        self.checks = 0
        self.while_waiting: Callable[[], object] | None = None
        self.while_checking: Callable[[], object] | None = None
        self._lock = threading.Lock()

    def verify(self, password: str, stored_hash: str, *, admit: Callable[[], bool] | None = None) -> bool:
        if self.while_waiting is not None:
            self.while_waiting()
        if admit is not None and not admit():
            raise credentials.VerificationNotAdmittedError(TEXT_GATE_NOT_ADMITTED)
        with self._lock:
            self.checks += 1
        verified = stored_hash == text_hash(password)
        if self.while_checking is not None:
            self.while_checking()
        return verified

    def hash(self, password: str) -> str:
        with self._lock:
            self.hashes += 1
        return text_hash(password)


class Clock:
    """The clock of the service and of the resolver, which a test moves."""

    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


class Orchestration:
    """An orchestration that accepts every submission and runs nothing."""

    async def submit(self, parameters: dict[str, object], *, idempotency_key: str) -> Submission:  # noqa: PLR6301
        del parameters, idempotency_key
        return Submission(flow_run_id="test-flow", state="pending")

    async def observe(self, flow_run_id: str) -> Observation:  # noqa: PLR6301
        del flow_run_id
        return Observation(available=True, state="running")

    async def pool_status(self, work_pool_name: str, now: datetime) -> PoolStatus:  # noqa: PLR6301
        del work_pool_name, now
        return PoolStatus(detail_available=False, queue_depth=None, observed_at=None)

    async def cancel(self, flow_run_id: str) -> CancellationResult:  # noqa: PLR6301
        del flow_run_id
        return CancellationResult(acknowledged=True)

    async def read_logs(self, flow_run_ids: Sequence[str]) -> RunLogRead:  # noqa: PLR6301
        del flow_run_ids
        return RunLogRead(expired=False)


def assert_shows_no_stored_secret(response: Response) -> None:
    """Check that an answer shows no account identifier, no password hash and no digest."""
    text = response.text
    assert "acct_" not in text
    assert "scrypt$" not in text
    assert TEXT_HASH_PREFIX not in text
    assert _DIGEST.search(text) is None


@dataclass
class AccountApi:
    """The API, its store and what a test controls: the clock and the gate of password checks."""

    client: TestClient
    store: ProductProjection
    service: RunService
    clock: Clock
    gate: TextGate
    _token_names: int = field(default=0)
    _version: dict[str, Any] | None = field(default=None)
    # The token values that a case received in an answer, to check that the store holds none of them.
    received_values: list[str] = field(default_factory=list)

    def call(self, method: str, path: str, headers: dict[str, str] | None = None, **arguments: Any) -> Response:  # noqa: ANN401
        """Send one request and check that its answer shows nothing that the store keeps secret."""
        response = self.client.request(method, path, headers=headers, **arguments)
        assert_shows_no_stored_secret(response)
        return response

    def add(self, name: str, *, password: str = PASSWORD, administrator: bool = False) -> Account:
        """Create an account in the store whose hash stands for this password under the text gate."""
        return self.store.create_account(
            name=name, password_hash=text_hash(password), administrator=administrator, now=self.clock.now
        )

    def identifier(self, name: str) -> str:
        """Return the identifier that the records of the store hold for this account."""
        account = self.store.lookup_account_by_name(name)
        assert account is not None
        return account.account_id

    def token(self, name: str, *, expires_at: datetime | None = None) -> str:
        """Create an API token for the account in the store and return its value."""
        value = credentials.new_api_token()
        self._token_names += 1
        self.store.create_account_token(
            self.identifier(name),
            name=f"seeded-{self._token_names}",
            digest=credentials.digest(value),
            expires_at=expires_at,
            now=self.clock.now,
        )
        return value

    def token_headers(self, name: str) -> dict[str, str]:
        """Return the header that presents a new API token of the account."""
        return bearer(self.token(name))

    def sign_in(self, name: str, password: str = PASSWORD) -> Response:
        return self.call("POST", "/auth/sign-in", json={"name": name, "password": password})

    def session_headers(self, name: str, password: str = PASSWORD) -> dict[str, str]:
        """Sign in and return the header that presents the session token."""
        response = self.sign_in(name, password)
        assert response.status_code == 200, response.json()["error"]["code"]
        return bearer(response.json()["token"])

    def events(self) -> list[AuditEvent]:
        """Return every audit event of the store, oldest first."""
        return list(self.store.audit_events())

    def account_events(self) -> list[AuditEvent]:
        """Return the events of the account actions, oldest first."""
        return [event for event in self.events() if (event.event_type or "").startswith("infrahub_sync.account.")]

    def refusals(self, outcome: str) -> list[AuditEvent]:
        """Return the events with this outcome. Events of one instant have no order, so a test names the outcome."""
        return [event for event in self.events() if event.outcome == outcome]

    def stored_text(self) -> str:
        """Return every audit event as text, to check what the store holds of a request."""
        return repr(self.events())

    def register(self, *, reason: str = "register") -> dict[str, Any]:
        """Register one configuration as ``admin``, once, and return its version."""
        if self._version is None:
            registered = self.client.post(
                "/configs",
                headers={**bearer(ADMIN_TOKEN), "Idempotency-Key": "register-for-account-tests"},
                json={"package": package_data(), "reason": reason},
            )
            assert registered.status_code == 201, registered.text
            self._version = registered.json()["version"]
        return self._version

    def create_run(self, headers: dict[str, str], *, reason: str | None = "review the plan") -> Response:
        """Create a plan run of the registered configuration with these headers."""
        version = self.register()
        body: dict[str, object] = {
            "operation": "plan",
            "config_id": version["config_id"],
            "registry_version": version["registry_version"],
        }
        if reason is not None:
            body["reason"] = reason
        self._token_names += 1
        return self.client.post("/runs", headers={**headers, "Idempotency-Key": f"run-{self._token_names}"}, json=body)


def bearer(value: str) -> dict[str, str]:
    """Return the header that presents an API token or a session token."""
    return {"Authorization": f"Bearer {value}"}


def build_account_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AccountApi:
    """Return the API with ``admin``, ``root`` and ``alice``, under the text gate and a clock at ``T0``."""
    monkeypatch.setenv(SESSION_IDLE_SECONDS_ENV, str(IDLE_SECONDS))
    gate = TextGate()
    monkeypatch.setattr(credentials, "PASSWORD_VERIFICATION_GATE", gate)
    store = local_product_projection(tmp_path.resolve())
    clock = Clock()
    admin = store.create_account(
        name=ADMIN_ACCOUNT_NAME,
        password_hash=text_hash(ADMIN_PASSWORD),
        administrator=True,
        environment_managed=True,
        now=T0,
    )
    store.create_account_token(
        admin.account_id,
        name=ADMIN_ACCOUNT_TOKEN_NAME,
        digest=credentials.digest(ADMIN_TOKEN),
        environment_managed=True,
        now=T0,
    )
    resolver = StoreAccountResolver(store, idle_seconds=IDLE_SECONDS, admin_token=ADMIN_TOKEN, clock=clock)
    service = RunService(store, cast("Any", Orchestration()), secrets=resolver.secret_values, clock=clock)
    routes = ConfigurationRoutes(product_projection=store, secrets=resolver.secret_values)
    api = AccountApi(
        client=TestClient(create_app(service, resolver, routes)), store=store, service=service, clock=clock, gate=gate
    )
    api.add("root", administrator=True)
    api.add("alice")
    return api
