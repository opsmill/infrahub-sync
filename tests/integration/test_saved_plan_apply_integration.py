"""Phase H — the six criteria and half-criteria that need a running Infrahub.

**Authored, not satisfied (AD045b) — and now, for five of the six, satisfied (AD091).** No live
Infrahub was reachable in the environment this feature was built in (AD007). One later was, and
this module was run against it: **`7 passed, 1 error`**. SC-001, SC-002, SC-003, SC-008 and
SC-007's live half **pass live**, which closes DBA-001, DBA-002, DBA-003, DBA-008 and DBA-007's
live half. SC-016's live half does **not**: seeding a genuinely ambiguous peer needs a referenced
kind whose uniqueness constraints do not cover the components the resolver filters on, and every
kind the qualified configuration touches declares one that does — the destination answers the clone
with `Violates uniqueness constraint 'device-name'`. That precondition is left exactly as written.
Every test here stays `integration`-marked and skips itself when the destination environment is
not configured, so a default `uv run pytest -q` still reports them as skips and the offline suite
is unchanged. The offline conformance harness at `tests/plan/test_apply_conformance.py` narrowed
the exposure by asserting the mutation the SDK renders; it never closed it, and nothing here was
ever reportable as covered on its strength.

**Amended by AD090: "authored, not satisfied" was too weak a claim.** The first live run errored
in fixture setup on every test here, because the fixture wrote its bounded configuration into a
workspace and never generated the adapter code `import_adapter` resolves per-kind model classes
from. These tests were not merely unexecuted — they were **non-functional**, and only executing
them could reveal that. A test that has never run is not evidence of anything, including of its
own validity. `_generate_adapters` closes that gap; nothing else about the paragraph above
changes, and no assertion below was touched.

**Amended by AD092: SC-016's live half is guarded, not left erroring.**
`test_an_ambiguous_peer_refuses_the_operation` now states its unsatisfiability as a precondition
checked against the destination schema — `_ambiguous_peer_or_skip`, over
`covering_uniqueness_constraint` — so on a destination where no referenced peer kind admits an
ambiguity it **skips** naming the covering uniqueness constraint that establishes it, instead of
erroring on a clone the schema was always going to refuse. Nothing in the test body is weakened,
mocked or deleted: the check is over the schema as data, and a schema declaring a referenced kind
whose constraints leave a filtered component free runs the whole test rather than skipping it.

Run them with a reachable destination and source::

    export INFRAHUB_ADDRESS="http://localhost:8000"
    export INFRAHUB_API_TOKEN="<token>"
    export NETBOX_URL="https://demo.netbox.dev"
    export NETBOX_TOKEN="<token>"
    uv run pytest -m integration tests/integration/test_saved_plan_apply_integration.py

**These tests write to the destination.** SC-002 and SC-003 measure convergence, which is
not observable without writing, so point them at a disposable Infrahub — the same posture
`tests/integration/test_infrahub_node_to_diffsync_integration.py` already takes when it
loads a throwaway schema and creates nodes against it.

**Amended by AD091: the slice had to widen, because its own precondition refused it.** The
earlier seven-entry slice ended at `DcimDevice`, and every kind `DcimDevice` references —
`LocationRack`, `LocationSite`, `DcimDeviceType`, `DcimPlatform`, `BuiltinTag` — carries an
**all-direct** destination human-friendly ID (`['name__value']`). So `_require_preexisting_peer`
refused the fixture: with no relationship-crossing peer, SC-008's nested identity walk and
PD-004's nested `<rel>__<attr>__value` filter spelling would have passed vacuously. The refusal
was correct, and only live data could raise it, because which kinds have such a human-friendly ID
is a fact about the **destination schema** and not about the configuration's `identifiers` lists —
the same conflation AD091 corrects in the run's records. `InterfacePhysical` is the kind that
supplies what was missing: two mapped kinds reference a kind whose destination human-friendly ID
crosses a relationship — `DcimDevice.primary_address` → `IpamIPAddress` and
`InterfacePhysical.bundle` → `InterfaceLag` — but `IpamIPAddress`'s plan identity
(`identifiers: ["address", "vrf"]`) supplies no `ip_namespace` component at all, so no crossing
filter can be formed for it and its own operations would be refused by
`assert_convergence_key_is_supplied` first. `InterfaceLag`'s crossing component is
`device__name__value`, its plan identity (`["device", "name"]`) supplies it, and it resolves
against `DcimDevice` — already in the slice. `DcimDevice` and `InterfaceLag` therefore move into
the seed. No assertion and no precondition below was weakened to get there.

The fixture runs the qualified path (`examples/netbox_to_infrahub/config.yml`, NetBox →
Infrahub) narrowed to ten of its schema-mapping entries, copied **verbatim** apart from the
documented field removals (`DROPPED_FIELDS`) and the documented source-side bounding filter
(`ADDED_FILTERS`): `BuiltinTag`, `LocationSite`, `LocationRack`, `OrganizationManufacturer`,
`DcimPlatform`, `DcimDeviceType`, both `DcimDevice` entries, `InterfaceLag` and
`InterfacePhysical`. The slice is the **reference closure** of `InterfacePhysical`, so no kind
outside it is needed to resolve a peer, and it is the smallest closure that carries every
shape this phase measures: a create with no references (`BuiltinTag`), an update
(`LocationSite`), a relationship-bearing kind with a cardinality-one **and** a
cardinality-many reference (`InterfacePhysical` — `device`, `bundle`; `DcimDevice` — `tags`),
and — the one SC-008 turns on — a *referenced* kind whose **destination human-friendly ID
crosses a relationship** (`InterfaceLag`, `['device__name__value', 'name__value']`), so a peer
identity is a nested `{peer_kind, identity}` pair, the destination query that resolves it has
to be spelled the nested way, and AD043's recursive resolution is exercised rather than
declared.

The plan under test is built in two phases, because three of its properties cannot be
arranged after it exists:

1. a **seed** run applies every kind except `InterfacePhysical`, so the devices, LAG
   interfaces, racks, device types, platforms and tags a physical interface references already
   exist at the destination *and no operation in the plan under test creates them* — the
   pre-existing peers SC-008 requires, without which dependency-tier ordering fills the
   resolver's memo from the plan's own creates and the destination-query path under test never
   runs;
2. the destination is then perturbed three ways — one unreferenced tag removed, one site's
   description changed, one tag created that the source does not have — so the plan under
   test carries a plain create, an update and a delete;
3. the plan under test is derived over all ten entries, which adds the relationship-bearing
   `InterfacePhysical` creates.
"""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
import yaml
from diffsync import Adapter

from infrahub_sync import DiffSyncMixin
from infrahub_sync.adapters.infrahub import InfrahubAdapter
from infrahub_sync.plan import read_saved_plan
from infrahub_sync.plan.errors import OperationApplyFailedError, PeerAmbiguousError
from infrahub_sync.utils import find_missing_schema_model, get_instance, get_potenda_from_instance, render_adapter
from tests.plan.ownership_fixtures import granted_ownership

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence

    from infrahub_sync import SyncInstance
    from infrahub_sync.plan.models import PlannedOperation, RelationshipReference
    from infrahub_sync.plan.review import SavedPlan

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
QUALIFIED_CONFIG = REPO_ROOT / "examples" / "netbox_to_infrahub" / "config.yml"

DESTINATION_BRANCH = "main"

# The bounded slice of the qualified configuration, and the role each kind plays.
TAG_KIND = "BuiltinTag"
SITE_KIND = "LocationSite"
DEVICE_KIND = "DcimDevice"
# The pre-existing peer whose destination human-friendly ID crosses a relationship (AD091).
BUNDLE_KIND = "InterfaceLag"
RELATIONSHIP_KIND = "InterfacePhysical"

# Everything `InterfacePhysical` references, transitively. Seeded first, so every peer of an
# interface pre-exists the plan under test and none of them is created by it (SC-008).
SEED_KINDS = (
    TAG_KIND,
    SITE_KIND,
    "LocationRack",
    "OrganizationManufacturer",
    "DcimPlatform",
    "DcimDeviceType",
    DEVICE_KIND,
    BUNDLE_KIND,
)
KINDS_UNDER_TEST = (*SEED_KINDS, RELATIONSHIP_KIND)

# The first departure from copying the qualified configuration verbatim, and why. Every dropped
# field references `IpamIPAddress` or `IpamVLAN`, whose own closures pull in `IpamVRF`,
# `IpamRouteTarget`, `IpamVLANGroup` and, on the NetBox demo data, some thousands of addresses —
# none of which any of these six criteria measures. Dropping them bounds the live run; every
# reference that carries SC-008's nested peer identity — `DcimDevice.location`,
# `DcimDevice.device_type`, `InterfacePhysical.device` and `InterfacePhysical.bundle` — is
# untouched.
DROPPED_FIELDS: Mapping[str, tuple[str, ...]] = {
    DEVICE_KIND: ("primary_address",),
    RELATIONSHIP_KIND: ("ip_addresses",),
    BUNDLE_KIND: ("ip_addresses", "untagged_vlan", "tagged_vlan"),
}

# The second departure, and why (AD091). It has two independent grounds, and the second is a
# defect the first live run exposed rather than a matter of run time.
#
# **Ground one — size.** The qualified configuration maps 1727 physical interfaces on the NetBox
# demo data, and the tests below apply the plan between one and six times each. Writing that
# population twenty-odd times measures nothing these criteria do not already measure at one
# device, and would put the live run into the hours. `BOUNDING_DEVICE` is chosen because two of
# its physical interfaces are members of its one LAG interface, which is what makes the
# relationship-crossing peer SC-008 needs exist at all.
#
# **Ground two — `LocationRack` is not convergent on the qualified path against this destination
# schema, and that is a separate, now-recorded finding rather than something this fixture may
# assert away.** Destination `LocationRack` declares `human_friendly_id: ['name__value']` and
# `uniqueness_constraints: [['name__value']]` — keyed on the rack name **alone** — while the
# configuration's identity for it is `identifiers: ["name", "site"]`. Thirteen NetBox demo racks
# are named `Comms closet`, one per site, so thirteen distinct plan identities converge onto one
# destination object whose `site` is whichever operation wrote last. Every re-derived plan then
# reports a create *and* a delete for the same rack, and the churn cascades: `DcimDevice.location`
# nests the rack identity, and `InterfaceLag` / `InterfacePhysical` nest the device identity, so
# no kind in that chain can ever be seeded out of the plan. Note which way the mismatch runs:
# FR-024's warning fires when the destination's key is **coarser than the plan can key on**
# (`constraint <= identity` is what `warn_missing_convergence_key` tests), and here the constraint
# *is* a subset of the identity, so both of its arms stay silent. Bounding both rack-bearing
# entries to one site puts exactly one `Comms closet` in scope, which converges. The defect is not
# fixed here and not hidden: it is recorded in the run's own records against this run's live
# evidence, and none of the six criteria below is about it.
#
# Same species of documented departure as `DROPPED_FIELDS`, and asserted the same way: a filter
# that stops matching leaves the plan with no relationship-bearing operation, which is a loud
# setup error in `live_plan` naming the device.
BOUNDING_DEVICE = "cisco1"
BOUNDING_SITE = "dm-akron"
ADDED_FILTERS: Mapping[str, tuple[dict[str, Any], ...]] = {
    "LocationRack": ({"field": "site.slug", "operation": "==", "value": BOUNDING_SITE},),
    DEVICE_KIND: ({"field": "site.slug", "operation": "==", "value": BOUNDING_SITE},),
    RELATIONSHIP_KIND: ({"field": "device.name", "operation": "==", "value": BOUNDING_DEVICE},),
    BUNDLE_KIND: ({"field": "device.name", "operation": "==", "value": BOUNDING_DEVICE},),
}

# The environment both sides of the qualified path need. Missing any one of them skips.
REQUIRED_ENVIRONMENT = ("INFRAHUB_ADDRESS", "INFRAHUB_API_TOKEN", "NETBOX_URL", "NETBOX_TOKEN")

_COMPONENT_SEPARATOR = "__"
_VALUE_SUFFIX = "value"
_UNSUPPLIED = object()


class LivePlanPreconditionError(Exception):
    """A condition the live fixture must establish before any assertion below means anything.

    Raised from a **fixture**, never from a test body, so pytest reports it as a setup error
    naming what could not be established rather than as a test failure. The distinction is
    the point of T074: an unkeyed upsert produces destination duplicates that read exactly
    like a product bug, so the state that would cause them is reported as a broken fixture
    and never as a broken feature.
    """


class ExtractionCalledError(AssertionError):
    """A comparison-engine or adapter load call was made on the apply path (FR-012, SC-001)."""


class InjectedCrashError(RuntimeError):
    """SC-003's crash window: raised inside the apply loop at a chosen point."""


# ---------------------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------------------


def _env_or_skip() -> dict[str, str]:
    """The live environment, or skip — the pattern of the sibling integration module."""
    values = {name: os.environ.get(name) or "" for name in REQUIRED_ENVIRONMENT}
    missing = [name for name, value in values.items() if not value]
    if missing:
        pytest.skip(
            f"{', '.join(REQUIRED_ENVIRONMENT)} must be set to run the saved-plan apply integration "
            f"tests; missing: {', '.join(missing)}"
        )
    return values


def _destination_client(environment: Mapping[str, str]) -> Any:  # noqa: ANN401 — the SDK client is dynamically typed
    """A sync Infrahub client for reading the destination back, imported lazily."""
    from infrahub_sdk import Config, InfrahubClientSync

    return InfrahubClientSync(
        config=Config(address=environment["INFRAHUB_ADDRESS"], api_token=environment["INFRAHUB_API_TOKEN"])
    )


# ---------------------------------------------------------------------------------------
# The qualified configuration, bounded
# ---------------------------------------------------------------------------------------


def _write_bounded_config(path: Path, *, name: str, kinds: Sequence[str], environment: Mapping[str, str]) -> Path:
    """Write the qualified configuration narrowed to `kinds`, pointed at the live systems.

    The retained `schema_mapping` entries are copied **verbatim** apart from `DROPPED_FIELDS`
    and `ADDED_FILTERS`; only `name` and the two `settings` blocks are rewritten, so the
    mappings under test are the shipped ones rather than a paraphrase. The name is unique per
    run, so this cache root cannot collide with an operator's own `from-netbox` runs.

    A dropped field that is not there to drop is a setup error rather than a silent no-op: if
    the qualified configuration renames it, the slice quietly stops being bounded and the live
    run pulls in a closure this phase never intended to write.

    A bounding filter is **appended** to whatever filters the shipped entry already declares,
    never substituted for them, so the entry keeps every exclusion it ships with (AD091).
    """
    document = yaml.safe_load(QUALIFIED_CONFIG.read_text(encoding="utf-8"))
    entries = [entry for entry in document["schema_mapping"] if entry["name"] in kinds]
    absent = sorted(set(kinds) - {entry["name"] for entry in entries})
    if absent:
        msg = (
            f"The qualified configuration at {QUALIFIED_CONFIG} declares no schema-mapping entry for "
            f"{', '.join(absent)}, so the bounded slice this phase runs against cannot be built."
        )
        raise LivePlanPreconditionError(msg)
    for entry in entries:
        for field in DROPPED_FIELDS.get(entry["name"], ()):
            retained = [item for item in entry["fields"] if item["name"] != field]
            if len(retained) == len(entry["fields"]):
                msg = (
                    f"The {entry['name']!r} schema-mapping entry declares no field {field!r}, which this phase "
                    "drops to bound the live run. The bounding is stale — re-derive DROPPED_FIELDS against the "
                    "qualified configuration."
                )
                raise LivePlanPreconditionError(msg)
            entry["fields"] = retained
        added = ADDED_FILTERS.get(entry["name"], ())
        if added:
            entry["filters"] = [*(entry.get("filters") or ()), *(dict(item) for item in added)]
    document["name"] = name
    document["source"]["settings"] = {"url": environment["NETBOX_URL"], "token": environment["NETBOX_TOKEN"]}
    document["destination"]["settings"] = {
        "url": environment["INFRAHUB_ADDRESS"],
        "token": environment["INFRAHUB_API_TOKEN"],
        "branch": DESTINATION_BRANCH,
    }
    document["schema_mapping"] = entries
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path


def _generate_adapters(config_path: Path, destination_schema: Any) -> None:  # noqa: ANN401 — SDK schema mapping
    """Render the sync adapters the bounded configuration needs, beside it in the workspace.

    Without this step nothing in this module can run at all (AD090). `import_adapter`
    (`infrahub_sync/utils.py:72-98`) resolves the per-kind model classes from **generated** code
    at `<config directory>/<adapter name>/sync_adapter.py`, and when that file is absent it falls
    back to the plugin loader and returns a bare adapter carrying no per-kind attributes — so
    `DiffSyncMixin.load` fails on the first kind with `'NetboxAdapter' object has no attribute
    'BuiltinTag'`. Pointing the fixture at the adapters checked in under
    `examples/netbox_to_infrahub/` is not the alternative: those are stale, and declare kinds the
    qualified `config.yml` no longer maps.

    In process rather than through a subprocess: this is the same pair of calls the `generate`
    command makes once it holds a schema (`infrahub_sync/cli.py:781-787`), and the fixture already
    holds the destination schema, so shelling out would only add a second schema round trip and
    replace a typed precondition error with an exit code to parse.

    Rendered from the **widest** slice this module uses, `KINDS_UNDER_TEST`, and shared by the seed
    configuration: the retained schema-mapping entries are copied verbatim, so every kind the seed
    maps renders identically from either configuration, and an adapter class carrying model classes
    a configuration does not map is inert — `DiffSyncMixin.load` walks the configuration's own
    `top_level`, never the class's attributes. Rendering per configuration would instead be
    actively wrong: the generated adapter reaches its models through `from .sync_models import ...`,
    so the first import caches `<adapter>.sync_models` in `sys.modules` and a later re-render of the
    same package is never seen — the widened import fails, `import_adapter` swallows it as a
    warning, and the bare-adapter fallback returns with the original defect restored silently.
    """
    sync_instance = _sync_instance(config_path)
    missing_models = find_missing_schema_model(sync_instance=sync_instance, schema=destination_schema)
    if missing_models:
        msg = (
            f"The destination schema declares no kind for {', '.join(sorted(missing_models))}, which the bounded "
            f"configuration at {config_path} maps, so no adapter can be generated for it. The bounded slice is stale "
            "against this destination — re-derive KINDS_UNDER_TEST against the qualified configuration."
        )
        raise LivePlanPreconditionError(msg)

    render_adapter(sync_instance=sync_instance, schema=destination_schema)

    for adapter_name in (sync_instance.source.name, sync_instance.destination.name):
        generated = config_path.parent / adapter_name / "sync_adapter.py"
        if not generated.is_file():
            msg = (
                f"Generating the sync adapters for {config_path} produced no {generated}. `import_adapter` resolves "
                f"the per-kind model classes from that path and, when it is absent, silently falls back to a bare "
                f"{adapter_name!r} adapter that carries none of them — which fails inside `DiffSyncMixin.load` on the "
                "first kind. Nothing below could measure the apply path."
            )
            raise LivePlanPreconditionError(msg)


# ---------------------------------------------------------------------------------------
# Identity component paths — an independent implementation of AD043 / PD-004
# ---------------------------------------------------------------------------------------
#
# Written here rather than imported from `infrahub_sync.adapters.infrahub`: the precondition
# and the destination read-back are checks *on* the product's path walk and its filter
# spelling, and a check that reuses the implementation it is checking cannot fail when that
# implementation is wrong.


def _component_path(component: str) -> list[str]:
    """The field segments of a schema component path, with a trailing `value` dropped."""
    segments = component.split(_COMPONENT_SEPARATOR)
    if len(segments) > 1 and segments[-1] == _VALUE_SUFFIX:
        segments = segments[:-1]
    return segments


def _identity_path_value(identity: Mapping[str, Any], segments: Sequence[str]) -> Any:  # noqa: ANN401
    """The value a canonical plan identity supplies at `segments`, or `_UNSUPPLIED`.

    Recurses through the nested `{"peer_kind", "identity"}` pair an identity-bearing
    reference is recorded as (AD043), so a **data value** is never split on `__` — only the
    schema path is.
    """
    if not segments:
        return _UNSUPPLIED
    head, rest = segments[0], segments[1:]
    if head not in identity:
        return _UNSUPPLIED
    value = identity[head]
    if not rest:
        return _UNSUPPLIED if value is None else value
    if not isinstance(value, dict) or not isinstance(value.get("identity"), dict):
        return _UNSUPPLIED
    return _identity_path_value(value["identity"], rest)


def _filter_name(component: str) -> str:
    """The GraphQL filter kwarg a component path is queried by (PD-004)."""
    if component.rsplit(_COMPONENT_SEPARATOR, maxsplit=1)[-1] == _VALUE_SUFFIX:
        return component
    return f"{component}{_COMPONENT_SEPARATOR}{_VALUE_SUFFIX}"


def _human_friendly_id(schemas: Mapping[str, Any], kind: str) -> list[str]:
    """The destination kind's declared human-friendly-ID components, possibly empty."""
    node_schema = schemas.get(kind)
    return list(getattr(node_schema, "human_friendly_id", None) or ())


def _uniqueness_constraints(schemas: Mapping[str, Any], kind: str) -> list[list[str]]:
    """The destination kind's declared uniqueness constraints, each a list of component paths.

    Spelled the way the destination schema spells them: an attribute component carries the
    trailing `value` segment (`name__value`) while a relationship component is named on its
    own (`device`), which is why coverage below is decided over **paths** and not over strings.
    """
    node_schema = schemas.get(kind)
    return [list(constraint) for constraint in (getattr(node_schema, "uniqueness_constraints", None) or ())]


def _peer_kind_at(schemas: Mapping[str, Any], kind: str, path: Sequence[str]) -> str | None:
    """The destination kind reached by walking `path` from `kind` through relationships only.

    `None` as soon as a segment names something that is not a relationship of the kind reached
    so far, which is what stops an attribute path being read as a peer.
    """
    current: str | None = kind
    for segment in path:
        node_schema = schemas.get(current) if current is not None else None
        current = next(
            (
                getattr(relationship, "peer", None)
                for relationship in getattr(node_schema, "relationships", None) or ()
                if relationship.name == segment
            ),
            None,
        )
        if current is None:
            return None
    return current


def _pins_one_object(
    schemas: Mapping[str, Any],
    kind: str,
    path: tuple[str, ...],
    queried: set[tuple[str, ...]],
    seen: tuple[str, ...],
) -> bool:
    """Whether a query over `queried` fixes `path` on `kind` to one value or one peer object."""
    if path in queried:
        return True
    depth = len(path)
    nested = {candidate[depth:] for candidate in queried if candidate[:depth] == path and len(candidate) > depth}
    if not nested:
        return False
    peer_kind = _peer_kind_at(schemas, kind, path)
    if peer_kind is None:
        return False
    return _covering_constraint(schemas, peer_kind, nested, seen) is not None


def _covering_constraint(
    schemas: Mapping[str, Any],
    kind: str,
    queried: set[tuple[str, ...]],
    seen: tuple[str, ...] = (),
) -> list[str] | None:
    """The kind's first uniqueness constraint every component of which `queried` pins."""
    if kind in seen:
        # A cyclic reference between two kinds' constraints; nothing further is pinned by it.
        return None
    for constraint in _uniqueness_constraints(schemas, kind):
        if constraint and all(
            _pins_one_object(schemas, kind, tuple(_component_path(component)), queried, (*seen, kind))
            for component in constraint
        ):
            return constraint
    return None


def covering_uniqueness_constraint(
    *,
    destination_schema: Mapping[str, Any],
    kind: str,
    filters: Mapping[str, Any],
) -> list[str] | None:
    """The constraint that makes two objects of `kind` both matching `filters` impossible.

    `None` means this destination schema **admits an ambiguous peer** for that query: no
    declared uniqueness constraint is fully pinned by the components the resolver filters on,
    so a second matching object can be created and the multi-match state SC-016's live half
    measures is seedable. A returned constraint is the one the destination answers the second
    object with (`Violates uniqueness constraint`, HTTP 422), which is what makes a refusal to
    seed a **fact about the schema** rather than an absent environment (AD092).

    A component is pinned when the filters ask for it directly (`name__value`) or, for a
    relationship component named on its own (`device`), when the filters reach through it far
    enough to identify a single peer — decided by applying this same test to the peer kind.
    That recursion is the difference between this check and a prefix match: on a destination
    where device names are not themselves unique, `device__name__value` does **not** pin
    `device`, two interfaces could match, and the ambiguity would be seedable after all.

    Pure — no destination is contacted — so it is exercisable, and is exercised, offline in
    `tests/test_live_fixture_preconditions.py`: the check that decides whether SC-016's live
    half runs or skips is not itself carried only by the runs it guards.
    """
    return _covering_constraint(destination_schema, kind, {tuple(_component_path(name)) for name in filters})


def _identity_filters(schemas: Mapping[str, Any], kind: str, identity: Mapping[str, Any]) -> dict[str, Any]:
    """The destination query naming exactly the object a plan identity refers to.

    Built from the destination kind's own human-friendly ID, so a component crossing a
    relationship is spelled `<rel>__<attr>__value` and takes its value from the identity's
    nested pair — PD-004's spelling, which is only decidable against a live destination.
    """
    filters: dict[str, Any] = {}
    for component in _human_friendly_id(schemas, kind):
        value = _identity_path_value(identity, _component_path(component))
        if value is _UNSUPPLIED or isinstance(value, (dict, list, tuple)):
            continue
        filters[_filter_name(component)] = value
    if not filters:
        msg = (
            f"No destination filter can be formed for a {kind!r} with identity {dict(identity)!r}: the kind's "
            f"human-friendly ID is {_human_friendly_id(schemas, kind)} and the identity supplies no component "
            "of it as a scalar."
        )
        raise LivePlanPreconditionError(msg)
    return filters


def assert_convergence_key_is_supplied(
    *,
    operations: Sequence[PlannedOperation],
    destination_schema: Mapping[str, Any],
) -> None:
    """T074's precondition: every kind under test is convergently keyed by its own plan identity.

    Two conditions, checked per operation, each raising `LivePlanPreconditionError` naming
    the destination kind and the component that is missing:

    1. the destination kind declares a `human_friendly_id` at all;
    2. the plan's identity supplies **every one of its component paths**, following AD043's
       nesting for a component that crosses a relationship.

    Without both, the convergent upsert goes out unkeyed, the destination accepts a second
    object on the second apply, and SC-002 and SC-003 fail as though convergence were broken
    when the real fault is a schema the plan cannot key against (FR-024, AD017, V15). That
    reads as a product bug, so it is refused as a fixture error before a single assertion runs.

    Pure — no destination is contacted — so it is exercisable, and is exercised, offline:
    `tests/test_live_fixture_preconditions.py` imports it and runs both refusals and both
    accept paths with no marker and no destination, so the one check that separates "broken
    fixture" from "broken feature" is not itself carried only by the skipped runs it guards.
    """
    for operation in operations:
        node_schema = destination_schema.get(operation.kind)
        if node_schema is None:
            msg = (
                f"The destination schema declares no kind {operation.kind!r}, which operation "
                f"{operation.operation_id!r} plans a {operation.action} for. Missing component: the kind itself."
            )
            raise LivePlanPreconditionError(msg)
        components = _human_friendly_id(destination_schema, operation.kind)
        if not components:
            msg = (
                f"Destination kind {operation.kind!r} declares no 'human_friendly_id', so operation "
                f"{operation.operation_id!r} would be written unkeyed and every re-apply would duplicate it. "
                "Missing component: 'human_friendly_id' — the kind declares none."
            )
            raise LivePlanPreconditionError(msg)
        missing = [
            component
            for component in components
            if _identity_path_value(operation.identity, _component_path(component)) is _UNSUPPLIED
        ]
        if missing:
            msg = (
                f"Destination kind {operation.kind!r}: the plan identity of operation "
                f"{operation.operation_id!r} ({dict(operation.identity)!r}) does not supply every component of "
                f"that kind's human-friendly ID {components}. Missing component(s): {', '.join(missing)}. The "
                "convergent write would be unkeyed and every re-apply would duplicate the object."
            )
            raise LivePlanPreconditionError(msg)


# ---------------------------------------------------------------------------------------
# The seeded live plan, and reading the destination back
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class LivePlan:
    """Everything a test in this module needs about the seeded live plan."""

    environment: dict[str, str]
    config_path: Path
    sync_name: str
    run_id: str
    run_dir: Path
    plan: SavedPlan
    client: Any
    schemas: Mapping[str, Any]
    preexisting_peer_kind: str
    preexisting_peer_identity: dict[str, Any]
    preexisting_peer_filters: dict[str, Any]


def _matching_node_ids(live: LivePlan, kind: str, identity: Mapping[str, Any]) -> list[str]:
    """Every destination node id matching a plan identity, queried independently of the resolver."""
    filters = _identity_filters(live.schemas, kind, identity)
    return [node.id for node in live.client.filters(kind=kind, populate_store=False, **filters)]


def _resolve_at_most_one(live: LivePlan, kind: str, identity: Mapping[str, Any]) -> str | None:
    """The single destination node for a plan identity, `None` when absent.

    More than one match is the duplicate SC-002 exists to detect, so it fails here naming the
    kind, the identity and the real count rather than being silently reduced to the first.
    """
    node_ids = _matching_node_ids(live, kind, identity)
    assert len(node_ids) <= 1, (
        f"{len(node_ids)} objects of destination kind {kind!r} match the plan identity "
        f"{dict(identity)!r} ({node_ids}). The convergent write duplicated the object."
    )
    return node_ids[0] if node_ids else None


def _resolve_exactly_one(live: LivePlan, kind: str, identity: Mapping[str, Any]) -> str:
    """The single destination node for a plan identity; absent is a failure."""
    node_id = _resolve_at_most_one(live, kind, identity)
    assert node_id is not None, (
        f"No object of destination kind {kind!r} matches the plan identity {dict(identity)!r} at the destination."
    )
    return node_id


def _plan_kinds(live: LivePlan) -> list[str]:
    """The destination kinds the plan holds an operation for."""
    return sorted({operation.kind for operation in live.plan.operations()})


def _counts_by_kind(live: LivePlan) -> dict[str, int]:
    """Destination object counts, scoped to the kinds appearing in the plan."""
    return {kind: live.client.count(kind=kind, branch=DESTINATION_BRANCH) for kind in _plan_kinds(live)}


def _identities_by_operation(live: LivePlan, operations: Sequence[PlannedOperation]) -> dict[str, str | None]:
    """The destination node each operation's identity resolves to, or `None` when absent."""
    return {
        operation.operation_id: _resolve_at_most_one(live, operation.kind, operation.identity)
        for operation in operations
    }


def _observed_peer_ids(node: Any, reference: RelationshipReference) -> set[str]:  # noqa: ANN401 — SDK node
    """The destination's current peer ids for one relationship of one node."""
    manager = getattr(node, reference.field)
    if reference.cardinality == "one":
        return set() if manager is None or manager.id is None else {manager.id}
    return set(manager.peer_ids)


def _observed_peer_sets(live: LivePlan, operations: Sequence[PlannedOperation]) -> dict[tuple[str, str], set[str]]:
    """The destination peer set of every relationship the given operations carry.

    Keyed by `(operation id, relationship field)`; the values are destination node ids. The
    comparison against the plan is made against `_expected_peer_sets`, which resolves each of
    the plan's `(peer kind, peer identity)` pairs through its own query — so the pair set is
    compared unordered, and the kind half of each pair is evidenced by the lookup having been
    issued against that kind.
    """
    observed: dict[tuple[str, str], set[str]] = {}
    for operation in operations:
        references = list(operation.relationships or ())
        if not references:
            continue
        node_id = _resolve_exactly_one(live, operation.kind, operation.identity)
        node = live.client.get(
            kind=operation.kind,
            id=node_id,
            branch=DESTINATION_BRANCH,
            include=[reference.field for reference in references],
        )
        for reference in references:
            observed[operation.operation_id, reference.field] = _observed_peer_ids(node, reference)
    return observed


def _expected_peer_sets(live: LivePlan, operations: Sequence[PlannedOperation]) -> dict[tuple[str, str], set[str]]:
    """The peer set the plan specifies, resolved to destination node ids independently."""
    expected: dict[tuple[str, str], set[str]] = {}
    for operation in operations:
        for reference in operation.relationships or ():
            expected[operation.operation_id, reference.field] = {
                _resolve_exactly_one(live, reference.peer_kind, peer) for peer in reference.peers
            }
    return expected


# ---------------------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------------------


def _sync_instance(config_path: Path) -> SyncInstance:
    """The parsed bounded configuration; an unparseable one is a setup error, not a failure."""
    instance = get_instance(config_file=str(config_path))
    if instance is None:
        msg = f"The bounded configuration at {config_path} could not be loaded."
        raise LivePlanPreconditionError(msg)
    return instance


def _plan_run(config_path: Path) -> tuple[str, Path]:
    """Run the qualified path's `diff` in process and return `(run_id, run_dir)`.

    The same three calls `diff_cmd` makes — load both sides, diff, write the plan — with a
    forced full destination extract, because FR-015 derives deletes only from a full one and
    SC-007's live half needs the plan to carry one.
    """
    potenda = get_potenda_from_instance(sync_instance=_sync_instance(config_path), show_progress=False)
    potenda.force_full_extract = True
    if potenda.run_dir is None or potenda.run_id is None:
        msg = "get_potenda_from_instance allocated no run directory for the plan run."
        raise LivePlanPreconditionError(msg)
    potenda.load_both_sides()
    potenda.write_plan(potenda.diff())
    return potenda.run_id, potenda.run_dir


def _potenda_for_apply(live: LivePlan) -> Any:  # noqa: ANN401 — Potenda is imported lazily by the CLI too
    """A `Potenda` bound to the stored run, constructed exactly as `apply_cmd` constructs it."""
    return get_potenda_from_instance(
        sync_instance=_sync_instance(live.config_path), run_id=live.run_id, show_progress=False
    )


def _seed_the_peer_kinds(workspace: Path, environment: Mapping[str, str], suffix: str) -> None:
    """Converge every kind a physical interface references, so its peers pre-exist the plan under test.

    Runs against the adapters `_generate_adapters` has already rendered into `workspace` from the
    wider `KINDS_UNDER_TEST` slice, which is why this writes its configuration into the same
    directory and generates nothing of its own — see that function for why one render is shared.
    """
    seed_config = _write_bounded_config(
        workspace / "seed-config.yml",
        name=f"live-apply-seed-{suffix}",
        kinds=SEED_KINDS,
        environment=environment,
    )
    run_id, _ = _plan_run(seed_config)
    get_potenda_from_instance(sync_instance=_sync_instance(seed_config), run_id=run_id, show_progress=False).apply_plan(
        ownership=granted_ownership()
    )


def _unreferenced_tag(client: Any, schemas: Mapping[str, Any]) -> Any:  # noqa: ANN401 — SDK client and nodes
    """A destination tag nothing already seeded refers to, so removing it disturbs nothing else.

    Removing a referenced tag would either be refused by the destination or would silently
    unlink it from its holder, and either way the perturbation would stop being the one thing
    it is for: making the plan under test carry a create with no relationship references.
    """
    referenced: set[str] = set()
    for kind in SEED_KINDS:
        node_schema = schemas.get(kind)
        if not any(relationship.name == "tags" for relationship in getattr(node_schema, "relationships", ())):
            continue
        for holder in client.all(kind=kind, branch=DESTINATION_BRANCH, include=["tags"], populate_store=False):
            referenced.update(holder.tags.peer_ids)
    for tag in client.all(kind=TAG_KIND, branch=DESTINATION_BRANCH, populate_store=False):
        if tag.id not in referenced:
            return tag
    msg = (
        "Every destination tag is referenced by something already seeded, so none can be removed to make the "
        "plan under test carry a plain create. Point these tests at a destination seeded from the NetBox demo "
        "data."
    )
    raise LivePlanPreconditionError(msg)


def _perturb_the_destination(client: Any, schemas: Mapping[str, Any], suffix: str) -> dict[str, str]:  # noqa: ANN401
    """Make the plan under test carry a create, an update and a delete.

    Returns the identity names the fixture then asserts the plan actually holds, so a
    perturbation that failed to produce its operation is a setup error rather than a silently
    weaker plan.
    """
    removed = _unreferenced_tag(client, schemas)
    removed_name = removed.name.value
    removed.delete()

    sites = client.all(kind=SITE_KIND, branch=DESTINATION_BRANCH, limit=1, populate_store=False)
    if not sites:
        msg = "The seed run left no LocationSite at the destination, so no update can be planned."
        raise LivePlanPreconditionError(msg)
    site = sites[0]
    site.description.value = f"perturbed-for-apply-integration-{suffix}"
    site.save()

    canary_name = f"plan-apply-delete-canary-{suffix}"
    canary = client.create(
        kind=TAG_KIND,
        branch=DESTINATION_BRANCH,
        data={"name": canary_name, "description": "Absent from the source, so the plan records a delete for it."},
    )
    canary.save()
    return {
        "created_tag_name": removed_name,
        "updated_site_name": site.name.value,
        "delete_canary_name": canary_name,
    }


def _require_operation(plan: SavedPlan, *, kind: str, action: str, name: str, role: str) -> PlannedOperation:
    """The plan's `action` on `kind` whose identity name is `name`, or a setup error."""
    for operation in plan.operations(kind=kind):
        if operation.action == action and operation.identity.get("name") == name:
            return operation
    held = sorted("{} {!r}".format(op.action, op.identity.get("name")) for op in plan.operations(kind=kind))
    msg = (
        f"The plan under test holds no {action} of {kind!r} named {name!r}, which this phase needs as {role}. "
        f"It holds: {held}."
    )
    raise LivePlanPreconditionError(msg)


def _crosses_a_relationship(filters: Mapping[str, Any]) -> bool:
    """Whether any filter kwarg is PD-004's nested `<rel>__<attr>__value` form."""
    suffix = f"{_COMPONENT_SEPARATOR}{_VALUE_SUFFIX}"
    return any(_COMPONENT_SEPARATOR in name.removesuffix(suffix) for name in filters)


def _referenced_peers_absent_from_the_plan(
    plan: SavedPlan,
    operations: Sequence[PlannedOperation],
) -> list[tuple[str, dict[str, Any]]]:
    """Every `(peer kind, peer identity)` the given operations reference that the plan does not create.

    Those are the peers that must already exist at the destination for the plan to apply, so they
    are the only ones a fixture can read back — or perturb — before the apply under test runs.
    """
    planned = {(operation.kind, _identity_key(operation.identity)) for operation in plan.operations()}
    return [
        (reference.peer_kind, dict(peer))
        for operation in operations
        for reference in operation.relationships or ()
        for peer in reference.peers
        if (reference.peer_kind, _identity_key(peer)) not in planned
    ]


def _require_preexisting_peer(
    plan: SavedPlan,
    operations: Sequence[PlannedOperation],
    schemas: Mapping[str, Any],
) -> dict[str, Any]:
    """A referenced peer that pre-exists at the destination and that the plan does not create.

    SC-008's load-bearing precondition. With every peer created by the same plan, tier
    ordering fills the resolver's memo and the destination-query path — the requirement the
    criterion exists to measure — never runs, so its absence is a setup error.

    Among those peers, the one returned is a peer whose **destination human-friendly ID
    crosses a relationship** and whose plan identity supplies that crossing component. That
    is the property that makes AD043's nested `{peer_kind, identity}` walk and PD-004's
    nested `<rel>__<attr>__value` filter spelling actually run, and neither is decidable
    offline. Which kinds have such a human-friendly ID is a fact about the destination
    schema, so when none of the referenced kinds does, this is a setup error telling the
    maintainer to widen `KINDS_UNDER_TEST` rather than a failing assertion about the product.
    """
    candidates = _referenced_peers_absent_from_the_plan(plan, operations)
    if not candidates:
        msg = (
            "Every peer the plan references is also created by the plan, so dependency-tier ordering fills the "
            "resolver's memo and the destination-query path SC-008 measures is never exercised. The seed phase "
            "was supposed to leave at least one referenced peer at the destination and out of the plan."
        )
        raise LivePlanPreconditionError(msg)

    inspected: dict[str, list[str]] = {}
    for kind, identity in candidates:
        inspected[kind] = _human_friendly_id(schemas, kind)
        try:
            filters = _identity_filters(schemas, kind, identity)
        except LivePlanPreconditionError:
            # This candidate supplies no scalar component at all; another may.
            continue
        if _crosses_a_relationship(filters):
            return {"kind": kind, "identity": identity, "filters": filters}
    msg = (
        "No peer that pre-exists at the destination and is absent from the plan is queried through a "
        f"relationship-crossing filter, so PD-004's nested `<rel>__<attr>__value` spelling and AD043's nested "
        f"identity walk are not exercised. Referenced kinds and their destination human-friendly IDs: "
        f"{inspected}. Widen KINDS_UNDER_TEST to a slice whose referenced kinds include one whose "
        "human-friendly ID crosses a relationship."
    )
    raise LivePlanPreconditionError(msg)


def _identity_key(identity: Mapping[str, Any]) -> str:
    """A comparable rendering of a canonical identity, for set membership."""
    return repr(sorted(identity.items()))


@pytest.fixture(scope="module")
def live_plan(tmp_path_factory: pytest.TempPathFactory) -> Iterator[LivePlan]:
    """T074 — the shared fixture: a stored plan on the qualified path, and its preconditions.

    Module-scoped because seeding it extracts NetBox twice; every test below applies the same
    stored plan, which is convergent by design, so they do not disturb one another.

    Before yielding, the plan is checked against `assert_convergence_key_is_supplied` and
    against the shape every test depends on. Both raise `LivePlanPreconditionError`, so a
    destination whose schema cannot key the plan is reported as a fixture error naming the
    kind and the missing component — never as a test failure (FR-024, AD017, V15).
    """
    environment = _env_or_skip()
    workspace = tmp_path_factory.mktemp("saved-plan-apply")
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(workspace / "cache"))
    try:
        suffix = uuid.uuid4().hex[:8]
        client = _destination_client(environment)
        schemas = client.schema.all(branch=DESTINATION_BRANCH)

        sync_name = f"live-apply-{suffix}"
        config_path = _write_bounded_config(
            workspace / "config.yml",
            name=sync_name,
            kinds=KINDS_UNDER_TEST,
            environment=environment,
        )
        # Before anything constructs a `Potenda` over this workspace, including the seed run
        # below: without the generated adapters every load in this module fails on its first
        # kind (AD090).
        _generate_adapters(config_path, schemas)

        _seed_the_peer_kinds(workspace, environment, suffix)
        perturbations = _perturb_the_destination(client, schemas, suffix)

        run_id, run_dir = _plan_run(config_path)
        plan = read_saved_plan(
            sync_name=sync_name,
            run_id=run_id,
            config=_sync_instance(config_path),
        )

        assert_convergence_key_is_supplied(operations=plan.operations(), destination_schema=schemas)
        _require_operation(
            plan, kind=TAG_KIND, action="create", name=perturbations["created_tag_name"], role="SC-003's create class"
        )
        _require_operation(
            plan, kind=SITE_KIND, action="update", name=perturbations["updated_site_name"], role="SC-003's update class"
        )
        _require_operation(
            plan, kind=TAG_KIND, action="delete", name=perturbations["delete_canary_name"], role="SC-007's live half"
        )
        relationship_operations = [
            operation for operation in plan.operations(kind=RELATIONSHIP_KIND) if operation.relationships
        ]
        if not relationship_operations:
            msg = (
                f"The plan under test holds no relationship-bearing operation on {RELATIONSHIP_KIND!r}, so SC-008 and "
                f"SC-003's third write class would measure nothing. The slice is bounded to the interfaces of "
                f"{BOUNDING_DEVICE!r} (ADDED_FILTERS); if the qualified configuration or the source data no longer "
                "carries that device, re-derive the bounding against them."
            )
            raise LivePlanPreconditionError(msg)
        peer = _require_preexisting_peer(plan, relationship_operations, schemas)

        yield LivePlan(
            environment=environment,
            config_path=config_path,
            sync_name=sync_name,
            run_id=run_id,
            run_dir=run_dir,
            plan=plan,
            client=client,
            schemas=schemas,
            preexisting_peer_kind=peer["kind"],
            preexisting_peer_identity=peer["identity"],
            preexisting_peer_filters=peer["filters"],
        )
    finally:
        monkeypatch.undo()


# ---------------------------------------------------------------------------------------
# Apply-path instrumentation
# ---------------------------------------------------------------------------------------


@contextmanager
def _extraction_forbidden() -> Iterator[list[str]]:
    """Fail if the apply path extracts either side or runs the comparison engine (FR-012).

    Both halves are recorded **and** raised: raising is what stops a stray call being written
    into the destination behind the assertion, and the record is what makes the failure legible
    wherever the raise is caught and re-raised as something else on the way out.
    """
    calls: list[str] = []

    def forbid(name: str) -> Any:  # noqa: ANN401 — patched onto arbitrary signatures
        def call(*_args: Any, **_kwargs: Any) -> Any:  # noqa: ANN401
            calls.append(name)
            msg = (
                f"{name} was called while applying a saved plan. A saved-plan apply re-runs no extraction and "
                "no comparison (FR-012, SC-001)."
            )
            raise ExtractionCalledError(msg)

        return call

    with (
        patch.object(Adapter, "diff_from", forbid("Adapter.diff_from")),
        patch.object(Adapter, "sync_from", forbid("Adapter.sync_from")),
        patch.object(DiffSyncMixin, "load", forbid("DiffSyncMixin.load")),
    ):
        yield calls


@contextmanager
def _destination_queries_recorded(live: LivePlan) -> Iterator[list[dict[str, Any]]]:
    """Record every peer query the resolver issues, and let it through."""
    recorded: list[dict[str, Any]] = []
    client_type = type(live.client)
    real = client_type.filters

    def spy(self: Any, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        recorded.append(dict(kwargs))
        return real(self, *args, **kwargs)

    with patch.object(client_type, "filters", spy):
        yield recorded


@contextmanager
def _crash_at(operation_id: str, *, before_the_write: bool) -> Iterator[None]:
    """SC-003's two crash windows, injected inside the apply loop.

    `before_the_write=True` raises before the destination write is issued;
    `before_the_write=False` raises after it has committed and before the loop advances,
    which is the window a partial apply leaves the destination in.
    """
    real = InfrahubAdapter.apply_planned_operation

    def wrapper(self: InfrahubAdapter, *, operation: PlannedOperation, peers: Any) -> str:  # noqa: ANN401
        if operation.operation_id != operation_id:
            return real(self, operation=operation, peers=peers)
        if before_the_write:
            msg = f"Injected crash before the destination write of {operation_id!r}."
            raise InjectedCrashError(msg)
        real(self, operation=operation, peers=peers)
        msg = f"Injected crash after the destination write of {operation_id!r} committed."
        raise InjectedCrashError(msg)

    with patch.object(InfrahubAdapter, "apply_planned_operation", wrapper):
        yield


def _all_operation_ids(live: LivePlan) -> set[str]:
    """Every operation identifier the plan contains."""
    return {operation.operation_id for operation in live.plan.operations()}


# ---------------------------------------------------------------------------------------
# T075 — SC-001: applying a stored plan re-runs no extraction
# ---------------------------------------------------------------------------------------


def test_applying_a_stored_plan_runs_no_extraction(live_plan: LivePlan) -> None:
    """SC-001: the apply completes, and neither side is extracted nor compared (FR-012).

    `diff_from` and `sync_from` are the comparison engine's two entry points and
    `DiffSyncMixin.load` is what every adapter in this repository extracts through, so
    patching all three to fail covers "no source or destination extraction ran" rather than
    only the fork-wide rewrite the criterion also rules out.
    """
    with _extraction_forbidden() as calls:
        record = _potenda_for_apply(live_plan).apply_plan(ownership=granted_ownership())

    assert calls == [], f"The apply path called {calls}, so it re-ran extraction or comparison."
    assert record.applied_operations, "The apply completed without applying a single operation."
    assert set(record.applied_operations) | set(record.skipped_delete_operations) == _all_operation_ids(live_plan)


# ---------------------------------------------------------------------------------------
# T076 — SC-002: re-applying an identical plan converges
# ---------------------------------------------------------------------------------------


def test_re_applying_an_identical_plan_converges(live_plan: LivePlan) -> None:
    """SC-002: the same object, the same identity, no duplicate, for every kind in the plan.

    This criterion **measures** convergence rather than asserting it holds (AD080): for a
    destination kind whose convergence key crosses a relationship the render is unkeyed
    today, and a duplicate here is the recorded AD066/AD067 limitation that
    `tests/plan/test_apply_conformance.py` carries as a strict expected failure. A failure on
    such a kind is this test doing its job. The assertion is not weakened for it.
    """
    writes = [operation for operation in live_plan.plan.operations() if operation.action != "delete"]

    _potenda_for_apply(live_plan).apply_plan(ownership=granted_ownership())
    first_counts = _counts_by_kind(live_plan)
    first_identities = _identities_by_operation(live_plan, writes)

    _potenda_for_apply(live_plan).apply_plan(ownership=granted_ownership())
    second_counts = _counts_by_kind(live_plan)
    second_identities = _identities_by_operation(live_plan, writes)

    assert second_counts == first_counts, (
        f"Per-kind destination counts changed across a second apply of the identical plan: "
        f"{first_counts} then {second_counts}."
    )
    assert second_identities == first_identities, (
        "A second apply of the identical plan moved at least one operation's identity to a different "
        "destination object, so the write is not convergent."
    )
    unresolved = sorted(op_id for op_id, node_id in second_identities.items() if node_id is None)
    assert not unresolved, f"These operations left no object at the destination after applying twice: {unresolved}."


# ---------------------------------------------------------------------------------------
# T077 — SC-003: the per-class conformance matrix
# ---------------------------------------------------------------------------------------

WRITE_CLASSES = ("create", "update", "relationship")


def _members(live: LivePlan, write_class: str) -> list[PlannedOperation]:
    """Every operation of one write class.

    The three classes are not disjoint by construction — SC-003 names "the create and update
    write classes, and the class of operations whose payload carries relationship references"
    — so a create that carries references belongs to two of them and is measured under both.
    """
    if write_class == "relationship":
        return [operation for operation in live.plan.operations() if operation.relationships]
    return [operation for operation in live.plan.operations() if operation.action == write_class]


def _representative(live: LivePlan, write_class: str) -> PlannedOperation:
    """One operation of the requested write class, to inject a crash into.

    Prefers a member that belongs to *only* this class, so the crash windows exercise the
    class rather than an operation that happens to sit in two of them.
    """
    members = _members(live, write_class)
    assert members, f"The plan under test holds no operation of write class {write_class!r}."
    exclusive = [operation for operation in members if bool(operation.relationships) == (write_class == "relationship")]
    return (exclusive or members)[0]


def _observation(live: LivePlan, write_class: str) -> Any:  # noqa: ANN401 — one of two comparable shapes
    """The destination state this write class is measured by.

    Counts and identities for create and update; the peer-set comparison for the relationship
    class, because an object created with its peers unlinked leaves the counts correct and the
    relationships wrong (SC-003, measured through SC-008).
    """
    members = _members(live, write_class)
    if write_class == "relationship":
        return _observed_peer_sets(live, members)
    counts = {kind: live.client.count(kind=kind, branch=DESTINATION_BRANCH) for kind in {op.kind for op in members}}
    return {"counts": dict(sorted(counts.items()))} | _identities_by_operation(live, members)


@pytest.mark.parametrize("write_class", WRITE_CLASSES)
def test_the_write_class_conformance_matrix(live_plan: LivePlan, write_class: str) -> None:
    """SC-003: create, update and the relationship-bearing class across four scenarios.

    Apply-once fixes the clean-single-run state; apply-twice, a crash injected **after** the
    destination write commits and before the loop advances, and one injected **before** the
    write is issued each have to leave the destination back at that same state once the plan
    is applied again. Delete is excluded because applying deletes is out of scope.

    Same AD080 caveat as SC-002: for a relationship-crossing convergence key the relationship
    class is exactly the population the narrowed keyedness guarantee excludes, so a failure
    there is the recorded limitation surfacing rather than a regression in this code.
    """
    operation = _representative(live_plan, write_class)

    _potenda_for_apply(live_plan).apply_plan(ownership=granted_ownership())
    clean = _observation(live_plan, write_class)

    _potenda_for_apply(live_plan).apply_plan(ownership=granted_ownership())
    assert _observation(live_plan, write_class) == clean, f"apply-twice moved the {write_class} class off its counts."

    for before_the_write in (False, True):
        window = "before" if before_the_write else "after"
        with (
            # The injected crash escapes as **itself**: it is outside the engine's operational
            # boundary, which is what keeps a defect from being reported as a destination
            # refusal. What this case measures is unaffected — the destination's
            # state after re-applying, in either crash window.
            pytest.raises(InjectedCrashError),
            _crash_at(operation.operation_id, before_the_write=before_the_write),
        ):
            _potenda_for_apply(live_plan).apply_plan(ownership=granted_ownership())
        _potenda_for_apply(live_plan).apply_plan(ownership=granted_ownership())
        assert _observation(live_plan, write_class) == clean, (
            f"A crash injected {window} the destination write of {operation.operation_id!r} left the "
            f"{write_class} class off its clean-single-run state after re-applying."
        )


# ---------------------------------------------------------------------------------------
# T079 — SC-008: the destination's peer sets match the plan's references
# ---------------------------------------------------------------------------------------


def test_relationship_peer_sets_match_the_plan(live_plan: LivePlan) -> None:
    """SC-008: peer sets compared as unordered `(peer kind, peer identity)` pairs (AD043).

    Two things are asserted that a peer-set comparison alone would not reach:

    - the **destination-query path ran** for a peer the plan does not create. With every peer
      created by the same plan, dependency-tier ordering fills the resolver's memo and the
      query path never runs, so the comparison would pass while apply-time peer resolution is
      broken. The fixture guarantees such a peer exists; here the query it forces is asserted
      to have been issued.
    - that query's **filter spelling**. The fixture picks a pre-existing peer whose
      destination human-friendly ID crosses a relationship and whose plan identity supplies
      that crossing component as a nested `{peer_kind, identity}` pair, so resolving it walks
      AD043's nesting and asks the destination in PD-004's nested `<rel>__<attr>__value`
      form — a claim no offline harness can settle.
    """
    operations = [
        operation for operation in live_plan.plan.operations(kind=RELATIONSHIP_KIND) if operation.relationships
    ]
    assert operations, f"The plan holds no relationship-bearing {RELATIONSHIP_KIND} operation."

    with _extraction_forbidden() as calls, _destination_queries_recorded(live_plan) as queries:
        _potenda_for_apply(live_plan).apply_plan(ownership=granted_ownership())
    assert calls == [], f"The apply path called {calls}, so the no-comparison-store precondition does not hold."

    expected_filters = live_plan.preexisting_peer_filters
    matching = [
        query
        for query in queries
        if query.get("kind") == live_plan.preexisting_peer_kind
        and all(query.get(name) == value for name, value in expected_filters.items())
    ]
    assert matching, (
        f"No destination query resolved the pre-existing peer {live_plan.preexisting_peer_kind!r} "
        f"{live_plan.preexisting_peer_identity!r} with {expected_filters}. The resolver answered it from its "
        f"memo instead, so apply-time peer resolution was never exercised. Queries issued: {queries}"
    )
    assert _crosses_a_relationship(expected_filters), (
        f"The pre-existing peer {live_plan.preexisting_peer_kind!r} is filtered by {sorted(expected_filters)}, none "
        "of which crosses a relationship, so PD-004's nested filter spelling is not exercised."
    )

    observed = _observed_peer_sets(live_plan, operations)
    expected = _expected_peer_sets(live_plan, operations)
    assert observed == expected, (
        "The destination's peer sets do not match the plan's reference lists as unordered sets of "
        f"(peer kind, peer identity) pairs. Observed {observed}, expected {expected}."
    )


# ---------------------------------------------------------------------------------------
# T080 — SC-016's live half: a genuinely ambiguous peer refuses the operation
# ---------------------------------------------------------------------------------------


def _clone_node(live: LivePlan, kind: str, node_id: str) -> Any:  # noqa: ANN401 — SDK node
    """A second destination object carrying the same attribute values as an existing one.

    Cloning rather than constructing: the clone inherits whatever mandatory attributes and
    cardinality-one relationships the kind declares, so this works for any peer kind the plan
    happens to reference, and it is guaranteed to match the very filter the resolver builds.
    """
    node_schema = live.schemas[kind]
    original = live.client.get(kind=kind, id=node_id, branch=DESTINATION_BRANCH)
    data: dict[str, Any] = {}
    for attribute in node_schema.attributes:
        if attribute.read_only:
            continue
        value = getattr(original, attribute.name).value
        if value is not None:
            data[attribute.name] = value
    for relationship in node_schema.relationships:
        if relationship.cardinality != "one" or relationship.optional:
            continue
        related = getattr(original, relationship.name)
        if related is not None and related.id is not None:
            data[relationship.name] = related.id
    clone = live.client.create(kind=kind, branch=DESTINATION_BRANCH, data=data)
    try:
        clone.save()
    # Any destination refusal here means the seed is impossible, not that a test failed, so
    # the whole class of them is converted into the precondition error.
    except Exception as exc:
        msg = (
            f"The destination refused a second {kind!r} carrying the same identity as {node_id!r}, so no "
            f"genuinely ambiguous peer can be seeded for it: {exc}. SC-016's live half needs a referenced peer "
            "kind whose uniqueness constraints do not cover the components the resolver filters on."
        )
        raise LivePlanPreconditionError(msg) from exc
    return clone


@dataclass(frozen=True)
class AmbiguousPeer:
    """The seeded ambiguity: which peer it is, and how many destination objects really match it."""

    kind: str
    identity: dict[str, Any]
    filters: dict[str, Any]
    match_count: int


def _ambiguous_peer_or_skip(live: LivePlan) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """A referenced peer whose ambiguity this destination schema admits, or skip saying why (AD092).

    SC-016's live half needs two destination objects that both match the query the resolver
    issues for one peer identity. Whether that state is reachable at all is a property of the
    **destination schema**: a kind declaring a uniqueness constraint every component of which
    those filters pin cannot hold a second matching object, and the destination answers the
    attempt with `Violates uniqueness constraint`, HTTP 422.

    So every peer the plan references and does not create is checked against the schema — the
    module fixture's chosen pre-existing peer first, so that where an ambiguity is admissible
    for it the test measures exactly the peer T079 also resolves — and the first candidate
    whose constraints leave a filtered component free is returned. When none does, that is
    neither a missing environment nor a product defect but a verifiable, reproducible fact
    about this schema, so it skips with the constraint that establishes it in the message.
    The same skip pattern as `_env_or_skip`: a checked precondition, reported as a skip naming
    what could not be established, rather than the setup error `LivePlanPreconditionError`
    raises for a fixture that *should* have been satisfiable.
    """
    chosen = (live.preexisting_peer_kind, dict(live.preexisting_peer_identity))
    referencing = [operation for operation in live.plan.operations() if operation.relationships]
    candidates = [
        chosen,
        *(
            candidate
            for candidate in _referenced_peers_absent_from_the_plan(live.plan, referencing)
            if (candidate[0], _identity_key(candidate[1])) != (chosen[0], _identity_key(chosen[1]))
        ),
    ]
    refused: dict[str, str] = {}
    for kind, identity in candidates:
        try:
            filters = _identity_filters(live.schemas, kind, identity)
        except LivePlanPreconditionError:
            # Nothing about this candidate is queryable, so it can be neither resolved nor made
            # ambiguous. Another candidate may be.
            continue
        covering = covering_uniqueness_constraint(destination_schema=live.schemas, kind=kind, filters=filters)
        if covering is None:
            return kind, identity, filters
        refused[kind] = f"filtered on {sorted(filters)}, covered by uniqueness constraint {covering}"
    pytest.skip(
        "This destination's schema admits no genuinely ambiguous peer, so SC-016's live half cannot be seeded: "
        "every peer kind the plan references declares a uniqueness constraint every component of which the "
        "filters the resolver queries it with pin, so the destination refuses a second matching object with "
        "`Violates uniqueness constraint` (HTTP 422). Candidate peer kinds, their resolver filters and the "
        f"constraint covering each: {'; '.join(f'{kind} — {reason}' for kind, reason in sorted(refused.items()))}. "
        "Every assertion in the test is unchanged and runs as soon as a referenced kind's uniqueness "
        "constraints leave one of those filtered components free."
    )


@pytest.fixture
def ambiguous_peer(live_plan: LivePlan) -> Iterator[AmbiguousPeer]:
    """Seed a genuinely ambiguous peer and yield it with the **real** number of matches.

    Function-scoped and torn down, because the ambiguity breaks every other test in this
    module by construction: it is a destination state, not a fixture of the plan.

    Which peer is seeded is decided by `_ambiguous_peer_or_skip` against the destination
    schema, so a schema that admits no ambiguity for any referenced peer skips here with that
    reason rather than erroring on a clone the destination was always going to refuse (AD092).
    """
    kind, identity, filters = _ambiguous_peer_or_skip(live_plan)
    original_id = _resolve_exactly_one(live_plan, kind, identity)
    clone = _clone_node(live_plan, kind, original_id)
    try:
        matches = _matching_node_ids(live_plan, kind, identity)
        if len(matches) < 2:
            msg = (
                f"Seeding a second {kind!r} left {len(matches)} object(s) matching the peer identity "
                f"{identity!r}, so the peer is not genuinely ambiguous and SC-016's live half would measure "
                "a refusal that never happens."
            )
            raise LivePlanPreconditionError(msg)
        yield AmbiguousPeer(kind=kind, identity=identity, filters=filters, match_count=len(matches))
    finally:
        clone.delete()


def test_an_ambiguous_peer_refuses_the_operation(live_plan: LivePlan, ambiguous_peer: AmbiguousPeer) -> None:
    """SC-016 live half: the refusal names the peer kind, the peer identity and the real count.

    "Refused rather than skipped" is asserted from the partial record the failure carries: the
    referring operation appears in neither the applied set nor the skipped-delete set, so the
    apply stopped at it instead of stepping over it.

    The peer under test is the one the `ambiguous_peer` fixture actually seeded, which is any
    referenced peer this destination's schema admits an ambiguity for (AD092) — not necessarily
    the pre-existing crossing peer T079 uses.
    """
    kind, identity = ambiguous_peer.kind, ambiguous_peer.identity
    referrers = [
        operation
        for operation in live_plan.plan.operations()
        for reference in operation.relationships or ()
        if reference.peer_kind == kind
        and any(_identity_key(peer) == _identity_key(identity) for peer in reference.peers)
    ]
    assert referrers, f"No operation in the plan references the seeded ambiguous peer {kind!r} {identity!r}."
    referring = referrers[0]

    with pytest.raises(OperationApplyFailedError) as caught:
        _potenda_for_apply(live_plan).apply_plan(ownership=granted_ownership())

    cause = caught.value.__cause__
    assert isinstance(cause, PeerAmbiguousError), f"The apply failed with {cause!r}, not a multi-match refusal."
    message = str(cause)
    assert kind in message, f"The refusal does not name the peer kind {kind!r}: {message}"
    assert str(ambiguous_peer.match_count) in message, (
        f"The refusal does not name the real match count {ambiguous_peer.match_count}: {message}"
    )
    for value in ambiguous_peer.filters.values():
        assert str(value) in message, f"The refusal does not name the peer identity value {value!r}: {message}"

    record = caught.value.apply_record
    assert referring.operation_id not in record.applied_operations
    assert referring.operation_id not in record.skipped_delete_operations, (
        "The operation whose peer is ambiguous was recorded as skipped. It must be refused, which stops the "
        "apply, rather than stepped over."
    )
