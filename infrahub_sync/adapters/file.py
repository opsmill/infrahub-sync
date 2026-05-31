from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from diffsync import Adapter, DiffSyncModel
from diffsync.exceptions import ObjectAlreadyExists
from typing_extensions import Self

from infrahub_sync import (
    DiffSyncMixin,
    DiffSyncModelMixin,
    SchemaMappingModel,
    SyncAdapter,
    SyncConfig,
)

from .utils import get_value

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


def read_csv(path: Path, settings: dict[str, Any]) -> list[dict[str, Any]]:
    """Read a CSV file into a list of dictionaries keyed by the header row.

    Recognized settings:
        delimiter: Field separator (default ",").
        encoding: File encoding (default "utf-8").
        empty_as_none: Treat empty cells as ``None`` instead of "" (default True).
    """
    delimiter = settings.get("delimiter", ",")
    encoding = settings.get("encoding", "utf-8")
    empty_as_none = settings.get("empty_as_none", True)

    records: list[dict[str, Any]] = []
    with path.open(newline="", encoding=encoding) as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        if reader.fieldnames is None:
            return records
        for row in reader:
            if empty_as_none:
                records.append({key: (value or None) for key, value in row.items()})
            else:
                records.append(dict(row))
    return records


# Registry of file readers keyed by normalized format name. Adding support for a
# new format (json, yaml, excel, ...) is a matter of writing a reader function
# with the same signature and registering it here.
FILE_READERS: dict[str, Callable[[Path, dict[str, Any]], list[dict[str, Any]]]] = {
    "csv": read_csv,
}

# Map file extensions to a format name in FILE_READERS.
EXTENSION_FORMATS: dict[str, str] = {
    ".csv": "csv",
}


class FileAdapter(DiffSyncMixin, Adapter):
    """Adapter that loads data from local file exports (CSV today, extensible to other formats).

    Each entry in ``schema_mapping`` maps a model to a file via its ``mapping`` key,
    which is interpreted as a path relative to the configured base directory (or an
    absolute path). The file format is inferred from the extension unless overridden
    through the ``format`` setting.
    """

    type = "File"

    def __init__(self, target: str, adapter: SyncAdapter, config: SyncConfig, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        self.target = target
        self.settings = adapter.settings or {}
        self.config = config
        self.base_directory = self._resolve_base_directory(config=config)
        self.format_override = self.settings.get("format")

    def _resolve_base_directory(self, config: SyncConfig) -> Path:
        """Determine the directory used to resolve relative file paths.

        Priority: the ``directory`` setting, then the sync instance directory, then CWD.
        """
        directory = self.settings.get("directory")
        if directory:
            return Path(directory)
        instance_directory = getattr(config, "directory", None)
        if instance_directory:
            return Path(instance_directory)
        return Path.cwd()

    def _resolve_path(self, mapping: str) -> Path:
        """Resolve a schema mapping value to an absolute file path."""
        path = Path(mapping)
        if not path.is_absolute():
            path = self.base_directory / path
        return path

    def _read_file(self, path: Path) -> list[dict[str, Any]]:
        """Read a file using the reader registered for its format."""
        if not path.is_file():
            msg = f"File not found: {path}"
            raise FileNotFoundError(msg)

        file_format = (self.format_override or EXTENSION_FORMATS.get(path.suffix.lower()) or "").lower()
        reader = FILE_READERS.get(file_format)
        if reader is None:
            supported = ", ".join(sorted(FILE_READERS)) or "none"
            msg = (
                f"Unsupported file format for '{path}'. "
                f"Supported formats: {supported}. "
                f"Set the 'format' setting explicitly or use a recognized extension "
                f"({', '.join(sorted(EXTENSION_FORMATS))})."
            )
            raise ValueError(msg)

        return reader(path, self.settings)

    def model_loader(self, model_name: str, model: type[FileModel]) -> None:
        """Load and process models using schema mapping filters and transformations.

        This reads each mapped file, applies filters and transformations as specified
        in the schema mapping, and loads the processed records into the adapter.
        """
        for element in self.config.schema_mapping:
            if element.name != model_name:
                continue

            if not element.mapping:
                logger.info("No mapping defined for '%s', skipping", element.name)
                continue

            path = self._resolve_path(mapping=element.mapping)
            try:
                objs = self._read_file(path=path)
            except (FileNotFoundError, ValueError, OSError, csv.Error) as exc:
                msg = f"Error reading data from '{path}': {exc!s}"
                raise ValueError(msg) from exc

            total = len(objs)
            adapter_type_title = (self.type or "").title()
            if self.config.source.name.title() == adapter_type_title:
                # Filter records
                filtered_objs = model.filter_records(records=objs, schema_mapping=element)
                logger.info("%s: Loading %d/%d %s", self.type, len(filtered_objs), total, element.mapping)
                # Transform records
                transformed_objs = model.transform_records(records=filtered_objs, schema_mapping=element)
            else:
                logger.info("%s: Loading all %d %s", self.type, total, element.mapping)
                transformed_objs = objs

            # Flat-file rows often repeat the same identifier when a model is derived from a
            # foreign-key column (e.g. every device row carries the same site name).
            # Keep the first occurrence and skip subsequent duplicates so the load completes.
            duplicates = 0
            for index, obj in enumerate(transformed_objs):
                data = self.obj_to_diffsync(obj=obj, mapping=element, model=model, index=index)
                item = model(**data)
                try:
                    self.add(item)
                except ObjectAlreadyExists:
                    duplicates += 1
            if duplicates:
                logger.info(
                    "%s: skipped %d duplicate %s in %s (first occurrence kept)",
                    self.type,
                    duplicates,
                    model_name,
                    element.mapping,
                )

    def _derive_local_id(self, obj: dict[str, Any], mapping: SchemaMappingModel, index: int) -> str:
        """Derive a source-local identifier for a record.

        Files rarely carry a database ``id``, so fall back to ``id``/``*_id`` columns,
        then the configured identifiers, then the row index.
        """
        obj_id = obj.get("id")
        if obj_id is None:
            for key, value in obj.items():
                if key.endswith("_id") and value:
                    obj_id = value
                    break
        if obj_id is None and mapping.identifiers:
            parts = [str(obj.get(identifier, "")) for identifier in mapping.identifiers]
            if any(parts):
                obj_id = "__".join(parts)
        if obj_id is None:
            obj_id = str(index)
        return str(obj_id)

    def obj_to_diffsync(
        self, obj: dict[str, Any], mapping: SchemaMappingModel, model: type[FileModel], index: int
    ) -> dict[str, Any]:
        """Convert a file record into a DiffSync-ready dictionary.

        Supports static values, simple field mappings, and scalar/list references that
        point at another model's identifier (the natural shape for flat file exports).
        """
        data: dict[str, Any] = {"local_id": self._derive_local_id(obj=obj, mapping=mapping, index=index)}

        if not mapping.fields:
            return data

        list_separator = self.settings.get("list_separator", ";")

        for field in mapping.fields:
            field_is_list = model.is_list(name=field.name)

            if field.static:
                data[field.name] = field.static
            elif not field_is_list and field.mapping and not field.reference:
                value = get_value(obj, field.mapping)
                if value is not None:
                    data[field.name] = value
            elif field_is_list and field.mapping and not field.reference:
                value = get_value(obj, field.mapping)
                if value is not None:
                    data[field.name] = self._split_list(value=value, separator=list_separator)
            elif field.mapping and field.reference:
                value = get_value(obj, field.mapping)
                if not field_is_list:
                    if value is not None:
                        # Flat files reference another object directly by its identifier.
                        data[field.name] = value
                else:
                    references = self._split_list(value=value, separator=list_separator) if value is not None else []
                    data[field.name] = sorted(str(item) for item in references if item not in (None, ""))

        return data

    @staticmethod
    def _split_list(value: Any, separator: str) -> list[Any]:
        """Normalize a value into a list, splitting delimited strings."""
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            return [part.strip() for part in value.split(separator) if part.strip()]
        return [value]


class FileModel(DiffSyncModelMixin, DiffSyncModel):
    """Model for the file adapter. The file adapter is read-only (source) for now."""

    @classmethod
    def create(
        cls,
        adapter: Adapter,
        ids: dict[Any, Any],
        attrs: dict[Any, Any],
    ) -> Self | None:
        # The file adapter is a source-only adapter; writing back to files is not supported yet.
        return super().create(adapter=adapter, ids=ids, attrs=attrs)

    def update(self, attrs: dict) -> Self | None:
        # The file adapter is a source-only adapter; writing back to files is not supported yet.
        return super().update(attrs=attrs)
