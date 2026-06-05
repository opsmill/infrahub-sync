"""Unit tests for the file adapter (CSV reader, identifier derivation, references, errors)."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest
from diffsync.store.local import LocalStore

from infrahub_sync import SyncInstance
from infrahub_sync.adapters.file import FileAdapter, FileModel, read_csv

if TYPE_CHECKING:
    from pathlib import Path


class OrganizationGeneric(FileModel):
    _modelname = "OrganizationGeneric"
    _identifiers = ("name",)
    _attributes = ("description",)
    name: str
    description: str | None = None


class InfraDevice(FileModel):
    _modelname = "InfraDevice"
    _identifiers = ("name",)
    _attributes = ("description", "organization", "tags")
    name: str
    description: str | None = None
    organization: str | None = None
    tags: list[str] = []  # noqa: RUF012


class _FileSync(FileAdapter):
    top_level = ["OrganizationGeneric", "InfraDevice"]  # noqa: RUF012
    OrganizationGeneric = OrganizationGeneric
    InfraDevice = InfraDevice


def _build_instance(directory: Path) -> SyncInstance:
    """Build a minimal SyncInstance for the file adapter pointing at a data directory."""
    return SyncInstance.model_validate(
        {
            "name": "from-file-test",
            "directory": str(directory),
            "source": {"name": "file", "settings": {"directory": str(directory)}},
            "destination": {"name": "infrahub", "settings": {}},
            "order": ["OrganizationGeneric", "InfraDevice"],
            "schema_mapping": [
                {
                    "name": "OrganizationGeneric",
                    "mapping": "organizations.csv",
                    "identifiers": ["name"],
                    "fields": [
                        {"name": "name", "mapping": "name"},
                        {"name": "description", "mapping": "description"},
                    ],
                },
                {
                    "name": "InfraDevice",
                    "mapping": "devices.csv",
                    "identifiers": ["name"],
                    "fields": [
                        {"name": "name", "mapping": "name"},
                        {"name": "description", "mapping": "description"},
                        {"name": "organization", "mapping": "organization", "reference": "OrganizationGeneric"},
                        {"name": "tags", "mapping": "tags"},
                    ],
                },
            ],
        }
    )


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """Write sample CSV files and return the directory containing them."""
    (tmp_path / "organizations.csv").write_text("name,description\nAcme,HQ\nGlobex,\n", encoding="utf-8")
    (tmp_path / "devices.csv").write_text(
        "name,description,organization,tags\ndevice-1,Router,Acme,core;edge\n",
        encoding="utf-8",
    )
    return tmp_path


def test_read_csv_empty_cell_becomes_none(tmp_path: Path) -> None:
    """An empty CSV cell is converted to None by default."""
    path = tmp_path / "orgs.csv"
    path.write_text("name,description\nGlobex,\n", encoding="utf-8")
    records = read_csv(path, settings={})
    assert records == [{"name": "Globex", "description": None}]


def test_read_csv_respects_delimiter(tmp_path: Path) -> None:
    """A custom delimiter is honored when reading."""
    path = tmp_path / "orgs.csv"
    path.write_text("name;description\nAcme;HQ\n", encoding="utf-8")
    records = read_csv(path, settings={"delimiter": ";"})
    assert records == [{"name": "Acme", "description": "HQ"}]


@pytest.mark.parametrize(
    ("content", "case"),
    [("", "fully-empty"), ("name,description\n", "header-only")],
)
def test_read_csv_empty_returns_no_records(tmp_path: Path, content: str, case: str) -> None:
    """A CSV with no data rows yields an empty list whether or not a header is present."""
    path = tmp_path / f"orgs-{case}.csv"
    path.write_text(content, encoding="utf-8")
    assert read_csv(path, settings={}) == []


def test_model_loader_loads_records_and_resolves_reference(data_dir: Path) -> None:
    """Records load, identifiers seed local_id, and a scalar reference is kept as the target identifier."""
    instance = _build_instance(directory=data_dir)
    adapter = _FileSync(target="source", adapter=instance.source, config=instance, internal_storage_engine=LocalStore())
    adapter.load()

    orgs = sorted(cast("list[OrganizationGeneric]", adapter.get_all("OrganizationGeneric")), key=lambda o: o.name)
    assert [o.name for o in orgs] == ["Acme", "Globex"]
    assert orgs[0].local_id == "Acme"

    device = cast("InfraDevice", adapter.get_all("InfraDevice")[0])
    assert device.organization == "Acme"
    assert device.tags == ["core", "edge"]


def _single_model_instance(directory: Path, mapping: str) -> SyncInstance:
    """Build a SyncInstance with a single InfraDevice model mapped to ``mapping``."""
    return SyncInstance.model_validate(
        {
            "name": "from-file-test",
            "directory": str(directory),
            "source": {"name": "file", "settings": {"directory": str(directory)}},
            "destination": {"name": "infrahub", "settings": {}},
            "order": ["InfraDevice"],
            "schema_mapping": [
                {
                    "name": "InfraDevice",
                    "mapping": mapping,
                    "identifiers": ["name"],
                    "fields": [{"name": "name", "mapping": "name"}],
                }
            ],
        }
    )


def test_unsupported_format_raises(tmp_path: Path) -> None:
    """An unrecognized extension yields a clear error listing supported formats."""
    (tmp_path / "devices.txt").write_text("name\ndevice-1\n", encoding="utf-8")
    instance = _single_model_instance(directory=tmp_path, mapping="devices.txt")
    adapter = _FileSync(target="source", adapter=instance.source, config=instance, internal_storage_engine=LocalStore())
    with pytest.raises(ValueError, match="Unsupported file format"):
        adapter.model_loader(model_name="InfraDevice", model=InfraDevice)


def test_duplicate_identifiers_are_deduped(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Multiple rows producing the same identifier load once instead of aborting the sync."""
    (tmp_path / "organizations.csv").write_text("name,description\nAcme,HQ\n", encoding="utf-8")
    (tmp_path / "devices.csv").write_text(
        "name,description,organization,tags\ndevice-1,Router,Acme,\ndevice-2,Switch,Acme,\ndevice-3,Firewall,Acme,\n",
        encoding="utf-8",
    )
    instance = SyncInstance.model_validate(
        {
            "name": "from-file-test",
            "directory": str(tmp_path),
            "source": {"name": "file", "settings": {"directory": str(tmp_path)}},
            "destination": {"name": "infrahub", "settings": {}},
            "order": ["OrganizationGeneric"],
            "schema_mapping": [
                {
                    "name": "OrganizationGeneric",
                    "mapping": "devices.csv",
                    "identifiers": ["name"],
                    "fields": [
                        {"name": "name", "mapping": "organization"},
                    ],
                },
            ],
        }
    )
    adapter = _FileSync(target="source", adapter=instance.source, config=instance, internal_storage_engine=LocalStore())
    with caplog.at_level("INFO", logger="infrahub_sync.adapters.file"):
        adapter.load()

    orgs = adapter.get_all("OrganizationGeneric")
    assert [o.get_unique_id() for o in orgs] == ["Acme"]
    assert "skipped 2 duplicate OrganizationGeneric" in caplog.text


def test_missing_file_raises(tmp_path: Path) -> None:
    """A missing mapped file raises a descriptive error."""
    instance = _single_model_instance(directory=tmp_path, mapping="missing.csv")
    adapter = _FileSync(target="source", adapter=instance.source, config=instance, internal_storage_engine=LocalStore())
    with pytest.raises(ValueError, match="Error reading data"):
        adapter.model_loader(model_name="InfraDevice", model=InfraDevice)
