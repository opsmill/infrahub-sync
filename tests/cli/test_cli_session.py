"""The session file of the CLI: its place, its mode, its keys, and what a damaged file reads as."""

from __future__ import annotations

import json
import os
import stat
import subprocess  # noqa: S404 - a fresh interpreter shows what an import alone loads.
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import typer

from infrahub_sync import cli_session
from infrahub_sync.cli_session import (
    SESSION_FILE_NAME,
    SavedSession,
    normalize_address,
    read_session,
    remove_session,
    write_session,
)

ADDRESS = "http://127.0.0.1:8001"
OTHER_ADDRESS = "https://sync.example.test"
TOKEN = "ifs_session_canary_1"  # noqa: S105 - a canary that must never be printed.
OTHER_TOKEN = "ifs_session_canary_2"  # noqa: S105 - a canary that must never be printed.
POSIX_ONLY = pytest.mark.skipif(sys.platform == "win32", reason="a Windows file mode holds only the read-only flag")


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _session(token: str = TOKEN, account: str = "alice") -> SavedSession:
    return SavedSession(account=account, token=token)


def test_the_default_directory_is_the_application_directory_of_infrahub_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    # The autouse fixture of this directory replaces `default_directory`; this test needs the real one.
    monkeypatch.undo()

    assert cli_session.default_directory() == Path(typer.get_app_dir("infrahub-sync"))
    assert SESSION_FILE_NAME == "sessions.json"


def test_read_and_write_use_the_default_directory_when_none_is_given(session_directory: Path) -> None:
    write_session(ADDRESS, _session())

    assert (session_directory / SESSION_FILE_NAME).is_file()
    assert read_session(ADDRESS) == _session()


@POSIX_ONLY
def test_the_file_is_created_with_mode_0600_in_a_directory_with_mode_0700(tmp_path: Path) -> None:
    directory = tmp_path / "missing" / "infrahub-sync"
    previous = os.umask(0)
    try:
        write_session(ADDRESS, _session(), directory=directory)
    finally:
        os.umask(previous)

    assert _mode(directory / SESSION_FILE_NAME) == 0o600
    assert _mode(directory) == 0o700
    # The lock file that serializes writes stays, and holds nothing.
    assert sorted(entry.name for entry in directory.iterdir()) == [SESSION_FILE_NAME, f"{SESSION_FILE_NAME}.lock"]
    assert (directory / f"{SESSION_FILE_NAME}.lock").read_bytes() == b""


@POSIX_ONLY
def test_the_file_never_exists_with_a_wider_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[int] = []
    real_replace = Path.replace

    def replace(self: Path, target: Path) -> Path:
        # The temporary file holds the token before it takes the place of the session file.
        seen.append(_mode(self))
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", replace)
    previous = os.umask(0)
    try:
        write_session(ADDRESS, _session(), directory=tmp_path)
    finally:
        os.umask(previous)

    assert seen == [0o600]


@POSIX_ONLY
def test_a_file_with_wider_permissions_is_tightened_on_write(tmp_path: Path) -> None:
    path = tmp_path / SESSION_FILE_NAME
    path.write_text(json.dumps({OTHER_ADDRESS: {"token": OTHER_TOKEN, "account": "bob"}}), encoding="utf-8")
    path.chmod(0o644)

    write_session(ADDRESS, _session(), directory=tmp_path)

    assert _mode(path) == 0o600
    assert read_session(OTHER_ADDRESS, directory=tmp_path) == _session(OTHER_TOKEN, "bob")


def test_two_addresses_are_kept_apart(tmp_path: Path) -> None:
    write_session(ADDRESS, _session(), directory=tmp_path)
    write_session(OTHER_ADDRESS, _session(OTHER_TOKEN, "bob"), directory=tmp_path)

    assert read_session(ADDRESS, directory=tmp_path) == _session()
    assert read_session(OTHER_ADDRESS, directory=tmp_path) == _session(OTHER_TOKEN, "bob")
    document = json.loads((tmp_path / SESSION_FILE_NAME).read_text(encoding="utf-8"))
    assert document == {
        ADDRESS: {"token": TOKEN, "account": "alice"},
        OTHER_ADDRESS: {"token": OTHER_TOKEN, "account": "bob"},
    }


def test_a_write_replaces_the_session_of_the_same_address(tmp_path: Path) -> None:
    write_session(ADDRESS, _session(), directory=tmp_path)
    write_session(ADDRESS + "/", _session(OTHER_TOKEN, "bob"), directory=tmp_path)

    assert read_session(ADDRESS, directory=tmp_path) == _session(OTHER_TOKEN, "bob")
    assert list(json.loads((tmp_path / SESSION_FILE_NAME).read_text(encoding="utf-8"))) == [ADDRESS]


def test_remove_deletes_one_entry_and_reports_whether_it_existed(tmp_path: Path) -> None:
    write_session(ADDRESS, _session(), directory=tmp_path)
    write_session(OTHER_ADDRESS, _session(OTHER_TOKEN, "bob"), directory=tmp_path)

    assert remove_session(ADDRESS, directory=tmp_path) is True
    assert remove_session(ADDRESS, directory=tmp_path) is False
    assert read_session(ADDRESS, directory=tmp_path) is None
    assert read_session(OTHER_ADDRESS, directory=tmp_path) == _session(OTHER_TOKEN, "bob")


def test_remove_with_a_token_keeps_a_session_that_replaced_it(tmp_path: Path) -> None:
    write_session(ADDRESS, _session(OTHER_TOKEN, "bob"), directory=tmp_path)

    assert remove_session(ADDRESS, token=TOKEN, directory=tmp_path) is False
    assert read_session(ADDRESS, directory=tmp_path) == _session(OTHER_TOKEN, "bob")
    assert remove_session(ADDRESS, token=OTHER_TOKEN, directory=tmp_path) is True
    assert read_session(ADDRESS, directory=tmp_path) is None


def test_writes_for_many_addresses_at_the_same_time_keep_every_session(tmp_path: Path) -> None:
    addresses = [f"http://127.0.0.1:{8000 + index}" for index in range(16)]

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda address: write_session(address, _session(), directory=tmp_path), addresses))

    assert all(read_session(address, directory=tmp_path) == _session() for address in addresses)


def test_remove_on_a_missing_file_creates_nothing(tmp_path: Path) -> None:
    directory = tmp_path / "missing"

    assert remove_session(ADDRESS, directory=directory) is False
    assert not directory.exists()


@pytest.mark.parametrize(
    ("address", "key"),
    [
        ("http://127.0.0.1:8001", "http://127.0.0.1:8001"),
        ("http://127.0.0.1:8001/", "http://127.0.0.1:8001"),
        ("http://127.0.0.1:8001///", "http://127.0.0.1:8001"),
        ("http://127.0.0.1:8001/api", "http://127.0.0.1:8001"),
        ("http://127.0.0.1:8001/api/", "http://127.0.0.1:8001"),
        ("  http://127.0.0.1:8001  ", "http://127.0.0.1:8001"),
        ("HTTPS://Sync.Example.TEST", "https://sync.example.test"),
        ("https://sync.example.test/Prefix/api", "https://sync.example.test/Prefix"),
        ("https://sync.example.test/prefix/", "https://sync.example.test/prefix"),
        ("https://sync.example.test:8443", "https://sync.example.test:8443"),
        ("https://sync.example.test?x=1#y", "https://sync.example.test"),
        ("http://[::1]:8001/", "http://[::1]:8001"),
        ("http://127.0.0.1:08001", "http://127.0.0.1:8001"),
        ("", None),
        ("sync.example.test", None),
        ("ftp://sync.example.test", None),
        ("http://", None),
        ("https://user:secret@sync.example.test", None),
        ("https://user@sync.example.test", None),
        ("http://sync.example.test:notaport", None),
    ],
)
def test_the_key_of_an_address(address: str, key: str | None) -> None:
    assert normalize_address(address) == key


def test_addresses_with_different_ports_or_paths_are_different_sync_apis(tmp_path: Path) -> None:
    write_session("http://sync.example.test", _session(), directory=tmp_path)

    assert read_session("http://sync.example.test:8001", directory=tmp_path) is None
    assert read_session("http://sync.example.test/other", directory=tmp_path) is None
    assert read_session("https://sync.example.test", directory=tmp_path) is None
    assert read_session("HTTP://SYNC.example.test/api/", directory=tmp_path) == _session()


def test_an_address_that_is_not_one_has_no_session_and_cannot_be_saved(tmp_path: Path) -> None:
    assert read_session("not an address", directory=tmp_path) is None
    assert remove_session("not an address", directory=tmp_path) is False
    with pytest.raises(ValueError, match="not the address of a Sync API") as raised:
        write_session("not an address", _session(), directory=tmp_path)
    assert TOKEN not in str(raised.value)
    assert not (tmp_path / SESSION_FILE_NAME).exists()


@pytest.mark.parametrize(
    "content",
    [
        pytest.param(None, id="missing"),
        pytest.param("", id="empty"),
        pytest.param("{not json", id="not-json"),
        pytest.param("[]", id="an-array"),
        pytest.param('"text"', id="a-string"),
        pytest.param(json.dumps({ADDRESS: "a-token"}), id="entry-not-an-object"),
        pytest.param(json.dumps({ADDRESS: {"account": "alice"}}), id="entry-without-token"),
        pytest.param(json.dumps({ADDRESS: {"token": "", "account": "alice"}}), id="empty-token"),
        pytest.param(json.dumps({ADDRESS: {"token": 5, "account": "alice"}}), id="token-not-text"),
        pytest.param(json.dumps({ADDRESS: {"token": "t", "account": None}}), id="account-not-text"),
        pytest.param("\udcff".encode("utf-8", "surrogateescape").decode("latin-1"), id="not-utf-8-text"),
    ],
)
def test_a_file_that_cannot_be_used_reads_as_no_saved_session(tmp_path: Path, content: str | None) -> None:
    if content is not None:
        (tmp_path / SESSION_FILE_NAME).write_text(content, encoding="latin-1")

    assert read_session(ADDRESS, directory=tmp_path) is None


def test_a_file_that_cannot_be_opened_reads_as_no_saved_session(tmp_path: Path) -> None:
    # A directory in the place of the file: opening it fails on every platform, for every user.
    (tmp_path / SESSION_FILE_NAME).mkdir()

    assert read_session(ADDRESS, directory=tmp_path) is None


def test_a_write_over_a_damaged_file_keeps_only_the_new_session(tmp_path: Path) -> None:
    (tmp_path / SESSION_FILE_NAME).write_text("{not json", encoding="utf-8")

    write_session(ADDRESS, _session(), directory=tmp_path)

    assert json.loads((tmp_path / SESSION_FILE_NAME).read_text(encoding="utf-8")) == {
        ADDRESS: {"token": TOKEN, "account": "alice"}
    }


def test_a_write_that_fails_leaves_the_previous_file_whole_and_no_partial_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_session(ADDRESS, _session(), directory=tmp_path)
    before = (tmp_path / SESSION_FILE_NAME).read_bytes()

    def fail(*_args: object, **_kwargs: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(cli_session.json, "dump", fail)

    with pytest.raises(OSError, match="No space left"):
        write_session(OTHER_ADDRESS, _session(OTHER_TOKEN, "bob"), directory=tmp_path)

    assert (tmp_path / SESSION_FILE_NAME).read_bytes() == before
    assert sorted(entry.name for entry in tmp_path.iterdir()) == [SESSION_FILE_NAME, f"{SESSION_FILE_NAME}.lock"]


def test_a_replace_that_fails_leaves_the_previous_file_whole_and_no_partial_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_session(ADDRESS, _session(), directory=tmp_path)
    before = (tmp_path / SESSION_FILE_NAME).read_bytes()

    def fail(_self: Path, _target: Path) -> None:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "replace", fail)

    with pytest.raises(PermissionError):
        write_session(ADDRESS, _session(OTHER_TOKEN, "bob"), directory=tmp_path)

    assert (tmp_path / SESSION_FILE_NAME).read_bytes() == before
    assert sorted(entry.name for entry in tmp_path.iterdir()) == [SESSION_FILE_NAME, f"{SESSION_FILE_NAME}.lock"]


def test_the_printed_form_of_a_saved_session_holds_no_token() -> None:
    session = _session()

    for printed in (repr(session), str(session), f"{session}", repr([session])):
        assert TOKEN not in printed
        assert "alice" in printed


def test_the_cli_and_its_session_module_load_no_service_code() -> None:
    script = (
        "import sys, infrahub_sync.cli, infrahub_sync.cli_session, infrahub_sync.cli_accounts\n"
        "loaded = sorted(name for name in sys.modules if name.startswith(('infrahub_sync.service', "
        "'infrahub_sync.product_store', 'fastapi', 'psycopg', 'uvicorn')))\n"
        "print(loaded)\n"
    )

    result = subprocess.run([sys.executable, "-c", script], check=True, capture_output=True, text=True)  # noqa: S603

    assert result.stdout.strip() == "[]"
