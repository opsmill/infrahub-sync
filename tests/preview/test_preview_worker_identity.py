"""The preview worker's canonical identity reaches managed flow-run children.

A managed flow run claims its execution with `PREFECT__WORKER_ID`, and the claim is a
fencing token: only the worker that claimed an execution may write its terminal state
back. On a self-hosted Prefect server the worker never learns its own backend id — the
client only asks for one against Prefect Cloud — so nothing populates that variable and
every managed run dies at the claim gate.

Preview closes that here, at startup: it names its worker uniquely, asks the server for
that exact worker's registered UUID, and binds it into the managed deployment's
`job_variables.env`, which `prepare_for_flow_run` merges into the flow-run process last.
The ordering is the security property — the managed API is never exposed before a
canonical identity is installed, so no run can be submitted that would claim under an
identity nobody issued.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, cast

import pytest
from invoke import Context

from tasks import preview
from tasks.preview import PreviewError

if TYPE_CHECKING:
    from pathlib import Path

    from invoke.tasks import Task

WORKER_UUID = "7f1d3c52-9a04-4f0f-9b7a-2c5f1e8d6a31"
WORK_POOL = "preview-pool"
PREFECT_API = "http://localhost:4210/api"


class _Recorder:
    """One ordered log of every startup step preview takes."""

    def __init__(self) -> None:
        self.events: list[str] = []

    def add(self, event: str) -> None:
        self.events.append(event)


def _worker_record(name: str, *, worker_id: str = WORKER_UUID, status: str = "ONLINE") -> dict[str, Any]:
    return {"id": worker_id, "name": name, "status": status}


class _WorkersEndpoint:
    """A stand-in Prefect workers/filter route returning a scripted page."""

    def __init__(self, pages: list[list[dict[str, Any]]]) -> None:
        self.pages = pages
        self.requests: list[tuple[str, Any]] = []

    def __call__(self, url: str, **kwargs: Any) -> Any:  # noqa: ANN401 — httpx keyword surface
        self.requests.append((url, kwargs.get("json")))
        page = self.pages[min(len(self.requests), len(self.pages)) - 1]
        return _Response(page)


class _Response:
    def __init__(self, payload: Any) -> None:  # noqa: ANN401 — any JSON body
        self._payload = payload
        self.status_code = 200
        self.text = "stubbed"

    def json(self) -> Any:  # noqa: ANN401 — any JSON body
        return self._payload


def _install_endpoint(monkeypatch: pytest.MonkeyPatch, pages: list[list[dict[str, Any]]]) -> _WorkersEndpoint:
    import httpx

    endpoint = _WorkersEndpoint(pages)
    monkeypatch.setattr(httpx, "post", endpoint)
    monkeypatch.setattr(preview.time, "sleep", lambda _seconds: None)
    return endpoint


# ======================================================================================
# Resolving exactly one online worker, by the name preview gave it
# ======================================================================================


def test_the_registered_worker_id_is_read_from_the_named_online_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    endpoint = _install_endpoint(
        monkeypatch, [[_worker_record("other-worker", worker_id=str(uuid.uuid4())), _worker_record("preview-1")]]
    )

    resolved = preview._registered_worker_id(PREFECT_API, WORK_POOL, "preview-1", timeout=5)

    assert resolved == WORKER_UUID
    url, body = endpoint.requests[0]
    assert url == f"{PREFECT_API}/work_pools/{WORK_POOL}/workers/filter"
    assert body["workers"]["status"]["any_"] == ["ONLINE"]


def test_a_worker_that_is_not_yet_registered_is_waited_for(monkeypatch: pytest.MonkeyPatch) -> None:
    """Registration is asynchronous, so absence early is normal and absence late is not."""
    _install_endpoint(monkeypatch, [[], [], [_worker_record("preview-1")]])

    assert preview._registered_worker_id(PREFECT_API, WORK_POOL, "preview-1", timeout=5) == WORKER_UUID


@pytest.mark.parametrize(
    "page",
    [
        pytest.param([], id="absent"),
        pytest.param([_worker_record("preview-1", status="OFFLINE")], id="offline-only"),
        pytest.param([_worker_record("someone-else")], id="different-name"),
    ],
)
def test_an_unresolvable_worker_refuses_rather_than_guessing(
    monkeypatch: pytest.MonkeyPatch, page: list[dict[str, Any]]
) -> None:
    _install_endpoint(monkeypatch, [page])

    with pytest.raises(PreviewError, match="canonical"):
        preview._registered_worker_id(PREFECT_API, WORK_POOL, "preview-1", timeout=1)


def test_two_workers_sharing_the_name_refuse_rather_than_picking_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ambiguity is a refusal: the wrong id fences the run against the wrong worker."""
    _install_endpoint(
        monkeypatch, [[_worker_record("preview-1"), _worker_record("preview-1", worker_id=str(uuid.uuid4()))]]
    )

    with pytest.raises(PreviewError, match="canonical"):
        preview._registered_worker_id(PREFECT_API, WORK_POOL, "preview-1", timeout=1)


@pytest.mark.parametrize(
    "identifier",
    [
        pytest.param("not-a-uuid", id="text"),
        pytest.param("", id="empty"),
        pytest.param("7F1D3C52-9A04-4F0F-9B7A-2C5F1E8D6A31", id="non-canonical-case"),
        pytest.param("7f1d3c529a044f0f9b7a2c5f1e8d6a31", id="unhyphenated"),
        pytest.param(None, id="null"),
        pytest.param(7, id="non-string"),
    ],
)
def test_a_noncanonical_identity_is_refused(monkeypatch: pytest.MonkeyPatch, identifier: object) -> None:
    """The product accepts only a canonical UUID string; preview must not hand it less."""
    _install_endpoint(monkeypatch, [[{"id": identifier, "name": "preview-1", "status": "ONLINE"}]])

    with pytest.raises(PreviewError, match="canonical"):
        preview._registered_worker_id(PREFECT_API, WORK_POOL, "preview-1", timeout=1)


def test_the_refusal_echoes_no_worker_record(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fixed text: a server record is external content and never reaches the message."""
    _install_endpoint(
        monkeypatch,
        [[{"id": "canary-identity-value", "name": "preview-1", "status": "ONLINE", "note": "canary-note-value"}]],
    )

    with pytest.raises(PreviewError) as caught:
        preview._registered_worker_id(PREFECT_API, WORK_POOL, "preview-1", timeout=1)

    assert "canary-identity-value" not in str(caught.value)
    assert "canary-note-value" not in str(caught.value)


# ======================================================================================
# Startup ordering: nothing is exposed before the identity is installed
# ======================================================================================


def _staged_up(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[_Recorder, dict[str, Any]]:
    """Drive `preview.up` with every side effect recorded in order."""
    recorder = _Recorder()
    captured: dict[str, Any] = {}

    monkeypatch.setattr(preview, "STATE_DIR", tmp_path / ".preview")
    monkeypatch.setattr(
        preview,
        "load_preview_env",
        lambda: {
            "COMPOSE_PROJECT_NAME": "preview-test",
            "PREVIEW_INFRAHUB_PORT": "8080",
            "PREVIEW_PREFECT_PORT": "4210",
            "PREVIEW_SYNC_API_PORT": "8090",
            "PREVIEW_WORK_POOL": WORK_POOL,
            "PREVIEW_BEARER_TOKENS": '{"tester@local": {"token": "t", "administrator": true}}',
        },
    )
    monkeypatch.setattr(
        preview,
        "_runtime_env",
        lambda _values: {
            "INFRAHUB_SYNC_CACHE_DIR": str(tmp_path / "sync-cache"),
            "INFRAHUB_SYNC_CONFIG_DIRECTORY": str(tmp_path / "examples"),
            "INFRAHUB_ADDRESS": "http://localhost:8080",
            "INFRAHUB_API_TOKEN": "token",
            "PREFECT_API_URL": PREFECT_API,
        },
    )
    monkeypatch.setattr(preview, "_compose", lambda *_args, **_kwargs: recorder.add("compose-up"))
    monkeypatch.setattr(preview, "_wait_for_http", lambda _url, description, **_kw: recorder.add(f"wait:{description}"))
    monkeypatch.setattr(preview, "_run_smoke", lambda *_args, **_kwargs: recorder.add("smoke"))

    def _start(name: str, argv: list[str], env: dict[str, str]) -> None:
        recorder.add(f"start:{name}")
        captured[f"argv:{name}"] = argv
        captured[f"env:{name}"] = env

    def _resolve(_api: str, _pool: str, worker_name: str, **_kwargs: object) -> str:
        recorder.add("resolve-worker-id")
        captured["worker_name"] = worker_name
        return WORKER_UUID

    monkeypatch.setattr(preview, "_start_process", _start)
    monkeypatch.setattr(preview, "_registered_worker_id", _resolve)

    class _RecordingContext(Context):
        def run(self, command: str, **kwargs: Any) -> None:  # noqa: ANN401, PLR6301 — invoke's surface
            if "managed.deploy" in command:
                recorder.add("deploy")
                captured["deploy_env"] = kwargs.get("env", {})
            elif "work-pool create" in command:
                recorder.add("work-pool")
            elif "schema load" in command:
                recorder.add("schema-load")
            elif "infrahub-sync diff" in command:
                recorder.add("first-plan")

    cast("Task", preview.up).body(_RecordingContext())
    return recorder, captured


def test_the_worker_identity_is_installed_before_the_api_is_exposed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The ordering property: no run can be submitted before a claim can succeed."""
    recorder, _ = _staged_up(monkeypatch, tmp_path)

    order = recorder.events
    assert order.index("start:prefect-worker") < order.index("resolve-worker-id")
    assert order.index("resolve-worker-id") < order.index("deploy")
    assert order.index("deploy") < order.index("start:sync-api")
    assert order.index("start:sync-api") < order.index("wait:managed Sync API")
    assert order.index("wait:managed Sync API") < order.index("smoke")


def test_the_worker_is_started_under_the_unique_name_that_is_resolved(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The name is how the exact worker is selected, so it must be ours and unique."""
    _, captured = _staged_up(monkeypatch, tmp_path)

    argv = captured["argv:prefect-worker"]
    assert "--name" in argv
    name = argv[argv.index("--name") + 1]
    assert name == captured["worker_name"]
    # The uniqueness comes from a UUID suffix, so the name cannot collide with a
    # registration left behind by an earlier session.
    assert str(uuid.UUID(name[-36:])) == name[-36:]


def test_each_bring_up_names_its_worker_differently(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A recycled name could resolve a stale registration from an earlier session."""
    first_root = tmp_path / "one"
    second_root = tmp_path / "two"
    first_root.mkdir()
    second_root.mkdir()

    _, first = _staged_up(monkeypatch, first_root)
    _, second = _staged_up(monkeypatch, second_root)

    assert first["worker_name"] != second["worker_name"]


def test_the_resolved_identity_reaches_the_deployment_binding(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _, captured = _staged_up(monkeypatch, tmp_path)

    assert captured["deploy_env"][preview.MANAGED_WORKER_ID_ENV] == WORKER_UUID
