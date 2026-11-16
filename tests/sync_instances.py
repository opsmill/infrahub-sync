"""Test helpers that read `SyncInstance` objects from configuration files.

These functions were part of `infrahub_sync.utils`. No product code calls them, so they
live with the tests that still use them.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from infrahub_sync import SyncConfig, SyncInstance

if TYPE_CHECKING:
    from collections.abc import MutableMapping

    from infrahub_sdk.schema import GenericSchema, NodeSchema


def find_missing_schema_model(
    sync_instance: SyncInstance,
    schema: MutableMapping[str, NodeSchema | GenericSchema],
) -> list[str]:
    missing_schema_models = []
    for item in sync_instance.schema_mapping:
        match_found = any(item.name == node.kind for node in schema.values())

        if not match_found:
            missing_schema_models.append(item.name)

    return missing_schema_models


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
