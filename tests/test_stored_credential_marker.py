"""No output of the product holds the value of a stored credential (spec 027: SC-004, FR-002, FR-007).

One test. An administrator creates two credentials whose values are marker texts, registers a
package that names them, and a run plans and then applies. Every place a person or a tool reads
is then searched for the two markers:

- every answer of the Sync API, recorded on the API's own client, except the answer of the
  worker route, which is the one answer that holds a value (FR-005);
- the output of the CLI commands that create, list and show the credentials and the run;
- the log records of the whole logging tree, the structured log events, and the standard output
  and standard error of the test process;
- the run records, the audit events and the recorded uses of the credentials;
- a dump of the SQLite store, and the bytes of every file the Sync API, the worker and Prefect
  wrote, the write-ahead log of the store included.

The Sync API, its routes, the store, the encryption, the CLI, the worker flow, the worker's
client and the engine are the product's own. In the plan, both adapters are the product's own
and the two provider clients are faked, as in ``tests/runtime_schema/test_registered_execution.py``.
In the apply, the destination adapter is a recording one, as in
``tests/service/test_apply_checkpoint_order.py``: the faked Infrahub client has no write
surface. The recording adapter is built with the settings the worker resolved, so it holds the
released token. Each request of the worker and of the CLI reaches the in-process Sync API
through a transport, so no network call is possible. The flow runs against Prefect's test
harness.

The test is not empty: it checks by digest that both markers reached the provider clients, and
that the search finds a marker in the answer of the worker route.

No assertion shows a value. Each check collects the labels of the places that hold a marker and
compares that list with the empty list, or compares digests.
"""

from __future__ import annotations

import dataclasses
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
from structlog.testing import capture_logs
from typer.testing import CliRunner

from infrahub_sync.cli import app as cli_app
from infrahub_sync.client import SyncClient
from infrahub_sync.runtime_schema import RuntimeSideModels
from infrahub_sync.runtime_schema import worker as worker_module
from infrahub_sync.service import credential_client
from infrahub_sync.service import flow as service_flow
from infrahub_sync.service.auth import apply_worker_account
from infrahub_sync.service.flow import service_sync_run
from infrahub_sync.service.orchestration import Submission
from infrahub_sync.service.settings import WORKER_API_URL_ENV, WORKER_TOKEN_ENV
from tests.configuration.validation_packages import credential_package_data
from tests.helpers.stored_credentials import TEST_ENCRYPTION_KEY
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
from tests.service.execution_fixtures import bind_granting_guard

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from infrahub_sync.configuration import ConfigurationPackage
    from infrahub_sync.product_store import ProductProjection

API_URL = "http://sync-api.test:8000"
WORKER_TOKEN = "worker-token-of-the-marker-test-1"  # noqa: S105 - a test value, not a secret.
# The two values no output may hold. Test values only; they reach no system.
SOURCE_MARKER = "marker-source-3b9e61d07c5a4f28-stored-credential"
DESTINATION_MARKER = "marker-destination-a4c81f5e9d0b7263-stored-credential"
MARKERS = (SOURCE_MARKER, DESTINATION_MARKER)
SOURCE_CREDENTIAL = "netbox-marker"
DESTINATION_CREDENTIAL = "infrahub-marker"
OLD_VARIABLE_PREFIX = "INFRAHUB_SYNC_CREDENTIAL_"
# The one route whose answer holds a value: `POST /worker/runs/{run_id}/credentials`.
WORKER_ROUTE_PREFIX = "/worker/"
# The parameters of a `GET` route that this test has no value for: the test creates no schedule.
UNFILLED_PARAMETERS = {"schedule_id"}


def _digest(value: object) -> str:
    return hashlib.sha256(str(value).encode()).hexdigest()


def _holds_a_marker(content: str | bytes) -> bool:
    """Return whether the content holds a marker; a caller keeps the boolean and never the content."""
    if isinstance(content, bytes):
        return any(marker.encode() in content for marker in MARKERS)
    return any(marker in content for marker in MARKERS)


def _package() -> dict[str, Any]:
    """Return a NetBox to Infrahub package whose two blocks each name one of the marker credentials."""
    data = credential_package_data()
    data["configuration"]["schema_mapping"] = MAPPING
    for block, name in (("source", SOURCE_CREDENTIAL), ("destination", DESTINATION_CREDENTIAL)):
        data["configuration"][block]["credential"] = name
    return data


class _Answers:
    """Every answer of the in-process Sync API, as ``(label, whether it holds a marker)``.

    It is a response hook of the API's client, so it records the requests of the test, of the
    CLI and of the worker alike, and a route added later is recorded without a change here.
    """

    def __init__(self) -> None:
        self.seen: list[tuple[str, bool]] = []

    def __call__(self, response: httpx.Response) -> None:
        response.read()
        request = response.request
        self.seen.append(
            (f"{request.method} {request.url.path} -> {response.status_code}", _holds_a_marker(response.content))
        )

    def holding(self, *, worker_route: bool) -> list[str]:
        """Return the labels of the answers that hold a marker, of the worker route or of every other route."""
        return [
            label
            for label, holds in self.seen
            if holds and (label.split(" ")[1].startswith(WORKER_ROUTE_PREFIX) is worker_route)
        ]

    def routes(self) -> set[str]:
        return {label.split(" -> ")[0] for label, _holds in self.seen}


class _InProcessApi(httpx.BaseTransport):
    """Send a request of the worker or of the CLI to the in-process Sync API, served without the ``/api`` prefix."""

    def __init__(self, api: AccountApi) -> None:
        self._api = api

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        headers = {
            name: value
            for name, value in request.headers.items()
            if name.lower() in {"authorization", "content-type", "idempotency-key", "accept"}
        }
        answer = self._api.client.request(
            request.method,
            request.url.path.removeprefix("/api"),
            params=dict(request.url.params),
            headers=headers,
            content=request.read(),
        )
        return httpx.Response(
            answer.status_code,
            headers={"Content-Type": answer.headers.get("content-type", "application/json")},
            content=answer.content,
        )


class _Records(logging.Handler):
    """Keep every record of the logging tree, formatted with its arguments and its exception text."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.setFormatter(logging.Formatter("%(name)s %(levelname)s %(message)s"))
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))


class Product:
    """The Sync API, a worker and the CLI in one process, with every place the test searches."""

    def __init__(  # noqa: PLR0913, PLR0917 - each part of the product under test is given once.
        self,
        api: AccountApi,
        answers: _Answers,
        transport: _InProcessApi,
        submissions: dict[str, dict[str, Any]],
        clients: dict[str, list[str]],
        root: Path,
    ) -> None:
        self.api = api
        self.answers = answers
        self.transport = transport
        self.submissions = submissions
        self.clients = clients
        self.root = root
        self.cli_output: list[tuple[str, bool]] = []

    @property
    def store(self) -> ProductProjection:
        return self.api.store

    def request(self, method: str, path: str, *, key: str | None = None, body: dict[str, Any] | None = None) -> Any:  # noqa: ANN401 - a JSON document.
        """Send one request as ``admin`` and return the JSON answer of an accepted request."""
        headers = bearer(ADMIN_TOKEN) if key is None else {**bearer(ADMIN_TOKEN), "Idempotency-Key": key}
        answer = self.api.client.request(method, path, headers=headers, json=body)
        code = answer.json().get("error", {}).get("code") if answer.status_code >= 400 else None
        assert answer.status_code < 400, f"{method} {path} was refused: {answer.status_code} {code}"
        return answer.json()

    def cli(self, *arguments: str, stdin: str | None = None) -> None:
        """Run one CLI command as ``admin`` and keep whether its output holds a marker."""
        root_logger = logging.getLogger()
        handlers = list(root_logger.handlers)
        result = CliRunner().invoke(cli_app, list(arguments), input=stdin)
        # A command adds a handler that writes to the runner's standard error, which is closed
        # when the command returns. The log records of the command are in the test's own handler.
        for handler in [handler for handler in root_logger.handlers if handler not in handlers]:
            root_logger.removeHandler(handler)
        label = "infrahub-sync " + " ".join(arguments)
        failure = type(result.exception).__name__ if result.exception is not None else None
        assert result.exit_code == 0, f"`{label}` ended with code {result.exit_code} ({failure})"
        held = _holds_a_marker(result.output) or _holds_a_marker(result.stdout) or _holds_a_marker(result.stderr)
        self.cli_output.append((label, held))

    def execute(self, run_id: str, monkeypatch: pytest.MonkeyPatch) -> Any:  # noqa: ANN401 - a Prefect state.
        """Run the stage the Sync API last submitted for the run, as a worker, and return the final state.

        The worker flow gets the parameters the Sync API submitted to the orchestration, unchanged.
        """
        worker_id = str(uuid4())
        stored = self.store.lookup_run(run_id).value
        assert stored is not None
        flow_run_id = stored.prefect_executions[-1].flow_run_id

        def claim(projection: ProductProjection, claimed_run_id: str) -> tuple[str, str]:
            assert projection.claim_execution(claimed_run_id, flow_run_id, worker_id=worker_id)
            return flow_run_id, worker_id

        monkeypatch.setattr(service_flow, "_claim_current_execution", claim)
        return service_sync_run(**self.submissions[flow_run_id], return_state=True)

    def read_every_route(self, run_id: str, version: dict[str, Any]) -> list[str]:
        """Send a ``GET`` to every route of the Sync API that reads, and return the routes left out.

        The routes come from the OpenAPI document of the application, so a route added later is
        read without a change here. A route with a parameter this test has no value for is left
        out and returned.
        """
        artifacts = self.request("GET", f"/runs/{run_id}/artifacts")
        values: dict[str, list[Any]] = {
            "run_id": [run_id],
            "config_id": [version["config_id"]],
            "registry_version": [version["registry_version"]],
            "artifact_id": sorted(_values_of(artifacts, "artifact_id")),
        }
        names_of = {"credentials": [SOURCE_CREDENTIAL, DESTINATION_CREDENTIAL], "accounts": ["admin"]}
        left_out: list[str] = []
        document = cast("Any", self.api.client.app).openapi()
        for path, operations in sorted(document["paths"].items()):
            if "get" not in operations:
                continue
            names = _parameters(path)
            values["name"] = names_of.get(path.split("/")[1], [])
            if any(not values.get(name) for name in names):
                left_out.append(path)
                continue
            for index in range(max((len(values[name]) for name in names), default=1)):
                filled = path
                for name in names:
                    filled = filled.replace(f"{{{name}}}", str(values[name][min(index, len(values[name]) - 1)]))
                # The answer is recorded by the response hook; a refusal is an answer too.
                self.api.client.get(filled, headers=bearer(ADMIN_TOKEN))
        # The documents the application serves about itself.
        for path in ("/openapi.json", "/docs", "/redoc"):
            self.api.client.get(path)
        return left_out

    def store_dump(self) -> str:
        """Return the SQL text of every row of the SQLite store."""
        databases = sorted(self.root.rglob("*.sqlite3"))
        assert len(databases) == 1, [path.name for path in databases]
        with sqlite3.connect(databases[0]) as connection:
            return "\n".join(connection.iterdump())

    def files_holding_a_marker(self) -> tuple[int, list[str]]:
        """Return the number of files under the test's directory, and those whose bytes hold a marker."""
        files = sorted(path for path in self.root.rglob("*") if path.is_file())
        return len(files), [str(path.relative_to(self.root)) for path in files if _holds_a_marker(path.read_bytes())]


def _sign_the_cli_in(monkeypatch: pytest.MonkeyPatch, api: AccountApi, tmp_path: Path) -> None:
    """Make the CLI send its requests to the in-process Sync API, signed in as ``admin`` with the API token.

    Its session file is in the test's directory, so no command reads or writes the user's own.
    """

    class _Client(SyncClient):
        def __init__(
            self,
            service_url: str,
            token: str,
            *,
            timeout: float = 30.0,
            transport: httpx.BaseTransport | None = None,
        ) -> None:
            del transport
            super().__init__(service_url, token, timeout=timeout, transport=_InProcessApi(api))

    monkeypatch.setattr("infrahub_sync.cli.SyncClient", _Client)
    monkeypatch.setattr("infrahub_sync.cli_session.default_directory", lambda: tmp_path / "cli-session")
    monkeypatch.setenv("INFRAHUB_SYNC_API_URL", API_URL)
    monkeypatch.setenv("INFRAHUB_SYNC_API_TOKEN", ADMIN_TOKEN)


@pytest.fixture
def product(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Product]:
    # FR-006: the worker of this test holds no credential variable.
    for name in [name for name in os.environ if name.startswith(OLD_VARIABLE_PREFIX)]:
        monkeypatch.delenv(name)
    flow_runs = iter(range(1_000))
    submissions: dict[str, dict[str, Any]] = {}

    async def submit(  # noqa: RUF029 - it replaces an asynchronous method.
        _self: object, parameters: dict[str, object], *, idempotency_key: str
    ) -> Submission:
        del idempotency_key
        flow_run_id = f"flow-run-{next(flow_runs)}"
        submissions[flow_run_id] = dict(parameters)
        return Submission(flow_run_id=flow_run_id, state="pending")

    monkeypatch.setattr(account_api.Orchestration, "submit", submit)
    api = build_account_api(tmp_path / "api", monkeypatch, credential_key=TEST_ENCRYPTION_KEY)
    apply_worker_account(api.store, WORKER_TOKEN)
    answers = _Answers()
    api.client.event_hooks["response"].append(answers)
    transport = _InProcessApi(api)

    # The two provider clients; each records the digest of the token it was built with.
    clients: dict[str, list[str]] = {
        "netbox_tokens": [],
        "infrahub_tokens": [],
        "apply_tokens": [],
        "applied_operations": [],
    }

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

    class _RecordingDestination:
        """The destination adapter of the apply: it records the token of its settings and each write."""

        def __init__(self, **kwargs: Any) -> None:  # noqa: ANN401 - the arguments of an adapter.
            clients["apply_tokens"].append(_digest((kwargs["adapter"].settings or {}).get("token")))

        def new_peer_resolver(self) -> object:  # noqa: PLR6301 - the adapter's own method.
            return object()

        def apply_planned_operation(self, *, operation: Any, peers: Any) -> str:  # noqa: ANN401, PLR6301
            del peers
            clients["applied_operations"].append(str(operation.operation_id))
            return f"node-{len(clients['applied_operations'])}"

    build_models = service_flow.build_runtime_model_plan

    def models_of_the_stage(**kwargs: Any) -> Any:  # noqa: ANN401 - the arguments of the product's function.
        models = build_models(**kwargs)
        if models.source is not None:
            return models
        # A stage that builds the destination only is the apply.
        destination = RuntimeSideModels(adapter_class=_RecordingDestination, models={})
        return dataclasses.replace(models, destination=destination)

    monkeypatch.setattr(service_flow, "build_runtime_model_plan", models_of_the_stage)
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path / "runs"))

    # What a worker holds to reach the Sync API, and nothing else.
    monkeypatch.setenv(WORKER_API_URL_ENV, API_URL)
    monkeypatch.setenv(WORKER_TOKEN_ENV, WORKER_TOKEN)
    monkeypatch.setattr(
        service_flow,
        "release_run_credentials",
        functools.partial(credential_client.release_run_credentials, transport=transport),
    )
    monkeypatch.setattr(service_flow, "_runtime", lambda: api.store)
    monkeypatch.setattr(service_flow, "create_markdown_artifact", lambda **_kwargs: None)
    # The unit tier has no PostgreSQL session for the write guard of an apply.
    bind_granting_guard(monkeypatch, service_flow)

    _sign_the_cli_in(monkeypatch, api, tmp_path)

    # Prefect's own files are under the test's directory, so the search of the files covers them.
    home = tmp_path / "prefect-home"
    monkeypatch.setenv("PREFECT_HOME", str(home))
    monkeypatch.setenv("PREFECT_LOCAL_STORAGE_PATH", str(home / "storage"))
    with prefect_test_harness():
        yield Product(api, answers, transport, submissions, clients, tmp_path)
    _forget_netbox_adapter()


def test_no_output_holds_the_value_of_a_stored_credential(  # noqa: PLR0914, PLR0915 - one scenario, searched in every place.
    product: Product, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    records = _Records()
    root_logger = logging.getLogger()
    previous_level = root_logger.level
    root_logger.addHandler(records)
    root_logger.setLevel(logging.DEBUG)
    try:
        with capture_logs() as events:
            # An administrator creates one credential with the CLI and one through the API.
            product.cli("credentials", "create", SOURCE_CREDENTIAL, "--token-stdin", stdin=SOURCE_MARKER + "\n")
            product.request(
                "POST",
                "/credentials",
                key="marker-create-destination",
                body={"name": DESTINATION_CREDENTIAL, "token": DESTINATION_MARKER, "description": "marker test"},
            )
            version = product.request(
                "POST", "/configs", key="marker-register", body={"package": _package(), "reason": "register"}
            )["version"]

            # A plan, which reads both systems.
            run_id = product.request(
                "POST",
                "/runs",
                key="marker-plan",
                body={
                    "operation": "plan",
                    "config_id": version["config_id"],
                    "registry_version": version["registry_version"],
                    "reason": "marker test: plan",
                },
            )["run"]["run_id"]
            planned = product.execute(run_id, monkeypatch)
            assert planned.is_completed(), planned.type
            plan = product.request("GET", f"/runs/{run_id}/plan")

            # An apply of the reviewed plan, which writes to the destination.
            product.request(
                "POST",
                f"/runs/{run_id}/apply",
                key="marker-apply",
                body={"expected_checksum": plan["checksum"], "confirm_writes": True, "reason": "marker test: apply"},
            )
            applied = product.execute(run_id, monkeypatch)
            assert applied.is_completed(), applied.type

            # Everything a person reads afterwards: every route that reads, and the CLI.
            left_out = product.read_every_route(run_id, version)
            for arguments in (
                ("credentials", "list"),
                ("credentials", "list", "--json"),
                ("credentials", "show", SOURCE_CREDENTIAL),
                ("credentials", "show", DESTINATION_CREDENTIAL, "--json"),
                ("configs", "list"),
                ("runs", "list"),
                ("runs", "show", run_id),
                ("runs", "plan", run_id),
                ("runs", "results", run_id),
                ("runs", "artifacts", run_id),
            ):
                product.cli(*arguments)
    finally:
        root_logger.removeHandler(records)
        root_logger.setLevel(previous_level)

    stored = product.store.lookup_run(run_id).value
    assert stored is not None
    assert stored.phase == "applied"

    # The test is not empty: both values reached the provider clients, in the plan and in the apply.
    uses = [(use.stage, use.block, use.credential_name) for use in product.store.credential_uses(run_id)]
    assert uses == [
        ("plan", "destination", DESTINATION_CREDENTIAL),
        ("plan", "source", SOURCE_CREDENTIAL),
        ("apply", "destination", DESTINATION_CREDENTIAL),
    ]
    assert set(product.clients["netbox_tokens"]) == {_digest(SOURCE_MARKER)}
    assert set(product.clients["infrahub_tokens"]) == {_digest(DESTINATION_MARKER)}
    assert product.clients["apply_tokens"] == [_digest(DESTINATION_MARKER)]
    assert len(product.clients["applied_operations"]) == len(plan["operations"]) > 0
    # And the search finds a value where one is: the two answers of the worker route.
    worker_answers = product.answers.holding(worker_route=True)
    assert worker_answers == [f"POST /worker/runs/{run_id}/credentials -> 200"] * 2

    # 1. Every answer of the Sync API except the worker route.
    assert product.answers.holding(worker_route=False) == []
    read = product.answers.routes()
    for expected in (
        "POST /credentials",
        "GET /credentials",
        f"GET /credentials/{SOURCE_CREDENTIAL}",
        "POST /configs",
        "POST /runs",
        f"POST /runs/{run_id}/apply",
        f"GET /runs/{run_id}",
        f"GET /runs/{run_id}/plan",
        f"GET /runs/{run_id}/results",
        f"GET /runs/{run_id}/logs",
        "GET /events",
    ):
        assert expected in read, f"no answer of `{expected}` was searched"
    unexpected = sorted(path for path in left_out if not UNFILLED_PARAMETERS & set(_parameters(path)))
    assert unexpected == [], f"these routes that read were not searched: {unexpected}"

    # 2. The output of the CLI.
    assert [label for label, held in product.cli_output if held] == []
    assert len(product.cli_output) == 11

    # 3. The logs, the structured log events, and the standard output and error of the process.
    assert [number for number, line in enumerate(records.lines) if _holds_a_marker(line)] == []
    assert records.lines, "no log record was captured"
    assert [event.get("event") for event in events if _holds_a_marker(json.dumps(event, default=str))] == []
    captured = capsys.readouterr()
    assert [name for name, text in (("stdout", captured.out), ("stderr", captured.err)) if _holds_a_marker(text)] == []

    # 4. The run records.
    run_records = {
        "run": json.dumps(stored.model_dump(mode="json")),
        "audit events": product.api.stored_text(),
        "credential uses": repr(product.store.credential_uses(run_id)),
        "configuration version": json.dumps(version),
    }
    assert [name for name, text in run_records.items() if _holds_a_marker(text)] == []

    # 5. The SQLite store, and every file the Sync API, the worker and Prefect wrote.
    dump = product.store_dump()
    assert SOURCE_CREDENTIAL in dump, "the dump does not hold the stored credentials"
    dump_holds_a_marker = _holds_a_marker(dump)
    assert not dump_holds_a_marker
    count, holding = product.files_holding_a_marker()
    assert holding == []
    assert count > 0


def _values_of(document: Any, key: str) -> set[str]:  # noqa: ANN401 - a JSON document.
    """Return every text value of `key` in a JSON document, at any depth."""
    found: set[str] = set()
    if isinstance(document, dict):
        value = document.get(key)
        if isinstance(value, str):
            found.add(value)
        for nested in document.values():
            found |= _values_of(nested, key)
    elif isinstance(document, list):
        for item in document:
            found |= _values_of(item, key)
    return found


def _parameters(path: str) -> list[str]:
    """Return the names of the parameters of a route path."""
    return [part[1:-1] for part in path.split("/") if part.startswith("{") and part.endswith("}")]
