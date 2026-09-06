"""The deployment bootstrap converges durable objects and repeats safely.

Every case here drives the real convergence functions against fakes that answer
the way the providers do. What is being checked is the decision each one makes —
create, leave alone, or refuse — because that decision is what makes a second
`start` a no-op instead of a second set of objects.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

pytest.importorskip("prefect")
pytest.importorskip("opsmill_prefect_extras")

import httpx
from botocore.exceptions import ClientError
from prefect.exceptions import ObjectNotFound

from infrahub_sync.configuration.models import parse_configuration_package
from infrahub_sync.product_store import local_product_projection
from infrahub_sync.service import bootstrap
from infrahub_sync.service.bootstrap import (
    BOOTSTRAP_ACTOR,
    PROCESS_POOL_TYPE,
    BootstrapError,
    converge_bucket,
    converge_configuration,
    converge_work_pool,
)
from tests.configuration.validation_packages import package_data

if TYPE_CHECKING:
    from collections.abc import Coroutine

    from prefect.client.schemas.actions import WorkPoolCreate

    from infrahub_sync.configuration import ConfigurationPackage
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
    """A minimal S3 client that answers create_bucket the way the SDK does."""

    def __init__(self, *, response: dict[str, Any] | None = None) -> None:
        self.created: list[str] = []
        self._response = response

    def create_bucket(self, *, Bucket: str) -> None:  # noqa: N803 -- the SDK's own keyword
        if self._response is not None:
            raise ClientError(self._response, "CreateBucket")
        self.created.append(Bucket)


def _client_error(code: str) -> dict[str, Any]:
    return {
        "Error": {
            "Code": code,
            "Message": f"{code} at {ENDPOINT_CANARY} using {CREDENTIAL_CANARY}",
        },
        "ResponseMetadata": {"HTTPStatusCode": 403},
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


def _package(name: str = "from-netbox", *, url: str | None = None) -> ConfigurationPackage:
    content = copy.deepcopy(package_data())
    content["configuration"]["name"] = name
    if url is not None:
        content["configuration"]["destination"]["settings"]["url"] = url
    return parse_configuration_package(content)


def _projection(tmp_path: Path) -> ProductProjection:
    return local_product_projection(tmp_path / "product")


def _audit_actors(projection: ProductProjection) -> list[str]:
    events = projection.audit_events() if hasattr(projection, "list_audit_events") else ()
    return [event.actor for event in events]


# ---------------------------------------------------------------------------
# The artifact bucket
# ---------------------------------------------------------------------------


def test_an_absent_bucket_is_created() -> None:
    client = _Bucket()

    assert converge_bucket(client, BUCKET) is True
    assert client.created == [BUCKET]


@pytest.mark.parametrize("code", ["BucketAlreadyOwnedByYou", "BucketAlreadyExists"])
def test_a_bucket_that_is_already_there_is_left_alone(code: str) -> None:
    """A second bootstrap must not treat the bucket it made last time as a failure."""
    client = _Bucket(response=_client_error(code))

    assert converge_bucket(client, BUCKET) is False


def test_an_object_store_failure_carries_no_endpoint_or_credential() -> None:
    """Its inputs are an endpoint and a key pair, and the SDK renders both into its text."""
    client = _Bucket(response=_client_error("AccessDenied"))

    with pytest.raises(BootstrapError) as refusal:
        converge_bucket(client, BUCKET)

    assert refusal.value.family == "object-store-unavailable"
    rendered = repr(refusal.value) + str(refusal.value)
    assert ENDPOINT_CANARY not in rendered
    assert CREDENTIAL_CANARY not in rendered


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
# The bundled configuration
# ---------------------------------------------------------------------------


def test_the_bundled_configuration_is_registered_once(tmp_path: Path) -> None:
    projection = _projection(tmp_path)
    package = _package()

    config_id, registry_version, created = converge_configuration(projection, package)

    stored = projection.list_configuration_versions(config_id)
    assert (created, registry_version) == (True, 1)
    assert [version.package_checksum for version in stored] == [package.checksum()]


def test_a_first_registration_records_exactly_one_bootstrap_audit_event(tmp_path: Path) -> None:
    """One event, with a fixed actor: there is no operator here to attribute a decision to."""
    projection = _projection(tmp_path)

    converge_configuration(projection, _package())

    events = projection.audit_events()
    assert [event.actor for event in events] == [BOOTSTRAP_ACTOR]


def test_a_repeated_bootstrap_finds_the_registration_it_made(tmp_path: Path) -> None:
    """The registry allocates the identifier, so a repeat has to recognise its own work.

    Nothing in the bundle can name the configuration created last time, which is
    why identity is the declared name plus the checksum rather than an id.
    """
    projection = _projection(tmp_path)
    package = _package()
    first = converge_configuration(projection, package)

    second = converge_configuration(projection, package)

    assert second == (first[0], first[1], False)
    assert len(projection.list_configurations()) == 1
    assert len(projection.list_configuration_versions(first[0])) == 1


def test_a_repeated_bootstrap_records_no_second_audit_event(tmp_path: Path) -> None:
    """Convergence is not a decision, so it leaves no evidence of one."""
    projection = _projection(tmp_path)
    package = _package()
    converge_configuration(projection, package)

    converge_configuration(projection, package)

    assert len(projection.audit_events()) == 1


def test_changed_content_under_the_same_name_is_refused(tmp_path: Path) -> None:
    """Registering a second one would leave two live candidates under one name."""
    projection = _projection(tmp_path)
    converge_configuration(projection, _package())

    with pytest.raises(BootstrapError) as refusal:
        converge_configuration(projection, _package(url="http://elsewhere.internal:8000"))

    assert refusal.value.family == "configuration-conflict"
    assert len(projection.list_configurations()) == 1


def test_two_registrations_under_one_name_are_refused(tmp_path: Path) -> None:
    """A registry this job did not create is one it must not add to."""
    projection = _projection(tmp_path)
    package = _package()
    projection.create_configuration(package)
    projection.create_configuration(package)

    with pytest.raises(BootstrapError) as refusal:
        converge_configuration(projection, package)

    assert refusal.value.family == "configuration-conflict"


def test_a_configuration_registered_under_another_name_is_not_matched(tmp_path: Path) -> None:
    """Identity is the declared name; an unrelated configuration is simply unrelated."""
    projection = _projection(tmp_path)
    projection.create_configuration(_package(name="something-else"))

    config_id, _registry_version, created = converge_configuration(projection, _package())

    assert created is True
    assert len(projection.list_configurations()) == 2
    assert [version.package_checksum for version in projection.list_configuration_versions(config_id)] == [
        _package().checksum()
    ]


def _bootstrap_before_deployment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the successful steps before deployment without leaking a coroutine."""
    monkeypatch.setattr(bootstrap, "_bundled_package", _package)
    monkeypatch.setattr(bootstrap.boto3, "client", lambda *_arguments, **_settings: _Bucket())
    monkeypatch.setattr(bootstrap, "converge_bucket", lambda *_arguments: False)
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
    monkeypatch.setattr(bootstrap, "_bundled_package", _package)
    monkeypatch.setattr(bootstrap.boto3, "client", lambda *_arguments, **_settings: _Bucket())
    monkeypatch.setattr(bootstrap, "converge_bucket", lambda *_arguments: False)
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
