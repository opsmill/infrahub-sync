"""The service deployment names an installed module, not a file in a checkout.

A worker resolves this deployment by importing the installed distribution. Nothing here
may depend on a repository being present on the worker host, on the worker's current
working directory, or on a Prefect step that puts source there.
"""

from __future__ import annotations

import json
import os
import subprocess  # noqa: S404 - the worker parent runs a fixed interpreter and inline script.
import sys
from asyncio import sleep
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import pytest

pytest.importorskip("prefect")
pytest.importorskip("opsmill_prefect_extras")

from prefect.flows import load_flow_from_entrypoint
from prefect.utilities import importtools
from prefect.workers.process import ProcessWorker

from infrahub_sync.service import deploy
from infrahub_sync.service import flow as installed_flow
from infrahub_sync.service.orchestration import SERVICE_DEFINITION

INSTALLED_FLOW_FILE = Path(installed_flow.__file__ or "").resolve()

if TYPE_CHECKING:
    from prefect.client.schemas.actions import DeploymentUpdate
    from typing_extensions import Self

INSTALLED_IDENTITY = "infrahub_sync.service.flow.service_sync_run"


@pytest.fixture
def _no_script_loading(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly if Prefect resolves this deployment by reading a source file.

    Prefect routes an entrypoint containing ``.py:`` to ``load_script_as_module``, which
    executes a file at a recorded absolute path. That path is this installation's, so a
    worker that does not share this filesystem has nothing to load.
    """

    def refuse(path: str) -> None:
        msg = f"the service entrypoint must not be resolved by loading the file {path!r}"
        raise AssertionError(msg)

    monkeypatch.setattr(importtools, "load_script_as_module", refuse)


def _write_shadow_package(root: Path) -> None:
    """Write a package that would answer to ``infrahub_sync`` if cwd won resolution."""
    service = root / "infrahub_sync" / "service"
    service.mkdir(parents=True)
    (root / "infrahub_sync" / "__init__.py").write_text("SHADOW = True\n", encoding="utf-8")
    (service / "__init__.py").write_text("SHADOW = True\n", encoding="utf-8")
    (service / "flow.py").write_text(
        "SHADOW = True\n"
        "from prefect import flow\n\n\n"
        "@flow(name='infrahub-sync-service')\n"
        "def service_sync_run(run_id: str = '', stage: str = ''):\n"
        "    return 'shadow'\n",
        encoding="utf-8",
    )


@pytest.mark.usefixtures("_no_script_loading")
def test_the_deployment_resolves_through_the_installed_module_path() -> None:
    """The recorded entrypoint imports the installed distribution by dotted identity."""
    entrypoint = SERVICE_DEFINITION.entrypoint
    assert entrypoint is not None
    assert entrypoint == INSTALLED_IDENTITY
    assert ":" not in entrypoint
    assert os.sep not in entrypoint
    assert not Path(entrypoint).exists()

    resolved = load_flow_from_entrypoint(entrypoint)

    assert resolved.name == SERVICE_DEFINITION.flow_name
    assert resolved.fn is installed_flow.service_sync_run.fn


def test_the_flow_run_working_directory_is_prefects_own_disposable_one() -> None:
    """No configured working directory means each flow run gets a fresh empty one.

    A process worker with no ``working_dir`` in its job configuration runs every flow run
    in a new temporary directory. That is what makes a repository-resident shadow package
    unreachable, so the default has to stay unset rather than be pointed at a checkout.
    """
    configuration = ProcessWorker.job_configuration()

    assert "working_dir" in type(configuration).model_fields
    assert configuration.working_dir is None


def test_the_service_declares_no_flow_working_directory_setting() -> None:
    """The retired working-directory environment name is gone from the deploy surface."""
    assert not hasattr(deploy, "FLOW_WORKING_DIRECTORY_ENV")
    assert not hasattr(deploy, "required_flow_working_directory")
    assert not hasattr(deploy, "flow_pull_steps")
    assert not any("FLOW_WORKING_DIRECTORY" in name for name in os.environ if name.startswith("INFRAHUB_SYNC"))


class _ExistingDeploymentClient:
    """A Prefect client holding one deployment that still carries a pull step."""

    def __init__(self, pull_steps: list[dict[str, Any]] | None) -> None:
        self.deployment_id = uuid4()
        self.pull_steps = pull_steps
        self.updates: list[DeploymentUpdate] = []

    async def read_deployment_by_name(self, name: str) -> Any:  # noqa: ANN401 - Prefect model stand-in.
        assert name == SERVICE_DEFINITION.key
        return SimpleNamespace(id=self.deployment_id, pull_steps=self.pull_steps)

    async def update_deployment(self, deployment_id: UUID, deployment: DeploymentUpdate) -> None:
        assert deployment_id == self.deployment_id
        self.updates.append(deployment)
        self.pull_steps = deployment.pull_steps

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


@pytest.fixture
def offline_catalogue_apply(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record the catalogue apply without contacting Prefect."""
    order: list[str] = []

    async def apply(*_args: object, **_kwargs: object) -> SimpleNamespace:
        order.append("catalogue")
        await sleep(0)
        return SimpleNamespace(is_successful=True)

    monkeypatch.setenv(deploy.WORK_POOL_ENV, "service-pool")
    monkeypatch.setattr(deploy, "apply_deployments", apply)
    return order


@pytest.mark.parametrize(
    "existing",
    [
        pytest.param(
            [{"prefect.deployments.steps.set_working_directory": {"directory": "/srv/checkout"}}],
            id="working-directory-step",
        ),
        pytest.param(
            [{"prefect.deployments.steps.git_clone": {"repository": "https://example.test/x.git"}}],
            id="source-pull-step",
        ),
    ],
)
@pytest.mark.usefixtures("offline_catalogue_apply")
async def test_deploying_the_service_converges_existing_pull_steps_to_empty(
    monkeypatch: pytest.MonkeyPatch,
    existing: list[dict[str, Any]],
) -> None:
    """A deployed pull step is removed by applying, not merely left unspecified.

    Omitting ``pull_steps`` leaves whatever a previous apply installed, so a worker would
    keep resolving source before the entrypoint runs. Convergence states the empty value.
    """
    client = _ExistingDeploymentClient(existing)
    monkeypatch.setattr(deploy, "get_client", lambda: client)

    assert await deploy._deploy() == 0

    assert client.pull_steps == []
    assert [update.pull_steps for update in client.updates] == [[]]


@pytest.mark.usefixtures("offline_catalogue_apply")
async def test_deploying_the_service_leaves_already_empty_pull_steps_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Convergence is idempotent: an empty deployment is not rewritten on every apply."""
    client = _ExistingDeploymentClient([])
    monkeypatch.setattr(deploy, "get_client", lambda: client)

    assert await deploy._deploy() == 0

    assert client.pull_steps == []
    assert client.updates == []


async def test_deploying_the_service_needs_no_working_directory_in_the_environment(
    monkeypatch: pytest.MonkeyPatch,
    offline_catalogue_apply: list[str],
) -> None:
    """The deploy path completes with no working-directory setting present at all."""
    client = _ExistingDeploymentClient(None)
    monkeypatch.setattr(deploy, "get_client", lambda: client)
    for name in list(os.environ):
        if name.startswith("INFRAHUB_SYNC_SERVICE_FLOW"):
            monkeypatch.delenv(name, raising=False)

    assert await deploy._deploy() == 0

    assert client.pull_steps == []
    assert offline_catalogue_apply == ["catalogue"]


# The worker parent resolves the deployment's entrypoint in its own process: a
# ProcessWorker builds a Runner (``prefect/workers/process.py``), and that runner imports
# the flow to run ``on_crashed`` hooks once a child dies
# (``prefect/runner/runner.py`` -> ``prefect/runner/_hook_runner.py``). Prefect's module
# loader prepends the process working directory to ``sys.path``, so this has to be checked
# in a *fresh* process: inside the test session the installed module is already cached in
# ``sys.modules``, and the cache -- not the entrypoint form -- is what answers.
_CRASH_HOOK_PARENT = '''
import json, os, sys
from pathlib import Path

sys.path.insert(0, {repository_root!r})

# The parent imports its own entry module first, exactly as `python -m` does, before it
# is ever asked to resolve the flow.
from infrahub_sync.service import worker as service_worker
from infrahub_sync.service.orchestration import SERVICE_DEFINITION

report = {{}}
os.chdir({shadow!r})
report["cwd_at_start"] = os.getcwd()


def resolve_like_a_crash_hook():
    """Do what the parent's runner does after a child crashes: import the flow.

    The distribution is dropped from ``sys.modules`` first, on purpose. Prefect's module
    loader prepends the working directory to ``sys.path``, so an already-cached package
    would answer from the cache and the assertion would say nothing about the directory --
    which is exactly how the earlier in-process test produced a false pass. Cleared, the
    only thing deciding the answer is where this parent is running.
    """
    from prefect.flows import load_flow_from_entrypoint

    report["cwd_at_hook"] = os.getcwd()
    report["cwd_contents"] = sorted(entry.name for entry in Path.cwd().iterdir())
    for name in [module for module in sys.modules if module.split(".")[0] == "infrahub_sync"]:
        del sys.modules[name]
    load_flow_from_entrypoint(SERVICE_DEFINITION.entrypoint)
    module = sys.modules["infrahub_sync.service.flow"]
    report["resolved_file"] = str(Path(module.__file__).resolve())
    report["is_shadow"] = bool(getattr(module, "SHADOW", False))


class _Probe:
    def __init__(self, **kwargs):
        pass

    async def start(self):
        try:
            resolve_like_a_crash_hook()
        except BaseException as failure:
            report["error_type"] = type(failure).__name__
            report["error"] = str(failure)[:400]


service_worker.ServiceProcessWorker = _Probe
try:
    report["exit"] = service_worker.main(["--pool", "qualification-pool"])
finally:
    Path({report_path!r}).write_text(json.dumps(report), encoding="utf-8")
'''


def _run_crash_hook_parent(tmp_path: Path) -> dict[str, Any]:
    """Start a worker parent in a fresh process whose working directory holds a shadow."""
    shadow = tmp_path / "checkout-with-shadow"
    shadow.mkdir()
    _write_shadow_package(shadow)
    report_path = tmp_path / "crash-hook-report.json"
    script = _CRASH_HOOK_PARENT.format(
        repository_root=str(Path(__file__).resolve().parents[2]),
        shadow=str(shadow),
        report_path=str(report_path),
    )
    completed = subprocess.run(  # noqa: S603 - fixed interpreter, inline script, no shell.
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(tmp_path),
    )
    if not report_path.is_file():
        pytest.fail(f"the worker parent wrote no report; stderr: {completed.stderr[-3000:]}")
    report: dict[str, Any] = json.loads(report_path.read_text(encoding="utf-8"))
    report["shadow"] = str(shadow)
    report["stderr"] = completed.stderr[-2000:]
    return report


def _resolution(report: dict[str, Any]) -> tuple[bool, str]:
    """Return whether the shadow answered, and the evidence either way."""
    if "is_shadow" not in report:
        return (
            True,
            f"resolution failed inside the working directory: {report.get('error_type')}: {report.get('error')}",
        )
    return bool(report["is_shadow"]), str(report.get("resolved_file"))


def test_the_worker_parent_resolves_the_installed_flow_after_a_child_crash(tmp_path: Path) -> None:
    """A crash hook in the parent imports the installation, never the working directory.

    The parent is started the way an operator can start it -- inside a checkout that holds
    an ``infrahub_sync`` package -- and is then asked to resolve the flow the way its
    runner does after a child crashes. It must reach the installed distribution.
    """
    report = _run_crash_hook_parent(tmp_path)
    from_shadow, evidence = _resolution(report)

    assert from_shadow is False, f"the crash hook resolved out of the working directory: {evidence}"
    assert report["resolved_file"] == str(INSTALLED_FLOW_FILE)
    assert report["exit"] == 0


def test_the_worker_parent_runs_its_whole_lifetime_from_an_empty_directory(tmp_path: Path) -> None:
    """The parent leaves the directory it was started in before it builds the worker.

    Nothing the parent imports later -- the runner, its crash hooks, an adapter -- can then
    resolve out of a source tree, because its working directory holds nothing to import.
    """
    report = _run_crash_hook_parent(tmp_path)

    assert report["cwd_at_start"] == report["shadow"]
    assert report["cwd_at_hook"] != report["shadow"]
    assert Path(report["cwd_at_hook"]).is_absolute()
    assert report["cwd_contents"] == []
