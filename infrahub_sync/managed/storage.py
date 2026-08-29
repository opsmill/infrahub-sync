"""Managed-only adapters for PostgreSQL records and S3-compatible artifacts."""

from __future__ import annotations

from typing import Any

from botocore.exceptions import ClientError

from infrahub_sync.product_store import DuplicateArtifactError

_CONDITIONAL_CONFLICT_ATTEMPTS = 3


def _error_code(error: ClientError) -> str | None:
    """Return the SDK service code without reading an error message."""
    value = error.response.get("Error", {}).get("Code")
    return value if isinstance(value, str) else None


class Boto3S3Client:
    """Adapt Boto3's low-level client to the product-store S3 protocol."""

    def __init__(self, client: Any) -> None:
        self._client = client

    def put(self, *, bucket: str, key: str, data: bytes, if_absent: bool = False) -> None:
        """Write one byte object, preserving the create-only remote precondition."""
        arguments: dict[str, object] = {"Bucket": bucket, "Key": key, "Body": data}
        if not if_absent:
            self._client.put_object(**arguments)
            return
        arguments["IfNoneMatch"] = "*"
        for attempt in range(_CONDITIONAL_CONFLICT_ATTEMPTS):
            try:
                self._client.put_object(**arguments)
            except ClientError as error:
                code = _error_code(error)
                if code == "PreconditionFailed":
                    msg = "S3 object already exists"
                    raise DuplicateArtifactError(msg) from None
                if code == "ConditionalRequestConflict" and attempt + 1 < _CONDITIONAL_CONFLICT_ATTEMPTS:
                    continue
                raise
            return

    def get(self, *, bucket: str, key: str) -> bytes | None:
        """Return exact object bytes, mapping only the exact missing-object response."""
        try:
            response = self._client.get_object(Bucket=bucket, Key=key)
        except ClientError as error:
            if _error_code(error) == "NoSuchKey":
                return None
            raise
        return response["Body"].read()

    def copy(self, *, bucket: str, source: str, destination: str) -> None:
        """Copy exactly one object within the configured bucket."""
        self._client.copy_object(Bucket=bucket, Key=destination, CopySource={"Bucket": bucket, "Key": source})

    def delete(self, *, bucket: str, key: str) -> None:
        """Delete exactly one object key."""
        self._client.delete_object(Bucket=bucket, Key=key)
