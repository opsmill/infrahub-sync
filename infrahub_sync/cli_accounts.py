"""The CLI commands of the local accounts: sign in, sign out, change a password, tokens and accounts.

`infrahub_sync/cli.py` calls `register` once, with the helpers that every command shares. This
module imports nothing from `cli.py`, so the two modules do not import each other.

To add an `accounts` subcommand, define a function decorated with `@accounts_app.command(...)`
in this module. To add a command group, create a `typer.Typer` here and add it in `register`.

A password is never a command argument or an option value. `_read_password` reads it from a
prompt without echo, or from standard input with `--password-stdin`.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from enum import Enum
from typing import TYPE_CHECKING, NoReturn

import typer

from infrahub_sync.cli_session import SavedSession, default_directory, read_session, remove_session, write_session
from infrahub_sync.client import APIError

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from contextlib import AbstractContextManager

    from infrahub_sync.client import AccountResource, SyncClient, TokenResource

API_TOKEN_VARIABLE = "INFRAHUB_SYNC_API_TOKEN"
PRECEDENCE_NOTICE = (
    f"note: {API_TOKEN_VARIABLE} is set and takes precedence over the saved session for every other command"
)
LOGIN_HINT = "run infrahub-sync login"
# Fixed text for each refusal code of the account routes. The client keeps no message text of
# an answer (see the secret-redaction guideline), so the CLI prints its own text for each code.
ACCOUNT_ERROR_MESSAGES = {
    "sign-in-refused": "the account name or the password was not accepted",
    "sign-in-busy": "too many sign-ins are in progress; try again in a few seconds",
    "not-a-session": "this request presents an API token; only a session can be signed out",
    "token-expired": "the API token has expired",
    "account-not-found": "no account has this name",
    "account-name-invalid": (
        "an account name has 1 to 64 characters among letters, digits, '.', '_', '@' and '-', starts with a "
        "letter or a digit, and is not a name that the product reserves"
    ),
    "account-name-taken": "an account already has this name",
    "password-too-short": "a password has at least 12 characters",
    "password-too-long": "a password has at most 1024 characters",
    "current-password-refused": "the current password was not accepted",
    "token-name-taken": "the account already has a token with this name",
    "token-not-found": "the account has no token with this identifier",
    "expiry-in-the-past": "the expiry date must be a time in the future",
    "environment-managed-account": (
        "the environment of the Sync API manages the admin account; change "
        "INFRAHUB_SYNC_ADMIN_ACCOUNT_PASSWORD for its password or INFRAHUB_SYNC_ADMIN_ACCOUNT_TOKEN "
        "for its token, and restart the Sync API"
    ),
}
_PASSWORD_STDIN_HELP = "Read the password from standard input instead of a prompt."
_JSON_HELP = "Print the answer of the Sync API as JSON."
_ARGUMENT_EXIT = 2
_UNAUTHENTICATED = 401
_DATE_ONLY = re.compile(r"\d{4}-\d{2}-\d{2}")
_END_OF_DAY = time(23, 59, 59)

tokens_app = typer.Typer(help="List, create and delete the API tokens of your account.")
accounts_app = typer.Typer(help="Manage local accounts. Every command needs an administrator.")


@dataclass(frozen=True)
class CommandSupport:
    """The helpers of `cli.py` that the account commands share with every other command."""

    client: Callable[[typer.Context], SyncClient]
    sign_in_client: Callable[[typer.Context], SyncClient]
    session_client: Callable[[typer.Context, str], SyncClient]
    api_url: Callable[[typer.Context], str]
    client_errors: Callable[[], AbstractContextManager[None]]
    echo_fields: Callable[..., None]
    display: Callable[[object], str]
    stdin_is_interactive: Callable[[], bool]


class _Registered:
    """Hold the helpers that `register` received, for the commands of this module."""

    support: CommandSupport | None = None


def _support() -> CommandSupport:
    support = _Registered.support
    if support is None:
        message = "the account commands are not registered"
        raise RuntimeError(message)
    return support


class AccountState(str, Enum):
    """The states `accounts list` filters by."""

    active = "active"
    disabled = "disabled"
    all = "all"


def _fail(message: str) -> NoReturn:
    """Print one error line and end a command that sent no request."""
    typer.echo(f"error: {message}", err=True)
    raise typer.Exit(_ARGUMENT_EXIT)


def _stdin_line() -> str:
    """Read one line of standard input without its line ending. A password keeps every other character."""
    line = sys.stdin.readline()
    line = line.removesuffix("\n")
    line = line.removesuffix("\r")
    if not line:
        _fail("standard input holds no password; with --password-stdin, give one password on each line")
    return line


def _prompt_password(label: str, *, twice: bool) -> str:
    """Ask for a password without echo; with ``twice``, ask again and refuse two different values."""
    if not _support().stdin_is_interactive():
        _fail(
            "standard input is not a terminal, so no password can be asked; "
            "pass --password-stdin and give the password on standard input"
        )
    password = typer.prompt(label, hide_input=True, err=True)
    if twice and typer.prompt(f"{label} (again)", hide_input=True, err=True) != password:
        _fail("the two passwords do not match")
    return password


def _read_password(label: str, *, from_stdin: bool, twice: bool = False) -> str:
    """Return a password from standard input with ``--password-stdin``, else from a prompt."""
    return _stdin_line() if from_stdin else _prompt_password(label, twice=twice)


def _echo_precedence_notice() -> None:
    if os.environ.get(API_TOKEN_VARIABLE):
        typer.echo(PRECEDENCE_NOTICE, err=True)


def _echo_json(document: object) -> None:
    # `json.dumps` escapes control characters, so the output is safe for a terminal as it is.
    typer.echo(json.dumps(document, sort_keys=True, indent=2))


def _time_text(value: datetime | None) -> str:
    return "-" if value is None else value.isoformat()


def _account_fields(account: AccountResource) -> tuple[tuple[str, object], ...]:
    return (
        ("account", account.name),
        ("administrator", account.administrator),
        ("disabled", account.disabled),
        ("environment_managed", account.environment_managed),
        ("created_at", account.created_at.isoformat()),
        ("sign_in_refused_until", _time_text(account.sign_in_refused_until)),
    )


def _echo_account(account: AccountResource, *, as_json: bool) -> None:
    if as_json:
        _echo_json(account.model_dump(mode="json"))
    else:
        _support().echo_fields(_account_fields(account))


def _row(columns: Iterable[str]) -> str:
    display = _support().display
    return "\t".join(display(column) for column in columns)


def _account_row(account: AccountResource) -> str:
    return _row(
        (
            account.name,
            "administrator" if account.administrator else "not-administrator",
            "disabled" if account.disabled else "active",
            _time_text(account.sign_in_refused_until),
        )
    )


def _token_row(token: TokenResource) -> str:
    return _row(
        (
            token.name,
            token.token_id,
            token.created_at.isoformat(),
            _time_text(token.expires_at),
            _time_text(token.last_used_at),
        )
    )


def parse_expiry(value: str) -> datetime:
    """Return the expiry time that ``--expires`` names.

    A date alone (``2027-01-31``) means the last second of that day in UTC. A date and a time
    must carry a time zone (``2027-01-31T12:00:00+00:00`` or ``2027-01-31T12:00:00Z``).

    Raises:
        typer.BadParameter: when the value is not an ISO 8601 date or date-time, or names no time zone.
    """
    text = value.strip()
    try:
        if _DATE_ONLY.fullmatch(text):
            return datetime.combine(date.fromisoformat(text), _END_OF_DAY, tzinfo=timezone.utc)
        # Python 3.10 does not read the `Z` suffix.
        moment = datetime.fromisoformat(text[:-1] + "+00:00" if text[-1:] in {"Z", "z"} else text)
    except ValueError:
        message = "give an ISO 8601 date such as 2027-01-31, or a date-time with a time zone"
        raise typer.BadParameter(message, param_hint="--expires") from None
    if moment.utcoffset() is None:
        message = "a date-time needs a time zone, such as 2027-01-31T12:00:00+00:00 or 2027-01-31T12:00:00Z"
        raise typer.BadParameter(message, param_hint="--expires")
    return moment


def login_cmd(
    ctx: typer.Context,
    name: str | None = typer.Option(None, "--name", help="Account name. Asked for when not given."),
    password_stdin: bool = typer.Option(False, "--password-stdin", help=_PASSWORD_STDIN_HELP),  # noqa: FBT003
) -> None:
    """Sign in with an account name and its password, and save the session for this Sync API address.

    The password is asked for without echo. With --password-stdin it is read from standard input,
    and --name is required. The session is saved in sessions.json in the application directory of
    infrahub-sync, readable by you only. While INFRAHUB_SYNC_API_TOKEN is set, every other command
    uses that variable and not the saved session.
    """
    support = _support()
    with support.client_errors():
        address = support.api_url(ctx)
        client = support.sign_in_client(ctx)
        if name is None:
            if password_stdin or not support.stdin_is_interactive():
                _fail("pass --name: the account name cannot be asked for when standard input is not a terminal")
            name = typer.prompt("Account name", err=True)
        password = _read_password("Password", from_stdin=password_stdin)
        session = client.sign_in(name, password)
        try:
            write_session(address, SavedSession(account=session.account.name, token=session.token))
        except (OSError, ValueError):
            typer.echo(
                support.display(f"error: signed in, but the session could not be saved in {default_directory()}"),
                err=True,
            )
            raise typer.Exit(1) from None
        support.echo_fields((("account", session.account.name), ("administrator", session.account.administrator)))
        _echo_precedence_notice()


def logout_cmd(ctx: typer.Context) -> None:
    """Sign out: end the session saved for this Sync API address and remove it from the session file.

    While INFRAHUB_SYNC_API_TOKEN is set, this command still acts on the saved session.
    """
    support = _support()
    with support.client_errors():
        address = support.api_url(ctx)
        saved = read_session(address)
        if saved is None:
            # Building the client refuses an address that is not one, with the usual hint.
            support.sign_in_client(ctx)
            typer.echo("no saved session for this Sync API address")
            _echo_precedence_notice()
            return
        try:
            support.session_client(ctx, saved.token).sign_out()
        except APIError as error:
            # A session the Sync API no longer accepts has already ended.
            if error.status != _UNAUTHENTICATED:
                raise
        remove_session(address)
        typer.echo("signed out")
        _echo_precedence_notice()


def password_cmd(
    ctx: typer.Context,
    password_stdin: bool = typer.Option(
        False,  # noqa: FBT003
        "--password-stdin",
        help="Read two lines from standard input instead of prompts: the current password, then the new one.",
    ),
    reason: str | None = typer.Option(None, "--reason", help="Optional audit reason for the change."),
) -> None:
    """Change the password of your account. Your other sessions end; your API tokens stay.

    The current password is asked for once and the new one twice, without echo.
    """
    support = _support()
    with support.client_errors():
        client = support.client(ctx)
        current = _read_password("Current password", from_stdin=password_stdin)
        new = _read_password("New password", from_stdin=password_stdin, twice=True)
        client.change_password(current, new, reason=reason)
        typer.echo("password changed")


@tokens_app.command("list")
def tokens_list(
    ctx: typer.Context,
    as_json: bool = typer.Option(False, "--json", help=_JSON_HELP),  # noqa: FBT003
) -> None:
    """List the API tokens of your account: name, token ID, creation, expiry and last use. No value is shown."""
    support = _support()
    with support.client_errors():
        tokens = support.client(ctx).list_tokens()
        if as_json:
            _echo_json([token.model_dump(mode="json") for token in tokens])
            return
        for token in tokens:
            typer.echo(_token_row(token))


@tokens_app.command("create")
def tokens_create(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Name of the new token, unique in your account."),
    expires: str | None = typer.Option(
        None,
        "--expires",
        help=(
            "Expiry as an ISO 8601 date or date-time. A date alone, such as 2027-01-31, means the last "
            "second of that day in UTC. A date-time must carry a time zone, such as "
            "2027-01-31T12:00:00+00:00. Without this option the token does not expire."
        ),
    ),
    reason: str | None = typer.Option(None, "--reason", help="Optional audit reason for the creation."),
    as_json: bool = typer.Option(
        False,  # noqa: FBT003
        "--json",
        help="Print the answer of the Sync API as JSON, with the token value in it.",
    ),
) -> None:
    """Create an API token for your account and print its value once.

    The value is the only line on standard output, so TOKEN=$(infrahub-sync tokens create ci)
    captures it. The Sync API does not show the value again.
    """
    support = _support()
    expires_at = None
    if expires is not None:
        try:
            expires_at = parse_expiry(expires)
        except typer.BadParameter as error:
            # One plain error line, like every other refusal of this module, before any request.
            _fail(f"--expires: {error.message}")
    with support.client_errors():
        created = support.client(ctx).create_token(name, expires_at=expires_at, reason=reason)
        if as_json:
            _echo_json(created.model_dump(mode="json"))
        else:
            typer.echo(created.token)
            support.echo_fields(
                (
                    ("token_id", created.token_id),
                    ("name", created.name),
                    ("expires_at", _time_text(created.expires_at)),
                ),
                err=True,
            )
        typer.echo("note: the token value is not shown again; store it now", err=True)


@tokens_app.command("delete")
def tokens_delete(
    ctx: typer.Context,
    token_id: str = typer.Argument(..., help="Token ID, as `tokens list` prints it."),
    reason: str | None = typer.Option(None, "--reason", help="Optional audit reason for the deletion."),
) -> None:
    """Delete one API token of your account. The Sync API refuses its value afterwards."""
    support = _support()
    with support.client_errors():
        support.client(ctx).delete_token(token_id, reason=reason)
        typer.echo(support.display(f"token {token_id} deleted"))


@accounts_app.command("list")
def accounts_list(
    ctx: typer.Context,
    state: AccountState = typer.Option(AccountState.all, "--state", help="Only accounts in this state."),
    as_json: bool = typer.Option(False, "--json", help=_JSON_HELP),  # noqa: FBT003
) -> None:
    """List accounts by name: name, level, state, and the time until which sign-ins are refused."""
    support = _support()
    with support.client_errors():
        accounts = support.client(ctx).list_accounts(state.value)
        if as_json:
            _echo_json([account.model_dump(mode="json") for account in accounts])
            return
        for account in accounts:
            typer.echo(_account_row(account))


@accounts_app.command("create")
def accounts_create(  # pylint: disable=too-many-positional-arguments
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Name of the new account."),
    administrator: bool = typer.Option(False, "--administrator", help="Give the account the administrator level."),  # noqa: FBT003
    password_stdin: bool = typer.Option(False, "--password-stdin", help=_PASSWORD_STDIN_HELP),  # noqa: FBT003
    reason: str | None = typer.Option(None, "--reason", help="Optional audit reason for the creation."),
    as_json: bool = typer.Option(False, "--json", help=_JSON_HELP),  # noqa: FBT003
) -> None:
    """Create an account. The initial password is asked for twice, without echo."""
    support = _support()
    with support.client_errors():
        client = support.client(ctx)
        password = _read_password("Initial password", from_stdin=password_stdin, twice=True)
        account = client.create_account(name, password, administrator=administrator, reason=reason)
        _echo_account(account, as_json=as_json)


@accounts_app.command("set-password")
def accounts_set_password(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Account name."),
    password_stdin: bool = typer.Option(False, "--password-stdin", help=_PASSWORD_STDIN_HELP),  # noqa: FBT003
    reason: str | None = typer.Option(None, "--reason", help="Optional audit reason for the change."),
) -> None:
    """Replace the password of an account. The new password is asked for twice; every session of the account ends."""
    support = _support()
    with support.client_errors():
        client = support.client(ctx)
        password = _read_password("New password", from_stdin=password_stdin, twice=True)
        client.set_account_password(name, password, reason=reason)
        typer.echo(support.display(f"password of account {name} changed"))


@accounts_app.command("unlock")
def accounts_unlock(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Account name."),
    reason: str | None = typer.Option(None, "--reason", help="Optional audit reason for the unlock."),
) -> None:
    """End the refusal of an account's sign-ins that follows repeated failed sign-ins."""
    support = _support()
    with support.client_errors():
        support.client(ctx).unlock_account(name, reason=reason)
        typer.echo(support.display(f"account {name} unlocked"))


def _set_disabled(ctx: typer.Context, name: str, *, disabled: bool, reason: str | None, as_json: bool) -> None:
    support = _support()
    with support.client_errors():
        account = support.client(ctx).set_account_disabled(name, disabled, reason=reason)
        _echo_account(account, as_json=as_json)


@accounts_app.command("disable")
def accounts_disable(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Account name."),
    reason: str | None = typer.Option(None, "--reason", help="Optional audit reason for the change."),
    as_json: bool = typer.Option(False, "--json", help=_JSON_HELP),  # noqa: FBT003
) -> None:
    """Disable an account. The Sync API deletes its API tokens and ends its sessions."""
    _set_disabled(ctx, name, disabled=True, reason=reason, as_json=as_json)


@accounts_app.command("enable")
def accounts_enable(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Account name."),
    reason: str | None = typer.Option(None, "--reason", help="Optional audit reason for the change."),
    as_json: bool = typer.Option(False, "--json", help=_JSON_HELP),  # noqa: FBT003
) -> None:
    """Enable a disabled account. Its earlier API tokens and sessions are not restored."""
    _set_disabled(ctx, name, disabled=False, reason=reason, as_json=as_json)


@accounts_app.command("set-level")
def accounts_set_level(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Account name."),
    administrator: bool | None = typer.Option(
        None,
        "--administrator/--no-administrator",
        help="Give the account the administrator level, or take it away. One of the two is required.",
        show_default=False,
    ),
    reason: str | None = typer.Option(None, "--reason", help="Optional audit reason for the change."),
    as_json: bool = typer.Option(False, "--json", help=_JSON_HELP),  # noqa: FBT003
) -> None:
    """Set the level of an account. It applies from the next request of the account, with no new sign-in."""
    if administrator is None:
        _fail("pass --administrator or --no-administrator")
    support = _support()
    with support.client_errors():
        account = support.client(ctx).set_account_level(name, administrator, reason=reason)
        _echo_account(account, as_json=as_json)


@accounts_app.command("delete")
def accounts_delete(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Account name."),
    yes: bool = typer.Option(False, "--yes", help="Delete without asking for confirmation."),  # noqa: FBT003
    reason: str | None = typer.Option(None, "--reason", help="Optional audit reason for the deletion."),
) -> None:
    """Delete an account. This cannot be reversed; `accounts disable` can.

    The name, the password, the API tokens and the sessions of the account are removed. Its runs,
    approvals and audit events stay, and display `Deleted account` and a short identifier, which
    this command prints. The name can be given to a new account, which has no right over the old
    runs.
    """
    support = _support()
    if not yes:
        if not support.stdin_is_interactive():
            _fail("standard input is not a terminal, so no confirmation can be asked; pass --yes to delete")
        prompt = support.display(
            f"Delete account {name}? This cannot be reversed; `infrahub-sync accounts disable` can be reversed"
        )
        if not typer.confirm(prompt, default=False, err=True):
            typer.echo("account not deleted", err=True)
            raise typer.Exit(1)
    with support.client_errors():
        placeholder = support.client(ctx).delete_account(name, reason=reason)
        typer.echo(support.display(placeholder))


@accounts_app.command("tokens")
def accounts_tokens(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Account name."),
    as_json: bool = typer.Option(False, "--json", help=_JSON_HELP),  # noqa: FBT003
) -> None:
    """List the API tokens of an account: name, token ID, creation, expiry and last use. No value is shown."""
    support = _support()
    with support.client_errors():
        tokens = support.client(ctx).list_account_tokens(name)
        if as_json:
            _echo_json([token.model_dump(mode="json") for token in tokens])
            return
        for token in tokens:
            typer.echo(_token_row(token))


@accounts_app.command("delete-token")
def accounts_delete_token(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Account name."),
    token_id: str = typer.Argument(..., help="Token ID, as `accounts tokens` prints it."),
    reason: str | None = typer.Option(None, "--reason", help="Optional audit reason for the deletion."),
) -> None:
    """Delete one API token of an account. The Sync API refuses its value afterwards."""
    support = _support()
    with support.client_errors():
        support.client(ctx).delete_account_token(name, token_id, reason=reason)
        typer.echo(support.display(f"token {token_id} of account {name} deleted"))


def register(app: typer.Typer, support: CommandSupport) -> None:
    """Add `login`, `logout`, `password`, `tokens` and `accounts` to the CLI."""
    _Registered.support = support
    app.command("login")(login_cmd)
    app.command("logout")(logout_cmd)
    app.command("password")(password_cmd)
    app.add_typer(tokens_app, name="tokens")
    app.add_typer(accounts_app, name="accounts")


__all__ = [
    "ACCOUNT_ERROR_MESSAGES",
    "LOGIN_HINT",
    "PRECEDENCE_NOTICE",
    "CommandSupport",
    "accounts_app",
    "parse_expiry",
    "register",
    "tokens_app",
]
