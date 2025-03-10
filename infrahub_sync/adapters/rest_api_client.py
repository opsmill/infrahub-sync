from __future__ import annotations

from typing import Any

import requests


class RestApiClient:
    def __init__(
        self,
        base_url: str,
        auth_method: str,
        api_token: str | None = None,
        username: str | None = None,
        password: str | None = None,
        timeout: int | None = 30,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        # Determine authentication method, some are use by more than one API.
        # Example:
        # -> Peering Manager
        if auth_method == "token" and api_token:
            self.headers["Authorization"] = f"Token {api_token}"
        # -> LibreNMS
        elif auth_method == "x-auth-token" and api_token:
            self.headers["X-Auth-Token"] = api_token
        # -> Peering DB
        elif auth_method == "api-key" and api_token:
            self.headers["Authorization"] = f"Api-Key {api_token}"
        # -> RIPE API
        elif auth_method == "key" and api_token:
            self.headers["Authorization"] = f"Key {api_token}"
        # -> Observium
        elif auth_method == "basic" and username and password:
            self.auth = (username, password)
        else:
            msg = "Invalid authentication configuration!"
            raise ValueError(msg)

        self.timeout = timeout

    def request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> Any:
        """Make a request to the REST API."""
        url = f"{self.base_url}/{endpoint.lstrip('/')}"

        try:
            if hasattr(self, "auth"):
                response = requests.request(
                    method=method,
                    url=url,
                    headers=self.headers,
                    params=params,
                    json=data,
                    auth=self.auth,
                    timeout=self.timeout,
                )
            else:
                response = requests.request(
                    method=method,
                    url=url,
                    headers=self.headers,
                    params=params,
                    json=data,
                    timeout=self.timeout,
                )

            response.raise_for_status()  # Raise an HTTPError for bad responses

            try:
                return response.json()
            except requests.exceptions.JSONDecodeError as exc:
                print("Response content is not valid JSON:", response.text)  # Print the response content
                msg = "Response content is not valid JSON."
                raise ValueError(msg) from exc

        except requests.exceptions.RequestException as exc:
            msg = f"API request failed: {exc!s}"
            raise ConnectionError(msg) from exc

    def get(self, endpoint: str, params: dict[str, Any] | None = None) -> Any:
        return self.request("GET", endpoint, params=params)

    def post(self, endpoint: str, data: dict[str, Any] | None = None) -> Any:
        return self.request("POST", endpoint, data=data)

    def patch(self, endpoint: str, data: dict[str, Any] | None = None) -> Any:
        return self.request("PATCH", endpoint, data=data)

    def put(self, endpoint: str, data: dict[str, Any] | None = None) -> Any:
        return self.request("PUT", endpoint, data=data)

    def delete(self, endpoint: str) -> Any:
        return self.request("DELETE", endpoint)
