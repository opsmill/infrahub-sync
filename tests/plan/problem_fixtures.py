"""Shared source fixture for plan problem reporting (spec 009 T001).

The fixture is an in-memory source adapter and a destination schema record. Together they hold
one record of each of the eight problem types of `data-model.md`, a set of valid records, a
device with a problem that five physical interfaces require, an IP address that refers to that
device through an optional relationship, and an IP address that refers to a VRF the source does
not return.

The adapter implements `list_records` and `record_to_data` only. The record loop, the filter,
the admission step and the load report belong to the adapter base class (research R3), so a
test that loads this adapter exercises the shared loader, not code of its own.

Problem records of the full fixture (`Variant.ALL`):

| Problem type | Record | How |
| --- | --- | --- |
| `dropdown_value_not_in_choices` | `LocationSite` `berlin` | status `decommissioning` is not a choice |
| `duplicate_source_identity` | two `LocationSite` `dup` | same mapping identity `name` |
| `destination_identity_collision` | two `DcimDevice` `edge` | serials differ, the human-friendly ID `name` is equal |
| `wrong_value_type` | `DcimDevice` `core-1` | `position` is `2.5` for a Number |
| `required_peer_excluded_by_filter` | `InterfacePhysical` `mgmt0` | its required `device` is `lab-1`, removed by the `role != lab` filter |
| `identities_merge_in_destination` | two `LocationRack` `comms-closet` | facility IDs differ; Infrahub holds one (`destination_records`) |
| `peer_not_found_in_source` | `InterfacePhysical` `eth9` | its required `device` has source ID `999`, which the source does not return |
| `destination_identity_ambiguous` | `LocationSite` `twin` | Infrahub holds two `LocationSite` nodes named `twin` (`destination_records`) |

Dependents of `core-1`: five `InterfacePhysical` records (`eth0` to `eth4`) through the required
relationship `device`. `IpamIPAddress` `10.0.0.5/32` refers to `core-1` only through the
optional relationship `device`. `IpamIPAddress` `10.0.0.9/32` refers through the optional
relationship `vrf` to VRF source ID `998`, which the source does not return.

Not a test module: no assertions live here.
"""

from __future__ import annotations

import copy
import enum
from typing import TYPE_CHECKING, Any

from diffsync import Adapter, DiffSyncModel

from infrahub_sync import (
    DiffSyncMixin,
    DiffSyncModelMixin,
    SchemaMappingField,
    SchemaMappingFilter,
    SchemaMappingModel,
    SyncAdapter,
    SyncConfig,
)
from infrahub_sync.adapters.references import reference_is_required
from infrahub_sync.adapters.utils import get_value
from infrahub_sync.runtime_schema.domain import normalize_destination_schema
from infrahub_sync.runtime_schema.models import build_runtime_models
from infrahub_sync.runtime_schema.worker import bind_runtime_models

if TYPE_CHECKING:
    from collections.abc import Mapping

SYNC_NAME = "problem-fixture"

# Two credentials for the absence tests (SC-005, SC-007). The first has five characters, so a
# masking rule that replaces short values would also change unrelated text. The second holds
# a double quote, a backslash and a comma, which break JSON and CSV when they are inserted
# into text without encoding.
SHORT_CREDENTIAL = "k3y9Q"
SPECIAL_CREDENTIAL = 'p"ss\\wo,rd-2026'
SHORT_CREDENTIAL_NAME = "INFRAHUB_SYNC_CREDENTIAL_PROBLEM_FIXTURE_TOKEN"
SPECIAL_CREDENTIAL_NAME = "INFRAHUB_SYNC_CREDENTIAL_PROBLEM_FIXTURE_PASSWORD"

# The eight stable problem type names of data-model.md.
PROBLEM_TYPES: tuple[str, ...] = (
    "dropdown_value_not_in_choices",
    "duplicate_source_identity",
    "destination_identity_collision",
    "wrong_value_type",
    "required_peer_excluded_by_filter",
    "identities_merge_in_destination",
    "peer_not_found_in_source",
    "destination_identity_ambiguous",
)

# Load order: every kind loads after the kinds it refers to.
ORDER: tuple[str, ...] = (
    "LocationSite",
    "IpamVRF",
    "LocationRack",
    "DcimDevice",
    "InterfacePhysical",
    "InterfaceVirtual",
    "IpamIPAddress",
)

# The resource name each kind is listed from, as `SchemaMappingModel.mapping`.
RESOURCES: Mapping[str, str] = {
    "LocationSite": "dcim.sites",
    "IpamVRF": "ipam.vrfs",
    "LocationRack": "dcim.racks",
    "DcimDevice": "dcim.devices",
    "InterfacePhysical": "dcim.interfaces",
    "InterfaceVirtual": "dcim.virtual_interfaces",
    "IpamIPAddress": "ipam.ip_addresses",
}

SITE_STATUS_CHOICES: tuple[str, ...] = ("active", "planned")
EXCLUDED_DEVICE_ROLE = "lab"
MISSING_DEVICE_SOURCE_ID = "999"
MISSING_VRF_SOURCE_ID = "998"
PROBLEM_DEVICE_SOURCE_ID = "30"
PROBLEM_DEVICE_DEPENDENTS = 5
TWIN_SITE_NAME = "twin"
TWIN_SITE_SOURCE_ID = "6"
# The two Infrahub node IDs that hold the one identity `name: twin`.
TWIN_SITE_NODE_IDS: tuple[str, ...] = (
    "18d52a8a-0000-0000-0000-000000000061",
    "18d52a8a-0000-0000-0000-000000000062",
)


class Variant(enum.Enum):
    """Which records the source returns."""

    ALL = "all"
    """Valid records, one record of each problem type, the five dependents and both IP addresses."""

    NO_PROBLEMS = "no_problems"
    """Valid records only."""

    EVERY_RECORD_HAS_A_PROBLEM = "every_record_has_a_problem"
    """The problem records of the table in the module docstring, and nothing else."""

    DEPENDENCY_CHAIN = "dependency_chain"
    """Three levels: `core-1` (problem) <- `InterfacePhysical eth0` <- `InterfaceVirtual eth0.100`."""


# ---------------------------------------------------------------------------------------
# Destination schema record
# ---------------------------------------------------------------------------------------


def _attribute(
    kind: str, *, optional: bool = False, unique: bool = False, choices: tuple[str, ...] | None = None
) -> dict[str, Any]:
    shape: dict[str, Any] = {
        "kind": kind,
        "optional": optional,
        "default_value": None,
        "unique": unique,
        "read_only": False,
    }
    if choices is not None:
        shape["choices"] = list(choices)
    return shape


def _relationship(peer: str, *, optional: bool, kind: str = "Attribute", cardinality: str = "one") -> dict[str, Any]:
    return {"peer": peer, "cardinality": cardinality, "optional": optional, "kind": kind, "read_only": False}


def _kind(
    human_friendly_id: list[str],
    attributes: Mapping[str, Any],
    relationships: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "human_friendly_id": human_friendly_id,
        "uniqueness_constraints": [],
        "attributes": dict(attributes),
        "relationships": dict(relationships or {}),
    }


def schema_record() -> dict[str, Any]:
    """The destination schema record, in the JSON shape the schema read stores.

    `LocationSite.status` is a Dropdown and carries its `choices` (research R6). The schema
    read of the current code does not store choices yet; normalization ignores the key.
    """
    return {
        "LocationSite": _kind(
            ["name__value"],
            {
                "name": _attribute("Text", unique=True),
                "status": _attribute("Dropdown", choices=SITE_STATUS_CHOICES),
            },
        ),
        "IpamVRF": _kind(["name__value"], {"name": _attribute("Text", unique=True)}),
        "LocationRack": _kind(
            ["name__value"],
            {
                "name": _attribute("Text"),
                "facility_id": _attribute("Text"),
                "description": _attribute("Text", optional=True),
            },
        ),
        "DcimDevice": _kind(
            ["name__value"],
            {
                "name": _attribute("Text"),
                "serial": _attribute("Text"),
                "position": _attribute("Number", optional=True),
            },
        ),
        "InterfacePhysical": _kind(
            ["device__name__value", "name__value"],
            {"name": _attribute("Text")},
            {"device": _relationship("DcimDevice", optional=False, kind="Parent")},
        ),
        "InterfaceVirtual": _kind(
            ["parent__name__value", "name__value"],
            {"name": _attribute("Text")},
            {"parent": _relationship("InterfacePhysical", optional=False, kind="Parent")},
        ),
        "IpamIPAddress": _kind(
            ["address__value"],
            {"address": _attribute("IPHost", unique=True)},
            {
                "device": _relationship("DcimDevice", optional=True),
                "vrf": _relationship("IpamVRF", optional=True),
            },
        ),
    }


# ---------------------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------------------


def _entry(
    kind: str,
    identifiers: list[str],
    fields: Mapping[str, str | None],
    *,
    filters: list[SchemaMappingFilter] | None = None,
) -> SchemaMappingModel:
    """One mapping entry; a field's value is its `reference`, or `None` for an attribute."""
    return SchemaMappingModel(
        name=kind,
        mapping=RESOURCES[kind],
        identifiers=identifiers,
        filters=filters,
        fields=[SchemaMappingField(name=name, mapping=name, reference=reference) for name, reference in fields.items()],
    )


def schema_mapping() -> list[SchemaMappingModel]:
    """The mapping of the seven kinds. `DcimDevice` filters out the `lab` role."""
    return [
        _entry("LocationSite", ["name"], {"name": None, "status": None}),
        _entry("IpamVRF", ["name"], {"name": None}),
        _entry("LocationRack", ["name", "facility_id"], {"name": None, "facility_id": None, "description": None}),
        _entry(
            "DcimDevice",
            ["name", "serial"],
            {"name": None, "serial": None, "position": None},
            filters=[SchemaMappingFilter(field="role", operation="!=", value=EXCLUDED_DEVICE_ROLE)],
        ),
        _entry("InterfacePhysical", ["name", "device"], {"name": None, "device": "DcimDevice"}),
        _entry("InterfaceVirtual", ["name", "parent"], {"name": None, "parent": "InterfacePhysical"}),
        _entry("IpamIPAddress", ["address"], {"address": None, "device": "DcimDevice", "vrf": "IpamVRF"}),
    ]


def build_config() -> SyncConfig:
    """The fixture configuration.

    It declares no credential. A test that needs the two fixture credentials declares them as
    referenced credentials named `SHORT_CREDENTIAL_NAME` and `SPECIAL_CREDENTIAL_NAME`.
    """
    return SyncConfig(
        name=SYNC_NAME,
        source=SyncAdapter(name="problem_fixture", settings={"url": "https://source.invalid"}),
        destination=SyncAdapter(name="infrahub", settings={"url": "https://destination.invalid"}),
        order=list(ORDER),
        schema_mapping=schema_mapping(),
    )


# ---------------------------------------------------------------------------------------
# Source records
# ---------------------------------------------------------------------------------------


def _ref(source_id: str) -> dict[str, Any]:
    return {"id": int(source_id)}


def _valid_records() -> dict[str, list[dict[str, Any]]]:
    return {
        "LocationSite": [
            {"id": 1, "name": "paris", "status": "active"},
            {"id": 2, "name": "lyon", "status": "planned"},
        ],
        "IpamVRF": [{"id": 10, "name": "default"}],
        "LocationRack": [{"id": 20, "name": "r1", "facility_id": "F1", "description": "row 1"}],
        "DcimDevice": [
            {"id": 31, "name": "leaf-1", "serial": "L1", "position": 3, "role": "leaf"},
            {"id": 32, "name": "leaf-2", "serial": "L2", "position": None, "role": "leaf"},
        ],
        "InterfacePhysical": [
            {"id": 50, "name": "eth0", "device": _ref("31")},
            {"id": 51, "name": "eth1", "device": _ref("31")},
        ],
        "InterfaceVirtual": [{"id": 70, "name": "eth0.10", "parent": _ref("50")}],
        "IpamIPAddress": [{"id": 80, "address": "10.0.0.1/32", "device": _ref("31"), "vrf": _ref("10")}],
    }


def _problem_device() -> dict[str, Any]:
    """`core-1`: `position` 2.5 is refused by the Number attribute (`wrong_value_type`)."""
    return {"id": int(PROBLEM_DEVICE_SOURCE_ID), "name": "core-1", "serial": "C1", "position": 2.5, "role": "core"}


def _problem_records() -> dict[str, list[dict[str, Any]]]:
    return {
        "LocationSite": [
            {"id": 3, "name": "berlin", "status": "decommissioning"},
            {"id": 4, "name": "dup", "status": "active"},
            {"id": 5, "name": "dup", "status": "planned"},
            {"id": int(TWIN_SITE_SOURCE_ID), "name": TWIN_SITE_NAME, "status": "active"},
        ],
        "LocationRack": [
            {"id": 21, "name": "comms-closet", "facility_id": "F1", "description": "first floor"},
            {"id": 22, "name": "comms-closet", "facility_id": "F2", "description": "second floor"},
        ],
        "DcimDevice": [
            _problem_device(),
            {"id": 33, "name": "edge", "serial": "E1", "position": 1, "role": "edge"},
            {"id": 34, "name": "edge", "serial": "E2", "position": 2, "role": "edge"},
            # Removed by the `role != lab` filter; not a record with a problem itself.
            {"id": 35, "name": "lab-1", "serial": "B1", "position": 1, "role": EXCLUDED_DEVICE_ROLE},
        ],
        "InterfacePhysical": [
            {"id": 60, "name": "mgmt0", "device": _ref("35")},
            {"id": 61, "name": "eth9", "device": _ref(MISSING_DEVICE_SOURCE_ID)},
        ],
    }


def _dependents_of_problem_device() -> dict[str, list[dict[str, Any]]]:
    return {
        "InterfacePhysical": [
            {"id": 40 + index, "name": f"eth{index}", "device": _ref(PROBLEM_DEVICE_SOURCE_ID)}
            for index in range(PROBLEM_DEVICE_DEPENDENTS)
        ],
        "IpamIPAddress": [
            {"id": 81, "address": "10.0.0.5/32", "device": _ref(PROBLEM_DEVICE_SOURCE_ID), "vrf": None},
            {"id": 82, "address": "10.0.0.9/32", "device": None, "vrf": _ref(MISSING_VRF_SOURCE_ID)},
        ],
    }


def _merge(*parts: Mapping[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    merged: dict[str, list[dict[str, Any]]] = {kind: [] for kind in ORDER}
    for part in parts:
        for kind, records in part.items():
            merged[kind].extend(copy.deepcopy(records))
    return merged


def source_records(variant: Variant = Variant.ALL) -> dict[str, list[dict[str, Any]]]:
    """The raw records the source returns for `variant`, by kind, before any filter."""
    if variant is Variant.NO_PROBLEMS:
        return _merge(_valid_records())
    if variant is Variant.EVERY_RECORD_HAS_A_PROBLEM:
        return _merge(_problem_records())
    if variant is Variant.DEPENDENCY_CHAIN:
        return _merge(
            {
                "DcimDevice": [_problem_device()],
                "InterfacePhysical": [{"id": 40, "name": "eth0", "device": _ref(PROBLEM_DEVICE_SOURCE_ID)}],
                "InterfaceVirtual": [{"id": 71, "name": "eth0.100", "parent": _ref("40")}],
            }
        )
    return _merge(_valid_records(), _problem_records(), _dependents_of_problem_device())


def twin_site_nodes() -> list[dict[str, Any]]:
    """Two Infrahub `LocationSite` nodes with the one identity `name: twin` (`destination_identity_ambiguous`)."""
    return [
        {"local_id": node_id, "name": TWIN_SITE_NAME, "status": status}
        for node_id, status in zip(TWIN_SITE_NODE_IDS, ("active", "planned"), strict=True)
    ]


def destination_records(records: Mapping[str, list[dict[str, Any]]] | None = None) -> dict[str, list[dict[str, Any]]]:
    """Nodes Infrahub already holds, as model data by kind, for the source `records` (the full fixture by default).

    `LocationRack comms-closet` at facility `F1` exists with another description, so the
    source's `F1` rack is an update and its `F2` rack a create: two source identities that
    Infrahub stores as one node (`identities_merge_in_destination`). When the source returns
    the site `twin`, Infrahub holds two nodes with that identity (`destination_identity_ambiguous`).
    """
    sites = (records if records is not None else source_records()).get("LocationSite", [])
    twin = [] if all(site.get("name") != TWIN_SITE_NAME for site in sites) else twin_site_nodes()
    return {
        **({"LocationSite": twin} if twin else {}),
        "LocationRack": [
            {
                "local_id": "18d52a8a-0000-0000-0000-000000000021",
                "name": "comms-closet",
                "facility_id": "F1",
                "description": "old description",
            }
        ],
    }


# ---------------------------------------------------------------------------------------
# Adapter and models
# ---------------------------------------------------------------------------------------


class ProblemFixtureModel(DiffSyncModelMixin, DiffSyncModel):
    """Model base of the fixture adapter's runtime models."""


class ProblemFixtureAdapter(DiffSyncMixin, Adapter):
    """In-memory source adapter that implements `list_records` and `record_to_data` only."""

    type = "ProblemFixture"

    def __init__(
        self,
        target: str,
        adapter: SyncAdapter,
        config: SyncConfig,
        *,
        records: Mapping[str, list[dict[str, Any]]] | None = None,
    ) -> None:
        """Hold `records` by kind; `None` holds the full fixture."""
        super().__init__()
        # An instance attribute, as `Potenda` sets it: diffsync checks a class-level
        # `top_level` against class attributes, and the runtime models are bound per instance.
        self.top_level = list(ORDER)  # ty: ignore[invalid-attribute-access]
        self.target = target
        self.settings = dict(adapter.settings or {})
        self.config = config
        self._records = copy.deepcopy(dict(records) if records is not None else source_records())

    def list_records(self, model_name: str, element: SchemaMappingModel) -> list[dict[str, Any]]:  # noqa: ARG002
        """Return copies of the raw records of `model_name`."""
        return copy.deepcopy(self._records.get(model_name, []))

    def record_to_data(
        self, record: Mapping[str, Any], element: SchemaMappingModel, model: type[DiffSyncModel]
    ) -> dict[str, Any]:
        """Convert one raw record to model data; references resolve through the base class."""
        source_id = str(record["id"])
        data: dict[str, Any] = {"local_id": source_id}
        for field in element.fields:
            if field.mapping is None:
                continue
            value = get_value(record, field.mapping)
            if field.reference is None:
                data[field.name] = value
                continue
            if not value:
                continue
            data[field.name] = self._resolve_peer(
                field.reference,
                str(value["id"]),
                owner=(element.name, source_id),
                field=field.name,
                required=reference_is_required(model, field.name),
            )
        return data

    def _resolve_peer(
        self,
        peer_kind: str,
        peer_source_id: str,
        *,
        owner: tuple[str, str],
        field: str,
        required: bool,
    ) -> str | None:
        """Resolve a reference through the base class's `peer_unique_id`.

        Until the base class has `peer_unique_id` (spec 009 T015), this scans the store by
        `local_id`, so the valid variant can be loaded with the current code.
        """
        resolver = getattr(self, "peer_unique_id", None)
        if resolver is not None:
            return resolver(peer_kind, peer_source_id, owner=owner, field=field, required=required)
        for item in self.store.get_all(model=peer_kind):
            if getattr(item, "local_id", None) == peer_source_id:
                return item.get_unique_id()
        if required:
            msg = f"{owner[0]} record {owner[1]} field {field}: no {peer_kind} with source ID {peer_source_id}"
            raise LookupError(msg)
        return None


def build_models(config: SyncConfig | None = None) -> dict[str, type[DiffSyncModel]]:
    """Build the runtime models of the seven kinds from the destination schema record."""
    return build_runtime_models(
        snapshot=normalize_destination_schema(schema_record()),
        configuration=config or build_config(),
        model_base=ProblemFixtureModel,
    )


def build_source_adapter(variant: Variant = Variant.ALL, *, config: SyncConfig | None = None) -> ProblemFixtureAdapter:
    """A source adapter holding `variant`, with the runtime models bound."""
    configuration = config or build_config()
    adapter = ProblemFixtureAdapter(
        target="source",
        adapter=configuration.source,
        config=configuration,
        records=source_records(variant),
    )
    bind_runtime_models(adapter, build_models(configuration))
    return adapter


__all__ = [
    "EXCLUDED_DEVICE_ROLE",
    "MISSING_DEVICE_SOURCE_ID",
    "MISSING_VRF_SOURCE_ID",
    "ORDER",
    "PROBLEM_DEVICE_DEPENDENTS",
    "PROBLEM_DEVICE_SOURCE_ID",
    "PROBLEM_TYPES",
    "SHORT_CREDENTIAL",
    "SHORT_CREDENTIAL_NAME",
    "SITE_STATUS_CHOICES",
    "SPECIAL_CREDENTIAL",
    "SPECIAL_CREDENTIAL_NAME",
    "TWIN_SITE_NAME",
    "TWIN_SITE_NODE_IDS",
    "TWIN_SITE_SOURCE_ID",
    "ProblemFixtureAdapter",
    "ProblemFixtureModel",
    "Variant",
    "build_config",
    "build_models",
    "build_source_adapter",
    "destination_records",
    "schema_mapping",
    "schema_record",
    "source_records",
    "twin_site_nodes",
]
