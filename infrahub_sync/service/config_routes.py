"""HTTP-only adapter for the shared configuration application service."""

import os
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from functools import wraps
from hashlib import sha256
from typing import Annotated, Any, Literal, cast
from uuid import uuid4

import yaml
from fastapi import APIRouter, Depends, Query
from fastapi import Path as APIPath
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from infrahub_sync.client.errors import CONFIGURATION_NAME_IN_USE, configuration_name_in_use_message
from infrahub_sync.client.models import (
    ConfigurationPage,
    ConfigurationSummaryResource,
    ConfigurationVersionPage,
    ConfigurationVersionResource,
    ConfigurationVersionSummaryResource,
    PackageCheckResource,
    RegisteredConfigurationResource,
    RegisteredVersionResource,
    ValidationReportResource,
)
from infrahub_sync.configuration import ValidationFinding
from infrahub_sync.configuration.schema_validation import DestinationSchemaOptions
from infrahub_sync.configuration.storage import UNSUPPORTED_STORE_REASON
from infrahub_sync.plan.canonical import canonical_json_bytes
from infrahub_sync.product_store import (
    MutationReceipt,
    ProductProjection,
    ProductStoreProviderError,
    configs,
)
from infrahub_sync.product_store.models import MAX_REGISTRY_VERSION

from .auth import Principal
from .events import (
    CONFIG_REGISTERED,
    CONFIG_VERSION_REGISTERED,
    CONFIG_VERSION_RETRIEVED,
    config_decision_type,
    config_event,
)
from .liveness import LivenessPolicy
from .models import ConfigMutationRequest, ErrorEnvelope
from .paging import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    MIN_LIMIT,
    FilterValue,
    decode_cursor,
    encode_cursor,
    timestamp_cursor,
    timestamp_key,
    version_key,
)
from .service import REASON_REFUSED_MESSAGE, ServiceAPIError, is_free_text

CONFIG_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
# The errors the two registration routes declare: authentication and authorization, a name or an
# idempotency key in use, and a request that does not match the API schema.
_MUTATION_ERRORS: dict[int | str, dict[str, Any]] = {
    status: {"model": ErrorEnvelope} for status in (401, 403, 409, 422)
}
# Most findings one validation-report page returns, and the default page size.
_MAX_VALIDATION_FINDINGS_PAGE = 256


# The refusal of a document that is not a mapping with string keys, or whose YAML aliases expand
# past `_MAX_DOCUMENT_VALUES` values (`POST /configs/validate`).
INVALID_PACKAGE_REASON = "invalid_package"
# The refusal of a v2 `config.yml` sent where a configuration package is expected.
V2_CONFIGURATION_REASON = "v2_configuration"
V2_CONFIGURATION_MESSAGE = (
    "this is a v2 configuration, not a configuration package; "
    "convert it with `infrahub-sync configs convert`, then check the package it writes"
)


class PackageCheckRequest(BaseModel):
    """The body of `POST /configs/validate`: a package as JSON, or the text of a package file.

    Exactly one field is set. The route checks that rule itself, so that breaking it is the
    `request` refusal of the contract rather than a schema refusal, and so that it is checked
    only after the caller is known to be an administrator.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)
    package: dict[str, Any] | None = None
    document: str | None = None


def _is_v2_configuration(package: Mapping[str, Any]) -> bool:
    """Return whether a mapping is a v2 `config.yml` rather than a configuration package."""
    return "configuration" not in package and "source" in package and "destination" in package


# Most values a package document may hold, counting a YAML alias once at every place it is
# used. A few hundred bytes of nested aliases expand to billions of values, and the package
# checks walk every one of them, so such a document would hold a request thread for hours.
_MAX_DOCUMENT_VALUES = 1_000_000


def _within_document_value_limit(value: object) -> bool:
    """Return whether ``value`` holds at most `_MAX_DOCUMENT_VALUES` values, aliases expanded.

    The walk stops at the limit, so it costs at most that many steps, also for an alias that
    refers to itself.
    """
    pending = [value]
    for _ in range(_MAX_DOCUMENT_VALUES):
        if not pending:
            return True
        item = pending.pop()
        if type(item) is dict:  # pylint: disable=unidiomatic-typecheck
            pending.extend(cast("dict[object, object]", item).values())
        elif type(item) is list:  # pylint: disable=unidiomatic-typecheck
            pending.extend(cast("list[object]", item))
    return not pending


def _document_package(document: str) -> dict[str, Any]:
    """Parse a package file's text as the CLI does, refusing anything but a non-empty mapping.

    A document whose aliases expand past `_MAX_DOCUMENT_VALUES` values is refused the same
    way. The refusal is fixed text: the parser's error would repeat the document.
    """
    try:
        value = yaml.safe_load(document)
    except (yaml.YAMLError, ValueError, RecursionError):
        raise ConfigurationAPIError(400, "request", reason=INVALID_PACKAGE_REASON, proven_pre_effect=True) from None
    if type(value) is not dict or not value or not all(type(key) is str for key in value):  # pylint: disable=unidiomatic-typecheck
        raise ConfigurationAPIError(400, "request", reason=INVALID_PACKAGE_REASON, proven_pre_effect=True)
    if not _within_document_value_limit(value):
        raise ConfigurationAPIError(400, "request", reason=INVALID_PACKAGE_REASON, proven_pre_effect=True)
    return cast("dict[str, Any]", value)


def _registrable_document(document: str) -> dict[str, Any]:
    """Parse a package file's text for registration, refusing it as `POST /configs/validate` does."""
    content = _document_package(document)
    if _is_v2_configuration(content):
        raise ConfigurationAPIError(422, "validation", reason=V2_CONFIGURATION_REASON, proven_pre_effect=True)
    return content


class ConfigurationAPIError(Exception):
    """Fixed public representation of one shared configuration-service refusal."""

    def __init__(self, status: int, family: str, *, reason: str | None = None, proven_pre_effect: bool = False) -> None:
        self.status = status
        self.family = family
        self.reason = reason
        self.proven_pre_effect = proven_pre_effect


def _provider_error_boundary(operation: Any) -> Any:
    """Keep direct receipt and audit provider failures in the configuration vocabulary."""

    @wraps(operation)
    def guarded(*args: Any, **kwargs: Any) -> Any:
        try:
            return operation(*args, **kwargs)
        except (ConfigurationAPIError, ServiceAPIError):
            raise
        except ProductStoreProviderError:
            raise ConfigurationAPIError(503, "storage") from None
        except Exception:  # noqa: BLE001  # Direct receipt/audit defects retain the fixed configuration family.
            raise ConfigurationAPIError(503, "internal") from None

    return guarded


def parse_bounded_integer(value: str, *, minimum: int, maximum: int) -> int:
    """Parse one bounded API integer without accepting FastAPI's coercions."""
    if not value.isascii() or not value.isdecimal():
        raise ServiceAPIError(422, "request-invalid", "the request does not match the API schema")
    # Refuse a value with more digits than the maximum before ``int()``, which raises
    # ``ValueError`` past Python's integer string-digit limit.
    digits = value.lstrip("0") or "0"
    if len(digits) > len(str(maximum)):
        raise ServiceAPIError(422, "request-invalid", "the request does not match the API schema")
    number = int(digits)
    if number < minimum or number > maximum:
        raise ServiceAPIError(422, "request-invalid", "the request does not match the API schema")
    return number


# The longest Infrahub branch name `configs validate --branch` accepts; Infrahub's own limit is
# lower, and a longer value is refused before any read.
_MAX_BRANCH_LENGTH = 250


def parse_branch(value: str | None) -> str | None:
    """Parse the ``branch`` query value of a validation: absent, or a non-empty printable name."""
    if value is None:
        return None
    if not value or len(value) > _MAX_BRANCH_LENGTH or not value.isprintable() or value != value.strip():
        raise ServiceAPIError(422, "request-invalid", "the request does not match the API schema")
    return value


def parse_page_limit(value: str | None) -> int | None:
    """Parse a list's ``limit`` query value without FastAPI's coercions (``1.0``, ``true``)."""
    return None if value is None else parse_bounded_integer(value, minimum=MIN_LIMIT, maximum=MAX_LIMIT)


class ConfigurationRoutes:
    """Bind configuration operations to this server's durable product projection."""

    def __init__(
        self,
        *,
        product_projection: ProductProjection,
        service: Any = configs,
        secrets: tuple[str, ...] = (),
        worker_liveness_seconds: float | None = None,
    ) -> None:
        self._projection = product_projection
        self._service = service
        self._secrets = secrets
        # How recently a worker must have published its credential variable names to count as
        # live: the service liveness rule, from the same setting `serve` reads.
        self._worker_liveness_seconds = (
            LivenessPolicy.from_environment(
                worker_query_seconds=os.environ.get("PREFECT_WORKER_QUERY_SECONDS", "10")
            ).stall_threshold_seconds
            if worker_liveness_seconds is None
            else worker_liveness_seconds
        )

    def _call(self, operation: Any, **kwargs: Any) -> Any:
        """Translate configuration service refusals into fixed API classifications.

        Raises:
            ServiceAPIError: ``409 configuration-name-in-use`` when another configuration has
                the name of the package; ``details`` names that configuration.
        """
        try:
            return operation(projection=self._projection, **kwargs)
        except self._service.ConfigsError as error:
            error_type = type(error)
            if error_type is configs.ConfigsNameInUseError:
                raise ServiceAPIError(
                    409,
                    CONFIGURATION_NAME_IN_USE,
                    configuration_name_in_use_message(error.name, error.config_id),
                    details={"config_id": error.config_id, "name": error.name},
                    secrets=self._secrets,
                ) from None
            if error_type is self._service.ConfigsRequestError:
                raise ConfigurationAPIError(400, "request", proven_pre_effect=True) from None
            if error_type is self._service.ConfigsValidationError:
                reason = UNSUPPORTED_STORE_REASON if error.reason == UNSUPPORTED_STORE_REASON else None
                raise ConfigurationAPIError(422, "validation", reason=reason, proven_pre_effect=True) from None
            if error_type is self._service.ConfigsNotFoundError:
                raise ConfigurationAPIError(404, "not-found", reason=error.reason, proven_pre_effect=True) from None
            if error_type is self._service.ConfigsStorageError:
                raise ConfigurationAPIError(503, "storage") from None
            if error_type is self._service.ConfigsInternalError:
                raise ConfigurationAPIError(503, "internal") from None
            raise ConfigurationAPIError(503, "configs") from None

    def register(
        self,
        package: dict[str, Any],
        *,
        actor: str | None = None,
        reason: str | None = None,
        registered_without_validation: bool = False,
    ) -> RegisteredConfigurationResource:
        """Register a package and return its configuration and first version.

        ``actor`` and ``reason`` are recorded on the first version, with this server's
        secrets redacted; ``registered_without_validation`` marks it.
        """

        result = self._call(
            self._service.register,
            package=package,
            actor=actor,
            reason=reason,
            registered_without_validation=registered_without_validation,
            secrets=self._secrets,
        )
        return RegisteredConfigurationResource.model_validate(result, from_attributes=True)

    def create_version(
        self,
        config_id: str,
        package: dict[str, Any],
        *,
        actor: str | None = None,
        reason: str | None = None,
        registered_without_validation: bool = False,
    ) -> RegisteredVersionResource:
        """Create or retrieve an identical registered configuration version.

        ``actor`` and ``reason`` are recorded on a created version, with this server's
        secrets redacted; ``registered_without_validation`` marks it. An identical stored
        version keeps its own values.
        """

        result = self._call(
            self._service.create_version,
            config_id=config_id,
            package=package,
            actor=actor,
            reason=reason,
            registered_without_validation=registered_without_validation,
            secrets=self._secrets,
        )
        return RegisteredVersionResource.model_validate(result, from_attributes=True)

    def list_configs(
        self, *, name: str | None = None, cursor: str | None = None, limit: int | None = None
    ) -> ConfigurationPage:
        """Return one page of configurations, newest first, optionally only those named ``name``.

        ``limit`` is already bounded by the route (`parse_page_limit`); ``None`` means the default.

        Raises:
            InvalidCursorError: ``cursor`` does not decode or was issued for another name.
        """
        page_size = DEFAULT_LIMIT if limit is None else limit
        filters: dict[str, FilterValue] = {"list": "configs", "name": name}
        after = None if cursor is None else timestamp_key(decode_cursor(cursor, filters))
        page = self._call(self._service.list_configs, name=name, after=after, limit=page_size)
        items = tuple(ConfigurationSummaryResource.model_validate(item, from_attributes=True) for item in page.items)
        last = items[-1] if page.has_more else None
        next_cursor = None if last is None else timestamp_cursor(last.created_at, last.config_id, filters)
        return ConfigurationPage(items=items, next_cursor=next_cursor)

    def get_config(self, config_id: str) -> ConfigurationSummaryResource:
        """Return one registered configuration summary."""

        result = self._call(self._service.get_config, config_id=config_id)
        return ConfigurationSummaryResource.model_validate(result, from_attributes=True)

    def list_versions(
        self, config_id: str, *, cursor: str | None = None, limit: int | None = None
    ) -> ConfigurationVersionPage:
        """Return one page of a configuration's version summaries, newest version first.

        ``limit`` is already bounded by the route (`parse_page_limit`); ``None`` means the default.

        Raises:
            InvalidCursorError: ``cursor`` does not decode or was issued for another
                configuration.
            ConfigurationAPIError: ``404`` for an unknown configuration.
        """
        page_size = DEFAULT_LIMIT if limit is None else limit
        filters: dict[str, FilterValue] = {"list": "versions", "config_id": config_id}
        after = None if cursor is None else version_key(decode_cursor(cursor, filters))
        page = self._call(self._service.list_versions, config_id=config_id, after=after, limit=page_size)
        items = tuple(
            ConfigurationVersionSummaryResource.model_validate(item, from_attributes=True) for item in page.items
        )
        next_cursor = encode_cursor(items[-1].registry_version, filters) if page.has_more else None
        return ConfigurationVersionPage(items=items, next_cursor=next_cursor)

    def get_version(self, config_id: str, registry_version: int) -> ConfigurationVersionResource:
        """Return one immutable registered configuration version."""

        result = self._call(self._service.get_version, config_id=config_id, registry_version=registry_version)
        return ConfigurationVersionResource.model_validate(result, from_attributes=True)

    def validate(
        self,
        config_id: str,
        registry_version: int,
        *,
        offset: int = 0,
        limit: int = _MAX_VALIDATION_FINDINGS_PAGE,
        branch: str | None = None,
    ) -> ValidationReportResource:
        """Validate a version and return the requested ordered findings page.

        This server's collected secrets reach the producer, which can replace a declared key
        whole before the diagnostic bounds cut it, and the same set is applied again to every
        finding on the returned page.

        With ``branch``, the destination checks run against that Infrahub branch and the
        package's credential references are compared with what the live workers hold (spec
        007 FR-034 to FR-036). Without it, validation judges declared content only, as before.
        """

        report = self._call(
            self._service.validate,
            config_id=config_id,
            registry_version=registry_version,
            secrets=self._secrets,
            **self._destination_options(branch),
        )
        return ValidationReportResource.model_validate(
            {
                "config_id": report.config_id,
                "registry_version": report.registry_version,
                "package_checksum": report.package_checksum,
                "destination_schema_fingerprint": report.destination_schema_fingerprint,
                **self._findings_page(report.findings, offset=offset, limit=limit),
            }
        )

    def check_package(
        self,
        *,
        package: dict[str, Any] | None,
        document: str | None,
        offset: int = 0,
        limit: int = _MAX_VALIDATION_FINDINGS_PAGE,
        branch: str | None = None,
    ) -> PackageCheckResource:
        """Validate a package that is not registered and return the requested findings page.

        Exactly one of ``package`` and ``document`` is set; ``document`` is the text of a
        package file, YAML or JSON. Every refusal the registration gives for the same package
        is given here. With ``branch``, the destination checks run as for :meth:`validate`, and
        ``destination_read`` says whether the destination was read. Findings are redacted
        with this server's secrets, as for :meth:`validate`. ``declared_content`` is returned
        as the caller sent it, defaults filled, because its checksum is ``package_checksum``.
        """
        if (package is None) == (document is None):
            raise ConfigurationAPIError(400, "request", proven_pre_effect=True)
        content = _document_package(document) if document is not None else cast("dict[str, Any]", package)
        if _is_v2_configuration(content):
            raise ConfigurationAPIError(422, "validation", reason=V2_CONFIGURATION_REASON, proven_pre_effect=True)
        checked = self._call(
            self._service.check_package, package=content, secrets=self._secrets, **self._destination_options(branch)
        )
        return PackageCheckResource.model_validate(
            {
                "package_checksum": checked.package_checksum,
                "name": checked.name,
                "declared_content": checked.declared_content,
                "destination_read": checked.destination_read,
                "destination_schema_fingerprint": checked.destination_schema_fingerprint,
                **self._findings_page(checked.findings, offset=offset, limit=limit),
            }
        )

    def _destination_options(self, branch: str | None) -> dict[str, Any]:
        """Return the service arguments of the destination checks for ``branch``, or none without one."""
        if branch is None:
            return {}
        return {
            "destination_schema": DestinationSchemaOptions(branch=branch),
            "worker_liveness_seconds": self._worker_liveness_seconds,
        }

    def _findings_page(self, findings: Sequence[ValidationFinding], *, offset: int, limit: int) -> dict[str, Any]:
        """Return one page of ordered findings, redacted with this server's secrets, and its paging fields."""
        page = tuple(configs.redact_finding(finding, self._secrets) for finding in findings[offset : offset + limit])
        return {
            "findings": tuple(finding.model_dump(mode="json") for finding in page),
            "offset": offset,
            "limit": limit,
            "total_findings": len(findings),
            "next_offset": offset + len(page) if offset + len(page) < len(findings) else None,
        }

    @_provider_error_boundary
    def mutate(
        self,
        *,
        actor: str,
        idempotency_key: str,
        operation: str,
        resource_kind: Literal["configuration", "configuration-registry"],
        resource_id: str,
        package: dict[str, Any] | None,
        reason: str | None,
        registered_without_validation: bool = False,
        document: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        """Persist one configuration mutation response and replay it exactly.

        Exactly one of ``package`` and ``document`` is set; ``document`` is the text of a
        package file, YAML or JSON, parsed and refused as :meth:`check_package` does. A
        document is fingerprinted by a digest of its text, so a retry replays only when it sends
        the same text, and a package request keeps the fingerprint it had before documents existed.

        A key reused with a different ``registered_without_validation`` is refused as for a
        different reason. The fingerprint carries the mark only when it is ``true``, so an
        unmarked request keeps the fingerprint that receipts stored before the mark existed
        were given, and their retries still replay.
        """
        if (package is None) == (document is None):
            raise ConfigurationAPIError(400, "request", proven_pre_effect=True)
        now = datetime.now(timezone.utc)
        fingerprinted = _mutation_fingerprint(
            resource_kind=resource_kind,
            resource_id=resource_id,
            operation=operation,
            reason=reason,
            package=package,
            document=document,
            registered_without_validation=registered_without_validation,
        )
        receipt = MutationReceipt(
            receipt_id=f"m-{uuid4().hex}",
            actor=actor,
            key_digest=sha256(idempotency_key.encode()).hexdigest(),
            operation=operation,
            request_fingerprint=sha256(canonical_json_bytes(fingerprinted)).hexdigest(),
            reason=reason,
            resource_kind=resource_kind,
            resource_id=resource_id,
            created_at=now,
            updated_at=now,
        )
        projection = self._store_projection()
        stored, created = projection.reserve_mutation(receipt, secrets=self._secrets)
        if not created:
            if (
                stored.resource_kind != receipt.resource_kind
                or stored.resource_id != receipt.resource_id
                or stored.operation != receipt.operation
                or stored.request_fingerprint != receipt.request_fingerprint
            ):
                self._audit(actor, operation, reason, "refused-idempotency", *_configuration_of(receipt, None))
                raise ServiceAPIError(
                    409, "idempotency-conflict", "Idempotency-Key was already used by this actor for different content"
                )
            if stored.state == "accepted":
                assert stored.response_status is not None
                assert stored.response_body is not None
                self._audit(actor, operation, reason, "replayed", *_configuration_of(stored, stored.response_body))
                return stored.response_status, stored.response_body
        if not projection.claim_mutation(stored.receipt_id, secrets=self._secrets):
            self._audit(actor, operation, reason, "refused-idempotency-in-progress", *_configuration_of(receipt, None))
            raise ServiceAPIError(409, "idempotency-in-progress", "the matching request is still being processed")
        try:
            content = _registrable_document(document) if document is not None else cast("dict[str, Any]", package)
            if operation == "register-config":
                result = self.register(
                    content, actor=actor, reason=reason, registered_without_validation=registered_without_validation
                )
                response_status = 201
                event_type = CONFIG_REGISTERED
            else:
                result = self.create_version(
                    resource_id,
                    content,
                    actor=actor,
                    reason=reason,
                    registered_without_validation=registered_without_validation,
                )
                response_status = 201 if result.created else 200
                event_type = CONFIG_VERSION_REGISTERED if result.created else CONFIG_VERSION_RETRIEVED
        except ConfigurationAPIError as error:
            if error.proven_pre_effect:
                projection.release_mutation(stored.receipt_id, secrets=self._secrets)
            self._audit(actor, operation, reason, "unavailable", *_configuration_of(receipt, None))
            raise
        except ServiceAPIError as error:
            # A name in use is refused before anything is stored, so the same key can be sent
            # again, for example after the package is renamed.
            projection.release_mutation(stored.receipt_id, secrets=self._secrets)
            self._audit(actor, operation, reason, f"refused-{error.code}", *_configuration_of(receipt, None))
            raise
        response = jsonable_encoder(result)
        config_id, registry_version = _configuration_of(receipt, response)
        completed = projection.complete_mutation(
            stored.receipt_id,
            response_status=response_status,
            response_body=response,
            flow_run_id=None,
            event=config_event(
                event_type=event_type,
                actor=actor,
                operation=operation,
                reason=reason,
                outcome="accepted",
                config_id=config_id,
                registry_version=registry_version,
            ),
            secrets=self._secrets,
        )
        assert completed.response_body is not None
        assert completed.response_status is not None
        return completed.response_status, completed.response_body

    @_provider_error_boundary
    def audit_refusal(self, actor: str, operation: str, reason: str | None, *, config_id: str | None = None) -> None:
        """Record a refused configuration mutation without reserving it.

        ``config_id`` names the configuration a refused version request was for; a refused
        registration has none yet.
        """
        self._audit(actor, operation, self._recordable(reason), "refused-authorization", config_id)

    @_provider_error_boundary
    def require_safe_reason(
        self, actor: str, operation: str, reason: str | None, *, config_id: str | None = None
    ) -> None:
        """Refuse a ``reason`` that holds a secret of the process or the prefix of a token.

        The check runs before anything records the reason, and the event of the refusal holds no
        reason.

        Raises:
            ServiceAPIError: ``422 secret-parameter-refused``.
        """
        if self._recordable(reason) != reason:
            self._audit(actor, operation, None, "refused-secret-parameter", config_id)
            raise ServiceAPIError(422, "secret-parameter-refused", REASON_REFUSED_MESSAGE)

    def _recordable(self, reason: str | None) -> str | None:
        """Return the reason when a record can hold it as it was sent, else nothing."""
        return reason if reason is None or is_free_text(reason, self._secrets) else None

    def _audit(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        actor: str,
        operation: str,
        reason: str | None,
        outcome: str,
        config_id: str | None = None,
        registry_version: int | None = None,
    ) -> None:
        """Record the event of a configuration decision that saves nothing else, such as a refusal."""
        self._store_projection().record_audit(
            config_event(
                event_type=config_decision_type(outcome),
                actor=actor,
                operation=operation,
                reason=reason,
                outcome=outcome,
                config_id=config_id,
                registry_version=registry_version,
            ),
            secrets=self._secrets,
        )

    def _store_projection(self) -> ProductProjection:
        """Return the injected durable projection this server writes receipts and audit to."""
        return self._projection


def _mutation_fingerprint(
    *,
    resource_kind: str,
    resource_id: str,
    operation: str,
    reason: str | None,
    package: dict[str, Any] | None,
    document: str | None,
    registered_without_validation: bool,
) -> dict[str, Any]:
    """Return the values a configuration mutation's request fingerprint digests.

    A document is fingerprinted by a digest of its text, and the mark only when it is ``true``,
    so a package request without the mark keeps the fingerprint it had before either existed.
    """
    fingerprinted: dict[str, Any] = {
        "resource_kind": resource_kind,
        "resource_id": resource_id,
        "operation": operation,
        "reason": reason,
    }
    if document is None:
        fingerprinted["package"] = package
    else:
        # A digest, not the text: canonical JSON cannot encode a lone surrogate, which the
        # parser then refuses as `invalid_package`, as the check route does.
        fingerprinted["document_sha256"] = sha256(document.encode("utf-8", "surrogatepass")).hexdigest()
    if registered_without_validation:
        fingerprinted["registered_without_validation"] = True
    return fingerprinted


def _configuration_of(receipt: MutationReceipt, response: Mapping[str, Any] | None) -> tuple[str | None, int | None]:
    """Return the configuration and version a configuration decision is about, where known.

    A stored response names both. Without one, a version request names its configuration
    and a registration names none yet.
    """
    version = None if response is None else response.get("version")
    if isinstance(version, Mapping):
        config_id, registry_version = version.get("config_id"), version.get("registry_version")
        if isinstance(config_id, str) and isinstance(registry_version, int):
            return config_id, registry_version
    if receipt.resource_kind == "configuration":
        return receipt.resource_id, None
    return None, None


def configuration_router(routes: ConfigurationRoutes, authenticate: Any, idempotency_key: Any) -> APIRouter:
    """Create the eight authenticated configuration resources."""
    router = APIRouter()
    config_id_parameter = APIPath(pattern=CONFIG_ID_PATTERN)
    registry_version_parameter = APIPath()
    page_offset = Query()
    page_limit = Query()
    branch_parameter = Query()

    @router.post("/configs", status_code=201, responses=_MUTATION_ERRORS)
    def register(
        body: ConfigMutationRequest,
        principal: Annotated[Principal, Depends(authenticate)],
        key: Annotated[str, Depends(idempotency_key)],
    ) -> Any:
        if not principal.administrator:
            routes.audit_refusal(principal.actor, "register-config", body.reason)
            raise ServiceAPIError(403, "forbidden", "administrator access is required")
        routes.require_safe_reason(principal.actor, "register-config", body.reason)
        status, content = routes.mutate(
            actor=principal.actor,
            idempotency_key=key,
            operation="register-config",
            resource_kind="configuration-registry",
            resource_id="configs",
            package=body.package,
            document=body.document,
            reason=body.reason,
            registered_without_validation=body.registered_without_validation,
        )
        return JSONResponse(status_code=status, content=content)

    def check_administrator(principal: Annotated[Principal, Depends(authenticate)]) -> Principal:
        """Refuse a package check by a caller that is not an administrator.

        A dependency, so the refusal comes before FastAPI validates the body and before any
        parsing or destination read: with a branch, the check connects to the address the
        package declares with the destination credential this server holds (critique E1).
        """
        if not principal.administrator:
            routes.audit_refusal(principal.actor, "check-config-package", None)
            raise ServiceAPIError(403, "forbidden", "administrator access is required")
        return principal

    # Registered before the `/configs/{config_id}` routes: `validate` is a valid configuration ID.
    @router.post("/configs/validate")
    def check_package(
        body: PackageCheckRequest,
        _principal: Annotated[Principal, Depends(check_administrator)],
        offset: Annotated[str, page_offset] = "0",
        limit: Annotated[str, page_limit] = str(_MAX_VALIDATION_FINDINGS_PAGE),
        branch: Annotated[str | None, branch_parameter] = None,
    ) -> PackageCheckResource:
        """Validate a package without registering it; an administrator token is required."""
        return routes.check_package(
            package=body.package,
            document=body.document,
            offset=parse_bounded_integer(offset, minimum=0, maximum=MAX_REGISTRY_VERSION),
            limit=parse_bounded_integer(limit, minimum=1, maximum=_MAX_VALIDATION_FINDINGS_PAGE),
            branch=parse_branch(branch),
        )

    @router.post("/configs/{config_id}/versions", status_code=201, responses=_MUTATION_ERRORS)
    def create_version(
        config_id: Annotated[str, config_id_parameter],
        body: ConfigMutationRequest,
        principal: Annotated[Principal, Depends(authenticate)],
        key: Annotated[str, Depends(idempotency_key)],
    ) -> Any:
        if not principal.administrator:
            routes.audit_refusal(principal.actor, "create-config-version", body.reason, config_id=config_id)
            raise ServiceAPIError(403, "forbidden", "administrator access is required")
        routes.require_safe_reason(principal.actor, "create-config-version", body.reason, config_id=config_id)
        status, content = routes.mutate(
            actor=principal.actor,
            idempotency_key=key,
            operation="create-config-version",
            resource_kind="configuration",
            resource_id=config_id,
            package=body.package,
            document=body.document,
            reason=body.reason,
            registered_without_validation=body.registered_without_validation,
        )
        return JSONResponse(status_code=status, content=content)

    @router.get("/configs")
    def list_configs(
        _principal: Annotated[Principal, Depends(authenticate)],
        name: Annotated[str | None, Query()] = None,
        limit: Annotated[str | None, page_limit] = None,
        cursor: Annotated[str | None, Query()] = None,
    ) -> ConfigurationPage:
        """Return one page of configurations, newest first, optionally only those named ``name``."""
        return routes.list_configs(name=name, cursor=cursor, limit=parse_page_limit(limit))

    @router.get("/configs/{config_id}")
    def get_config(
        config_id: Annotated[str, config_id_parameter], _principal: Annotated[Principal, Depends(authenticate)]
    ) -> Any:
        return routes.get_config(config_id)

    @router.get("/configs/{config_id}/versions")
    def list_versions(
        config_id: Annotated[str, config_id_parameter],
        _principal: Annotated[Principal, Depends(authenticate)],
        limit: Annotated[str | None, page_limit] = None,
        cursor: Annotated[str | None, Query()] = None,
    ) -> ConfigurationVersionPage:
        """Return one page of a configuration's version summaries, newest version first."""
        return routes.list_versions(config_id, cursor=cursor, limit=parse_page_limit(limit))

    @router.get("/configs/{config_id}/versions/{registry_version}")
    def get_version(
        config_id: Annotated[str, config_id_parameter],
        registry_version: Annotated[str, registry_version_parameter],
        _principal: Annotated[Principal, Depends(authenticate)],
    ) -> Any:
        return routes.get_version(
            config_id, parse_bounded_integer(registry_version, minimum=1, maximum=MAX_REGISTRY_VERSION)
        )

    @router.post("/configs/{config_id}/versions/{registry_version}/validate")
    def validate(
        config_id: Annotated[str, config_id_parameter],
        registry_version: Annotated[str, registry_version_parameter],
        _principal: Annotated[Principal, Depends(authenticate)],
        offset: Annotated[str, page_offset] = "0",
        limit: Annotated[str, page_limit] = str(_MAX_VALIDATION_FINDINGS_PAGE),
        branch: Annotated[str | None, branch_parameter] = None,
    ) -> Any:
        return routes.validate(
            config_id,
            parse_bounded_integer(registry_version, minimum=1, maximum=MAX_REGISTRY_VERSION),
            offset=parse_bounded_integer(offset, minimum=0, maximum=MAX_REGISTRY_VERSION),
            limit=parse_bounded_integer(limit, minimum=1, maximum=_MAX_VALIDATION_FINDINGS_PAGE),
            branch=parse_branch(branch),
        )

    return router
