"""Typed refusals of the runtime model path, raised before adapter extraction."""

from __future__ import annotations

from types import MappingProxyType


class RuntimeSchemaError(Exception):
    """A registered run cannot build its runtime models from the destination schema."""


class UnsupportedDestinationProfileError(RuntimeSchemaError):
    """The package's destination is outside the admitted runtime-model profile."""


_UNKNOWN_REASON = (
    "Infrahub could not provide the destination schema for branch `{branch}` ({reason}).",
    "Check the destination settings and that Infrahub is running, then start the run again.",
)

# One fixed (cause, next action) template per accessor reason (FR-001). The templates take the
# branch, and for a refused token or a missing permission the cause Infrahub states, nothing else.
_REASON_TEXT: MappingProxyType[str, tuple[str, str]] = MappingProxyType(
    {
        "timeout": (
            "Infrahub did not answer in time while Sync read the destination schema for branch `{branch}`.",
            "Check that Infrahub is running and not overloaded, then start the run again.",
        ),
        "unreachable": (
            "Sync could not reach Infrahub, or Infrahub answered with a server error, while reading the "
            "destination schema for branch `{branch}`.",
            "Check the destination `url` setting and that Infrahub is running, then start the run again.",
        ),
        "unauthorized": (
            "Infrahub refused the destination token while Sync read the schema for branch `{branch}`.",
            "Check the credential that the destination `token` setting references, then start the run again.",
        ),
        "forbidden": (
            "The account of the destination token cannot read the schema for branch `{branch}`.",
            "Give the account the permission Infrahub names, then start the run again.",
        ),
        "credentials": (
            "Sync could not resolve the destination token credential to read the schema for branch `{branch}`.",
            "Make the credential that the destination `token` setting references available to the Sync "
            "worker, then start the run again.",
        ),
        "unconfigured": (
            "The destination settings cannot configure an Infrahub client to read the schema for branch `{branch}`.",
            "Correct the destination `url` and client settings, register the new configuration version, "
            "then start the run again.",
        ),
        "rejected": (
            "Infrahub rejected the schema read for branch `{branch}`, or returned a schema Sync cannot use.",
            "Check that the destination `url` points to an Infrahub server, then start the run again.",
        ),
    }
)


class DestinationSchemaUnavailableError(RuntimeSchemaError):
    """The declared accessor could not deliver a destination schema snapshot.

    ``reason`` is the accessor's own short failure class ("timeout", "unauthorized",
    ...). ``message`` (also the exception text) and ``next_action`` come from one fixed
    template per reason, filled with the branch and, for a refused token or a missing
    permission, the accessor's ``detail``. Nothing else from the failed read crosses this
    boundary.
    """

    def __init__(self, *, reason: str, branch: str, detail: str | None = None) -> None:
        cause, next_action = _REASON_TEXT.get(reason, _UNKNOWN_REASON)
        message = cause.format(branch=branch, reason=reason)
        if detail:
            message = f"{message} Infrahub reports {detail}."
        super().__init__(message)
        self.reason = reason
        self.branch = branch
        self.detail = detail
        self.message = message
        self.next_action = next_action


class DestinationBranchNotFoundError(DestinationSchemaUnavailableError):
    """The destination branch the run reads does not exist in Infrahub (FR-002)."""

    def __init__(self, *, branch: str) -> None:
        super().__init__(reason="nobranch", branch=branch)
        self.message = f"Branch `{branch}` does not exist in Infrahub."
        self.next_action = "Create it, or start the run with `--branch <existing branch>`."
        self.args = (self.message,)


class UnsupportedSchemaSemanticsError(RuntimeSchemaError):
    """The snapshot carries a value outside the closed normalized schema domain."""


class MissingMappedKindError(RuntimeSchemaError):
    """The destination schema does not declare a kind the configuration maps."""


class UnwritableConvergenceIdentityError(RuntimeSchemaError):
    """A mapped kind has no complete writable destination identity."""


class RuntimeModelScopeError(RuntimeSchemaError):
    """A run asked a runtime model plan for a side that plan does not carry."""
