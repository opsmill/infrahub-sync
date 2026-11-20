"""The CLI commands of the stored credentials: list, show, create, set and describe.

`infrahub_sync/cli.py` calls `register` once, with the helpers that every command shares. This
module imports nothing from `cli.py`, so the two modules do not import each other.

To add a `credentials` subcommand, define a function decorated with `@credentials_app.command(...)`
in this module.

The value of a credential is never a command argument or an option value. `_read_values` reads it
from a prompt without echo, or from standard input with `--token-stdin` or `--password-stdin`. No
command prints a value, and no error line holds one.
"""

from __future__ import annotations

import json
import re
import sys
from typing import TYPE_CHECKING, NoReturn

import typer

from infrahub_sync.client import ClientInputError
from infrahub_sync.client.models import CREDENTIAL_NAME_PATTERN

if TYPE_CHECKING:
    from infrahub_sync.cli_accounts import CommandSupport
    from infrahub_sync.client import CredentialResource

# Fixed text for each refusal code of the credential routes. The client keeps no message text of
# an answer (see the secret-redaction guideline), so the CLI prints its own text for each code.
CREDENTIAL_ERROR_MESSAGES = {
    "credential-not-found": "no stored credential has this name",
    "credential-exists": "a stored credential already has this name; `infrahub-sync credentials set` replaces its value",
    "credential-fields-invalid": (
        "a credential holds a token, a username and a password, or a username and a token; each value is "
        "not empty, is within the length limit of the Sync API and holds no control character"
    ),
    "credential-key-not-configured": (
        "INFRAHUB_SYNC_ENCRYPTION_KEY is not set, so the Sync API cannot store a credential value; "
        "the operator of the Sync API sets it"
    ),
}
_JSON_HELP = "Print the answer of the Sync API as JSON. It holds the names of the fields and no value."
_NAME_HELP = "Name of the credential, as a configuration package names it in `credential:`."
_USERNAME_HELP = (
    "Username of the credential. A username is not a secret, so it is an option value. With it, the "
    "command reads a password, or a token with --token-stdin."
)
_TOKEN_STDIN_HELP = "Read the token from standard input instead of a prompt."
_PASSWORD_STDIN_HELP = "Read the password from standard input instead of a prompt. Requires --username."
_ARGUMENT_EXIT = 2
_NAME_ARG = "name"
_CREDENTIAL_NAME = re.compile(CREDENTIAL_NAME_PATTERN)

credentials_app = typer.Typer(
    help="List and view stored credentials. Creating and changing one needs an administrator. No command prints a value."
)


class _Registered:
    """Hold the helpers that `register` received, for the commands of this module."""

    support: CommandSupport | None = None


def _support() -> CommandSupport:
    support = _Registered.support
    if support is None:
        message = "the credential commands are not registered"
        raise RuntimeError(message)
    return support


def _fail(message: str) -> NoReturn:
    """Print one error line and end a command that sent no request. The line never holds a value."""
    typer.echo(f"error: {message}", err=True)
    raise typer.Exit(_ARGUMENT_EXIT)


def _require_name(name: str) -> None:
    """Refuse a name that no stored credential can have, before a value is asked for.

    Raises:
        ClientInputError: with the argument ``name``, as the shared client refuses the same name.
    """
    if _CREDENTIAL_NAME.fullmatch(name) is None:
        raise ClientInputError(_NAME_ARG)


def _stdin_value(flag: str) -> str:
    """Read one line of standard input without its line ending. The value keeps every other character."""
    line = sys.stdin.readline()
    line = line.removesuffix("\n")
    line = line.removesuffix("\r")
    if not line:
        _fail(f"standard input holds no value; with {flag}, give the value on one line of standard input")
    return line


def _prompt_value(label: str, flag: str) -> str:
    """Ask for a value once, without echo, as `login` asks for a password."""
    if not _support().stdin_is_interactive():
        _fail(
            f"standard input is not a terminal, so no {label.lower()} can be asked; "
            f"pass {flag} and give the {label.lower()} on standard input"
        )
    return typer.prompt(label, hide_input=True, err=True)


def _check_inputs(username: str | None, *, token_stdin: bool, password_stdin: bool) -> None:
    """Refuse a combination of options that names no allowed set of fields, before any request."""
    if token_stdin and password_stdin:
        _fail("pass only one of --token-stdin and --password-stdin: standard input holds one value")
    if password_stdin and username is None:
        _fail("--password-stdin needs --username: a credential with a password also has a username")


def _read_values(username: str | None, *, token_stdin: bool, password_stdin: bool) -> dict[str, str]:
    """Return the fields of the credential: a token, a username and a password, or a username and a token.

    Without a username the value is a token. With a username the value is a password, unless
    ``--token-stdin`` is given.
    """
    if username is None:
        value = _stdin_value("--token-stdin") if token_stdin else _prompt_value("Token", "--token-stdin")
        return {"token": value}
    if token_stdin:
        return {"username": username, "token": _stdin_value("--token-stdin")}
    value = _stdin_value("--password-stdin") if password_stdin else _prompt_value("Password", "--password-stdin")
    return {"username": username, "password": value}


def _credential_fields(credential: CredentialResource) -> tuple[tuple[str, object], ...]:
    return (
        ("credential", credential.name),
        ("description", credential.description),
        ("fields", credential.fields),
        ("revision", credential.revision),
        ("created_by", credential.created_by),
        ("created_at", credential.created_at.isoformat()),
        ("updated_by", credential.updated_by),
        ("updated_at", credential.updated_at.isoformat()),
    )


def _credential_row(credential: CredentialResource) -> str:
    display = _support().display
    columns = (
        credential.name,
        ",".join(credential.fields),
        str(credential.revision),
        credential.updated_by,
        credential.updated_at.isoformat(),
    )
    return "\t".join(display(column) for column in columns)


def _echo_json(document: object) -> None:
    # `json.dumps` escapes control characters, so the output is safe for a terminal as it is.
    typer.echo(json.dumps(document, sort_keys=True, indent=2))


@credentials_app.command("list")
def credentials_list(
    ctx: typer.Context,
    as_json: bool = typer.Option(False, "--json", help=_JSON_HELP),  # noqa: FBT003
) -> None:
    """List stored credentials by name: name, fields, revision, the account that last changed it, and when.

    The fields column holds the names of the fields, such as `token` or `username,password`. No value is shown.
    """
    support = _support()
    with support.client_errors():
        credentials = list(support.client(ctx).iter_credentials())
        if as_json:
            _echo_json([credential.model_dump(mode="json") for credential in credentials])
            return
        for credential in credentials:
            typer.echo(_credential_row(credential))


@credentials_app.command("show")
def credentials_show(
    ctx: typer.Context,
    name: str = typer.Argument(..., help=_NAME_HELP),
    as_json: bool = typer.Option(False, "--json", help=_JSON_HELP),  # noqa: FBT003
) -> None:
    """View one stored credential: its description, the names of its fields, its revision and who changed it.

    No value is shown.
    """
    support = _support()
    with support.client_errors():
        credential = support.client(ctx).get_credential(name)
        if as_json:
            _echo_json(credential.model_dump(mode="json"))
        else:
            support.echo_fields(_credential_fields(credential))


@credentials_app.command("create")
def credentials_create(  # pylint: disable=too-many-positional-arguments
    ctx: typer.Context,
    name: str = typer.Argument(..., help=_NAME_HELP),
    description: str | None = typer.Option(None, "--description", help="What the credential is for."),
    username: str | None = typer.Option(None, "--username", help=_USERNAME_HELP),
    token_stdin: bool = typer.Option(False, "--token-stdin", help=_TOKEN_STDIN_HELP),  # noqa: FBT003
    password_stdin: bool = typer.Option(False, "--password-stdin", help=_PASSWORD_STDIN_HELP),  # noqa: FBT003
) -> None:
    """Create a stored credential with its first value. Needs an administrator.

    Without --username, the command asks for a token without echo, or reads it from standard input
    with --token-stdin. With --username, it asks for a password, or reads it with --password-stdin;
    with --username and --token-stdin, the credential holds a username and a token. The value is
    never an argument, and the command prints the credential without it.
    """
    _check_inputs(username, token_stdin=token_stdin, password_stdin=password_stdin)
    support = _support()
    with support.client_errors():
        client = support.client(ctx)
        _require_name(name)
        values = _read_values(username, token_stdin=token_stdin, password_stdin=password_stdin)
        credential = client.create_credential(name, description=description, **values)
        support.echo_fields(_credential_fields(credential))


@credentials_app.command("set")
def credentials_set(
    ctx: typer.Context,
    name: str = typer.Argument(..., help=_NAME_HELP),
    username: str | None = typer.Option(None, "--username", help=_USERNAME_HELP),
    token_stdin: bool = typer.Option(False, "--token-stdin", help=_TOKEN_STDIN_HELP),  # noqa: FBT003
    password_stdin: bool = typer.Option(False, "--password-stdin", help=_PASSWORD_STDIN_HELP),  # noqa: FBT003
) -> None:
    """Replace the value of a stored credential, and print its new revision. Needs an administrator.

    The new value replaces every field of the credential, so give --username again for a credential
    that has one. The value is read as `credentials create` reads it. Runs that start afterwards use
    the new value.
    """
    _check_inputs(username, token_stdin=token_stdin, password_stdin=password_stdin)
    support = _support()
    with support.client_errors():
        client = support.client(ctx)
        _require_name(name)
        values = _read_values(username, token_stdin=token_stdin, password_stdin=password_stdin)
        credential = client.set_credential_value(name, **values)
        support.echo_fields((("credential", credential.name), ("revision", credential.revision)))


@credentials_app.command("describe")
def credentials_describe(
    ctx: typer.Context,
    name: str = typer.Argument(..., help=_NAME_HELP),
    text: str = typer.Argument(..., help="The new description. It replaces the earlier one."),
) -> None:
    """Change the description of a stored credential. Its value and its revision stay. Needs an administrator."""
    support = _support()
    with support.client_errors():
        credential = support.client(ctx).set_credential_description(name, text)
        support.echo_fields(_credential_fields(credential))


def register(app: typer.Typer, support: CommandSupport) -> None:
    """Add the `credentials` group to the CLI."""
    _Registered.support = support
    app.add_typer(credentials_app, name="credentials")


__all__ = ["CREDENTIAL_ERROR_MESSAGES", "credentials_app", "register"]
