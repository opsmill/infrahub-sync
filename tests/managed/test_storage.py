"""Managed-only durable storage adapter contracts."""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace

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


def test_managed_storage_factory_validates_settings_and_hides_startup_details() -> None:
    """The factory has one value-free environment contract and startup failure."""
    from infrahub_sync.managed import storage

    required = {
        "INFRAHUB_SYNC_DATABASE_URL": "postgresql://secret-canary@db/sync",
        "INFRAHUB_SYNC_S3_BUCKET": "sync-artifacts",
    }
    captured: dict[str, object] = {}

    def projection(**kwargs: object) -> object:
        captured.update(kwargs)
        return object()

    result = storage.managed_product_projection(
        environ={**required, "INFRAHUB_SYNC_S3_PREFIX": "/stable/", "INFRAHUB_SYNC_S3_ENDPOINT_URL": "https://s3.test"},
        database_connect=lambda: object(),
        s3_client_builder=lambda service, **kwargs: captured.setdefault("sdk", {"service": service, **kwargs}),
        projection_builder=projection,
    )
    assert result is not None
    assert captured["bucket"] == "sync-artifacts"
    assert captured["prefix"] == "stable"
    assert captured["sdk"] == {"service": "s3", "endpoint_url": "https://s3.test", "region_name": None}

    for name, value in (
        ("INFRAHUB_SYNC_DATABASE_URL", ""),
        ("INFRAHUB_SYNC_S3_BUCKET", ""),
        ("INFRAHUB_SYNC_S3_ENDPOINT_URL", "not-a-url"),
    ):
        values = {**required, name: value}
        with pytest.raises(ValueError) as error:
            storage.managed_product_projection(environ=values, s3_client_builder=lambda *_args, **_kwargs: object())
        assert name in str(error.value)
        assert "secret-canary" not in str(error.value)

    def unavailable() -> NoReturn:
        raise storage.ProductStoreProviderError(sqlstate="08006")

    with pytest.raises(storage.ManagedStorageStartupError) as error:
        storage.managed_product_projection(
            environ=required,
            database_connect=unavailable,
            s3_client_builder=lambda *_args, **_kwargs: object(),
        )
    assert str(error.value) == "managed durable storage startup failed"
    assert error.value.__cause__ is None


def test_psycopg_adapter_marks_only_driver_errors() -> None:
    """Psycopg errors retain SQLSTATE; unrelated provider defects escape unmarked."""
    from infrahub_sync.managed import storage

    driver_error = storage.psycopg.OperationalError("driver-secret-canary")
    factory = storage.PsycopgConnectionFactory(lambda _dsn: (_ for _ in ()).throw(driver_error))
    with pytest.raises(storage.ProductStoreProviderError) as error:
        factory("postgresql://secret-canary@db/sync")
    assert error.value.sqlstate is None
    assert error.value.__cause__ is None

    failure = RuntimeError("product defect")
    factory = storage.PsycopgConnectionFactory(lambda _dsn: (_ for _ in ()).throw(failure))
    with pytest.raises(RuntimeError, match="product defect"):
        factory("postgresql://secret-canary@db/sync")
