"""A stored value is redacted from the moment it is decrypted or released (spec 027, research R6).

The redaction lists of the Sync API and of a worker were read from the process environment. A
stored value is not in the environment, so it is added to the redaction registry of the request or
of the run before the function that produced it returns it.

No assertion of this module shows a value: each one computes whether a text holds the value first,
then asserts on that boolean, so a failure message holds `True` or `False` only.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import threading
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from uuid import NAMESPACE_URL, uuid5

import pytest

pytest.importorskip("prefect")
pytest.importorskip("fastapi")
pytest.importorskip("cryptography")

from fastapi import FastAPI
from fastapi.testclient import TestClient
from prefect.testing.utilities import prefect_test_harness

from infrahub_sync.execution import (
    MIN_SECRET_LENGTH,
    REDACTED,
    LiveSecretValues,
    NoRedactionScopeError,
    merge_secret_values,
    redact,
    redaction_scope,
    register_secret_values,
    registered_secret_values,
    sanitize_exception_chain,
)
from infrahub_sync.infrahub_errors import cause_chain
from infrahub_sync.orchestration.flow import remote_log_bridge
from infrahub_sync.product_store import PrefectExecutionLink, ProductRun, local_product_projection
from infrahub_sync.service import flow as service_flow
from infrahub_sync.service.app import RedactionScopeMiddleware, create_app
from infrahub_sync.service.apply_guard import ApplyGuardError, hold_apply_guard
from infrahub_sync.service.credential_crypto import CredentialCipher
from infrahub_sync.service.flow import service_sync_run
from infrahub_sync.service.schedules import ScheduleService
from infrahub_sync.service.service import RunService, ServiceAPIError, is_free_text
from tests.service.accounts import seed_accounts
from tests.service.execution_fixtures import append_execution
from tests.service.schedule_fixtures import FakeOrchestration

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from pathlib import Path

    from infrahub_sync.product_store import ProductProjection

_KEY = bytes(range(32))
_CREDENTIAL_ID = "cred_0123456789abcdef0123456789abcdef"
# Test values only; they reach no system and are in no environment variable.
_TOKEN = "released-test-token-0001"  # noqa: S105 - a test value
_PASSWORD = "released-test-password-0001"  # noqa: S105 - a test value
_OTHER = "released-test-other-0001"
_TEST_LOGGER = "infrahub_sync.service.test_released_value_redaction"
_WORKER_ID = "8c1da53d-0e6b-4d3d-a0f1-97b6a9ccebf0"


def _holds(text: str, *values: str) -> bool:
    """Return whether `text` holds one of the values; the caller asserts on the boolean only."""
    return any(value in text for value in values)


def _reachable_text(error: BaseException) -> str:
    """Return the message of the error and of every cause and context reachable from it."""
    return "\n".join(f"{link!s} {link.args!r}" for link in cause_chain(error))


class _Records(logging.Handler):
    """Keep every record a logger writes."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def text(self) -> str:
        return "\n".join(record.getMessage() for record in self.records)


@pytest.fixture
def records() -> Iterator[_Records]:
    """The records of the whole logging tree during one test."""
    handler = _Records()
    root = logging.getLogger()
    previous = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        yield handler
    finally:
        root.removeHandler(handler)
        root.setLevel(previous)


def _encrypted(fields: dict[str, str]) -> tuple[CredentialCipher, dict[str, Any]]:
    cipher = CredentialCipher(_KEY)
    key_id, nonce, ciphertext = cipher.encrypt(_CREDENTIAL_ID, 1, fields)
    return cipher, {"key_id": key_id, "nonce": nonce, "ciphertext": ciphertext}


# --------------------------------------------------------------------------- #
# The registry
# --------------------------------------------------------------------------- #


def test_a_decrypted_value_is_in_the_registry_when_decrypt_returns() -> None:
    cipher, stored = _encrypted({"username": "sync-reader", "password": _PASSWORD})

    with redaction_scope() as registry:
        fields = cipher.decrypt(_CREDENTIAL_ID, 1, **stored)
        registered = set(fields.values()) <= set(registry)
        message = redact(f"login failed for {fields['username']} with {fields['password']}", ())

    assert registered
    redacted = message == f"login failed for {REDACTED} with {REDACTED}"
    assert redacted


def test_decrypt_outside_a_redaction_scope_returns_no_value() -> None:
    cipher, stored = _encrypted({"token": _TOKEN})

    with pytest.raises(NoRedactionScopeError) as caught:
        cipher.decrypt(_CREDENTIAL_ID, 1, **stored)

    leaked = _holds(_reachable_text(caught.value), _TOKEN)
    assert not leaked


def test_a_registered_value_ends_with_its_scope() -> None:
    with redaction_scope():
        register_secret_values([_TOKEN])
        inside = _holds(redact(f"token {_TOKEN}", ()), _TOKEN)
    outside = redact(f"token {_TOKEN}", ()) == f"token {_TOKEN}"

    assert not inside
    assert outside
    assert registered_secret_values() == ()


def test_a_value_below_the_length_floor_is_not_registered() -> None:
    short = "x" * (MIN_SECRET_LENGTH - 1)

    with redaction_scope() as registry:
        register_secret_values([short, "y" * MIN_SECRET_LENGTH])

        assert registry == ["y" * MIN_SECRET_LENGTH]


def test_a_rebuilt_list_keeps_the_values_registered_before() -> None:
    registry = [_TOKEN]

    merge_secret_values(registry, ["environment-value-0001", _TOKEN])

    kept = registry == [_TOKEN, "environment-value-0001"]
    assert kept


def test_a_thread_that_copies_the_context_registers_into_the_same_registry() -> None:
    with redaction_scope() as registry:
        context = contextvars.copy_context()
        thread = threading.Thread(target=context.run, args=(register_secret_values, [_TOKEN]))
        thread.start()
        thread.join()
        shared = registry == [_TOKEN]

    assert shared


def test_a_live_list_reads_the_registry_of_the_current_scope_only() -> None:
    live = LiveSecretValues(("fixed-value-0001",))

    with redaction_scope():
        register_secret_values([_TOKEN])
        inside = tuple(live)
    outside = tuple(live)

    read_inside = inside == ("fixed-value-0001", _TOKEN)
    read_outside = outside == ("fixed-value-0001",)
    in_repr = _holds(repr(live), _TOKEN)
    assert read_inside
    assert read_outside
    assert not in_repr


# --------------------------------------------------------------------------- #
# The Sync API: the registry of a request
# --------------------------------------------------------------------------- #


class _Api:
    """The application with test-only routes that decrypt a stored value and then fail."""

    def __init__(self, tmp_path: Path) -> None:
        projection = local_product_projection(tmp_path.resolve())
        resolver = seed_accounts(projection, {"owner": {"token": "released-value-owner-token"}}).resolver
        # No route these tests call reaches the orchestration.
        self.service = RunService(projection, object(), secrets=resolver.secret_values)  # ty: ignore[invalid-argument-type]
        self.schedules = ScheduleService(
            projection, FakeOrchestration(), resolver, delivered_write_targets=(), secrets=resolver.secret_values
        )
        self.app = create_app(self.service, resolver)
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.cipher, self.stored = _encrypted({"token": _TOKEN})
        # What the route saw inside the request; a test reads booleans from it only.
        self.seen: dict[str, bool] = {}

    def decrypt(self) -> str:
        return self.cipher.decrypt(_CREDENTIAL_ID, 1, **self.stored)["token"]

    def service_secrets(self) -> Sequence[str]:
        return self.service._secrets  # pylint: disable=protected-access

    def schedule_secrets(self) -> Sequence[str]:
        return self.schedules._secrets  # pylint: disable=protected-access


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _Api:
    monkeypatch.delenv("INFRAHUB_SYNC_REQUIRE_APPROVAL", raising=False)
    return _Api(tmp_path)


def test_a_refusal_that_holds_a_decrypted_value_is_redacted_in_the_answer_and_the_log(
    api: _Api, records: _Records
) -> None:
    application = api.app
    assert isinstance(application, FastAPI)

    # A plain `def` route: FastAPI runs it in the thread pool, with a copy of the request's context.
    @application.get("/test-only/decrypt-and-refuse")
    def decrypt_and_refuse() -> None:
        value = api.decrypt()
        failure = RuntimeError(f"the destination refused the token {value}")
        logging.getLogger(_TEST_LOGGER).error("schema read failed: %s", redact(str(failure), api.service_secrets()))
        error = ServiceAPIError(409, "test-refusal", f"refused: {failure}", secrets=tuple(api.service_secrets()))
        error.__cause__ = sanitize_exception_chain(failure, api.service_secrets())
        api.seen["cause"] = _holds(_reachable_text(error.__cause__), _TOKEN)
        raise error

    response = api.client.get("/test-only/decrypt-and-refuse")

    assert response.status_code == 409
    in_answer = _holds(response.text, _TOKEN)
    in_log = _holds(records.text(), _TOKEN)
    assert not in_answer
    assert not in_log
    assert response.json()["error"]["message"] == f"refused: the destination refused the token {REDACTED}"
    assert api.seen == {"cause": False}
    assert f"schema read failed: the destination refused the token {REDACTED}" in records.text()


def test_a_refusal_built_without_a_list_is_redacted_with_the_registry_of_the_request(api: _Api) -> None:
    application = api.app
    assert isinstance(application, FastAPI)

    @application.get("/test-only/decrypt-and-refuse-without-a-list")
    def decrypt_and_refuse() -> None:
        raise ServiceAPIError(409, "test-refusal", f"refused: {api.decrypt()}")

    response = api.client.get("/test-only/decrypt-and-refuse-without-a-list")

    in_answer = _holds(response.text, _TOKEN)
    assert not in_answer
    assert response.json()["error"]["message"] == f"refused: {REDACTED}"


def test_an_unhandled_error_that_holds_a_decrypted_value_reaches_no_answer_and_no_log(
    api: _Api, records: _Records
) -> None:
    application = api.app
    assert isinstance(application, FastAPI)

    @application.get("/test-only/decrypt-and-fail")
    def decrypt_and_fail() -> None:
        msg = f"unexpected failure with {api.decrypt()}"
        raise RuntimeError(msg)

    response = api.client.get("/test-only/decrypt-and-fail")

    assert response.status_code == 503
    in_answer = _holds(response.text, _TOKEN)
    in_log = _holds(records.text(), _TOKEN)
    assert not in_answer
    assert not in_log


def test_the_lists_of_the_services_hold_a_decrypted_value_during_its_request_only(api: _Api) -> None:
    application = api.app
    assert isinstance(application, FastAPI)

    @application.get("/test-only/decrypt")
    def decrypt() -> dict[str, bool]:
        value = api.decrypt()
        api.seen["service"] = value in tuple(api.service_secrets())
        api.seen["schedules"] = value in tuple(api.schedule_secrets())
        api.seen["free_text"] = is_free_text(f"reason with {value}", api.service_secrets())
        return {}

    @application.get("/test-only/next-request")
    def next_request() -> dict[str, bool]:
        api.seen["service_after"] = _TOKEN in tuple(api.service_secrets())
        api.seen["free_text_after"] = is_free_text(f"reason with {_TOKEN}", api.service_secrets())
        return {}

    assert api.client.get("/test-only/decrypt").status_code == 200
    assert api.client.get("/test-only/next-request").status_code == 200

    assert api.seen == {
        "service": True,
        "schedules": True,
        "free_text": False,
        # The value is in no list after its request, so no later request can test a candidate value.
        "service_after": False,
        "free_text_after": True,
    }


def test_the_lifespan_of_the_application_has_no_registry() -> None:
    """The lifespan lasts until the process stops, so a registry bound for it would never end."""
    seen: dict[str, bool] = {}

    async def application(scope: dict[str, Any], _receive: object, _send: object) -> None:  # noqa: RUF029
        try:
            register_secret_values([_TOKEN])
        except NoRedactionScopeError:
            seen[scope["type"]] = False
        else:
            seen[scope["type"]] = True

    middleware = RedactionScopeMiddleware(application)  # ty: ignore[invalid-argument-type]

    async def call() -> None:
        for kind in ("lifespan", "http"):
            await middleware({"type": kind}, None, None)  # ty: ignore[invalid-argument-type]

    asyncio.run(call())

    assert seen == {"lifespan": False, "http": True}


def test_a_guard_failure_is_redacted_with_a_value_registered_while_the_guard_is_taken() -> None:
    def connect() -> Any:  # noqa: ANN401 - the connection is never returned.
        register_secret_values([_PASSWORD])
        msg = f"connection refused for password={_PASSWORD}"
        raise RuntimeError(msg)

    with redaction_scope(), pytest.raises(ApplyGuardError) as caught, hold_apply_guard("config-001", connect=connect):
        pass

    leaked = _holds(_reachable_text(caught.value), _PASSWORD)
    assert not leaked


# --------------------------------------------------------------------------- #
# The worker: the registry of a run
# --------------------------------------------------------------------------- #


class _RunLogger:
    """A run logger that keeps the text of each line."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def log(self, level: int, msg: str, *args: object, **_kwargs: object) -> None:
        del level
        self.lines.append(msg % args if args else msg)


def test_the_log_bridge_redacts_a_value_registered_after_it_was_attached() -> None:
    run_logger = _RunLogger()
    source = logging.getLogger(_TEST_LOGGER)

    with redaction_scope() as secrets, remote_log_bridge(run_logger, secrets=secrets):  # ty: ignore[invalid-argument-type]
        source.info("before the release")
        # Registered in another thread, which shares the run's registry through a copied context.
        registering = threading.Thread(
            target=contextvars.copy_context().run, args=(register_secret_values, [_TOKEN, _PASSWORD])
        )
        registering.start()
        registering.join()
        source.error("the source refused the token %s", _TOKEN)
        # Written by a thread that has no copy of the context: the bridge holds the registry itself.
        writing = threading.Thread(target=source.error, args=("the source refused the password %s", _PASSWORD))
        writing.start()
        writing.join()

    text = "\n".join(run_logger.lines)
    leaked = _holds(text, _TOKEN, _PASSWORD)
    assert not leaked
    assert len(run_logger.lines) == 3
    assert text.count(REDACTED) == 2


@pytest.fixture(scope="module")
def prefect_harness(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Run flows against a throwaway Prefect backend, with Prefect's files in a temporary home."""
    home = tmp_path_factory.mktemp("prefect-home")
    with pytest.MonkeyPatch.context() as patcher:
        patcher.setenv("PREFECT_HOME", str(home))
        patcher.setenv("PREFECT_LOCAL_STORAGE_PATH", str(home / "storage"))
        with prefect_test_harness():
            yield


@pytest.fixture
def run_records() -> Iterator[_Records]:
    """The records of `prefect.flow_runs`, the logger `get_run_logger` returns in a flow run."""
    handler = _Records()
    run_logger = logging.getLogger("prefect.flow_runs")
    run_logger.addHandler(handler)
    try:
        yield handler
    finally:
        run_logger.removeHandler(handler)


def _claimed_run(tmp_path: Path, run_id: str) -> tuple[ProductProjection, str]:
    projection = local_product_projection(tmp_path)
    projection.create_run(
        ProductRun(
            run_id=run_id,
            operation="plan",
            configuration_reference="config-001@1",
            config_id="config-001",
            registry_version=1,
            package_checksum="a" * 64,
            actor="owner",
            started_at=datetime.now(timezone.utc),
            phase="accepted",
            summary={"sync_name": "inventory"},
        )
    )
    flow_run_id = str(uuid5(NAMESPACE_URL, run_id))
    append_execution(projection, run_id, PrefectExecutionLink(flow_run_id=flow_run_id, purpose="plan", attempt=1))
    assert projection.claim_execution(run_id, flow_run_id, worker_id=_WORKER_ID)
    return projection, flow_run_id


@pytest.mark.usefixtures("prefect_harness")
def test_a_value_released_to_a_run_is_redacted_in_the_run_log_and_the_failure_cause(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, run_records: _Records
) -> None:
    run_id = "run-released-value"
    projection, flow_run_id = _claimed_run(tmp_path, run_id)
    monkeypatch.setattr(service_flow, "_runtime", lambda: projection)
    monkeypatch.setattr(
        service_flow, "_claim_current_execution", lambda _projection, _run_id: (flow_run_id, _WORKER_ID)
    )
    monkeypatch.setattr(service_flow, "create_markdown_artifact", lambda **_kwargs: None)
    seen: dict[str, bool] = {}

    def stage(*_args: object, secrets: list[str], **_kwargs: object) -> None:
        # What the worker's credential client does with the values the Sync API releases.
        register_secret_values([_TOKEN, _PASSWORD])
        seen["in_the_list_of_the_run"] = {_TOKEN, _PASSWORD} <= set(secrets)
        # What the stage does next: it rebuilds the list from the configuration.
        merge_secret_values(secrets, [_OTHER])
        seen["kept_after_the_rebuild"] = {_TOKEN, _PASSWORD, _OTHER} <= set(secrets)
        logging.getLogger("infrahub_sync.adapters.test").error("the source refused the token %s", _TOKEN)
        msg = f"authentication failed with password {_PASSWORD}"
        raise RuntimeError(msg) from ValueError(f"token {_TOKEN} was refused")

    monkeypatch.setattr(service_flow, "_execute_stage", stage)

    state = service_sync_run(run_id, "plan", "config-001", 1, "a" * 64, return_state=True)  # ty: ignore[no-matching-overload] - TODO: ty cannot select prefect's ParamSpec return_state overload

    assert seen == {"in_the_list_of_the_run": True, "kept_after_the_rebuild": True}
    assert state.is_failed()
    with pytest.raises(RuntimeError) as caught:
        state.result()
    in_cause = _holds(_reachable_text(caught.value), _TOKEN, _PASSWORD)
    in_state = _holds(str(state.message), _TOKEN, _PASSWORD)
    in_log = _holds(run_records.text(), _TOKEN, _PASSWORD)
    stored = projection.lookup_run(run_id).value
    assert stored is not None
    in_store = _holds(json.dumps(stored.model_dump(mode="json")), _TOKEN, _PASSWORD)
    assert not in_cause
    assert not in_state
    assert not in_log
    assert not in_store
    assert f"the source refused the token {REDACTED}" in run_records.text()
    assert f"authentication failed with password {REDACTED}" in _reachable_text(caught.value)
    # The registry ended with the flow run.
    assert registered_secret_values() == ()
