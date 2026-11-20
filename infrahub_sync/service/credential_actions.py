"""The requests, answers and actions of the stored credential routes (spec 027, contracts/http-api.md).

``credential_routes`` mounts the routes; each route calls one method of ``CredentialActions``.

Rights: every check names one permission, ``manage-credentials``, which administrators hold
(FR-015). A later version can give it to a role without changing the routes. The check runs in a
route dependency, so a caller without it is refused before the request body is checked.

Values: a request that creates a credential or changes its value holds secrets. The action adds
them to the redaction registry of the request before any other check, so every message the
request writes afterwards is redacted. No answer holds a value, and a refused request is never
repeated in its answer. The values are encrypted with the key of the Sync API inside the store's
transaction; the store keeps the ciphertext only.

Idempotency: a mutating request takes ``Idempotency-Key``. Its receipt stores the answer, the
credential resource, which holds no value, and a keyed fingerprint of the request
(``CredentialCipher.request_fingerprint``): an unkeyed digest of a short password could be
reversed by trying candidates.

Audit: each accepted change writes one event in the transaction of the change
(``credential.created``, ``credential.value_changed``, ``credential.updated``), with the name and
the revision; a refused right or a refused description writes ``credential.refused``. No event
holds a value.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import datetime  # noqa: TC003 - Pydantic resolves the resource annotations at runtime.
from hashlib import sha256
from typing import TYPE_CHECKING, Annotated, Any, Final
from uuid import uuid4

import structlog
from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from infrahub_sync.client.models import DEFAULT_LIMIT
from infrahub_sync.execution import register_secret_values
from infrahub_sync.plan.canonical import canonical_json_bytes
from infrahub_sync.product_store import (
    CredentialNameTakenError,
    MutationReceipt,
    StoredCredential,
    StoredCredentialInvalidError,
    StoredCredentialNotFoundError,
)
from infrahub_sync.product_store.models import (
    STORED_CREDENTIAL_DESCRIPTION_MAX_LENGTH,
    STORED_CREDENTIAL_NAME_PATTERN,
    is_credential_field_set,
)

from .events import (
    CREDENTIAL_CREATED,
    CREDENTIAL_REFUSED,
    CREDENTIAL_UPDATED,
    CREDENTIAL_VALUE_CHANGED,
    credential_event,
)
from .service import ServiceAPIError
from .settings import ENCRYPTION_KEY_ENV

if TYPE_CHECKING:
    from collections.abc import Callable

    from infrahub_sync.product_store import AuditEvent

    from .auth import Principal
    from .credential_crypto import CredentialCipher
    from .service import AccountAccess, RunService

logger = structlog.get_logger(__name__)

# The one permission of the credential checks (FR-015). Administrators hold it.
MANAGE_CREDENTIALS: Final = "manage-credentials"
CREDENTIAL_VALUE_MAX_LENGTH: Final = 4096
CREATE_OPERATION: Final = "credential-create"
SET_VALUE_OPERATION: Final = "credential-set-value"
DESCRIBE_OPERATION: Final = "credential-describe"

FORBIDDEN_MESSAGE = f"this action needs the {MANAGE_CREDENTIALS} permission, which administrators hold"
KEY_NOT_CONFIGURED_MESSAGE = (
    f"{ENCRYPTION_KEY_ENV} is not set, so the Sync API cannot store a credential value; "
    "the operator of the Sync API sets it"
)
FIELDS_INVALID_MESSAGE = (
    "a credential holds a token, a username and a password, or a username and a token; each value holds "
    f"1 to {CREDENTIAL_VALUE_MAX_LENGTH} characters and no control character"
)
DESCRIPTION_REFUSED_MESSAGE = "description must not contain a secret or a token"
_DESCRIPTION_NOT_PRINTABLE = "description must hold printable characters only"
_IDEMPOTENCY_CONFLICT_MESSAGE = "Idempotency-Key was already used by this actor for different content"
_IN_PROGRESS_MESSAGE = "the matching request is still being processed"
_NOT_FOUND_MESSAGE = "no stored credential has the name {name!r}"
_EXISTS_MESSAGE = "a stored credential already has the name {name!r}; change its value instead"


def _printable_description(value: str) -> str:
    """Refuse a description with a character that is not printable, such as a line break or NUL."""
    if not value.isprintable():
        raise ValueError(_DESCRIPTION_NOT_PRINTABLE)
    return value


Description = Annotated[
    str, Field(max_length=STORED_CREDENTIAL_DESCRIPTION_MAX_LENGTH), AfterValidator(_printable_description)
]
CredentialName = Annotated[str, Field(pattern=STORED_CREDENTIAL_NAME_PATTERN)]


class _SecretRequest(BaseModel):
    """A request that can hold a value: no field is trimmed, echoed in an error or printed."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    def __repr__(self) -> str:
        """Show no field: a field can be a value."""
        return f"{type(self).__name__}()"

    __str__ = __repr__


class CredentialValueRequest(_SecretRequest):
    """The whole new set of fields of a credential: ``PUT /credentials/{name}/value``."""

    username: str | None = None
    password: str | None = None
    token: str | None = None

    def values(self) -> dict[str, str]:
        """Return the fields that the request sets, by name."""
        candidates = {"username": self.username, "password": self.password, "token": self.token}
        return {name: value for name, value in candidates.items() if value is not None}


class CreateCredentialRequest(CredentialValueRequest):
    """A new credential: ``POST /credentials``."""

    name: CredentialName
    description: Description = ""


class DescribeCredentialRequest(BaseModel):
    """A new description: ``PATCH /credentials/{name}``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    description: Description


class CredentialResource(BaseModel):
    """A stored credential as the routes return it: the names of its fields, never their values.

    ``created_by`` and ``updated_by`` hold account identifiers here; the answer shows names.
    """

    name: str
    description: str
    fields: list[str]
    revision: int
    created_by: str
    created_at: datetime
    updated_by: str
    updated_at: datetime


class CredentialPage(BaseModel):
    """One page of credentials sorted by name; ``next_offset`` is ``None`` on the last page."""

    items: list[CredentialResource]
    next_offset: int | None


def credential_resource(credential: StoredCredential) -> CredentialResource:
    """Return the answer of one credential."""
    return CredentialResource(
        name=credential.name,
        description=credential.description,
        fields=list(credential.fields),
        revision=credential.current_revision,
        created_by=credential.created_by,
        created_at=credential.created_at,
        updated_by=credential.updated_by,
        updated_at=credential.updated_at,
    )


def _valid_value(value: str) -> bool:
    return 0 < len(value) <= CREDENTIAL_VALUE_MAX_LENGTH and not any(
        unicodedata.category(character) == "Cc" for character in value
    )


@dataclass(frozen=True)
class _Mutation:
    """One mutating request: who sent it, its key, its operation, the credential it names and its content."""

    principal: Principal
    idempotency_key: str
    operation: str
    name: str
    content: dict[str, Any]


class CredentialActions:
    """The actions of the stored credential routes, over the product store of a ``RunService``.

    ``cipher`` is ``None`` when ``INFRAHUB_SYNC_ENCRYPTION_KEY`` is not set: the list and the view
    answer, and every change answers ``503 credential-key-not-configured``.
    """

    def __init__(self, service: RunService, cipher: CredentialCipher | None) -> None:
        self._service = service
        self._cipher = cipher

    @property
    def _access(self) -> AccountAccess:
        return self._service.accounts

    # ------------------------------------------------------------------
    # Rights
    # ------------------------------------------------------------------

    def require_permission(self, principal: Principal, permission: str, operation: str) -> None:
        """Refuse a caller that does not hold ``permission``, and record the refusal.

        Administrators hold ``manage-credentials``, the only permission of these routes.

        Raises:
            ServiceAPIError: ``403 forbidden``.
        """
        if permission == MANAGE_CREDENTIALS and principal.administrator:
            return
        self._record_refusal(principal, operation, outcome="refused-authorization", cause=permission)
        raise ServiceAPIError(403, "forbidden", FORBIDDEN_MESSAGE)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def list_credentials(self, offset: int = 0, limit: int = DEFAULT_LIMIT) -> CredentialPage:
        """Return one page of credentials sorted by name."""
        page = self._access.projection.list_stored_credentials(offset=offset, limit=limit)
        items = [credential_resource(credential) for credential in page.items]
        return CredentialPage(items=items, next_offset=offset + len(items) if page.has_more else None)

    def read_credential(self, name: str) -> CredentialResource:
        """Return one credential by its name.

        Raises:
            ServiceAPIError: ``404 credential-not-found``.
        """
        credential = self._access.projection.lookup_stored_credential(name)
        if credential is None:
            raise _not_found(name)
        return credential_resource(credential)

    # ------------------------------------------------------------------
    # Changes
    # ------------------------------------------------------------------

    def create(
        self, principal: Principal, request: CreateCredentialRequest, idempotency_key: str
    ) -> tuple[int, dict[str, Any]]:
        """Create a credential with revision 1 and answer ``201`` with it.

        Raises:
            ServiceAPIError: ``503 credential-key-not-configured``, ``422 secret-parameter-refused``,
                ``422 credential-fields-invalid``, ``409 credential-exists``, or a refusal of the key.
        """
        values = request.values()
        register_secret_values(values.values())
        cipher = self._require_cipher()
        self._require_safe_description(principal, CREATE_OPERATION, request.name, request.description)
        _require_field_set(values)
        mutation = _Mutation(
            principal,
            idempotency_key,
            CREATE_OPERATION,
            request.name,
            {"name": request.name, "description": request.description, **values},
        )

        def act(now: datetime) -> StoredCredential:
            return self._access.projection.create_stored_credential(
                name=request.name,
                description=request.description,
                fields=tuple(values),
                encrypt=_encrypt(cipher, values),
                actor=principal.actor,
                now=now,
                event_for=_accepted(CREDENTIAL_CREATED, principal, CREATE_OPERATION, now),
                secrets=self._access.secrets,
            )

        return self._mutate(cipher, mutation, 201, act)

    def set_value(
        self, principal: Principal, name: str, request: CredentialValueRequest, idempotency_key: str
    ) -> tuple[int, dict[str, Any]]:
        """Write the next revision of a credential with the whole new set of fields.

        Raises:
            ServiceAPIError: ``503 credential-key-not-configured``, ``422 credential-fields-invalid``,
                ``404 credential-not-found``, or a refusal of the key.
        """
        values = request.values()
        register_secret_values(values.values())
        cipher = self._require_cipher()
        _require_field_set(values)
        mutation = _Mutation(principal, idempotency_key, SET_VALUE_OPERATION, name, dict(values))

        def act(now: datetime) -> StoredCredential:
            return self._access.projection.change_stored_credential_value(
                name=name,
                fields=tuple(values),
                encrypt=_encrypt(cipher, values),
                actor=principal.actor,
                now=now,
                event_for=_accepted(CREDENTIAL_VALUE_CHANGED, principal, SET_VALUE_OPERATION, now),
                secrets=self._access.secrets,
            )

        return self._mutate(cipher, mutation, 200, act)

    def describe(
        self, principal: Principal, name: str, request: DescribeCredentialRequest, idempotency_key: str
    ) -> tuple[int, dict[str, Any]]:
        """Replace the description of a credential; its revision stays.

        Raises:
            ServiceAPIError: ``503 credential-key-not-configured``, ``422 secret-parameter-refused``,
                ``404 credential-not-found``, or a refusal of the key.
        """
        cipher = self._require_cipher()
        self._require_safe_description(principal, DESCRIBE_OPERATION, name, request.description)
        mutation = _Mutation(principal, idempotency_key, DESCRIBE_OPERATION, name, {"description": request.description})

        def act(now: datetime) -> StoredCredential:
            return self._access.projection.set_stored_credential_description(
                name=name,
                description=request.description,
                actor=principal.actor,
                now=now,
                event_for=_accepted(CREDENTIAL_UPDATED, principal, DESCRIBE_OPERATION, now),
                secrets=self._access.secrets,
            )

        return self._mutate(cipher, mutation, 200, act)

    # ------------------------------------------------------------------
    # The steps of a change
    # ------------------------------------------------------------------

    def _require_cipher(self) -> CredentialCipher:
        if self._cipher is None:
            raise ServiceAPIError(503, "credential-key-not-configured", KEY_NOT_CONFIGURED_MESSAGE)
        return self._cipher

    def _require_safe_description(self, principal: Principal, operation: str, name: str, description: str) -> None:
        """Refuse a description that holds a secret the process knows, a submitted value or a token prefix."""
        if description and not self._access.accepts_free_text(description):
            self._record_refusal(principal, operation, outcome="refused-secret-parameter", name=name)
            raise ServiceAPIError(422, "secret-parameter-refused", DESCRIPTION_REFUSED_MESSAGE)

    def _record_refusal(
        self,
        principal: Principal,
        operation: str,
        *,
        outcome: str,
        cause: str | None = None,
        name: str | None = None,
    ) -> None:
        event = credential_event(
            CREDENTIAL_REFUSED,
            actor=principal.actor,
            operation=operation,
            name=name,
            outcome=outcome,
            cause=cause,
            created_at=self._access.now(),
        )
        self._access.projection.record_audit(event, secrets=self._access.secrets)

    def _mutate(
        self,
        cipher: CredentialCipher,
        mutation: _Mutation,
        status: int,
        act: Callable[[datetime], StoredCredential],
    ) -> tuple[int, dict[str, Any]]:
        """Run one change under its idempotency key and return its answer, or the stored answer of a repeat."""
        projection = self._access.projection
        secrets = self._access.secrets
        now = self._access.now()
        fingerprint = cipher.request_fingerprint(
            canonical_json_bytes({"operation": mutation.operation, "name": mutation.name, "body": mutation.content})
        )
        receipt = MutationReceipt(
            receipt_id=f"m-{uuid4().hex}",
            actor=mutation.principal.actor,
            key_digest=sha256(mutation.idempotency_key.encode()).hexdigest(),
            operation=mutation.operation,
            request_fingerprint=fingerprint,
            reason=None,
            resource_kind="credential",
            resource_id=mutation.name,
            created_at=now,
            updated_at=now,
        )
        existing = projection.lookup_mutation(receipt.actor, receipt.key_digest).value
        if existing is not None:
            _require_matching(existing, receipt)
            if existing.state == "accepted":
                assert existing.response_status is not None
                assert existing.response_body is not None
                return existing.response_status, existing.response_body
        stored, _created = projection.reserve_mutation(receipt, secrets=secrets)
        _require_matching(stored, receipt)
        if stored.state == "accepted" or not projection.claim_mutation(stored.receipt_id, secrets=secrets):
            raise ServiceAPIError(409, "idempotency-in-progress", _IN_PROGRESS_MESSAGE, mutation_id=stored.receipt_id)
        try:
            credential = act(now)
        except CredentialNameTakenError:
            projection.release_mutation(stored.receipt_id, secrets=secrets)
            raise ServiceAPIError(409, "credential-exists", _EXISTS_MESSAGE.format(name=mutation.name)) from None
        except StoredCredentialNotFoundError:
            projection.release_mutation(stored.receipt_id, secrets=secrets)
            raise _not_found(mutation.name) from None
        except StoredCredentialInvalidError:
            # The checks above hold every rule of the store; this answers a rule added there later.
            projection.release_mutation(stored.receipt_id, secrets=secrets)
            raise ServiceAPIError(422, "credential-fields-invalid", FIELDS_INVALID_MESSAGE) from None
        except Exception:
            projection.release_mutation(stored.receipt_id, secrets=secrets)
            raise
        completed = projection.complete_mutation(
            stored.receipt_id,
            response_status=status,
            response_body=credential_resource(credential).model_dump(mode="json"),
            flow_run_id=None,
            secrets=secrets,
        )
        logger.info(
            "credential changed",
            operation=mutation.operation,
            credential=credential.name,
            revision=credential.current_revision,
        )
        assert completed.response_status is not None
        assert completed.response_body is not None
        return completed.response_status, completed.response_body


def _require_field_set(values: dict[str, str]) -> None:
    """Refuse a set of fields outside the three allowed sets, or a value outside the rules (FR-013).

    Raises:
        ServiceAPIError: ``422 credential-fields-invalid``; the message repeats no value.
    """
    if not is_credential_field_set(values) or not all(_valid_value(value) for value in values.values()):
        raise ServiceAPIError(422, "credential-fields-invalid", FIELDS_INVALID_MESSAGE)


def _require_matching(stored: MutationReceipt, requested: MutationReceipt) -> None:
    if (stored.resource_kind, stored.operation, stored.request_fingerprint) != (
        requested.resource_kind,
        requested.operation,
        requested.request_fingerprint,
    ):
        raise ServiceAPIError(409, "idempotency-conflict", _IDEMPOTENCY_CONFLICT_MESSAGE, mutation_id=stored.receipt_id)


def _not_found(name: str) -> ServiceAPIError:
    return ServiceAPIError(404, "credential-not-found", _NOT_FOUND_MESSAGE.format(name=name))


def _encrypt(cipher: CredentialCipher, values: dict[str, str]) -> Callable[[str, int], tuple[str, bytes, bytes]]:
    """Return the callable the store calls with the credential identifier and revision, inside its transaction."""

    def encrypt(credential_id: str, revision: int) -> tuple[str, bytes, bytes]:
        encrypted = cipher.encrypt(credential_id, revision, values)
        return encrypted.key_id, encrypted.nonce, encrypted.ciphertext

    return encrypt


def _accepted(
    event_type: str, principal: Principal, operation: str, now: datetime
) -> Callable[[StoredCredential], AuditEvent]:
    """Return the builder of the event of an accepted change, which the store writes with the change."""

    def build(credential: StoredCredential) -> AuditEvent:
        return credential_event(
            event_type,
            actor=principal.actor,
            operation=operation,
            name=credential.name,
            revision=credential.current_revision,
            created_at=now,
        )

    return build
