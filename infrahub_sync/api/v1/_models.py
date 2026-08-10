"""Typed records for the version 1 local Python API."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer,
    model_validator,
)

from infrahub_sync.execution import collect_secret_values, redact

API_VERSION: Literal["1"] = "1"
Operation = Literal["plan", "sync", "verify", "apply"]


def _secret_length(value: str) -> int:
    """Return the replacement-order length of one collected secret."""
    return len(value)


def _merged_secrets(values: Sequence[str]) -> tuple[str, ...]:
    """Merge current boundary secrets in longest-first replacement order."""
    merged: dict[str, None] = dict.fromkeys((*values, *collect_secret_values()))
    return tuple(sorted(merged, key=_secret_length, reverse=True))


def _redact_data(value: Any, secrets: Sequence[str]) -> Any:
    """Return a copy of JSON-like data with collected credential values redacted."""
    if isinstance(value, str):
        return redact(value, secrets)
    if isinstance(value, BaseModel):
        return _redact_data(value.model_dump(), secrets)
    if isinstance(value, Mapping):
        redacted: dict[Any, Any] = {}
        for key, item in value.items():
            safe_key = redact(key, secrets) if isinstance(key, str) else key
            candidate = safe_key
            suffix = 2
            while candidate in redacted:
                candidate = f"{safe_key} [{suffix}]"
                suffix += 1
            redacted[candidate] = _redact_data(item, secrets)
        return redacted
    if isinstance(value, (list, tuple)):
        return [_redact_data(item, secrets) for item in value]
    if isinstance(value, (set, frozenset)):
        redacted_items = (_redact_data(item, secrets) for item in value)
        return frozenset(redacted_items) if isinstance(value, frozenset) else set(redacted_items)
    return value


class _Request(BaseModel):
    """Common strict, immutable request behavior."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    sync_name: str = Field(min_length=1)
    config_directory: str = Field(min_length=1)
    product_cache_location: str | None = None

    @field_validator("product_cache_location")
    @classmethod
    def _require_absolute_product_cache(cls, value: str | None) -> str | None:
        if value is not None and not Path(value).expanduser().is_absolute():
            msg = "product_cache_location must be absolute after user expansion"
            raise ValueError(msg)
        return value


class PlanRequest(_Request):
    """Inputs for creating a saved local plan."""

    branch: str | None = None


class SyncRequest(_Request):
    """Inputs for a confirmed plan, verify, and apply composition."""

    branch: str | None = None
    confirm_writes: bool = False


class VerifyRequest(_Request):
    """Inputs for independently verifying an existing saved plan."""

    run_id: str = Field(min_length=1)


class ApplyRequest(_Request):
    """Inputs for applying the exact saved plan whose checksum was reviewed."""

    run_id: str = Field(min_length=1)
    expected_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    branch: str | None = None


class ActionCounts(BaseModel):
    """Zero-filled saved-operation counts grouped by action."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    create: int = Field(default=0, ge=0)
    update: int = Field(default=0, ge=0)
    delete: int = Field(default=0, ge=0)


class ArtifactReference(BaseModel):
    """One local artifact produced or consumed by a run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: str = Field(min_length=1)
    path: str = Field(min_length=1)


class LifecycleEvent(BaseModel):
    """Structured lifecycle fields read from a standard-library log record.

    ``stage`` and ``outcome`` intentionally remain strings. Readers therefore
    preserve values introduced by later API releases instead of rejecting them.
    Additional fields are also retained for forward-compatible log consumers.
    """

    model_config = ConfigDict(extra="allow", frozen=True)

    api_version: Literal["1"] = API_VERSION
    run_id: str = Field(min_length=1)
    operation: Operation
    stage: str = Field(min_length=1)
    outcome: str = Field(min_length=1)


class RunResult(BaseModel):
    """Stable success result shared by every local product operation.

    ``phase`` and ``outcome`` are forward-tolerant strings. The operation
    vocabulary is closed to the four product operations supported by API v1.
    """

    model_config = ConfigDict(extra="allow", frozen=True)

    api_version: Literal["1"] = API_VERSION
    run_id: str = Field(min_length=1)
    operation: Operation
    phase: str = Field(min_length=1)
    outcome: str = Field(min_length=1)
    counts: ActionCounts
    domain_summary: dict[str, int]
    artifacts: tuple[ArtifactReference, ...]

    @model_validator(mode="before")
    @classmethod
    def _redact_current_values(cls, data: Any) -> Any:
        """Remove current process credentials before public attributes are built."""
        return _redact_data(data, collect_secret_values())

    def _with_secret_values(self, values: Sequence[str]) -> RunResult:
        """Return a result whose public fields contain no boundary credentials."""
        secrets = _merged_secrets(values)
        return type(self).model_validate(_redact_data(self.model_dump(), secrets))

    @model_serializer(mode="wrap")
    def _serialize_redacted(self, handler: SerializerFunctionWrapHandler) -> Any:
        """Redact credential values from every serialized result field."""
        return _redact_data(handler(self), collect_secret_values())


class RunError(Exception):
    """Secret-safe base error returned by the local API boundary."""

    def __init__(
        self,
        message: str,
        *,
        operation: Operation,
        stage: str,
        run_id: str | None,
        secrets: Sequence[str] = (),
    ) -> None:
        current_secrets = _merged_secrets(secrets)
        self.api_version = API_VERSION
        self.run_id = None if run_id is None else redact(run_id, current_secrets)
        self.operation = operation
        self.stage = stage
        self.outcome = "failed"
        self.message = redact(message, current_secrets)
        super().__init__(self.message)

    def model_dump(self) -> dict[str, str | None]:
        """Serialize the structured public error with current redaction values."""
        data = {
            "api_version": self.api_version,
            "run_id": self.run_id,
            "operation": self.operation,
            "stage": self.stage,
            "outcome": self.outcome,
            "message": self.message,
        }
        return _redact_data(data, collect_secret_values())


class RunValidationError(RunError):
    """A request, configuration, or saved-plan safety refusal."""


class RunExecutionError(RunError):
    """An adapter or engine failure after request validation passed."""
