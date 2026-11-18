"""The session file of the CLI: the session token that `infrahub-sync login` saves for each Sync API address.

The file is ``sessions.json`` in the directory that ``typer.get_app_dir("infrahub-sync")`` returns.
It holds one JSON object. Each key is the normalized address of a Sync API (see
``normalize_address``) and each value holds the session token and the account name:

    {"http://127.0.0.1:8001": {"token": "<session token>", "account": "alice"}}

The directory is created with mode ``0700`` and the file with mode ``0600``. A write goes to a
temporary file in the same directory, created with mode ``0600``, which then replaces the session
file, so an interrupted write leaves the previous file whole.

A file that is missing, empty, not JSON or of another shape reads as "no saved session". No
function of this module prints or logs the content of the file.

On Windows a file mode holds only the read-only flag, so the modes above have no full meaning there.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import typer

APP_NAME = "infrahub-sync"
SESSION_FILE_NAME = "sessions.json"
_DIRECTORY_MODE = 0o700
_FILE_MODE = 0o600
_TOKEN_KEY = "token"
_ACCOUNT_KEY = "account"
_API_SUFFIX = "/api"


@dataclass(frozen=True)
class SavedSession:
    """One saved session: the account name and the session token. Its printed form holds no token."""

    account: str
    token: str

    def __repr__(self) -> str:
        """Show the account name only, so that no log line or traceback holds the session token."""
        return f"SavedSession(account={self.account!r})"


def default_directory() -> Path:
    """Return the directory of the session file for the user who runs the CLI."""
    return Path(typer.get_app_dir(APP_NAME))


def normalize_address(address: str) -> str | None:
    """Return the key of a Sync API address in the session file, or nothing for an address that is not one.

    Two addresses that name the same Sync API get the same key:

    - the scheme and the host are lower-cased;
    - trailing slashes are removed, then one trailing ``/api`` path segment, which the client adds itself;
    - a query string and a fragment are dropped.

    The port is kept as written: ``http://host`` and ``http://host:80`` are two keys. An address
    without an ``http`` or ``https`` scheme, without a host, or with a user name or a password
    has no key.
    """
    try:
        parts = urlsplit(address.strip())
        host = parts.hostname
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"} or not host or parts.username is not None or parts.password is not None:
        return None
    path = parts.path.rstrip("/")
    if path.endswith(_API_SUFFIX):
        path = path[: -len(_API_SUFFIX)].rstrip("/")
    authority = f"[{host}]" if ":" in host else host
    if port is not None:
        authority = f"{authority}:{port}"
    return f"{scheme}://{authority}{path}"


def _session_file(directory: Path | None) -> Path:
    return (default_directory() if directory is None else directory) / SESSION_FILE_NAME


def _read_all(path: Path) -> dict[str, dict[str, str]]:
    """Return every well-formed entry of the session file; a file that cannot be read holds none."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(document, dict):
        return {}
    entries: dict[str, dict[str, str]] = {}
    for address, entry in document.items():
        if not isinstance(entry, dict):
            continue
        token = entry.get(_TOKEN_KEY)
        account = entry.get(_ACCOUNT_KEY)
        if isinstance(token, str) and token and isinstance(account, str):
            entries[address] = {_TOKEN_KEY: token, _ACCOUNT_KEY: account}
    return entries


def _write_all(path: Path, entries: dict[str, dict[str, str]]) -> None:
    """Replace the session file with ``entries`` through a temporary file created with mode ``0600``."""
    path.parent.mkdir(mode=_DIRECTORY_MODE, parents=True, exist_ok=True)
    # `mkstemp` creates the file with mode 0600, whatever the umask, so no other user can read
    # the token at any time. `os.replace` then gives the session file that mode too, which also
    # tightens a session file that had wider permissions.
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{SESSION_FILE_NAME}.", suffix=".tmp")
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(entries, stream, sort_keys=True, indent=2)
            stream.write("\n")
        temporary.chmod(_FILE_MODE)
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def read_session(address: str, *, directory: Path | None = None) -> SavedSession | None:
    """Return the session saved for a Sync API address, or nothing when the file holds none."""
    key = normalize_address(address)
    if key is None:
        return None
    entry = _read_all(_session_file(directory)).get(key)
    if entry is None:
        return None
    return SavedSession(account=entry[_ACCOUNT_KEY], token=entry[_TOKEN_KEY])


def write_session(address: str, session: SavedSession, *, directory: Path | None = None) -> None:
    """Save the session of a Sync API address, in place of the one the file held for that address.

    Raises:
        ValueError: when the address is not the address of a Sync API.
        OSError: when the directory or the file cannot be written.
    """
    key = normalize_address(address)
    if key is None:
        message = "the address is not the address of a Sync API"
        raise ValueError(message)
    path = _session_file(directory)
    entries = _read_all(path)
    entries[key] = {_TOKEN_KEY: session.token, _ACCOUNT_KEY: session.account}
    _write_all(path, entries)


def remove_session(address: str, *, directory: Path | None = None) -> bool:
    """Remove the session saved for a Sync API address. Return whether the file held one.

    Raises:
        OSError: when the file cannot be written.
    """
    key = normalize_address(address)
    path = _session_file(directory)
    entries = _read_all(path)
    if key is None or key not in entries:
        return False
    del entries[key]
    _write_all(path, entries)
    return True
