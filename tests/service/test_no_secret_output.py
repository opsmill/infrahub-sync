"""No password, token value, digest or password hash leaves the Sync API (spec 022, SC-005).

One case runs the flow of User Story 1, and the deletion of User Story 3, through the HTTP API of
a test application over a SQLite store. The ``admin`` account is applied from a known password
and token, and every password is hashed and checked with the real scrypt gate, so the store holds
real hashes. The case then searches, for each secret of the flow:

- every answer: its status line, its headers and its body;
- every log line: the records of the standard ``logging`` module (``caplog``), the events of
  ``structlog`` (``capture_logs``) and what the process wrote to standard output and standard
  error (``capfd``);
- every text column of every row of every table of the store, read from the SQLite catalog, so
  the audit tables (``audit_events``, ``audit_event_resources``) and the stored answers
  (``mutation_receipts``) are searched with every other table;
- every artifact file the store wrote.

The secrets are the passwords (the ones accepted, the ones refused and the ones replaced), the
session tokens, the API tokens, the token of ``admin``, the SHA-256 digest of each of these values
and each scrypt hash. Each one is allowed in a fixed list of places only; see ``_Secrets.allow``.

The negative controls at the end of the module put a secret in a log line, in an answer and in a
row of the store, and check that the same search finds each one.
"""

from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("prefect")

import structlog
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from infrahub_sync.product_store import local_product_projection
from infrahub_sync.service.app import create_app
from infrahub_sync.service.auth import StoreAccountResolver, apply_admin_account
from infrahub_sync.service.config_routes import ConfigurationRoutes
from infrahub_sync.service.service import RunService
from infrahub_sync.service.settings import SESSION_IDLE_SECONDS_ENV, AdminAccountSettings
from tests.configuration.validation_packages import package_data
from tests.service.account_api import Orchestration

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence
    from pathlib import Path

    from httpx import Response

# Each value is long and holds a mark that no name, path, field or generated value contains, so
# a match is a leak and not a coincidence.
_ADMIN_PASSWORD = "admin-password-Q7vX-sc005"  # noqa: S105 - a test value, not a secret.
_ADMIN_TOKEN = "admin-token-K2mW-sc005-value"  # noqa: S105 - a test value, not a secret.
_FIRST_PASSWORD = "first-password-R4nB-sc005"  # noqa: S105 - a test value, not a secret.
_CHANGED_PASSWORD = "changed-password-T8pL-sc005"  # noqa: S105 - a test value, not a secret.
_ADMIN_SET_PASSWORD = "admin-set-password-Y3hD-sc005"  # noqa: S105 - a test value, not a secret.
_WRONG_CURRENT_PASSWORD = "wrong-current-password-U6cF-sc005"  # noqa: S105 - a test value, not a secret.
_DELETED_PASSWORD = "deleted-account-password-J5sG-sc005"  # noqa: S105 - a test value, not a secret.
_WRONG_PASSWORDS = tuple(f"wrong-password-{index}-M9zA-sc005" for index in range(1, 7))
_IDLE_SECONDS = 600
_NAME = "carol.sc005"
_DELETED_NAME = "dave.sc005"
_SQLITE_FILE = "product-records.sqlite3"
_SCRYPT = re.compile(r"scrypt\$\d")
# The only places the store must hold a digest: the one lookup by digest of each kind of value
# (research R3). A digest anywhere else, a password hash included, is a leak.
_DIGEST_COLUMNS = frozenset({"store account_tokens.digest", "store account_sessions.digest"})
# The only place the store must hold a password hash (research R2).
_HASH_COLUMNS = frozenset({"store accounts.password_hash"})


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass
class _Secrets:
    """Every secret of the flow, with the places where each one may appear.

    ``allow`` maps a secret to the labels of the texts that may hold it. A secret with no entry
    may appear nowhere. A label names one text: one answer, one log channel or one column.
    """

    values: dict[str, str] = field(default_factory=dict)
    allow: dict[str, frozenset[str]] = field(default_factory=dict)

    def add(self, kind: str, value: str, *, allowed_in: frozenset[str] = frozenset()) -> None:
        """Add a value, and its SHA-256 digest, which may appear in the digest columns only."""
        self.values[f"{kind} {len(self.values)}"] = value
        if allowed_in:
            self.allow[value] = allowed_in
        digest = _sha256(value)
        self.values[f"digest of {kind} {len(self.values)}"] = digest
        self.allow[digest] = _DIGEST_COLUMNS

    def add_hash(self, stored_hash: str) -> None:
        """Add a password hash, which may appear in ``accounts.password_hash`` only."""
        self.values[f"password hash {len(self.values)}"] = stored_hash
        self.allow[stored_hash] = _HASH_COLUMNS


@dataclass
class _Corpus:
    """Every text the flow produced, each with its label."""

    texts: list[tuple[str, str]] = field(default_factory=list)

    def add(self, label: str, text: str) -> None:
        self.texts.append((label, text))


def _leaks(corpus: _Corpus, secrets: _Secrets) -> list[str]:
    """Return one line for each secret found in a text where it is not allowed.

    A line names the kind of the secret and the label of the text, never the value, so a failing
    run prints no secret either.
    """
    found: list[str] = []
    for label, text in corpus.texts:
        for kind, value in secrets.values.items():
            if value in text and label not in secrets.allow.get(value, frozenset()):
                found.append(f"{kind} in {label}")
        if _SCRYPT.search(text) and label not in _HASH_COLUMNS:
            found.append(f"a scrypt hash in {label}")
    return found


def _answer_text(response: Response) -> str:
    headers = "\n".join(f"{name}: {value}" for name, value in response.headers.items())
    return f"{response.status_code}\n{headers}\n{response.text}"


def _store_texts(root: Path) -> Iterator[tuple[str, str]]:
    """Yield every non-empty value of every column of every table, as text, from the SQLite catalog.

    The tables and columns come from the database itself, so a table or a column added later is
    searched too.
    """
    connection = sqlite3.connect(root / _SQLITE_FILE)
    try:
        tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        for table in tables:
            columns = [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]
            for row in connection.execute(f"SELECT * FROM {table}"):  # noqa: S608 - a table of the catalog.
                for column, value in zip(columns, row, strict=True):
                    if value is not None:
                        text = value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)
                        yield f"store {table}.{column}", text
    finally:
        connection.close()


def _file_texts(root: Path) -> Iterator[tuple[str, str]]:
    """Yield the content of every file the store wrote, except the database, which is read by column."""
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.name.startswith(_SQLITE_FILE):
            yield f"file {path.relative_to(root)}", path.read_bytes().decode("utf-8", "replace")


@dataclass
class _Api:
    """The test application, its store and every answer it gave, each with its label."""

    client: TestClient
    root: Path
    answers: list[tuple[str, Response]] = field(default_factory=list)

    def call(self, method: str, path: str, *, token: str, expected: int, **arguments: Any) -> Response:  # noqa: ANN401
        headers = {"Authorization": f"Bearer {token}"}
        if method == "POST" and path in {"/configs", "/runs"}:
            headers["Idempotency-Key"] = f"sc005-{len(self.answers)}"
        response = self.client.request(method, path, headers=headers, **arguments)
        assert response.status_code == expected, (method, path, response.text)
        return self._keep(method, path, response)

    def sign_in(self, name: str, password: str, *, expected: int = 200) -> Response:
        response = self.client.post("/auth/sign-in", json={"name": name, "password": password})
        assert response.status_code == expected, response.text
        return self._keep("POST", "/auth/sign-in", response)

    def _keep(self, method: str, path: str, response: Response) -> Response:
        label = f"answer {len(self.answers)} {method} {path}"
        self.answers.append((label, response))
        return response

    @property
    def last_label(self) -> str:
        return self.answers[-1][0]


def _build_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Api:
    """Return the API with the ``admin`` account applied from a known password and token.

    The case of the flow calls it inside its captures, so the start-up is searched too.
    """
    monkeypatch.setenv(SESSION_IDLE_SECONDS_ENV, str(_IDLE_SECONDS))
    root = tmp_path.resolve()
    store = local_product_projection(root)
    apply_admin_account(store, AdminAccountSettings(password=_ADMIN_PASSWORD, token=_ADMIN_TOKEN))
    resolver = StoreAccountResolver(store, idle_seconds=_IDLE_SECONDS, admin_token=_ADMIN_TOKEN)
    service = RunService(store, cast("Any", Orchestration()), secrets=resolver.secret_values)
    routes = ConfigurationRoutes(product_projection=store, secrets=resolver.secret_values)
    return _Api(client=TestClient(create_app(service, resolver, routes)), root=root)


@pytest.fixture
def api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Api:
    """Return the API for the negative controls, which plant their secret after the start-up."""
    return _build_api(tmp_path, monkeypatch)


def _run_the_flow(api: _Api, secrets: _Secrets) -> None:
    """Run User Story 1 and the deletion of User Story 3, and record each secret where it may appear."""
    secrets.add("password of admin", _ADMIN_PASSWORD)
    secrets.add("token of admin", _ADMIN_TOKEN)

    # The administrator signs in and creates an account with a first password.
    admin_session = api.sign_in("admin", _ADMIN_PASSWORD).json()["token"]
    secrets.add("session token of admin", admin_session, allowed_in=frozenset({api.last_label}))
    secrets.add("first password", _FIRST_PASSWORD)
    api.call("POST", "/accounts", token=admin_session, expected=201, json={"name": _NAME, "password": _FIRST_PASSWORD})

    # The account signs in, fails to change its password once, then changes it.
    first_session = api.sign_in(_NAME, _FIRST_PASSWORD).json()["token"]
    secrets.add("first session token", first_session, allowed_in=frozenset({api.last_label}))
    secrets.add("wrong current password", _WRONG_CURRENT_PASSWORD)
    api.call(
        "PUT",
        "/me/password",
        token=first_session,
        expected=403,
        json={"current_password": _WRONG_CURRENT_PASSWORD, "new_password": _CHANGED_PASSWORD},
    )
    secrets.add("changed password", _CHANGED_PASSWORD)
    api.call(
        "PUT",
        "/me/password",
        token=first_session,
        expected=204,
        json={"current_password": _FIRST_PASSWORD, "new_password": _CHANGED_PASSWORD},
    )

    # It creates an API token and uses it on protected routes, a run included.
    token = api.call("POST", "/me/tokens", token=first_session, expected=201, json={"name": "script"}).json()["token"]
    secrets.add("API token", token, allowed_in=frozenset({api.last_label}))
    api.call("GET", "/me", token=token, expected=200)
    api.call("GET", "/me/tokens", token=token, expected=200)
    version = api.call(
        "POST",
        "/configs",
        token=_ADMIN_TOKEN,
        expected=201,
        json={"package": package_data(), "reason": "register for the search of SC-005"},
    ).json()["version"]
    api.call(
        "POST",
        "/runs",
        token=token,
        expected=202,
        json={
            "operation": "plan",
            "config_id": version["config_id"],
            "registry_version": version["registry_version"],
            "reason": "a run with an account token",
        },
    )
    api.call("GET", "/runs", token=token, expected=200)
    api.call("GET", "/events", token=_ADMIN_TOKEN, expected=200)

    # The administrator replaces the password, disables and enables the account.
    secrets.add("password set by the administrator", _ADMIN_SET_PASSWORD)
    api.call(
        "PUT",
        f"/accounts/{_NAME}/password",
        token=admin_session,
        expected=204,
        json={"new_password": _ADMIN_SET_PASSWORD},
    )
    api.call("PATCH", f"/accounts/{_NAME}", token=admin_session, expected=200, json={"disabled": True})
    api.call("PATCH", f"/accounts/{_NAME}", token=admin_session, expected=200, json={"disabled": False})
    api.call("GET", "/accounts", token=admin_session, expected=200)
    api.call("GET", f"/accounts/{_NAME}/tokens", token=admin_session, expected=200)

    # Repeated failed sign-ins refuse the name; the administrator ends the refusal.
    for wrong in _WRONG_PASSWORDS:
        secrets.add("wrong password", wrong)
        api.sign_in(_NAME, wrong, expected=401)
    api.sign_in(_NAME, _ADMIN_SET_PASSWORD, expected=401)
    api.call("POST", f"/accounts/{_NAME}/unlock", token=admin_session, expected=204)

    # The account signs in with the password the administrator set, then signs out.
    second_session = api.sign_in(_NAME, _ADMIN_SET_PASSWORD).json()["token"]
    secrets.add("second session token", second_session, allowed_in=frozenset({api.last_label}))
    api.call("POST", "/auth/sign-out", token=second_session, expected=204)
    api.call("GET", "/me", token=second_session, expected=401)

    # A second account gets a session and a token, then the administrator deletes it.
    secrets.add("password of the deleted account", _DELETED_PASSWORD)
    api.call(
        "POST",
        "/accounts",
        token=admin_session,
        expected=201,
        json={"name": _DELETED_NAME, "password": _DELETED_PASSWORD},
    )
    deleted_session = api.sign_in(_DELETED_NAME, _DELETED_PASSWORD).json()["token"]
    secrets.add("session token of the deleted account", deleted_session, allowed_in=frozenset({api.last_label}))
    deleted_token = api.call("POST", "/me/tokens", token=deleted_session, expected=201, json={"name": "script"}).json()[
        "token"
    ]
    secrets.add("API token of the deleted account", deleted_token, allowed_in=frozenset({api.last_label}))
    for label, text in _store_texts(api.root):
        if label in _HASH_COLUMNS:
            secrets.add_hash(text)
    api.call("DELETE", f"/accounts/{_DELETED_NAME}", token=admin_session, expected=200)
    api.call("GET", "/me", token=deleted_token, expected=401)
    api.call("GET", "/events", token=_ADMIN_TOKEN, expected=200)


def _collect(
    api: _Api, records: Sequence[logging.LogRecord], events: Sequence[Mapping[str, Any]], output: tuple[str, str]
) -> _Corpus:
    corpus = _Corpus()
    for label, response in api.answers:
        corpus.add(label, _answer_text(response))
    for index, record in enumerate(records):
        corpus.add(f"log record {index} {record.name}", f"{record.getMessage()} {record.args!r} {record.__dict__!r}")
    for index, event in enumerate(events):
        corpus.add(f"structlog event {index}", repr(event))
    corpus.add("standard output", output[0])
    corpus.add("standard error", output[1])
    for label, text in _store_texts(api.root):
        corpus.add(label, text)
    for label, text in _file_texts(api.root):
        corpus.add(label, text)
    return corpus


def test_the_flow_of_user_story_1_shows_no_password_token_digest_or_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """SC-005: every answer, log line and stored value of the start-up and the flow is searched."""
    secrets = _Secrets()
    caplog.set_level(logging.DEBUG)
    with capture_logs() as events:
        api = _build_api(tmp_path, monkeypatch)
        _run_the_flow(api, secrets)
        structlog.get_logger("tests.sc005").info("the flow of SC-005 ended", answers=len(api.answers))
    corpus = _collect(api, caplog.records, events, capfd.readouterr())

    # The capture caught the lines of the flow: the client logs each request, and structlog the line above.
    assert any(record.name == "httpx" and "/auth/sign-in" in record.getMessage() for record in caplog.records)
    assert any(event.get("event") == "the flow of SC-005 ended" for event in events)
    # The store holds the hashes and the digests that the search allows, so the exclusions are needed.
    labels = {label for label, _ in corpus.texts}
    assert labels >= _HASH_COLUMNS
    assert labels >= _DIGEST_COLUMNS
    assert ("store account_tokens.digest", _sha256(_ADMIN_TOKEN)) in corpus.texts
    assert any(label.startswith("store audit_events.") for label in labels)
    assert any(label.startswith("store audit_event_resources.") for label in labels)
    assert any(label.startswith("store mutation_receipts.") for label in labels)

    # Each allowed place really holds its secret, so the search reads the texts where secrets are.
    texts = dict(corpus.texts)
    for value, allowed in secrets.allow.items():
        if allowed not in {_DIGEST_COLUMNS, _HASH_COLUMNS}:
            assert any(value in texts[label] for label in allowed)
    assert sum(1 for kind in secrets.values if kind.startswith("password hash")) == 3

    assert _leaks(corpus, secrets) == []


# The negative controls: the same collection and the same search find a secret placed on purpose.


def test_the_search_finds_a_secret_in_a_log_line(
    api: _Api, caplog: pytest.LogCaptureFixture, capfd: pytest.CaptureFixture[str]
) -> None:
    secrets = _Secrets()
    secrets.add("password of admin", _ADMIN_PASSWORD)
    caplog.set_level(logging.DEBUG)
    with capture_logs() as events:
        logging.getLogger("infrahub_sync.service.app").warning("planted %s", _ADMIN_PASSWORD)
        structlog.get_logger("tests.sc005").info("planted", value=_sha256(_ADMIN_PASSWORD))
    corpus = _collect(api, caplog.records, events, capfd.readouterr())

    leaks = _leaks(corpus, secrets)

    assert any(leak.startswith("password of admin") and "log record" in leak for leak in leaks)
    assert any(leak.startswith("digest of password of admin") and "structlog event" in leak for leak in leaks)


def test_the_search_finds_a_token_in_an_answer_other_than_its_creation(
    api: _Api, caplog: pytest.LogCaptureFixture, capfd: pytest.CaptureFixture[str]
) -> None:
    secrets = _Secrets()
    session = api.sign_in("admin", _ADMIN_PASSWORD).json()["token"]
    # The session token is allowed in an answer that did not create it: the one of `GET /me`.
    api.call("GET", "/me", token=session, expected=200)
    secrets.add("session token of admin", session, allowed_in=frozenset({api.last_label}))
    corpus = _collect(api, caplog.records, [], capfd.readouterr())

    assert _leaks(corpus, secrets) == ["session token of admin 0 in answer 0 POST /auth/sign-in"]


def test_the_search_finds_a_password_and_a_hash_in_the_store(
    api: _Api, caplog: pytest.LogCaptureFixture, capfd: pytest.CaptureFixture[str]
) -> None:
    secrets = _Secrets()
    secrets.add("first password", _FIRST_PASSWORD)
    # A password is no token, so a reason may hold it: the store keeps the reason as it was typed.
    api.call(
        "POST",
        "/configs",
        token=_ADMIN_TOKEN,
        expected=201,
        json={"package": package_data(), "reason": f"planted {_FIRST_PASSWORD} scrypt$32768"},
    )
    corpus = _collect(api, caplog.records, [], capfd.readouterr())

    leaks = _leaks(corpus, secrets)

    assert "first password 0 in store audit_events.reason" in leaks
    assert "a scrypt hash in store audit_events.reason" in leaks
