"""Read-only Cisco Catalyst SD-WAN Manager session client and validated source settings."""

from __future__ import annotations

import math
import os
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Literal
from urllib.parse import parse_qsl, urlsplit

import requests
import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from infrahub_sync.adapters.rest_api_client import RestApiClient

logger = structlog.get_logger(__name__)

URL_ENV = "CISCO_SDWAN_URL"
DATA_PREFIX = "/dataservice/"


class CiscosdwanSettings(BaseModel):
    """Connection settings; credential fields contain environment variable names."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    url: str | None = None
    username_env: str = "CISCO_SDWAN_USERNAME"
    password_env: str = "CISCO_SDWAN_PASSWORD"
    verify_ssl: bool = True
    timeout: float = Field(default=30, gt=0, allow_inf_nan=False)
    retries: int = Field(default=3, ge=0)
    max_retry_delay: float = Field(default=60, gt=0, allow_inf_nan=False)


class CiscosdwanError(ValueError):
    """A Cisco SD-WAN Manager failure whose message excludes credentials and response bodies."""


class CiscosdwanHTTPError(CiscosdwanError):
    """An HTTP failure with a status code usable by callers."""

    def __init__(self, status: int, resource: str, reason: str) -> None:
        self.status = status
        super().__init__(f"Cisco SD-WAN: {resource}: HTTP {status}: {reason}")


def environment_value(name: str) -> str:
    """Read a required variable without including its value in failures."""
    value = os.environ.get(name)
    if not value:
        msg = f"Cisco SD-WAN: required environment variable {name} is not set"
        raise CiscosdwanError(msg)
    return value


def _is_html(response: requests.Response) -> bool:
    """SD-WAN Manager answers an unauthenticated data request with its HTML login page."""
    return "text/html" in response.headers.get("Content-Type", "").lower()


class CiscosdwanClient(RestApiClient):
    """GET-only client with session login, XSRF token handling and pageInfo pagination."""

    def __init__(self, settings: dict[str, Any]) -> None:  # pylint: disable=super-init-not-called
        try:
            self.options = CiscosdwanSettings.model_validate(settings)
        except ValidationError as exc:
            names = ", ".join(sorted({".".join(str(part) for part in error["loc"]) for error in exc.errors()}))
            msg = f"Cisco SD-WAN: invalid source settings: {names}"
            raise CiscosdwanError(msg) from None
        url = os.environ.get(URL_ENV) or self.options.url
        if not url:
            msg = f"Cisco SD-WAN: set the url setting or the {URL_ENV} environment variable"
            raise CiscosdwanError(msg)
        origin = urlsplit(url.strip().rstrip("/"))
        extra_components = any((origin.username, origin.password, origin.path, origin.query, origin.fragment))
        if origin.scheme != "https" or not origin.hostname or extra_components:
            msg = "Cisco SD-WAN: url must be an HTTPS origin without credentials, path, query or fragment"
            raise CiscosdwanError(msg)
        self.base_url = f"{origin.scheme}://{origin.netloc}"
        self.timeout = self.options.timeout
        self._username = environment_value(self.options.username_env)
        self._password = environment_value(self.options.password_env)
        self._session: requests.Session | None = None
        self.request_count = 0

    @staticmethod
    def resource_path(endpoint: str) -> tuple[str, dict[str, str]]:
        """Split a mapping into an absolute `/dataservice/` path and its query parameters."""
        parts = urlsplit(endpoint.strip())
        path = parts.path.lstrip("/")
        path = path.removeprefix(DATA_PREFIX.strip("/") + "/")
        if parts.scheme or parts.netloc or parts.fragment or not path or ".." in path.split("/"):
            msg = f"Cisco SD-WAN: mapping '{endpoint}' must be a path under {DATA_PREFIX}, such as 'device'"
            raise CiscosdwanError(msg)
        return DATA_PREFIX + path, dict(parse_qsl(parts.query, keep_blank_values=True))

    @staticmethod
    def _retry_after(response: requests.Response) -> float | None:
        """Return a valid server-requested delay, or None for default backoff."""
        value = response.headers.get("Retry-After", "")
        try:
            delay = float(value)
        except ValueError:
            try:
                delay = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
            except (ValueError, TypeError, OverflowError):
                return None
        return max(0.0, delay) if math.isfinite(delay) else None

    def _perform(
        self, session: requests.Session, method: str, path: str, params: dict[str, Any] | None, *, login: bool
    ) -> requests.Response:
        # The login form is built only in this frame. Errors raised from `_send` and its callers then
        # never hold the password in a local variable that a traceback with locals would print.
        form = {"j_username": self._username, "j_password": self._password} if login else None
        return session.request(
            method, self.base_url + path, params=params, data=form, timeout=self.timeout, allow_redirects=False
        )

    def _send(
        self,
        session: requests.Session,
        method: Literal["GET", "POST"],
        path: str,
        *,
        params: dict[str, Any] | None = None,
        login: bool = False,
    ) -> requests.Response:
        """Send one request with bounded retries for timeouts, rate limits and server errors."""
        for attempt in range(self.options.retries + 1):
            self.request_count += 1
            logger.debug("Cisco SD-WAN request", method=method, resource=path, requests=self.request_count)
            try:
                response = self._perform(session, method, path, params, login=login)
            except requests.exceptions.SSLError:
                msg = (
                    f"Cisco SD-WAN: {path}: TLS certificate verification failed; trust the Manager certificate "
                    "authority or set verify_ssl: false for a lab Manager"
                )
                raise CiscosdwanError(msg) from None
            except (requests.Timeout, requests.ConnectionError):
                if attempt == self.options.retries:
                    msg = f"Cisco SD-WAN: {path}: timeout or connection failure after {attempt + 1} attempts"
                    raise CiscosdwanError(msg) from None
                time.sleep(min(2**attempt, self.options.max_retry_delay))
                continue
            except requests.RequestException:
                msg = f"Cisco SD-WAN: {path}: request failed"
                raise CiscosdwanError(msg) from None
            status = response.status_code
            if not isinstance(status, int):
                msg = f"Cisco SD-WAN: {path}: response lacks an HTTP status"
                raise CiscosdwanError(msg)
            if status == 429 or status >= 500:
                if attempt == self.options.retries:
                    reason = "rate limit retry budget exhausted" if status == 429 else "server retries exhausted"
                    raise CiscosdwanHTTPError(status, path, reason)
                delay = self._retry_after(response)
                if delay is not None and delay > self.options.max_retry_delay:
                    reason = (
                        f"Retry-After of {delay:.0f}s exceeds max_retry_delay of {self.options.max_retry_delay:.0f}s"
                    )
                    raise CiscosdwanHTTPError(status, path, reason)
                if delay is None:
                    delay = min(2**attempt, self.options.max_retry_delay)
                logger.warning("Cisco SD-WAN retry", resource=path, status=status, delay=delay)
                time.sleep(delay)
                continue
            return response
        msg = f"Cisco SD-WAN: {path}: retry budget exhausted"
        raise CiscosdwanError(msg)

    @staticmethod
    def _login_rejected(response: requests.Response) -> bool:
        """A rejected login still answers HTTP 200, with an HTML page or a JSON error body."""
        body = response.text.strip()
        if not body:
            return False
        if "<html" in body.lower():
            return True
        try:
            payload = response.json()
        except ValueError:
            return False
        return isinstance(payload, dict) and "error" in payload

    def login(self) -> requests.Session:
        """Open a session with form login, then attach the XSRF token the Manager issues for it."""
        session = requests.Session()
        session.verify = self.options.verify_ssl
        session.headers["Accept"] = "application/json"
        response = self._send(session, "POST", "/j_security_check", login=True)
        if response.status_code != 200 or self._login_rejected(response):
            session.close()
            status = "" if response.status_code == 200 else f" (HTTP {response.status_code})"
            msg = (
                f"Cisco SD-WAN: the Manager rejected the login{status}; check the "
                f"{self.options.username_env} and {self.options.password_env} environment variables"
            )
            raise CiscosdwanError(msg)
        token = self._send(session, "GET", DATA_PREFIX + "client/token")
        if token.status_code == 200 and not _is_html(token) and token.text.strip():
            session.headers["X-XSRF-TOKEN"] = token.text.strip()
        elif token.status_code == 404:
            logger.info("Cisco SD-WAN Manager issues no XSRF token; continuing without one")
        else:
            session.close()
            msg = f"Cisco SD-WAN: login failed; the Manager rejected the new session (HTTP {token.status_code})"
            raise CiscosdwanError(msg)
        self._session = session
        logger.info("Cisco SD-WAN session opened", manager=urlsplit(self.base_url).hostname)
        return session

    def close(self) -> None:
        """Log out so the session does not count against the Manager session limit."""
        session, self._session = self._session, None
        if session is None:
            return
        try:
            self.request_count += 1
            response = session.post(self.base_url + "/logout", timeout=self.timeout, allow_redirects=False)
            if response.status_code == 405:
                self.request_count += 1
                response = session.get(
                    self.base_url + "/logout",
                    params={"nocache": str(int(time.time()))},
                    timeout=self.timeout,
                    allow_redirects=False,
                )
            logger.info("Cisco SD-WAN session closed", status=response.status_code)
        except requests.RequestException:
            logger.warning("Cisco SD-WAN logout failed; the Manager expires the session after its idle timeout")
        finally:
            session.close()

    @staticmethod
    def _error_reason(response: requests.Response) -> str:
        """Return the Manager's short error message, never the raw body."""
        try:
            payload = response.json()
        except ValueError:
            return "request rejected; check the mapping path and query parameters"
        error = payload.get("error") if isinstance(payload, dict) else None
        message = error.get("message") if isinstance(error, dict) else None
        if isinstance(message, str) and message.strip():
            return " ".join(message.split())[:200]
        return "request rejected; check the mapping path and query parameters"

    def _get(self, path: str, params: dict[str, Any]) -> requests.Response:
        session = self._session or self.login()
        response = self._send(session, "GET", path, params=params)
        if response.status_code in {401, 403} or (response.status_code == 200 and _is_html(response)):
            logger.info("Cisco SD-WAN session rejected; logging in again", resource=path)
            session.close()
            self._session = None
            response = self._send(self.login(), "GET", path, params=params)
        status = response.status_code
        if not isinstance(status, int):
            msg = f"Cisco SD-WAN: {path}: response lacks an HTTP status"
            raise CiscosdwanError(msg)
        if status == 401 or (status == 200 and _is_html(response)):
            raise CiscosdwanHTTPError(401, path, "the Manager rejected the session after a new login")
        if status == 403:
            raise CiscosdwanHTTPError(403, path, "the user lacks permission for this resource")
        if status == 404:
            raise CiscosdwanHTTPError(404, path, "endpoint not found; check the mapping path and Manager version")
        if 300 <= status < 400:
            raise CiscosdwanHTTPError(status, path, "redirect refused")
        if not 200 <= status < 300:
            raise CiscosdwanHTTPError(status, path, self._error_reason(response))
        return response

    @staticmethod
    def _json(response: requests.Response, path: str) -> Any:
        try:
            return response.json()
        except ValueError:
            msg = f"Cisco SD-WAN: {path}: response is not valid JSON"
            raise CiscosdwanError(msg) from None

    @staticmethod
    def _extract(payload: Any, path: str) -> tuple[list[dict[str, Any]], Any]:
        """Return the records and the pageInfo block of one response."""
        page_info = None
        records = payload
        if isinstance(payload, dict) and "data" in payload:
            records = payload["data"]
            page_info = payload.get("pageInfo")
        if isinstance(records, dict):
            records = [records]
        if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
            msg = f"Cisco SD-WAN: {path}: response must contain JSON objects"
            raise CiscosdwanError(msg)
        return records, page_info

    def request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> Any:
        """Read one JSON response; reject the inherited REST mutation helpers unconditionally."""
        if method != "GET" or data is not None:
            msg = "Cisco SD-WAN: this adapter permits only GET data requests"
            raise CiscosdwanError(msg)
        path, query = self.resource_path(endpoint)
        return self._json(self._get(path, {**query, **(params or {})}), path)

    def records(self, endpoint: str) -> list[dict[str, Any]]:
        """Read every record of a mapping, following `pageInfo` pages until `moreEntries` is false."""
        path, params = self.resource_path(endpoint)
        request_start = self.request_count
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        while True:
            page, page_info = self._extract(self._json(self._get(path, params), path), path)
            records.extend(page)
            if not isinstance(page_info, dict):
                break
            if page_info.get("hasMoreData"):
                msg = f"Cisco SD-WAN: {path}: scroll pagination is not supported; choose a pageInfo or unpaged endpoint"
                raise CiscosdwanError(msg)
            if not page_info.get("moreEntries"):
                break
            end_id = page_info.get("endId")
            if end_id is None or str(end_id) in seen:
                msg = f"Cisco SD-WAN: {path}: pagination returned no new endId"
                raise CiscosdwanError(msg)
            seen.add(str(end_id))
            params = {**params, "startId": str(end_id)}
        logger.info(
            "Cisco SD-WAN resource fetched",
            resource=path,
            records=len(records),
            requests=self.request_count - request_start,
            total_requests=self.request_count,
        )
        return records
