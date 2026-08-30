"""Closed public error taxonomy for the Sync HTTP client."""

from __future__ import annotations

from typing import Any


class SyncClientError(Exception):
    """Base class for every client-owned failure."""


class ClientInputError(SyncClientError):
    def __init__(self, argument: str) -> None:
        self.argument = argument
        super().__init__("invalid client input")


class CompatibilityError(SyncClientError):
    def __init__(self, server_version: str | None = None, api_versions: tuple[str, ...] = ()) -> None:
        self.server_version = server_version
        self.api_versions = api_versions
        super().__init__("the Sync API is not compatible with this client")


class TransportError(SyncClientError):
    def __init__(self, operation: str, *, message: str = "the Sync API transport failed") -> None:
        self.operation = operation
        super().__init__(message)


class ClientTimeoutError(TransportError):
    def __init__(self, operation: str) -> None:
        super().__init__(operation, message="the Sync API request timed out")


class ProtocolError(SyncClientError):
    def __init__(self, operation: str, status: int | None = None) -> None:
        self.operation = operation
        self.status = status
        super().__init__("the Sync API response violated the protocol")


class APIError(SyncClientError):
    def __init__(
        self,
        status: int,
        code: str,
        *,
        run_id: str | None = None,
        mutation_id: str | None = None,
    ) -> None:
        self.status = status
        self.code = code
        self.run_id = run_id
        self.mutation_id = mutation_id
        super().__init__("the Sync API refused the request")


class ConfigsAPIError(APIError):
    def __init__(self, status: int, code: str, family: str, reason: str | None = None) -> None:
        self.family = family
        self.reason = reason
        super().__init__(status, code)


class RunWaitTimeoutError(SyncClientError):
    def __init__(
        self,
        run_id: str,
        *,
        phase: str,
        outcome: str | None,
        execution_state: str | None,
    ) -> None:
        self.run_id = run_id
        self.phase = phase
        self.outcome = outcome
        self.execution_state = execution_state
        super().__init__("the accepted Sync run did not finish before the wait deadline")


class RunTerminalError(SyncClientError):
    def __init__(
        self,
        run_id: str,
        *,
        terminal_state: str,
        terminal_outcome: str,
        phase: str,
        outcome: str | None,
    ) -> None:
        self.run_id = run_id
        self.terminal_state = terminal_state
        self.terminal_outcome = terminal_outcome
        self.phase = phase
        self.outcome = outcome
        super().__init__("the accepted Sync run ended without success")


def exception_public_fields(error: SyncClientError) -> dict[str, Any]:
    """Return the stable machine fields carried by one client error."""
    return {name: value for name, value in vars(error).items() if not name.startswith("_")}
