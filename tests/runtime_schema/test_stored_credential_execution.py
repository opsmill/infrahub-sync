"""A worker with no credential variable executes a package that names stored credentials (spec 027).

FR-006 and SC-003 at unit level: two workers, with no ``INFRAHUB_SYNC_CREDENTIAL_*`` variable in
their environment, each complete the plan of a run. Each one gets the values of its run from the
worker route of the Sync API, through ``credential_client.release_run_credentials``, once for
its stage execution.

Critique E6: a value is in the redaction registry of the run from the moment of its release. An
error that holds a released value, raised after the release and before the stage collects the
secret values of its configuration, reaches no log, no failure cause and no run record.

The flow, the worker route, the product store, the engine and both adapters are the product's
own. The two provider clients are faked, as in ``test_registered_execution``, and the worker's
request reaches the in-process API through a transport, so no network call is possible. No
Prefect server runs: the flow runs against Prefect's test harness.

Every value is a marker text. A check compares digests or a boolean, so a failed check prints no
value.
"""

from __future__ import annotations

import functools
import hashlib
import json
import logging
import os
import sqlite3
import sys
import types
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

import httpx
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("prefect")
pytest.importorskip("cryptography")
pytest.importorskip("opsmill_prefect_extras")
# The Sync service and its worker flow need the `service` extra, which Python 3.10 does not install.
pytest.importorskip("psycopg")

from prefect.testing.utilities import prefect_test_harness

from infrahub_sync.execution import REDACTED, registered_secret_values
from infrahub_sync.runtime_schema import worker as worker_module
from infrahub_sync.service import credential_client
from infrahub_sync.service import flow as service_flow
from infrahub_sync.service.auth import apply_worker_account
from infrahub_sync.service.flow import service_sync_run
from infrahub_sync.service.orchestration import Submission
from infrahub_sync.service.settings import WORKER_API_URL_ENV, WORKER_TOKEN_ENV
from tests.configuration.validation_packages import credential_package_data
from tests.helpers.stored_credentials import TEST_ENCRYPTION_KEY, create_stored_credential
from tests.runtime_schema.test_registered_execution import (
    MAPPING,
    NETBOX_TAGS,
    SNAPSHOT,
    _forget_netbox_adapter,
    _InfrahubClient,
    _NetboxEndpoint,
)
from tests.service import account_api
from tests.service.account_api import ADMIN_TOKEN, AccountApi, bearer, build_account_api

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from infrahub_sync.configuration import ConfigurationPackage
    from infrahub_sync.product_store import ProductProjection

WORKER_TOKEN = "worker-token-of-the-environment-2"  # noqa: S105 - a test value, not a secret.
SOURCE_TOKEN = "execution-marker-source-token-7c2d"  # noqa: S105 - a test value, not a secret.
DESTINATION_TOKEN = "execution-marker-destination-token-b41f"  # noqa: S105 - a test value, not a secret.
MARKERS = (SOURCE_TOKEN, DESTINATION_TOKEN)
SOURCE_CREDENTIAL = "netbox-lab"
DESTINATION_CREDENTIAL = "infrahub-main"
RELEASED = "infrahub_sync.credential.released"
OLD_VARIABLE_PREFIX = "INFRAHUB_SYNC_CREDENTIAL_"


def _digest(value: object) -> str:
    return hashlib.sha256(str(value).encode()).hexdigest()


def _holds_a_value(text: str) -> bool:
    """Return whether the text holds a marker value; the caller asserts the boolean."""
    return any(marker in text for marker in MARKERS)


def _reachable_text(error: BaseException) -> str:
    """Return the text of an error and of every error its chain reaches."""
    seen: list[str] = []
    link: BaseException | None = error
    while link is not None and len(seen) < 20:
        seen.append(f"{type(link).__name__}: {link}")
        link = link.__cause__ or link.__context__
    return "\n".join(seen)


def _package() -> dict[str, Any]:
    """Return a NetBox to Infrahub package whose two blocks each name a stored credential."""
    data = credential_package_data()
    data["configuration"]["schema_mapping"] = MAPPING
    for block, name in (("source", SOURCE_CREDENTIAL), ("destination", DESTINATION_CREDENTIAL)):
        data["configuration"][block]["credential"] = name
    return data


class _InProcessApi(httpx.BaseTransport):
    """Send the worker's request to the in-process Sync API, which is served without the ``/api`` prefix."""

    def __init__(self, api: AccountApi) -> None:
        self._api = api
        self.requests: list[str] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/api")
        self.requests.append(path)
        answer = self._api.client.request(
            request.method,
            path,
            headers={"Authorization": request.headers["Authorization"], "Content-Type": "application/json"},
            content=request.read(),
        )
        return httpx.Response(answer.status_code, headers={"Content-Type": "application/json"}, content=answer.content)


class _Records(logging.Handler):
    """Keep every record a logger emits, formatted as a handler of the worker would write it."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))

    def text(self) -> str:
        return "\n".join(self.lines)


class Workers:
    """The Sync API with its stored credentials, and the steps of a worker that executes a run."""

    def __init__(
        self, api: AccountApi, transport: _InProcessApi, clients: dict[str, list[Any]], database: Path
    ) -> None:
        self.api = api
        self._database = database
        self.transport = transport
        self.clients = clients
        self._runs = 0
        registered = api.client.post(
            "/configs",
            headers={**bearer(ADMIN_TOKEN), "Idempotency-Key": "register-for-execution"},
            json={"package": _package(), "reason": "register"},
        )
        assert registered.status_code == 201, registered.json()["error"]["code"]
        self.version: dict[str, Any] = registered.json()["version"]

    @property
    def store(self) -> ProductProjection:
        return self.api.store

    def binding(self) -> tuple[str, int, str]:
        return self.version["config_id"], self.version["registry_version"], self.version["package_checksum"]

    def run(self) -> str:
        """Create a plan run through the API, as a person does, and return its identifier."""
        self._runs += 1
        created = self.api.client.post(
            "/runs",
            headers={**bearer(ADMIN_TOKEN), "Idempotency-Key": f"execution-run-{self._runs}"},
            json={
                "operation": "plan",
                "config_id": self.version["config_id"],
                "registry_version": self.version["registry_version"],
            },
        )
        assert created.status_code in {200, 201, 202}, created.json()["error"]["code"]
        return created.json()["run"]["run_id"]

    def execute_plan(self, run_id: str, monkeypatch: pytest.MonkeyPatch) -> Any:  # noqa: ANN401 - a Prefect state.
        """Run the plan stage of the run as one worker with its own identifier, and return the final state."""
        worker_id = str(uuid4())

        def claim(projection: ProductProjection, claimed_run_id: str) -> tuple[str, str]:
            stored = projection.lookup_run(claimed_run_id).value
            assert stored is not None
            flow_run_id = stored.prefect_executions[-1].flow_run_id
            assert projection.claim_execution(claimed_run_id, flow_run_id, worker_id=worker_id)
            return flow_run_id, worker_id

        monkeypatch.setattr(service_flow, "_claim_current_execution", claim)
        return service_sync_run(run_id, "plan", *self.binding(), return_state=True)  # ty: ignore[no-matching-overload] - TODO: ty cannot select prefect's ParamSpec return_state overload

    def delete_credential(self, name: str) -> None:
        """Remove a credential and its revisions; the deletion route belongs to a later user story."""
        databases = sorted(self._database.rglob("*.sqlite3"))
        assert len(databases) == 1
        with sqlite3.connect(databases[0]) as connection:
            connection.execute(
                "DELETE FROM stored_credential_revisions WHERE credential_id IN "
                "(SELECT credential_id FROM stored_credentials WHERE name = ?)",
                (name,),
            )
            connection.execute("DELETE FROM stored_credentials WHERE name = ?", (name,))

    def released_events(self, run_id: str) -> int:
        return len([event for event in self.api.events() if event.event_type == RELEASED and event.run_id == run_id])


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
    """The records of ``prefect.flow_runs``: the run log, which the log bridge writes the worker's lines to."""
    handler = _Records()
    run_logger = logging.getLogger("prefect.flow_runs")
    run_logger.addHandler(handler)
    try:
        yield handler
    finally:
        run_logger.removeHandler(handler)


@pytest.fixture
def workers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    prefect_harness: None,
) -> Iterator[Workers]:
    del prefect_harness
    # FR-006: no worker of this test holds a credential variable.
    for name in [name for name in os.environ if name.startswith(OLD_VARIABLE_PREFIX)]:
        monkeypatch.delenv(name)
    flow_runs = iter(range(1_000))

    async def submit(  # noqa: RUF029 - it replaces an asynchronous method.
        _self: object, parameters: dict[str, object], *, idempotency_key: str
    ) -> Submission:
        del parameters, idempotency_key
        return Submission(flow_run_id=f"flow-run-{next(flow_runs)}", state="pending")

    monkeypatch.setattr(account_api.Orchestration, "submit", submit)
    api = build_account_api(tmp_path / "api", monkeypatch, credential_key=TEST_ENCRYPTION_KEY)
    apply_worker_account(api.store, WORKER_TOKEN)
    create_stored_credential(api.store, SOURCE_CREDENTIAL, {"token": SOURCE_TOKEN})
    create_stored_credential(api.store, DESTINATION_CREDENTIAL, {"token": DESTINATION_TOKEN})

    # The two provider clients; each records the token it was built with.
    clients: dict[str, list[Any]] = {"netbox_tokens": [], "infrahub_tokens": []}

    def netbox_api(url: str, token: str) -> types.SimpleNamespace:
        del url
        clients["netbox_tokens"].append(_digest(token))
        return types.SimpleNamespace(extras=types.SimpleNamespace(tags=_NetboxEndpoint(NETBOX_TAGS)))

    def infrahub_client(address: str, config: Any) -> _InfrahubClient:  # noqa: ANN401 - the SDK's Config.
        clients["infrahub_tokens"].append(_digest(config.api_token))
        return _InfrahubClient(address, config)

    driver = cast("Any", types.ModuleType("pynetbox"))
    driver.api = netbox_api
    monkeypatch.setitem(sys.modules, "pynetbox", driver)
    _forget_netbox_adapter()
    monkeypatch.setattr("infrahub_sync.adapters.infrahub.InfrahubClientSync", infrahub_client)

    def read_schema(_package: ConfigurationPackage, _branch: str) -> dict[str, Any]:
        return SNAPSHOT

    monkeypatch.setattr(worker_module, "read_destination_schema_snapshot", read_schema)
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path / "runs"))

    # What a worker holds to reach the Sync API, and nothing else.
    monkeypatch.setenv(WORKER_API_URL_ENV, "http://sync-api.test:8000")
    monkeypatch.setenv(WORKER_TOKEN_ENV, WORKER_TOKEN)
    transport = _InProcessApi(api)
    monkeypatch.setattr(
        service_flow,
        "release_run_credentials",
        functools.partial(credential_client.release_run_credentials, transport=transport),
    )
    monkeypatch.setattr(service_flow, "_runtime", lambda: api.store)
    monkeypatch.setattr(service_flow, "create_markdown_artifact", lambda **_kwargs: None)
    yield Workers(api, transport, clients, tmp_path / "api")
    _forget_netbox_adapter()


def test_two_workers_without_a_credential_variable_complete_plans(
    workers: Workers, monkeypatch: pytest.MonkeyPatch
) -> None:
    held = [name for name in os.environ if name.startswith(OLD_VARIABLE_PREFIX)]
    assert held == []
    first, second = workers.run(), workers.run()

    states = [workers.execute_plan(run_id, monkeypatch) for run_id in (first, second)]

    for run_id, state in zip((first, second), states, strict=True):
        assert state.is_completed(), state.type
        stored = workers.store.lookup_run(run_id).value
        assert stored is not None
        assert stored.phase == "planned"
        # Each worker got both blocks, once for its stage execution (FR-016).
        assert [
            (use.stage, use.block, use.credential_name, use.revision) for use in workers.store.credential_uses(run_id)
        ] == [
            ("plan", "destination", DESTINATION_CREDENTIAL, 1),
            ("plan", "source", SOURCE_CREDENTIAL, 1),
        ]
        assert workers.released_events(run_id) == 1
    assert workers.transport.requests == [f"/worker/runs/{run_id}/credentials" for run_id in (first, second)]
    # Both adapters of both workers were built with the released values.
    assert set(workers.clients["netbox_tokens"]) == {_digest(SOURCE_TOKEN)}
    assert set(workers.clients["infrahub_tokens"]) == {_digest(DESTINATION_TOKEN)}
    assert len(workers.clients["netbox_tokens"]) == 2


def test_no_run_record_holds_a_released_value(workers: Workers, monkeypatch: pytest.MonkeyPatch) -> None:
    run_id = workers.run()

    state = workers.execute_plan(run_id, monkeypatch)

    assert state.is_completed(), state.type
    stored = workers.store.lookup_run(run_id).value
    assert stored is not None
    in_run = _holds_a_value(json.dumps(stored.model_dump(mode="json")))
    in_events = _holds_a_value(workers.api.stored_text())
    assert not in_run
    assert not in_events


def test_an_error_raised_after_the_release_and_before_the_stage_collects_its_secrets_is_redacted(
    workers: Workers, monkeypatch: pytest.MonkeyPatch, run_records: _Records
) -> None:
    run_id = workers.run()
    seen: dict[str, bool] = {}

    def failing_schema_read(_package: ConfigurationPackage, _branch: str) -> dict[str, Any]:
        # The stage released its values and has not yet collected the secrets of its configuration.
        seen["registered_at_the_release"] = set(MARKERS) <= set(registered_secret_values())
        logging.getLogger("infrahub_sync.adapters.test").error(
            "the destination refused the token %s", DESTINATION_TOKEN
        )
        msg = f"the schema read failed with the token {DESTINATION_TOKEN}"
        raise RuntimeError(msg) from ValueError(f"the source token {SOURCE_TOKEN} was read too")

    monkeypatch.setattr(worker_module, "read_destination_schema_snapshot", failing_schema_read)

    state = workers.execute_plan(run_id, monkeypatch)

    assert seen == {"registered_at_the_release": True}
    assert state.is_failed()
    with pytest.raises(RuntimeError) as caught:
        state.result()
    stored = workers.store.lookup_run(run_id).value
    assert stored is not None
    in_cause = _holds_a_value(_reachable_text(caught.value))
    in_state = _holds_a_value(str(state.message))
    in_log = _holds_a_value(run_records.text())
    in_run = _holds_a_value(json.dumps(stored.model_dump(mode="json")))
    in_events = _holds_a_value(workers.api.stored_text())
    assert not in_cause
    assert not in_state
    assert not in_log
    assert not in_run
    assert not in_events
    assert f"the destination refused the token {REDACTED}" in run_records.text()
    assert f"the schema read failed with the token {REDACTED}" in _reachable_text(caught.value)
    # The registry ended with the flow run.
    assert registered_secret_values() == ()


def test_a_run_whose_credential_was_deleted_fails_before_it_reads_the_source(
    workers: Workers, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = workers.run()
    # Deleted after the run was accepted, so only the worker route can refuse.
    workers.delete_credential(SOURCE_CREDENTIAL)

    state = workers.execute_plan(run_id, monkeypatch)

    assert state.is_failed()
    stored = workers.store.lookup_run(run_id).value
    assert stored is not None
    failure = stored.results["plan_failure"]
    assert failure["failure_type"] == "credential_unavailable"
    assert f"infrahub-sync credentials create {SOURCE_CREDENTIAL}" in failure["next_action"]
    assert "administrator" in failure["next_action"]
    assert SOURCE_CREDENTIAL in failure["cause"]
    # No adapter was built, so the source was not read (FR-019).
    assert workers.clients["netbox_tokens"] == []
