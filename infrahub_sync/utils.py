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
    directory = Path(sync_instance.directory)
    sys.path.insert(0, str(directory))
    adapter_file_path = directory / f"{adapter.name}" / "sync_adapter.py"

    try:
        adapter_name = f"{adapter.name.title()}Sync"
        spec = importlib.util.spec_from_file_location(f"{adapter.name}.adapter", str(adapter_file_path))
        adapter_module = importlib.util.module_from_spec(spec)
        sys.modules[f"{adapter.name}.adapter"] = adapter_module
        spec.loader.exec_module(adapter_module)

        adapter_class = getattr(adapter_module, adapter_name, None)
        if adapter_class is None:
            msg = f"{adapter_name} not found in adapter.py"
            raise ImportError(msg)

    except FileNotFoundError as exc:
        msg = f"{adapter_name}: {exc!s}"
        raise ImportError(msg) from exc

    return adapter_class


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
