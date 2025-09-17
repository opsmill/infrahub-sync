from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Union

import yaml
from diffsync.store.local import LocalStore
from diffsync.store.redis import RedisStore
from infrahub_sdk import Config

from infrahub_sync import SyncAdapter, SyncConfig, SyncInstance
from infrahub_sync.generator import render_template
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


def get_adapter_import_info(sync_instance: SyncInstance, adapter: SyncAdapter) -> tuple[str, str]:
    """
    Determine the correct import path and class name for an adapter.
    
    Returns:
        tuple: (import_statement, class_name)
    """
    directory = Path(sync_instance.directory)
    adapter_name = f"{adapter.name.title()}Adapter"
    
    # First check if it's a built-in adapter
    try:
        importlib.import_module(f"infrahub_sync.adapters.{adapter.name}")
        return f"infrahub_sync.adapters.{adapter.name}", adapter_name
    except ImportError:
        pass
    
    # Check for local adapter files in order of preference
    possible_paths = [
        directory / f"{adapter.name}.py",  # e.g., my_adapter.py
        directory / f"{adapter.name}_adapter.py",  # e.g., my_adapter_adapter.py
        directory / "adapters" / f"{adapter.name}.py",  # e.g., adapters/my_adapter.py
        directory / adapter.name / "adapter.py",  # e.g., my_adapter/adapter.py
    ]
    
    # Handle underscores properly in adapter name by creating CamelCase
    parts = adapter.name.split("_")
    camel_case_name = "".join(part.capitalize() for part in parts)
    
    for adapter_file_path in possible_paths:
        if adapter_file_path.exists():
            # Determine the correct import path based on file location
            relative_path = adapter_file_path.relative_to(directory)
            if relative_path.parent.name == "adapters":
                import_path = f"adapters.{adapter.name}"
            elif adapter_file_path.name == "adapter.py":
                import_path = f"{adapter.name}.adapter" 
            else:
                import_path = adapter_file_path.stem
            
            # Try different class names that might exist in the local file
            possible_class_names = [
                f"{camel_case_name}Adapter",  # e.g., MyCustomAdapterAdapter
                f"{camel_case_name}",  # e.g., MyCustomAdapter
                f"{camel_case_name}Sync",  # e.g., MyCustomAdapterSync
            ]
            
            # We can't easily check which class name exists without importing,
            # so we'll use the first one as default and let the user know
            return import_path, possible_class_names[0]
    
    # Default to built-in path if no local file found (will likely fail but gives better error)
    return f"infrahub_sync.adapters.{adapter.name}", adapter_name


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

        # Get import information for this adapter
        import_path, class_name = get_adapter_import_info(sync_instance, adapter)

        for item in files_to_render:
            render_template(
                template_file=item[0],
                output_dir=output_dir_path,
                output_file=item[1],
                context={
                    "schema": schema, 
                    "adapter": adapter, 
                    "config": sync_instance,
                    "adapter_import_path": import_path,
                    "adapter_class_name": class_name,
                },
            )
            output_file_path = output_dir_path / item[1]
            rendered_files.append((item[0], output_file_path))

    return rendered_files


def import_adapter(sync_instance: SyncInstance, adapter: SyncAdapter):
    adapter_name = f"{adapter.name.title()}Adapter"

    # First try to import from built-in adapters
    try:
        adapter_module = importlib.import_module(f"infrahub_sync.adapters.{adapter.name}")
        adapter_class = getattr(adapter_module, adapter_name, None)
        if adapter_class is not None:
            return adapter_class
    except ImportError:
        pass

    # If built-in adapter not found, try to load from local generated sync_adapter.py
    directory = Path(sync_instance.directory)
    sys.path.insert(0, str(directory))
    adapter_file_path = directory / f"{adapter.name}" / "sync_adapter.py"

    try:
        sync_adapter_name = f"{adapter.name.title()}Sync"
        spec = importlib.util.spec_from_file_location(f"{adapter.name}.adapter", str(adapter_file_path))
        adapter_module = importlib.util.module_from_spec(spec)
        sys.modules[f"{adapter.name}.adapter"] = adapter_module
        spec.loader.exec_module(adapter_module)

        adapter_class = getattr(adapter_module, sync_adapter_name, None)
        if adapter_class is None:
            msg = f"{sync_adapter_name} not found in sync_adapter.py"
            raise ImportError(msg)
        return adapter_class

    except FileNotFoundError:
        # If neither built-in nor generated adapter found, try loading from local adapter file
        return _import_local_adapter(sync_instance, adapter)


def _import_local_adapter(sync_instance: SyncInstance, adapter: SyncAdapter):
    """Import a local adapter from a Python module file."""
    directory = Path(sync_instance.directory)

    # Try different possible locations and naming conventions for local adapters
    possible_paths = [
        directory / f"{adapter.name}.py",  # e.g., my_adapter.py
        directory / f"{adapter.name}_adapter.py",  # e.g., my_adapter_adapter.py
        directory / "adapters" / f"{adapter.name}.py",  # e.g., adapters/my_adapter.py
        directory / adapter.name / "adapter.py",  # e.g., my_adapter/adapter.py
    ]

    # Handle underscores properly in adapter name by creating CamelCase
    parts = adapter.name.split("_")
    camel_case_name = "".join(part.capitalize() for part in parts)
    adapter_base_name = f"{camel_case_name}Adapter"

    for adapter_file_path in possible_paths:
        if not adapter_file_path.exists():
            continue

        try:
            # Add the directory to sys.path to allow imports
            parent_dir = str(adapter_file_path.parent)
            if parent_dir not in sys.path:
                sys.path.insert(0, parent_dir)

            spec = importlib.util.spec_from_file_location(f"{adapter.name}_local", str(adapter_file_path))
            if spec is None or spec.loader is None:
                continue

            adapter_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(adapter_module)

            # Try different possible class names
            possible_class_names = [
                adapter_base_name,  # e.g., MyCustomAdapterAdapter
                f"{camel_case_name}",  # e.g., MyCustomAdapter
                f"{camel_case_name}Sync",  # e.g., MyCustomAdapterSync
            ]

            for class_name in possible_class_names:
                adapter_class = getattr(adapter_module, class_name, None)
                if adapter_class is not None:
                    return adapter_class

        except (ImportError, AttributeError):
            # Continue to next possible path if this one fails
            continue

    # If we get here, no adapter could be loaded
    paths_str = ", ".join(str(p) for p in possible_paths)
    msg = (
        f"Could not load adapter '{adapter.name}'. Tried built-in adapters, "
        f"generated sync_adapter.py, and local adapter files at: {paths_str}"
    )
    raise ImportError(msg)


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
    source = import_adapter(sync_instance=sync_instance, adapter=sync_instance.source)
    destination = import_adapter(sync_instance=sync_instance, adapter=sync_instance.destination)

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
    try:
        if sync_instance.source.name == "infrahub":
            settings_branch = sync_instance.source.settings.get("branch") or branch or "main"
            src: SyncInstance = source(
                config=sync_instance,
                target="source",
                adapter=sync_instance.source,
                branch=settings_branch,
                internal_storage_engine=source_store,
            )
        else:
            src: SyncInstance = source(
                config=sync_instance,
                target="source",
                adapter=sync_instance.source,
                internal_storage_engine=source_store,
            )
    except ValueError as exc:
        msg = f"{sync_instance.source.name.title()}Adapter - {exc}"
        raise ValueError(msg) from exc
    try:
        if sync_instance.destination.name == "infrahub":
            settings_branch = sync_instance.source.settings.get("branch") or branch or "main"
            dst: SyncInstance = destination(
                config=sync_instance,
                target="destination",
                adapter=sync_instance.destination,
                branch=settings_branch,
                internal_storage_engine=destination_store,
            )
        else:
            dst: SyncInstance = destination(
                config=sync_instance,
                target="destination",
                adapter=sync_instance.destination,
                internal_storage_engine=destination_store,
            )
    except ValueError as exc:
        msg = f"{sync_instance.destination.name.title()}Adapter - {exc}"
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
