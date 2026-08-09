from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Union, cast

import yaml
from diffsync.store.local import LocalStore
from diffsync.store.redis import RedisStore
from infrahub_sdk import Config

from infrahub_sync import SyncAdapter, SyncConfig, SyncInstance
from infrahub_sync.cache.paths import run_dir as stored_run_dir
from infrahub_sync.generator import render_template
from infrahub_sync.plan.errors import PlanVerificationError
from infrahub_sync.plan.reader import read_plan_artifact_bytes
from infrahub_sync.plan.verify import destination_binding_failure
from infrahub_sync.plugin_loader import PluginLoader, PluginLoadError
from infrahub_sync.potenda import Potenda

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import MutableMapping

    from diffsync import Adapter
    from infrahub_sdk.schema import GenericSchema, NodeSchema

    from infrahub_sync.plan.models import ApplyRecord
    from infrahub_sync.plan.reader import RawPlanArtifact


def find_missing_schema_model(
    sync_instance: SyncInstance,
    schema: MutableMapping[str, Union[NodeSchema, GenericSchema]],
) -> list[str]:
    missing_schema_models = []
    for item in sync_instance.schema_mapping:
        match_found = any(item.name == node.kind for node in schema.values())

        if not match_found:
            missing_schema_models.append(item.name)

    return missing_schema_models


def render_adapter(
    sync_instance: SyncInstance,
    schema: MutableMapping[str, Union[NodeSchema, GenericSchema]],
) -> list[tuple[str, str]]:
    files_to_render = (
        ("diffsync_models.j2", "sync_models.py"),
        ("diffsync_adapter.j2", "sync_adapter.py"),
    )
    rendered_files = []
    for adapter in [sync_instance.source, sync_instance.destination]:
        output_dir_path = Path(sync_instance.directory, adapter.name)
        if not output_dir_path.is_dir():
            output_dir_path.mkdir(exist_ok=True)

        init_file_path = output_dir_path / "__init__.py"
        if not init_file_path.exists():
            init_file_path.touch()

        for item in files_to_render:
            render_template(
                template_file=Path(item[0]),
                output_dir=output_dir_path,
                output_file=Path(item[1]),
                context={"schema": schema, "adapter": adapter, "config": sync_instance},
            )
            output_file_path = output_dir_path / item[1]
            rendered_files.append((item[0], output_file_path))

    return rendered_files


def import_adapter(sync_instance: SyncInstance, adapter: SyncAdapter):
    # ALWAYS try the generated adapter class first
    if adapter.name and sync_instance.directory:
        directory = Path(sync_instance.directory)
        adapter_file_path = directory / f"{adapter.name}" / "sync_adapter.py"
        adapter_name = f"{PluginLoader().camelize(adapter.name)}Sync"

        if adapter_file_path.exists():
            # Add directory to path so relative imports work
            if str(directory) not in sys.path:
                sys.path.insert(0, str(directory))

            try:
                # Import the generated adapter module
                spec = importlib.util.spec_from_file_location(f"{adapter.name}.adapter", str(adapter_file_path))
                if spec is not None and spec.loader is not None:
                    adapter_module = importlib.util.module_from_spec(spec)
                    sys.modules[f"{adapter.name}.adapter"] = adapter_module
                    spec.loader.exec_module(adapter_module)

                    # Get the generated adapter class
                    generated_class = getattr(adapter_module, adapter_name, None)
                    if generated_class:
                        return generated_class
            except (ImportError, AttributeError, SyntaxError, TypeError, ValueError, OSError) as exc:
                logger.warning("Could not load generated adapter from %s: %s", adapter_file_path, exc)

    # Fall back to the plugin loader
    # The "sync" classes could be declared into a separate module
    adapter_paths = sync_instance.adapters_path or []
    loader = PluginLoader.from_env_and_args(adapter_paths=adapter_paths)

    # If explicit adapter spec is provided, use it
    if adapter.adapter:
        try:
            # Try loading the explicitly specified adapter
            adapter_class = loader.resolve(adapter.adapter)
            logger.debug("Using directly specified adapter class: %s", adapter_class.__name__)
        except PluginLoadError as exc:
            msg = f"Failed to load adapter '{adapter.adapter}': {exc}"
            raise ImportError(msg) from exc
        else:
            return adapter_class

    else:
        try:
            return loader.resolve(adapter.name)
        except PluginLoadError:
            return None


def get_all_sync(directory: str | None = None) -> list[SyncInstance]:
    results = []
    search_directory = Path(directory) if directory else Path(__file__).parent
    config_files = search_directory.glob("**/config.yml")

    for config_file in config_files:
        with config_file.open("r") as file:
            directory_name = str(config_file.parent)
            config_data = yaml.safe_load(file)
            SyncConfig(**config_data)
            results.append(SyncInstance(**config_data, directory=directory_name))

    return results


def get_instance(
    name: str | None = None,
    config_file: str | None = "config.yml",
    directory: str | None = None,
) -> SyncInstance | None:
    if name:
        all_sync_instances = get_all_sync(directory=directory)
        for item in all_sync_instances:
            if item.name == name:
                return item
        return None

    if config_file is None:
        # TODO: Log or raise an Error/Warning
        return None

    # Check `directory is None` (not truthiness) so an empty string still collapses to Path(config_file).
    config_file_path: Path = (
        Path(config_file) if Path(config_file).is_absolute() or directory is None else Path(directory, config_file)
    )

    if config_file_path:
        directory_path = config_file_path.parent
        if config_file_path.is_file():
            with config_file_path.open("r", encoding="UTF-8") as file:
                config_data = yaml.safe_load(file)
                return SyncInstance(**config_data, directory=str(directory_path))

    return None


def get_potenda_from_instance(
    sync_instance: SyncInstance,
    branch: str | None = None,
    show_progress: bool | None = None,
    verbosity: int | None = None,
    run_id: str | None = None,
    continue_on_error: bool = False,
    concurrent_load: bool = True,
) -> Potenda:
    """Create and return a Potenda instance based on the provided SyncInstance.

    When ``run_id`` is None, a fresh sortable identifier is allocated via
    ``generate_run_id()`` so each invocation gets its own cache directory.
    """
    source = import_adapter(sync_instance=sync_instance, adapter=sync_instance.source)
    destination = import_adapter(sync_instance=sync_instance, adapter=sync_instance.destination)

    if not source or not destination:
        missing = []
        if not source:
            missing.append(f"source adapter '{sync_instance.source.name}'")
        if not destination:
            missing.append(f"destination adapter '{sync_instance.destination.name}'")
        msg = f"Could not load the following adapter(s): {', '.join(missing)}"
        raise ImportError(msg)

    source_store = LocalStore()
    destination_store = LocalStore()

    if sync_instance.store and sync_instance.store.type == "redis":
        if sync_instance.store.settings and isinstance(sync_instance.store.settings, dict):
            redis_settings = sync_instance.store.settings
            source_store = RedisStore(**redis_settings, name=sync_instance.source.name)
            destination_store = RedisStore(**redis_settings, name=sync_instance.destination.name)
        else:
            source_store = RedisStore(name=sync_instance.source.name)
            destination_store = RedisStore(name=sync_instance.destination.name)

    source_kwargs = {
        "config": sync_instance,
        "target": "source",
        "adapter": sync_instance.source,
        "internal_storage_engine": source_store,
    }
    if "infrahub" in sync_instance.source.name.lower():
        source_kwargs["branch"] = (sync_instance.source.settings or {}).get("branch") or branch or "main"

    try:
        src = source(**source_kwargs)
    except (ValueError, TypeError) as exc:
        msg = f"Error initializing {sync_instance.source.name.title()}Adapter: {exc}"
        raise ValueError(msg) from exc

    dest_kwargs = {
        "config": sync_instance,
        "target": "destination",
        "adapter": sync_instance.destination,
        "internal_storage_engine": destination_store,
    }
    if "infrahub" in sync_instance.destination.name.lower():
        dest_kwargs["branch"] = (sync_instance.destination.settings or {}).get("branch") or branch or "main"

    try:
        dst = destination(**dest_kwargs)
    except (ValueError, TypeError) as exc:
        msg = f"Error initializing {sync_instance.destination.name.title()}Adapter: {exc}"
        raise ValueError(msg) from exc

    # Single topological pass yields both the flat order and the tier layout
    # (tiers is None when an explicit `order` is configured).
    top_level, tiers = sync_instance.compute_order_and_tiers()

    from infrahub_sync.cache.paths import generate_run_id

    rid = run_id or generate_run_id()
    rdir = stored_run_dir(sync_instance.name, rid)
    rdir.mkdir(parents=True, exist_ok=True)

    # Compute (and persist) the schema sub-hash *before* constructing Potenda so
    # the engine receives fully-formed cache identity rather than being mutated
    # into shape afterwards. Its **only** reader is incremental extraction:
    # `should_use_incremental` compares it against the prior run's, and a run whose
    # shape has changed re-extracts in full. `apply` does not read it — the plan
    # artifact's own gate is what an apply is refused by. Uses the destination
    # adapter's live schema (populated at __init__); falls back to
    # `sync_instance._cached_schema` for test seams.
    subhash = ""
    try:
        from infrahub_sync.cache import compute_schema_subhash
        from infrahub_sync.cache.sidecars import SchemaHashFile

        schema = getattr(dst, "schema", None) or getattr(sync_instance, "_cached_schema", None)
        if schema:
            subhash = compute_schema_subhash(sync_instance, schema)
            SchemaHashFile(path=rdir / "schema-sub-hash.txt", value=subhash).save()
    except ImportError:
        pass  # cache extras not available — degrade silently

    return Potenda(
        destination=dst,
        source=src,
        config=sync_instance,
        top_level=top_level,
        tiers=tiers,
        show_progress=show_progress,
        verbosity=verbosity,
        run_dir=rdir,
        run_id=rid,
        cache_root=rdir.parent,  # .infrahub-sync-cache/<sync_name>/
        schema_subhash=subhash,
        continue_on_error=continue_on_error,
        concurrent_load=concurrent_load,
    )


class _PlanApplySource:
    """Stands in for the source adapter an apply never constructs.

    Apply reads no source (FR-012), so `PlanApplier.open_existing` neither imports nor
    instantiates one. `Potenda.__init__` assigns `top_level` and `continue_on_error` onto
    its source, which a plain instance accepts; anything that would actually *use* the
    source raises immediately, so a change that reintroduces source access on the apply
    path fails loudly instead of quietly requiring the source's dependencies again.
    """

    def __getattr__(self, name: str) -> Any:
        msg = f"apply constructs no source adapter, so nothing on the apply path may read source.{name}"
        raise AttributeError(msg)


def _destination_store(sync_instance: SyncInstance) -> LocalStore | RedisStore:
    """The diffsync store the destination adapter uses, per the configuration's `store` block."""
    if sync_instance.store and sync_instance.store.type == "redis":
        if sync_instance.store.settings and isinstance(sync_instance.store.settings, dict):
            return RedisStore(**sync_instance.store.settings, name=sync_instance.destination.name)
        return RedisStore(name=sync_instance.destination.name)
    return LocalStore()


class PlanApplier:
    """The apply command's assembly seam: a stored run opened for applying.

    Apply executes a plan that already exists, against the destination it names, so this
    seam constructs the **destination adapter only** — a host holding destination
    credentials applies a reviewed plan without the source's dependencies, credentials or
    reachability. It locates the stored run directory without creating anything (AD026)
    and writes none of the run's sidecars: a stored run's files are the immutable
    provenance of the plan under apply. `run.json` remains the CLI's to write (AD069).
    """

    def __init__(self, engine: Potenda, *, run_dir: Path, run_id: str) -> None:
        self.engine = engine
        self.run_dir = run_dir
        self.run_id = run_id

    @property
    def applied_plan_action_counts(self) -> dict[str, int]:
        """Return counts parsed from the in-memory artifact just applied."""
        counts = self.engine._last_applied_plan_action_counts
        if counts is None:
            msg = "PlanApplier completed without retaining action counts from the applied artifact."
            raise RuntimeError(msg)
        return counts

    @classmethod
    def open_existing(
        cls,
        sync_instance: SyncInstance,
        *,
        run_id: str,
        branch: str | None = None,
        verbosity: int | None = None,
    ) -> PlanApplier:
        """Open the stored run `run_id` of `sync_instance` for applying.

        Raises:
            ImportError: the destination adapter could not be loaded.
            ValueError: the destination adapter could not be initialized.
        """
        destination_class = import_adapter(sync_instance=sync_instance, adapter=sync_instance.destination)
        if not destination_class:
            msg = f"Could not load the destination adapter '{sync_instance.destination.name}'"
            raise ImportError(msg)

        dest_kwargs: dict[str, Any] = {
            "config": sync_instance,
            "target": "destination",
            "adapter": sync_instance.destination,
            "internal_storage_engine": _destination_store(sync_instance),
        }
        if "infrahub" in sync_instance.destination.name.lower():
            dest_kwargs["branch"] = (sync_instance.destination.settings or {}).get("branch") or branch or "main"
        try:
            destination = destination_class(**dest_kwargs)
        except (ValueError, TypeError) as exc:
            msg = f"Error initializing {sync_instance.destination.name.title()}Adapter: {exc}"
            raise ValueError(msg) from exc

        top_level, tiers = sync_instance.compute_order_and_tiers()

        # Located, never created: the run being applied already exists, and an apply that
        # allocated directories could manufacture the very run whose absence it should report.
        rdir = stored_run_dir(sync_instance.name, run_id)

        engine = Potenda(
            source=cast("Adapter", _PlanApplySource()),
            destination=destination,
            config=sync_instance,
            top_level=top_level,
            tiers=tiers,
            verbosity=verbosity,
            run_dir=rdir,
            run_id=run_id,
            cache_root=rdir.parent,
        )
        return cls(engine, run_dir=rdir, run_id=run_id)

    def apply_plan(
        self,
        *,
        config_version: str | None = None,
        allow_destination_change: bool = False,
        expected_checksum: str | None = None,
        artifact: RawPlanArtifact | None = None,
    ) -> ApplyRecord:
        """Apply the stored plan — the engine's contract, unchanged; writes no run file (AD069).

        By default, this seam performs the apply's one read. A caller already holding the
        pipeline lock may instead supply that read as ``artifact``. In either case, the same
        object reaches the destination-binding precheck and the engine, so the bytes the
        binding was compared against, the bytes verified, and the bytes applied are the same
        bytes — a plan swapped under the run between two reads cannot be applied by an apply
        that checked the other copy (DBR-006).

        The binding precheck compares the manifest's recorded destination against the live
        adapter's and refuses on a mismatch; `allow_destination_change` turns that refusal into
        a logged warning for a deliberate cross-environment apply. Plans without the recorded
        field, and destinations that expose no binding, skip the check.

        `expected_checksum` travels to the engine rather than being answered here, so the
        operator's approval is decided against the artifact the apply loop consumes.

        Raises:
            PlanVerificationError: the plan was computed against a different destination
                and `allow_destination_change` is false; nothing was written.
        """
        artifact = read_plan_artifact_bytes(self.run_dir) if artifact is None else artifact
        self._require_recorded_destination(artifact=artifact, allow_destination_change=allow_destination_change)
        return self.engine.apply_plan(
            config_version=config_version, artifact=artifact, expected_checksum=expected_checksum
        )

    def _require_recorded_destination(self, *, artifact: RawPlanArtifact, allow_destination_change: bool) -> None:
        """The apply-time destination-binding guard, on the seam that owns apply-specific assembly.

        Here rather than inside `Potenda.apply_plan` because the check's subject is the
        destination this seam constructed, and its refusal is the one pre-apply verdict an
        operator may deliberately override. It answers about `artifact` — the same object the
        engine verifies and applies — rather than a read of its own.
        """
        failure = destination_binding_failure(
            run_id=self.run_id,
            artifact=artifact,
            live=getattr(self.engine.destination, "destination_binding", None),
        )
        if failure is None:
            return
        if allow_destination_change:
            logger.warning(
                "Applying run %s to a different destination than it was planned against: "
                "recorded %s; live %s (--allow-destination-change).",
                self.run_id,
                failure.expected,
                failure.found,
            )
            return
        msg = (
            f"The plan of run {self.run_id!r} is bound to a different destination than this "
            f"apply would write to: expected {failure.expected}; found {failure.found}. "
            f"Nothing was written."
        )
        raise PlanVerificationError(msg, next_action=failure.next_action)


def get_infrahub_config(settings: dict[str, str | None], branch: str | None) -> Config:
    """Creates and returns a Config object for infrahub if settings are valid.

    Args:
        settings (Dict[str, Optional[str]]): The settings dictionary containing `url`, `token`, and `branch`.
        branch (Optional[str]): The default branch to use if none is provided in settings.

    Returns:
        Optional[Config]: A Config instance if `token` is available, otherwise None.
    """
    infrahub_token = settings.get("token") or None
    infrahub_branch = settings.get("branch") or branch or "main"

    return Config(default_branch=infrahub_branch, api_token=infrahub_token)
