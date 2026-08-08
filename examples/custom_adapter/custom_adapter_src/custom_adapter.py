from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

try:
    from typing import Self
except ImportError:
    from typing_extensions import Self


from diffsync import Adapter, DiffSyncModel

from infrahub_sync import (
    DiffSyncMixin,
    DiffSyncModelMixin,
    SchemaMappingModel,
    SyncAdapter,
    SyncConfig,
)

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

    def model_loader(self, model_name: str, model: MockdbModel) -> None:
        """
        Load data for a specific model.

        This method is called by the DiffSync framework when loading
        data for a specific model.

        Args:
            model_name: The name of the model to load data for.
            model: The model class to instantiate.
        """
        logger.debug("Loading %s data via model_loader...", model_name)

        # Find the corresponding schema mapping for this model
        for element in self.config.schema_mapping:
            if element.name == model_name:
                # Get the resource name from the mapping
                resource_name = element.mapping
                logger.debug("Found mapping for %s: %s", model_name, resource_name)

                # Get all nodes of this type from the mock database
                nodes = self.client.get_all_nodes(resource_name)
                total = len(nodes)

                # Apply filters if this is the source adapter
                if self.config.source.name.title() == self.type.title():
                    # Filter and transform the records
                    filtered_objs = model.filter_records(records=nodes, schema_mapping=element)
                    logger.info("%s: Loading %s/%s %s", self.type, len(filtered_objs), total, model_name)
                    transformed_objs = model.transform_records(records=filtered_objs, schema_mapping=element)
                else:
                    # No filtering needed
                    logger.info("%s: Loading all %s %s", self.type, total, model_name)
                    transformed_objs = nodes

                # Add each object to the adapter
                for obj in transformed_objs:
                    data = self.obj_to_diffsync(obj=obj, mapping=element, model=model)
                    logger.debug("Adding %s to adapter", data.get("name", data.get("local_id")))
                    self.add(model(**data))

                # We found and processed the mapping, so we're done
                break

    def obj_to_diffsync(self, obj: dict[str, Any], mapping: SchemaMappingModel, model: MockdbModel) -> dict[str, Any]:
        """Transform an object from the mock database to a format compatible with DiffSync.

        Args:
            obj: The object from the mock database.
            mapping: The schema mapping to use.
            model: The model class.

        Returns:
            A dictionary containing the transformed data.
        """
        # Use name or id as the local ID
        obj_id = obj.get("name") or obj.get("id")
        data: dict[str, Any] = {"local_id": str(obj_id)}

        # Process each field in the mapping
        if mapping.fields:
            for field in mapping.fields:
                if field.static:
                    # Use the static value if provided
                    data[field.name] = field.static
                elif field.mapping and not field.reference:
                    # Map directly from the source object
                    value = obj.get(field.mapping)
                    if value is not None:
                        data[field.name] = value
                elif field.mapping and field.reference:
                    # Handle references to other objects
                    ref_model = field.reference
                    all_refs = self.store.get_all(model=ref_model)

                    if model.is_list(field.name):
                        # Handle list references
                        peers = []
                        for ref_id in obj.get(field.mapping, []):
                            matched = [ref for ref in all_refs if ref.local_id == str(ref_id)]
                            if matched:
                                peers.append(matched[0].get_unique_id())
                        data[field.name] = peers
                    else:
                        # Handle single references
                        ref_id = obj.get(field.mapping)
                        if ref_id is not None:
                            matched = [ref for ref in all_refs if ref.local_id == str(ref_id)]
                            if matched:
                                data[field.name] = matched[0].get_unique_id()

        return data


class MockdbModel(DiffSyncModelMixin, DiffSyncModel):
    """A custom model for demonstration purposes."""

    @classmethod
    def create(
        cls,
        adapter: Adapter,
        ids: Mapping[Any, Any],
        attrs: Mapping[Any, Any],
    ) -> Self | None:
        # TODO: To implement
        return super().create(adapter=adapter, ids=ids, attrs=attrs)

    def update(self, attrs: dict) -> Self | None:
        # TODO: To implement
        return super().update(attrs=attrs)
