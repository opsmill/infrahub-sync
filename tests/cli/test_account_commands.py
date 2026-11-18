"""The `tokens` and `accounts` command groups, and the rule that no command takes a password as a value."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import httpx
import pytest
import typer
from typer.main import get_command
from typer.testing import CliRunner

from infrahub_sync.cli import app
from infrahub_sync.cli_accounts import ACCOUNT_ERROR_MESSAGES, parse_expiry
from infrahub_sync.cli_session import SavedSession, read_session, write_session

if TYPE_CHECKING:
    from collections.abc import Iterator

    from typer.testing import Result

    from tests.cli.conftest import FakeSyncAPI

URL = "http://127.0.0.1:8001"
SESSION_TOKEN = "ifs_session_canary"  # noqa: S105 - a canary that must never be printed.
TOKEN_VALUE = "ifs_token_canary"  # noqa: S105 - the value `tokens create` prints once.
TOKEN_ID = "0b5c4e0e-9a51-4f0b-8f0e-3f1f6f6f0a11"  # noqa: S105 - an identifier, not a value.
PASSWORD = "correct horse battery staple"  # noqa: S105 - a canary that must never be printed.
NOW = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)
RUNNER = CliRunner()
NO_CONTENT = httpx.Response(204)
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
NOT_SHOWN_AGAIN = "note: the token value is not shown again; store it now"


def _account(name: str = "alice", **extra: object) -> dict[str, object]:
    return {
        "name": name,
        "administrator": False,
        "disabled": False,
        "environment_managed": False,
        "created_at": NOW.isoformat(),
        "sign_in_refused_until": None,
        **extra,
    }


def _token(**extra: object) -> dict[str, object]:
    return {
        "token_id": TOKEN_ID,
        "name": "ci",
        "created_at": NOW.isoformat(),
        "expires_at": None,
        "last_used_at": None,
        **extra,
    }


@pytest.fixture(autouse=True)
def _signed_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INFRAHUB_SYNC_API_URL", URL)
    monkeypatch.delenv("INFRAHUB_SYNC_API_TOKEN", raising=False)
    write_session(URL, SavedSession(account="alice", token=SESSION_TOKEN))


@pytest.fixture
def terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let a command ask its questions: the test runner's standard input is not a terminal."""
    monkeypatch.setattr("infrahub_sync.cli._stdin_is_interactive", lambda: True)


def _invoke(*args: str, stdin: str | None = None) -> Result:
    return RUNNER.invoke(app, list(args), input=stdin)


def _assert_no_secret(result: Result) -> None:
    for secret in (SESSION_TOKEN, PASSWORD):
        assert secret not in result.output
        assert secret not in result.stderr


# --- tokens --------------------------------------------------------------------------------


def test_tokens_list_prints_one_row_for_each_token_without_any_value(sync_api: FakeSyncAPI) -> None:
    later = NOW + timedelta(days=30)
    sync_api.answers["GET /me/tokens"] = httpx.Response(
        200,
        json=[
            _token(),
            _token(
                token_id="t-2",  # noqa: S106 - an identifier, not a value.
                name="laptop",
                expires_at=later.isoformat(),
                last_used_at=NOW.isoformat(),
            ),
        ],
    )

    result = _invoke("tokens", "list")

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        f"ci\t{TOKEN_ID}\t{NOW.isoformat()}\t-\t-",
        f"laptop\tt-2\t{NOW.isoformat()}\t{later.isoformat()}\t{NOW.isoformat()}",
    ]
    assert sync_api.bearers == [f"Bearer {SESSION_TOKEN}"]
    _assert_no_secret(result)


def test_tokens_list_prints_json_with_the_option(sync_api: FakeSyncAPI) -> None:
    sync_api.answers["GET /me/tokens"] = httpx.Response(200, json=[_token()])

    result = _invoke("tokens", "list", "--json")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == [
        {
            "token_id": TOKEN_ID,
            "name": "ci",
            "created_at": "2026-11-18T12:00:00Z",
            "expires_at": None,
            "last_used_at": None,
        }
    ]


def test_tokens_list_makes_a_control_character_in_a_name_visible(sync_api: FakeSyncAPI) -> None:
    sync_api.answers["GET /me/tokens"] = httpx.Response(200, json=[_token(name="ci\x1b[2Jx")])

    result = _invoke("tokens", "list")

    assert result.exit_code == 0, result.output
    assert "\x1b" not in result.stdout
    assert "ci\\x1b[2Jx" in result.stdout


def test_tokens_create_prints_the_value_once_on_standard_output_only(sync_api: FakeSyncAPI) -> None:
    sync_api.answers["POST /me/tokens"] = httpx.Response(201, json=_token(token=TOKEN_VALUE))

    result = _invoke("tokens", "create", "ci", "--reason", "pipeline")

    assert result.exit_code == 0, result.output
    # `TOKEN=$(infrahub-sync tokens create ci)` captures the value and nothing else.
    assert result.stdout == f"{TOKEN_VALUE}\n"
    assert TOKEN_VALUE not in result.stderr
    assert result.output.count(TOKEN_VALUE) == 1
    assert NOT_SHOWN_AGAIN in result.stderr
    assert f"token_id: {TOKEN_ID}" in result.stderr
    assert sync_api.routes == ["POST /me/tokens"]
    assert sync_api.body() == {"name": "ci", "reason": "pipeline"}
    _assert_no_secret(result)


def test_tokens_create_prints_the_value_in_the_json_document(sync_api: FakeSyncAPI) -> None:
    expiry = NOW + timedelta(days=30)
    sync_api.answers["POST /me/tokens"] = httpx.Response(
        201, json=_token(token=TOKEN_VALUE, expires_at=expiry.isoformat())
    )

    result = _invoke("tokens", "create", "ci", "--json")

    assert result.exit_code == 0, result.output
    document = json.loads(result.stdout)
    assert document["token"] == TOKEN_VALUE
    assert document["token_id"] == TOKEN_ID
    assert document["expires_at"] == "2026-12-18T12:00:00Z"
    assert result.stdout.count(TOKEN_VALUE) == 1
    assert TOKEN_VALUE not in result.stderr
    assert NOT_SHOWN_AGAIN in result.stderr


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2027-01-31", datetime(2027, 1, 31, 23, 59, 59, tzinfo=timezone.utc)),
        (" 2027-01-31 ", datetime(2027, 1, 31, 23, 59, 59, tzinfo=timezone.utc)),
        ("2027-01-31T12:00:00+00:00", datetime(2027, 1, 31, 12, tzinfo=timezone.utc)),
        ("2027-01-31T12:00:00Z", datetime(2027, 1, 31, 12, tzinfo=timezone.utc)),
        ("2027-01-31T12:00:00z", datetime(2027, 1, 31, 12, tzinfo=timezone.utc)),
        ("2027-01-31 12:00:00+00:00", datetime(2027, 1, 31, 12, tzinfo=timezone.utc)),
        ("2027-01-31T12:00:00+02:00", datetime(2027, 1, 31, 12, tzinfo=timezone(timedelta(hours=2)))),
        (
            "2027-01-31T12:00:00.250000-05:00",
            datetime(2027, 1, 31, 12, 0, 0, 250000, tzinfo=timezone(-timedelta(hours=5))),
        ),
    ],
)
def test_expires_accepts_a_date_or_a_date_time_with_a_time_zone(value: str, expected: datetime) -> None:
    parsed = parse_expiry(value)

    assert parsed == expected
    assert parsed.utcoffset() == expected.utcoffset()


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ("2027-01-31T12:00:00", "needs a time zone"),
        ("2027-01-31 12:00", "needs a time zone"),
        ("tomorrow", "ISO 8601"),
        ("", "ISO 8601"),
        ("2027-13-01", "ISO 8601"),
        ("2027-02-30", "ISO 8601"),
        ("31/01/2027", "ISO 8601"),
        ("Z", "ISO 8601"),
    ],
)
def test_expires_refuses_a_value_without_a_time_zone_or_that_is_not_a_date(value: str, reason: str) -> None:
    with pytest.raises(typer.BadParameter, match=reason):
        parse_expiry(value)


def test_tokens_create_sends_the_end_of_the_day_in_utc_for_a_date(sync_api: FakeSyncAPI) -> None:
    sync_api.answers["POST /me/tokens"] = httpx.Response(201, json=_token(token=TOKEN_VALUE))

    result = _invoke("tokens", "create", "ci", "--expires", "2027-01-31")

    assert result.exit_code == 0, result.output
    assert sync_api.body() == {"name": "ci", "expires_at": "2027-01-31T23:59:59+00:00"}


def test_tokens_create_refuses_an_expiry_without_a_time_zone_before_any_request(sync_api: FakeSyncAPI) -> None:
    result = _invoke("tokens", "create", "ci", "--expires", "2027-01-31T12:00:00")

    assert result.exit_code == 2
    assert "--expires" in result.stderr
    assert "time zone" in _ANSI.sub("", result.stderr)
    assert not result.stdout
    assert sync_api.requests == []


@pytest.mark.parametrize(
    ("status", "code"),
    [(409, "token-name-taken"), (422, "expiry-in-the-past"), (409, "environment-managed-account")],
)
def test_a_refused_token_creation_prints_the_fixed_message_and_no_value(
    sync_api: FakeSyncAPI, status: int, code: str
) -> None:
    sync_api.answers["POST /me/tokens"] = sync_api.error(status, code)

    result = _invoke("tokens", "create", "ci")

    assert result.exit_code == 1
    assert not result.stdout
    assert f"code: {code}" in result.stderr
    assert f"message: {ACCOUNT_ERROR_MESSAGES[code]}" in result.stderr
    assert NOT_SHOWN_AGAIN not in result.stderr
    assert "text of the Sync API" not in result.stderr


def test_the_message_of_the_admin_account_names_both_variables() -> None:
    message = ACCOUNT_ERROR_MESSAGES["environment-managed-account"]

    assert "INFRAHUB_SYNC_ADMIN_ACCOUNT_PASSWORD" in message
    assert "INFRAHUB_SYNC_ADMIN_ACCOUNT_TOKEN" in message


def test_tokens_delete_sends_the_identifier_and_the_reason(sync_api: FakeSyncAPI) -> None:
    sync_api.answers[f"DELETE /me/tokens/{TOKEN_ID}"] = NO_CONTENT

    result = _invoke("tokens", "delete", TOKEN_ID, "--reason", "leaked")

    assert result.exit_code == 0, result.output
    assert result.stdout == f"token {TOKEN_ID} deleted\n"
    assert sync_api.routes == [f"DELETE /me/tokens/{TOKEN_ID}"]
    assert dict(sync_api.requests[0].url.params) == {"reason": "leaked"}


def test_tokens_delete_of_an_unknown_token_prints_the_fixed_message(sync_api: FakeSyncAPI) -> None:
    sync_api.answers[f"DELETE /me/tokens/{TOKEN_ID}"] = sync_api.error(404, "token-not-found")

    result = _invoke("tokens", "delete", TOKEN_ID)

    assert result.exit_code == 1
    assert "status: 404" in result.stderr
    assert "message: the account has no token with this identifier" in result.stderr


def test_tokens_delete_refuses_an_identifier_that_cannot_be_one(sync_api: FakeSyncAPI) -> None:
    result = _invoke("tokens", "delete", "../accounts")

    assert result.exit_code == 2
    assert "argument: token_id" in result.stderr
    assert sync_api.requests == []


# --- accounts ------------------------------------------------------------------------------


def test_accounts_list_prints_name_level_state_and_refusal_time(sync_api: FakeSyncAPI) -> None:
    until = NOW + timedelta(minutes=15)
    sync_api.answers["GET /accounts"] = httpx.Response(
        200,
        json=[
            _account("admin", administrator=True, environment_managed=True),
            _account("alice", sign_in_refused_until=until.isoformat()),
            _account("bob", disabled=True),
        ],
    )

    result = _invoke("accounts", "list")

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        "admin\tadministrator\tactive\t-",
        f"alice\tnot-administrator\tactive\t{until.isoformat()}",
        "bob\tnot-administrator\tdisabled\t-",
    ]
    assert dict(sync_api.requests[0].url.params) == {"state": "all"}
    _assert_no_secret(result)


@pytest.mark.parametrize("state", ["active", "disabled", "all"])
def test_accounts_list_sends_the_state(sync_api: FakeSyncAPI, state: str) -> None:
    sync_api.answers["GET /accounts"] = httpx.Response(200, json=[])

    result = _invoke("accounts", "list", "--state", state)

    assert result.exit_code == 0, result.output
    assert not result.stdout
    assert dict(sync_api.requests[0].url.params) == {"state": state}


def test_accounts_list_refuses_an_unknown_state(sync_api: FakeSyncAPI) -> None:
    result = _invoke("accounts", "list", "--state", "deleted")

    assert result.exit_code == 2
    assert sync_api.requests == []


def test_accounts_list_prints_json_with_the_option(sync_api: FakeSyncAPI) -> None:
    sync_api.answers["GET /accounts"] = httpx.Response(200, json=[_account()])

    result = _invoke("accounts", "list", "--json")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == [{**_account(), "created_at": "2026-11-18T12:00:00Z"}]


def test_accounts_list_by_a_caller_that_is_not_an_administrator_exits_1(sync_api: FakeSyncAPI) -> None:
    sync_api.answers["GET /accounts"] = sync_api.error(403, "forbidden")

    result = _invoke("accounts", "list")

    assert result.exit_code == 1
    assert not result.stdout
    assert "status: 403" in result.stderr
    assert "code: forbidden" in result.stderr
    assert read_session(URL) is not None


@pytest.mark.usefixtures("terminal")
def test_accounts_create_asks_for_the_password_twice_and_prints_the_account(sync_api: FakeSyncAPI) -> None:
    sync_api.answers["POST /accounts"] = httpx.Response(201, json=_account("carol", administrator=True))

    result = _invoke(
        "accounts", "create", "carol", "--administrator", "--reason", "new colleague", stdin=f"{PASSWORD}\n{PASSWORD}\n"
    )

    assert result.exit_code == 0, result.output
    assert sync_api.routes == ["POST /accounts"]
    assert sync_api.body() == {
        "name": "carol",
        "password": PASSWORD,
        "administrator": True,
        "reason": "new colleague",
    }
    assert result.stdout.endswith(
        "account: carol\nadministrator: true\ndisabled: false\nenvironment_managed: false\n"
        f"created_at: {NOW.isoformat()}\nsign_in_refused_until: -\n"
    )
    assert result.stderr.count("Initial password") == 2
    _assert_no_secret(result)


@pytest.mark.usefixtures("terminal")
@pytest.mark.parametrize("command", [("accounts", "create", "carol"), ("accounts", "set-password", "carol")])
def test_two_different_passwords_send_no_request(sync_api: FakeSyncAPI, command: tuple[str, ...]) -> None:
    result = _invoke(*command, stdin=f"{PASSWORD}\n{PASSWORD}x\n")

    assert result.exit_code == 2
    assert "error: the two passwords do not match" in result.stderr
    assert sync_api.requests == []
    _assert_no_secret(result)


def test_accounts_create_reads_the_password_from_standard_input(sync_api: FakeSyncAPI) -> None:
    sync_api.answers["POST /accounts"] = httpx.Response(201, json=_account("carol"))

    result = _invoke("accounts", "create", "carol", "--password-stdin", "--json", stdin=f"{PASSWORD}\n")

    assert result.exit_code == 0, result.output
    assert sync_api.body() == {"name": "carol", "password": PASSWORD, "administrator": False}
    assert json.loads(result.stdout)["name"] == "carol"
    assert not result.stderr
    _assert_no_secret(result)


@pytest.mark.parametrize(
    "command",
    [("accounts", "create", "carol"), ("accounts", "set-password", "carol")],
)
def test_a_password_command_without_a_terminal_names_password_stdin(
    sync_api: FakeSyncAPI, command: tuple[str, ...]
) -> None:
    result = _invoke(*command, stdin=f"{PASSWORD}\n{PASSWORD}\n")

    assert result.exit_code == 2
    assert "--password-stdin" in result.stderr
    assert sync_api.requests == []
    _assert_no_secret(result)


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (409, "account-name-taken"),
        (422, "account-name-invalid"),
        (422, "password-too-short"),
        (503, "sign-in-busy"),
    ],
)
def test_a_refused_account_creation_prints_the_fixed_message(sync_api: FakeSyncAPI, status: int, code: str) -> None:
    sync_api.answers["POST /accounts"] = sync_api.error(status, code, f"server text with {PASSWORD}")

    result = _invoke("accounts", "create", "carol", "--password-stdin", stdin=f"{PASSWORD}\n")

    assert result.exit_code == 1
    assert not result.stdout
    assert f"status: {status}" in result.stderr
    assert f"message: {ACCOUNT_ERROR_MESSAGES[code]}" in result.stderr
    assert "server text" not in result.stderr
    _assert_no_secret(result)


def test_accounts_set_password_sends_the_new_password_in_the_body(sync_api: FakeSyncAPI) -> None:
    sync_api.answers["PUT /accounts/carol/password"] = NO_CONTENT

    result = _invoke(
        "accounts", "set-password", "carol", "--password-stdin", "--reason", "forgotten", stdin=f"{PASSWORD}\n"
    )

    assert result.exit_code == 0, result.output
    assert result.stdout == "password of account carol changed\n"
    assert sync_api.routes == ["PUT /accounts/carol/password"]
    assert sync_api.body() == {"new_password": PASSWORD, "reason": "forgotten"}
    assert PASSWORD not in str(sync_api.requests[0].url)
    _assert_no_secret(result)


def test_accounts_set_password_of_admin_names_the_variable(sync_api: FakeSyncAPI) -> None:
    sync_api.answers["PUT /accounts/admin/password"] = sync_api.error(409, "environment-managed-account")

    result = _invoke("accounts", "set-password", "admin", "--password-stdin", stdin=f"{PASSWORD}\n")

    assert result.exit_code == 1
    assert "INFRAHUB_SYNC_ADMIN_ACCOUNT_PASSWORD" in result.stderr
    _assert_no_secret(result)


def test_accounts_unlock_sends_the_reason_when_given(sync_api: FakeSyncAPI) -> None:
    sync_api.answers["POST /accounts/carol/unlock"] = NO_CONTENT

    first = _invoke("accounts", "unlock", "carol")
    second = _invoke("accounts", "unlock", "carol", "--reason", "confirmed by phone")

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert first.stdout == "account carol unlocked\n"
    assert sync_api.requests[0].content == b""
    assert sync_api.body(1) == {"reason": "confirmed by phone"}


@pytest.mark.parametrize(("command", "disabled"), [("disable", True), ("enable", False)])
def test_accounts_disable_and_enable_send_the_state_and_print_the_account(
    sync_api: FakeSyncAPI, command: str, *, disabled: bool
) -> None:
    sync_api.answers["PATCH /accounts/carol"] = httpx.Response(200, json=_account("carol", disabled=disabled))

    result = _invoke("accounts", command, "carol", "--reason", "leave")

    assert result.exit_code == 0, result.output
    assert sync_api.routes == ["PATCH /accounts/carol"]
    assert sync_api.body() == {"disabled": disabled, "reason": "leave"}
    assert f"account: carol\nadministrator: false\ndisabled: {str(disabled).lower()}\n" in result.stdout


@pytest.mark.parametrize(
    "command",
    [
        ("accounts", "unlock", "ghost"),
        ("accounts", "disable", "ghost"),
        ("accounts", "enable", "ghost"),
    ],
)
def test_an_unknown_account_prints_the_fixed_message(sync_api: FakeSyncAPI, command: tuple[str, ...]) -> None:
    # A route without an answer gets `404`; here the code is the one of the account routes.
    route = "POST /accounts/ghost/unlock" if command[1] == "unlock" else "PATCH /accounts/ghost"
    sync_api.answers[route] = sync_api.error(404, "account-not-found")

    result = _invoke(*command)

    assert result.exit_code == 1
    assert "message: no account has this name" in result.stderr


def test_an_account_name_that_cannot_be_one_sends_no_request(sync_api: FakeSyncAPI) -> None:
    result = _invoke("accounts", "unlock", "a/b")

    assert result.exit_code == 2
    assert "argument: name" in result.stderr
    assert sync_api.requests == []


@pytest.mark.parametrize(("flag", "administrator"), [("--administrator", True), ("--no-administrator", False)])
def test_accounts_set_level_sends_the_level_and_prints_the_account(
    sync_api: FakeSyncAPI, flag: str, *, administrator: bool
) -> None:
    sync_api.answers["PATCH /accounts/carol"] = httpx.Response(200, json=_account("carol", administrator=administrator))

    result = _invoke("accounts", "set-level", "carol", flag, "--reason", "team lead")

    assert result.exit_code == 0, result.output
    assert sync_api.body() == {"administrator": administrator, "reason": "team lead"}
    assert f"account: carol\nadministrator: {str(administrator).lower()}\n" in result.stdout


def test_accounts_set_level_without_a_level_sends_no_request(sync_api: FakeSyncAPI) -> None:
    result = _invoke("accounts", "set-level", "carol")

    assert result.exit_code == 2
    assert "--administrator or --no-administrator" in result.stderr
    assert sync_api.requests == []


def test_accounts_delete_with_yes_prints_the_placeholder(sync_api: FakeSyncAPI) -> None:
    sync_api.answers["DELETE /accounts/carol"] = httpx.Response(200, json={"placeholder": "Deleted account 0badc0de"})

    result = _invoke("accounts", "delete", "carol", "--yes", "--reason", "left the company")

    assert result.exit_code == 0, result.output
    assert result.stdout == "Deleted account 0badc0de\n"
    assert sync_api.routes == ["DELETE /accounts/carol"]
    assert dict(sync_api.requests[0].url.params) == {"reason": "left the company"}


@pytest.mark.usefixtures("terminal")
def test_accounts_delete_asks_for_a_confirmation_that_names_disable(sync_api: FakeSyncAPI) -> None:
    sync_api.answers["DELETE /accounts/carol"] = httpx.Response(200, json={"placeholder": "Deleted account 0badc0de"})

    result = _invoke("accounts", "delete", "carol", stdin="y\n")

    assert result.exit_code == 0, result.output
    assert "cannot be reversed" in result.stderr
    assert "accounts disable" in result.stderr
    # The test runner echoes the typed answer to standard output.
    assert result.stdout.endswith("Deleted account 0badc0de\n")
    assert sync_api.routes == ["DELETE /accounts/carol"]


@pytest.mark.usefixtures("terminal")
@pytest.mark.parametrize("answer", ["n\n", "\n"])
def test_accounts_delete_that_is_not_confirmed_sends_no_request(sync_api: FakeSyncAPI, answer: str) -> None:
    result = _invoke("accounts", "delete", "carol", stdin=answer)

    assert result.exit_code == 1
    assert "account not deleted" in result.stderr
    assert sync_api.requests == []


def test_accounts_delete_without_a_terminal_and_without_yes_names_yes(sync_api: FakeSyncAPI) -> None:
    result = _invoke("accounts", "delete", "carol", stdin="y\n")

    assert result.exit_code == 2
    assert "--yes" in result.stderr
    assert sync_api.requests == []


@pytest.mark.parametrize(
    ("status", "code", "text"),
    [
        (404, "account-not-found", "no account has this name"),
        (409, "environment-managed-account", "INFRAHUB_SYNC_ADMIN_ACCOUNT_PASSWORD"),
    ],
)
def test_a_refused_deletion_prints_the_fixed_message(sync_api: FakeSyncAPI, status: int, code: str, text: str) -> None:
    sync_api.answers["DELETE /accounts/carol"] = sync_api.error(status, code, "server text")

    result = _invoke("accounts", "delete", "carol", "--yes")

    assert result.exit_code == 1
    assert not result.stdout
    assert text in result.stderr
    assert "server text" not in result.stderr


def test_accounts_tokens_prints_one_row_for_each_token_without_any_value(sync_api: FakeSyncAPI) -> None:
    sync_api.answers["GET /accounts/carol/tokens"] = httpx.Response(200, json=[_token()])

    table = _invoke("accounts", "tokens", "carol")
    as_json = _invoke("accounts", "tokens", "carol", "--json")

    assert table.exit_code == 0, table.output
    assert table.stdout == f"ci\t{TOKEN_ID}\t{NOW.isoformat()}\t-\t-\n"
    assert json.loads(as_json.stdout)[0]["token_id"] == TOKEN_ID
    _assert_no_secret(table)


def test_accounts_delete_token_sends_the_identifier_and_the_reason(sync_api: FakeSyncAPI) -> None:
    sync_api.answers[f"DELETE /accounts/carol/tokens/{TOKEN_ID}"] = NO_CONTENT

    result = _invoke("accounts", "delete-token", "carol", TOKEN_ID, "--reason", "leaked")

    assert result.exit_code == 0, result.output
    assert result.stdout == f"token {TOKEN_ID} of account carol deleted\n"
    assert dict(sync_api.requests[0].url.params) == {"reason": "leaked"}


def test_accounts_delete_token_of_admin_names_the_variable(sync_api: FakeSyncAPI) -> None:
    sync_api.answers[f"DELETE /accounts/admin/tokens/{TOKEN_ID}"] = sync_api.error(409, "environment-managed-account")

    result = _invoke("accounts", "delete-token", "admin", TOKEN_ID)

    assert result.exit_code == 1
    assert "INFRAHUB_SYNC_ADMIN_ACCOUNT_TOKEN" in result.stderr


# --- the surface ---------------------------------------------------------------------------


def _commands(command: Any, path: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], Any]]:  # noqa: ANN401
    """Yield every command of the application with its path, groups walked through."""
    children = getattr(command, "commands", None)
    if children is None:
        yield path, command
        return
    for name, child in children.items():
        yield from _commands(child, (*path, name))


_SECRET_NAME = re.compile(r"password|passwd|secret|credential|token")
# `token_id` is the public identifier that `tokens list` prints, not a value.
_NOT_A_SECRET = frozenset({"token_id"})


def test_no_command_takes_a_password_or_a_token_as_an_argument_or_an_option_value() -> None:
    checked = 0
    for path, command in _commands(get_command(app)):
        for parameter in command.params:
            names = {parameter.name or "", *parameter.opts, *parameter.secondary_opts}
            if not any(_SECRET_NAME.search(name.lower()) for name in names) or parameter.name in _NOT_A_SECRET:
                continue
            checked += 1
            where = f"{' '.join(path)}: {parameter.name}"
            assert parameter.param_type_name == "option", where
            assert parameter.is_flag, where
            assert parameter.opts == ["--password-stdin"], where
    # login, password, accounts create, accounts set-password.
    assert checked == 4


def test_the_rule_finds_a_command_that_takes_a_password_value() -> None:
    probe = typer.Typer()

    @probe.command()
    def bad(password: str = typer.Option(..., "--password")) -> None:
        typer.echo(len(password))

    @probe.command()
    def other() -> None:
        typer.echo("")

    parameters = [parameter for _path, command in _commands(get_command(probe)) for parameter in command.params]
    flagged = [parameter for parameter in parameters if _SECRET_NAME.search(parameter.name or "")]

    assert [parameter.name for parameter in flagged] == ["password"]
    assert not flagged[0].is_flag


def test_every_account_command_is_listed() -> None:
    paths = {" ".join(path) for path, _command in _commands(get_command(app))}

    assert {
        "login",
        "logout",
        "password",
        "tokens list",
        "tokens create",
        "tokens delete",
        "accounts list",
        "accounts create",
        "accounts set-password",
        "accounts unlock",
        "accounts disable",
        "accounts enable",
        "accounts set-level",
        "accounts delete",
        "accounts tokens",
        "accounts delete-token",
    } <= paths


def _help(*args: str) -> str:
    result = RUNNER.invoke(app, [*args, "--help"], env={"NO_COLOR": "1", "COLUMNS": "200"})
    assert result.exit_code == 0, result.output
    return _ANSI.sub("", result.output)


def test_the_root_help_lists_the_account_commands() -> None:
    commands = {line.split()[1] for line in _help().splitlines() if line.startswith("│ ") and len(line.split()) > 2}

    assert {"login", "logout", "password", "tokens", "accounts", "whoami"} <= commands


@pytest.mark.parametrize(
    ("command", "options"),
    [
        (("login",), ("--name", "--password-stdin")),
        (("password",), ("--password-stdin", "--reason")),
        (("tokens", "create"), ("--expires", "--reason", "--json")),
        (("tokens", "delete"), ("--reason",)),
        (("tokens", "list"), ("--json",)),
        (("accounts", "list"), ("--state", "--json")),
        (("accounts", "create"), ("--administrator", "--password-stdin", "--reason", "--json")),
        (("accounts", "set-password"), ("--password-stdin", "--reason")),
        (("accounts", "unlock"), ("--reason",)),
        (("accounts", "disable"), ("--reason", "--json")),
        (("accounts", "enable"), ("--reason", "--json")),
        (("accounts", "set-level"), ("--administrator", "--no-administrator", "--reason", "--json")),
        (("accounts", "delete"), ("--yes", "--reason")),
        (("accounts", "tokens"), ("--json",)),
        (("accounts", "delete-token"), ("--reason",)),
    ],
)
def test_each_command_documents_its_options(command: tuple[str, ...], options: tuple[str, ...]) -> None:
    text = _help(*command)

    for option in options:
        assert option in text
    assert "--password " not in text


def test_the_expires_help_states_the_parsing_rule() -> None:
    text = " ".join(_help("tokens", "create").split())

    assert "ISO 8601" in text
    assert "UTC" in text
    assert "time zone" in text
