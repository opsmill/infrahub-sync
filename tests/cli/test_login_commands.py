"""`login`, `logout` and `password`, and which value every command presents to the Sync API."""

from __future__ import annotations

import json
from collections import UserDict
from datetime import datetime, timezone
from typing import TYPE_CHECKING, cast

import httpx
import pytest
from typer.testing import CliRunner

from infrahub_sync import cli_session
from infrahub_sync.cli import app
from infrahub_sync.cli_session import SESSION_FILE_NAME, SavedSession, read_session, write_session
from tests.demo.fakes import FakeSchemaLoader

if TYPE_CHECKING:
    from pathlib import Path

    from typer.testing import Result

    from tests.cli.conftest import FakeSyncAPI

URL = "http://127.0.0.1:8001"
OTHER_URL = "https://sync.example.test"
VARIABLE_TOKEN = "ifs_variable_canary"  # noqa: S105 - a canary that must never be printed.
SESSION_TOKEN = "ifs_session_canary"  # noqa: S105 - a canary that must never be printed.
PASSWORD = "correct horse battery staple"  # noqa: S105 - a canary that must never be printed.
NEW_PASSWORD = "another long passphrase"  # noqa: S105 - a canary that must never be printed.
NOW = datetime(2026, 11, 18, 12, tzinfo=timezone.utc).isoformat()
RUNNER = CliRunner()
NO_CONTENT = httpx.Response(204)
PRECEDENCE = "note: INFRAHUB_SYNC_API_TOKEN is set and takes precedence over the saved session for every other command"
NO_TOKEN_HINT = "hint: run infrahub-sync login, or set INFRAHUB_SYNC_API_TOKEN to a Sync API token"  # noqa: S105
SECRETS = (VARIABLE_TOKEN, SESSION_TOKEN, PASSWORD, NEW_PASSWORD)
ALICE_SESSION = SavedSession(account="alice", token=SESSION_TOKEN)
BOB_SESSION = SavedSession(account="bob", token="other-session")  # noqa: S106 - a test value.
NEWER_SESSION = SavedSession(account="alice", token="newer-session")  # noqa: S106 - a test value.


def _session_answer(name: str = "alice", *, administrator: bool = False) -> httpx.Response:
    account = {
        "name": name,
        "administrator": administrator,
        "disabled": False,
        "environment_managed": False,
        "created_at": NOW,
        "sign_in_refused_until": None,
    }
    return httpx.Response(200, json={"token": SESSION_TOKEN, "account": account, "idle_seconds": 28800})


ME = httpx.Response(200, json={"actor": "alice", "administrator": False, "approval_required": False})


@pytest.fixture(autouse=True)
def _environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INFRAHUB_SYNC_API_URL", URL)
    monkeypatch.delenv("INFRAHUB_SYNC_API_TOKEN", raising=False)


@pytest.fixture
def terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let a command ask its questions: the test runner's standard input is not a terminal."""
    monkeypatch.setattr("infrahub_sync.cli._stdin_is_interactive", lambda: True)


def _invoke(*args: str, stdin: str | None = None) -> Result:
    return RUNNER.invoke(app, list(args), input=stdin)


def _assert_no_secret(result: Result, *, allowed: tuple[str, ...] = ()) -> None:
    for secret in SECRETS:
        if secret not in allowed:
            assert secret not in result.output
            assert secret not in result.stderr


# --- login ---------------------------------------------------------------------------------


@pytest.mark.usefixtures("terminal")
def test_login_asks_for_the_name_and_the_password_and_saves_the_session(
    sync_api: FakeSyncAPI, session_directory: Path
) -> None:
    sync_api.answers["POST /auth/sign-in"] = _session_answer(administrator=True)

    result = _invoke("login", stdin=f"alice\n{PASSWORD}\n")

    assert result.exit_code == 0, result.output
    # The test runner echoes each answer to a question on standard output; the command's own lines end it.
    assert result.stdout.endswith("account: alice\nadministrator: true\n")
    assert "Account name" in result.stderr
    assert sync_api.routes == ["POST /auth/sign-in"]
    assert sync_api.bearers == [None]
    assert sync_api.body() == {"name": "alice", "password": PASSWORD}
    assert read_session(URL) == ALICE_SESSION
    assert (session_directory / SESSION_FILE_NAME).is_file()
    assert PRECEDENCE not in result.stderr
    _assert_no_secret(result)


@pytest.mark.usefixtures("terminal")
def test_login_with_a_name_asks_for_the_password_only(sync_api: FakeSyncAPI) -> None:
    sync_api.answers["POST /auth/sign-in"] = _session_answer()

    result = _invoke("login", "--name", "alice", stdin=f"{PASSWORD}\n")

    assert result.exit_code == 0, result.output
    assert sync_api.body() == {"name": "alice", "password": PASSWORD}
    assert "Account name" not in result.stderr
    assert "Password" in result.stderr
    _assert_no_secret(result)


def test_login_reads_the_password_from_standard_input(sync_api: FakeSyncAPI) -> None:
    sync_api.answers["POST /auth/sign-in"] = _session_answer()

    result = _invoke("login", "--name", "alice", "--password-stdin", stdin=f" {PASSWORD} \r\n")

    assert result.exit_code == 0, result.output
    # One line ending is removed; every other character of the password is kept.
    assert sync_api.body() == {"name": "alice", "password": f" {PASSWORD} "}
    assert read_session(URL) == ALICE_SESSION
    _assert_no_secret(result)


@pytest.mark.parametrize(
    ("args", "stdin", "named"),
    [
        pytest.param(("login", "--name", "alice"), f"{PASSWORD}\n", "--password-stdin", id="password-needs-a-terminal"),
        pytest.param(("login",), f"alice\n{PASSWORD}\n", "--name", id="name-needs-a-terminal"),
        pytest.param(("login", "--password-stdin"), f"{PASSWORD}\n", "--name", id="stdin-needs-a-name"),
        pytest.param(("login", "--name", "alice", "--password-stdin"), "", "--password-stdin", id="empty-stdin"),
    ],
)
def test_login_without_a_terminal_names_the_option_to_use_and_sends_nothing(
    sync_api: FakeSyncAPI, args: tuple[str, ...], stdin: str, named: str
) -> None:
    result = _invoke(*args, stdin=stdin)

    assert result.exit_code == 2
    assert named in result.stderr
    assert sync_api.requests == []
    assert read_session(URL) is None
    _assert_no_secret(result)


@pytest.mark.parametrize(
    ("status", "code", "message"),
    [
        (401, "sign-in-refused", "message: the account name or the password was not accepted"),
        (503, "sign-in-busy", "message: too many sign-ins are in progress; try again in a few seconds"),
    ],
)
def test_a_refused_login_prints_the_fixed_message_and_saves_nothing(
    sync_api: FakeSyncAPI, status: int, code: str, message: str
) -> None:
    sync_api.answers["POST /auth/sign-in"] = sync_api.error(status, code, f"server text with {PASSWORD}")
    write_session(OTHER_URL, BOB_SESSION)

    result = _invoke("login", "--name", "alice", "--password-stdin", stdin=f"{PASSWORD}\n")

    assert result.exit_code == 1
    assert not result.stdout
    assert f"status: {status}" in result.stderr
    assert f"code: {code}" in result.stderr
    assert message in result.stderr
    assert "server text" not in result.stderr
    # The answer is the same for a wrong name and a wrong password, and names no account.
    assert "alice" not in result.stderr
    assert "infrahub-sync login" not in result.stderr
    assert read_session(URL) is None
    assert read_session(OTHER_URL) == BOB_SESSION
    _assert_no_secret(result)


def test_login_replaces_the_session_saved_for_the_address(sync_api: FakeSyncAPI) -> None:
    write_session(URL, BOB_SESSION)
    sync_api.answers["POST /auth/sign-in"] = _session_answer()

    result = _invoke("login", "--name", "alice", "--password-stdin", stdin=f"{PASSWORD}\n")

    assert result.exit_code == 0, result.output
    assert sync_api.bearers == [None]
    assert read_session(URL) == ALICE_SESSION


def test_login_with_the_variable_set_still_signs_in_and_prints_the_notice(
    sync_api: FakeSyncAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INFRAHUB_SYNC_API_TOKEN", VARIABLE_TOKEN)
    sync_api.answers["POST /auth/sign-in"] = _session_answer()

    result = _invoke("login", "--name", "alice", "--password-stdin", stdin=f"{PASSWORD}\n")

    assert result.exit_code == 0, result.output
    assert sync_api.bearers == [None]
    assert read_session(URL) == ALICE_SESSION
    assert result.stderr.count(PRECEDENCE) == 1
    assert result.stdout == "account: alice\nadministrator: false\n"
    _assert_no_secret(result)


def test_login_saves_the_session_under_the_address_given_with_the_option(sync_api: FakeSyncAPI) -> None:
    sync_api.answers["POST /auth/sign-in"] = _session_answer()

    result = _invoke("--api-url", f"{OTHER_URL}/api/", "login", "--name", "alice", "--password-stdin", stdin="pw\n")

    assert result.exit_code == 0, result.output
    assert str(sync_api.requests[0].url) == f"{OTHER_URL}/api/auth/sign-in"
    assert read_session(OTHER_URL) == ALICE_SESSION
    assert read_session(URL) is None


def test_login_without_an_address_fails_before_any_question(
    sync_api: FakeSyncAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("INFRAHUB_SYNC_API_URL")

    result = _invoke("login", "--name", "alice", "--password-stdin", stdin=f"{PASSWORD}\n")

    assert result.exit_code == 2
    assert "argument: service_url" in result.stderr
    assert sync_api.requests == []
    _assert_no_secret(result)


def test_login_reports_a_session_that_cannot_be_saved(sync_api: FakeSyncAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr("infrahub_sync.cli_accounts.write_session", refuse)
    sync_api.answers["POST /auth/sign-in"] = _session_answer()

    result = _invoke("login", "--name", "alice", "--password-stdin", stdin=f"{PASSWORD}\n")

    assert result.exit_code == 1
    assert "the session could not be saved" in result.stderr
    assert not result.stdout
    _assert_no_secret(result)


# --- which value a command presents ----------------------------------------------------------


def test_the_variable_is_presented_and_the_session_file_is_not_read(
    sync_api: FakeSyncAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_session(URL, ALICE_SESSION)

    def refuse_read(*_args: object, **_kwargs: object) -> None:
        message = "the session file must not be read while the variable is set"
        raise AssertionError(message)

    monkeypatch.setattr(cli_session, "read_session", refuse_read)
    monkeypatch.setattr(cli_session, "default_directory", refuse_read)
    monkeypatch.setenv("INFRAHUB_SYNC_API_TOKEN", VARIABLE_TOKEN)
    sync_api.answers["GET /me"] = ME

    result = _invoke("whoami")

    assert result.exit_code == 0, result.output
    assert sync_api.bearers == [f"Bearer {VARIABLE_TOKEN}"]
    assert result.stdout == "actor: alice\nadministrator: false\napproval_required: false\n"
    _assert_no_secret(result)


def test_the_saved_session_is_presented_when_the_variable_is_not_set(sync_api: FakeSyncAPI) -> None:
    write_session(URL, ALICE_SESSION)
    write_session(OTHER_URL, BOB_SESSION)
    sync_api.answers["GET /me"] = ME

    result = _invoke("whoami")

    assert result.exit_code == 0, result.output
    assert sync_api.bearers == [f"Bearer {SESSION_TOKEN}"]
    _assert_no_secret(result)


def test_an_empty_variable_counts_as_not_set(sync_api: FakeSyncAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INFRAHUB_SYNC_API_TOKEN", "")
    write_session(URL, ALICE_SESSION)
    sync_api.answers["GET /me"] = ME

    result = _invoke("whoami")

    assert result.exit_code == 0, result.output
    assert sync_api.bearers == [f"Bearer {SESSION_TOKEN}"]


def test_the_session_of_another_address_is_not_presented(sync_api: FakeSyncAPI) -> None:
    write_session(OTHER_URL, SavedSession(account="bob", token=SESSION_TOKEN))

    result = _invoke("whoami")

    assert result.exit_code == 2
    assert NO_TOKEN_HINT in result.stderr
    assert sync_api.requests == []
    _assert_no_secret(result)


@pytest.mark.parametrize("command", [("whoami",), ("configs", "list"), ("tokens", "list"), ("accounts", "list")])
def test_without_the_variable_and_a_session_the_hint_names_both_ways(
    sync_api: FakeSyncAPI, command: tuple[str, ...]
) -> None:
    result = _invoke(*command)

    assert result.exit_code == 2
    assert result.stderr == f"error: client-input\nargument: token\n{NO_TOKEN_HINT}\n"
    assert "infrahub-sync login" in result.stderr
    assert "INFRAHUB_SYNC_API_TOKEN" in result.stderr
    assert sync_api.requests == []


def test_a_damaged_session_file_reads_as_no_session_without_its_content(
    sync_api: FakeSyncAPI, session_directory: Path
) -> None:
    session_directory.mkdir(parents=True)
    (session_directory / SESSION_FILE_NAME).write_text(f'{{"{URL}": "{SESSION_TOKEN}"', encoding="utf-8")

    result = _invoke("whoami")

    assert result.exit_code == 2
    assert NO_TOKEN_HINT in result.stderr
    assert "Traceback" not in result.output
    assert sync_api.requests == []
    _assert_no_secret(result)


def test_a_refused_saved_session_is_removed_and_the_login_hint_is_printed(sync_api: FakeSyncAPI) -> None:
    write_session(URL, ALICE_SESSION)
    write_session(OTHER_URL, BOB_SESSION)
    sync_api.answers["GET /me"] = sync_api.error(401, "unauthenticated")

    result = _invoke("whoami")

    assert result.exit_code == 1
    assert "code: unauthenticated" in result.stderr
    assert result.stderr.endswith(
        "hint: the Sync API no longer accepts the saved session, so it was removed; run infrahub-sync login\n"
    )
    assert read_session(URL) is None
    assert read_session(OTHER_URL) == BOB_SESSION
    _assert_no_secret(result)


def test_demo_prepare_removes_a_refused_saved_session(sync_api: FakeSyncAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    # `demo prepare` handles a Sync client error itself, outside `_client_errors`.
    write_session(URL, ALICE_SESSION)
    monkeypatch.setenv("INFRAHUB_API_TOKEN", VARIABLE_TOKEN)
    monkeypatch.setattr("infrahub_sync.demo.prepare.load_schema", FakeSchemaLoader())
    sync_api.answers["GET /configs"] = sync_api.error(401, "unauthenticated")

    result = _invoke(
        "demo", "prepare", "--netbox-url", "http://netbox:8080", "--infrahub-url", "http://infrahub-server:8000"
    )

    assert result.exit_code == 1
    assert "code: unauthenticated" in result.stderr
    assert "the saved session, so it was removed; run infrahub-sync login" in result.stderr
    assert read_session(URL) is None
    _assert_no_secret(result)


class _LoginWhileWaiting(UserDict):
    """The answers of the fake Sync API; a `login` saves another session before the refusal arrives."""

    def get(self, key: str, default: object = None) -> object:
        write_session(URL, BOB_SESSION)
        return super().get(key, default)


def test_a_session_saved_while_the_refused_one_was_in_use_is_kept(sync_api: FakeSyncAPI) -> None:
    write_session(URL, ALICE_SESSION)
    # The fake reads its answers with `get` only, so the mapping stands in for its dictionary.
    sync_api.answers = cast(
        "dict[str, httpx.Response]", _LoginWhileWaiting({"GET /me": sync_api.error(401, "unauthenticated")})
    )

    result = _invoke("whoami")

    assert result.exit_code == 1
    assert "so it was removed" not in result.stderr
    assert result.stderr.endswith("hint: the Sync API no longer accepts the saved session; run infrahub-sync login\n")
    assert read_session(URL) == BOB_SESSION
    _assert_no_secret(result)


def test_a_refused_variable_token_removes_nothing(sync_api: FakeSyncAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    write_session(URL, ALICE_SESSION)
    monkeypatch.setenv("INFRAHUB_SYNC_API_TOKEN", VARIABLE_TOKEN)
    sync_api.answers["GET /me"] = sync_api.error(401, "unauthenticated")

    result = _invoke("whoami")

    assert result.exit_code == 1
    assert "code: unauthenticated" in result.stderr
    assert "infrahub-sync login" not in result.stderr
    assert read_session(URL) == ALICE_SESSION


def test_a_saved_session_is_kept_after_a_refusal_that_is_not_about_the_session(sync_api: FakeSyncAPI) -> None:
    write_session(URL, ALICE_SESSION)
    sync_api.answers["GET /accounts"] = sync_api.error(403, "forbidden")

    result = _invoke("accounts", "list")

    assert result.exit_code == 1
    assert "infrahub-sync login" not in result.stderr
    assert read_session(URL) == ALICE_SESSION


def test_a_command_after_a_refused_session_does_not_remove_a_new_one(
    sync_api: FakeSyncAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_session(URL, ALICE_SESSION)
    sync_api.answers["GET /me"] = sync_api.error(401, "unauthenticated")
    assert _invoke("whoami").exit_code == 1
    # The next command presents the variable: its refusal must leave the file alone.
    write_session(URL, NEWER_SESSION)
    monkeypatch.setenv("INFRAHUB_SYNC_API_TOKEN", VARIABLE_TOKEN)

    result = _invoke("whoami")

    assert result.exit_code == 1
    assert read_session(URL) == NEWER_SESSION


# --- logout --------------------------------------------------------------------------------


def test_logout_ends_the_saved_session_and_removes_it(sync_api: FakeSyncAPI) -> None:
    write_session(URL, ALICE_SESSION)
    write_session(OTHER_URL, BOB_SESSION)
    sync_api.answers["POST /auth/sign-out"] = NO_CONTENT

    result = _invoke("logout")

    assert result.exit_code == 0, result.output
    assert result.stdout == "signed out\n"
    assert sync_api.routes == ["POST /auth/sign-out"]
    assert sync_api.bearers == [f"Bearer {SESSION_TOKEN}"]
    assert read_session(URL) is None
    assert read_session(OTHER_URL) == BOB_SESSION
    assert PRECEDENCE not in result.stderr
    _assert_no_secret(result)


def test_logout_without_a_saved_session_says_so_and_succeeds(sync_api: FakeSyncAPI) -> None:
    result = _invoke("logout")

    assert result.exit_code == 0, result.output
    assert result.stdout == "no saved session for this Sync API address\n"
    assert sync_api.requests == []


def test_logout_removes_a_session_that_the_sync_api_already_ended(sync_api: FakeSyncAPI) -> None:
    write_session(URL, ALICE_SESSION)
    sync_api.answers["POST /auth/sign-out"] = sync_api.error(401, "unauthenticated")

    result = _invoke("logout")

    assert result.exit_code == 0, result.output
    assert result.stdout == "signed out\n"
    assert not result.stderr
    assert read_session(URL) is None


def test_logout_with_the_variable_set_ends_the_saved_session_and_prints_the_notice(
    sync_api: FakeSyncAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INFRAHUB_SYNC_API_TOKEN", VARIABLE_TOKEN)
    write_session(URL, ALICE_SESSION)
    sync_api.answers["POST /auth/sign-out"] = NO_CONTENT

    result = _invoke("logout")

    assert result.exit_code == 0, result.output
    # The request presents the saved session, not the variable's token.
    assert sync_api.bearers == [f"Bearer {SESSION_TOKEN}"]
    assert read_session(URL) is None
    assert result.stderr.count(PRECEDENCE) == 1
    _assert_no_secret(result)


def test_logout_with_the_variable_set_and_no_saved_session_prints_the_notice(
    sync_api: FakeSyncAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INFRAHUB_SYNC_API_TOKEN", VARIABLE_TOKEN)

    result = _invoke("logout")

    assert result.exit_code == 0, result.output
    assert "no saved session" in result.stdout
    assert result.stderr.count(PRECEDENCE) == 1
    assert sync_api.requests == []


def test_logout_keeps_the_session_when_the_sync_api_cannot_be_reached(sync_api: FakeSyncAPI) -> None:
    write_session(URL, ALICE_SESSION)
    sync_api.answers["POST /auth/sign-out"] = sync_api.error(503, "unavailable")

    result = _invoke("logout")

    assert result.exit_code == 1
    assert "status: 503" in result.stderr
    assert read_session(URL) == ALICE_SESSION
    _assert_no_secret(result)


def test_logout_reports_a_session_that_cannot_be_removed(
    sync_api: FakeSyncAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_session(URL, ALICE_SESSION)
    sync_api.answers["POST /auth/sign-out"] = NO_CONTENT

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr("infrahub_sync.cli_accounts.remove_session", refuse)

    result = _invoke("logout")

    assert result.exit_code == 1
    assert "signed out, but the session could not be removed" in result.stderr
    assert "Traceback" not in result.output
    assert not result.stdout
    _assert_no_secret(result)


def test_logout_without_an_address_prints_the_address_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INFRAHUB_SYNC_API_URL")

    result = _invoke("logout")

    assert result.exit_code == 2
    assert "argument: service_url" in result.stderr


# --- password ------------------------------------------------------------------------------


@pytest.fixture
def signed_in(sync_api: FakeSyncAPI) -> FakeSyncAPI:
    write_session(URL, ALICE_SESSION)
    return sync_api


@pytest.mark.usefixtures("terminal")
def test_password_asks_for_the_current_password_once_and_the_new_one_twice(signed_in: FakeSyncAPI) -> None:
    signed_in.answers["PUT /me/password"] = NO_CONTENT

    result = _invoke("password", "--reason", "rotation", stdin=f"{PASSWORD}\n{NEW_PASSWORD}\n{NEW_PASSWORD}\n")

    assert result.exit_code == 0, result.output
    assert result.stdout.endswith("password changed\n")
    assert signed_in.routes == ["PUT /me/password"]
    assert signed_in.body() == {"current_password": PASSWORD, "new_password": NEW_PASSWORD, "reason": "rotation"}
    assert PASSWORD not in str(signed_in.requests[0].url)
    assert result.stderr.count("Current password") == 1
    assert result.stderr.count("New password") == 2
    _assert_no_secret(result)


@pytest.mark.usefixtures("terminal")
def test_password_refuses_two_different_new_passwords_before_any_request(signed_in: FakeSyncAPI) -> None:
    result = _invoke("password", stdin=f"{PASSWORD}\n{NEW_PASSWORD}\n{NEW_PASSWORD}x\n")

    assert result.exit_code == 2
    assert "error: the two passwords do not match" in result.stderr
    assert signed_in.requests == []
    _assert_no_secret(result)


def test_password_reads_two_lines_from_standard_input(signed_in: FakeSyncAPI) -> None:
    signed_in.answers["PUT /me/password"] = NO_CONTENT

    result = _invoke("password", "--password-stdin", stdin=f"{PASSWORD}\n{NEW_PASSWORD}\n")

    assert result.exit_code == 0, result.output
    assert signed_in.body() == {"current_password": PASSWORD, "new_password": NEW_PASSWORD}
    assert not result.stderr
    _assert_no_secret(result)


def test_password_with_one_line_on_standard_input_sends_nothing(signed_in: FakeSyncAPI) -> None:
    result = _invoke("password", "--password-stdin", stdin=f"{PASSWORD}\n")

    assert result.exit_code == 2
    assert "--password-stdin" in result.stderr
    assert signed_in.requests == []
    _assert_no_secret(result)


def test_password_without_a_terminal_names_password_stdin(signed_in: FakeSyncAPI) -> None:
    result = _invoke("password", stdin=f"{PASSWORD}\n{NEW_PASSWORD}\n{NEW_PASSWORD}\n")

    assert result.exit_code == 2
    assert "--password-stdin" in result.stderr
    assert signed_in.requests == []
    _assert_no_secret(result)


@pytest.mark.parametrize(
    ("status", "code", "expected"),
    [
        pytest.param(403, "current-password-refused", "message: the current password was not accepted", id="wrong"),
        pytest.param(422, "password-too-short", "message: a password has at least 12 characters", id="too-short"),
        pytest.param(422, "password-too-long", "message: a password has at most 1024 characters", id="too-long"),
        pytest.param(409, "environment-managed-account", "INFRAHUB_SYNC_ADMIN_ACCOUNT_PASSWORD", id="admin"),
    ],
)
def test_a_refused_password_change_prints_the_fixed_message(
    signed_in: FakeSyncAPI, status: int, code: str, expected: str
) -> None:
    signed_in.answers["PUT /me/password"] = signed_in.error(status, code, f"server text with {NEW_PASSWORD}")

    result = _invoke("password", "--password-stdin", stdin=f"{PASSWORD}\n{NEW_PASSWORD}\n")

    assert result.exit_code == 1
    assert not result.stdout
    assert f"code: {code}" in result.stderr
    assert expected in result.stderr
    assert "server text" not in result.stderr
    assert read_session(URL) == ALICE_SESSION
    _assert_no_secret(result)


def test_the_session_file_never_holds_a_password(sync_api: FakeSyncAPI, session_directory: Path) -> None:
    sync_api.answers["POST /auth/sign-in"] = _session_answer()

    assert _invoke("login", "--name", "alice", "--password-stdin", stdin=f"{PASSWORD}\n").exit_code == 0

    text = (session_directory / SESSION_FILE_NAME).read_text(encoding="utf-8")
    assert PASSWORD not in text
    assert json.loads(text) == {URL: {"token": SESSION_TOKEN, "account": "alice"}}


def test_an_unexpected_exception_prints_no_local_variable() -> None:
    # A local variable of `login` or `password` holds a typed password; a traceback must not show it.
    assert app.pretty_exceptions_show_locals is False
