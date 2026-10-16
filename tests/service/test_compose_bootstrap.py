"""The deployment bootstrap converges durable objects and repeats safely.

Every case here drives the real convergence functions against fakes that answer
the way the providers do. What is being checked is the decision each one makes —
create, leave alone, or refuse — because that decision is what makes a second
`start` a no-op instead of a second set of objects.

Registering a configuration is not one of those objects: a deployment starts
empty and an operator registers through the API.
"""

from __future__ import annotations

import re
import socket
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

pytest.importorskip("prefect")
pytest.importorskip("opsmill_prefect_extras")

import httpx
from botocore.exceptions import ClientError, EndpointConnectionError
from prefect.exceptions import ObjectNotFound

from infrahub_sync.product_store import configs, local_product_projection
from infrahub_sync.service import bootstrap
from infrahub_sync.service.bootstrap import (
    PROCESS_POOL_TYPE,
    BootstrapError,
    check_create_only,
    converge_bucket,
    converge_work_pool,
)
from infrahub_sync.service.storage import Boto3S3Client, build_s3_client
from tests.configuration.validation_packages import package_data

if TYPE_CHECKING:
    from collections.abc import Coroutine, Mapping

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


def _error(code: str, status: int) -> dict[str, Any]:
    return {
        "Error": {
            "Code": code,
            "Message": f"{code} at {ENDPOINT_CANARY} using {CREDENTIAL_CANARY}",
        },
        "ResponseMetadata": {"HTTPStatusCode": status},
    }


class _Store:
    """A minimal S3 client that answers the bootstrap's requests the way the SDK does.

    Every request is recorded, so a case can check which requests were made and in
    which order, and that a refused setting made none.
    """

    def __init__(  # noqa: PLR0913 -- one keyword per provider answer a case sets
        self,
        *,
        head_status: int = 200,
        create_code: str | None = None,
        create_status: int = 409,
        honours_condition: bool = True,
        put_failures: dict[int, tuple[str, int]] | None = None,
        delete_failure: tuple[str, int] | None = None,
    ) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self._head_status = head_status
        self._create_code = create_code
        self._create_status = create_status
        self._honours_condition = honours_condition
        self._put_failures = put_failures or {}
        self._delete_failure = delete_failure
        self._objects: set[str] = set()
        self._puts = 0

    def head_bucket(self, **request: object) -> dict[str, Any]:
        self.calls.append(("HeadBucket", request))
        if self._head_status != 200:
            raise ClientError(_error(str(self._head_status), self._head_status), "HeadBucket")
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    def create_bucket(self, **request: object) -> None:
        self.calls.append(("CreateBucket", request))
        if self._create_code is not None:
            raise ClientError(_error(self._create_code, self._create_status), "CreateBucket")

    def put_object(self, **request: object) -> None:
        self.calls.append(("PutObject", request))
        self._puts += 1
        if self._puts in self._put_failures:
            code, status = self._put_failures[self._puts]
            raise ClientError(_error(code, status), "PutObject")
        key = str(request["Key"])
        if self._honours_condition and request.get("IfNoneMatch") == "*" and key in self._objects:
            raise ClientError(_error("PreconditionFailed", 412), "PutObject")
        self._objects.add(key)

    def delete_object(self, **request: object) -> None:
        self.calls.append(("DeleteObject", request))
        if self._delete_failure is not None:
            code, status = self._delete_failure
            raise ClientError(_error(code, status), "DeleteObject")
        self._objects.discard(str(request["Key"]))

    @property
    def requests(self) -> list[str]:
        return [name for name, _request in self.calls]


class _RefusesCreate(_Store):
    """A credential that may read the bucket and may not create one."""

    def create_bucket(self, **request: object) -> None:
        self.calls.append(("CreateBucket", request))
        raise ClientError(_error("AccessDenied", 403), "CreateBucket")


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


def test_an_existing_bucket_is_left_alone_without_a_create_request() -> None:
    """A credential that may not create buckets still bootstraps against an existing one."""
    client = _RefusesCreate()

    assert converge_bucket(client, BUCKET) is False
    assert client.requests == ["HeadBucket"]


@pytest.mark.parametrize(
    ("region", "configuration"),
    [
        (None, None),
        ("us-east-1", None),
        ("eu-west-3", {"LocationConstraint": "eu-west-3"}),
    ],
    ids=["no region", "us-east-1", "another region"],
)
def test_an_absent_bucket_is_created_with_the_location_constraint_rule(
    region: str | None, configuration: dict[str, str] | None
) -> None:
    client = _Store(head_status=404)

    assert converge_bucket(client, BUCKET, region) is True

    assert client.requests == ["HeadBucket", "CreateBucket"]
    expected: dict[str, Any] = {"Bucket": BUCKET}
    if configuration is not None:
        expected["CreateBucketConfiguration"] = configuration
    assert client.calls[1][1] == expected


@pytest.mark.parametrize("code", ["BucketAlreadyOwnedByYou", "BucketAlreadyExists"])
def test_a_bucket_created_between_the_check_and_the_create_counts_as_present(code: str) -> None:
    """A second bootstrap must not treat the bucket it made last time as a failure."""
    client = _Store(head_status=404, create_code=code)

    assert converge_bucket(client, BUCKET) is False


@pytest.mark.parametrize(
    ("status", "family"),
    [(403, "object-store-access-refused"), (500, "object-store-unavailable"), (400, "object-store-unavailable")],
)
def test_a_refused_or_failed_bucket_check_stops_with_its_family_only(status: int, family: str) -> None:
    """Its inputs are an endpoint and a key pair, and the SDK renders both into its text."""
    client = _Store(head_status=status)

    with pytest.raises(BootstrapError) as refusal:
        converge_bucket(client, BUCKET)

    assert refusal.value.family == family
    assert client.requests == ["HeadBucket"]
    rendered = repr(refusal.value) + str(refusal.value)
    assert ENDPOINT_CANARY not in rendered
    assert CREDENTIAL_CANARY not in rendered


def test_an_unreachable_store_stops_with_the_unavailable_family() -> None:
    class Unreachable(_Store):
        def head_bucket(self, **request: object) -> dict[str, Any]:
            self.calls.append(("HeadBucket", request))
            raise EndpointConnectionError(endpoint_url=ENDPOINT_CANARY)

    with pytest.raises(BootstrapError) as refusal:
        converge_bucket(Unreachable(), BUCKET)

    assert refusal.value.family == "object-store-unavailable"
    assert ENDPOINT_CANARY not in str(refusal.value)


def test_a_bucket_check_redirected_to_another_region_is_unavailable() -> None:
    """A store answers 301 when the bucket lives in another region than the client's."""
    client = _Store(head_status=301)

    with pytest.raises(BootstrapError) as refusal:
        converge_bucket(client, BUCKET)

    assert refusal.value.family == "object-store-unavailable"
    assert client.requests == ["HeadBucket"]


def test_a_bucket_check_error_without_a_status_is_unavailable() -> None:
    class NoStatus(_Store):
        def head_bucket(self, **request: object) -> dict[str, Any]:
            self.calls.append(("HeadBucket", request))
            raise ClientError({"Error": {"Code": "Unknown", "Message": CREDENTIAL_CANARY}}, "HeadBucket")

    with pytest.raises(BootstrapError) as refusal:
        converge_bucket(NoStatus(), BUCKET)

    assert refusal.value.family == "object-store-unavailable"
    assert CREDENTIAL_CANARY not in repr(refusal.value) + str(refusal.value)


@pytest.mark.parametrize(
    ("code", "status", "family"),
    [
        ("AccessDenied", 403, "object-store-access-refused"),
        ("NotImplemented", 501, "object-store-unsupported"),
        ("InternalError", 500, "object-store-unavailable"),
    ],
    ids=["refused", "not implemented", "failed"],
)
def test_a_refused_or_failed_bucket_create_stops_with_its_family_only(code: str, status: int, family: str) -> None:
    """The bucket is absent, and the store refuses or cannot carry out the create."""
    client = _Store(head_status=404, create_code=code, create_status=status)

    with pytest.raises(BootstrapError) as refusal:
        converge_bucket(client, BUCKET)

    assert refusal.value.family == family
    assert client.requests == ["HeadBucket", "CreateBucket"]
    rendered = repr(refusal.value) + str(refusal.value)
    assert ENDPOINT_CANARY not in rendered
    assert CREDENTIAL_CANARY not in rendered


def test_an_unreachable_store_during_the_bucket_create_is_unavailable() -> None:
    class DropsTheCreate(_Store):
        def create_bucket(self, **request: object) -> None:
            self.calls.append(("CreateBucket", request))
            raise EndpointConnectionError(endpoint_url=ENDPOINT_CANARY)

    with pytest.raises(BootstrapError) as refusal:
        converge_bucket(DropsTheCreate(head_status=404), BUCKET)

    assert refusal.value.family == "object-store-unavailable"
    assert ENDPOINT_CANARY not in repr(refusal.value) + str(refusal.value)


# ---------------------------------------------------------------------------
# The create-only check
# ---------------------------------------------------------------------------

_CHECK_KEY = re.compile(
    r"^stable/\.infrahub-sync/bootstrap-check/[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


def _check_keys(client: _Store) -> list[str]:
    return [str(request["Key"]) for _name, request in client.calls]


def test_a_store_that_refuses_the_second_create_only_write_passes_the_check() -> None:
    client = _Store()

    check_create_only(Boto3S3Client(client), BUCKET, "stable")

    assert client.requests == ["PutObject", "PutObject", "DeleteObject"]
    assert all(request.get("IfNoneMatch") == "*" for name, request in client.calls if name == "PutObject")
    [key] = set(_check_keys(client))
    assert _CHECK_KEY.match(key)


def test_a_store_that_accepts_the_second_create_only_write_is_unsupported() -> None:
    """Such a store would let a second artifact write replace a stored one."""
    client = _Store(honours_condition=False)

    with pytest.raises(BootstrapError) as refusal:
        check_create_only(Boto3S3Client(client), BUCKET, "stable")

    assert refusal.value.family == "object-store-unsupported"
    assert client.requests == ["PutObject", "PutObject", "DeleteObject"]
    assert len(set(_check_keys(client))) == 1


@pytest.mark.parametrize("failing_write", [1, 2], ids=["first write", "second write"])
def test_a_failed_check_write_is_unavailable_and_still_deletes_the_key(failing_write: int) -> None:
    client = _Store(put_failures={failing_write: ("InternalError", 500)})

    with pytest.raises(BootstrapError) as refusal:
        check_create_only(Boto3S3Client(client), BUCKET, "stable")

    assert refusal.value.family == "object-store-unavailable"
    assert client.requests[-1] == "DeleteObject"
    assert len(set(_check_keys(client))) == 1
    assert ENDPOINT_CANARY not in str(refusal.value)


@pytest.mark.parametrize(
    ("code", "status", "family"),
    [
        ("AccessDenied", 403, "object-store-access-refused"),
        ("NotImplemented", 501, "object-store-unsupported"),
    ],
    ids=["refused", "not implemented"],
)
def test_a_refused_or_unsupported_check_write_stops_with_its_family_and_deletes_the_key(
    code: str, status: int, family: str
) -> None:
    """A credential without `PutObject`, or a store without conditional writes, is named as such."""
    client = _Store(put_failures={1: (code, status)})

    with pytest.raises(BootstrapError) as refusal:
        check_create_only(Boto3S3Client(client), BUCKET, "stable")

    assert refusal.value.family == family
    assert client.requests == ["PutObject", "DeleteObject"]
    rendered = repr(refusal.value) + str(refusal.value)
    assert ENDPOINT_CANARY not in rendered
    assert CREDENTIAL_CANARY not in rendered


def test_a_refused_check_key_delete_stops_with_the_access_refused_family() -> None:
    client = _Store(delete_failure=("AccessDenied", 403))

    with pytest.raises(BootstrapError) as refusal:
        check_create_only(Boto3S3Client(client), BUCKET, "stable")

    assert refusal.value.family == "object-store-access-refused"
    assert client.requests == ["PutObject", "PutObject", "DeleteObject"]


def test_a_failed_check_write_keeps_its_own_family_when_the_delete_fails_too() -> None:
    """The delete runs on every path; its failure must not replace the one that stopped the check."""
    client = _Store(put_failures={1: ("NotImplemented", 501)}, delete_failure=("AccessDenied", 403))

    with pytest.raises(BootstrapError) as refusal:
        check_create_only(Boto3S3Client(client), BUCKET, "stable")

    assert refusal.value.family == "object-store-unsupported"
    assert client.requests == ["PutObject", "DeleteObject"]


# ---------------------------------------------------------------------------
# The object store steps of a whole bootstrap
# ---------------------------------------------------------------------------


def _resolves(monkeypatch: pytest.MonkeyPatch, *, bundled_store_running: bool) -> None:
    """Answer the name lookup the guard makes, without a network."""

    def getaddrinfo(host: str, *_arguments: object, **_settings: object) -> list[object]:
        if bundled_store_running and host == "object-store":
            return [("family", "type", "proto", "", ("172.18.0.2", 0))]
        raise socket.gaierror(socket.EAI_NONAME, "name does not resolve")

    monkeypatch.setattr(bootstrap.socket, "getaddrinfo", getaddrinfo)


def _settings(monkeypatch: pytest.MonkeyPatch, endpoint: str | None, region: str | None = None) -> None:
    monkeypatch.setenv("INFRAHUB_SYNC_S3_BUCKET", BUCKET)
    monkeypatch.setenv("INFRAHUB_SYNC_S3_PREFIX", "/stable/")
    for name, value in (("INFRAHUB_SYNC_S3_ENDPOINT_URL", endpoint), ("INFRAHUB_SYNC_S3_REGION", region)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    monkeypatch.delenv("INFRAHUB_SYNC_S3_ADDRESSING_STYLE", raising=False)


def _after_the_object_store(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace every later step with one that records it ran."""
    later: list[str] = []
    # `main` would otherwise bind a root handler to this test's captured stream.
    monkeypatch.setattr(bootstrap.logging, "basicConfig", lambda **_settings: None)
    monkeypatch.setattr(bootstrap, "_required", lambda name: name and "present")

    def finish(coroutine: Coroutine[object, object, bool]) -> bool:
        coroutine.close()
        later.append("work pool")
        return False

    monkeypatch.setattr(bootstrap.asyncio, "run", finish)
    monkeypatch.setattr(bootstrap, "apply_deployment", lambda: 0)
    monkeypatch.setattr(bootstrap, "service_product_projection", lambda: later.append("product schema"))
    return later


def _store_from_the_shared_builder(monkeypatch: pytest.MonkeyPatch, store: _Store) -> list[Mapping[str, str]]:
    built: list[Mapping[str, str]] = []

    def build(values: Mapping[str, str]) -> _Store:
        built.append(values)
        return store

    monkeypatch.setattr(bootstrap, "build_s3_client", build)
    return built


def _printed(caplog: pytest.LogCaptureFixture) -> list[str]:
    """Every message the bootstrap printed, at every level it prints."""
    return [record.getMessage() for record in caplog.records]


def test_a_bootstrap_checks_the_bucket_then_the_create_only_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _settings(monkeypatch, "http://object-store:9000", region="eu-west-3")
    _resolves(monkeypatch, bundled_store_running=True)
    store = _Store(head_status=404)
    built = _store_from_the_shared_builder(monkeypatch, store)
    later = _after_the_object_store(monkeypatch)

    assert bootstrap.main() == 0

    assert len(built) == 1
    assert store.requests == ["HeadBucket", "CreateBucket", "PutObject", "PutObject", "DeleteObject"]
    assert store.calls[1][1]["CreateBucketConfiguration"] == {"LocationConstraint": "eu-west-3"}
    assert _CHECK_KEY.match(str(store.calls[2][1]["Key"]))
    assert later == ["work pool", "product schema"]


@pytest.mark.parametrize(
    ("store", "family"),
    [
        (_Store(head_status=403), "object-store-access-refused"),
        (_RefusesCreate(head_status=404), "object-store-access-refused"),
        (_Store(head_status=503), "object-store-unavailable"),
        (_Store(honours_condition=False), "object-store-unsupported"),
        (_Store(put_failures={1: ("NotImplemented", 501)}), "object-store-unsupported"),
    ],
    ids=["refused", "create refused", "unavailable", "unsupported", "conditional write not implemented"],
)
def test_each_object_store_failure_prints_its_family_only(
    store: _Store,
    family: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _settings(monkeypatch, ENDPOINT_CANARY)
    _resolves(monkeypatch, bundled_store_running=False)
    _store_from_the_shared_builder(monkeypatch, store)
    later = _after_the_object_store(monkeypatch)

    with caplog.at_level("INFO"):
        assert bootstrap.main() == 1

    assert _printed(caplog) == [f"refused: {family}"]
    assert later == []


@pytest.mark.parametrize("endpoint", ["https://s3.example.test", None], ids=["external endpoint", "no endpoint"])
def test_an_external_store_while_the_bundled_one_runs_stops_before_any_request(
    endpoint: str | None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An unused MinIO would hold the external store's credential as its root credential."""
    _settings(monkeypatch, endpoint)
    _resolves(monkeypatch, bundled_store_running=True)
    store = _Store()
    _store_from_the_shared_builder(monkeypatch, store)
    later = _after_the_object_store(monkeypatch)

    with caplog.at_level("INFO"):
        assert bootstrap.main() == 1

    assert _printed(caplog) == ["refused: bundled-object-store-not-left-out"]
    assert store.calls == []
    assert later == []


@pytest.mark.parametrize(
    ("endpoint", "bundled_store_running"),
    [
        ("http://object-store:9000", True),
        ("https://s3.example.test", False),
        (None, False),
    ],
    ids=["bundled store", "external store alone", "default endpoint alone"],
)
def test_the_guard_lets_a_consistent_deployment_through(
    endpoint: str | None, *, bundled_store_running: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    _resolves(monkeypatch, bundled_store_running=bundled_store_running)

    bootstrap.refuse_unused_bundled_object_store(endpoint)


_ENDPOINT_REFUSAL = "INFRAHUB_SYNC_S3_ENDPOINT_URL must be an absolute http or https URL without userinfo"


@pytest.mark.parametrize(
    ("setting", "value", "message"),
    [
        ("INFRAHUB_SYNC_S3_ENDPOINT_URL", f"https://access-key:{CREDENTIAL_CANARY}@s3.example.test", _ENDPOINT_REFUSAL),
        ("INFRAHUB_SYNC_S3_ENDPOINT_URL", f"{CREDENTIAL_CANARY}.example.test", _ENDPOINT_REFUSAL),
        (
            "INFRAHUB_SYNC_S3_ADDRESSING_STYLE",
            CREDENTIAL_CANARY,
            "INFRAHUB_SYNC_S3_ADDRESSING_STYLE must be path or virtual",
        ),
        (
            "INFRAHUB_SYNC_S3_REGION",
            "  ",
            "INFRAHUB_SYNC_S3_REGION must be a non-empty string when set",
        ),
    ],
    ids=["endpoint with userinfo", "endpoint without a scheme", "addressing style", "blank region"],
)
def test_a_setting_the_shared_builder_refuses_is_named_without_its_value(
    setting: str,
    value: str,
    message: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The bootstrap client comes from the shared builder, so its setting checks apply here too (FR-034)."""
    _settings(monkeypatch, "https://s3.example.test")
    monkeypatch.setenv(setting, value)
    _resolves(monkeypatch, bundled_store_running=False)
    built: list[Mapping[str, str]] = []

    def build(values: Mapping[str, str]) -> object:
        built.append(values)
        return build_s3_client(values)

    monkeypatch.setattr(bootstrap, "build_s3_client", build)
    later = _after_the_object_store(monkeypatch)

    with caplog.at_level("INFO"):
        assert bootstrap.main() == 1

    assert len(built) == 1
    assert _printed(caplog) == [f"refused: object-store-setting-invalid: {message}"]
    assert CREDENTIAL_CANARY not in caplog.text
    assert later == []


def test_a_client_the_sdk_cannot_build_stops_with_the_unavailable_family(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The SDK renders the endpoint into its own text, so only the family is printed."""
    _settings(monkeypatch, ENDPOINT_CANARY)
    _resolves(monkeypatch, bundled_store_running=False)

    def sdk_failure(*_arguments: object, **_settings: object) -> object:
        raise RuntimeError(ENDPOINT_CANARY)

    monkeypatch.setattr(bootstrap, "build_s3_client", lambda values: build_s3_client(values, builder=sdk_failure))
    later = _after_the_object_store(monkeypatch)

    with caplog.at_level("INFO"):
        assert bootstrap.main() == 1

    assert _printed(caplog) == ["refused: object-store-unavailable"]
    assert ENDPOINT_CANARY not in caplog.text
    assert later == []


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


def _object_store_converged(monkeypatch: pytest.MonkeyPatch, *, created: bool) -> None:
    """Replace the object store steps with ones that succeed without a request."""
    monkeypatch.setattr(bootstrap, "build_s3_client", lambda _values: _Store())
    monkeypatch.setattr(bootstrap, "refuse_unused_bundled_object_store", lambda _endpoint: None)
    monkeypatch.setattr(bootstrap, "converge_bucket", lambda *_arguments: created)
    monkeypatch.setattr(bootstrap, "check_create_only", lambda *_arguments: None)


def _converged(monkeypatch: pytest.MonkeyPatch, projection: ProductProjection) -> None:
    """Replace every provider a successful convergence reaches, and nothing else."""
    _object_store_converged(monkeypatch, created=True)
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
    _object_store_converged(monkeypatch, created=False)
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
    _object_store_converged(monkeypatch, created=False)
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


KEY_CANARY = "bootstrap-ca-directory-key-canary"


def test_the_trust_store_is_installed_before_anything_converges(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(bootstrap, "install_custom_ca_bundle", lambda: calls.append("trust store"))
    monkeypatch.setattr(bootstrap, "_converge", lambda: calls.append("converge"))

    assert bootstrap.main() == 0
    assert calls == ["trust store", "converge"]


def test_a_private_key_in_the_ca_directory_stops_the_bootstrap_without_its_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = tmp_path / "ca"
    directory.mkdir()
    (directory / "operator.pem").write_text(
        f"-----BEGIN PRIVATE KEY-----\n{KEY_CANARY}\n-----END PRIVATE KEY-----\n", encoding="utf-8"
    )
    monkeypatch.setenv("INFRAHUB_SYNC_CA_DIRECTORY", str(directory))
    converged: list[bool] = []
    monkeypatch.setattr(bootstrap, "_converge", lambda: converged.append(True))

    assert bootstrap.main() == 1

    captured = capsys.readouterr()
    assert "CA certificate file operator.pem holds a private key" in captured.err
    assert KEY_CANARY not in captured.err + captured.out
    assert "Traceback" not in captured.err
    assert converged == []
