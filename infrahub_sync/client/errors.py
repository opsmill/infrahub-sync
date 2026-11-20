"""Closed public error taxonomy for the Sync HTTP client."""

from __future__ import annotations


class SyncClientError(Exception):
    """Base class for every client-owned failure."""


class ClientInputError(SyncClientError):
    """Report an invalid explicit client argument before network I/O."""

    def __init__(self, argument: str) -> None:
        self.argument = argument
        super().__init__("invalid client input")


class CompatibilityError(SyncClientError):
    """Report an API version that the client cannot use."""

    def __init__(self, server_version: str | None = None, api_versions: tuple[str, ...] = ()) -> None:
        self.server_version = server_version
        self.api_versions = api_versions
        super().__init__("the Sync API is not compatible with this client")


class TransportError(SyncClientError):
    """Contain a non-timeout HTTP transport failure."""

    def __init__(self, operation: str, *, message: str = "the Sync API transport failed") -> None:
        self.operation = operation
        super().__init__(message)


class ClientTimeoutError(TransportError):
    """Contain an HTTP request timeout."""

    def __init__(self, operation: str) -> None:
        super().__init__(operation, message="the Sync API request timed out")


class ProtocolError(SyncClientError):
    """Report a response that violates the declared HTTP contract."""

    def __init__(self, operation: str, status: int | None = None) -> None:
        self.operation = operation
        self.status = status
        super().__init__("the Sync API response violated the protocol")


class NotSyncAPIError(SyncClientError):
    """Report an answer that does not come from a Sync API, for example a web page or a proxy page.

    The response body is never kept: only the operation, the address called without its query
    string, and the HTTP status.
    """

    def __init__(self, operation: str, url: str, status: int) -> None:
        self.operation = operation
        self.url = url
        self.status = status
        super().__init__("the address did not answer as a Sync API")


# The refusal of a package whose name another configuration already has. The Sync API and the
# CLI write the same message: the CLI rebuilds it from the refusal's ``details``.
CONFIGURATION_NAME_IN_USE = "configuration-name-in-use"


def configuration_name_in_use_message(name: str, config_id: str) -> str:
    """Return the message of a ``configuration-name-in-use`` refusal."""
    return (
        f"A configuration named {name!r} already exists ({config_id}). Did you mean to add a new version "
        "of it? Register the package as a new version of that configuration."
    )


class APIError(SyncClientError):
    """Expose safe machine fields from a general API refusal."""

    def __init__(
        self,
        status: int,
        code: str,
        *,
        run_id: str | None = None,
        mutation_id: str | None = None,
        left_out: int | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        self.status = status
        self.code = code
        self.run_id = run_id
        self.mutation_id = mutation_id
        # Set on a `left-out-confirmation-required` refusal: the count the plan leaves out.
        self.left_out = left_out
        # The structured context of a refusal, such as the configuration that already has the
        # name of a package on `configuration-name-in-use`. Absent from most refusals.
        self.details = details
        super().__init__("the Sync API refused the request")


class ConfigsAPIError(APIError):
    """Expose safe machine fields from a configuration API refusal."""

    def __init__(
        self,
        status: int,
        code: str,
        family: str,
        reason: str | None = None,
        *,
        mutation_id: str | None = None,
    ) -> None:
        self.family = family
        self.reason = reason
        super().__init__(status, code, mutation_id=mutation_id)


class RunWaitTimeoutError(SyncClientError):
    """Report an accepted run that exceeded its bounded wait deadline."""

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
    """Report an accepted run that reached a non-success terminal verdict."""

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
