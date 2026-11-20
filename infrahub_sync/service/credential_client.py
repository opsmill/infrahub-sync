"""The worker's call to the worker route of the Sync API (spec 027, research R6).

A stage execution calls ``release_run_credentials`` after it claimed its execution. The Sync API
answers with the values of the stored credentials that the blocks of the stage name
(``POST /api/worker/runs/{run_id}/credentials``, contracts/http-api.md).

Rules:

- The request presents ``INFRAHUB_SYNC_WORKER_TOKEN`` and waits at most 10 seconds.
- A connection error, a timeout and the answers ``502``, ``503`` and ``504`` are tried again
  three times, after 1, 2 and 4 seconds. The route can be repeated: the Sync API replaces the
  rows of the execution. Every other refusal is final at once.
- ``https://`` addresses trust the bundle that ``trust_store.install_custom_ca_bundle`` wrote
  from ``INFRAHUB_SYNC_CA_DIRECTORY`` when the worker started, as the rest of the worker does.
  A redirect is not followed and no proxy variable is read: the request carries the worker token.
- Every released value is added to the redaction registry of the run before the function
  returns, and before the answer is checked. Without a registry, no request is sent.
- A failure is a ``CredentialUnavailableError`` with a reason. Its message is written here, from
  the reason, the code of the refusal and the names the refusal gives; it never repeats the text
  of an answer, which could hold a value.
"""

from __future__ import annotations

import os
import re
import ssl
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final, Literal
from urllib.parse import quote

import httpx
import structlog

from infrahub_sync.execution import register_secret_values

from .settings import WorkerSettings, worker_settings

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

logger = structlog.get_logger(__name__)

TIMEOUT_SECONDS: Final = 10.0
RETRY_DELAYS_SECONDS: Final = (1.0, 2.0, 4.0)
RETRIED_STATUSES: Final = frozenset({502, 503, 504})
# The variable that `trust_store.install_custom_ca_bundle` sets to the bundle it wrote.
CA_BUNDLE_ENV: Final = "SSL_CERT_FILE"
# The prefix under which `service.web` serves the Sync API; a test holds the two equal.
_API_PREFIX: Final = "/api"
_ANSWER_INVALID: Final = "answer-invalid"
_BLOCKS: Final = ("source", "destination")
_FIELD_NAMES: Final = frozenset({"username", "password", "token"})
_NAME = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
_CODE = re.compile(r"^[a-z][a-z0-9-]{0,99}$")
_OK: Final = 200
_UNAUTHENTICATED: Final = 401
_FORBIDDEN: Final = 403

CredentialUnavailableReason = Literal[
    "api-unreachable",
    "worker-token-refused",
    "credential-missing",
    "credential-field-missing",
    "credential-undecryptable",
    "release-refused",
]

_API_UNREACHABLE: Final[CredentialUnavailableReason] = "api-unreachable"
_RELEASE_REFUSED: Final[CredentialUnavailableReason] = "release-refused"

# The refusal codes of the worker route that have a reason of their own (research R6).
_REASON_OF_CODE: Final[dict[str, CredentialUnavailableReason]] = {
    "credential-not-found": "credential-missing",
    "credential-field-missing": "credential-field-missing",
    "credential-undecryptable": "credential-undecryptable",
}
_MESSAGES: Final[dict[CredentialUnavailableReason, str]] = {
    "api-unreachable": "the Sync API could not be reached to release the stored credentials of the run",
    "worker-token-refused": "the Sync API refused the worker token",
    "credential-missing": "a stored credential that the configuration names does not exist",
    "credential-field-missing": "a stored credential does not hold a field that its block needs",
    "credential-undecryptable": "the Sync API cannot decrypt a stored credential with its current key",
    "release-refused": "the Sync API refused to release the stored credentials of the run",
}


class CredentialUnavailableError(RuntimeError):
    """The worker could not get the stored credential values of its run. The message holds no value.

    ``reason`` is the cause that the run failure reports. ``code`` is the code of the refusal of
    the Sync API, when it gave one; ``answer-invalid`` when a ``200`` answer was not the
    contract. ``block``, ``name`` and ``fields`` are the block, the credential name and the
    missing field names that the refusal gave, kept only when they have the form of one.
    """

    def __init__(  # pylint: disable=too-many-arguments
        self,
        reason: CredentialUnavailableReason,
        *,
        code: str | None = None,
        block: str | None = None,
        name: str | None = None,
        fields: tuple[str, ...] = (),
    ) -> None:
        parts = [_MESSAGES[reason]]
        if code is not None:
            parts.append(f"code {code}")
        if block is not None:
            parts.append(f"block {block}")
        if name is not None:
            parts.append(f"credential {name}")
        if fields:
            parts.append(f"missing {', '.join(fields)}")
        super().__init__("; ".join(parts))
        self.reason: CredentialUnavailableReason = reason
        self.code = code
        self.block = block
        self.name = name
        self.fields = fields


@dataclass(frozen=True)
class ReleasedBlock:
    """The credential that the Sync API released for one block: ``fields`` holds the values."""

    name: str
    revision: int
    fields: dict[str, str] = field(repr=False)


def certificate_trust() -> ssl.SSLContext | bool:
    """Return what the request trusts: the worker's CA bundle when it installed one, else the public roots.

    Raises:
        OSError: the bundle cannot be read (``ssl.SSLError`` is one).
    """
    bundle = os.environ.get(CA_BUNDLE_ENV)
    return ssl.create_default_context(cafile=bundle) if bundle else True


def release_run_credentials(
    run_id: str,
    flow_run_id: str,
    *,
    settings: WorkerSettings | None = None,
    sleep: Callable[[float], object] = time.sleep,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, ReleasedBlock]:
    """Return the stored credentials of the blocks that this stage execution needs, by block.

    A block that names no credential is absent. ``settings`` defaults to the variables of the
    worker (``settings.worker_settings``). ``sleep`` and ``transport`` are for tests.

    Raises:
        CredentialUnavailableError: the Sync API could not be reached after the retries, or it
            refused. No value is in the error.
        NoRedactionScopeError: no redaction registry is bound (``execution.redaction_scope``).
            Checked before the request, so no value is received.
        CredentialSettingsError: ``settings`` is not given and the variables of the worker
            break a rule.
    """
    # Refuses here when no registry is bound, before a value can be received.
    register_secret_values(())
    resolved = worker_settings() if settings is None else settings
    url = f"{resolved.api_url.rstrip('/')}{_API_PREFIX}/worker/runs/{quote(run_id, safe='')}/credentials"
    try:
        verify = certificate_trust()
    except OSError as error:
        logger.warning("credential.release_unreachable", run_id=run_id, error_type=type(error).__name__)
        raise CredentialUnavailableError(_API_UNREACHABLE) from None
    with httpx.Client(
        timeout=httpx.Timeout(TIMEOUT_SECONDS),
        verify=verify,
        transport=transport,
        follow_redirects=False,
        trust_env=False,
    ) as client:
        response = _send(client, url, resolved.token, run_id, flow_run_id, sleep)
    if response.status_code != _OK:
        raise _refusal(response)
    return _released(response, run_id)


def _send(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    client: httpx.Client, url: str, token: str, run_id: str, flow_run_id: str, sleep: Callable[[float], object]
) -> httpx.Response:
    """Send the request, with the retries, and return the first answer that is not retried.

    Raises:
        CredentialUnavailableError: ``api-unreachable`` after the last attempt.
    """
    code: str | None = None
    for delay in _delays():
        code = None
        try:
            response = client.post(url, headers={"Authorization": f"Bearer {token}"}, json={"flow_run_id": flow_run_id})
        except httpx.TransportError as error:
            # Only the class is logged: the text of a transport error names the address.
            failure = type(error).__name__
        else:
            if response.status_code not in RETRIED_STATUSES:
                return response
            failure = f"status {response.status_code}"
            code = _error_code(_json(response))
        logger.warning(
            "credential.release_retry" if delay is not None else "credential.release_unreachable",
            run_id=run_id,
            flow_run_id=flow_run_id,
            failure=failure,
            retry_after_seconds=delay,
        )
        if delay is not None:
            sleep(delay)
    raise CredentialUnavailableError(_API_UNREACHABLE, code=code)


def _delays() -> Iterator[float | None]:
    """Yield, for each attempt, the wait before the next one; ``None`` for the last attempt."""
    yield from RETRY_DELAYS_SECONDS
    yield None


def _json(response: httpx.Response) -> Any:
    """Return the JSON document of an answer, or ``None``; a JSON error quotes the text, so it is dropped."""
    try:
        return response.json()
    except ValueError:
        return None


def _error_code(document: Any) -> str | None:
    error = document.get("error") if isinstance(document, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    return code if isinstance(code, str) and _CODE.fullmatch(code) else None


def _refusal(response: httpx.Response) -> CredentialUnavailableError:
    """Return the error of a refusal: its reason, its code, and the names it gives when they have their form."""
    document = _json(response)
    code = _error_code(document)
    details = document["error"].get("details") if code is not None else None
    details = details if isinstance(details, dict) else {}
    block = details.get("block")
    name = details.get("name")
    fields = details.get("fields")
    refused_token = response.status_code in {_UNAUTHENTICATED, _FORBIDDEN}
    reason = "worker-token-refused" if refused_token else _REASON_OF_CODE.get(code or "", _RELEASE_REFUSED)
    return CredentialUnavailableError(
        reason,
        code=code,
        block=block if block in _BLOCKS else None,
        name=name if isinstance(name, str) and _NAME.fullmatch(name) else None,
        fields=tuple(sorted(item for item in fields if item in _FIELD_NAMES)) if isinstance(fields, list) else (),
    )


def _answer_invalid() -> CredentialUnavailableError:
    return CredentialUnavailableError(_RELEASE_REFUSED, code=_ANSWER_INVALID)


def _released(response: httpx.Response, run_id: str) -> dict[str, ReleasedBlock]:
    """Register every value of the answer, then check the answer and return its blocks.

    Raises:
        CredentialUnavailableError: ``release-refused`` with the code ``answer-invalid``.
    """
    document = _json(response)
    blocks = document.get("blocks") if isinstance(document, dict) else None
    if not isinstance(blocks, dict):
        raise _answer_invalid()
    # Before any check: an answer that is refused below has its values in the registry too.
    for block in blocks.values():
        fields = block.get("fields") if isinstance(block, dict) else None
        if isinstance(fields, dict):
            register_secret_values(value for value in fields.values() if isinstance(value, str))
    if document.get("run_id") != run_id:
        raise _answer_invalid()
    released: dict[str, ReleasedBlock] = {}
    for key, block in blocks.items():
        entry = _released_block(block) if key in _BLOCKS else None
        if entry is None:
            raise _answer_invalid()
        released[key] = entry
    return released


def _released_block(block: Any) -> ReleasedBlock | None:
    """Return one block of the answer, or ``None`` when it is not a name, a revision and fields of text."""
    if not isinstance(block, dict):
        return None
    name, revision, fields = block.get("name"), block.get("revision"), block.get("fields")
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        return None
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        return None
    if not isinstance(fields, dict) or not all(
        key in _FIELD_NAMES and isinstance(value, str) for key, value in fields.items()
    ):
        return None
    return ReleasedBlock(name=name, revision=revision, fields=dict(fields))
