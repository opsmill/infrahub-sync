"""The `credentials` command group, and the rule that no output of it holds a credential value."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, cast

import httpx
import pytest
from typer.main import get_command
from typer.testing import CliRunner

from infrahub_sync.cli import _error_fields, app  # noqa: PLC2701 - the error renderer under test is private.
from infrahub_sync.cli_session import SavedSession, write_session
from infrahub_sync.client import APIError

if TYPE_CHECKING:
    from collections.abc import Iterator

    from typer.testing import Result

    from tests.cli.conftest import FakeSyncAPI

URL = "http://127.0.0.1:8001"
SESSION_TOKEN = "ifs_session_canary"  # noqa: S105 - a canary that must never be printed.
MARKER = "marker-7f3a9c51-credential-value"  # the value of a credential: no output may hold it.
MARKER_DIGEST = hashlib.sha256(MARKER.encode()).hexdigest()
NOW = datetime(2026, 11, 20, 10, tzinfo=timezone.utc)
NOW_JSON = "2026-11-20T10:00:00Z"
RUNNER = CliRunner()
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _credential(name: str = "netbox-lab", **extra: object) -> dict[str, object]:
    return {
        "name": name,
        "description": "NetBox lab, read-only token",
        "fields": ["token"],
        "revision": 1,
        "created_by": "alice",
        "created_at": NOW.isoformat(),
        "updated_by": "bob",
        "updated_at": NOW.isoformat(),
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


def _assert_no_value(result: Result) -> None:
    """Fail without printing the output when it holds the value or the session token."""
    for secret in (MARKER, SESSION_TOKEN):
        leaked = secret in result.output or secret in result.stdout or secret in result.stderr
        assert not leaked, "a command output holds a value that must never be printed"


def _body(sync_api: FakeSyncAPI) -> dict[str, Any]:
    """Return the JSON document of the last request."""
    body = sync_api.body()
    assert isinstance(body, dict)
    return cast("dict[str, Any]", body)


def _digests(body: dict[str, Any]) -> dict[str, str]:
    """Return the SHA-256 digest of each field of a request body, so a failure prints no value."""
    return {name: hashlib.sha256(str(value).encode()).hexdigest() for name, value in body.items()}


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


# --- list and show -------------------------------------------------------------------------


def test_list_prints_one_row_for_each_credential(sync_api: FakeSyncAPI) -> None:
    sync_api.answers["GET /credentials"] = httpx.Response(
        200,
        json={
            "items": [
                _credential("infrahub-token", revision=3),
                _credential("netbox-lab", fields=["username", "password"], updated_by="Deleted account 1a2b3c4d"),
            ],
            "next_offset": None,
        },
    )

    result = _invoke("credentials", "list")

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        f"infrahub-token\ttoken\t3\tbob\t{NOW.isoformat()}",
        f"netbox-lab\tusername,password\t1\tDeleted account 1a2b3c4d\t{NOW.isoformat()}",
    ]
    assert sync_api.routes == ["GET /credentials"]
    assert sync_api.bearers == [f"Bearer {SESSION_TOKEN}"]
    _assert_no_value(result)


def test_list_reads_every_page(sync_api: FakeSyncAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    pages = {
        "0": {"items": [_credential("a-first")], "next_offset": 1},
        "1": {"items": [_credential("b-second")], "next_offset": None},
    }
    answer_one_route = type(sync_api).__call__

    def paged(self: FakeSyncAPI, request: httpx.Request) -> httpx.Response:
        if not request.url.path.endswith("/credentials"):
            return answer_one_route(self, request)
        self.requests.append(request)
        return httpx.Response(200, json=pages[request.url.params["offset"]])

    monkeypatch.setattr(type(sync_api), "__call__", paged)

    result = _invoke("credentials", "list")

    assert result.exit_code == 0, result.output
    assert [line.split("\t")[0] for line in result.stdout.splitlines()] == ["a-first", "b-second"]
    assert [request.url.params["offset"] for request in sync_api.requests] == ["0", "1"]


def test_list_prints_json_with_the_option(sync_api: FakeSyncAPI) -> None:
    sync_api.answers["GET /credentials"] = httpx.Response(200, json={"items": [_credential()], "next_offset": None})

    result = _invoke("credentials", "list", "--json")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == [
        {
            "name": "netbox-lab",
            "description": "NetBox lab, read-only token",
            "fields": ["token"],
            "revision": 1,
            "created_by": "alice",
            "created_at": NOW_JSON,
            "updated_by": "bob",
            "updated_at": NOW_JSON,
        }
    ]


def test_list_makes_a_control_character_visible(sync_api: FakeSyncAPI) -> None:
    sync_api.answers["GET /credentials"] = httpx.Response(
        200, json={"items": [_credential(updated_by="bob\x1b[2Jx")], "next_offset": None}
    )

    result = _invoke("credentials", "list")

    assert result.exit_code == 0, result.output
    assert "\x1b" not in result.stdout
    assert "bob\\x1b[2Jx" in result.stdout


def test_show_prints_the_resource(sync_api: FakeSyncAPI) -> None:
    sync_api.answers["GET /credentials/netbox-lab"] = httpx.Response(
        200, json=_credential(fields=["username", "password"], revision=4)
    )

    result = _invoke("credentials", "show", "netbox-lab")

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        "credential: netbox-lab",
        "description: NetBox lab, read-only token",
        "fields: username,password",
        "revision: 4",
        "created_by: alice",
        f"created_at: {NOW.isoformat()}",
        "updated_by: bob",
        f"updated_at: {NOW.isoformat()}",
    ]
    assert sync_api.routes == ["GET /credentials/netbox-lab"]
    _assert_no_value(result)


def test_show_prints_json_with_the_option(sync_api: FakeSyncAPI) -> None:
    sync_api.answers["GET /credentials/netbox-lab"] = httpx.Response(200, json=_credential())

    result = _invoke("credentials", "show", "netbox-lab", "--json")

    assert result.exit_code == 0, result.output
    document = json.loads(result.stdout)
    assert document["name"] == "netbox-lab"
    assert document["fields"] == ["token"]
    assert set(document) == {
        "name",
        "description",
        "fields",
        "revision",
        "created_by",
        "created_at",
        "updated_by",
        "updated_at",
    }


def test_show_of_a_credential_that_does_not_exist_exits_1_with_a_fixed_message(sync_api: FakeSyncAPI) -> None:
    sync_api.answers["GET /credentials/absent"] = sync_api.error(404, "credential-not-found", f"text {MARKER}")

    result = _invoke("credentials", "show", "absent")

    assert result.exit_code == 1
    assert "code: credential-not-found" in result.stderr
    assert "message: no stored credential has this name" in result.stderr
    assert not result.stdout
    _assert_no_value(result)


@pytest.mark.parametrize(
    "command",
    [
        ("credentials", "show", "Not_A_Name"),
        ("credentials", "describe", "Not_A_Name", "text"),
        ("credentials", "create", "Not_A_Name", "--token-stdin"),
        ("credentials", "set", "Not_A_Name", "--token-stdin"),
    ],
)
def test_a_name_that_no_credential_can_have_exits_2_and_sends_no_request(
    sync_api: FakeSyncAPI, command: tuple[str, ...]
) -> None:
    result = _invoke(*command, stdin=f"{MARKER}\n")

    assert result.exit_code == 2
    assert "error: client-input" in result.stderr
    assert "argument: name" in result.stderr
    assert sync_api.routes == []
    _assert_no_value(result)


@pytest.mark.usefixtures("terminal")
@pytest.mark.parametrize("command", ["create", "set"])
def test_a_name_that_no_credential_can_have_is_refused_before_the_value_is_asked(
    sync_api: FakeSyncAPI, command: str
) -> None:
    result = _invoke("credentials", command, "Not_A_Name", stdin=f"{MARKER}\n")

    assert result.exit_code == 2
    assert "argument: name" in result.stderr
    assert "Token" not in result.stderr
    assert sync_api.routes == []


def test_the_fixed_text_of_a_credential_route_is_not_printed_for_the_same_code_at_the_start_of_a_run() -> None:
    # `plan`, `sync`, `apply`, `verify`, `approve` and `runs resume` answer `422 credential-not-found`
    # when a configuration names a credential that does not exist: no name was typed.
    _label, at_run_start = _error_fields(APIError(422, "credential-not-found"))
    _label, at_credential_route = _error_fields(APIError(404, "credential-not-found"))

    assert "message" not in dict(at_run_start)
    assert dict(at_credential_route)["message"] == "no stored credential has this name"


def test_the_text_of_a_missing_encryption_key_holds_for_a_change_and_for_the_start_of_a_run() -> None:
    _label, fields = _error_fields(APIError(503, "credential-key-not-configured"))

    message = str(dict(fields)["message"])
    assert "INFRAHUB_SYNC_ENCRYPTION_KEY is not set in the Sync API" in message
    assert "store or read" in message


# --- create --------------------------------------------------------------------------------

# The options, the label of the prompt when the value comes from a prompt, and the fields sent.
_INPUTS = [
    pytest.param((), "Token", {"token"}, id="prompt-token"),
    pytest.param(("--token-stdin",), None, {"token"}, id="token-stdin"),
    pytest.param(("--username", "svc-sync"), "Password", {"username", "password"}, id="username-prompt-password"),
    pytest.param(("--username", "svc-sync", "--password-stdin"), None, {"username", "password"}, id="password-stdin"),
    pytest.param(("--username", "svc-sync", "--token-stdin"), None, {"username", "token"}, id="username-token-stdin"),
]


def _assert_value_sent(body: dict[str, Any], fields: set[str]) -> None:
    """Check the fields of a request body by digest: the value as given, and the username when one is sent."""
    digests = _digests({name: body[name] for name in body if name in {"username", "password", "token"}})
    expected = dict.fromkeys(fields - {"username"}, MARKER_DIGEST)
    if "username" in fields:
        expected["username"] = _digest("svc-sync")
    assert digests == expected


@pytest.mark.parametrize(("options", "label", "fields"), _INPUTS)
def test_create_reads_the_value_and_prints_the_resource_without_it(
    sync_api: FakeSyncAPI,
    monkeypatch: pytest.MonkeyPatch,
    options: tuple[str, ...],
    label: str | None,
    fields: set[str],
) -> None:
    if label is not None:
        monkeypatch.setattr("infrahub_sync.cli._stdin_is_interactive", lambda: True)
    sync_api.answers["POST /credentials"] = httpx.Response(201, json=_credential(fields=sorted(fields)))

    result = _invoke("credentials", "create", "netbox-lab", *options, stdin=f"{MARKER}\n")

    assert result.exit_code == 0, "credentials create failed"
    assert sync_api.routes == ["POST /credentials"]
    body = _body(sync_api)
    assert body["name"] == "netbox-lab"
    assert "description" not in body
    _assert_value_sent(body, fields)
    assert sync_api.requests[-1].headers.get("Idempotency-Key")
    assert "credential: netbox-lab" in result.stdout
    assert "revision: 1" in result.stdout
    assert f"fields: {','.join(sorted(fields))}" in result.stdout
    if label is not None:
        # The question is asked on standard error, so standard output holds the resource only.
        assert label in result.stderr
        assert label not in result.stdout
    _assert_no_value(result)


def test_create_sends_the_description(sync_api: FakeSyncAPI) -> None:
    sync_api.answers["POST /credentials"] = httpx.Response(201, json=_credential(description="NetBox lab"))

    result = _invoke(
        "credentials", "create", "netbox-lab", "--description", "NetBox lab", "--token-stdin", stdin=f"{MARKER}\n"
    )

    assert result.exit_code == 0, "credentials create failed"
    body = _body(sync_api)
    assert body["description"] == "NetBox lab"
    assert "description: NetBox lab" in result.stdout
    _assert_no_value(result)


@pytest.mark.parametrize("line_ending", ["\n", "\r\n", ""])
def test_a_value_on_standard_input_loses_its_line_ending_only(sync_api: FakeSyncAPI, line_ending: str) -> None:
    value = f"  {MARKER} with spaces  "
    sync_api.answers["POST /credentials"] = httpx.Response(201, json=_credential())

    result = _invoke("credentials", "create", "netbox-lab", "--token-stdin", stdin=f"{value}{line_ending}")

    assert result.exit_code == 0, "credentials create failed"
    assert _digests(_body(sync_api))["token"] == _digest(value)
    _assert_no_value(result)


@pytest.mark.parametrize("command", ["create", "set"])
@pytest.mark.parametrize(
    ("options", "stdin", "text"),
    [
        pytest.param(("--password-stdin",), f"{MARKER}\n", "--username", id="password-without-username"),
        pytest.param(
            ("--username", "svc-sync", "--token-stdin", "--password-stdin"),
            f"{MARKER}\n",
            "only one of --token-stdin and --password-stdin",
            id="both-flags",
        ),
        pytest.param(("--token-stdin",), "", "standard input holds no value", id="empty-stdin-token"),
        pytest.param(
            ("--username", "svc-sync", "--password-stdin"), "\n", "standard input holds no value", id="empty-stdin"
        ),
        pytest.param((), f"{MARKER}\n", "pass --token-stdin", id="no-terminal-token"),
        pytest.param(("--username", "svc-sync"), f"{MARKER}\n", "pass --password-stdin", id="no-terminal-password"),
    ],
)
def test_an_input_that_cannot_give_a_value_exits_2_and_sends_no_request(
    sync_api: FakeSyncAPI, command: str, options: tuple[str, ...], stdin: str, text: str
) -> None:
    result = _invoke("credentials", command, "netbox-lab", *options, stdin=stdin)

    assert result.exit_code == 2
    assert result.stderr.startswith("error: ")
    assert text in result.stderr
    assert sync_api.routes == []
    assert not result.stdout
    _assert_no_value(result)


@pytest.mark.parametrize(
    ("status", "code", "text"),
    [
        (403, "forbidden", None),
        (409, "credential-exists", "a stored credential already has this name"),
        (422, "credential-fields-invalid", "a credential holds a token, a username and a password"),
        (503, "credential-key-not-configured", "INFRAHUB_SYNC_ENCRYPTION_KEY"),
    ],
)
def test_a_refused_creation_exits_1_and_prints_no_text_of_the_answer(
    sync_api: FakeSyncAPI, status: int, code: str, text: str | None
) -> None:
    # The answer repeats the value, which no real answer does: the CLI prints its own text only.
    sync_api.answers["POST /credentials"] = sync_api.error(status, code, f"refused: {MARKER}")

    result = _invoke("credentials", "create", "netbox-lab", "--token-stdin", stdin=f"{MARKER}\n")

    assert result.exit_code == 1
    assert f"status: {status}" in result.stderr
    assert f"code: {code}" in result.stderr
    if text is None:
        assert "message:" not in result.stderr
    else:
        assert text in result.stderr
    assert not result.stdout
    _assert_no_value(result)


def test_a_sync_api_that_does_not_answer_exits_1_without_the_value(
    sync_api: FakeSyncAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    answer_one_route = type(sync_api).__call__

    def refuse(self: FakeSyncAPI, request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            # The version check passes, so the request that carries the value is the one that fails.
            return answer_one_route(self, request)
        self.requests.append(request)
        message = f"connection refused while sending {MARKER}"
        raise httpx.ConnectError(message, request=request)

    monkeypatch.setattr(type(sync_api), "__call__", refuse)

    result = _invoke("credentials", "create", "netbox-lab", "--token-stdin", stdin=f"{MARKER}\n")

    assert result.exit_code == 1
    assert "error: transport" in result.stderr
    assert sync_api.routes == ["POST /credentials"]
    assert _digests(_body(sync_api))["token"] == MARKER_DIGEST
    _assert_no_value(result)


# --- set -----------------------------------------------------------------------------------


@pytest.mark.parametrize(("options", "label", "fields"), _INPUTS)
def test_set_replaces_the_value_and_prints_the_new_revision(
    sync_api: FakeSyncAPI,
    monkeypatch: pytest.MonkeyPatch,
    options: tuple[str, ...],
    label: str | None,
    fields: set[str],
) -> None:
    if label is not None:
        monkeypatch.setattr("infrahub_sync.cli._stdin_is_interactive", lambda: True)
    sync_api.answers["PUT /credentials/netbox-lab/value"] = httpx.Response(
        200, json=_credential(fields=sorted(fields), revision=4)
    )

    result = _invoke("credentials", "set", "netbox-lab", *options, stdin=f"{MARKER}\n")

    assert result.exit_code == 0, "credentials set failed"
    assert sync_api.routes == ["PUT /credentials/netbox-lab/value"]
    body = _body(sync_api)
    assert set(body) == fields
    _assert_value_sent(body, fields)
    assert sync_api.requests[-1].headers.get("Idempotency-Key")
    # The test runner writes the line ending of a hidden prompt on standard output; a terminal does not.
    assert [line for line in result.stdout.splitlines() if line.strip()] == ["credential: netbox-lab", "revision: 4"]
    if label is not None:
        assert label in result.stderr
    _assert_no_value(result)


@pytest.mark.parametrize(
    ("status", "code", "text"),
    [
        (403, "forbidden", None),
        (404, "credential-not-found", "no stored credential has this name"),
        (422, "credential-fields-invalid", "a credential holds a token, a username and a password"),
    ],
)
def test_a_refused_change_of_value_exits_1_and_prints_no_text_of_the_answer(
    sync_api: FakeSyncAPI, status: int, code: str, text: str | None
) -> None:
    sync_api.answers["PUT /credentials/netbox-lab/value"] = sync_api.error(status, code, f"refused: {MARKER}")

    result = _invoke("credentials", "set", "netbox-lab", "--token-stdin", stdin=f"{MARKER}\n")

    assert result.exit_code == 1
    assert f"code: {code}" in result.stderr
    if text is not None:
        assert text in result.stderr
    assert not result.stdout
    _assert_no_value(result)


# --- describe ------------------------------------------------------------------------------


def test_describe_sends_the_description_only_and_prints_the_resource(sync_api: FakeSyncAPI) -> None:
    sync_api.answers["PATCH /credentials/netbox-lab"] = httpx.Response(
        200, json=_credential(description="NetBox lab, read-write token", revision=2)
    )

    result = _invoke("credentials", "describe", "netbox-lab", "NetBox lab, read-write token")

    assert result.exit_code == 0, result.output
    assert sync_api.routes == ["PATCH /credentials/netbox-lab"]
    assert sync_api.body() == {"description": "NetBox lab, read-write token"}
    assert sync_api.requests[-1].headers.get("Idempotency-Key")
    assert "description: NetBox lab, read-write token" in result.stdout
    assert "revision: 2" in result.stdout


def test_describe_without_a_text_exits_2_and_sends_no_request(sync_api: FakeSyncAPI) -> None:
    result = _invoke("credentials", "describe", "netbox-lab")

    assert result.exit_code == 2
    assert "Missing argument" in _ANSI.sub("", result.stderr)
    assert sync_api.routes == []


def test_a_refused_description_exits_1(sync_api: FakeSyncAPI) -> None:
    sync_api.answers["PATCH /credentials/absent"] = sync_api.error(404, "credential-not-found")

    result = _invoke("credentials", "describe", "absent", "text")

    assert result.exit_code == 1
    assert "code: credential-not-found" in result.stderr
    assert "message: no stored credential has this name" in result.stderr


# --- the surface ---------------------------------------------------------------------------


def _commands(command: Any, path: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], Any]]:  # noqa: ANN401
    """Yield every command of the application with its path, groups walked through."""
    children = getattr(command, "commands", None)
    if children is None:
        yield path, command
        return
    for name, child in children.items():
        yield from _commands(child, (*path, name))


def _help(*args: str) -> str:
    result = RUNNER.invoke(app, [*args, "--help"], env={"NO_COLOR": "1", "COLUMNS": "200"})
    assert result.exit_code == 0, result.output
    return _ANSI.sub("", result.output)


def test_every_credential_command_of_this_group_is_listed() -> None:
    paths = {" ".join(path) for path, _command in _commands(get_command(app))}

    assert {
        "credentials list",
        "credentials show",
        "credentials create",
        "credentials set",
        "credentials describe",
    } <= paths


def test_the_root_help_lists_the_credentials_group() -> None:
    commands = {line.split()[1] for line in _help().splitlines() if line.startswith("│ ") and len(line.split()) > 2}

    assert "credentials" in commands


@pytest.mark.parametrize(
    ("command", "options"),
    [
        (("credentials", "list"), ("--json",)),
        (("credentials", "show"), ("--json",)),
        (("credentials", "create"), ("--description", "--username", "--token-stdin", "--password-stdin")),
        (("credentials", "set"), ("--username", "--token-stdin", "--password-stdin")),
    ],
)
def test_each_command_documents_its_options_and_takes_no_value(
    command: tuple[str, ...], options: tuple[str, ...]
) -> None:
    text = _help(*command)

    for option in options:
        assert option in text
    assert "--password " not in text
    assert "--token " not in text
