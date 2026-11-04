"""Tell a missing permission from a refused token in the errors Infrahub returns.

infrahub-sdk 1.23.2 raises `AuthenticationError` for both HTTP 401 and HTTP 403 and keeps only
the server's message on it. It raises it `from` the `httpx.HTTPStatusError`, so the status is
still on `__cause__.response.status_code`. A REST read such as the schema read raises the
`httpx.HTTPStatusError` itself. A denial inside a GraphQL resolver comes back with HTTP 200 and
the SDK raises `GraphQLError`.

`classify_infrahub_error` reads those three shapes and returns one classification, so every
place that reports an Infrahub failure records a 403 as a missing permission and only a 401 as
a refused token.

The description quotes only the permission text Infrahub names, or, for a 403 with another
message, that message with control characters removed and its length capped. It never quotes
the request, its headers, the GraphQL query or its variables, so the token is never in it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import httpx
from infrahub_sdk.exceptions import AuthenticationError, GraphQLError

if TYPE_CHECKING:
    from collections.abc import Iterator

REFUSED_TOKEN_STATUS = 401
MISSING_PERMISSION_STATUS = 403

# The prefix every permission denial of Infrahub starts with
# (`backend/infrahub/permissions/manager.py`, `raise_for_permission(s)`).
_DENIAL_PREFIX = "You do not have"
_ONE_PERMISSION = re.compile(r"^You do not have the following permission: (\S+)$")
_ONE_OF_PERMISSIONS = re.compile(r"^You do not have one of the following permissions: (.+)$")
# A permission as Infrahub renders it, for example `global:edit_default_branch:allow_all` or
# `object:*:Core:Account:view:allow_all`. Anything else is quoted as a server message instead.
_PERMISSION = re.compile(r"^[A-Za-z0-9_:*.\-]{1,200}$")
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_MAX_QUOTED_MESSAGE = 300


@dataclass(frozen=True, slots=True)
class InfrahubErrorClassification:
    """What an Infrahub error means for the import account.

    `problem` is `refused_token` for an HTTP 401, or an `AuthenticationError` with no HTTP
    cause, and `missing_permission` for an HTTP 403 or a GraphQL permission denial.
    `permissions` holds the permission Infrahub names, or each one when Infrahub names
    several of which the account needs one. `server_message` is set instead when a denial
    names no permission in the expected form.
    """

    problem: Literal["refused_token", "missing_permission"]
    error_type: str
    http_status: int | None = None
    permissions: tuple[str, ...] = ()
    server_message: str | None = None

    def describe(self) -> str:
        """Return the cause a run records, without the token or any request content."""
        context = self.error_type if self.http_status is None else f"{self.error_type}, HTTP {self.http_status}"
        if self.problem == "refused_token":
            return f"an authentication failure: Infrahub refused the token ({context})"
        if len(self.permissions) == 1:
            need = f"the import account needs the permission `{self.permissions[0]}`"
        elif self.permissions:
            need = "the import account needs one of the permissions " + ", ".join(
                f"`{permission}`" for permission in self.permissions
            )
        elif self.server_message:
            need = f"Infrahub said: {self.server_message!r}"
        else:
            need = "Infrahub named no permission"
        return f"a missing permission: {need} ({context})"


def classify_infrahub_error(exc: BaseException) -> InfrahubErrorClassification | None:
    """Classify a refused token or a missing permission, or return `None` for any other error.

    - `AuthenticationError` caused by an HTTP 403: a missing permission.
    - `AuthenticationError` caused by an HTTP 401, or with no HTTP cause: a refused token.
    - `httpx.HTTPStatusError` with status 403 or 401: the same, with the message read from the
      response's GraphQL `errors` list.
    - `GraphQLError` with an error message starting "You do not have": a missing permission.
    """
    if isinstance(exc, AuthenticationError):
        return _classify_authentication_error(exc)
    if isinstance(exc, httpx.HTTPStatusError):
        return _classify_status_error(exc)
    if isinstance(exc, GraphQLError):
        return _classify_graphql_error(exc)
    return None


def cause_chain(exc: BaseException) -> Iterator[BaseException]:
    """Yield `exc`, then each error it was raised from, stopping at a cycle.

    The walk follows `__cause__`, then `__context__` unless it is suppressed.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or (None if current.__suppress_context__ else current.__context__)


def classify_infrahub_error_chain(exc: BaseException) -> InfrahubErrorClassification | None:
    """Classify the first error in `exc`'s cause chain that `classify_infrahub_error` classifies.

    A plan wraps each side's load failure in a `ValueError` raised `from` the original, so the
    Infrahub error can be a cause of the error the run fails with. The lineage resolution that
    starts a plan, and an apply, raise the original unwrapped. The walk covers both.
    """
    for link in cause_chain(exc):
        classification = classify_infrahub_error(link)
        if classification is not None:
            return classification
    return None


def _classify_authentication_error(exc: AuthenticationError) -> InfrahubErrorClassification:
    """A 403 cause is a missing permission; a 401 cause, or no HTTP cause, is a refused token."""
    cause = exc.__cause__
    status = cause.response.status_code if isinstance(cause, httpx.HTTPStatusError) else None
    if status == MISSING_PERMISSION_STATUS:
        return _missing_permission([exc.message or ""], error_type=type(exc).__name__, http_status=status)
    return InfrahubErrorClassification(problem="refused_token", error_type=type(exc).__name__, http_status=status)


def _classify_status_error(exc: httpx.HTTPStatusError) -> InfrahubErrorClassification | None:
    """Classify a 401 or a 403 the SDK left as an `httpx` error, as a REST read such as the schema read does."""
    status = exc.response.status_code
    if status == REFUSED_TOKEN_STATUS:
        return InfrahubErrorClassification(problem="refused_token", error_type=type(exc).__name__, http_status=status)
    if status == MISSING_PERMISSION_STATUS:
        return _missing_permission(_response_messages(exc.response), error_type=type(exc).__name__, http_status=status)
    return None


def _classify_graphql_error(exc: GraphQLError) -> InfrahubErrorClassification | None:
    """A GraphQL error whose message starts "You do not have" is a missing permission."""
    messages = [
        error["message"]
        for error in exc.errors or ()
        if isinstance(error, dict) and isinstance(error.get("message"), str)
    ]
    denials = [message for message in messages if message.strip().startswith(_DENIAL_PREFIX)]
    if not denials:
        return None
    return _missing_permission(denials, error_type=type(exc).__name__, http_status=None)


def _response_messages(response: httpx.Response) -> list[str]:
    """Return the `errors[].message` strings of a refused REST or GraphQL response, if any."""
    try:
        body = response.json()
    except ValueError:
        return []
    errors = body.get("errors") if isinstance(body, dict) else None
    if not isinstance(errors, list):
        return []
    return [error["message"] for error in errors if isinstance(error, dict) and isinstance(error.get("message"), str)]


def _missing_permission(
    messages: list[str], *, error_type: str, http_status: int | None
) -> InfrahubErrorClassification:
    """Build a missing-permission classification, quoting the permission text when it has the expected form."""
    text = " | ".join(message.strip() for message in messages if message.strip())
    permissions = _permissions_named(text)
    if permissions:
        return InfrahubErrorClassification(
            problem="missing_permission", error_type=error_type, http_status=http_status, permissions=permissions
        )
    return InfrahubErrorClassification(
        problem="missing_permission",
        error_type=error_type,
        http_status=http_status,
        server_message=quotable_text(text) or None,
    )


def _permissions_named(text: str) -> tuple[str, ...]:
    """Return the permissions a denial names, or `()` when the text is not exactly a denial."""
    one = _ONE_PERMISSION.match(text)
    if one is not None:
        candidates = [one.group(1)]
    else:
        several = _ONE_OF_PERMISSIONS.match(text)
        if several is None:
            return ()
        candidates = [candidate.strip() for candidate in several.group(1).split(" | ")]
    if not all(_PERMISSION.match(candidate) for candidate in candidates):
        return ()
    return tuple(candidates)


def quotable_text(text: str, *, limit: int = _MAX_QUOTED_MESSAGE) -> str:
    """Return text fit to quote: no control characters, single spaces, at most `limit` characters.

    Text longer than `limit` is cut and ends with "…".
    """
    cleaned = " ".join(_CONTROL_CHARACTERS.sub(" ", text).split())
    if len(cleaned) > limit:
        cleaned = cleaned[: limit - 1] + "…"
    return cleaned
