"""The deployment bootstrap converges durable objects and repeats safely.

Every case here drives the real convergence functions against fakes that answer
the way the providers do. What is being checked is the decision each one makes —
create, leave alone, or refuse — because that decision is what makes a second
`start` a no-op instead of a second set of objects.

Registering a configuration is not one of those objects: a deployment starts
empty and an operator registers through the API.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

pytest.importorskip("prefect")
pytest.importorskip("opsmill_prefect_extras")

import httpx
from botocore.exceptions import ClientError
from prefect.exceptions import ObjectNotFound

from infrahub_sync.product_store import DuplicateArtifactError, configs, local_product_projection
from infrahub_sync.service import bootstrap
from infrahub_sync.service.bootstrap import (
    PROCESS_POOL_TYPE,
    BootstrapError,
    check_create_only,
    check_store_settings,
    converge_bucket,
    converge_work_pool,
)
from tests.configuration.validation_packages import package_data

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine
    from typing import NoReturn

    from prefect.client.schemas.actions import WorkPoolCreate

    from infrahub_sync.product_store import ProductProjection

_REQUEST = httpx.Request("GET", "http://prefect-server:4200/api/work_pools/infrahub-sync")
_RESPONSE = httpx.Response(404, request=_REQUEST)

BUCKET = "infrahub-sync"
POOL = "infrahub-sync"

# A credential and an endpoint the fakes render into their failure text, so a
# family that leaked provider detail would carry one of them.
ENDPOINT_CANARY = "http://object-store.internal:9000"
CREDENTIAL_CANARY = "bootstrap-object-store-canary"
PROVIDER_CANARY = "provider-endpoint-and-secret-canary"


class _Bucket:
    """A minimal S3 client that answers head_bucket and create_bucket the way the SDK does.

    By default the bucket is absent: `head_bucket` answers 404 and `create_bucket` succeeds.
    """

    def __init__(
        self,
        *,
        response: dict[str, Any] | None = None,
        head: dict[str, Any] | None = None,
        present: bool = False,
    ) -> None:
        self.created: list[tuple[str, dict[str, Any]]] = []
        self.calls: list[str] = []
        self._response = response
        self._head = head if head is not None else (None if present else _client_error("404", 404))

    def head_bucket(self, *, Bucket: str) -> None:  # noqa: N803 -- the SDK's own keyword
        self.calls.append(f"head {Bucket}")
        if self._head is not None:
            raise ClientError(self._head, "HeadBucket")

    def create_bucket(self, *, Bucket: str, **settings: object) -> None:  # noqa: N803 -- the SDK's own keyword
        self.calls.append(f"create {Bucket}")
        if self._response is not None:
            raise ClientError(self._response, "CreateBucket")
        self.created.append((Bucket, settings))


def _client_error(code: str, status: int = 403) -> dict[str, Any]:
    return {
        "Error": {
            "Code": code,
            "Message": f"{code} at {ENDPOINT_CANARY} using {CREDENTIAL_CANARY}",
        },
        "ResponseMetadata": {"HTTPStatusCode": status},
    }


class _Pool:
    """Enough of the Prefect client for the read-then-create the bootstrap does."""

    def __init__(self, *, existing_type: str | None = None) -> None:
        self._existing_type = existing_type
        self.created: list[tuple[str, str]] = []

    async def read_work_pool(self, name: str) -> object:
        if self._existing_type is None:
            raise ObjectNotFound(httpx.HTTPStatusError("404", request=_REQUEST, response=_RESPONSE))
        return type("WorkPool", (), {"name": name, "type": self._existing_type})()

    async def create_work_pool(self, work_pool: WorkPoolCreate) -> None:
        self.created.append((work_pool.name, work_pool.type))


def _projection(tmp_path: Path) -> ProductProjection:
    return local_product_projection(tmp_path / "product")


# ---------------------------------------------------------------------------
# The artifact bucket
# ---------------------------------------------------------------------------


def test_an_absent_bucket_is_created() -> None:
    client = _Bucket()

    assert converge_bucket(client, BUCKET) is True
    assert client.calls == [f"head {BUCKET}", f"create {BUCKET}"]
    assert client.created == [(BUCKET, {})]


def test_an_existing_bucket_is_found_and_never_created() -> None:
    """A credential that may not create buckets still completes (FR-003)."""
    client = _Bucket(present=True, response=_client_error("AccessDenied"))

    assert converge_bucket(client, BUCKET) is False
    assert client.calls == [f"head {BUCKET}"]


@pytest.mark.parametrize(
    ("region", "expected"),
    [(None, {}), ("us-east-1", {}), ("eu-west-1", {"CreateBucketConfiguration": {"LocationConstraint": "eu-west-1"}})],
)
def test_an_absent_bucket_is_created_in_its_region(region: str | None, expected: dict[str, Any]) -> None:
    """The S3 API takes a location constraint for every region but its default one."""
    client = _Bucket()

    assert converge_bucket(client, BUCKET, region) is True
    assert client.created == [(BUCKET, expected)]


def test_a_refused_bucket_check_names_the_access_refusal() -> None:
    client = _Bucket(head=_client_error("Forbidden", 403))

    with pytest.raises(BootstrapError) as refusal:
        converge_bucket(client, BUCKET)

    assert refusal.value.family == "object-store-access-refused"
    assert client.calls == [f"head {BUCKET}"]
    rendered = repr(refusal.value) + str(refusal.value)
    assert ENDPOINT_CANARY not in rendered
    assert CREDENTIAL_CANARY not in rendered


@pytest.mark.parametrize("code", ["BucketAlreadyOwnedByYou", "BucketAlreadyExists"])
def test_a_bucket_that_is_already_there_is_left_alone(code: str) -> None:
    """A bucket another writer created between the check and the create is not a failure."""
    client = _Bucket(response=_client_error(code))

    assert converge_bucket(client, BUCKET) is False


def test_an_object_store_failure_carries_no_endpoint_or_credential() -> None:
    """Its inputs are an endpoint and a key pair, and the SDK renders both into its text."""
    client = _Bucket(response=_client_error("InternalError", 500))

    with pytest.raises(BootstrapError) as refusal:
        converge_bucket(client, BUCKET)

    assert refusal.value.family == "object-store-unavailable"
    rendered = repr(refusal.value) + str(refusal.value)
    assert ENDPOINT_CANARY not in rendered
    assert CREDENTIAL_CANARY not in rendered


# ---------------------------------------------------------------------------
# The store settings and the create-only check
# ---------------------------------------------------------------------------


def _resolves(*names: str) -> Callable[[str], bool]:
    return lambda host: host in names


@pytest.mark.parametrize("endpoint", ["https://s3.example.test", "http://10.0.0.5:9000", None])
def test_an_external_endpoint_with_the_bundled_store_running_is_refused(endpoint: str | None) -> None:
    """The bundled store has then received the external store's credential (FR-002)."""
    with pytest.raises(BootstrapError) as refusal:
        check_store_settings(endpoint, resolves=_resolves("object-store"))

    assert refusal.value.family == "bundled-object-store-not-left-out"


def test_the_bundled_store_left_out_with_the_default_endpoint_is_refused() -> None:
    with pytest.raises(BootstrapError) as refusal:
        check_store_settings("http://object-store:9000", resolves=_resolves())

    assert refusal.value.family == "object-store-endpoint-not-set"


@pytest.mark.parametrize(
    ("endpoint", "running"),
    [("http://object-store:9000", ("object-store",)), ("https://s3.example.test", ())],
    ids=["bundled", "external"],
)
def test_matching_store_settings_pass(endpoint: str, running: tuple[str, ...]) -> None:
    check_store_settings(endpoint, resolves=_resolves(*running))


class _Objects:
    """The create-only protocol of `Boto3S3Client`: put, with or without `if_absent`, and delete."""

    def __init__(
        self,
        *,
        honors_create_only: bool = True,
        put_error: Exception | None = None,
        delete_error: Exception | None = None,
    ) -> None:
        self.honors_create_only = honors_create_only
        self.put_error = put_error
        self.delete_error = delete_error
        self.keys: set[str] = set()
        self.deleted: list[str] = []

    def put(self, *, bucket: str, key: str, data: bytes, if_absent: bool = False) -> None:
        del bucket, data
        if self.put_error is not None:
            raise self.put_error
        if if_absent and self.honors_create_only and key in self.keys:
            raise DuplicateArtifactError(key)
        self.keys.add(key)

    def delete(self, *, bucket: str, key: str) -> None:
        del bucket
        self.deleted.append(key)
        if self.delete_error is not None:
            raise self.delete_error
        self.keys.discard(key)


def test_a_store_that_refuses_a_second_create_only_write_passes_and_leaves_nothing() -> None:
    objects = _Objects()

    check_create_only(objects, BUCKET, "infrahub-sync")

    (key,) = objects.deleted
    assert key.startswith("infrahub-sync/.infrahub-sync/bootstrap-check/")
    assert objects.keys == set()


def test_a_store_that_accepts_a_second_create_only_write_is_refused_and_cleaned_up() -> None:
    """Sync's artifact store relies on that refusal (FR-004)."""
    objects = _Objects(honors_create_only=False)

    with pytest.raises(BootstrapError) as refusal:
        check_create_only(objects, BUCKET, "infrahub-sync")

    assert refusal.value.family == "object-store-unsupported"
    assert objects.keys == set()
    assert len(objects.deleted) == 1


def test_a_failed_check_write_stays_behind_the_object_store_family() -> None:
    objects = _Objects(put_error=ClientError(_client_error("InternalError", 500), "PutObject"))

    with pytest.raises(BootstrapError) as refusal:
        check_create_only(objects, BUCKET, "infrahub-sync")

    assert refusal.value.family == "object-store-unavailable"
    assert len(objects.deleted) == 1


@pytest.mark.parametrize(
    "endpoint",
    ["http://user@s3.example.test", f"https://user:{CREDENTIAL_CANARY}@s3.example.test", "not-a-url"],
)
def test_an_invalid_endpoint_stops_bootstrap_before_any_s3_request(
    endpoint: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The refusal names the setting and the family, never the value (FR-005)."""
    monkeypatch.setenv("INFRAHUB_SYNC_S3_ENDPOINT_URL", endpoint)

    def no_client(*_arguments: object, **_settings: object) -> NoReturn:
        message = "an S3 client was built"
        raise AssertionError(message)

    monkeypatch.setattr(bootstrap.boto3, "client", no_client)

    with caplog.at_level("ERROR"):
        assert bootstrap.main() == 1

    assert "refused: object-store-endpoint-invalid" in caplog.text
    assert "INFRAHUB_SYNC_S3_ENDPOINT_URL must be an absolute http or https URL without userinfo" in caplog.text
    assert CREDENTIAL_CANARY not in caplog.text
    assert endpoint not in caplog.text


def test_the_store_checks_run_in_order_before_the_work_pool(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Settings, then the bucket, then the create-only check, and only then Prefect."""
    events: list[str] = []
    monkeypatch.setattr(bootstrap.boto3, "client", lambda *_arguments, **_settings: _Bucket())
    monkeypatch.setattr(bootstrap, "check_store_settings", lambda *_arguments, **_settings: events.append("settings"))
    monkeypatch.setattr(bootstrap, "converge_bucket", lambda *_arguments: events.append("bucket") or False)
    monkeypatch.setattr(bootstrap, "check_create_only", lambda *_arguments: events.append("create-only"))
    monkeypatch.setattr(bootstrap, "_required", lambda _name: "present")
    monkeypatch.setattr(bootstrap, "apply_deployment", lambda: events.append("deployment") or 0)
    monkeypatch.setattr(bootstrap, "service_product_projection", lambda: _projection(tmp_path))

    def finish(coroutine: Coroutine[object, object, bool]) -> bool:
        coroutine.close()
        events.append("pool")
        return False

    monkeypatch.setattr(bootstrap.asyncio, "run", finish)

    assert bootstrap.main() == 0
    assert events == ["settings", "bucket", "create-only", "pool", "deployment"]


# ---------------------------------------------------------------------------
# The work pool
# ---------------------------------------------------------------------------


async def test_an_absent_work_pool_is_created_as_a_process_pool() -> None:
    """The worker joins a process pool; any other type it could never poll."""
    client = _Pool()

    assert await converge_work_pool(client, POOL) is True
    assert client.created == [(POOL, PROCESS_POOL_TYPE)]


async def test_an_existing_process_pool_is_left_alone() -> None:
    client = _Pool(existing_type=PROCESS_POOL_TYPE)

    assert await converge_work_pool(client, POOL) is False
    assert client.created == []


async def test_a_pool_of_another_type_is_refused_before_anything_is_created() -> None:
    """Creating regardless and reading a conflict as success would hide this until the worker failed."""
    client = _Pool(existing_type="docker")

    with pytest.raises(BootstrapError) as refusal:
        await converge_work_pool(client, POOL)

    assert refusal.value.family == "work-pool-conflict"
    assert client.created == []


# ---------------------------------------------------------------------------
# The registry a bootstrap must leave alone
# ---------------------------------------------------------------------------


def _converged(monkeypatch: pytest.MonkeyPatch, projection: ProductProjection) -> None:
    """Replace every provider a successful convergence reaches, and nothing else."""
    monkeypatch.setattr(bootstrap.boto3, "client", lambda *_arguments, **_settings: _Bucket())
    monkeypatch.setattr(bootstrap, "converge_bucket", lambda *_arguments: True)
    monkeypatch.setattr(bootstrap, "check_store_settings", lambda *_arguments, **_settings: None)
    monkeypatch.setattr(bootstrap, "check_create_only", lambda *_arguments: None)
    monkeypatch.setattr(bootstrap, "_required", lambda _name: "present")
    monkeypatch.setattr(bootstrap, "apply_deployment", lambda: 0)
    monkeypatch.setattr(bootstrap, "service_product_projection", lambda: projection)

    def finish(coroutine: Coroutine[object, object, bool]) -> bool:
        coroutine.close()
        return False

    monkeypatch.setattr(bootstrap.asyncio, "run", finish)


def test_a_cold_bootstrap_leaves_the_registry_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No configuration and no registration event: a start registers nothing."""
    projection = _projection(tmp_path)
    _converged(monkeypatch, projection)

    assert bootstrap.main() == 0
    assert len(projection.list_configurations()) == 0
    assert len(projection.audit_events()) == 0


def test_bootstrap_reports_its_progress_at_info_whatever_the_root_level(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Prefect leaves the root logger at a level that drops INFO; the job still reports each step."""
    _converged(monkeypatch, _projection(tmp_path))
    bootstrap.logger.setLevel("WARNING")

    with caplog.at_level("INFO"):
        assert bootstrap.main() == 0

    assert "bucket created" in caplog.text
    assert "object store refuses a second create-only write" in caplog.text


def test_a_repeated_bootstrap_changes_nothing_an_operator_registered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Registered explicitly first: an empty registry is also what a wipe leaves."""
    projection = _projection(tmp_path)
    registered = configs.register(package=package_data(), projection=projection)
    _converged(monkeypatch, projection)

    assert bootstrap.main() == 0
    assert bootstrap.main() == 0

    versions = projection.list_configuration_versions(registered.version.config_id)
    assert [version.registry_version for version in versions] == [registered.version.registry_version]
    assert [version.package_checksum for version in versions] == [registered.version.package_checksum]
    assert len(projection.list_configurations()) == 1
    assert len(projection.audit_events()) == 0


def test_no_registration_path_is_retained_for_a_configured_deployment() -> None:
    """Registration is deleted, not made optional: no setting can switch it back on."""
    source = Path(bootstrap.__file__).read_text(encoding="utf-8")

    retained = [
        name for name in ("configs.register", "load_package_content", "parse_configuration_package") if name in source
    ]
    assert retained == [], f"the bootstrap still carries the registration surface: {retained}"


def _bootstrap_before_deployment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the successful steps before deployment without leaking a coroutine."""
    monkeypatch.setattr(bootstrap.boto3, "client", lambda *_arguments, **_settings: _Bucket())
    monkeypatch.setattr(bootstrap, "converge_bucket", lambda *_arguments: False)
    monkeypatch.setattr(bootstrap, "check_store_settings", lambda *_arguments, **_settings: None)
    monkeypatch.setattr(bootstrap, "check_create_only", lambda *_arguments: None)
    monkeypatch.setattr(bootstrap, "_required", lambda _name: "present")

    def finish(coroutine: Coroutine[object, object, bool]) -> bool:
        coroutine.close()
        return False

    monkeypatch.setattr(bootstrap.asyncio, "run", finish)


def test_a_deployment_provider_exception_stays_behind_its_fixed_family(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Provider exception text can contain endpoints and credentials."""
    _bootstrap_before_deployment(monkeypatch)

    def fail_deployment() -> int:
        raise RuntimeError(PROVIDER_CANARY)

    monkeypatch.setattr(bootstrap, "apply_deployment", fail_deployment)

    with caplog.at_level("ERROR"):
        result = bootstrap.main()

    assert result == 1
    assert "deployment-failed" in caplog.text
    assert PROVIDER_CANARY not in caplog.text


def test_a_failure_running_the_pool_coroutine_stays_behind_the_work_pool_family(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The loop `asyncio.run` owns is part of the provider boundary too.

    The asynchronous context's own entry and exit are driven for real at the end of this
    module; this case covers what the stub stands in for, the run call itself.
    """
    _bootstrap_before_deployment(monkeypatch)

    def fail(coroutine: Coroutine[object, object, bool]) -> bool:
        coroutine.close()
        raise RuntimeError(PROVIDER_CANARY)

    monkeypatch.setattr(bootstrap.asyncio, "run", fail)

    with caplog.at_level("ERROR"):
        result = bootstrap.main()

    assert result == 1
    assert "work-pool-unavailable" in caplog.text
    assert PROVIDER_CANARY not in caplog.text


class _FailsOnEntry:
    """A real asynchronous context manager whose entry raises a provider exception.

    Entering and leaving the Prefect client context are provider calls like any other: the
    client is built from an endpoint and a credential, and both are rendered into the
    exception text a failure carries.
    """

    async def __aenter__(self) -> _Pool:
        raise RuntimeError(PROVIDER_CANARY)

    async def __aexit__(self, *_details: object) -> None:
        """Unreachable: entry never returns."""


class _FailsOnExit:
    """A real asynchronous context manager whose exit raises after a successful pool read."""

    async def __aenter__(self) -> _Pool:
        return _Pool(existing_type=PROCESS_POOL_TYPE)

    async def __aexit__(self, *_details: object) -> None:
        raise RuntimeError(PROVIDER_CANARY)


def _bootstrap_before_the_work_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the steps before the pool, leaving `asyncio.run` and the context real."""
    monkeypatch.setattr(bootstrap.boto3, "client", lambda *_arguments, **_settings: _Bucket())
    monkeypatch.setattr(bootstrap, "converge_bucket", lambda *_arguments: False)
    monkeypatch.setattr(bootstrap, "check_store_settings", lambda *_arguments, **_settings: None)
    monkeypatch.setattr(bootstrap, "check_create_only", lambda *_arguments: None)
    monkeypatch.setattr(bootstrap, "_required", lambda _name: "present")


@pytest.mark.parametrize("context", [_FailsOnEntry, _FailsOnExit], ids=["entering the context", "leaving it"])
def test_a_real_client_context_failure_stays_behind_the_work_pool_family(
    context: type, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The asynchronous context is driven for real, not stubbed at `asyncio.run`."""
    _bootstrap_before_the_work_pool(monkeypatch)
    monkeypatch.setattr(bootstrap, "get_client", context)

    with caplog.at_level("ERROR"):
        result = bootstrap.main()

    assert result == 1
    assert "work-pool-unavailable" in caplog.text
    assert PROVIDER_CANARY not in caplog.text


@pytest.mark.parametrize(
    ("status", "code", "family"),
    [
        (403, "AccessDenied", "object-store-access-refused"),
        (501, "NotImplemented", "object-store-unsupported"),
        (500, "InternalError", "object-store-unavailable"),
    ],
)
def test_a_refused_bucket_creation_names_its_family(status: int, code: str, family: str) -> None:
    client = _Bucket(response=_client_error(code, status))

    with pytest.raises(BootstrapError) as refusal:
        converge_bucket(client, BUCKET)

    assert refusal.value.family == family
    rendered = repr(refusal.value) + str(refusal.value)
    assert ENDPOINT_CANARY not in rendered
    assert CREDENTIAL_CANARY not in rendered


@pytest.mark.parametrize(
    ("status", "code", "family"),
    [(403, "AccessDenied", "object-store-access-refused"), (501, "NotImplemented", "object-store-unsupported")],
)
def test_a_refused_check_write_names_its_family(status: int, code: str, family: str) -> None:
    objects = _Objects(put_error=ClientError(_client_error(code, status), "PutObject"))

    with pytest.raises(BootstrapError) as refusal:
        check_create_only(objects, BUCKET, "infrahub-sync")

    assert refusal.value.family == family
    assert len(objects.deleted) == 1


def test_a_check_key_that_cannot_be_deleted_is_logged_without_failing(caplog: pytest.LogCaptureFixture) -> None:
    objects = _Objects(delete_error=ClientError(_client_error("AccessDenied", 403), "DeleteObject"))

    with caplog.at_level("WARNING"):
        check_create_only(objects, BUCKET, "infrahub-sync")

    assert "the create-only check could not delete its key under .infrahub-sync/bootstrap-check" in caplog.text
    assert CREDENTIAL_CANARY not in caplog.text


@pytest.mark.parametrize(
    ("name", "value"),
    [("INFRAHUB_SYNC_S3_ADDRESSING_STYLE", "sideways"), ("INFRAHUB_SYNC_S3_REGION", "not a region!")],
)
def test_another_refused_s3_setting_is_not_reported_as_the_endpoint(
    name: str, value: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("INFRAHUB_SYNC_S3_ENDPOINT_URL", raising=False)
    monkeypatch.setenv(name, value)

    with caplog.at_level("ERROR"):
        assert bootstrap.main() == 1

    assert "refused: object-store-setting-invalid" in caplog.text
    assert value not in caplog.text
    if name == "INFRAHUB_SYNC_S3_ADDRESSING_STYLE":
        assert "INFRAHUB_SYNC_S3_ADDRESSING_STYLE must be path or virtual" in caplog.text
