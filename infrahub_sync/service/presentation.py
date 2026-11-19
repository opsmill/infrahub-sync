"""Display names for the account identifiers in the answers of the Sync API.

A record holds an account identifier, never a name. Every JSON answer of the API application
passes through `AccountNameMiddleware`, which shows the account name where a record holds an
identifier. A route therefore needs no step of its own, and an answer replayed from a stored
receipt is presented like an answer that was just built.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers, MutableHeaders

from infrahub_sync.product_store.models import ACCOUNT_ID_PATTERN

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

    from infrahub_sync.product_store import Account

# The keys whose string value names an account, at any depth of a body.
ACCOUNT_FIELDS = frozenset({"actor", "approved_by", "registered_by"})
# The key that holds the related resources of an event, and the kind of an entry that names an account.
RELATED_KEY = "related"
# The key that holds the resource an event is about.
PRIMARY_KEY = "primary_resource"
ACCOUNT_KIND = "account"

AccountReader = Callable[[Sequence[str]], Mapping[str, "Account"]]

_ACCOUNT_ID = re.compile(ACCOUNT_ID_PATTERN)
# Every account identifier starts with these bytes, so a body without them needs no parsing.
_ACCOUNT_ID_MARK = b"acct_"
_Convert = Callable[[str], str]


def deleted_account_placeholder(short_id: str) -> str:
    """Return what every answer shows in place of a deleted account with this short identifier."""
    return f"Deleted account {short_id}"


def display_name(account: Account) -> str:
    """Return the name shown for an account: its name, or the placeholder of a deleted account."""
    if account.deleted_at is not None or account.name is None:
        return deleted_account_placeholder(account.short_id)
    return account.name


def account_identifiers(body: Any) -> set[str]:
    """Return every account identifier that `present_accounts` would replace in this body."""
    found: set[str] = set()

    def collect(value: str) -> str:
        found.add(value)
        return value

    _convert_accounts(body, collect)
    return found


def present_accounts(body: Any, read_accounts: AccountReader) -> Any:
    """Return the body with each account identifier replaced by the display name of its account.

    Only the string value of `actor`, `approved_by` or `registered_by`, and the `id` of a related
    resource or of a primary resource of kind `account`, is replaced, and only when it has the
    form of an account identifier. An identifier that no account has is left as it is. The accounts are read with
    one call; a body that holds no identifier is returned as it is, with no call.
    """
    identifiers = account_identifiers(body)
    if not identifiers:
        return body
    accounts = read_accounts(sorted(identifiers))
    names = {identifier: display_name(account) for identifier, account in accounts.items()}
    return _convert_accounts(body, lambda value: names.get(value, value))


def _convert_accounts(value: Any, convert: _Convert) -> Any:
    """Return a copy of a JSON-like value in which `convert` was applied to every account identifier."""
    if isinstance(value, list):
        return [_convert_accounts(item, convert) for item in value]
    if not isinstance(value, dict):
        return value
    converted: dict[Any, Any] = {}
    for key, item in value.items():
        if key in ACCOUNT_FIELDS and isinstance(item, str):
            converted[key] = _convert_identifier(item, convert)
        elif key == RELATED_KEY and isinstance(item, list):
            converted[key] = [_convert_related(entry, convert) for entry in item]
        elif key == PRIMARY_KEY:
            converted[key] = _convert_related(item, convert)
        else:
            converted[key] = _convert_accounts(item, convert)
    return converted


def _convert_related(entry: Any, convert: _Convert) -> Any:
    """Convert the `id` of a resource of kind `account`; convert any other entry like a body."""
    if isinstance(entry, dict) and entry.get("kind") == ACCOUNT_KIND and isinstance(entry.get("id"), str):
        return {**entry, "id": _convert_identifier(entry["id"], convert)}
    return _convert_accounts(entry, convert)


def _convert_identifier(value: str, convert: _Convert) -> str:
    """Apply `convert` to a value that has the form of an account identifier; return any other value as it is."""
    return convert(value) if _ACCOUNT_ID.fullmatch(value) else value


class AccountNameMiddleware:
    """Show account names in the JSON answers of the API application.

    An answer is held back until its accounts are read, so a failed read reaches the caller as an
    exception before any byte is sent. An answer that is not `application/json`, or that carries a
    `Digest` header (an artifact, whose bytes must match the digest), is passed on unbuffered.
    """

    def __init__(self, app: ASGIApp, read_accounts: AccountReader) -> None:
        self._app = app
        self._read_accounts = read_accounts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Pass the request on and present the body of a JSON answer before it is sent."""
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        held: list[Message] = []
        chunks: list[bytes] = []

        async def send_presented(message: Message) -> None:
            if message["type"] == "http.response.start" and _is_presentable(message):
                held.append(message)
                return
            if not held or message["type"] != "http.response.body":
                await send(message)
                return
            chunks.append(message.get("body", b""))
            if message.get("more_body", False):
                return
            start = held.pop()
            raw = b"".join(chunks)
            body = await self._present(raw)
            if body is not raw:
                start = {**start, "headers": _with_content_length(start, len(body))}
            await send(start)
            await send({"type": "http.response.body", "body": body})

        await self._app(scope, receive, send_presented)

    async def _present(self, raw: bytes) -> bytes:
        """Return the bytes to send: `raw` itself unless an account identifier was replaced."""
        if _ACCOUNT_ID_MARK not in raw:
            return raw
        try:
            body = json.loads(raw)
        except ValueError:
            return raw
        presented = await run_in_threadpool(present_accounts, body, self._read_accounts)
        if presented is body:
            return raw
        return json.dumps(presented, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _is_presentable(start: Message) -> bool:
    """Return whether the answer is JSON that the API built, and not the bytes of an artifact."""
    headers = Headers(raw=start.get("headers", []))
    return headers.get("content-type", "").startswith("application/json") and "digest" not in headers


def _with_content_length(start: Message, length: int) -> list[tuple[bytes, bytes]]:
    """Return the headers of the answer with `Content-Length` set to the length of the new body."""
    headers = MutableHeaders(raw=list(start.get("headers", [])))
    headers["content-length"] = str(length)
    return headers.raw
