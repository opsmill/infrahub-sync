"""FR-024 after the keying redesign: creates are refused, updates are still only warned about.

A create is keyed by nothing but the values it carries, so planning has to prove the key
before the write exists. G1 M6 is the case that forces it: a payload omitting one HFID
component does not match the existing object — the server reports `ok: true` and creates a
second one. Nothing downstream can detect that, so the proof has to happen here.

An update is keyed by its recorded destination `id`, so none of these conditions can make it
write the wrong object. Every arm therefore stays a warning for updates, which is what makes
kinds with no HFID usable at all.

Two independent arms guard a create on a kind **with** an HFID `[F5-R1]`:

- **identity coverage** — every HFID component's mapping field is present in the operation's
  `identity`. The clean-host `CleanDevice` case is the reproduction: `site` is resolvable
  from the payload yet absent from identity, and it must refuse.
- **value presence** — every covered component actually has a usable value, including one
  that crosses a relationship and must be proven from the peer's identity.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from infrahub_sync.plan.derive import warn_missing_convergence_key
from infrahub_sync.plan.identity import canonical_identity, operation_id
from infrahub_sync.plan.models import PlannedOperation, RelationshipReference
from tests.adapters.test_infrahub_keyed_write import ALL_SCHEMAS, CONSTRAINED_KIND
from tests.adapters.test_infrahub_planned_write import DEVICE_KIND, KEYLESS_KIND, SITE_KIND

if TYPE_CHECKING:
    from collections.abc import Mapping

DESTINATION_ID = "18d52a8a-7e7d-9bf5-3967-c51149d169da"


def destination() -> SimpleNamespace:
    """A destination exposing only what FR-024 reads: the cached schema (AD052)."""
    return SimpleNamespace(schema=dict(ALL_SCHEMAS))


def planned(
    *,
    kind: str,
    identity: Mapping[str, Any],
    action: str = "create",
    payload: Mapping[str, Any] | None = None,
    relationships: list[RelationshipReference] | None = None,
) -> PlannedOperation:
    """One planned operation, built the way derivation builds it.

    An update carries a recorded destination id, because plan format 3 records one for every
    update; a create carries none, because there is no destination object to name yet.
    """
    canonical = canonical_identity(dict(identity), kind=kind)
    effective_id = DESTINATION_ID if action == "update" else None
    return PlannedOperation(
        operation_id=operation_id(action, kind, canonical),
        action=action,  # ty: ignore[invalid-argument-type]
        kind=kind,
        identity=canonical,
        tier=0,
        payload=dict(payload) if payload is not None else dict(canonical),
        relationships=relationships,
        destination_id=effective_id,
    )


def check(*operations: PlannedOperation) -> None:
    """Run FR-024 over `operations` against the fixture destination."""
    warn_missing_convergence_key(destination=destination(), operations=list(operations))


def site_create(name: str, description: str) -> PlannedOperation:
    """A `TestSite` create whose identity is finer than the kind's HFID (`[name__value]`)."""
    return planned(
        kind=SITE_KIND,
        identity={"name": name, "description": description},
        payload={"name": name, "description": description},
    )


# ---------------------------------------------------------------------------------------
# Arm (i) — identity coverage
# ---------------------------------------------------------------------------------------


def test_a_create_whose_identity_omits_an_hfid_component_is_refused() -> None:
    """`[F5-R1]` the `CleanDevice` reproduction: `site` is in the payload but not in identity."""
    from infrahub_sync.plan.errors import UnkeyedCreateRefusedError

    operation = planned(
        kind=DEVICE_KIND,
        identity={"name": "device-a"},
        payload={"name": "device-a"},
        relationships=[
            RelationshipReference(field="site", peer_kind=SITE_KIND, cardinality="one", peers=[{"name": "site-a"}])
        ],
    )

    with pytest.raises(UnkeyedCreateRefusedError) as excinfo:
        check(operation)

    assert DEVICE_KIND in str(excinfo.value)
    assert "site" in str(excinfo.value)


def test_a_create_whose_identity_covers_the_hfid_is_allowed() -> None:
    """The positive arm: every component is named by identity and carries a value."""
    operation = planned(
        kind=DEVICE_KIND,
        identity={"name": "device-a", "site": {"peer_kind": SITE_KIND, "identity": {"name": "site-a"}}},
        payload={"name": "device-a"},
        relationships=[
            RelationshipReference(field="site", peer_kind=SITE_KIND, cardinality="one", peers=[{"name": "site-a"}])
        ],
    )

    check(operation)


def test_an_update_whose_identity_omits_an_hfid_component_is_only_warned_about() -> None:
    """An update is keyed by its recorded id, so the HFID cannot misdirect it."""
    operation = planned(
        kind=DEVICE_KIND,
        identity={"name": "device-a"},
        payload={"name": "device-a"},
        action="update",
    )

    check(operation)


# ---------------------------------------------------------------------------------------
# Arm (ii) — value presence
# ---------------------------------------------------------------------------------------


def test_a_create_whose_relationship_component_has_no_value_is_refused() -> None:
    """A component that crosses a relationship is proven from the peer's identity values."""
    from infrahub_sync.plan.errors import UnkeyedCreateRefusedError

    operation = planned(
        kind=DEVICE_KIND,
        identity={"name": "device-a", "site": {"peer_kind": SITE_KIND, "identity": {"code": "site-a"}}},
        payload={"name": "device-a"},
        relationships=[
            RelationshipReference(field="site", peer_kind=SITE_KIND, cardinality="one", peers=[{"code": "site-a"}])
        ],
    )

    with pytest.raises(UnkeyedCreateRefusedError):
        check(operation)


def test_a_create_whose_identity_component_is_empty_is_refused() -> None:
    """A present-but-empty component keys nothing at the destination."""
    from infrahub_sync.plan.errors import UnkeyedCreateRefusedError

    with pytest.raises(UnkeyedCreateRefusedError):
        check(planned(kind=SITE_KIND, identity={"name": ""}, payload={"name": ""}))


# ---------------------------------------------------------------------------------------
# Kinds with no human-friendly ID
# ---------------------------------------------------------------------------------------


def test_a_create_on_a_no_hfid_kind_with_a_covered_uniqueness_constraint_is_allowed() -> None:
    """G1 M8: the destination refuses the duplicate, so the create is safe to attempt."""
    check(planned(kind=CONSTRAINED_KIND, identity={"name": "c-a", "scope": "s-a"}))


def test_a_create_on_a_no_hfid_kind_without_a_covered_constraint_is_refused() -> None:
    """G1 M9: a kind with neither an HFID nor a constraint duplicates on every write."""
    from infrahub_sync.plan.errors import UnkeyedCreateRefusedError

    with pytest.raises(UnkeyedCreateRefusedError):
        check(planned(kind=KEYLESS_KIND, identity={"name": "keyless-a"}))


def test_an_update_on_a_no_hfid_kind_is_allowed() -> None:
    """The recorded id unlocks no-HFID kinds for updates, which is the point of the design."""
    check(planned(kind=KEYLESS_KIND, identity={"name": "keyless-a"}, action="update"))


def test_an_update_on_a_no_hfid_kind_without_a_covered_constraint_is_only_warned_about() -> None:
    """The uniqueness-constraint arm stays a warning for updates."""
    check(planned(kind=CONSTRAINED_KIND, identity={"name": "c-a"}, action="update"))


# ---------------------------------------------------------------------------------------
# Collision — several creates projecting onto one destination HFID
# ---------------------------------------------------------------------------------------


def test_two_creates_projecting_onto_one_destination_hfid_are_refused() -> None:
    """The `LocationRack` case: distinct source objects silently become one destination object."""
    from infrahub_sync.plan.errors import DestinationIdentityCollisionError

    with pytest.raises(DestinationIdentityCollisionError) as excinfo:
        check(site_create("site-a", "first"), site_create("site-a", "second"))

    assert SITE_KIND in str(excinfo.value)


def test_a_create_and_an_update_sharing_a_projection_are_not_a_collision() -> None:
    """Mixed actions: the update is keyed by id, so the two cannot converge onto one object."""
    update = planned(
        kind=SITE_KIND,
        identity={"name": "site-a", "description": "second"},
        payload={"name": "site-a", "description": "second"},
        action="update",
    )

    check(site_create("site-a", "first"), update)


def test_two_updates_sharing_a_projection_are_not_a_collision() -> None:
    """Updates are excluded from the count entirely."""
    first = planned(
        kind=SITE_KIND,
        identity={"name": "site-a", "description": "first"},
        payload={"name": "site-a", "description": "first"},
        action="update",
    )
    second = planned(
        kind=SITE_KIND,
        identity={"name": "site-a", "description": "second"},
        payload={"name": "site-a", "description": "second"},
        action="update",
    )

    check(first, second)


def test_a_single_finer_than_hfid_create_stays_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    """One create cannot collide with itself, so the finer-identity case only warns."""
    with caplog.at_level("WARNING"):
        check(site_create("site-a", "first"))

    assert caplog.records, "The identity-finer-than-the-destination-key warning must still be emitted."
