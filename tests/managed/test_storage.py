"""Managed-only durable storage adapter contracts."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from botocore.exceptions import ClientError

from infrahub_sync.product_store import DuplicateArtifactError


def _client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "sdk-secret-canary"}}, "PutObject")


class _Body:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


class _SDK:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.put_calls: list[dict[str, object]] = []
        self.copy_calls: list[dict[str, object]] = []
        self.delete_calls: list[dict[str, object]] = []
        self.put_failure: Callable[[], None] | None = None

    def put_object(self, **kwargs: object) -> None:
        self.put_calls.append(kwargs)
        if self.put_failure is not None:
            self.put_failure()
        self.objects[(str(kwargs["Bucket"]), str(kwargs["Key"]))] = bytes(kwargs["Body"])

    def get_object(self, **kwargs: object) -> dict[str, object]:
        key = str(kwargs["Bucket"]), str(kwargs["Key"])
        if key not in self.objects:
            raise _client_error("NoSuchKey")
        return {"Body": _Body(self.objects[key])}

    def copy_object(self, **kwargs: object) -> None:
        self.copy_calls.append(kwargs)

    def delete_object(self, **kwargs: object) -> None:
        self.delete_calls.append(kwargs)


def test_s3_client_preserves_the_small_object_protocol() -> None:
    """The managed SDK adapter translates only the product-store protocol."""
    from infrahub_sync.managed.storage import Boto3S3Client

    sdk = _SDK()
    client = Boto3S3Client(sdk)

    client.put(bucket="records", key="plain", data=b"plain")
    client.put(bucket="records", key="create-only", data=b"new", if_absent=True)
    assert sdk.put_calls == [
        {"Bucket": "records", "Key": "plain", "Body": b"plain"},
        {"Bucket": "records", "Key": "create-only", "Body": b"new", "IfNoneMatch": "*"},
    ]
    assert client.get(bucket="records", key="plain") == b"plain"
    assert client.get(bucket="records", key="missing") is None

    client.copy(bucket="records", source="plain", destination="copied")
    client.delete(bucket="records", key="plain")
    assert sdk.copy_calls == [{"Bucket": "records", "Key": "copied", "CopySource": {"Bucket": "records", "Key": "plain"}}]
    assert sdk.delete_calls == [{"Bucket": "records", "Key": "plain"}]


def test_s3_client_classifies_only_exact_conditional_responses() -> None:
    """412 is duplicate, while conditional 409 retries exactly three total attempts."""
    from infrahub_sync.managed.storage import Boto3S3Client

    sdk = _SDK()
    client = Boto3S3Client(sdk)
    sdk.put_failure = lambda: (_ for _ in ()).throw(_client_error("PreconditionFailed"))
    with pytest.raises(DuplicateArtifactError):
        client.put(bucket="records", key="exists", data=b"data", if_absent=True)
    assert len(sdk.put_calls) == 1

    sdk.put_calls.clear()
    sdk.put_failure = lambda: (_ for _ in ()).throw(_client_error("ConditionalRequestConflict"))
    with pytest.raises(ClientError) as conflicts:
        client.put(bucket="records", key="race", data=b"data", if_absent=True)
    assert conflicts.value.response["Error"]["Code"] == "ConditionalRequestConflict"
    assert len(sdk.put_calls) == 3

    sdk.put_calls.clear()
    sdk.put_failure = lambda: (_ for _ in ()).throw(_client_error("AccessDenied"))
    with pytest.raises(ClientError):
        client.put(bucket="records", key="denied", data=b"data", if_absent=True)
    assert len(sdk.put_calls) == 1
