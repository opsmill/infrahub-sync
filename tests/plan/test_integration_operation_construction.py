"""`[S9]` Every operation the live modules build must be constructible without a destination.

Twice now an integration-only fixture has been committed carrying a `PlannedOperation` the
record type refuses, and twice it was found by a live run rather than by the suite: once when
an update's payload contradicted its own identity, once when an update carried no
`destination_id` after the format-3 rule reached in-process construction. Both were free to
catch — record validation touches no network — and both cost a host leg to find.

The integration modules cannot catch them themselves. They are `pytest.mark.integration` and
skip without `INFRAHUB_ADDRESS`, so their fixtures are never executed in an ordinary run and a
builder can be wrong for as long as nobody books the host. This module closes that gap from
the outside: it imports their **builders** and constructs every operation shape they build,
with no destination, no branch and no network.

What it does and does not claim. It proves each record is *constructible* — identity coherent
with payload, format rules satisfied, references well-formed. It proves nothing about what the
destination does with them; that is the live leg's job and stays there. The integration
modules keep their skip property untouched: nothing here imports a fixture or runs a test from
them, only module-level builder functions.

A builder that gains a required argument will fail here as a `TypeError`, which is the
intended behaviour — this file is meant to be edited whenever those signatures change.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

import tests.integration.test_infrahub_keyed_write_integration as keyed_write
import tests.integration.test_infrahub_replace_set_shrink_integration as replace_set

if TYPE_CHECKING:
    from collections.abc import Callable

    from infrahub_sync.plan.models import PlannedOperation

# A destination id shaped like the ones Infrahub issues. Never sent anywhere: these records are
# constructed and inspected, never applied.
DESTINATION_ID = "18d52a8a-7e7d-9bf5-3967-c51149d169da"


def keyed_write_builders() -> list[tuple[str, Callable[[], PlannedOperation]]]:
    """Every operation shape `test_infrahub_keyed_write_integration` builds."""
    return [
        ("create, HFID crossing a relationship", lambda: keyed_write._device_operation("device-a", "site-a")),
        (
            "update of that kind, keyed by its recorded id",
            lambda: keyed_write._device_update("device-a", "site-a", destination_id=DESTINATION_ID, serial="sn-second"),
        ),
        (
            "update renaming the destination's own human-friendly ID",
            lambda: keyed_write._renamable_update("serial-a", destination_id=DESTINATION_ID, renamed="after"),
        ),
        (
            "update omitting an HFID component, keyed only by its recorded id",
            lambda: keyed_write._device_partial_update("device-a", destination_id=DESTINATION_ID, serial="sn-x"),
        ),
        (
            "create referencing a peer whose own key crosses a relationship",
            lambda: keyed_write._mount_operation("mount-a", "device-a", "site-a"),
        ),
    ]


def replace_set_builders() -> list[tuple[str, Callable[[], PlannedOperation]]]:
    """Every operation shape `test_infrahub_replace_set_shrink_integration` builds."""
    return [
        ("create with three peers", lambda: replace_set._team_operation("team-a", ["a", "b", "c"], action="create")),
        (
            "update shrinking the peer set",
            lambda: replace_set._team_operation("team-a", ["a"], action="update", destination_id=DESTINATION_ID),
        ),
        (
            "update emptying the peer set",
            lambda: replace_set._team_operation("team-a", [], action="update", destination_id=DESTINATION_ID),
        ),
    ]


ALL_BUILDERS = [
    pytest.param(builder, id=f"keyed-write: {description}") for description, builder in keyed_write_builders()
] + [pytest.param(builder, id=f"replace-set: {description}") for description, builder in replace_set_builders()]


@pytest.mark.parametrize("builder", ALL_BUILDERS)
def test_every_operation_a_live_module_builds_is_constructible(builder: Callable[[], PlannedOperation]) -> None:
    """The record type accepts it, with no destination and no network involved."""
    operation = builder()

    assert operation.operation_id, "A constructed operation carries its derived identifier."


@pytest.mark.parametrize("builder", ALL_BUILDERS)
def test_every_operation_a_live_module_builds_records_an_id_exactly_when_it_is_an_update(
    builder: Callable[[], PlannedOperation],
) -> None:
    """Plan format 3's rule, checked on the records the live legs actually apply.

    Asserted as an equivalence rather than a one-way check: an update without a recorded id
    cannot be keyed, and a create carrying one names a destination object that does not exist
    yet. The record type refuses both, so this fails loudly if either arm is ever relaxed.
    """
    operation = builder()

    assert (operation.destination_id is not None) == (operation.action == "update"), (
        f"{operation.action} operation on {operation.kind} records destination_id="
        f"{operation.destination_id!r}, which plan format 3 does not admit for that action."
    )


def test_the_live_modules_are_not_executed_by_importing_their_builders() -> None:
    """Importing a builder must not cost a destination, or this file would need one too.

    Both modules mark themselves `integration` at module level, which is what makes them skip
    without a destination. That marker has to survive: a module that lost it would run its
    live tests in the ordinary suite and fail there for want of a server.
    """
    for module in (keyed_write, replace_set):
        markers = getattr(module, "pytestmark", None)
        names = {
            marker.name
            for marker in ([markers] if markers is not None and not isinstance(markers, list) else markers or [])
        }
        assert "integration" in names, (
            f"{module.__name__} no longer marks itself integration, so its live tests would run in the ordinary suite."
        )
