"""Read-only Webex HTTP transport and validated source configuration."""

from __future__ import annotations

import math
import os
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Literal
from urllib.parse import parse_qs, parse_qsl, quote, quote_plus, urlencode, urljoin, urlsplit, urlunsplit

import requests
import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from infrahub_sync.adapters.rest_api_client import RestApiClient

logger = structlog.get_logger(__name__)


class WebexResource(BaseModel):
    """Per-mapping extraction options, keyed by destination model name."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    response_key: str | None = "items"
    params: dict[str, Any] = Field(default_factory=dict)
    page_size: int | None = Field(default=None, gt=0)
    parent: str | None = None
    details: str | None = None
    read_only: bool = False


class WebexSettings(BaseModel):
    """Connection settings; credential fields contain environment variable names."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    url: str = "https://webexapis.com"
    auth_method: Literal["token", "service_app"] = "token"
    token_env: str = "WEBEX_TOKEN"
    client_id_env: str = "WEBEX_CLIENT_ID"
    client_secret_env: str = "WEBEX_CLIENT_SECRET"
    refresh_token_env: str = "WEBEX_REFRESH_TOKEN"
    timeout: float = Field(default=30, gt=0, allow_inf_nan=False)
    retries: int = Field(default=3, ge=0)
    page_size: int = Field(default=100, gt=0)
    org_id: str | dict[str, str] | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    resources: dict[str, WebexResource] = Field(default_factory=dict)


def environment_value(name: str) -> str:
    """Read a required variable without including its value in failures."""
    value = os.environ.get(name)
    if not value:
        msg = f"Webex: required environment variable {name} is not set"
        raise ValueError(msg)
    return value


def resolve_environment(value: Any) -> Any:
    """Resolve explicit environment references before any requests are sent."""
    if isinstance(value, dict) and set(value) == {"env"} and isinstance(value["env"], str):
        return environment_value(value["env"])
    if isinstance(value, dict):
        return {key: resolve_environment(item) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_environment(item) for item in value]
    return value


class WebexHTTPError(ValueError):
    """A safe Webex HTTP failure with a status usable by resource loaders."""

    def __init__(self, status: int, resource: str, reason: str) -> None:
        self.status = status
        super().__init__(f"Webex: {resource}: HTTP {status}: {reason}")


class WebexClient(RestApiClient):
    """GET-only client with Link pagination and a narrow OAuth refresh exception."""

    def __init__(self, settings: dict[str, Any]) -> None:  # pylint: disable=super-init-not-called
        try:
            self.options = WebexSettings.model_validate(resolve_environment(settings))
        except ValidationError:
            msg = "Webex: invalid source settings; check the documented connection/resource options"
            raise ValueError(msg) from None
        self.base_url = self.options.url.rstrip("/")
        origin = urlsplit(self.base_url)
        extra_components = any((origin.username, origin.password, origin.path, origin.query, origin.fragment))
        if origin.scheme != "https" or not origin.hostname or extra_components:
            msg = "Webex: url must be an HTTPS origin without credentials, path, query or fragment"
            raise ValueError(msg)
        self.timeout = self.options.timeout
        self._token = ""
        self._expires_at = 0.0
        self._client_id = ""
        self._client_secret = ""
        self._refresh_token = ""
        if self.options.auth_method == "token":
            self._token = environment_value(self.options.token_env)
        else:
            self._client_id = environment_value(self.options.client_id_env)
            self._client_secret = environment_value(self.options.client_secret_env)
            self._refresh_token = environment_value(self.options.refresh_token_env)
        self.request_count = 0
        self._credentials: set[str] = {self._token, self._client_id, self._client_secret, self._refresh_token} - {""}
        self._validate_organization()
        self._linked_org_id: str | None = None

    def _validate_organization(self) -> None:
        """Resolve one project scope, allowing resources to omit unsupported orgId."""
        candidates = [self.options.org_id, self.options.params.get("orgId")]
        candidates.extend(item.params.get("orgId") for item in self.options.resources.values())
        organizations = set()
        for value in candidates:
            if value is None:
                continue
            if not isinstance(value, str) or not value.strip():
                msg = "Webex: orgId must be a fixed nonempty organization identifier or environment reference"
                raise ValueError(msg)
            organizations.add(value)
        if len(organizations) > 1:
            msg = "Webex: one organization per project; resource orgId cannot differ from the project organization"
            raise ValueError(msg)
        self.options.org_id = next(iter(organizations), None)

    def _url(self, endpoint: str) -> str:
        url = urljoin(self.base_url + "/", endpoint)
        parts = urlsplit(url)
        origin = urlsplit(self.base_url)
        if (
            (parts.scheme, parts.netloc) != (origin.scheme, origin.netloc)
            or parts.username
            or parts.password
            or parts.fragment
        ):
            msg = "Webex: refused an endpoint, redirect or pagination link outside the configured HTTPS origin"
            raise ValueError(msg)
        linked_orgs = parse_qs(parts.query, keep_blank_values=True).get("orgId", [])
        if linked_orgs:
            organization = self.options.org_id or self._linked_org_id or linked_orgs[0]
            if any(not value.strip() or value != organization for value in linked_orgs):
                msg = "Webex: endpoint or pagination orgId differs from the project organization"
                raise ValueError(msg)
            self._linked_org_id = linked_orgs[0]
        return url

    def _redact(self, text: str) -> str:
        for secret in sorted(self._credentials, key=lambda value: -len(value)):
            for encoded in (secret, quote(secret, safe=""), quote_plus(secret)):
                text = text.replace(encoded, "[redacted]")
        text = re.sub(r"https?://\S+", "[redacted URL]", text, flags=re.IGNORECASE)
        text = re.sub(r"\b(Bearer|Basic)\s+\S+", r"\1 [redacted]", text, flags=re.IGNORECASE)
        text = re.sub(
            r"\b(authorization|(?:access|refresh)[_ -]?token|token|client[_ -]?(?:secret|id)|password|secret|api[_ -]?key)"
            r"[\"']?\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)",
            r"\1=[redacted]",
            text,
            flags=re.IGNORECASE,
        )
        return " ".join(text.split())

    def _resource(self, endpoint: str) -> str:
        return self._redact(urlsplit(endpoint).path)

    @staticmethod
    def _retry_delay(response: requests.Response, attempt: int) -> float:
        value = response.headers.get("Retry-After", "")
        try:
            delay = float(value)
        except ValueError:
            try:
                delay = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
            except (ValueError, TypeError, OverflowError):
                delay = float(2**attempt)
        return max(0.0, delay) if math.isfinite(delay) else float(2**attempt)

    def _perform_request(
        self,
        method: Literal["GET", "POST"],
        url: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> requests.Response:
        if method != "GET" and (
            method != "POST" or self.options.auth_method != "service_app" or url != self.base_url + "/v1/access_token"
        ):
            msg = "Webex: only GET data requests and OAuth refresh POST are permitted"
            raise ValueError(msg)
        headers = {"Accept": "application/json"}
        data = None
        if method == "GET":
            headers["Authorization"] = f"Bearer {self._token}"
        else:
            data = {
                "grant_type": "refresh_token",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "refresh_token": self._refresh_token,
            }
        self.request_count += 1
        logger.info("Webex request", resource=self._resource(url), method=method, requests=self.request_count)
        return requests.request(
            method,
            url,
            headers=headers,
            params=params,
            data=data,
            timeout=self.timeout,
            allow_redirects=False,
        )

    def _send(
        self, method: Literal["GET", "POST"], url: str, *, params: dict[str, Any] | None = None
    ) -> requests.Response:
        resource = self._resource(url)
        for attempt in range(self.options.retries + 1):
            try:
                response = self._perform_request(method, url, params=params)
            except (requests.Timeout, requests.ConnectionError):
                if attempt == self.options.retries:
                    msg = f"Webex: {resource}: timeout or connection failure after bounded retries"
                    raise ValueError(msg) from None
                time.sleep(2**attempt)
                continue
            except requests.RequestException:
                msg = f"Webex: {resource}: request failed"
                raise ValueError(msg) from None
            status = response.status_code
            if not isinstance(status, int):
                msg = f"Webex: {resource}: response lacks an HTTP status"
                raise TypeError(msg)
            if status == 429 or status >= 500:
                if attempt < self.options.retries:
                    delay = self._retry_delay(response, attempt)
                    logger.warning("Webex retry", resource=resource, status=status, delay=delay)
                    time.sleep(delay)
                    continue
                reason = "rate limit retry budget exhausted" if status == 429 else "server retries exhausted"
                raise WebexHTTPError(status, resource, reason)
            return response
        msg = "Webex: retry budget exhausted"
        raise ValueError(msg)

    @staticmethod
    def _json(response: requests.Response, resource: str) -> Any:
        try:
            return response.json()
        except (ValueError, requests.exceptions.JSONDecodeError):
            msg = f"Webex: {resource}: response is not valid JSON"
            raise ValueError(msg) from None

    def _refresh(self) -> None:
        response = self._send("POST", self.base_url + "/v1/access_token")
        if response.status_code != 200:
            msg = "Webex: /v1/access_token: service-app authentication failed; authorize the app again"
            raise ValueError(msg)
        try:
            self._accept_token_response(response)
        except (ValueError, KeyError, TypeError, OverflowError):
            msg = "Webex: /v1/access_token: refresh response lacks valid credentials or lifetime"
            raise ValueError(msg) from None

    def _accept_token_response(self, response: requests.Response) -> None:
        payload = self._json(response, "/v1/access_token")
        if (
            not isinstance(payload, dict)
            or not isinstance(payload.get("access_token"), str)
            or not payload["access_token"]
        ):
            msg = "Webex: /v1/access_token: refresh response lacks an access token"
            raise ValueError(msg)
        try:
            lifetime = float(payload["expires_in"])
        except (KeyError, ValueError, TypeError):
            msg = "Webex: /v1/access_token: refresh response lacks a valid lifetime"
            raise ValueError(msg) from None
        if not math.isfinite(lifetime) or lifetime <= 0:
            msg = "Webex: /v1/access_token: refresh response lacks a valid lifetime"
            raise ValueError(msg)
        self._token = payload["access_token"]
        self._credentials.add(self._token)
        self._expires_at = time.monotonic() + lifetime
        if isinstance(payload.get("refresh_token"), str) and payload["refresh_token"]:
            self._refresh_token = payload["refresh_token"]
            self._credentials.add(self._refresh_token)

    def _refresh_for(self, resource: str) -> None:
        try:
            self._refresh()
        except ValueError as exc:
            msg = f"Webex: {resource}: token refresh failed: {exc}"
            raise ValueError(msg) from None

    def _query_error(self, response: requests.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return "request rejected; check endpoint and query settings"
        if isinstance(payload, dict) and isinstance(payload.get("message"), str):
            return f"query rejected: {self._redact(payload['message'])}"
        return "request rejected; check endpoint and query settings"

    def _get_response(self, endpoint: str, params: dict[str, Any] | None = None) -> requests.Response:
        url = self._url(endpoint)
        resource = self._resource(endpoint)
        if params and params.get("orgId") is not None and params["orgId"] != self.options.org_id:
            msg = f"Webex: {resource}: query orgId differs from the project organization"
            raise ValueError(msg)
        if self.options.auth_method == "service_app" and time.monotonic() >= self._expires_at:
            self._refresh_for(resource)
        response = self._send("GET", url, params=params)
        if response.status_code == 401 and self.options.auth_method == "service_app":
            self._refresh_for(resource)
            response = self._send("GET", url, params=params)
        if response.status_code == 401:
            raise WebexHTTPError(401, resource, "authentication failed")
        if response.status_code == 403:
            raise WebexHTTPError(403, resource, "credential lacks permission for this resource")
        status = response.status_code
        if not isinstance(status, int):
            msg = f"Webex: {resource}: response lacks an HTTP status"
            raise TypeError(msg)
        if status == 400:
            raise WebexHTTPError(status, resource, self._query_error(response))
        if not 200 <= status < 300:
            raise WebexHTTPError(status, resource, "request rejected; check endpoint and query settings")
        return response

    def request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> Any:
        """Read JSON; reject inherited REST mutation helpers unconditionally."""
        if method != "GET" or data is not None:
            msg = "Webex: this adapter permits only GET data requests"
            raise ValueError(msg)
        return self._json(self._get_response(endpoint, params), self._resource(endpoint))

    def pages(self, endpoint: str, params: dict[str, Any]) -> list[Any]:
        """Read all pages, including empty intermediate pages, without leaking credentials."""
        parts = urlsplit(endpoint)
        query = [
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if key not in {"before", "after"}
        ]
        endpoint = urlunsplit(parts._replace(query=urlencode(query)))
        params = {key: value for key, value in params.items() if key not in {"before", "after"}}
        request_start = self.request_count
        pages: list[Any] = []
        seen: set[str] = set()
        next_url = self._url(endpoint)
        next_params: dict[str, Any] | None = params
        while next_url:
            prepared = requests.Request("GET", next_url, params=next_params).prepare()
            fingerprint = prepared.url or next_url
            if fingerprint in seen:
                msg = f"Webex: {self._resource(endpoint)}: pagination cycle detected"
                raise ValueError(msg)
            seen.add(fingerprint)
            response = self._get_response(next_url, next_params)
            pages.append(self._json(response, self._resource(endpoint)))
            link = response.links.get("next", {}).get("url")
            next_url = self._url(urljoin(next_url, link)) if link else ""
            linked_params = parse_qs(urlsplit(next_url).query, keep_blank_values=True)
            next_params = {key: value for key, value in params.items() if key not in linked_params} or None
        logger.info(
            "Webex resource fetched",
            resource=self._resource(endpoint),
            requests=self.request_count - request_start,
            total_requests=self.request_count,
        )
        return pages
