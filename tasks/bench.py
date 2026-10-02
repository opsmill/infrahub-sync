"""Manual S/M/L benchmark cells against disposable NetBox and pinned Infrahub stacks."""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shlex
import shutil
import time
from typing import TYPE_CHECKING
from uuid import uuid4

import structlog
import toml
import yaml
from invoke import Context, task
from rich.console import Console
from rich.table import Table

from development.bench.records import (
    ACTION_NAMES,
    ResultRecord,
    count_delta,
    expected_actions,
    mapped_kinds,
    medians,
    parse_v2_summary,
    stage_seconds,
    validate_result,
)
from development.bench.runtime import (
    LIMIT_SECONDS,
    BenchmarkError,
    MemorySampler,
    QuietContext,
    docker_memory_mib,
    machine_info,
    measured_process,
    output,
)
from development.bench.v2 import generation_command, release_environment, sync_command
from development.netbox.datasets.netbox_api import NetboxAPI
from development.netbox.datasets.tier_data import FOUNDATION_COUNTS, TIER_COUNTS, validate_tier
from infrahub_sync.client import (
    CancelRunRequest,
    ConfigMutationRequest,
    CreateRunRequest,
    RunWaitTimeoutError,
    SyncClient,
    SyncClientError,
)

from . import dev, netbox, preview

if TYPE_CHECKING:
    from pathlib import Path

ROOT = netbox.REPO_ROOT
STATE = netbox.STATE_DIR / "bench"
RESULTS = netbox.STATE_DIR / "benchmarks" / "results.jsonl"
INFRAHUB_VERSION = "1.11.3"
INFRAHUB_IMAGE = "registry.opsmill.io/opsmill/infrahub"
log = structlog.get_logger()


def cell_options(  # noqa: PLR0913, PLR0917 -- cell coordinates
    line: str, tier: str, scenario: str, variant: str, repetitions: int, v2_ref: str
) -> None:
    """Validate all options before any destructive stack operation."""
    validate_tier(tier)
    if line not in {"v2", "v3"} or scenario not in {"cold", "warm", "changed"} or repetitions < 1:
        msg = "expected line v2/v3, scenario cold/warm/changed, and positive repetitions"
        raise ValueError(msg)
    variants = {"full"} if line == "v3" else {"full", "parallel", "incremental"}
    if variant not in variants or (variant == "incremental" and scenario != "changed"):
        msg = "incremental is only for v2 changed; v3 supports only full"
        raise ValueError(msg)
    if line == "v2" and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", v2_ref):
        msg = "pass a named main release tag or commit with --v2-ref"
        raise ValueError(msg)
    if line == "v3" and not os.environ.get("INFRAHUB_SYNC_API_TOKEN"):
        msg = "set INFRAHUB_SYNC_API_TOKEN for the local development stack"
        raise ValueError(msg)
    netbox.verify_tier_dump(tier)


class CellStack:
    """Manage only disposable benchmark stacks, with quiet legacy task calls."""

    def __init__(self) -> None:
        self.context = QuietContext()
        self.netbox_env = netbox.load_netbox_env()
        self.preview_env = preview.load_preview_env() | {
            "VERSION": INFRAHUB_VERSION,
            "INFRAHUB_DOCKER_IMAGE": INFRAHUB_IMAGE,
            "INFRAHUB_DOCKER_IMAGE_DIGEST": "",
            "COMPOSE_PROJECT_NAME": "infrahub-sync-benchmark",
        }
        self.infrahub_url = preview.preview_urls(self.preview_env)["infrahub"]
        self.infrahub_token = self.preview_env["INFRAHUB_INITIAL_ADMIN_TOKEN"]
        self.worker = ""
        self.deadline = time.monotonic() + LIMIT_SECONDS
        self.context.deadline = self.deadline
        self.started_sync = False

    def remaining(self) -> float:
        """Enforce one six-hour envelope for preparation, baseline, and measured sync."""
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            msg = "cell exceeded the six-hour limit"
            raise TimeoutError(msg)
        return remaining

    def compose(self, arguments: str) -> None:
        """Use the existing preview files for the isolated pinned destination."""
        self.context.config.run.timeout = self.remaining()
        preview._compose(self.context, arguments, self.preview_env)  # noqa: SLF001 -- reuse the existing stack lifecycle

    def reset(self, tier: str) -> None:
        """Restore NetBox and recreate an empty pinned destination before each repetition."""
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            netbox.restore(self.context, tier=tier)
            self.compose("down --volumes --remove-orphans")
            services = yaml.safe_load((preview.DEV_DIR / "docker-compose.infrahub.yml").read_text(encoding="utf-8"))[
                "services"
            ]
            # Schema loading needs the Infrahub workers, which are not server dependencies.
            selected = " ".join(shlex.quote(name) for name in services)
            self.compose(f"up --detach --wait --wait-timeout {netbox.WAIT_TIMEOUT_SECONDS} {selected}")
        env = os.environ | {"INFRAHUB_ADDRESS": self.infrahub_url, "INFRAHUB_API_TOKEN": self.infrahub_token}
        output(
            [
                str(ROOT / ".venv/bin/infrahubctl"),
                "schema",
                "load",
                "--wait",
                "120",
                str(ROOT / "tests/data/nightly_schema/traditional-infrastructure-sot"),
            ],
            cwd=ROOT,
            env=env,
            timeout=self.remaining(),
        )

    def destination_identity(self) -> tuple[str, str, str | None]:
        """Verify the running server version and record its immutable Docker image identity."""
        import docker  # noqa: PLC0415 -- manual benchmark boundary
        from infrahub_sdk import InfrahubClientSync  # noqa: PLC0415

        client = InfrahubClientSync(address=self.infrahub_url, config={"api_token": self.infrahub_token})
        version = client.get_version()
        if version != INFRAHUB_VERSION:
            msg = "running Infrahub version differs from the benchmark pin"
            raise BenchmarkError(msg)
        with contextlib.closing(docker.from_env(timeout=5)) as engine:
            containers = engine.containers.list(
                filters={
                    "label": [
                        "com.docker.compose.project=" + self.preview_env["COMPOSE_PROJECT_NAME"],
                        "com.docker.compose.service=infrahub-server",
                    ]
                }
            )
            if len(containers) != 1:
                msg = "expected exactly one benchmark Infrahub server"
                raise BenchmarkError(msg)
            image = containers[0].image
            digest = next(
                (value for value in image.attrs.get("RepoDigests", []) if value.startswith(INFRAHUB_IMAGE + "@")), None
            )
            return version, image.id, digest

    def start_sync(self) -> None:
        """Build the current checkout and start the existing API/worker tasks quietly."""
        # The existing dev task owns its fixed project. Refuse another caller's stack.
        if netbox.dev_worker_containers(self.context):
            msg = "the development Sync stack has existing containers; run invoke destroy before benchmarking (removes volumes)"
            raise BenchmarkError(msg)
        env = {
            "INFRAHUB_SYNC_CREDENTIAL_NETBOX_TOKEN": netbox.netbox_token(self.netbox_env),
            "INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN": self.infrahub_token,
        }
        self.context.config.run.env = env
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.started_sync = True
            dev.build(self.context)
            dev.start(self.context)
        workers = netbox.dev_worker_containers(self.context)
        if len(workers) != 1:
            msg = "expected exactly one benchmark worker"
            raise BenchmarkError(msg)
        self.worker = workers[0]
        self.context.run(
            f"docker network connect {shlex.quote(self.preview_env['COMPOSE_PROJECT_NAME'] + '_default')} {shlex.quote(self.worker)}"
        )

    def package(self, *, worker: bool) -> dict:
        """Copy the shipped package, replacing only local connection URLs."""
        destination = self.infrahub_url
        if worker:
            # Resolve the server's network alias from the existing Compose service name.
            services = yaml.safe_load((preview.DEV_DIR / "docker-compose.infrahub.yml").read_text(encoding="utf-8"))[
                "services"
            ]
            # The source file lists infrastructure ports too; choose the HTTP server by its healthcheck.
            server = next(
                name for name, service in services.items() if "api/config" in str(service.get("healthcheck", ""))
            )
            destination = netbox.SHIPPED_INFRAHUB_URL.replace("localhost", server)
        text = netbox.local_package_text(
            netbox.SHIPPED_PACKAGE.read_text(encoding="utf-8"),
            netbox.WORKER_NETBOX_URL if worker else netbox.netbox_url(self.netbox_env),
            destination,
        )
        return yaml.safe_load(text)

    def counts(self, mapping: dict[str, list[str]]) -> tuple[dict[str, int], dict[str, int]]:
        """Read all source endpoint totals and mapped destination kind totals."""
        from infrahub_sdk import InfrahubClientSync  # noqa: PLC0415 -- optional SDK boundary

        with NetboxAPI(netbox.netbox_url(self.netbox_env), netbox.netbox_token(self.netbox_env)) as api:
            source = {kind: api.count(kind) for kind in (*TIER_COUNTS["S"], *FOUNDATION_COUNTS)}
        client = InfrahubClientSync(address=self.infrahub_url, config={"api_token": self.infrahub_token})
        destination = {kind: client.count(kind=kind) for kinds in mapping.values() for kind in kinds}
        return source, destination

    def memory(self) -> float:
        """Read Docker stats without the two-cycle CPU wait, for one-second sampling."""
        import docker  # noqa: PLC0415 -- only manual v3 cells need the Docker SDK
        from docker.errors import DockerException  # noqa: PLC0415

        try:
            with contextlib.closing(docker.from_env(timeout=5)) as client:
                statistics = client.api.stats(self.worker, stream=False, one_shot=True)
            return docker_memory_mib(statistics)
        except (DockerException, KeyError, TypeError, ValueError):
            msg = "worker Docker stats sampling failed"
            raise BenchmarkError(msg) from None

    def close(self) -> None:
        """Remove stacks and volumes created by this cell, including the disposable source."""
        self.deadline = time.monotonic() + 300
        self.context.deadline = self.deadline
        self.context.config.run.timeout = 300
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            if self.started_sync:
                dev.destroy(self.context)
            self.compose("down --volumes --remove-orphans")
            netbox.down(self.context)


def v2_environment(ref: str) -> tuple[Path, str, str]:
    """Build one isolated release worktree/environment and record its immutable identity."""
    output(["git", "fetch", "origin", "main", "--tags"], cwd=ROOT)
    commit = output(["git", "rev-parse", "--verify", f"{ref}^{{commit}}"], cwd=ROOT)
    output(["git", "merge-base", "--is-ancestor", commit, "origin/main"], cwd=ROOT)
    directory = STATE / f"v2-{ref}"
    if not directory.exists():
        output(["git", "worktree", "add", "--detach", str(directory), commit], cwd=ROOT)
    if output(["git", "rev-parse", "HEAD"], cwd=directory) != commit:
        msg = "cached v2 environment differs from the requested reference"
        raise BenchmarkError(msg)
    env = release_environment(directory)
    output(["uv", "sync", "--extra", "dev"], cwd=directory, env=env)
    output(
        ["uv", "pip", "install", "--python", str(directory / ".venv/bin/python"), "pynetbox>=7.6,<8"],
        cwd=directory,
        env=env,
    )
    version = toml.loads((directory / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    return directory, version, commit


def v2_inputs(stack: CellStack, directory: Path) -> tuple[Path, dict[str, str]]:
    """Write unchanged mapping entries and supply local credentials only in the environment."""
    config_dir = directory / ".benchmark-mapping"
    config_dir.mkdir(exist_ok=True)
    config = stack.package(worker=False)["configuration"]
    # Only transport/credential settings are runtime-specific; mapping entries are copied unchanged.
    config["source"]["settings"].pop("token")
    config["destination"]["settings"].pop("token")
    (config_dir / "config.yml").write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    env = release_environment(directory) | {
        "NETBOX_TOKEN": netbox.netbox_token(stack.netbox_env),
        "NETBOX_ADDRESS": netbox.netbox_url(stack.netbox_env),
        "NETBOX_URL": netbox.netbox_url(stack.netbox_env),
        "INFRAHUB_API_TOKEN": stack.infrahub_token,
        "INFRAHUB_ADDRESS": stack.infrahub_url,
        "INFRAHUB_URL": stack.infrahub_url,
    }
    return config_dir, env


def prepare_v2(stack: CellStack, directory: Path) -> None:
    """Generate the release's adapters before timing, without adapting its mapping."""
    config_dir, env = v2_inputs(stack, directory)
    path = config_dir / "config.yml"
    original = path.read_text(encoding="utf-8")
    configuration = yaml.safe_load(original)
    # The legacy generate command reads its SDK credential only from settings.
    # Supply the environment credential privately for generation, then remove it.
    configuration["destination"]["settings"]["token"] = env["INFRAHUB_API_TOKEN"]
    path.chmod(0o600)
    try:
        path.write_text(yaml.safe_dump(configuration, sort_keys=False), encoding="utf-8")
        output(generation_command(directory, config_dir), cwd=directory, env=env, timeout=stack.remaining())
    except BenchmarkError:
        msg = "v2 adapter generation failed; the unchanged current mapping may be incompatible with this release"
        raise BenchmarkError(msg) from None
    finally:
        path.write_text(original, encoding="utf-8")


def v2_sync(stack: CellStack, directory: Path, variant: str, timeout: float) -> tuple[str, float, float]:
    """Run the unchanged mapping copy; compatibility failures are invalid benchmark cells."""
    config_dir, env = v2_inputs(stack, directory)
    argv = sync_command(directory, config_dir, variant)
    return measured_process(argv, directory, env, timeout)


def v3_sync(stack: CellStack, registered: tuple[str, int], record: ResultRecord, timeout: float) -> None:
    """Submit confirmed sync through the API, then verify plan and applied summaries."""

    from infrahub_sync.service.service import PLAN_ARTIFACT_ID  # noqa: PLC0415 -- public plan publication boundary

    with SyncClient(dev.API_URL, os.environ["INFRAHUB_SYNC_API_TOKEN"]) as client:
        started = time.monotonic()
        accepted = client.sync(
            CreateRunRequest(
                operation="sync",
                config_id=registered[0],
                registry_version=registered[1],
                confirm_writes=True,
                reason="manual benchmark cell",
            ),
            uuid4().hex,
        )
        record.run_id = accepted.run.run_id
        with MemorySampler(stack.memory) as sampler:
            try:
                finished = client.wait_for_run(
                    accepted, timeout=max(0.001, timeout - (time.monotonic() - started)), poll_interval=1
                )
                record.wall_seconds = time.monotonic() - started
            except RunWaitTimeoutError:
                record.peak_rss_mb = sampler.peak or None
                with contextlib.suppress(SyncClientError):
                    client.cancel_run(record.run_id, CancelRunRequest(reason="benchmark six-hour limit"), uuid4().hex)
                msg = "sync exceeded the six-hour cell limit"
                raise TimeoutError(msg) from None
        record.peak_rss_mb = sampler.result()
        plan = client.get_plan(record.run_id)
        actions = {action: {} for action in ACTION_NAMES}
        for operation in plan.operations:
            bucket = actions[operation.action]
            bucket[operation.kind] = bucket.get(operation.kind, 0) + 1
        if sum(actions["delete"].values()) != plan.summary.deletes_not_executed:
            msg = "plan's nonexecuted delete disclosure differs from its operations"
            raise BenchmarkError(msg)
        applied = client.get_results(record.run_id).results["summary"]
        if applied != {action: sum(counts.values()) for action, counts in actions.items()}:
            msg = "apply summary differs from the saved plan"
            raise BenchmarkError(msg)
        # The current apply summary counts saved operations, including undispatched deletes.
        # Use the plan's explicit disclosure to report only executed actions.
        record.skipped_deletes = actions["delete"]
        actions["delete"] = {}
        record.actions = actions
        record.action_evidence = "run-plan-and-apply-summary"
        checkpoint = next(ref for ref in finished.run.artifact_refs if ref.artifact_id == PLAN_ARTIFACT_ID)
        if finished.run.finished_at is None:
            msg = "finished run has no timestamp"
            raise BenchmarkError(msg)
        record.plan_seconds, record.apply_seconds = stage_seconds(
            finished.run.started_at, checkpoint.created_at, finished.run.finished_at
        )


@task(name="run")
def run_cell(  # noqa: PLR0913, PLR0917, PLR0912, PLR0915 -- one record per repetition
    _context: Context,
    line: str = "v3",
    tier: str = "S",
    scenario: str = "cold",
    variant: str = "full",
    repetitions: int = 1,
    v2_ref: str = "",
) -> None:
    """Run a destructive, manual cell, restoring both databases for every repetition."""
    cell_options(line, tier, scenario, variant, repetitions, v2_ref)
    STATE.mkdir(parents=True, exist_ok=True)
    import fcntl  # noqa: PLC0415 -- Linux benchmark host

    with (STATE / "runner.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            msg = "another benchmark cell is running"
            raise BenchmarkError(msg) from None
        directory, version, commit = (
            ROOT,
            toml.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"],
            output(["git", "rev-parse", "HEAD"], cwd=ROOT)
            + ("-dirty" if output(["git", "status", "--porcelain"], cwd=ROOT) else ""),
        )
        for repetition in range(1, repetitions + 1):
            record = ResultRecord(
                line,
                v2_ref if line == "v2" else version,
                commit,
                tier,
                scenario,
                variant,
                repetition,
                machine=machine_info(),
            )
            stack = CellStack()
            mapping = None
            try:
                if line == "v2":
                    directory, version, commit = v2_environment(v2_ref)
                    record.version, record.commit = version + " (" + v2_ref + ")", commit
                    shutil.rmtree(directory / ".infrahub-sync-cache" / "from-netbox", ignore_errors=True)
                stack.reset(tier)
                record.infrahub_version, record.infrahub_image_id, record.infrahub_image_digest = (
                    stack.destination_identity()
                )
                mapping = mapped_kinds(stack.package(worker=False)["configuration"])
                if line == "v2":
                    prepare_v2(stack, directory)
                registered = ("", 0)
                if line == "v3":
                    stack.start_sync()
                    with SyncClient(dev.API_URL, os.environ["INFRAHUB_SYNC_API_TOKEN"]) as client:
                        resource = client.register_config(
                            ConfigMutationRequest(
                                package=stack.package(worker=True), reason="manual benchmark package"
                            ),
                            uuid4().hex,
                        )
                        registered = resource.configuration.config_id, resource.version.registry_version
                if scenario != "cold":
                    if line == "v3":
                        baseline = ResultRecord(line, version, commit, tier, "cold", "full", repetition)
                        v3_sync(stack, registered, baseline, stack.remaining())
                    else:
                        v2_sync(stack, directory, "full" if variant == "incremental" else variant, stack.remaining())
                    _, counts = stack.counts(mapping)
                    validate_result(tier, "cold", counts, {}, mapping)
                expected = None
                if scenario == "changed":
                    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                        netbox.change(stack.context, tier=tier)
                    expected = expected_actions(
                        json.loads(
                            (netbox.STATE_DIR / "changes" / f"{tier}.expected.json").read_text(encoding="utf-8")
                        ),
                        mapping,
                    )
                _, before = stack.counts(mapping)
                if line == "v3":
                    v3_sync(stack, registered, record, stack.remaining())
                else:
                    text, record.wall_seconds, record.peak_rss_mb = v2_sync(
                        stack, directory, variant, stack.remaining()
                    )
                    record.actions = parse_v2_summary(text, set(before)) or {}
                    record.action_evidence = "printed-summary" if record.actions else "count-delta"
                record.netbox_counts, record.infrahub_counts = stack.counts(mapping)
                if line == "v2" and not record.actions:
                    record.actions = count_delta(before, record.infrahub_counts)
                    if scenario in {"warm", "changed"}:
                        msg = "v2 summary unavailable: count deltas cannot prove updates or zero warm actions"
                        raise BenchmarkError(msg)  # noqa: TRY301 -- fail this cell before recording a time
                stack.remaining()
                if scenario == "changed" and line == "v3" and expected and expected["delete"]:
                    msg = "v3 does not execute planned deletes; the expected changed actions cannot be met"
                    raise BenchmarkError(msg)  # noqa: TRY301 -- do not report unexecuted deletes as applied
                validate_result(tier, scenario, record.infrahub_counts, record.actions, mapping, expected)
                record.status = "ok"
            except (TimeoutError, RunWaitTimeoutError):
                try:
                    stack.remaining()
                except TimeoutError:
                    record.status, record.error = "timed_out", "cell exceeded its time limit"
                else:
                    record.error = (
                        "benchmark command timed out before the six-hour cell limit; provider output suppressed"
                    )
            except Exception as exc:  # noqa: BLE001 -- suppress all provider causes at the result/output boundary
                if isinstance(exc, BenchmarkError):
                    try:
                        stack.remaining()
                    except TimeoutError:
                        record.status, record.error = "timed_out", "cell exceeded its time limit"
                record.error = record.error or (
                    str(exc)
                    if isinstance(exc, BenchmarkError)
                    else "cell failed during setup, sync, measurement, or validation; provider output suppressed"
                )
            finally:
                if mapping is not None and not record.infrahub_counts:
                    # Failed cells retain available count evidence; failure still clears all times.
                    with contextlib.suppress(Exception):
                        record.netbox_counts, record.infrahub_counts = stack.counts(mapping)
                try:
                    stack.close()
                except (BenchmarkError, TimeoutError):
                    record.status, record.error = (
                        "failed",
                        "benchmark stack cleanup failed; remove the disposable stacks manually",
                    )
                record.append(RESULTS)
            log.info(
                "benchmark_cell",
                line=line,
                tier=tier,
                scenario=scenario,
                variant=variant,
                repetition=repetition,
                status=record.status,
                error=record.error,
            )


@task
def report(_context: Context) -> None:
    """Print medians of valid samples only, with separate line/version identities."""
    table = Table(title="Valid benchmark medians (seconds)")
    for label in (
        "Tier",
        "Scenario",
        "v2 variant",
        "v2 version/commit",
        "v2 seconds",
        "v3 version/commit",
        "v3 seconds",
    ):
        table.add_column(label)
    for row in medians(RESULTS):
        table.add_row(
            row["tier"],
            row["scenario"],
            row["variant"],
            row["v2_version"] or "",
            f"{row['v2_seconds']:.3f}" if row["v2_seconds"] is not None else "",
            row["v3_version"] or "",
            f"{row['v3_seconds']:.3f}" if row["v3_seconds"] is not None else "",
        )
    Console().print(table)
