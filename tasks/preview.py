"""Preview environment: one command from a fresh clone to a testable v3 stack.

`invoke preview.up` brings up a disposable Infrahub instance and a dedicated
Prefect server (Docker), loads the example schema, starts the managed Sync HTTP
API and a Prefect worker from this checkout, applies the managed deployment,
creates a first saved plan, and finishes by running the preview smoke suite so a
tester never receives an environment that has not just proven its own basics.

Configuration ships in `development/preview.env` (no secrets — local-only
defaults); personal overrides belong in the gitignored
`development/preview.local.env`. Runtime state (pids, logs, caches) lives under
`.preview/`, also gitignored.
"""

from __future__ import annotations

import contextlib
import json
import os
import shlex
import signal
import subprocess  # noqa: S404 -- fixed argv process management for the local preview stack
import time
from pathlib import Path

from invoke import Context, task

from .utils import ESCAPED_REPO_PATH

NAMESPACE = "INFRAHUB-SYNC-PREVIEW"
REPO_ROOT = Path(__file__).parent.parent.resolve()
DEV_DIR = REPO_ROOT / "development"
STATE_DIR = REPO_ROOT / ".preview"
ENV_FILE = DEV_DIR / "preview.env"
LOCAL_ENV_FILE = DEV_DIR / "preview.local.env"
COMPOSE_FILES = (
    DEV_DIR / "docker-compose.infrahub.yml",
    DEV_DIR / "docker-compose.preview.yml",
)
SCHEMA_FILE = REPO_ROOT / "examples" / "prefect_remote_run" / "schemas" / "infra_device.yml"
# Process name -> substring its command line must contain before a recorded pid
# is treated as ours (guards against pid recycling by unrelated processes).
MANAGED_PROCESSES = {
    "sync-api": "infrahub_sync.managed.serve",
    "prefect-worker": "prefect worker",
}
WAIT_TIMEOUT_SECONDS = 420


class PreviewError(RuntimeError):
    """Raised when the preview environment cannot reach a required state."""


def load_preview_env() -> dict[str, str]:
    """Read shipped preview settings, then apply gitignored local overrides."""
    values: dict[str, str] = {}
    for env_file in (ENV_FILE, LOCAL_ENV_FILE):
        if not env_file.exists():
            continue
        for raw_line in env_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    required = {
        "COMPOSE_PROJECT_NAME",
        "INFRAHUB_INITIAL_ADMIN_TOKEN",
        "PREVIEW_BEARER_TOKENS",
        "PREVIEW_INFRAHUB_PORT",
        "PREVIEW_PREFECT_PORT",
        "PREVIEW_SYNC_API_PORT",
        "PREVIEW_WORK_POOL",
    }
    missing = required - values.keys()
    if missing:
        msg = f"{ENV_FILE} is missing required keys: {sorted(missing)}"
        raise PreviewError(msg)
    return values


def preview_urls(values: dict[str, str]) -> dict[str, str]:
    """Derive the three service URLs from the loaded settings."""
    return {
        "infrahub": f"http://localhost:{values['PREVIEW_INFRAHUB_PORT']}",
        "prefect": f"http://localhost:{values['PREVIEW_PREFECT_PORT']}",
        "sync_api": f"http://localhost:{values['PREVIEW_SYNC_API_PORT']}",
    }


def _runtime_env(values: dict[str, str]) -> dict[str, str]:
    """Environment for Sync processes: worker, managed API, CLI, and smoke."""
    urls = preview_urls(values)
    env = dict(os.environ)
    env.update(
        {
            "INFRAHUB_ADDRESS": urls["infrahub"],
            "INFRAHUB_API_TOKEN": values["INFRAHUB_INITIAL_ADMIN_TOKEN"],
            "PREFECT_API_URL": f"{urls['prefect']}/api",
            "INFRAHUB_SYNC_CONFIG_DIRECTORY": str(REPO_ROOT / "examples"),
            "INFRAHUB_SYNC_CACHE_DIR": str(STATE_DIR / "sync-cache"),
            "INFRAHUB_SYNC_MANAGED_CACHE_LOCATION": str(STATE_DIR / "product-cache"),
            "INFRAHUB_SYNC_MANAGED_BEARER_TOKENS": values["PREVIEW_BEARER_TOKENS"],
            "INFRAHUB_SYNC_MANAGED_WORK_POOL": values["PREVIEW_WORK_POOL"],
            "INFRAHUB_SYNC_MANAGED_FLOW_WORKING_DIRECTORY": str(REPO_ROOT),
        }
    )
    return env


def _compose(context: Context, arguments: str, values: dict[str, str]) -> None:
    files = " ".join(f"-f {shlex.quote(str(path))}" for path in COMPOSE_FILES)
    command = (
        f"docker compose --project-name {shlex.quote(values['COMPOSE_PROJECT_NAME'])} "
        f"--env-file {shlex.quote(str(ENV_FILE))} {files} {arguments}"
    )
    with context.cd(ESCAPED_REPO_PATH):
        context.run(command, pty=False)


_SERVER_ERROR_FLOOR = 500


def _wait_for_http(url: str, description: str, timeout: int = WAIT_TIMEOUT_SECONDS) -> None:
    import httpx  # noqa: PLC0415 -- lazy so importing the tasks package never requires the managed extras

    print(f" - [{NAMESPACE}] Waiting for {description} at {url}")
    deadline = time.monotonic() + timeout
    last_error = "no attempt made"
    while time.monotonic() < deadline:
        try:
            response = httpx.get(url, timeout=5)
        except httpx.HTTPError as exc:
            last_error = str(exc)
        else:
            # Deliberate: any response below 500 counts as "the service is up",
            # so unauthenticated probes of authenticated endpoints (401/404)
            # qualify. A new probe URL must not rely on its body or status.
            if response.status_code < _SERVER_ERROR_FLOOR:
                return
            last_error = f"HTTP {response.status_code}"
        time.sleep(3)
    msg = f"{description} did not become ready within {timeout}s (last: {last_error})"
    raise PreviewError(msg)


def _pid_file(name: str) -> Path:
    return STATE_DIR / f"{name}.pid"


def _log_file(name: str) -> Path:
    return STATE_DIR / f"{name}.log"


def _process_running(name: str) -> int | None:
    """Return the recorded pid when it is alive and still ours, else None."""
    pid_file = _pid_file(name)
    if not pid_file.exists():
        return None
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except ValueError:
        return None
    probe = subprocess.run(  # noqa: S603
        ["/bin/ps", "-p", str(pid), "-o", "command="],
        capture_output=True,
        text=True,
        check=False,
    )
    command_line = probe.stdout.strip()
    if probe.returncode != 0 or MANAGED_PROCESSES[name] not in command_line:
        return None
    return pid


def _start_process(name: str, argv: list[str], env: dict[str, str]) -> None:
    if _process_running(name) is not None:
        print(f" - [{NAMESPACE}] {name} already running")
        return
    STATE_DIR.mkdir(exist_ok=True)
    log_handle = _log_file(name).open("ab")
    process = subprocess.Popen(  # noqa: S603 -- fixed argv, local dev processes
        argv,
        cwd=REPO_ROOT,
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    _pid_file(name).write_text(f"{process.pid}\n", encoding="utf-8")
    print(f" - [{NAMESPACE}] Started {name} (pid {process.pid}, log {_log_file(name)})")


def _stop_process(name: str) -> None:
    pid = _process_running(name)
    if pid is None:
        _pid_file(name).unlink(missing_ok=True)
        return
    print(f" - [{NAMESPACE}] Stopping {name} (pid {pid})")
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(os.getpgid(pid), signal.SIGTERM)
        # Wait for a clean exit so containers are not torn down under a process
        # still talking to them; escalate if it lingers.
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and _process_running(name) is not None:
            time.sleep(0.5)
        if _process_running(name) is not None:
            print(f" - [{NAMESPACE}] {name} did not exit in 15s; sending SIGKILL")
            os.killpg(os.getpgid(pid), signal.SIGKILL)
    _pid_file(name).unlink(missing_ok=True)


@task
def up(context: Context) -> None:
    """Bring up the full preview stack, prove it with the smoke suite, print URLs."""
    values = load_preview_env()
    urls = preview_urls(values)
    env = _runtime_env(values)
    STATE_DIR.mkdir(exist_ok=True)
    Path(env["INFRAHUB_SYNC_CACHE_DIR"]).mkdir(parents=True, exist_ok=True)
    Path(env["INFRAHUB_SYNC_MANAGED_CACHE_LOCATION"]).mkdir(parents=True, exist_ok=True)

    print(f" - [{NAMESPACE}] Starting containers (first run downloads images)")
    _compose(context, "up --detach --wait --quiet-pull", values)
    _wait_for_http(f"{urls['infrahub']}/api/config", "Infrahub")
    _wait_for_http(f"{urls['prefect']}/api/health", "Prefect")

    print(f" - [{NAMESPACE}] Loading the example schema")
    context.run(
        f"uv run infrahubctl schema load {shlex.quote(str(SCHEMA_FILE))}",
        env={"INFRAHUB_ADDRESS": env["INFRAHUB_ADDRESS"], "INFRAHUB_API_TOKEN": env["INFRAHUB_API_TOKEN"]},
    )

    print(f" - [{NAMESPACE}] Ensuring the Prefect work pool exists")
    context.run(
        f"uv run prefect work-pool create {shlex.quote(values['PREVIEW_WORK_POOL'])} --type process",
        env={"PREFECT_API_URL": env["PREFECT_API_URL"]},
        warn=True,  # already-exists is fine
    )

    print(f" - [{NAMESPACE}] Applying the managed deployment")
    context.run("uv run python -m infrahub_sync.managed.deploy", env=env)

    _start_process(
        "prefect-worker",
        ["uv", "run", "prefect", "worker", "start", "--pool", values["PREVIEW_WORK_POOL"]],
        env,
    )
    _start_process(
        "sync-api",
        [
            "uv",
            "run",
            "uvicorn",
            "--factory",
            "infrahub_sync.managed.serve:build_app",
            "--host",
            "127.0.0.1",
            "--port",
            values["PREVIEW_SYNC_API_PORT"],
        ],
        env,
    )
    _wait_for_http(f"{urls['sync_api']}/openapi.json", "managed Sync API", timeout=90)

    print(f" - [{NAMESPACE}] Creating a first saved plan (custom-example)")
    context.run("uv run infrahub-sync diff --name custom-example --directory examples/", env=env)

    smoke(context)

    tokens = json.loads(values["PREVIEW_BEARER_TOKENS"])
    print(f" - [{NAMESPACE}] Preview environment ready")
    print(f"     Infrahub UI:      {urls['infrahub']}  (admin / infrahub)")
    print(f"     Prefect UI:       {urls['prefect']}")
    print(f"     Managed Sync API: {urls['sync_api']}  (bearer principals: {', '.join(sorted(tokens))})")
    print(f"     Config directory: {env['INFRAHUB_SYNC_CONFIG_DIRECTORY']}")
    print(f"     Runtime state:    {STATE_DIR}")
    print("     Next: docs/docs/reference/managed-http-api.mdx and `uv run invoke preview.status`")


@task
def smoke(context: Context) -> None:
    """Run the preview smoke suite against the running environment."""
    values = load_preview_env()
    print(f" - [{NAMESPACE}] Running the preview smoke suite")
    with context.cd(ESCAPED_REPO_PATH):
        context.run("uv run pytest -m preview tests/preview -q", env=_runtime_env(values))


@task
def status(context: Context) -> None:
    """Show container, process, and endpoint status."""
    values = load_preview_env()
    urls = preview_urls(values)
    _compose(context, "ps", values)
    for name in MANAGED_PROCESSES:
        pid = _process_running(name)
        state = f"running (pid {pid})" if pid else "stopped"
        print(f" - [{NAMESPACE}] {name}: {state}")
    for label, url in urls.items():
        print(f" - [{NAMESPACE}] {label}: {url}")


@task
def logs(context: Context, name: str = "sync-api", lines: int = 50) -> None:
    """Print the tail of a managed host process log (sync-api or prefect-worker)."""
    del context
    if name not in MANAGED_PROCESSES:
        msg = f"unknown process {name!r}; expected one of {sorted(MANAGED_PROCESSES)}"
        raise PreviewError(msg)
    log_path = _log_file(name)
    if not log_path.exists():
        print(f" - [{NAMESPACE}] no log at {log_path}")
        return
    content = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in content[-lines:]:
        print(line)


@task
def down(context: Context, volumes: bool = False) -> None:  # noqa: FBT001, FBT002 -- invoke flag idiom, as in bench.py
    """Stop the preview: host processes, then containers (add --volumes to reset data)."""
    values = load_preview_env()
    for name in MANAGED_PROCESSES:
        _stop_process(name)
    arguments = "down --volumes" if volumes else "down"
    _compose(context, arguments, values)
    print(f" - [{NAMESPACE}] Preview stopped{' and data volumes removed' if volumes else ''}")
