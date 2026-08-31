"""Typed refusals of the runtime model path, raised before adapter extraction."""

from __future__ import annotations


class RuntimeSchemaError(Exception):
    """A registered run cannot build its runtime models from the destination schema."""


class UnsupportedDestinationProfileError(RuntimeSchemaError):
    """The package's destination is outside the admitted runtime-model profile."""


class DestinationSchemaUnavailableError(RuntimeSchemaError):
    """The declared accessor could not deliver a destination schema snapshot.

    ``reason`` is the accessor's own short failure class ("timeout", "unauthorized",
    ...). Nothing else from the failed read crosses this boundary.
    """

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


class UnsupportedSchemaSemanticsError(RuntimeSchemaError):
    """The snapshot carries a value outside the closed normalized schema domain."""


class MissingMappedKindError(RuntimeSchemaError):
    """The destination schema does not declare a kind the configuration maps."""


class RuntimeModelScopeError(RuntimeSchemaError):
    """A run asked a runtime model plan for a side that plan does not carry."""
