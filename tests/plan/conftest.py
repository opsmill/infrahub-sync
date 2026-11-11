"""Loader for the shared plan problem fixture (spec 019 T002).

The data lives in `tests/data/plan_problems/`:

- `destination_schema.json`: the destination schema record, in the JSON shape that
  `normalize_destination_schema` reads. `LocationSite.status` is a Dropdown and carries its
  `choices`; today's normalization ignores that key (spec 019 research R4).
- `sync_config.json`: the configuration of the seven kinds. `DcimDevice` filters out the role
  `lab`, and `IpamVRF` maps its name through a transform that knows two names only.
- `source_records.json`: the raw source records by group and kind, as a source adapter lists
  them before any filter. A peer is `{"id": <source ID>}`.
- `destination_records.json`: the nodes Infrahub already holds, as model data by kind.
- `expected.json`: the expected problems, dependent left-out records and kept records of the
  full fixture.

Problem records of the full fixture (`Variant.ALL`):

| Problem type | Record | How |
| --- | --- | --- |
| `dropdown_value_not_in_choices` | `LocationSite` 3 `berlin` | status `decommissioning` is not a choice |
| `duplicate_source_identity` | `LocationSite` 4 and 5 `dup` | same mapping identity `name` |
| `destination_identity_collision` | `DcimDevice` 33 and 34 `edge` | serials differ, the human-friendly ID `name` is equal |
| `wrong_value_type` | `DcimDevice` 30 `core-1` | `position` is `2.5` for a Number |
| `required_peer_excluded_by_filter` | `InterfacePhysical` 60 `mgmt0` | its required `device` 35 is removed by the filter |
| `identities_merge_in_destination` | `LocationRack` 21 and 22 `comms-closet` | Infrahub holds one node for both |
| `peer_not_found_in_source` | `DcimDevice` 36 `spine-1` | its required `site` 997 is not in the source |
| `peer_not_found_in_source` | `IpamIPAddress` 82 `10.0.0.9/32` | its optional `vrf` 998 is not in the source |
| `destination_identity_ambiguous` | `LocationSite` 6 `twin` | Infrahub holds two `LocationSite` nodes named `twin` |
| `transform_failed` | `IpamVRF` 11 `q-in-q` | the `name` transform knows `default` and `mgmt` only |

The nine problem types give ten problem entries: `peer_not_found_in_source` has one record
through a required relationship (the device whose site is missing, left out) and one through
an optional relationship (the IP address whose VRF is missing, kept without the VRF).

Dependents of `core-1`: five `InterfacePhysical` records (40 to 44) through the required
relationship `device`. `IpamIPAddress` 81 refers to `core-1` only through the optional
relationship `device`, so it is kept without that relationship.

The fixture holds data and builders only. It does not hold a source adapter, because the
base-class read loop that such an adapter needs does not exist before spec 019 T017; a test
drives an existing adapter (for example NetBox with a mocked client) or its own.
"""

from __future__ import annotations

import copy
import enum
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from diffsync import DiffSyncModel

from infrahub_sync import DiffSyncModelMixin, SyncConfig
from infrahub_sync.runtime_schema.domain import DestinationSchemaSnapshot, normalize_destination_schema
from infrahub_sync.runtime_schema.models import build_runtime_models

PLAN_PROBLEMS_DIR = Path(__file__).resolve().parent.parent / "data" / "plan_problems"

# The nine stable problem type names of data-model.md.
PROBLEM_TYPES: tuple[str, ...] = (
    "dropdown_value_not_in_choices",
    "duplicate_source_identity",
    "destination_identity_collision",
    "wrong_value_type",
    "required_peer_excluded_by_filter",
    "identities_merge_in_destination",
    "peer_not_found_in_source",
    "destination_identity_ambiguous",
    "transform_failed",
)

# The site name whose two Infrahub nodes make `destination_identity_ambiguous`.
TWIN_SITE_NAME = "twin"


class Variant(enum.Enum):
    """Which groups of `source_records.json` the source returns."""

    ALL = ("valid", "problems", "dependents")
    """Valid records, the problem records and the dependents of `core-1`."""

    NO_PROBLEMS = ("valid",)
    """Valid records only."""

    DEPENDENCY_CHAIN = ("dependency_chain",)
    """Three levels: `core-1` (problem) <- `InterfacePhysical eth0` <- `InterfaceVirtual eth0.100`."""


class PlanProblemModel(DiffSyncModelMixin, DiffSyncModel):
    """Default model base of the fixture's runtime models."""


def _read_json(name: str) -> dict[str, Any]:
    return json.loads((PLAN_PROBLEMS_DIR / name).read_text(encoding="utf-8"))


@dataclass(frozen=True)
class PlanProblemFixture:
    """The shared problem fixture, read once per test from `tests/data/plan_problems/`."""

    schema_record: dict[str, Any]
    config_data: dict[str, Any]
    record_groups: dict[str, dict[str, list[dict[str, Any]]]]
    destination_data: dict[str, list[dict[str, Any]]]
    expected: dict[str, Any]

    @property
    def order(self) -> list[str]:
        """The load order: every kind loads after the kinds it refers to."""
        return list(self.config_data["order"])

    def config(self) -> SyncConfig:
        """A new parsed configuration of the fixture."""
        return SyncConfig(**copy.deepcopy(self.config_data))

    def snapshot(self) -> DestinationSchemaSnapshot:
        """The destination schema record, normalized."""
        return normalize_destination_schema(copy.deepcopy(self.schema_record))

    def build_models(
        self, *, model_base: type[DiffSyncModel] = PlanProblemModel, config: SyncConfig | None = None
    ) -> dict[str, type[DiffSyncModel]]:
        """The runtime models of the seven kinds, over `model_base`."""
        return build_runtime_models(
            snapshot=self.snapshot(), configuration=config or self.config(), model_base=model_base
        )

    def source_records(self, variant: Variant = Variant.ALL) -> dict[str, list[dict[str, Any]]]:
        """The raw records the source returns for `variant`, by kind in load order, before any filter."""
        merged: dict[str, list[dict[str, Any]]] = {kind: [] for kind in self.order}
        for group in variant.value:
            for kind, records in self.record_groups[group].items():
                merged[kind].extend(copy.deepcopy(records))
        return merged

    def destination_records(
        self, records: dict[str, list[dict[str, Any]]] | None = None
    ) -> dict[str, list[dict[str, Any]]]:
        """Nodes Infrahub already holds for the source `records` (the full fixture by default).

        The `comms-closet` rack at facility `F1` always exists, with another description. The two
        `twin` sites exist only when the source returns the site `twin`.
        """
        sites = (records if records is not None else self.source_records()).get("LocationSite", [])
        held = copy.deepcopy(self.destination_data)
        if all(site.get("name") != TWIN_SITE_NAME for site in sites):
            held.pop("LocationSite", None)
        return held

    def expected_problems(self) -> list[dict[str, Any]]:
        """One entry per expected problem of the full fixture: type, kind, source IDs and field."""
        return copy.deepcopy(self.expected["problems"])

    def expected_left_out_dependents(self) -> list[dict[str, Any]]:
        """The records of the full fixture left out because they depend on a left-out record."""
        return copy.deepcopy(self.expected["left_out_dependents"])

    def expected_kept(self) -> list[dict[str, Any]]:
        """The records of the full fixture kept without an optional relationship."""
        return copy.deepcopy(self.expected["kept"])


def load_plan_problem_fixture() -> PlanProblemFixture:
    """Read the fixture files."""
    return PlanProblemFixture(
        schema_record=_read_json("destination_schema.json"),
        config_data=_read_json("sync_config.json"),
        record_groups=_read_json("source_records.json"),
        destination_data=_read_json("destination_records.json"),
        expected=_read_json("expected.json"),
    )


@pytest.fixture
def plan_problems() -> PlanProblemFixture:
    """The shared plan problem fixture."""
    return load_plan_problem_fixture()
