"""`referenced_kinds` decides which unclean peer kinds force a kind of the source to read cold."""

from __future__ import annotations

from infrahub_sync import SchemaMappingField, SchemaMappingModel, SyncAdapter, SyncInstance
from infrahub_sync.cache.incremental import referenced_kinds


def _config() -> SyncInstance:
    config = SyncInstance(
        name="generic-reference",
        directory=".",
        source=SyncAdapter(name="netbox"),
        destination=SyncAdapter(name="infrahub"),
        schema_mapping=[
            SchemaMappingModel(name="LocationSite", mapping="dcim.sites", fields=[]),
            SchemaMappingModel(name="LocationRack", mapping="dcim.racks", fields=[]),
            SchemaMappingModel(
                name="DcimDevice",
                mapping="dcim.devices",
                fields=[SchemaMappingField(name="location", mapping="location", reference="LocationGeneric")],
            ),
        ],
    )
    config._direct_generic_peers = {"LocationGeneric": ("LocationSite", "LocationRack", "LocationRoom")}
    return config


def test_a_reference_to_a_generic_names_its_mapped_concrete_kinds() -> None:
    """`DcimDevice.location` references `LocationGeneric`: a rename of a site or a rack makes devices read cold."""
    assert referenced_kinds(_config(), "DcimDevice") == {"LocationRack", "LocationSite"}


def test_a_kind_without_references_names_no_kind() -> None:
    """`LocationSite` references nothing."""
    assert referenced_kinds(_config(), "LocationSite") == set()
