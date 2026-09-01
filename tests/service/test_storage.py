"""Service-only durable storage adapter contracts."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, cast

import pytest
from typing_extensions import override

pytest.importorskip("botocore")

from botocore.exceptions import ClientError

from infrahub_sync.product_store import DuplicateArtifactError

if TYPE_CHECKING:
    from typing import NoReturn

    from infrahub_sync.product_store import ProductProjection


def _client_error(code: str, status: int | None = None) -> ClientError:
    if status is None:
        status = {"PreconditionFailed": 412, "ConditionalRequestConflict": 409, "NoSuchKey": 404}.get(code, 400)
    return ClientError(
        {
            "Error": {"Code": code, "Message": "sdk-secret-canary"},
            "ResponseMetadata": {"HTTPStatusCode": status},
        },
        "PutObject",
    )


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
        self.objects[str(kwargs["Bucket"]), str(kwargs["Key"])] = bytes(cast("bytes", kwargs["Body"]))

    def get_object(self, **kwargs: object) -> dict[str, object]:
        key = str(kwargs["Bucket"]), str(kwargs["Key"])
        if key not in self.objects:
            code = "NoSuchKey"
            raise _client_error(code)
        return {"Body": _Body(self.objects[key])}

    def copy_object(self, **kwargs: object) -> None:
        self.copy_calls.append(kwargs)

    def delete_object(self, **kwargs: object) -> None:
        self.delete_calls.append(kwargs)


def test_s3_client_preserves_the_small_object_protocol() -> None:
    """The service SDK adapter translates only the product-store protocol."""
    from infrahub_sync.service.storage import Boto3S3Client

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
    assert sdk.copy_calls == [
        {"Bucket": "records", "Key": "copied", "CopySource": {"Bucket": "records", "Key": "plain"}}
    ]
    assert sdk.delete_calls == [{"Bucket": "records", "Key": "plain"}]


def test_s3_client_classifies_only_exact_conditional_responses() -> None:
    """412 is duplicate, while conditional 409 retries exactly three total attempts."""
    from infrahub_sync.service.storage import Boto3S3Client

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


@pytest.mark.parametrize("code", ["PreconditionFailed", "ConditionalRequestConflict", "AccessDenied"])
@pytest.mark.parametrize("status", [409, 412, 403])
def test_s3_conditional_put_classification_is_the_exact_code_status_product(code: str, status: int) -> None:
    """Neither a matching code nor a matching status is sufficient on its own."""
    from infrahub_sync.service.storage import Boto3S3Client

    sdk = _SDK()
    failure = _client_error(code, status)
    sdk.put_failure = lambda: (_ for _ in ()).throw(failure)

    if (code, status) == ("PreconditionFailed", 412):
        with pytest.raises(DuplicateArtifactError):
            Boto3S3Client(sdk).put(bucket="records", key="object", data=b"data", if_absent=True)
        expected_attempts = 1
    else:
        with pytest.raises(ClientError) as raised:
            Boto3S3Client(sdk).put(bucket="records", key="object", data=b"data", if_absent=True)
        assert raised.value is failure
        expected_attempts = 3 if (code, status) == ("ConditionalRequestConflict", 409) else 1

    assert len(sdk.put_calls) == expected_attempts


@pytest.mark.parametrize("code", ["NoSuchKey", "NoSuchBucket", "AccessDenied"])
@pytest.mark.parametrize("status", [404, 403, 200])
def test_s3_missing_get_classification_is_the_exact_code_status_product(code: str, status: int) -> None:
    """Only the service's exact missing-key code and HTTP status become absence."""
    from infrahub_sync.service.storage import Boto3S3Client

    failure = _client_error(code, status)

    class FailingGetSDK(_SDK):
        @override
        def get_object(self, **_kwargs: object) -> dict[str, object]:
            raise failure

    sdk = FailingGetSDK()

    if (code, status) == ("NoSuchKey", 404):
        assert Boto3S3Client(sdk).get(bucket="records", key="object") is None
    else:
        with pytest.raises(ClientError) as raised:
            Boto3S3Client(sdk).get(bucket="records", key="object")
        assert raised.value is failure


def test_s3_get_accepts_only_exact_bytes_and_only_exact_missing_object() -> None:
    """Lookup must not turn malformed bodies or non-missing SDK failures into absence."""
    from infrahub_sync.service.storage import Boto3S3Client, S3ProtocolError

    class Body:
        def __init__(self, value: object) -> None:
            self._value = value

        def read(self) -> object:
            return self._value

    class SDK:
        def __init__(self, result: object) -> None:
            self._result = result

        def get_object(self, **_kwargs: object) -> object:
            if isinstance(self._result, BaseException):
                raise self._result
            return self._result

    assert Boto3S3Client(SDK({"Body": Body(b"exact-bytes")})).get(bucket="records", key="object") == b"exact-bytes"
    assert Boto3S3Client(SDK(_client_error("NoSuchKey"))).get(bucket="records", key="missing") is None

    for failure in (_client_error("NoSuchBucket"), _client_error("AccessDenied"), _client_error("InternalError")):
        with pytest.raises(ClientError):
            Boto3S3Client(SDK(failure)).get(bucket="records", key="object")
    for response in ({"Body": Body(bytearray(b"not-bytes"))}, {"Body": object()}, {}):
        with pytest.raises(S3ProtocolError) as error:
            Boto3S3Client(SDK(response)).get(bucket="records", key="object")
        assert str(error.value) == "S3 get response body must return bytes"
        assert error.value.__cause__ is None


def test_service_storage_factory_validates_settings_and_hides_startup_details() -> None:
    """The factory has one value-free environment contract and startup failure."""
    from infrahub_sync.service import storage

    required = {
        "INFRAHUB_SYNC_DATABASE_URL": "postgresql://secret-canary@db/sync",
        "INFRAHUB_SYNC_S3_BUCKET": "sync-artifacts",
    }
    captured: dict[str, object] = {}

    def projection(**kwargs: object) -> ProductProjection:
        captured.update(kwargs)
        return cast("ProductProjection", object())

    def database_connect() -> object:
        return object()

    result = storage.service_product_projection(
        environ={**required, "INFRAHUB_SYNC_S3_PREFIX": "/stable/", "INFRAHUB_SYNC_S3_ENDPOINT_URL": "https://s3.test"},
        database_connect=database_connect,
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
            storage.service_product_projection(environ=values, s3_client_builder=lambda *_args, **_kwargs: object())
        assert name in str(error.value)
        assert "secret-canary" not in str(error.value)

    def unavailable() -> NoReturn:
        raise storage.ProductStoreProviderError(sqlstate="08006")

    with pytest.raises(storage.ServiceStorageStartupError) as error:
        storage.service_product_projection(
            environ=required,
            database_connect=unavailable,
            s3_client_builder=lambda *_args, **_kwargs: object(),
        )
    assert str(error.value) == "service durable storage startup failed"
    assert error.value.__cause__ is None


def test_service_storage_settings_refuse_absence_and_normalize_the_prefix_deterministically() -> None:
    """Every setting refuses absence or emptiness, and no refusal reflects its value."""
    from infrahub_sync.service import storage

    required = {
        storage.DATABASE_URL_ENV: "postgresql://database-secret-canary@db/sync",
        storage.S3_BUCKET_ENV: "bucket-secret-canary",
    }

    def database_connect() -> object:
        return object()

    def s3_client_builder(*_args: object, **_kwargs: object) -> object:
        return object()

    def projection_builder(**_kwargs: object) -> ProductProjection:
        return cast("ProductProjection", object())

    def construct(values: dict[str, str]) -> object:
        return storage.service_product_projection(
            environ=values,
            database_connect=database_connect,
            s3_client_builder=s3_client_builder,
            projection_builder=projection_builder,
        )

    for name in (storage.DATABASE_URL_ENV, storage.S3_BUCKET_ENV):
        for values in ({key: value for key, value in required.items() if key != name}, {**required, name: ""}):
            with pytest.raises(ValueError) as error:
                construct(values)
            assert str(error.value) == f"{name} must be set"
            assert "secret-canary" not in str(error.value)

    for name in (storage.S3_ENDPOINT_ENV, storage.S3_REGION_ENV, storage.S3_PREFIX_ENV):
        with pytest.raises(ValueError) as error:
            construct({**required, name: ""})
        assert str(error.value) == f"{name} must be a non-empty string when set"

    prefixes: list[object] = []

    def collect_prefix(**kwargs: object) -> ProductProjection:
        prefixes.append(kwargs["prefix"])
        return cast("ProductProjection", object())

    storage.service_product_projection(
        environ={**required, storage.S3_PREFIX_ENV: "/one/two/"},
        database_connect=database_connect,
        s3_client_builder=s3_client_builder,
        projection_builder=collect_prefix,
    )
    assert prefixes == ["one/two"]


@pytest.mark.parametrize(
    "database_url",
    [
        "not-a-postgresql-connection-string",
        "sqlite:///database-secret-canary",
        "host='unterminated-database-secret-canary",
        "postgresql://db/sync?unknown-option=database-secret-canary",
    ],
)
def test_service_storage_rejects_non_postgresql_conninfo_before_any_construction(database_url: str) -> None:
    """Database URL acceptance is exactly Psycopg's non-empty conninfo domain."""
    from infrahub_sync.service import storage

    calls: list[str] = []

    def constructed(name: str) -> NoReturn:
        calls.append(name)
        raise AssertionError(name)

    with pytest.raises(ValueError) as error:
        storage.service_product_projection(
            environ={
                storage.DATABASE_URL_ENV: database_url,
                storage.S3_BUCKET_ENV: "bucket",
            },
            database_connect=lambda: constructed("database"),
            s3_client_builder=lambda *_args, **_kwargs: constructed("sdk"),
            projection_builder=lambda **_kwargs: constructed("projection"),
        )
    assert str(error.value) == f"{storage.DATABASE_URL_ENV} must be a PostgreSQL connection string"
    assert error.value.__cause__ is None
    assert database_url not in str(error.value)
    assert "secret-canary" not in str(error.value)
    assert calls == []


def test_service_storage_contains_sdk_client_construction_failures() -> None:
    """SDK construction details become the fixed unchained startup refusal."""
    from infrahub_sync.service import storage

    calls: list[str] = []

    def sdk_failure(*_args: object, **_kwargs: object) -> NoReturn:
        calls.append("sdk")
        message = "driver-secret-canary at https://endpoint-secret-canary"
        raise RuntimeError(message)

    def projection_builder(**_kwargs: object) -> ProductProjection:
        calls.append("projection")
        return cast("ProductProjection", object())

    with pytest.raises(storage.ServiceStorageStartupError) as error:
        storage.service_product_projection(
            environ={
                storage.DATABASE_URL_ENV: "postgresql://database-secret-canary@db/sync",
                storage.S3_BUCKET_ENV: "bucket",
                storage.S3_ENDPOINT_ENV: "https://endpoint-secret-canary",
            },
            database_connect=lambda: calls.append("database"),
            s3_client_builder=sdk_failure,
            projection_builder=projection_builder,
        )

    assert str(error.value) == "service durable storage startup failed"
    assert error.value.__cause__ is None
    assert "secret-canary" not in str(error.value)
    assert calls == ["sdk"]


@pytest.mark.parametrize(
    "endpoint",
    [
        "not-a-url",
        "ftp://s3.example.test",
        "http://s3.example.test:not-a-port",
        "http://[::1",
        "http://user@s3.example.test",
        "https://user:secret-canary@s3.example.test",
    ],
)
def test_service_storage_endpoint_rejects_non_urls_and_userinfo_before_construction(endpoint: str) -> None:
    """A rejected endpoint never reaches a builder and never reflects its own value."""
    from infrahub_sync.service import storage

    calls: list[str] = []

    def database_connect() -> object:
        calls.append("database")
        return object()

    def s3_client_builder(*_args: object, **_kwargs: object) -> object:
        calls.append("sdk")
        return object()

    def projection_builder(**_kwargs: object) -> ProductProjection:
        calls.append("projection")
        return cast("ProductProjection", object())

    with pytest.raises(ValueError) as error:
        storage.service_product_projection(
            environ={
                storage.DATABASE_URL_ENV: "postgresql://database-secret-canary@db/sync",
                storage.S3_BUCKET_ENV: "bucket-secret-canary",
                storage.S3_ENDPOINT_ENV: endpoint,
            },
            database_connect=database_connect,
            s3_client_builder=s3_client_builder,
            projection_builder=projection_builder,
        )
    assert str(error.value) == f"{storage.S3_ENDPOINT_ENV} must be an absolute http or https URL without userinfo"
    assert endpoint not in str(error.value)
    assert "secret-canary" not in str(error.value)
    assert calls == []


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://s3.example.test",
        "http://s3.example.test:9000",
        "http://127.0.0.1",
        "http://127.0.0.1:9000",
        "http://localhost",
        "https://[::1]",
        "https://[::1]:9443",
        "HTTP://s3.example.test:9000/path%20with%20encoding?query=@value#fragment",
    ],
)
def test_service_storage_endpoint_accepts_valid_authorities(endpoint: str) -> None:
    """An accepted endpoint reaches Boto3 as the operator's own unmodified string."""
    from infrahub_sync.service import storage

    received: list[object] = []

    def database_connect() -> object:
        return object()

    def s3_client_builder(_service: object, **kwargs: object) -> object:
        received.append(kwargs["endpoint_url"])
        return object()

    def projection_builder(**_kwargs: object) -> ProductProjection:
        return cast("ProductProjection", object())

    storage.service_product_projection(
        environ={
            storage.DATABASE_URL_ENV: "postgresql://db/sync",
            storage.S3_BUCKET_ENV: "bucket",
            storage.S3_ENDPOINT_ENV: endpoint,
        },
        database_connect=database_connect,
        s3_client_builder=s3_client_builder,
        projection_builder=projection_builder,
    )
    assert received == [endpoint]


def test_psycopg_adapter_marks_only_driver_errors() -> None:
    """Psycopg errors retain SQLSTATE; unrelated provider defects escape unmarked."""
    from infrahub_sync.service import storage

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


def test_psycopg_adapter_marks_cursor_transaction_and_cleanup_failures() -> None:
    """Every DB-API operation exposed to the product store preserves the typed marker."""
    from infrahub_sync.service import storage

    cursor_message = "cursor-secret-canary"
    fetch_message = "fetch-secret-canary"
    fetchall_message = "fetchall-secret-canary"
    close_message = "close-secret-canary"
    commit_message = "commit-secret-canary"
    rollback_message = "rollback-secret-canary"

    class Cursor:
        @staticmethod
        def execute(*_args: object) -> NoReturn:
            raise storage.psycopg.OperationalError(cursor_message)

        @staticmethod
        def fetchone() -> NoReturn:
            raise storage.psycopg.OperationalError(fetch_message)

        @staticmethod
        def fetchall() -> NoReturn:
            raise storage.psycopg.OperationalError(fetchall_message)

        @staticmethod
        def close() -> NoReturn:
            raise storage.psycopg.OperationalError(close_message)

    class Connection:
        @staticmethod
        def cursor() -> Cursor:
            return Cursor()

        @staticmethod
        def commit() -> NoReturn:
            raise storage.psycopg.OperationalError(commit_message)

        @staticmethod
        def rollback() -> NoReturn:
            raise storage.psycopg.OperationalError(rollback_message)

        @staticmethod
        def close() -> NoReturn:
            raise storage.psycopg.OperationalError(close_message)

    connection = storage.PsycopgConnectionFactory(lambda _dsn: Connection())("postgresql://db/sync")
    cursor = connection.cursor()
    for operation in (
        lambda: cursor.execute("SELECT 1"),
        cursor.fetchone,
        cursor.fetchall,
        cursor.close,
        connection.commit,
        connection.rollback,
        connection.close,
    ):
        with pytest.raises(storage.ProductStoreProviderError):
            operation()
