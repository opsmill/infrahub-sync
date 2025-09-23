from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Union

import yaml
from diffsync.store.local import LocalStore
from diffsync.store.redis import RedisStore
from infrahub_sdk import Config

from infrahub_sync import SyncAdapter, SyncConfig, SyncInstance
from infrahub_sync.generator import render_template
from infrahub_sync.plugin_loader import PluginLoader, PluginLoadError
from infrahub_sync.potenda import Potenda

if TYPE_CHECKING:
    from collections.abc import MutableMapping

    from infrahub_sdk.schema import GenericSchema, NodeSchema


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
                template_file=item[0],
                output_dir=output_dir_path,
                output_file=item[1],
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
                print(f"Could not load generated adapter from {adapter_file_path}: {exc}")

    # Fall back to the plugin loader
    # The "sync" classes could be declared into a separate module
    adapter_paths = sync_instance.adapters_path or []
    loader = PluginLoader.from_env_and_args(adapter_paths=adapter_paths)

    # If explicit adapter spec is provided, use it
    if adapter.adapter:
        try:
            # Try loading the explicitly specified adapter
            adapter_class = loader.resolve(adapter.adapter)
            print(f"Using directly specified adapter class: {adapter_class.__name__}")
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

    config_file_path = None
    try:
        if Path(config_file).is_absolute() or directory is None:
            config_file_path = Path(config_file)
        elif directory:
            config_file_path = Path(directory, config_file)
    except TypeError:
        # TODO: Log or raise an Error/Warning
        return None

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
    show_progress: bool | None = True,
) -> Potenda:
    """Create and return a Potenda instance based on the provided SyncInstance."""
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

    ptd = Potenda(
        destination=dst,
        source=src,
        config=sync_instance,
        top_level=sync_instance.order,
        show_progress=show_progress,
    )

    return ptd


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
