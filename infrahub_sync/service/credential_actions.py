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

Release: ``CredentialActions.release`` answers the worker route (research R5), the only answer
that holds a value. It checks that a worker claimed the execution and that the execution has
not ended, reads which credential each block of the run's stored version names, checks every
block the stage needs, and only then decrypts. It writes the ``run_credential_uses`` rows and
the ``credential.released`` event in one transaction, and takes no ``Idempotency-Key``: a
receipt would store the answer, which holds the values.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import datetime  # noqa: TC003 - Pydantic resolves the resource annotations at runtime.
from hashlib import sha256
from itertools import starmap
from typing import TYPE_CHECKING, Annotated, Any, Final, cast
from uuid import uuid4

import structlog
from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from infrahub_sync.client.models import DEFAULT_LIMIT
from infrahub_sync.configuration import ConfigurationPackageParseError, parse_configuration_package
from infrahub_sync.configuration.capabilities import UnknownCredentialMethodError, credential_field_map
from infrahub_sync.execution import LiveSecretValues, register_secret_values
from infrahub_sync.plan.canonical import canonical_json_bytes
from infrahub_sync.product_store import (
    CredentialNameTakenError,
    CredentialUse,
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

from .credential_crypto import CredentialDecryptionError
from .events import (
    CREDENTIAL_CREATED,
    CREDENTIAL_REFUSED,
    CREDENTIAL_UPDATED,
    CREDENTIAL_VALUE_CHANGED,
    credential_event,
    credentials_released_event,
)
from .service import ServiceAPIError
from .settings import ENCRYPTION_KEY_ENV

if TYPE_CHECKING:
    from collections.abc import Callable

    from infrahub_sync.configuration import ConfigurationPackage
    from infrahub_sync.product_store import AuditEvent, PrefectExecutionLink, ProductRun
    from infrahub_sync.product_store.models import CredentialBlock, CredentialStage

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
RELEASE_OPERATION: Final = "credential-release"
FLOW_RUN_ID_MAX_LENGTH: Final = 200

# The blocks each stage builds (research R5). A saved-plan apply, a resume and a verify build the
# destination only, as `flow.py` resolves them; a plan and a sync, scheduled or not, build both.
STAGE_BLOCKS: Final[dict[str, tuple[CredentialBlock, ...]]] = {
    "plan": ("source", "destination"),
    "sync": ("source", "destination"),
    "apply": ("destination",),
    "resume": ("destination",),
    "verify": ("destination",),
}

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
_RUN_NOT_FOUND_MESSAGE = "no run has this identifier"
_NOT_IN_PROGRESS_MESSAGE = (
    "no execution of this run with this flow run identifier is claimed by a worker and still in progress"
)
_VERSION_UNAVAILABLE_MESSAGE = "the configuration version of this run cannot be read"
_BLOCK_CREDENTIAL_NOT_FOUND_MESSAGE = (
    "the {block} names the stored credential {name!r}, which does not exist; an administrator creates it "
    "with `infrahub-sync credentials create {name}`"
)
_FIELD_MISSING_MESSAGE = "the stored credential {name!r} of the {block} does not hold: {fields}"
_METHOD_UNKNOWN_MESSAGE = "the {block} uses an adapter or an auth_method for which no stored field is known"
_UNDECRYPTABLE_MESSAGE = (
    "the value of the stored credential {name!r} of the {block} cannot be decrypted with the current key"
)
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


class ReleaseCredentialsRequest(BaseModel):
    """The execution that asks for its values: ``POST /worker/runs/{run_id}/credentials``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    flow_run_id: str = Field(min_length=1, max_length=FLOW_RUN_ID_MAX_LENGTH)


class ReleasedBlock(BaseModel):
    """The credential of one block as the worker route releases it: its fields hold the values."""

    model_config = ConfigDict(frozen=True)

    name: str
    revision: int
    fields: dict[str, str] = Field(repr=False)


class ReleasedCredentials(BaseModel):
    """The answer of the worker route: the blocks the stage needs that name a credential."""

    model_config = ConfigDict(frozen=True)

    run_id: str
    blocks: dict[str, ReleasedBlock]


@dataclass(frozen=True)
class _BlockCredential:
    """One block that names a credential that exists and holds the fields the block needs."""

    block: CredentialBlock
    credential: StoredCredential
    fields: tuple[str, ...]


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

    @property
    def _process_secrets(self) -> tuple[str, ...]:
        """Return the secrets of the process, without the values this request registered.

        The receipt, the answer and the event hold only text the service wrote: the name, which
        matches the name pattern, identifiers, times, and a description that passed the free-text
        check against every secret, the request's values included. Redacting them with the
        request's values would corrupt a name that equals a value, such as a username named like
        its credential, and a repeat of the request would no longer match its receipt.
        """
        secrets = self._access.secrets
        return secrets.fixed if isinstance(secrets, LiveSecretValues) else tuple(secrets)

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
                secrets=self._process_secrets,
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
                secrets=self._process_secrets,
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
                secrets=self._process_secrets,
            )

        return self._mutate(cipher, mutation, 200, act)

    # ------------------------------------------------------------------
    # Release to a worker
    # ------------------------------------------------------------------

    def release(self, principal: Principal, run_id: str, request: ReleaseCredentialsRequest) -> ReleasedCredentials:
        """Return the values of the blocks that the stage of one claimed execution needs.

        The caller is the ``worker`` account: the route checks it. Every check of every block
        passes before a value is decrypted. Only the fields the block needs are returned. A
        repeated call answers again and replaces the rows of the execution.

        Raises:
            ServiceAPIError: ``404 run-not-found``; ``409 execution-not-in-progress``;
                ``422 credential-not-found``, ``422 credential-field-missing`` or
                ``422 credential-method-unknown``, each with the block; ``500
                credential-undecryptable``; ``500 configuration-version-unavailable``; ``503
                credential-key-not-configured`` when a block names a credential and no key is set.
                No refusal holds a value.
        """
        projection = self._access.projection
        run = projection.lookup_run(run_id).value
        if run is None:
            raise ServiceAPIError(404, "run-not-found", _RUN_NOT_FOUND_MESSAGE)
        execution = _execution_in_progress(run, request.flow_run_id)
        stage = cast("CredentialStage", execution.purpose)
        named = list(starmap(self._checked_block, self._named(run, stage)))
        named = [entry for entry in named if entry is not None]
        if not named:
            return ReleasedCredentials(run_id=run_id, blocks={})
        cipher = self._require_cipher()
        now = self._access.now()
        blocks: dict[str, ReleasedBlock] = {}
        uses: list[CredentialUse] = []
        for entry in named:
            credential = entry.credential
            blocks[entry.block] = ReleasedBlock(
                name=credential.name,
                revision=credential.current_revision,
                fields=self._decrypted(cipher, entry, run_id),
            )
            uses.append(
                CredentialUse(
                    run_id=run_id,
                    flow_run_id=request.flow_run_id,
                    stage=stage,
                    block=entry.block,
                    credential_id=credential.credential_id,
                    credential_name=credential.name,
                    revision=credential.current_revision,
                    released_at=now,
                )
            )
        event = credentials_released_event(
            actor=principal.actor,
            operation=RELEASE_OPERATION,
            run_id=run_id,
            config_id=run.config_id,
            registry_version=run.registry_version,
            released=[(use.credential_name, use.revision) for use in uses],
            created_at=now,
        )
        # The rows and the event hold names, identifiers and revisions only. They are redacted
        # with the secrets of the process, not with the values just decrypted: a name that
        # equals a value, such as a username, must stay intact.
        projection.record_credential_uses(uses, event=event, secrets=self._process_secrets)
        logger.info(
            "credential.released",
            run_id=run_id,
            flow_run_id=request.flow_run_id,
            stage=stage,
            credentials=[
                {"block": use.block, "name": use.credential_name, "revision": use.revision}
                for use in sorted(uses, key=lambda use: use.block)
            ],
        )
        return ReleasedCredentials(run_id=run_id, blocks=blocks)

    def _named(
        self, run: ProductRun, stage: CredentialStage
    ) -> list[tuple[CredentialBlock, str, ConfigurationPackage]]:
        """Return the blocks of the stage that name a credential, read from the run's stored version.

        The stored version is the source: the names are part of its declared content, which the
        package checksum covers. A run without a registered version names no stored credential.
        """
        if run.config_id is None or run.registry_version is None:
            return []
        version = self._access.projection.lookup_configuration_version(run.config_id, run.registry_version).value
        if version is None:
            raise ServiceAPIError(500, "configuration-version-unavailable", _VERSION_UNAVAILABLE_MESSAGE)
        try:
            package = parse_configuration_package(version.declared_content)
        except ConfigurationPackageParseError:
            # The parse error can quote the declared content; the answer names the fact only.
            raise ServiceAPIError(500, "configuration-version-unavailable", _VERSION_UNAVAILABLE_MESSAGE) from None
        named: list[tuple[CredentialBlock, str, ConfigurationPackage]] = []
        for block in STAGE_BLOCKS[stage]:
            name = getattr(package.configuration, block).credential
            if name is not None:
                named.append((block, name, package))
        return named

    def _checked_block(
        self, block: CredentialBlock, name: str, package: ConfigurationPackage
    ) -> _BlockCredential | None:
        """Return the block with its credential and the fields it needs, or ``None`` when it needs none.

        Raises:
            ServiceAPIError: ``422 credential-not-found``, ``422 credential-field-missing`` or
                ``422 credential-method-unknown``. The details name the block, the credential
                and the missing fields, never a value.
        """
        adapter = getattr(package.configuration, block)
        try:
            needed = credential_field_map(adapter.name, adapter.settings or {}).fields
        except UnknownCredentialMethodError:
            # An adapter without a capability declaration is not caught: registration refuses
            # it, so a stored version cannot name one.
            raise ServiceAPIError(
                422,
                "credential-method-unknown",
                _METHOD_UNKNOWN_MESSAGE.format(block=block),
                details={"block": block, "name": name},
            ) from None
        if not needed:
            return None
        credential = self._access.projection.lookup_stored_credential(name)
        if credential is None:
            raise _block_credential_not_found(block, name)
        missing = sorted(set(needed) - set(credential.fields))
        if missing:
            raise ServiceAPIError(
                422,
                "credential-field-missing",
                _FIELD_MISSING_MESSAGE.format(block=block, name=name, fields=", ".join(missing)),
                details={"block": block, "name": name, "fields": missing},
            )
        return _BlockCredential(block=block, credential=credential, fields=tuple(sorted(needed)))

    def _decrypted(self, cipher: CredentialCipher, entry: _BlockCredential, run_id: str) -> dict[str, str]:
        """Return the needed fields of the current revision; the cipher adds each value to the request's registry.

        Raises:
            ServiceAPIError: ``500 credential-undecryptable``, or ``422 credential-not-found``
                when the credential was deleted since it was checked.
        """
        credential = entry.credential
        stored = self._access.projection.lookup_stored_credential_revision(
            credential.credential_id, credential.current_revision
        )
        if stored is None:
            raise _block_credential_not_found(entry.block, credential.name)
        try:
            values = cipher.decrypt(
                credential.credential_id,
                stored.revision,
                key_id=stored.key_id,
                nonce=stored.nonce,
                ciphertext=stored.ciphertext,
            )
        except CredentialDecryptionError as error:
            logger.warning(
                "credential.undecryptable",
                run_id=run_id,
                block=entry.block,
                name=credential.name,
                revision=stored.revision,
                cause=error.reason,
            )
            raise _undecryptable(entry.block, credential.name) from None
        if any(field not in values for field in entry.fields):
            # The encrypted value does not hold a field its record lists.
            logger.warning(
                "credential.undecryptable",
                run_id=run_id,
                block=entry.block,
                name=credential.name,
                revision=stored.revision,
                cause="malformed-value",
            )
            raise _undecryptable(entry.block, credential.name)
        return {field: values[field] for field in entry.fields}

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
        self._access.projection.record_audit(event, secrets=self._process_secrets)

    def _mutate(
        self,
        cipher: CredentialCipher,
        mutation: _Mutation,
        status: int,
        act: Callable[[datetime], StoredCredential],
    ) -> tuple[int, dict[str, Any]]:
        """Run one change under its idempotency key and return its answer, or the stored answer of a repeat."""
        projection = self._access.projection
        secrets = self._process_secrets
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


def _execution_in_progress(run: ProductRun, flow_run_id: str) -> PrefectExecutionLink:
    """Return the execution of the run that a worker claimed and that has not ended.

    The row of the execution decides, not the phase of the run: a run that has a finished plan
    and a running apply has one execution in progress, the apply.

    Raises:
        ServiceAPIError: ``409 execution-not-in-progress``.
    """
    for link in run.prefect_executions:
        if (
            link.flow_run_id == flow_run_id
            and link.claimed_at is not None
            and link.claiming_worker_id is not None
            and link.terminal_at is None
            and link.purpose in STAGE_BLOCKS
        ):
            return link
    raise ServiceAPIError(409, "execution-not-in-progress", _NOT_IN_PROGRESS_MESSAGE, run_id=run.run_id)


def _block_credential_not_found(block: str, name: str) -> ServiceAPIError:
    return ServiceAPIError(
        422,
        "credential-not-found",
        _BLOCK_CREDENTIAL_NOT_FOUND_MESSAGE.format(block=block, name=name),
        details={"block": block, "name": name},
    )


def _undecryptable(block: str, name: str) -> ServiceAPIError:
    return ServiceAPIError(
        500,
        "credential-undecryptable",
        _UNDECRYPTABLE_MESSAGE.format(block=block, name=name),
        details={"block": block, "name": name},
    )


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
