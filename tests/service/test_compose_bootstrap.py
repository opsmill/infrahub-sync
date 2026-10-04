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

from infrahub_sync.product_store import configs, local_product_projection
from infrahub_sync.service import bootstrap
from infrahub_sync.service.bootstrap import (
    PROCESS_POOL_TYPE,
    BootstrapError,
    converge_bucket,
    converge_work_pool,
)
from infrahub_sync.service.prefect_server import (
    PrefectServerNotConfiguredError,
    PrefectServerUnavailableError,
    PrefectVersionMismatchError,
)
from tests.configuration.validation_packages import package_data

if TYPE_CHECKING:
    from collections.abc import Coroutine

    from prefect.client.schemas.actions import WorkPoolCreate

    from infrahub_sync.product_store import ProductProjection

_REQUEST = httpx.Request("GET", "http://task-manager:4200/api/work_pools/infrahub-sync")
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

    @staticmethod
    async def read_deployment_by_name(_name: str) -> object:
        raise ObjectNotFound(httpx.HTTPStatusError("404", request=_REQUEST, response=_RESPONSE))


def _projection(tmp_path: Path) -> ProductProjection:
    return local_product_projection(tmp_path / "product")


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
# The registry a bootstrap must leave alone
# ---------------------------------------------------------------------------


def _converged(monkeypatch: pytest.MonkeyPatch, projection: ProductProjection) -> None:
    """Replace every provider a successful convergence reaches, and nothing else."""
    monkeypatch.setattr(bootstrap.boto3, "client", lambda *_arguments, **_settings: _Bucket())
    monkeypatch.setattr(bootstrap, "converge_bucket", lambda *_arguments: True)
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


def _recorded_steps(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace every provider step before the schema with one that records its turn.

    The bucket step is replaced too, so that no object-store client is built; it
    records nothing, since its place is not what these cases assert.
    """
    steps: list[str] = []

    async def converge_pool(_name: str) -> bool:  # noqa: RUF029 - stands in for the coroutine `asyncio.run` drives
        steps.append("work-pool")
        return False

    def deploy() -> int:
        steps.append("deployment")
        return 0

    monkeypatch.setattr(bootstrap, "_required", lambda _name: "present")
    monkeypatch.setattr(bootstrap.boto3, "client", lambda *_arguments, **_settings: _Bucket())
    monkeypatch.setattr(bootstrap, "converge_bucket", lambda *_arguments: False)
    monkeypatch.setattr(bootstrap, "_check_prefect_server", lambda: steps.append("prefect-server"))
    monkeypatch.setattr(bootstrap, "_converge_pool", converge_pool)
    monkeypatch.setattr(bootstrap, "apply_deployment", deploy)
    return steps


def test_bootstrap_converges_in_dependency_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """The version check precedes every write, and the product schema comes last."""
    steps = _recorded_steps(monkeypatch)
    monkeypatch.setattr(bootstrap, "service_product_projection", lambda: steps.append("product-schema"))

    assert bootstrap.main() == 0
    assert steps == ["prefect-server", "work-pool", "deployment", "product-schema"]


def _bootstrap_before_deployment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the successful steps before deployment without leaking a coroutine."""
    monkeypatch.setattr(bootstrap.boto3, "client", lambda *_arguments, **_settings: _Bucket())
    monkeypatch.setattr(bootstrap, "converge_bucket", lambda *_arguments: False)
    monkeypatch.setattr(bootstrap, "_required", lambda _name: "present")
    monkeypatch.setattr(bootstrap, "_check_prefect_server", lambda: None)

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
    monkeypatch.setattr(bootstrap, "_required", lambda _name: "present")
    monkeypatch.setattr(bootstrap, "_check_prefect_server", lambda: None)


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


def test_a_task_manager_of_another_prefect_version_gets_no_pool_or_deployment(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The version check runs before anything is written to Infrahub's task manager."""
    monkeypatch.setattr(bootstrap, "_required", lambda _name: "present")
    monkeypatch.setattr(bootstrap.boto3, "client", lambda *_arguments, **_settings: _Bucket())
    monkeypatch.setattr(bootstrap, "converge_bucket", lambda *_arguments: False)

    def mismatch() -> None:
        server_version = "3.7.5"
        raise PrefectVersionMismatchError(server_version, "3.8.6")

    monkeypatch.setattr(bootstrap, "_check_prefect_server", mismatch)
    monkeypatch.setattr(bootstrap, "_converge_pool", lambda _name: pytest.fail("a pool was written"))
    monkeypatch.setattr(bootstrap, "apply_deployment", lambda: pytest.fail("a deployment was written"))

    with caplog.at_level("ERROR"):
        result = bootstrap.main()

    assert result == 1
    assert "prefect-server-unsupported" in caplog.text
    assert "3.7.5" in caplog.text


class _TaskManager:
    """A task manager whose version route answers each attempt from a script.

    Each answer is a version string or the exception the check would raise; once the
    script runs out, the last answer repeats, so one entry stands for a server that
    never changes its mind.
    """

    def __init__(self, *answers: str | Exception) -> None:
        self._answers = answers
        self.attempts = 0

    async def version(self) -> str:
        self.attempts += 1
        answer = self._answers[min(self.attempts, len(self._answers)) - 1]
        if isinstance(answer, Exception):
            raise answer
        return answer


def _bootstrap_waiting_on(task_manager: _TaskManager, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Drive the real wait against a scripted task manager, without sleeping, and record every write."""
    steps: list[str] = []

    async def converge_pool(_name: str) -> bool:  # noqa: RUF029 - stands in for the coroutine `asyncio.run` drives
        steps.append("work-pool")
        return True

    def deploy() -> int:
        steps.append("deployment")
        return 0

    monkeypatch.setattr(bootstrap, "_required", lambda _name: "present")
    monkeypatch.setattr(bootstrap.boto3, "client", lambda *_arguments, **_settings: _Bucket())
    monkeypatch.setattr(bootstrap, "converge_bucket", lambda *_arguments: False)
    monkeypatch.setattr(bootstrap, "log_prefect_auth_mode", lambda: None)
    monkeypatch.setattr(bootstrap, "_read_prefect_server_version", task_manager.version)
    monkeypatch.setattr(bootstrap.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(bootstrap, "_converge_pool", converge_pool)
    monkeypatch.setattr(bootstrap, "apply_deployment", deploy)
    monkeypatch.setattr(bootstrap, "service_product_projection", lambda: steps.append("product-schema"))
    return steps


def test_a_task_manager_still_starting_is_waited_for_and_then_converged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """No `depends_on` can wait for Infrahub's task manager, so a server not answering yet is asked again."""
    task_manager = _TaskManager(PrefectServerUnavailableError(), PrefectServerUnavailableError(), "3.8.6")
    steps = _bootstrap_waiting_on(task_manager, monkeypatch)

    with caplog.at_level("INFO"):
        result = bootstrap.main()

    assert result == 0
    assert task_manager.attempts == 3
    assert steps == ["work-pool", "deployment", "product-schema"]
    assert "attempt 2 of" in caplog.text


@pytest.mark.parametrize(
    ("answer", "family"),
    [
        (PrefectVersionMismatchError("3.7.5", "3.8.6"), "prefect-server-unsupported"),
        (PrefectServerNotConfiguredError(), "prefect-server-unavailable"),
    ],
    ids=["another-version", "no-url"],
)
def test_a_misconfigured_task_manager_is_refused_on_the_first_attempt(
    answer: Exception, family: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Another Prefect version or an unset URL is configuration, and waiting changes neither."""
    task_manager = _TaskManager(answer)
    steps = _bootstrap_waiting_on(task_manager, monkeypatch)

    with caplog.at_level("ERROR"):
        result = bootstrap.main()

    assert result == 1
    assert task_manager.attempts == 1
    assert steps == []
    assert family in caplog.text


def test_a_task_manager_that_never_answers_is_refused_as_unavailable(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """After the bounded wait the family names the cause: the server never answered, not an unsupported one."""
    task_manager = _TaskManager(PrefectServerUnavailableError())
    steps = _bootstrap_waiting_on(task_manager, monkeypatch)
    monkeypatch.setattr(bootstrap, "_SERVER_WAIT_ATTEMPTS", 3)

    with caplog.at_level("ERROR"):
        result = bootstrap.main()

    assert result == 1
    assert task_manager.attempts == 3
    assert steps == []
    assert "prefect-server-unavailable" in caplog.text
    assert "prefect-server-unsupported" not in caplog.text


class _Deployments:
    def __init__(self, pool: str | None) -> None:
        self._pool = pool

    async def read_deployment_by_name(self, name: str) -> object:
        assert name == "infrahub-sync-service/run"
        if self._pool == "absent":
            raise ObjectNotFound(httpx.HTTPStatusError("404", request=_REQUEST, response=_RESPONSE))
        return type("Deployment", (), {"work_pool_name": self._pool})()


@pytest.mark.parametrize("existing", ["absent", None, POOL], ids=["no-deployment", "no-pool", "this-pool"])
async def test_a_deployment_of_this_pool_or_none_is_accepted(existing: str | None) -> None:
    await bootstrap.refuse_a_deployment_of_another_pool(_Deployments(existing), POOL)


async def test_a_deployment_bound_to_another_pool_is_refused_as_a_second_installation() -> None:
    with pytest.raises(bootstrap.BootstrapError) as caught:
        await bootstrap.refuse_a_deployment_of_another_pool(_Deployments("another-sync-pool"), POOL)

    assert caught.value.family == bootstrap.DEPLOYMENT_OWNED_ELSEWHERE
