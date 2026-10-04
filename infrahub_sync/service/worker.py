"""Run the service ProcessWorker with a canonical self-hosted Prefect identity."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import tempfile
import warnings
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from shutil import rmtree
from tempfile import mkdtemp
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID, uuid4

import anyio
import httpx
from infrahub_sdk.exceptions import AuthenticationError
from infrahub_sdk.exceptions import Error as InfrahubSdkError
from prefect.client.schemas.objects import Worker, WorkerStatus
from prefect.exceptions import ObjectNotFound
from prefect.flows import load_flow_from_entrypoint
from prefect.logging.loggers import PrefectLogAdapter

# Prefect 3.8.6's own process-start parts, pinned with Prefect (see `_start_child`).
from prefect.runner._flow_run_executor import (  # noqa: PLC2701 - deliberate, version-pinned
    FlowRunExecutionResult,
    FlowRunExecutorContext,
)
from prefect.runner._starter_engine import EngineCommandStarter  # noqa: PLC2701 - deliberate, version-pinned
from prefect.utilities.processutils import command_to_string, get_sys_executable
from prefect.workers.process import ProcessJobConfiguration, ProcessWorker, ProcessWorkerResult
from pydantic import PrivateAttr

from infrahub_sync.platform.client import (
    SERVICE_ACCOUNT_REFUSED,
    PlatformSettings,
    PlatformSettingsError,
    default_branch_sync,
    service_client_sync,
)
from infrahub_sync.platform.schema_check import SyncSchemaMissingError, require_sync_schema_sync

from .orchestration import SERVICE_DEFINITION
from .prefect_server import WORKER_SERVICE, refuse_start_unless_prefect_ready

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from anyio.abc import TaskStatus
    from prefect.client.schemas.objects import Flow as APIFlow
    from prefect.client.schemas.objects import FlowRun, WorkPool
    from prefect.client.schemas.responses import DeploymentResponse

_logger = logging.getLogger(__name__)

_IDENTITY_ERROR = "service worker identity is unavailable"
_WORKER_NAME_PREFIX = "infrahub-sync-service"
_WORKER_PAGE_SIZE = 200
_NOT_ADMITTED = "flow run was not admitted as the service deployment"
# The one command a service child runs: what Prefect's process job configuration
# resolves an unset command to. The service sets it explicitly (see
# `ServiceProcessJobConfiguration.prepare_for_flow_run`).
_SERVICE_CHILD_COMMAND = command_to_string([get_sys_executable(), "-m", "prefect.engine"])
# The installed service flow, which every admitted child and hook loads.
_SERVICE_ENTRYPOINT: str = SERVICE_DEFINITION.entrypoint or ""
if not _SERVICE_ENTRYPOINT:
    _NO_ENTRYPOINT = "the service deployment declares no entrypoint"
    raise RuntimeError(_NO_ENTRYPOINT)
_SUBMISSION_IDENTITY: ContextVar[tuple[bool, int | None]] = ContextVar(
    "service_worker_submission_identity",
    default=(False, None),
)


class ServiceWorkerIdentityError(RuntimeError):
    """Refuse polling when the worker's exact server identity is unavailable."""


class ServiceFlowRunRefusedError(RuntimeError):
    """Refuse a flow run that is not the service deployment exactly as the service applied it."""


def service_worker_name() -> str:
    """Return a process-unique Prefect worker name for the supported entrypoint."""
    return f"{_WORKER_NAME_PREFIX}-{uuid4()}"


async def _installed_service_flow(_flow_run: FlowRun) -> Any:  # noqa: RUF029 - Prefect's async resolver contract
    """Resolve the service flow from the installed package, never from the deployment record."""
    return load_flow_from_entrypoint(_SERVICE_ENTRYPOINT, use_placeholder_flow=False)


def _canonical_uuid(value: object) -> UUID | None:
    if isinstance(value, UUID):
        return value
    if not isinstance(value, str):
        return None
    try:
        parsed = UUID(value)
    except ValueError:
        return None
    return parsed if str(parsed) == value else None


def _without_worker_id(
    logger: logging.Logger | logging.LoggerAdapter[logging.Logger],
) -> logging.Logger | logging.LoggerAdapter[logging.Logger]:
    if not isinstance(logger, PrefectLogAdapter):
        return logger
    extra = dict(logger.extra or {})
    extra.pop("worker_id", None)
    return PrefectLogAdapter(logger.logger, extra=extra)


def _names_the_service(flow_run: FlowRun, deployment: DeploymentResponse, flow: APIFlow) -> bool:
    """Whether these records are the service flow's own deployment, and this run belongs to it."""
    same_records = (
        flow_run.deployment_id is not None
        and deployment.id == flow_run.deployment_id
        and deployment.flow_id == flow_run.flow_id
        and flow.id == flow_run.flow_id
    )
    return (
        same_records
        and flow.name == SERVICE_DEFINITION.flow_name
        and deployment.name == SERVICE_DEFINITION.deployment_name
    )


def _admission_refusal(
    configuration: ProcessJobConfiguration,
    flow_run: FlowRun,
    deployment: DeploymentResponse | None,
    flow: APIFlow | None,
    work_pool: WorkPool | None,
) -> str | None:
    """Name the first way this flow run differs from the service's own deployment, if any.

    Everything that decides what a process child executes is data on the Prefect
    server: the deployment's entrypoint and pull steps, the pool's job template, and
    job variables on the deployment or the flow run. Anyone who can write to that
    server can change any of it, so none of it is trusted by default. A run is
    admitted only when every fact is what `deploy` and `bootstrap` themselves
    write. The reasons are fixed text: they are recorded on the flow run, and the
    values they describe are server data that may carry anything.
    """
    if deployment is None or flow is None or not _names_the_service(flow_run, deployment, flow):
        return "flow run is not a run of the service deployment"
    checks = (
        (
            deployment.entrypoint == SERVICE_DEFINITION.entrypoint,
            "service deployment entrypoint is not the service flow",
        ),
        (not deployment.pull_steps, "service deployment declares pull steps"),
        (
            not deployment.job_variables and not flow_run.job_variables,
            "job variables are not accepted for service flow runs",
        ),
        (
            work_pool is not None and work_pool.base_job_template == ProcessWorker.get_default_base_job_template(),
            "work pool job template is not the process default",
        ),
        # Implied by the two checks above; stated because these two fields are
        # exactly what a child's command line and import root are made of.
        (
            configuration.command == _SERVICE_CHILD_COMMAND and configuration.working_dir is None,
            "resolved child command is not the service default",
        ),
        # Prefect 3.8.6 runs an unconfigured command through its workspace
        # supervisor, which reads the deployment again, can pull storage, extends
        # PYTHONPATH, and can switch to another launcher. Only a configured
        # command is started exactly as admitted here.
        # Prefect's private flag, pinned with Prefect 3.8.6; a rename refuses rather than raises.
        (getattr(configuration, "_command_configured", False), "child command is not configured explicitly"),
    )
    return next((reason for held, reason in checks if not held), None)


class ServiceProcessJobConfiguration(ProcessJobConfiguration):
    """Carry the worker identity generation and admission used to prepare this child."""

    _identity_generation: int | None = PrivateAttr(default=None)
    _admitted: bool = PrivateAttr(default=False)

    def prepare_for_flow_run(  # pylint: disable=too-many-positional-arguments
        self,
        flow_run: FlowRun,
        deployment: DeploymentResponse | None = None,
        flow: APIFlow | None = None,
        work_pool: WorkPool | None = None,
        worker_name: str | None = None,
        worker_id: UUID | None = None,
    ) -> None:
        """Prepare the child, refusing it unless it is the service deployment as applied.

        Raising here reaches Prefect's submission handler before the run is labelled
        or proposed Submitting, and that handler records the refusal as a Crashed
        state. Nothing has started by then.

        The service command is set before Prefect prepares the configuration, so
        Prefect records it as configured and starts it as given rather than through
        its workspace supervisor. A pool or run that sets another command is still
        refused below.
        """
        if self.command is None:
            self.command = _SERVICE_CHILD_COMMAND
        super().prepare_for_flow_run(
            flow_run,
            deployment,
            flow,
            work_pool,
            worker_name,
            worker_id,
        )
        refusal = _admission_refusal(self, flow_run, deployment, flow, work_pool)
        if refusal is not None:
            raise ServiceFlowRunRefusedError(refusal)
        # The child loads the installed service flow, never the deployment record:
        # Prefect's engine otherwise reads the deployment again and runs its pull
        # steps, which anyone who can write to the task manager can change after
        # admission.
        self.env["PREFECT__FLOW_ENTRYPOINT"] = _SERVICE_ENTRYPOINT
        self._admitted = True
        bound, generation = _SUBMISSION_IDENTITY.get()
        self._identity_generation = generation if bound else None


class _IdentityLeaseTaskStatus:
    """Release the identity lease when Prefect reports that the child started."""

    def __init__(self, task_status: TaskStatus[int], lock: asyncio.Lock) -> None:
        self._task_status = task_status
        self._lock = lock
        self._released = False

    def started(self, value: int | None = None) -> None:
        try:
            if value is None:
                cast("TaskStatus[None]", self._task_status).started()
            else:
                self._task_status.started(value)
        finally:
            self.release()

    def release(self) -> None:
        if not self._released:
            self._released = True
            self._lock.release()


class ServiceProcessWorker(ProcessWorker):
    """Resolve this process worker's server record before Prefect can poll runs."""

    job_configuration: type[ServiceProcessJobConfiguration] = ServiceProcessJobConfiguration

    def __init__(  # pylint: disable=too-many-positional-arguments
        self,
        work_pool_name: str,
        work_queues: list[str] | None = None,
        name: str | None = None,
        prefetch_seconds: float | None = None,
        create_pool_if_not_found: bool = True,
        limit: int | None = None,
        heartbeat_interval_seconds: int | None = None,
        *,
        base_job_template: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            work_pool_name=work_pool_name,
            work_queues=work_queues,
            name=name,
            prefetch_seconds=prefetch_seconds,
            create_pool_if_not_found=create_pool_if_not_found,
            limit=limit,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
            base_job_template=base_job_template,
        )
        self._identity_lock = asyncio.Lock()
        self._identity_generation = 0
        self._identity_refresh_requests = 0
        self._identity_refresh_active = False

    @classmethod
    def __dispatch_key__(cls) -> str | None:
        """Keep the explicit service entrypoint separate from Prefect's process key."""
        return "infrahub-sync-service-process"

    async def sync_with_backend(self) -> None:
        """Make readiness depend on a fresh heartbeat followed by identity resolution."""
        self._identity_refresh_requests += 1
        try:
            async with self._identity_lock:
                self._identity_refresh_active = True
                previous_identity = self.backend_id
                previous_generation = self._identity_generation
                self._identity_generation += 1
                self._has_successfully_synced = False
                self.backend_id = None
                if previous_identity is not None and os.environ.get("PREFECT__WORKER_ID") == str(previous_identity):
                    os.environ.pop("PREFECT__WORKER_ID", None)
                try:
                    await super().sync_with_backend()
                finally:
                    self._identity_refresh_active = False
                # A heartbeat that re-resolved the same record changed nothing
                # about this worker's identity, so the generation goes back to
                # what it was and configurations prepared against it stay valid.
                # Without this, every periodic sync refused whatever submission
                # was in flight, and Prefect had already proposed Submitting by
                # then -- so the run was marked Crashed for a replacement that
                # never happened. A genuinely different record keeps the
                # increment, which is what makes a stale child refusable.
                if self.backend_id is not None and self.backend_id == previous_identity:
                    self._identity_generation = previous_generation
        finally:
            self._identity_refresh_requests -= 1

    async def _initialize_after_sync(self) -> None:
        if self._work_pool is None:
            await super()._initialize_after_sync()
            return
        if not await self._resolve_worker_identity():
            # No identity, so nothing may run: `_submission_generation` refuses
            # every submission while `backend_id` is None, and readiness stays
            # false. Raising instead would leave the sync loop, and Prefect
            # terminates the worker on anything it does not recognise as
            # intermittent -- which turned a momentary window into a deployment
            # that never came back. The next heartbeat resolves it.
            return
        await super()._initialize_after_sync()

    async def _resolve_worker_identity(self) -> bool:
        """Install this worker's server UUID, or report that it is not available yet."""
        try:
            await self._refresh_worker_identity()
        except ServiceWorkerIdentityError:
            self._logger.debug("Service worker identity is unavailable; deferring to the next heartbeat.")
            return False
        return True

    async def _refresh_worker_identity(self) -> None:
        """Resolve exactly one online, pool-scoped record and install its UUID."""
        try:
            records = await self._read_worker_records()
            matches = [record for record in records if record.name == self.name]
            if len(matches) != 1:
                raise ServiceWorkerIdentityError(_IDENTITY_ERROR)
            record = matches[0]
            worker_id = _canonical_uuid(record.id)
            record_pool_id = _canonical_uuid(record.work_pool_id)
            expected_pool_id = _canonical_uuid(self.work_pool.id)
            if (
                worker_id is None
                or record_pool_id is None
                or expected_pool_id is None
                or record_pool_id != expected_pool_id
                or record.status != WorkerStatus.ONLINE
            ):
                raise ServiceWorkerIdentityError(_IDENTITY_ERROR)
        except (httpx.HTTPError, ObjectNotFound, AttributeError, TypeError, ValueError):
            raise ServiceWorkerIdentityError(_IDENTITY_ERROR) from None
        self._record_worker_id(worker_id)

    def _record_worker_id(self, remote_id: UUID) -> None:
        super()._record_worker_id(remote_id)
        self._logger = _without_worker_id(self._logger)

    def get_flow_run_logger(self, flow_run: FlowRun) -> PrefectLogAdapter:
        logger = _without_worker_id(super().get_flow_run_logger(flow_run))
        return cast("PrefectLogAdapter", logger)

    def _submission_generation(self) -> int | None:
        if self._identity_refresh_requests or self._identity_refresh_active or self.backend_id is None:
            return None
        return self._identity_generation

    async def get_and_submit_flow_runs(self) -> list[FlowRun]:
        generation = self._submission_generation()
        if generation is None:
            self._logger.debug("Service worker identity is unavailable; skipping flow run submission.")
            self._last_polled_time = datetime.now(timezone.utc)
            return []
        token = _SUBMISSION_IDENTITY.set((True, generation))
        try:
            return await super().get_and_submit_flow_runs()
        finally:
            _SUBMISSION_IDENTITY.reset(token)

    async def _submit_run_and_capture_errors(
        self,
        flow_run: FlowRun,
        task_status: TaskStatus[int | Exception] | None = None,
    ) -> ProcessWorkerResult | Exception:
        bound, generation = _SUBMISSION_IDENTITY.get()
        token = None
        if not bound:
            generation = self._submission_generation()
            token = _SUBMISSION_IDENTITY.set((True, generation))
        try:
            return cast(
                "ProcessWorkerResult | Exception",
                await super()._submit_run_and_capture_errors(flow_run, task_status),
            )
        finally:
            if token is not None:
                _SUBMISSION_IDENTITY.reset(token)

    def _validate_child_identity(self, configuration: ProcessJobConfiguration) -> None:
        if not isinstance(configuration, ServiceProcessJobConfiguration):
            raise ServiceWorkerIdentityError(_IDENTITY_ERROR)
        # Only identity facts belong here. The two refresh flags are lock state:
        # `_identity_refresh_active` is set and cleared inside `_identity_lock`,
        # so it is always false for this caller, and a non-zero
        # `_identity_refresh_requests` says a heartbeat is queued -- which
        # cannot change anything until we release, and says nothing about
        # whether the identity we hold right now is the one we hold. Treating
        # either as invalidating refused children whose identity was current.
        if (
            self.backend_id is None
            or configuration._identity_generation != self._identity_generation
            or configuration.env.get("PREFECT__WORKER_ID") != str(self.backend_id)
        ):
            raise ServiceWorkerIdentityError(_IDENTITY_ERROR)

    async def run(
        self,
        flow_run: FlowRun,
        configuration: ProcessJobConfiguration,
        task_status: TaskStatus[int] | None = None,
    ) -> ProcessWorkerResult:
        """Start a child once it is admitted and its prepared identity is still current."""
        # Admission is decided while the configuration is prepared; this refuses any
        # configuration that reached `run` without going through that step.
        if not isinstance(configuration, ServiceProcessJobConfiguration) or not configuration._admitted:
            raise ServiceFlowRunRefusedError(_NOT_ADMITTED)
        # Waiting is the deferral. A refresh owns `_identity_lock` for its whole
        # window, so acquiring it holds this submission until the refresh has
        # finished and then revalidates against whatever it left. Refusing here
        # instead refused a submission that was valid immediately before and
        # immediately after, with no wait and no revalidation -- and Prefect had
        # already proposed Submitting, so the run was marked Crashed for a
        # replacement that never happened.
        await self._identity_lock.acquire()
        effective_status: TaskStatus[int] = task_status if task_status is not None else anyio.TASK_STATUS_IGNORED
        lease = _IdentityLeaseTaskStatus(effective_status, self._identity_lock)
        try:
            self._validate_child_identity(configuration)
            return await self._start_child(flow_run, configuration, lease)
        finally:
            lease.release()

    async def _start_child(
        self,
        flow_run: FlowRun,
        configuration: ProcessJobConfiguration,
        task_status: TaskStatus[int],
    ) -> ProcessWorkerResult:
        """Start the admitted child; tests replace this to observe what would start.

        Prefect 3.8.6's `ProcessWorker.run` for a configured command, with one change:
        the crash and cancellation hooks that run in this parent resolve the installed
        service flow. Prefect's own resolver reads the deployment record again and runs
        its pull steps in this process, and that record is server data anyone who can
        write to the task manager can change after admission.
        """
        with tempfile.TemporaryDirectory(suffix="prefect") as working_dir, warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            async with FlowRunExecutorContext() as context:
                starter = EngineCommandStarter(
                    command=configuration.command,
                    cwd=Path(working_dir),
                    env=configuration.env,
                    stream_output=configuration.stream_output,
                    control_channel=context.control_channel,
                )
                executor = context.create_executor(
                    flow_run,
                    starter,
                    resolve_flow=_installed_service_flow,
                    propose_submitting=False,
                )
                execution: FlowRunExecutionResult | None = None

                async def execute(*, task_status: Any = anyio.TASK_STATUS_IGNORED) -> None:
                    nonlocal execution
                    execution = await executor.submit(task_status=task_status)

                async with anyio.create_task_group() as task_group:
                    handle = await task_group.start(execute)
                    if handle.pid is None:
                        msg = "flow run process has no PID"
                        raise RuntimeError(msg)
                    task_status.started(handle.pid)
        if execution is None or execution.status_code is None:
            msg = "failed to start the flow run process"
            raise RuntimeError(msg)
        return ProcessWorkerResult(status_code=execution.status_code, identifier=str(execution.handle.pid))

    async def _read_worker_records(self) -> list[Worker]:
        records: list[Worker] = []
        offset = 0
        while True:
            page = await self.client.read_workers_for_work_pool(
                self._work_pool_name,
                offset=offset,
                limit=_WORKER_PAGE_SIZE,
            )
            if not isinstance(page, list):
                raise TypeError
            records.extend(page)
            if len(page) < _WORKER_PAGE_SIZE:
                return records
            offset += _WORKER_PAGE_SIZE


def _pool_argument(argv: Sequence[str] | None = None) -> str:
    parser = argparse.ArgumentParser(description="Start an Infrahub Sync service process worker")
    parser.add_argument("--pool", required=True, help="existing Prefect process work pool")
    arguments = parser.parse_args(argv)
    pool = arguments.pool
    if not isinstance(pool, str) or not pool.strip():
        parser.error("--pool must be non-empty")
    return pool


@contextmanager
def neutral_working_directory() -> Iterator[Path]:
    """Run this parent from a fresh empty directory, and remove it on the way out.

    Prefect prepends the process working directory to ``sys.path`` every time it resolves
    a deployment's module entrypoint, and this parent does resolve it: a ``ProcessWorker``
    builds a ``Runner`` in this process, and that runner imports the flow to run
    ``on_crashed`` hooks once a child dies. A parent started inside a source tree would
    import that copy of the package rather than the installed distribution, which is the
    checkout dependence this service does not have. An empty directory holds nothing to
    import, so the installed distribution is the only answer available -- for the whole
    lifetime of the parent, hook inspection after a crash included.
    """
    previous = Path.cwd()
    root = Path(mkdtemp(prefix="infrahub-sync-worker-"))
    os.chdir(root)
    try:
        yield root
    finally:
        os.chdir(previous)
        rmtree(root, ignore_errors=True)


def refuse_start_without_sync_schema() -> None:
    """Stop the worker when Infrahub lacks the Sync schema extension this release needs.

    Skipped when the worker is given no Infrahub, as in local use where configurations
    live in the product store. An unreachable Infrahub is tolerated, as the API does.
    """
    try:
        settings = PlatformSettings.from_environment()
    except PlatformSettingsError:
        _logger.info("no Infrahub is configured; runs read configurations from Sync's own store")
        return
    client = service_client_sync(settings)
    try:
        require_sync_schema_sync(client, default_branch_sync(client))
    except SyncSchemaMissingError as error:
        refusal = f"infrahub-sync worker refused to start: {error}"
        raise SystemExit(refusal) from None
    except AuthenticationError:
        refusal = f"infrahub-sync worker refused to start: {SERVICE_ACCOUNT_REFUSED}"
        raise SystemExit(refusal) from None
    except InfrahubSdkError as error:
        _logger.warning(
            "Infrahub could not be reached at startup (%s); the Sync schema was not checked", type(error).__name__
        )


def main(argv: Sequence[str] | None = None) -> int:
    """Start one fail-closed service process worker."""
    pool = _pool_argument(argv)
    refuse_start_unless_prefect_ready(WORKER_SERVICE)
    refuse_start_without_sync_schema()
    # Entered before the worker exists, so nothing this parent imports later -- the
    # runner, its crash hooks, or an adapter -- can resolve out of a source tree.
    with neutral_working_directory():
        worker = ServiceProcessWorker(
            work_pool_name=pool,
            name=service_worker_name(),
            create_pool_if_not_found=False,
        )
        asyncio.run(worker.start())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
