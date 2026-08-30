"""Synchronous client for the Sync HTTP API."""

from __future__ import annotations

import os
import re
from hashlib import sha256
from math import isfinite
from typing import TYPE_CHECKING, Any, TypeVar, cast

import httpx
from pydantic import BaseModel, ValidationError
from typing_extensions import Self

from .errors import (
    APIError,
    ClientInputError,
    ClientTimeoutError,
    CompatibilityError,
    ConfigsAPIError,
    ProtocolError,
    TransportError,
)
from .models import (
    ApplyRunRequest,
    ArtifactContent,
    ArtifactListResource,
    CancelRunRequest,
    ConfigErrorEnvelope,
    ConfigMutationRequest,
    ConfigurationSummaryResource,
    ConfigurationVersionResource,
    CreateRunRequest,
    ErrorEnvelope,
    PlanResource,
    RegisteredConfigurationResource,
    RegisteredVersionResource,
    ResultsResource,
    RunResource,
    ServiceStatusResource,
    ValidationReportResource,
    VerifyRunRequest,
    VersionResource,
)

if TYPE_CHECKING:
    from types import TracebackType

_Model = TypeVar("_Model", bound=BaseModel)
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_DIGEST = re.compile(r"sha-256=(?P<digest>[0-9a-f]{64})")
_ERROR_STATUSES = frozenset({401, 403, 404, 409, 410, 422, 503})
_CONFIG_ERROR_STATUSES = _ERROR_STATUSES | {400}
_MAX_JSON_BYTES = 16 * 1024 * 1024
_SERVICE_URL_ARG = "service_url"
_TOKEN_ARG = "token"
_TIMEOUT_ARG = "timeout"
_OFFSET_ARG = "offset"
_LIMIT_ARG = "limit"
_REQUEST_ARG = "request"
_IDEMPOTENCY_KEY_ARG = "idempotency_key"
_GET_ARTIFACT = "get_artifact"


class SyncClient:
    """One side-effect-free synchronous client for every shipped Sync API route."""

    def __init__(
        self,
        service_url: str,
        token: str,
        *,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        url = self._service_url(service_url)
        if not isinstance(token, str) or not token:
            raise ClientInputError(_TOKEN_ARG)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not isfinite(timeout) or timeout <= 0:
            raise ClientInputError(_TIMEOUT_ARG)
        self._http = httpx.Client(
            base_url=url,
            timeout=float(timeout),
            transport=transport,
            follow_redirects=False,
        )
        self._authorization = f"Bearer {token}"
        self._compatible = False

    @classmethod
    def from_environment(
        cls,
        *,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> SyncClient:
        """Build a client from the documented URL and token environment settings."""
        return cls(
            os.environ.get("INFRAHUB_SYNC_API_URL", ""),
            os.environ.get("INFRAHUB_SYNC_API_TOKEN", ""),
            timeout=timeout,
            transport=transport,
        )

    @staticmethod
    def _service_url(value: object) -> str:
        if not isinstance(value, str):
            raise ClientInputError(_SERVICE_URL_ARG)
        try:
            url = httpx.URL(value)
        except Exception:  # noqa: BLE001 - all URL parser failures are one client-input refusal.
            raise ClientInputError(_SERVICE_URL_ARG) from None
        if (
            url.scheme not in {"http", "https"}
            or not url.host
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ClientInputError(_SERVICE_URL_ARG)
        return str(url).rstrip("/")

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self.close()

    def get_version(self) -> VersionResource:
        response = self._send("get_version", "GET", "/version", authenticated=False)
        if response.status_code != 200:
            raise CompatibilityError
        try:
            return VersionResource.model_validate(self._json(response, "get_version"))
        except (ValidationError, ProtocolError):
            raise CompatibilityError from None

    def get_status(self) -> ServiceStatusResource:
        response = self._send("get_status", "GET", "/status", authenticated=False)
        return self._success(response, "get_status", ServiceStatusResource, {200})

    def register_config(self, request: ConfigMutationRequest, idempotency_key: str) -> RegisteredConfigurationResource:
        return self._request_model(
            "register_config",
            "POST",
            "/configs",
            RegisteredConfigurationResource,
            {201},
            body=request,
            idempotency_key=idempotency_key,
            config_route=True,
        )

    def create_config_version(
        self, config_id: str, request: ConfigMutationRequest, idempotency_key: str
    ) -> RegisteredVersionResource:
        config_id = self._identifier(config_id, "config_id")
        return self._request_model(
            "create_config_version",
            "POST",
            f"/configs/{config_id}/versions",
            RegisteredVersionResource,
            {200, 201},
            body=request,
            idempotency_key=idempotency_key,
            config_route=True,
        )

    def list_configs(self) -> tuple[ConfigurationSummaryResource, ...]:
        return self._request_list("list_configs", "/configs", ConfigurationSummaryResource, config_route=True)

    def get_config(self, config_id: str) -> ConfigurationSummaryResource:
        config_id = self._identifier(config_id, "config_id")
        return self._request_model(
            "get_config", "GET", f"/configs/{config_id}", ConfigurationSummaryResource, {200}, config_route=True
        )

    def list_config_versions(self, config_id: str) -> tuple[ConfigurationVersionResource, ...]:
        config_id = self._identifier(config_id, "config_id")
        return self._request_list(
            "list_config_versions", f"/configs/{config_id}/versions", ConfigurationVersionResource, config_route=True
        )

    def get_config_version(self, config_id: str, registry_version: int) -> ConfigurationVersionResource:
        config_id = self._identifier(config_id, "config_id")
        version = self._positive_integer(registry_version, "registry_version")
        return self._request_model(
            "get_config_version",
            "GET",
            f"/configs/{config_id}/versions/{version}",
            ConfigurationVersionResource,
            {200},
            config_route=True,
        )

    def validate_config(
        self, config_id: str, registry_version: int, *, offset: int = 0, limit: int = 256
    ) -> ValidationReportResource:
        config_id = self._identifier(config_id, "config_id")
        version = self._positive_integer(registry_version, "registry_version")
        if type(offset) is not int or offset < 0:  # pylint: disable=unidiomatic-typecheck
            raise ClientInputError(_OFFSET_ARG)
        if type(limit) is not int or not 1 <= limit <= 256:  # pylint: disable=unidiomatic-typecheck
            raise ClientInputError(_LIMIT_ARG)
        return self._request_model(
            "validate_config",
            "POST",
            f"/configs/{config_id}/versions/{version}/validate",
            ValidationReportResource,
            {200},
            params={"offset": offset, "limit": limit},
            config_route=True,
        )

    def create_run(self, request: CreateRunRequest, idempotency_key: str) -> RunResource:
        return self._request_model(
            "create_run", "POST", "/runs", RunResource, {202}, body=request, idempotency_key=idempotency_key
        )

    def plan(self, request: CreateRunRequest, idempotency_key: str) -> RunResource:
        if request.operation != "plan" or request.confirm_writes:
            raise ClientInputError(_REQUEST_ARG)
        return self.create_run(request, idempotency_key)

    def sync(self, request: CreateRunRequest, idempotency_key: str) -> RunResource:
        if request.operation != "sync" or not request.confirm_writes:
            raise ClientInputError(_REQUEST_ARG)
        return self.create_run(request, idempotency_key)

    def get_run(self, run_id: str) -> RunResource:
        run_id = self._identifier(run_id, "run_id")
        return self._request_model("get_run", "GET", f"/runs/{run_id}", RunResource, {200})

    def get_plan(self, run_id: str) -> PlanResource:
        run_id = self._identifier(run_id, "run_id")
        return self._request_model("get_plan", "GET", f"/runs/{run_id}/plan", PlanResource, {200})

    def get_results(self, run_id: str) -> ResultsResource:
        run_id = self._identifier(run_id, "run_id")
        return self._request_model("get_results", "GET", f"/runs/{run_id}/results", ResultsResource, {200})

    def list_artifacts(self, run_id: str) -> ArtifactListResource:
        run_id = self._identifier(run_id, "run_id")
        return self._request_model("list_artifacts", "GET", f"/runs/{run_id}/artifacts", ArtifactListResource, {200})

    def get_artifact(self, run_id: str, artifact_id: str) -> ArtifactContent:
        run_id = self._identifier(run_id, "run_id")
        artifact_id = self._identifier(artifact_id, "artifact_id")
        self._ensure_compatible()
        response = self._send(_GET_ARTIFACT, "GET", f"/runs/{run_id}/artifacts/{artifact_id}")
        if response.status_code != 200:
            self._raise_response_error(response, _GET_ARTIFACT, config_route=False)
        values = [value.strip() for header in response.headers.get_list("Digest") for value in header.split(",")]
        matches = {match.group("digest") for value in values if (match := _DIGEST.fullmatch(value)) is not None}
        if (
            not values
            or len(matches) != 1
            or len(matches) != len(set(values))
            or sha256(response.content).hexdigest() not in matches
        ):
            raise ProtocolError(_GET_ARTIFACT, response.status_code)
        digest = matches.pop()
        media_type = response.headers.get("Content-Type", "").partition(";")[0].strip()
        if not media_type:
            raise ProtocolError(_GET_ARTIFACT, response.status_code)
        return ArtifactContent(data=response.content, media_type=media_type, digest=digest)

    def verify_run(self, run_id: str, request: VerifyRunRequest, idempotency_key: str) -> RunResource:
        return self._run_mutation("verify_run", run_id, "verify", request, idempotency_key)

    def verify(self, run_id: str, request: VerifyRunRequest, idempotency_key: str) -> RunResource:
        return self.verify_run(run_id, request, idempotency_key)

    def apply_run(self, run_id: str, request: ApplyRunRequest, idempotency_key: str) -> RunResource:
        return self._run_mutation("apply_run", run_id, "apply", request, idempotency_key)

    def apply(self, run_id: str, request: ApplyRunRequest, idempotency_key: str) -> RunResource:
        return self.apply_run(run_id, request, idempotency_key)

    def cancel_run(self, run_id: str, request: CancelRunRequest, idempotency_key: str) -> RunResource:
        return self._run_mutation("cancel_run", run_id, "cancel", request, idempotency_key)

    def _run_mutation(
        self, operation: str, run_id: str, suffix: str, request: BaseModel, idempotency_key: str
    ) -> RunResource:
        run_id = self._identifier(run_id, "run_id")
        return self._request_model(
            operation,
            "POST",
            f"/runs/{run_id}/{suffix}",
            RunResource,
            {202},
            body=request,
            idempotency_key=idempotency_key,
        )

    def _request_list(
        self, operation: str, path: str, model: type[_Model], *, config_route: bool
    ) -> tuple[_Model, ...]:
        self._ensure_compatible()
        response = self._send(operation, "GET", path)
        if response.status_code != 200:
            self._raise_response_error(response, operation, config_route=config_route)
        payload = self._json(response, operation)
        if not isinstance(payload, list):
            raise ProtocolError(operation, response.status_code)
        try:
            return tuple(model.model_validate(item) for item in payload)
        except (ValidationError, TypeError, ValueError):
            raise ProtocolError(operation, response.status_code) from None

    def _request_model(
        self,
        operation: str,
        method: str,
        path: str,
        model: type[_Model],
        success: set[int],
        *,
        body: BaseModel | None = None,
        idempotency_key: str | None = None,
        params: dict[str, int] | None = None,
        config_route: bool = False,
    ) -> _Model:
        if idempotency_key is not None and (not isinstance(idempotency_key, str) or not idempotency_key.strip()):
            raise ClientInputError(_IDEMPOTENCY_KEY_ARG)
        self._ensure_compatible()
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key is not None else None
        response = self._send(
            operation,
            method,
            path,
            headers=headers,
            json=None if body is None else body.model_dump(mode="json"),
            params=params,
        )
        return self._success(response, operation, model, success, config_route=config_route)

    def _success(
        self,
        response: httpx.Response,
        operation: str,
        model: type[_Model],
        success: set[int],
        *,
        config_route: bool = False,
    ) -> _Model:
        if response.status_code not in success:
            self._raise_response_error(response, operation, config_route=config_route)
        try:
            return model.model_validate(self._json(response, operation))
        except (ValidationError, TypeError, ValueError):
            raise ProtocolError(operation, response.status_code) from None

    def _ensure_compatible(self) -> None:
        if self._compatible:
            return
        version = self.get_version()
        if not version.api_versions or "v3-unstable" not in version.api_versions:
            raise CompatibilityError(version.server_version, version.api_versions)
        self._compatible = True

    def _send(
        self,
        operation: str,
        method: str,
        path: str,
        *,
        authenticated: bool = True,
        headers: dict[str, str] | None = None,
        json: object = None,
        params: dict[str, int] | None = None,
    ) -> httpx.Response:
        request_headers = dict(headers or {})
        if authenticated:
            request_headers["Authorization"] = self._authorization
        try:
            return self._http.request(method, path, headers=request_headers, json=json, params=params)
        except httpx.TimeoutException:
            raise ClientTimeoutError(operation) from None
        except httpx.HTTPError:
            raise TransportError(operation) from None
        except Exception:  # noqa: BLE001 - no injected transport exception crosses the public boundary.
            raise TransportError(operation) from None

    @staticmethod
    def _json(response: httpx.Response, operation: str) -> Any:
        if len(response.content) > _MAX_JSON_BYTES:
            raise ProtocolError(operation, response.status_code)
        try:
            return response.json()
        except Exception:  # noqa: BLE001 - response decoders are contained at the external boundary.
            raise ProtocolError(operation, response.status_code) from None

    def _raise_response_error(self, response: httpx.Response, operation: str, *, config_route: bool) -> None:
        accepted = _CONFIG_ERROR_STATUSES if config_route else _ERROR_STATUSES
        if response.is_redirect or response.status_code not in accepted:
            raise ProtocolError(operation, response.status_code)
        payload = self._json(response, operation)
        try:
            error = self._parse_api_error(payload, response.status_code, config_route=config_route)
        except (ValidationError, KeyError, TypeError, ValueError):
            raise ProtocolError(operation, response.status_code) from None
        raise error

    @staticmethod
    def _parse_api_error(payload: object, status: int, *, config_route: bool) -> APIError:
        raw_error = cast("dict[str, object]", payload).get("error") if isinstance(payload, dict) else None
        if config_route and isinstance(raw_error, dict) and "family" in raw_error:
            detail = ConfigErrorEnvelope.model_validate(payload).error
            if detail.status != status:
                raise ValueError
            return ConfigsAPIError(detail.status, detail.code, detail.family, detail.reason)
        detail = ErrorEnvelope.model_validate(payload).error
        if detail.status != status:
            raise ValueError
        return APIError(detail.status, detail.code, run_id=detail.run_id, mutation_id=detail.mutation_id)

    @staticmethod
    def _identifier(value: object, argument: str) -> str:
        if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
            raise ClientInputError(argument)
        return value

    @staticmethod
    def _positive_integer(value: object, argument: str) -> int:
        if type(value) is not int or not 1 <= value <= 2**63 - 1:  # pylint: disable=unidiomatic-typecheck
            raise ClientInputError(argument)
        return value
