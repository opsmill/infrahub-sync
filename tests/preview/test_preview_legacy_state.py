"""The preview refuses retired-vocabulary state and resets it destructively."""

from __future__ import annotations

import json
import signal
from typing import TYPE_CHECKING, Any, cast

import httpx
import pytest
from invoke import Context

from tasks import preview
from tasks.preview import RESET_COMMAND, PreviewError

if TYPE_CHECKING:
    from pathlib import Path

    from invoke.tasks import Task

PREFECT_API = "http://localhost:4210/api"
WORK_POOL = "preview-pool"
LEGACY_WORKER = f"{preview.LEGACY_WORKER_NAME_PREFIX}0f0c2f0e-0000-4000-8000-000000000000"
LEGACY_SERVE_COMMAND = f"python -m {preview.LEGACY_PROCESS_COMMANDS[0]}"
LEGACY_WORKER_COMMAND = f"python -m {preview.LEGACY_PROCESS_COMMANDS[1]} --pool {WORK_POOL}"

_Payload = dict[str, str] | list[dict[str, str]]


class _Response:
    """Minimal stand-in for the Prefect responses the preflight reads."""

    def __init__(self, status_code: int, payload: _Payload) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self) -> _Payload:
        return self._payload


def _prefect_server(
    monkeypatch: pytest.MonkeyPatch,
    *,
    deployment: bool = False,
    pools: tuple[str, ...] = (WORK_POOL,),
    workers: dict[str, tuple[str, ...]] | None = None,
) -> None:
    """Answer the preflight's three Prefect reads from a declared server state."""
    registrations = workers or {}

    def _get(url: str, **_kwargs: object) -> _Response:
        assert url.endswith(f"/deployments/name/{preview.LEGACY_FLOW_NAME}/run")
        return _Response(200, {"id": "d-1"}) if deployment else _Response(404, {})

    def _post(url: str, **_kwargs: object) -> _Response:
        if url.endswith("/work_pools/filter"):
            return _Response(200, [{"name": name} for name in pools])
        pool = url.removeprefix(f"{PREFECT_API}/work_pools/").removesuffix("/workers/filter")
        if pool not in pools:
            return _Response(404, {})
        return _Response(200, [{"name": name} for name in registrations.get(pool, ())])

    monkeypatch.setattr(httpx, "get", _get)
    monkeypatch.setattr(httpx, "post", _post)


def _process_list(monkeypatch: pytest.MonkeyPatch, *entries: tuple[int, str]) -> None:
    """Replace the host process probe with a declared process table."""
    monkeypatch.setattr(preview, "_legacy_processes", lambda: tuple(entries))


def _staged_up(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, started: list[str] | None = None) -> list[str]:
    """Run `preview.up` far enough to reach the preflight, recording what it starts."""
    started = [] if started is None else started
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
    monkeypatch.setattr(preview, "_compose", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(preview, "_wait_for_http", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(preview, "_run_smoke", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(preview, "_start_process", lambda name, _argv, _env: started.append(name))

    class _SilentContext(Context):
        def run(self, command: str, **kwargs: Any) -> None:  # noqa: ANN401, PLR6301 - Invoke surface.
            del command, kwargs

    cast("Task", preview.up).body(_SilentContext())
    return started


def test_up_refuses_a_legacy_deployment_and_names_the_reset_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    started: list[str] = []
    _prefect_server(monkeypatch, deployment=True)
    _process_list(monkeypatch)

    with pytest.raises(PreviewError) as refusal:
        _staged_up(monkeypatch, tmp_path, started)

    assert f"{preview.LEGACY_FLOW_NAME}/run" in str(refusal.value)
    assert RESET_COMMAND in str(refusal.value)
    assert started == []


def test_up_refuses_a_running_legacy_host_process(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _prefect_server(monkeypatch)
    _process_list(monkeypatch, (4242, LEGACY_SERVE_COMMAND))

    with pytest.raises(PreviewError) as refusal:
        _staged_up(monkeypatch, tmp_path)

    assert "4242" in str(refusal.value)
    assert RESET_COMMAND in str(refusal.value)


def test_up_proceeds_from_a_legacy_clean_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _prefect_server(monkeypatch, workers={WORK_POOL: ("infrahub-sync-service-1",)})
    _process_list(monkeypatch)

    started = _staged_up(monkeypatch, tmp_path)

    assert started == ["prefect-worker", "sync-api"]


def test_the_preflight_reports_a_legacy_work_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    _prefect_server(monkeypatch, pools=(WORK_POOL, preview.LEGACY_FLOW_NAME))

    findings = preview._legacy_prefect_state(PREFECT_API, WORK_POOL)

    assert findings == [f"work pool {preview.LEGACY_FLOW_NAME}"]


def test_the_preflight_reports_a_legacy_worker_registration(monkeypatch: pytest.MonkeyPatch) -> None:
    _prefect_server(monkeypatch, workers={WORK_POOL: ("infrahub-sync-service-1", LEGACY_WORKER)})

    findings = preview._legacy_prefect_state(PREFECT_API, WORK_POOL)

    assert findings == [f"worker {LEGACY_WORKER} in work pool {WORK_POOL}"]


def test_the_preflight_reports_nothing_for_a_clean_server(monkeypatch: pytest.MonkeyPatch) -> None:
    _prefect_server(monkeypatch, workers={WORK_POOL: ("infrahub-sync-service-1",)})

    assert preview._legacy_prefect_state(PREFECT_API, WORK_POOL) == []


def test_the_destructive_reset_stops_a_legacy_command_line_process(monkeypatch: pytest.MonkeyPatch) -> None:
    signalled: list[tuple[int, int]] = []
    _process_list(monkeypatch, (4242, LEGACY_SERVE_COMMAND), (4243, LEGACY_WORKER_COMMAND))
    monkeypatch.setattr(preview.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(preview.os, "killpg", lambda pid, number: signalled.append((pid, number)))
    monkeypatch.setattr(preview, "_stop_process", lambda _name: None)
    monkeypatch.setattr(preview, "_compose", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(preview, "load_preview_env", dict)

    cast("Task", preview.down).body(Context(), volumes=True)

    assert sorted(signalled) == [(4242, signal.SIGTERM), (4243, signal.SIGTERM)]


def test_the_destructive_reset_refuses_ambiguous_legacy_processes_without_killing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signalled: list[tuple[int, int]] = []
    _process_list(monkeypatch, (4242, LEGACY_SERVE_COMMAND), (4244, LEGACY_SERVE_COMMAND))
    monkeypatch.setattr(preview.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(preview.os, "killpg", lambda pid, number: signalled.append((pid, number)))
    monkeypatch.setattr(preview, "_stop_process", lambda _name: None)
    monkeypatch.setattr(preview, "_compose", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(preview, "load_preview_env", dict)

    with pytest.raises(PreviewError) as refusal:
        cast("Task", preview.down).body(Context(), volumes=True)

    assert "4242" in str(refusal.value)
    assert "4244" in str(refusal.value)
    assert signalled == []


def test_a_plain_stop_leaves_legacy_command_lines_to_the_destructive_reset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signalled: list[tuple[int, int]] = []
    _process_list(monkeypatch, (4242, LEGACY_SERVE_COMMAND))
    monkeypatch.setattr(preview.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(preview.os, "killpg", lambda pid, number: signalled.append((pid, number)))
    monkeypatch.setattr(preview, "_stop_process", lambda _name: None)
    monkeypatch.setattr(preview, "_compose", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(preview, "load_preview_env", dict)

    cast("Task", preview.down).body(Context(), volumes=False)

    assert signalled == []
