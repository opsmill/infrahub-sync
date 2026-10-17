from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from diffsync import Adapter, DiffSyncModel

from infrahub_sync import (
    DiffSyncMixin,
    DiffSyncModelMixin,
    SchemaMappingModel,
    SyncAdapter,
    SyncConfig,
)
from infrahub_sync.adapters.references import owner_kind, reference_is_required, resolve_peer_list

"""Example custom adapter for demonstration purposes.

This adapter simulates connecting to a mock database containing Cars and People.
It demonstrates the key patterns for creating adapters in infrahub-sync.
"""

# Log under the `infrahub_sync` hierarchy so this adapter's narration reaches the
# same places the engine's own lifecycle logs go: the CLI output, and the Prefect
# flow-run log when the run is invoked remotely.
logger = logging.getLogger("infrahub_sync.examples.custom_adapter")


class MockDBClient:
    """Client for interacting with a mock database stored as a JSON file."""

    def __init__(self, filepath: str | None = None) -> None:
        """Initialize the mock database client.

        Args:
            filepath: Path to the mock database JSON file.
        """
        # Default to a sample database with a few entries
        self.data = {}

        if not filepath:
            return

        # A configured db_path that does not exist would otherwise leave this
        # client silently empty, and the run would report a successful plan with
        # zero changes. Say so instead: the path is relative to the working
        # directory of the process running the sync.
        if not Path(filepath).exists():
            logger.warning(
                "MockDB database file not found: %s (resolved to %s from working directory %s) - "
                "no records will be loaded",
                filepath,
                Path(filepath).absolute(),
                Path.cwd(),
            )
            return

        try:
            with Path(filepath).open(encoding="utf-8") as f:
                self.data = json.load(f)
            logger.debug("Loaded mock database from %s", filepath)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.warning("Failed to load mock database from %s: %s", filepath, e)

    def get_all_nodes(self, model: str) -> list[dict[str, Any]]:
        """Get all nodes of a specific model from the mock database.

        Args:
            model: The name of the model to get nodes for.

        Returns:
            A list of dictionaries representing the nodes.
        """
        logger.debug("Getting all %s nodes from mock database", model)
        nodes = self.data.get("nodes", {}).get(model, [])
        logger.info("Loading %s %s nodes", len(nodes), model)
        return nodes


class MockdbAdapter(DiffSyncMixin, Adapter):
    """A custom adapter that connects to a mock database."""

    type = "MockDB"

    def __init__(self, target: str, adapter: SyncAdapter, config: SyncConfig, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        """Initialize the custom adapter.

        Args:
            target: The target of the sync operation.
            adapter: The adapter configuration.
            config: The sync configuration.
        """
        super().__init__(*args, **kwargs)

        self.target = target
        self.config = config
        self.settings = adapter.settings or {}

        # Initialize the mock database client
        db_path = self.settings.get("db_path", "")
        self.client = MockDBClient(filepath=db_path or None)

        logger.debug("Initialized %s adapter with target: %s", self.__class__.__name__, target)
        # Adapter settings can hold credentials in a real adapter - log only the
        # setting NAMES, never their values.
        logger.debug("Settings provided: %s", sorted(self.settings))

    def list_records(self, model_name: str, element: SchemaMappingModel) -> list[dict[str, Any]]:
        """Return the raw records of one mapping entry, before any filter.

        The base class `model_loader` calls this method for each mapping entry. On the source
        side it applies the mapping's filters and transforms, then passes each record to
        `load_record`, which builds the model and adds it to the store. A record that cannot
        be built is listed in the plan as a problem instead of failing the load.

        Args:
            model_name: The name of the model to load data for.
            element: The schema mapping entry of the model.

        Returns:
            The records of the mapping's resource in the mock database.
        """
        logger.debug("Listing %s records from %s", model_name, element.mapping)
        return self.client.get_all_nodes(element.mapping)

    def record_source_id(self, record: Any) -> str | None:  # noqa: ANN401, PLR6301 - a raw record of this adapter
        """Return the source record ID: the mock database keys its records by name, or by id."""
        value = record.get("name") or record.get("id")
        return None if value is None else str(value)

    def record_to_data(
        self, record: dict[str, Any], element: SchemaMappingModel, model: type[MockdbModel]
    ) -> dict[str, Any]:
        """Convert one record of the mock database to the model's field data.

        Args:
            record: The record from the mock database.
            element: The schema mapping entry of the model.
            model: The model class.

        Returns:
            The model's field data, with `local_id` set to the record's source ID.
        """
        source_id = self.record_source_id(record)
        data: dict[str, Any] = {"local_id": source_id}
        owner = (owner_kind(model, element.name), source_id)

        for field in element.fields or []:
            if field.static:
                # Use the static value if provided
                data[field.name] = field.static
            elif field.mapping and not field.reference:
                # Map directly from the source object
                value = record.get(field.mapping)
                if value is not None:
                    data[field.name] = value
            elif field.mapping and field.reference:
                # Resolve each reference through the base class, which decides what a
                # reference to a peer that was not loaded becomes.
                required = reference_is_required(model, field.name)
                if model.is_list(field.name):
                    data[field.name], _ = resolve_peer_list(
                        self,
                        peer_kind=field.reference,
                        peer_source_ids=record.get(field.mapping, []),
                        owner=owner,
                        field=field.name,
                        required=required,
                    )
                else:
                    peer_source_id = record.get(field.mapping)
                    if peer_source_id is not None:
                        data[field.name] = self.peer_unique_id(
                            field.reference, str(peer_source_id), owner=owner, field=field.name, required=required
                        )

        return data


class MockdbModel(DiffSyncModelMixin, DiffSyncModel):
    """A custom model for demonstration purposes."""
