"""Unit tests for the file adapter (CSV reader, identifier derivation, references, errors)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

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


class LocationSite(FileModel):
    _modelname = "LocationSite"
    _identifiers = ("name", "region")
    _attributes = ()
    name: str
    region: str


class InfraRack(FileModel):
    """Mirrors the generator output: list attributes are ``list[Any] | None = None``."""

    _modelname = "InfraRack"
    _identifiers = ("name",)
    _attributes = ("site", "aliases", "height")
    name: str
    site: str | None = None
    aliases: list[Any] | None = None
    height: int | None = None


class _FileSync(FileAdapter):
    top_level = ["OrganizationGeneric", "InfraDevice", "LocationSite", "InfraRack"]  # noqa: RUF012
    OrganizationGeneric = OrganizationGeneric
    InfraDevice = InfraDevice
    LocationSite = LocationSite
    InfraRack = InfraRack


def _instance(
    directory: Path, schema_mapping: list[dict[str, Any]], settings: dict[str, Any] | None = None
) -> SyncInstance:
    """Build a SyncInstance for the file adapter from a raw schema mapping."""
    return SyncInstance.model_validate(
        {
            "name": "from-file-test",
            "directory": str(directory),
            "source": {"name": "file", "settings": {"directory": str(directory), **(settings or {})}},
            "destination": {"name": "infrahub", "settings": {}},
            "order": [element["name"] for element in schema_mapping],
            "schema_mapping": schema_mapping,
        }
    )


def _adapter(instance: SyncInstance) -> _FileSync:
    return _FileSync(target="source", adapter=instance.source, config=instance, internal_storage_engine=LocalStore())


SITES_MAPPING = {
    "name": "LocationSite",
    "mapping": "sites.csv",
    "identifiers": ["name", "region"],
    "fields": [{"name": "name", "mapping": "name"}, {"name": "region", "mapping": "region"}],
}


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
    """Records load, local_id falls back to the unique id, and a scalar reference resolves to the peer."""
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


def test_read_csv_strips_utf8_bom(tmp_path: Path) -> None:
    """A UTF-8 BOM (as written by Excel) does not leak into the first column name."""
    path = tmp_path / "orgs.csv"
    path.write_text("name,description\nAcme,HQ\n", encoding="utf-8-sig")
    assert read_csv(path, settings={}) == [{"name": "Acme", "description": "HQ"}]


def test_read_csv_ignores_trailing_empty_cells(tmp_path: Path) -> None:
    """Empty cells beyond the header (trailing delimiters) are dropped."""
    path = tmp_path / "orgs.csv"
    path.write_text("name,description\nAcme,HQ,,\n", encoding="utf-8")
    assert read_csv(path, settings={}) == [{"name": "Acme", "description": "HQ"}]


def test_read_csv_rejects_extra_cells(tmp_path: Path) -> None:
    """Data beyond the header columns is an error naming the line, not a crash."""
    path = tmp_path / "orgs.csv"
    path.write_text("name,description\nrouter,core,EXTRA1,EXTRA2\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"Line 2 .* more cells than the header"):
        read_csv(path, settings={})


def test_read_csv_rejects_duplicate_header(tmp_path: Path) -> None:
    """Repeated column names are rejected instead of silently overwriting each other."""
    path = tmp_path / "orgs.csv"
    path.write_text("name,name,description\na,b,c\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate column"):
        read_csv(path, settings={})


def test_list_attribute_with_generated_annotation_is_split(tmp_path: Path) -> None:
    """A ``list[Any] | None = None`` attribute is split on the list separator."""
    (tmp_path / "racks.csv").write_text("name,aliases\nr1,a;b;c\n", encoding="utf-8")
    instance = _instance(
        tmp_path,
        [
            {
                "name": "InfraRack",
                "mapping": "racks.csv",
                "identifiers": ["name"],
                "fields": [{"name": "name", "mapping": "name"}, {"name": "aliases", "mapping": "aliases"}],
            }
        ],
    )
    adapter = _adapter(instance)
    adapter.load()
    rack = cast("InfraRack", adapter.get_all("InfraRack")[0])
    assert rack.aliases == ["a", "b", "c"]


@pytest.mark.parametrize(
    ("site_column", "header"),
    [("dc1__emea", "name,site"), ("s-1", "name,site")],
    ids=["composite-identifier", "id-column"],
)
def test_reference_resolves_to_peer_unique_id(tmp_path: Path, site_column: str, header: str) -> None:
    """A reference cell matches the peer by its (composite) identifier or by its ``id`` column."""
    (tmp_path / "sites.csv").write_text("id,name,region\ns-1,dc1,emea\n", encoding="utf-8")
    (tmp_path / "racks.csv").write_text(f"{header}\nr1,{site_column}\n", encoding="utf-8")
    instance = _instance(
        tmp_path,
        [
            SITES_MAPPING,
            {
                "name": "InfraRack",
                "mapping": "racks.csv",
                "identifiers": ["name"],
                "fields": [
                    {"name": "name", "mapping": "name"},
                    {"name": "site", "mapping": "site", "reference": "LocationSite"},
                ],
            },
        ],
    )
    adapter = _adapter(instance)
    adapter.load()
    rack = cast("InfraRack", adapter.get_all("InfraRack")[0])
    assert rack.site == "dc1__emea"


def test_unresolved_reference_raises(tmp_path: Path) -> None:
    """A reference to an object that was not loaded fails with the offending value."""
    (tmp_path / "sites.csv").write_text("name,region\ndc1,emea\n", encoding="utf-8")
    (tmp_path / "racks.csv").write_text("name,site\nr1,dc9\n", encoding="utf-8")
    instance = _instance(
        tmp_path,
        [
            SITES_MAPPING,
            {
                "name": "InfraRack",
                "mapping": "racks.csv",
                "fields": [
                    {"name": "name", "mapping": "name"},
                    {"name": "site", "mapping": "site", "reference": "LocationSite"},
                ],
            },
        ],
    )
    with pytest.raises(ValueError, match="Unable to resolve 'dc9'"):
        _adapter(instance).load()


def test_unknown_reference_model_raises(tmp_path: Path) -> None:
    """A ``reference`` that names no schema_mapping entry is rejected (catches typos)."""
    (tmp_path / "racks.csv").write_text("name,site\nr1,dc1\n", encoding="utf-8")
    instance = _instance(
        tmp_path,
        [
            {
                "name": "InfraRack",
                "mapping": "racks.csv",
                "fields": [
                    {"name": "name", "mapping": "name"},
                    {"name": "site", "mapping": "site", "reference": "LocationSiteTypo"},
                ],
            }
        ],
    )
    with pytest.raises(ValueError, match="'LocationSiteTypo', which is not defined in schema_mapping"):
        _adapter(instance).load()


def test_empty_fields_raises(tmp_path: Path) -> None:
    """A mapping without fields fails fast instead of loading attribute-less objects."""
    (tmp_path / "racks.csv").write_text("name\nr1\n", encoding="utf-8")
    instance = _instance(tmp_path, [{"name": "InfraRack", "mapping": "racks.csv", "fields": []}])
    with pytest.raises(ValueError, match="No fields defined"):
        _adapter(instance).load()


def test_empty_list_separator_raises(tmp_path: Path) -> None:
    """An empty ``list_separator`` is rejected with a message naming the setting."""
    instance = _instance(tmp_path, [SITES_MAPPING], settings={"list_separator": ""})
    with pytest.raises(ValueError, match="list_separator"):
        _adapter(instance)


def test_falsy_static_value_is_kept(tmp_path: Path) -> None:
    """A ``static: 0`` is applied rather than treated as unset."""
    (tmp_path / "racks.csv").write_text("name\nr1\n", encoding="utf-8")
    instance = _instance(
        tmp_path,
        [
            {
                "name": "InfraRack",
                "mapping": "racks.csv",
                "fields": [{"name": "name", "mapping": "name"}, {"name": "height", "static": 0}],
            }
        ],
    )
    adapter = _adapter(instance)
    adapter.load()
    assert cast("InfraRack", adapter.get_all("InfraRack")[0]).height == 0


def test_conflicting_duplicate_is_reported(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Rows sharing an identifier but carrying different data are logged at WARNING by id."""
    (tmp_path / "devices.csv").write_text("name,description\nsw1,A\nsw1,B\n", encoding="utf-8")
    instance = _instance(
        tmp_path,
        [
            {
                "name": "InfraDevice",
                "mapping": "devices.csv",
                "fields": [{"name": "name", "mapping": "name"}, {"name": "description", "mapping": "description"}],
            }
        ],
    )
    adapter = _adapter(instance)
    with caplog.at_level("WARNING", logger="infrahub_sync.adapters.file"):
        adapter.load()

    device = cast("InfraDevice", adapter.get_all("InfraDevice")[0])
    assert device.description == "A"
    assert "InfraDevice 'sw1' appears more than once" in caplog.text


def test_model_write_is_not_supported() -> None:
    """Using the file adapter as a destination fails loudly instead of reporting success."""
    with pytest.raises(NotImplementedError, match="source-only"):
        OrganizationGeneric.create(adapter=cast("Any", None), ids={"name": "Acme"}, attrs={})


def test_relative_directory_resolves_against_config_directory(tmp_path: Path) -> None:
    """A relative ``directory`` setting is anchored to the config directory, not the CWD."""
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "racks.csv").write_text("name\nr1\n", encoding="utf-8")
    instance = _instance(
        tmp_path,
        [{"name": "InfraRack", "mapping": "racks.csv", "fields": [{"name": "name", "mapping": "name"}]}],
    )
    instance.source.settings = {"directory": "data"}
    adapter = _adapter(instance)
    adapter.load()
    assert [rack.get_unique_id() for rack in adapter.get_all("InfraRack")] == ["r1"]
