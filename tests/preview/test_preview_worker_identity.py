"""Preview starts the supported service worker without static identity plumbing."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from invoke import Context

from tasks import preview

if TYPE_CHECKING:
    from pathlib import Path

    import pytest
    from invoke.tasks import Task


def _staged_up(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
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
            "PREVIEW_WORK_POOL": "preview-pool",
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
            "PREFECT_API_URL": "http://localhost:4210/api",
        },
    )
    monkeypatch.setattr(preview, "_compose", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(preview, "_wait_for_http", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(preview, "assert_no_legacy_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(preview, "_run_smoke", lambda *_args, **_kwargs: None)

    def _start(name: str, argv: list[str], env: dict[str, str]) -> None:
        captured[f"argv:{name}"] = argv
        captured[f"env:{name}"] = env

    monkeypatch.setattr(preview, "_start_process", _start)

    class _RecordingContext(Context):
        def run(self, command: str, **kwargs: Any) -> None:  # noqa: ANN401, PLR6301 - Invoke surface.
            if "service.deploy" in command:
                captured["deploy_env"] = kwargs.get("env", {})

    cast("Task", preview.up).body(_RecordingContext())
    return captured


def test_preview_starts_the_supported_service_worker_entrypoint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured = _staged_up(monkeypatch, tmp_path)

    argv = captured["argv:prefect-worker"]
    assert argv == [
        "uv",
        "run",
        "python",
        "-m",
        "infrahub_sync.service.worker",
        "--pool",
        "preview-pool",
    ]


def test_preview_passes_the_worker_environment_to_deployment_unchanged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured = _staged_up(monkeypatch, tmp_path)

    assert captured["deploy_env"] == captured["env:prefect-worker"]
