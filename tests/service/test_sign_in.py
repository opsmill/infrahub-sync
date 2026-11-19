"""Sign-in with a name and a password, sign-out, and the refusal after repeated failures.

Most cases are about counting, not hashing: they replace the gate of password checks with one
that compares texts, so no scrypt hash is computed. The cases that need a real check share one
real hash, computed once for the module.
"""

from __future__ import annotations

import functools
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest
from structlog.testing import capture_logs

pytest.importorskip("fastapi")
pytest.importorskip("prefect")

from fastapi.testclient import TestClient

from infrahub_sync.product_store import Account, ProductProjection, local_product_projection
from infrahub_sync.product_store.accounts import (
    SIGN_IN_FAILURE_LIMIT,
    SIGN_IN_FAILURE_WINDOW,
    SIGN_IN_NAME_MAX_LENGTH,
    SIGN_IN_REFUSAL_PERIOD,
)
from infrahub_sync.service import account_routes, credentials
from infrahub_sync.service.account_routes import (
    PASSWORD_MAX_LENGTH,
    SessionResource,
    SignInRequest,
    account_resource,
    clear_failed_sign_ins,
    record_failed_sign_in,
    sign_in_refused_until,
    verify_account_password,
)
from infrahub_sync.service.app import create_app
from infrahub_sync.service.auth import StoreAccountResolver, apply_admin_account
from infrahub_sync.service.service import RunService, ServiceAPIError
from infrahub_sync.service.settings import SESSION_IDLE_SECONDS_ENV, AdminAccountSettings

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from httpx import Response

_PASSWORD = "correct-horse-battery"  # noqa: S105 - a test value, not a secret.
_WRONG_PASSWORD = "wrong-horse-battery"  # noqa: S105 - a test value, not a secret.
_ADMIN_PASSWORD = "first-admin-password"  # noqa: S105 - a test value, not a secret.
_T0 = datetime(2026, 11, 17, 9, 0, tzinfo=timezone.utc)
_SECOND = timedelta(seconds=1)
_IDLE_SECONDS = 600
_NOT_ADMITTED = "the text gate did not check the password"
_REFUSAL = {
    "error": {
        "code": "sign-in-refused",
        "message": "the account name or the password was not accepted",
        "status": 401,
        "run_id": None,
        "mutation_id": None,
    }
}


def _text_hash(password: str) -> str:
    """Return the text that stands for the hash of a password under ``_TextGate``."""
    return f"text-hash-of:{password}"


class _TextGate:
    """A gate that compares texts and records the stored hash of every check, never a password.

    ``while_waiting`` runs where a request waits for a place, before ``admit``; ``while_checking``
    runs after the comparison, while the place is held. A test uses them to make another request
    act at those moments.
    """

    def __init__(self) -> None:
        self.checked: list[str] = []
        self.while_waiting: Callable[[], object] | None = None
        self.while_checking: Callable[[], object] | None = None
        self._lock = threading.Lock()

    def verify(self, password: str, stored_hash: str, *, admit: Callable[[], bool] | None = None) -> bool:
        if self.while_waiting is not None:
            self.while_waiting()
        if admit is not None and not admit():
            raise credentials.VerificationNotAdmittedError(_NOT_ADMITTED)
        with self._lock:
            self.checked.append(stored_hash)
        verified = stored_hash == _text_hash(password)
        if self.while_checking is not None:
            self.while_checking()
        return verified


class _Clock:
    """The clock of the service and of the resolver, which a test moves."""

    def __init__(self) -> None:
        self.now = _T0

    def __call__(self) -> datetime:
        return self.now


@dataclass
class _Api:
    """A Sync API over a SQLite store, with the gate and the clock a test controls."""

    client: TestClient
    store: ProductProjection
    service: RunService
    clock: _Clock
    gate: _TextGate
    accounts: dict[str, Account] = field(default_factory=dict)

    def add(self, name: str, *, password: str | None = _PASSWORD, administrator: bool = False) -> Account:
        """Create an account whose stored hash stands for this password under the text gate."""
        account = self.store.create_account(
            name=name,
            password_hash=None if password is None else _text_hash(password),
            administrator=administrator,
            now=_T0,
        )
        self.accounts[name] = account
        return account

    def token_headers(self, name: str) -> dict[str, str]:
        """Create an API token for the account and return the header that presents it."""
        value = credentials.new_api_token()
        self.store.create_account_token(self.accounts[name].account_id, name="ci", digest=credentials.digest(value))
        return {"Authorization": f"Bearer {value}"}

    def sign_in(self, name: str, password: str = _PASSWORD) -> Response:
        return self.client.post("/auth/sign-in", json={"name": name, "password": password})

    def fail(self, name: str, times: int = 1) -> Response:
        """Sign in with a wrong password, one second apart, and return the last answer."""
        response = None
        for _ in range(times):
            response = self.sign_in(name, _WRONG_PASSWORD)
            self.clock.now += _SECOND
        assert response is not None
        return response

    def rows(self, statement: str) -> list[tuple[Any, ...]]:
        records = cast("Any", self.store._records)
        with records._reading() as cursor:
            cursor.execute(statement)
            return [tuple(row) for row in cursor.fetchall()]


@pytest.fixture(name="api")
def _api_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Api:
    monkeypatch.setenv(SESSION_IDLE_SECONDS_ENV, str(_IDLE_SECONDS))
    gate = _TextGate()
    monkeypatch.setattr(credentials, "PASSWORD_VERIFICATION_GATE", gate)
    store = local_product_projection(tmp_path.resolve())
    clock = _Clock()
    service = RunService(store, cast("Any", object()), clock=clock)
    resolver = StoreAccountResolver(store, idle_seconds=_IDLE_SECONDS, clock=clock)
    api = _Api(client=TestClient(create_app(service, resolver)), store=store, service=service, clock=clock, gate=gate)
    api.add("Alice")
    return api


@pytest.fixture(name="real_gate")
def _real_gate_fixture(api: _Api, monkeypatch: pytest.MonkeyPatch) -> _Api:
    """Give the API a real gate again, for a case that checks a real scrypt hash."""
    monkeypatch.setattr(credentials, "PASSWORD_VERIFICATION_GATE", credentials.VerificationGate())
    return api


@pytest.fixture(scope="module")
def real_hash() -> str:
    """One real hash of ``_PASSWORD`` for the module: a hash costs about 0.1 second."""
    return credentials.hash_password(_PASSWORD)


def _answer(response: Response) -> tuple[int, bytes, dict[str, str]]:
    """Return everything of an answer that a caller can compare: status, body bytes and headers."""
    return response.status_code, response.content, dict(response.headers)


def _session_headers(response: Response) -> dict[str, str]:
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['token']}"}


def test_a_sign_in_returns_a_session_token_that_resolves_to_the_account(real_gate: _Api, real_hash: str) -> None:
    api = real_gate
    account = api.store.create_account(name="Carol", password_hash=real_hash, now=_T0)

    response = api.sign_in("Carol")

    assert response.status_code == 200
    body = response.json()
    assert body["token"].startswith("ifs_ses_")
    assert body["idle_seconds"] == _IDLE_SECONDS
    assert body["account"] == {
        "name": "Carol",
        "administrator": False,
        "disabled": False,
        "environment_managed": False,
        "created_at": "2026-11-17T09:00:00Z",
        "sign_in_refused_until": None,
    }
    resolved = api.store.resolve_digest(credentials.digest(body["token"]), now=api.clock.now)
    assert resolved is not None
    assert (resolved.kind, resolved.account.account_id) == ("session", account.account_id)
    me = api.client.get("/me", headers=_session_headers(response))
    assert me.status_code == 200
    assert me.json()["actor"] == "Carol"


def test_a_wrong_password_is_refused_by_a_real_check(real_gate: _Api, real_hash: str) -> None:
    api = real_gate
    api.store.create_account(name="Carol", password_hash=real_hash, now=_T0)

    response = api.sign_in("Carol", _WRONG_PASSWORD)

    assert (response.status_code, response.json()) == (401, _REFUSAL)


def test_the_admin_account_signs_in_with_the_password_of_the_environment(real_gate: _Api) -> None:
    api = real_gate
    apply_admin_account(api.store, AdminAccountSettings(password=_ADMIN_PASSWORD, token=None))

    response = api.sign_in("admin", _ADMIN_PASSWORD)

    assert response.status_code == 200
    assert response.json()["account"]["environment_managed"] is True
    assert response.json()["account"]["administrator"] is True
    assert api.client.get("/me", headers=_session_headers(response)).json()["actor"] == "admin"


def test_the_answer_of_a_sign_in_holds_no_account_identifier_and_no_hash(api: _Api) -> None:
    response = api.sign_in("Alice")

    assert response.status_code == 200
    text = response.text
    assert "acct_" not in text
    assert api.accounts["Alice"].account_id not in text
    assert _text_hash(_PASSWORD) not in text
    assert "password" not in text
    assert sorted(response.json()) == ["account", "idle_seconds", "token"]


def test_the_store_holds_the_digest_of_the_session_token_and_not_the_token(api: _Api) -> None:
    token = api.sign_in("Alice").json()["token"]

    stored = api.rows("SELECT digest FROM account_sessions")

    assert stored == [(credentials.digest(token),)]


def test_a_name_is_accepted_in_any_letter_case(api: _Api) -> None:
    response = api.sign_in("ALICE")

    assert response.status_code == 200
    assert response.json()["account"]["name"] == "Alice"


def test_every_refused_sign_in_gets_one_identical_answer(api: _Api) -> None:
    api.add("Dave")
    api.store.disable_account(api.accounts["Dave"].account_id)
    api.add("Erin")
    api.fail("Erin", SIGN_IN_FAILURE_LIMIT)
    api.add("Frank", password=None)

    answers = {
        "a wrong name": api.sign_in("nobody"),
        "a wrong password": api.sign_in("Alice", _WRONG_PASSWORD),
        "a disabled account": api.sign_in("Dave"),
        "a refused name": api.sign_in("Erin"),
        "an account with no password": api.sign_in("Frank"),
        "an empty name": api.sign_in(""),
        "an empty password": api.sign_in("Alice", ""),
        "a name over 64 characters": api.sign_in("a" * (SIGN_IN_NAME_MAX_LENGTH + 1)),
        "a password over the maximum": api.sign_in("Alice", "p" * (PASSWORD_MAX_LENGTH + 1)),
    }

    expected = _answer(answers["a wrong password"])
    assert expected[0] == 401
    assert answers["a wrong password"].json() == _REFUSAL
    assert {case: _answer(response) for case, response in answers.items()} == dict.fromkeys(answers, expected)


def test_a_disabled_account_is_refused_with_the_right_password_and_counted(api: _Api) -> None:
    api.store.disable_account(api.accounts["Alice"].account_id)

    response = api.sign_in("Alice")

    assert response.status_code == 401
    stored = api.store.read_sign_in_attempt("alice")
    assert stored is not None
    assert stored.failed_count == 1
    assert api.gate.checked == [_text_hash(_PASSWORD)]


def test_the_sixth_attempt_after_five_failures_is_refused_with_the_right_password(api: _Api) -> None:
    api.fail("Alice", SIGN_IN_FAILURE_LIMIT)
    checks = len(api.gate.checked)

    response = api.sign_in("Alice")

    assert (response.status_code, response.json()) == (401, _REFUSAL)
    assert api.rows("SELECT COUNT(*) FROM account_sessions") == [(0,)]
    # A refused name runs no password check.
    assert len(api.gate.checked) == checks == SIGN_IN_FAILURE_LIMIT


def test_four_failures_do_not_refuse_the_right_password(api: _Api) -> None:
    api.fail("Alice", SIGN_IN_FAILURE_LIMIT - 1)

    assert api.sign_in("Alice").status_code == 200


def test_an_attempt_during_the_refusal_does_not_move_it_and_is_not_counted(api: _Api) -> None:
    api.fail("Alice", SIGN_IN_FAILURE_LIMIT)
    refused = api.store.read_sign_in_attempt("alice")
    assert refused is not None
    assert refused.refused_until == _T0 + (SIGN_IN_FAILURE_LIMIT - 1) * _SECOND + SIGN_IN_REFUSAL_PERIOD

    api.clock.now = _T0 + timedelta(minutes=10)
    assert api.sign_in("Alice", _WRONG_PASSWORD).status_code == 401
    assert api.sign_in("Alice").status_code == 401

    assert api.store.read_sign_in_attempt("alice") == refused


def test_a_name_can_sign_in_again_when_the_refusal_has_passed_and_the_count_starts_again(api: _Api) -> None:
    api.fail("Alice", SIGN_IN_FAILURE_LIMIT)
    refused = api.store.read_sign_in_attempt("alice")
    assert refused is not None
    assert refused.refused_until is not None

    api.clock.now = refused.refused_until
    assert api.sign_in("Alice", _WRONG_PASSWORD).status_code == 401

    restarted = api.store.read_sign_in_attempt("alice")
    assert restarted is not None
    assert (restarted.failed_count, restarted.window_started_at, restarted.refused_until) == (
        1,
        refused.refused_until,
        None,
    )
    api.clock.now += _SECOND
    assert api.sign_in("Alice").status_code == 200


def test_failures_older_than_the_window_are_not_counted(api: _Api) -> None:
    api.fail("Alice", SIGN_IN_FAILURE_LIMIT - 1)
    api.clock.now = _T0 + SIGN_IN_FAILURE_WINDOW + timedelta(minutes=1)

    api.fail("Alice")

    stored = api.store.read_sign_in_attempt("alice")
    assert stored is not None
    assert (stored.failed_count, stored.refused_until) == (1, None)


def test_a_success_clears_the_count(api: _Api) -> None:
    api.fail("Alice", SIGN_IN_FAILURE_LIMIT - 1)
    assert api.store.read_sign_in_attempt("alice") is not None

    assert api.sign_in("Alice").status_code == 200

    assert api.store.read_sign_in_attempt("alice") is None
    assert api.fail("Alice", SIGN_IN_FAILURE_LIMIT - 1).status_code == 401
    assert api.sign_in("Alice").status_code == 200


def test_a_name_with_no_account_is_counted_and_runs_one_check_against_the_fixed_hash(api: _Api) -> None:
    response = api.sign_in("Nobody")

    assert response.status_code == 401
    assert api.gate.checked == [credentials.no_account_hash()]
    stored = api.store.read_sign_in_attempt("nobody")
    assert stored is not None
    assert (stored.name_key, stored.failed_count) == ("nobody", 1)


def test_a_name_with_no_account_is_refused_after_five_failures_without_a_check(api: _Api) -> None:
    api.fail("nobody", SIGN_IN_FAILURE_LIMIT)

    assert api.sign_in("nobody").status_code == 401

    assert api.gate.checked == [credentials.no_account_hash()] * SIGN_IN_FAILURE_LIMIT
    stored = api.store.read_sign_in_attempt("nobody")
    assert stored is not None
    assert stored.failed_count == SIGN_IN_FAILURE_LIMIT


def test_an_account_with_no_password_runs_one_check_against_the_fixed_hash(api: _Api) -> None:
    api.add("Frank", password=None)

    assert api.sign_in("Frank").status_code == 401

    assert api.gate.checked == [credentials.no_account_hash()]


def test_the_fixed_hash_accepts_no_password_for_an_account_with_no_password(monkeypatch: pytest.MonkeyPatch) -> None:
    accepting = SimpleNamespace(verify=lambda _password, _stored_hash, **_options: True)
    monkeypatch.setattr(credentials, "PASSWORD_VERIFICATION_GATE", accepting)

    assert verify_account_password(None, _PASSWORD) is False


@pytest.mark.parametrize(
    "name",
    [
        "a" * (SIGN_IN_NAME_MAX_LENGTH + 1),
        "a" * 5000,
        # 64 characters whose lower-case form, the stored key, has 128.
        "İ" * SIGN_IN_NAME_MAX_LENGTH,
        "",
        # PostgreSQL refuses a NUL character in a text, which would answer 503.
        "al\x00ice",
    ],
    ids=["65-characters", "5000-characters", "longer-in-lower-case", "empty", "nul-character"],
)
def test_a_name_that_no_account_can_have_writes_nothing_and_runs_no_check(api: _Api, name: str) -> None:
    response = api.sign_in(name)

    assert (response.status_code, response.json()) == (401, _REFUSAL)
    assert api.rows("SELECT COUNT(*) FROM sign_in_attempts") == [(0,)]
    assert api.rows("SELECT COUNT(*) FROM audit_events") == [(0,)]
    assert api.gate.checked == []


@pytest.mark.parametrize("password", ["", "p" * (PASSWORD_MAX_LENGTH + 1)], ids=["empty", "over-the-maximum"])
def test_a_password_that_no_account_can_have_writes_nothing_and_runs_no_check(api: _Api, password: str) -> None:
    response = api.sign_in("Alice", password)

    assert (response.status_code, response.json()) == (401, _REFUSAL)
    assert api.rows("SELECT COUNT(*) FROM sign_in_attempts") == [(0,)]
    assert api.rows("SELECT COUNT(*) FROM audit_events") == [(0,)]
    assert api.gate.checked == []


def test_a_name_of_64_characters_is_counted(api: _Api) -> None:
    name = "a" * SIGN_IN_NAME_MAX_LENGTH

    assert api.sign_in(name).status_code == 401

    assert api.rows("SELECT name_key, failed_count FROM sign_in_attempts") == [(name, 1)]


def test_the_table_of_failed_sign_ins_never_exceeds_its_bound(api: _Api, monkeypatch: pytest.MonkeyPatch) -> None:
    bound = 3
    monkeypatch.setattr(
        api.store, "record_sign_in_failure", functools.partial(api.store.record_sign_in_failure, bound=bound)
    )

    for index in range(bound * 3):
        api.fail(f"nobody-{index}")
        assert api.rows("SELECT COUNT(*) FROM sign_in_attempts")[0][0] <= bound

    assert api.rows("SELECT name_key FROM sign_in_attempts ORDER BY name_key") == [
        ("nobody-6",),
        ("nobody-7",),
        ("nobody-8",),
    ]


def test_a_sign_in_that_waits_too_long_for_a_check_answers_503_and_is_not_counted(
    api: _Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    gate = credentials.VerificationGate(limit=1, wait_seconds=0.01)
    monkeypatch.setattr(credentials, "PASSWORD_VERIFICATION_GATE", gate)

    with gate.place():
        response = api.sign_in("Alice")

    assert response.status_code == 503
    assert response.json()["error"] == {
        "code": "sign-in-busy",
        "message": "too many sign-ins are in progress; try again in a few seconds",
        "status": 503,
        "run_id": None,
        "mutation_id": None,
    }
    assert api.store.read_sign_in_attempt("alice") is None
    assert api.rows("SELECT COUNT(*) FROM account_sessions") == [(0,)]
    assert api.rows("SELECT COUNT(*) FROM audit_events") == [(0,)]


def test_the_store_unreachable_during_a_sign_in_answers_503_service_unavailable(
    api: _Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unreachable(_name: str) -> None:
        msg = "unable to open database file"
        raise sqlite3.OperationalError(msg)

    monkeypatch.setattr(api.store, "read_sign_in_attempt", unreachable)

    response = api.sign_in("Alice")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "service-unavailable"
    assert "database" not in response.text


def test_two_sign_ins_that_fail_at_the_same_moment_leave_one_row_with_a_count_of_two(api: _Api) -> None:
    start = threading.Barrier(2)
    statuses: list[int] = []
    errors: list[BaseException] = []

    def fail() -> None:
        try:
            start.wait(timeout=10)
            statuses.append(api.sign_in("Alice", _WRONG_PASSWORD).status_code)
        except BaseException as error:  # noqa: BLE001 - reported by the assertion below.
            errors.append(error)

    threads = [threading.Thread(target=fail) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert [type(error).__name__ for error in errors] == []
    assert statuses == [401, 401]
    assert api.rows("SELECT name_key, failed_count FROM sign_in_attempts") == [("alice", 2)]


def test_a_sign_out_ends_the_session_and_a_second_sign_out_is_unauthenticated(api: _Api) -> None:
    headers = _session_headers(api.sign_in("Alice"))
    assert api.client.get("/me", headers=headers).status_code == 200

    first = api.client.post("/auth/sign-out", headers=headers)

    assert (first.status_code, first.content) == (204, b"")
    assert api.rows("SELECT COUNT(*) FROM account_sessions") == [(0,)]
    assert api.client.get("/me", headers=headers).status_code == 401
    second = api.client.post("/auth/sign-out", headers=headers)
    assert second.status_code == 401
    assert second.json()["error"]["code"] == "unauthenticated"


def test_a_sign_out_ends_one_session_and_keeps_the_others(api: _Api) -> None:
    first = _session_headers(api.sign_in("Alice"))
    second = _session_headers(api.sign_in("Alice"))

    assert api.client.post("/auth/sign-out", headers=first).status_code == 204

    assert api.client.get("/me", headers=first).status_code == 401
    assert api.client.get("/me", headers=second).status_code == 200


def test_a_sign_out_with_an_api_token_answers_422_and_keeps_the_token(api: _Api) -> None:
    headers = api.token_headers("Alice")

    response = api.client.post("/auth/sign-out", headers=headers)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "not-a-session"
    assert api.client.get("/me", headers=headers).status_code == 200
    assert api.rows("SELECT COUNT(*) FROM audit_events") == [(0,)]


def test_a_sign_out_with_no_value_is_unauthenticated(api: _Api) -> None:
    response = api.client.post("/auth/sign-out")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


def test_a_request_with_an_api_token_is_not_affected_by_the_refusal_of_its_name(api: _Api) -> None:
    headers = api.token_headers("Alice")
    api.fail("Alice", SIGN_IN_FAILURE_LIMIT)

    assert api.client.get("/me", headers=headers).status_code == 200


def test_a_sign_in_and_a_sign_out_each_record_one_event_with_the_account_identifier(api: _Api) -> None:
    account_id = api.accounts["Alice"].account_id
    api.add("Root", administrator=True)

    headers = _session_headers(api.sign_in("Alice"))
    api.clock.now += _SECOND
    assert api.client.post("/auth/sign-out", headers=headers).status_code == 204

    stored = api.rows("SELECT event_type, actor, operation, outcome, run_id FROM audit_events ORDER BY created_at")
    assert stored == [
        ("infrahub_sync.account.signed_in", account_id, "sign-in", "accepted", None),
        ("infrahub_sync.account.signed_out", account_id, "sign-out", "accepted", None),
    ]
    assert api.rows("SELECT DISTINCT kind, resource_id FROM audit_event_resources") == [("account", account_id)]
    events = api.client.get("/events", headers=api.token_headers("Root"))
    assert events.status_code == 200
    assert [(item["event_type"], item["actor"], item["related"]) for item in events.json()["items"]] == [
        ("infrahub_sync.account.signed_in", "Alice", [{"kind": "account", "id": "Alice"}]),
        ("infrahub_sync.account.signed_out", "Alice", [{"kind": "account", "id": "Alice"}]),
    ]
    assert "acct_" not in events.text


def test_a_refused_sign_in_records_a_refusal_with_no_name_and_no_cause(api: _Api) -> None:
    api.add("Dave")
    api.store.disable_account(api.accounts["Dave"].account_id)

    api.sign_in("Zed-with-no-account", _WRONG_PASSWORD)
    api.sign_in("Alice", _WRONG_PASSWORD)
    api.sign_in("Dave")

    stored = api.rows("SELECT event_type, actor, operation, reason, outcome FROM audit_events")
    assert stored == [(None, "unauthenticated", "/auth/sign-in", "sign-in-refused", "refused-authentication")] * 3
    # As for a refused token, the one resource of the record is the caller with no account.
    assert api.rows("SELECT DISTINCT kind, resource_id FROM audit_event_resources") == [("account", "unauthenticated")]


def test_no_record_of_a_sign_in_holds_a_password_a_token_or_a_digest(api: _Api) -> None:
    api.sign_in("Zed-with-no-account", _WRONG_PASSWORD)
    api.fail("Alice")
    token = api.sign_in("Alice").json()["token"]
    api.client.post("/auth/sign-out", headers={"Authorization": f"Bearer {token}"})

    recorded = repr(api.rows("SELECT * FROM audit_events") + api.rows("SELECT * FROM audit_event_resources"))
    recorded += repr(api.rows("SELECT * FROM sign_in_attempts"))

    for secret in (_PASSWORD, _WRONG_PASSWORD, token, credentials.digest(token), _text_hash(_PASSWORD)):
        assert secret not in recorded
    assert "zed-with-no-account" not in repr(api.rows("SELECT * FROM audit_events")).lower()


def test_a_request_and_a_session_show_no_secret_when_printed() -> None:
    request = SignInRequest(name="Alice", password=_PASSWORD)
    session = SessionResource(
        token="ifs_ses_value",  # noqa: S106 - a test value, not a secret.
        account=account_resource(
            Account(
                account_id="acct_" + "0" * 32,
                short_id="0badc0de",
                name="Alice",
                name_key="alice",
                password_hash=_text_hash(_PASSWORD),
                created_at=_T0,
                updated_at=_T0,
                password_changed_at=_T0,
            )
        ),
        idle_seconds=_IDLE_SECONDS,
    )

    printed = f"{request!r} {request} {session!r} {session}"

    assert _PASSWORD not in printed
    assert "ifs_ses_value" not in printed
    assert "acct_" not in printed


@pytest.mark.parametrize(
    "body",
    [
        {"name": "Alice"},
        {"password": _PASSWORD},
        {"name": "Alice", "password": 12},
        {"name": ["Alice"], "password": "p"},
    ],
    ids=["no-password", "no-name", "password-not-text", "name-not-text"],
)
def test_a_body_that_is_not_a_name_and_a_password_answers_422_without_its_values(
    api: _Api, body: dict[str, Any]
) -> None:
    response = api.client.post("/auth/sign-in", json=body)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "request-invalid"
    assert _PASSWORD not in response.text
    assert api.gate.checked == []


def test_the_refusal_functions_are_usable_for_a_name_without_a_request(api: _Api) -> None:
    for _ in range(SIGN_IN_FAILURE_LIMIT):
        assert sign_in_refused_until(api.service, "Alice") is None
        record_failed_sign_in(api.service, "Alice")

    assert sign_in_refused_until(api.service, "ALICE") == _T0 + SIGN_IN_REFUSAL_PERIOD
    api.clock.now = _T0 + SIGN_IN_REFUSAL_PERIOD
    assert sign_in_refused_until(api.service, "Alice") is None
    api.clock.now = _T0
    assert clear_failed_sign_ins(api.service, "Alice") is True
    assert sign_in_refused_until(api.service, "Alice") is None
    assert clear_failed_sign_ins(api.service, "Alice") is False


def test_a_busy_gate_is_a_service_error_that_names_no_account(monkeypatch: pytest.MonkeyPatch) -> None:
    gate = credentials.VerificationGate(limit=1, wait_seconds=0.01)
    monkeypatch.setattr(credentials, "PASSWORD_VERIFICATION_GATE", gate)

    with gate.place(), pytest.raises(ServiceAPIError) as refused:
        verify_account_password(None, _PASSWORD)

    assert (refused.value.status, refused.value.code) == (503, "sign-in-busy")
    assert refused.value.__cause__ is None


def test_the_api_document_declares_the_two_routes_with_their_answers(api: _Api) -> None:
    paths = api.client.get("/openapi.json").json()["paths"]

    sign_in = paths["/auth/sign-in"]["post"]
    sign_out = paths["/auth/sign-out"]["post"]
    assert sign_in["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/SessionResource"
    }
    assert {"401", "503"} <= set(sign_in["responses"])
    assert "security" not in sign_in
    assert {"204", "401", "422"} <= set(sign_out["responses"])
    assert sign_out["security"] == [{"BearerAuth": []}]
    assert account_routes.account_router.__doc__ is not None


def _refuse_by_failures(api: _Api, name: str) -> Callable[[], None]:
    """Return an action that records enough failures for this name to refuse it, once."""
    done: list[bool] = []

    def refuse() -> None:
        if not done:
            done.append(True)
            for _ in range(SIGN_IN_FAILURE_LIMIT):
                record_failed_sign_in(api.service, name)

    return refuse


def test_a_sign_in_whose_name_is_refused_while_it_waits_gets_the_refusal_with_no_check(api: _Api) -> None:
    # Other requests fail 5 times for the name while this one waits for a place of the gate.
    api.gate.while_waiting = _refuse_by_failures(api, "Alice")

    response = api.sign_in("Alice")

    assert (response.status_code, response.json()) == (401, _REFUSAL)
    assert api.gate.checked == []
    assert api.rows("SELECT COUNT(*) FROM account_sessions") == [(0,)]
    assert api.rows("SELECT failed_count FROM sign_in_attempts") == [(SIGN_IN_FAILURE_LIMIT,)]
    refusal = api.store.read_sign_in_attempt("Alice")
    assert refusal is not None
    assert refusal.refused_until == _T0 + SIGN_IN_REFUSAL_PERIOD


def test_a_sign_in_whose_name_is_refused_while_it_waits_answers_as_a_refused_name(api: _Api) -> None:
    for _ in range(SIGN_IN_FAILURE_LIMIT):
        record_failed_sign_in(api.service, "Alice")
    refused_before = api.sign_in("Alice")
    clear_failed_sign_ins(api.service, "Alice")
    api.gate.while_waiting = _refuse_by_failures(api, "Alice")

    refused_while_waiting = api.sign_in("Alice")

    assert _answer(refused_while_waiting) == _answer(refused_before)


def test_a_burst_of_parallel_sign_ins_for_one_name_runs_no_more_checks_than_the_limit(api: _Api) -> None:
    requests = SIGN_IN_FAILURE_LIMIT + 5
    barrier = threading.Barrier(requests)
    answers: list[int] = []

    def sign_in() -> None:
        barrier.wait()
        answers.append(api.sign_in("Alice", _WRONG_PASSWORD).status_code)

    threads = [threading.Thread(target=sign_in) for _ in range(requests)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert answers == [401] * requests
    assert len(api.gate.checked) == SIGN_IN_FAILURE_LIMIT
    assert api.rows("SELECT failed_count FROM sign_in_attempts") == [(SIGN_IN_FAILURE_LIMIT,)]


def test_a_sign_in_during_which_the_account_is_disabled_and_enabled_again_opens_no_session(api: _Api) -> None:
    alice = api.accounts["Alice"]

    def disable() -> None:
        # An administrator disables the account while its password is checked.
        api.store.disable_account(alice.account_id, now=_T0)

    api.gate.while_checking = disable

    response = api.sign_in("Alice")
    api.store.enable_account(alice.account_id, now=_T0)

    assert (response.status_code, response.json()) == (401, _REFUSAL)
    assert api.rows("SELECT COUNT(*) FROM account_sessions") == [(0,)]
    assert api.rows("SELECT COUNT(*) FROM sign_in_attempts") == [(0,)]
    assert [event.event_type for event in api.store.audit_events() if event.outcome == "accepted"] == []


@pytest.mark.parametrize(
    "body",
    [
        '{"name": "Alice", "password": "correct-horse-\\ud800-battery"}',
        '{"name": "Al\\ud800ice", "password": "correct-horse-battery"}',
    ],
    ids=("password", "name"),
)
def test_a_value_that_utf_8_cannot_encode_is_refused_with_no_check_and_no_count(api: _Api, body: str) -> None:
    response = api.client.post("/auth/sign-in", content=body.encode(), headers={"Content-Type": "application/json"})

    assert (response.status_code, response.json()) == (401, _REFUSAL)
    assert api.gate.checked == []
    assert api.rows("SELECT COUNT(*) FROM sign_in_attempts") == [(0,)]


def test_a_stored_hash_that_cannot_be_read_is_a_wrong_password_and_is_logged_without_the_hash(
    real_gate: _Api,
) -> None:
    api = real_gate
    damaged = "scrypt$32768$8$3$not-base64!$a2V5"
    account = api.store.create_account(name="Dora", password_hash=damaged, now=_T0)
    wrong = api.sign_in("Dora", _WRONG_PASSWORD)

    with capture_logs() as logs:
        response = api.sign_in("Dora")

    assert _answer(response)[:2] == _answer(wrong)[:2]
    assert (response.status_code, response.json()) == (401, _REFUSAL)
    attempt = api.store.read_sign_in_attempt("Dora")
    assert attempt is not None
    assert attempt.failed_count == 2
    unreadable = [log for log in logs if log["event"] == "account_password_hash_unreadable"]
    assert unreadable == [
        {"event": "account_password_hash_unreadable", "account_id": account.account_id, "log_level": "warning"}
    ]
    assert damaged not in repr(logs)
    assert _PASSWORD not in repr(logs)


def test_a_readable_hash_and_a_wrong_password_log_nothing(real_gate: _Api, real_hash: str) -> None:
    api = real_gate
    api.store.create_account(name="Carol", password_hash=real_hash, now=_T0)

    with capture_logs() as logs:
        assert api.sign_in("Carol", _WRONG_PASSWORD).status_code == 401

    assert [log for log in logs if log["event"] == "account_password_hash_unreadable"] == []
