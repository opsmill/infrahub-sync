"""No Sync message points the operator at Prefect, and Sync deletes no flow run (spec 017, FR-006, FR-011).

FR-006: a failed run (missing branch), an expired log and an unreachable orchestrator are served
through the Sync API and printed by `runs show`, `runs logs` and `events`. No response message,
no printed line and no served log line holds the Prefect API address, "open Prefect",
"in Prefect" or "Prefect flow run", and no API `message` value holds the word "Prefect". Field names such as `prefect_executions` and the configured
`prefect_flow_run_url` value are allowed (spec FR-006).

FR-011: no module under `infrahub_sync/` calls a Prefect flow run delete.
"""

from __future__ import annotations

import ast
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx
import pytest

pytest.importorskip("prefect")
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient
from prefect.exceptions import ObjectNotFound
from prefect.settings import get_current_settings
from prefect.testing.utilities import prefect_test_harness
from typer.testing import CliRunner

from infrahub_sync.cli import app as cli_app
from infrahub_sync.client import SyncClient
from infrahub_sync.product_store import PrefectExecutionLink, ProductRun, local_product_projection
from infrahub_sync.runtime_schema.errors import DestinationBranchNotFoundError
from infrahub_sync.service import flow as service_flow
from infrahub_sync.service.app import create_app
from infrahub_sync.service.auth import PRINCIPALS_ENV, EnvironmentPrincipalResolver
from infrahub_sync.service.flow import service_sync_run
from infrahub_sync.service.orchestration import PrefectOrchestration
from infrahub_sync.service.service import RunService
from tests.service.execution_fixtures import append_execution

if TYPE_CHECKING:
    from collections.abc import Iterator

    from infrahub_sync.product_store import ProductProjection

OWNER_TOKEN = "wording-owner-token-canary-0001"  # noqa: S105 - deliberate non-secret boundary canary.
RUN_ID = "run-wording-nobranch"
WORKER_ID = "8c1da53d-0e6b-4d3d-a0f1-97b6a9ccebf0"
# The address an unreachable orchestrator's transport error names.
UNREACHABLE_ADDRESS = "prefect-canary.invalid:4299"
FORBIDDEN_PHRASES = ("open prefect", "in prefect", "prefect flow run")
RUNNER = CliRunner()
REPOSITORY_SOURCE = Path(__file__).resolve().parents[2] / "infrahub_sync"


@pytest.fixture(scope="module")
def prefect_api_address(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """Run flows against a throwaway Prefect backend and yield its API host and port."""
    home = tmp_path_factory.mktemp("prefect-home")
    with pytest.MonkeyPatch.context() as patcher:
        patcher.setenv("PREFECT_HOME", str(home))
        patcher.setenv("PREFECT_LOCAL_STORAGE_PATH", str(home / "storage"))
        with prefect_test_harness():
            url = get_current_settings().api.url
            yield urlsplit(url).netloc if url else ""


class _Records(logging.Handler):
    """Keep every record the flow run logger writes."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


class _PrefectLogClient:
    """Answer the log reader's two Prefect calls with the flow's own lines, or fail as `mode` says."""

    def __init__(self, flow_run_id: str, records: list[logging.LogRecord]) -> None:
        self.mode = "read"
        self._entries = [
            SimpleNamespace(
                id=f"log-{index}",
                flow_run_id=flow_run_id,
                timestamp=datetime.fromtimestamp(record.created, tz=timezone.utc),
                level=record.levelno,
                message=record.getMessage(),
            )
            for index, record in enumerate(records)
        ]

    def _fail(self) -> None:
        request = httpx.Request("GET", f"http://{UNREACHABLE_ADDRESS}/api/flow_runs")
        if self.mode == "expired":
            raise ObjectNotFound(http_exc=Exception(f"404 from http://{UNREACHABLE_ADDRESS}/api"))
        if self.mode == "unavailable":
            msg = f"All connection attempts failed for http://{UNREACHABLE_ADDRESS}/api"
            raise httpx.ConnectError(msg, request=request)

    async def read_flow_run(self, flow_run_id: UUID) -> SimpleNamespace:
        self._fail()
        return SimpleNamespace(id=flow_run_id, state=None)

    async def read_logs(self, log_filter: object = None, **_kwargs: object) -> list[Any]:
        del log_filter
        self._fail()
        return list(self._entries)


def _failed_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[ProductProjection, str, list[logging.LogRecord]]:
    """Run the flow on a claimed plan whose destination branch is missing; return the flow's lines."""
    projection = local_product_projection(tmp_path)
    projection.create_run(
        ProductRun(
            run_id=RUN_ID,
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
    flow_run_id = str(uuid5(NAMESPACE_URL, RUN_ID))
    append_execution(projection, RUN_ID, PrefectExecutionLink(flow_run_id=flow_run_id, purpose="plan", attempt=1))
    assert projection.claim_execution(RUN_ID, flow_run_id, worker_id=WORKER_ID)
    monkeypatch.setattr(service_flow, "_runtime", lambda: (None, projection))
    monkeypatch.setattr(service_flow, "_claim_current_execution", lambda _projection, _run_id: (flow_run_id, WORKER_ID))

    def fail(*_args: object, **_kwargs: object) -> None:
        raise DestinationBranchNotFoundError(branch="demo-test-017")

    monkeypatch.setattr(service_flow, "_execute_stage", fail)
    monkeypatch.setattr(service_flow, "create_markdown_artifact", lambda **_kwargs: None)
    handler = _Records()
    run_logger = logging.getLogger("prefect.flow_runs")
    run_logger.addHandler(handler)
    try:
        state = service_sync_run(RUN_ID, "plan", "config-001", 1, "a" * 64, return_state=True)  # ty: ignore[no-matching-overload] - TODO: ty cannot select prefect's ParamSpec return_state overload
    finally:
        run_logger.removeHandler(handler)
    assert state.is_failed()
    return projection, flow_run_id, handler.records


def _forbidden(text: str, addresses: tuple[str, ...]) -> list[str]:
    """Return each forbidden phrase or Prefect address that `text` holds."""
    lowered = text.lower()
    found = [phrase for phrase in FORBIDDEN_PHRASES if phrase in lowered]
    found.extend(address for address in addresses if address and address.lower() in lowered)
    return found


def _messages(payload: object) -> list[str]:
    """Every `message` value in a response body, at any depth."""
    if isinstance(payload, dict):
        found = [value for key, value in payload.items() if key == "message" and isinstance(value, str)]
        for value in payload.values():
            found.extend(_messages(value))
        return found
    if isinstance(payload, list):
        return [message for item in payload for message in _messages(item)]
    return []


def _api_messages_naming_prefect(responses: dict[str, httpx.Response]) -> list[str]:
    """Every API `message` value that holds the word "Prefect", in any case.

    Field names and the `prefect_flow_run_url` value are not `message` values. The served log
    lines of `/runs/{id}/logs` are Sync's own lines and are checked against the phrases only,
    so only that route's error message is read here.
    """
    return [
        f"{path} message {message!r}"
        for path, response in responses.items()
        for message in _messages(response.json().get("error", {}) if path.endswith("/logs") else response.json())
        if "prefect" in message.lower()
    ]


@pytest.mark.parametrize("mode", ["read", "expired", "unavailable"])
def test_no_message_line_or_output_points_at_prefect(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, prefect_api_address: str, mode: str
) -> None:
    projection, flow_run_id, records = _failed_run(monkeypatch, tmp_path)
    monkeypatch.setenv(PRINCIPALS_ENV, json.dumps({"owner": {"token": OWNER_TOKEN}}))
    resolver = EnvironmentPrincipalResolver.from_environment()
    log_client = _PrefectLogClient(flow_run_id, records)
    log_client.mode = mode
    orchestration = PrefectOrchestration(log_client)  # ty: ignore[invalid-argument-type] - the log reader's two calls only.
    test_client = TestClient(
        create_app(RunService(projection, orchestration, secrets=resolver.secret_values), resolver)
    )

    def forward(request: httpx.Request) -> httpx.Response:
        response = test_client.request(
            request.method, request.url.raw_path.decode(), headers=dict(request.headers), content=request.content
        )
        return httpx.Response(response.status_code, headers=response.headers, content=response.content)

    sync_client = SyncClient("http://sync-api.test", OWNER_TOKEN, transport=httpx.MockTransport(forward))
    addresses = (prefect_api_address, UNREACHABLE_ADDRESS, "prefect-server:4200")
    headers = {"Authorization": f"Bearer {OWNER_TOKEN}"}

    responses = {
        path: test_client.get(path, headers=headers)
        for path in (f"/runs/{RUN_ID}", f"/runs/{RUN_ID}/logs", f"/events?run_id={RUN_ID}")
    }
    outputs = {
        command: RUNNER.invoke(cli_app, list(command), obj={"client": sync_client})
        for command in (("runs", "show", RUN_ID), ("runs", "logs", RUN_ID), ("events", "--run-id", RUN_ID))
    }

    expected_logs = {"read": 200, "expired": 410, "unavailable": 503}[mode]
    assert responses[f"/runs/{RUN_ID}/logs"].status_code == expected_logs
    assert responses[f"/runs/{RUN_ID}"].status_code == 200
    assert responses[f"/events?run_id={RUN_ID}"].status_code == 200
    if mode == "read":
        served = [item["message"] for item in responses[f"/runs/{RUN_ID}/logs"].json()["items"]]
        assert any("demo-test-017" in line for line in served)
        assert outputs["runs", "logs", RUN_ID].exit_code == 0
    findings = {
        f"{path} message {message!r}": _forbidden(message, addresses)
        for path, response in responses.items()
        for message in _messages(response.json())
    }
    findings.update(
        {f"{' '.join(command)} output": _forbidden(result.output, addresses) for command, result in outputs.items()}
    )
    assert {where: found for where, found in findings.items() if found} == {}
    assert _api_messages_naming_prefect(responses) == []


def _delete_calls(source_root: Path) -> list[str]:
    """Every call to a Prefect flow run delete (`delete_flow_run`) in the modules under `source_root`."""
    calls: list[str] = []
    for module in sorted(source_root.rglob("*.py")):
        tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            name = function.attr if isinstance(function, ast.Attribute) else getattr(function, "id", "")
            if name == "delete_flow_run":
                calls.append(f"{module.relative_to(source_root)}:{node.lineno}")
    return calls


def test_sync_deletes_no_prefect_flow_run() -> None:
    """Sync keeps a flow run while the run record that names it exists (FR-011, research R7)."""
    assert _delete_calls(REPOSITORY_SOURCE) == []


def test_the_delete_guard_finds_a_call(tmp_path: Path) -> None:
    """The guard reports a module that calls `delete_flow_run`, through an attribute or a bare name."""
    (tmp_path / "cleanup.py").write_text(
        "async def purge(client, flow_run_id):\n    await client.delete_flow_run(flow_run_id)\n", encoding="utf-8"
    )
    (tmp_path / "other.py").write_text("delete_flow_run('x')\n", encoding="utf-8")

    assert _delete_calls(tmp_path) == ["cleanup.py:2", "other.py:1"]
