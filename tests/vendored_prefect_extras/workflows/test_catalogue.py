"""Unit tests for ``WorkflowCatalogue``.

Composition and ordering; lookup; membership, immutability, and the deliberate
non-``Mapping`` shape; import isolation proved against the sentinel module that
explodes on import; the ``inventory-refresh``/``scheduled`` split-identity round
trip; and
construction-time refusal of a duplicate deployment identity. Everything runs
offline against the fixture modules in this package.

Note for test authors: ``KeyError`` applies ``repr`` to its argument, so
``str(excinfo.value)`` is the *quoted* message. Substring assertions on
it therefore work only because the implementation keeps the lookup-miss
message on a single line -- a newline would come back escaped as ``\\n``.
"""

from __future__ import annotations

import copy
import pickle
import sys
from collections.abc import Callable, Iterable, Iterator, Mapping

import pytest

from opsmill_prefect_extras.workflows.catalogue import WorkflowCatalogue
from opsmill_prefect_extras.workflows.definitions import WorkflowDefinition

FLOWS_MODULE = "tests.workflows.flows"
SENTINEL_MODULE = "tests.workflows.sentinel"


def _definition(
    flow_name: str,
    deployment_name: str | None = None,
    *,
    module: str = FLOWS_MODULE,
    function: str = "my_sync_flow",
    tags: Iterable[str] = (),
) -> WorkflowDefinition:
    """Build a definition from this file's fixture defaults."""
    return WorkflowDefinition(
        flow_name=flow_name,
        deployment_name=deployment_name,
        module=module,
        function=function,
        tags=tags,
    )


# ---------------------------------------------------------------------------
# Composition and ordering
# ---------------------------------------------------------------------------


def test_composition_accepts_bare_definitions_and_iterable_groups() -> None:
    alone = _definition("alone")
    grouped = (_definition("grouped-first"), _definition("grouped-second"))

    catalogue = WorkflowCatalogue(alone, grouped)

    assert len(catalogue) == 3
    assert catalogue["alone/alone"] is alone
    assert catalogue["grouped-first/grouped-first"] is grouped[0]


def test_iteration_yields_definitions_in_composition_order() -> None:
    """Bare definitions and groups are interleaved on purpose: both halves of
    the ordering rule are exercised in one composition.
    """
    first = _definition("first")
    group = (_definition("group-a"), _definition("group-b"))
    middle = _definition("middle")
    tail_group = (_definition("tail-a"), _definition("tail-b"))

    catalogue = WorkflowCatalogue(first, group, middle, tail_group)

    assert list(catalogue) == [first, *group, middle, *tail_group]


def test_iteration_yields_definitions_rather_than_keys() -> None:
    catalogue = WorkflowCatalogue(_definition("infrahub-sync", "run"))

    items = list(catalogue)

    assert items == [catalogue["infrahub-sync/run"]]
    for item in items:
        assert isinstance(item, WorkflowDefinition)


def test_composition_order_is_not_sorted_order() -> None:
    reversed_group = (_definition("zulu"), _definition("mike"), _definition("alfa"))

    catalogue = WorkflowCatalogue(reversed_group)

    assert catalogue.keys() == ("zulu/zulu", "mike/mike", "alfa/alfa")


def test_a_group_may_be_any_iterable_including_a_generator() -> None:
    definitions = (_definition("generated-first"), _definition("generated-second"))

    catalogue = WorkflowCatalogue(definition for definition in definitions)

    assert list(catalogue) == list(definitions)


def test_keys_returns_a_tuple_in_composition_order() -> None:
    catalogue = WorkflowCatalogue(
        _definition("infrahub-sync", "run"),
        (_definition("reports", "nightly"), _definition("reports", "weekly")),
    )

    keys = catalogue.keys()

    assert keys == ("infrahub-sync/run", "reports/nightly", "reports/weekly")
    assert isinstance(keys, tuple)


def test_len_counts_every_composed_definition() -> None:
    catalogue = WorkflowCatalogue(
        _definition("one"),
        (_definition("two"), _definition("three")),
        _definition("four"),
    )

    assert len(catalogue) == 4


def test_an_empty_catalogue_constructs_and_is_valid() -> None:
    catalogue = WorkflowCatalogue()

    assert len(catalogue) == 0
    assert catalogue.keys() == ()
    assert list(catalogue) == []
    assert "anything/at-all" not in catalogue


def test_an_empty_group_contributes_nothing() -> None:
    catalogue = WorkflowCatalogue((), _definition("only"), [])

    assert catalogue.keys() == ("only/only",)


# ---------------------------------------------------------------------------
# Lookup by key
# ---------------------------------------------------------------------------


def test_lookup_by_key_returns_the_definition() -> None:
    definition = _definition("infrahub-sync", "run")
    catalogue = WorkflowCatalogue(definition)

    assert catalogue[definition.key] is definition
    assert catalogue["infrahub-sync/run"] is definition


def test_lookup_of_a_missing_key_raises_key_error_naming_the_key() -> None:
    catalogue = WorkflowCatalogue(_definition("infrahub-sync", "run"))

    with pytest.raises(KeyError) as excinfo:
        catalogue["no-such-flow/no-such-deployment"]

    assert "no-such-flow/no-such-deployment" in str(excinfo.value)


def test_lookup_miss_states_the_key_convention_and_lists_known_keys() -> None:
    """``KeyError`` reprs its argument, so the assertions read the quoted message."""
    catalogue = WorkflowCatalogue(
        _definition("infrahub-sync", "run"),
        _definition("reports", "nightly"),
    )

    with pytest.raises(KeyError) as excinfo:
        catalogue["infrahub-sync"]

    message = str(excinfo.value)
    assert "infrahub-sync" in message
    assert "flow_name/deployment_name" in message
    assert "infrahub-sync/run" in message
    assert "reports/nightly" in message


def test_lookup_miss_caps_the_known_keys_listing_and_reports_the_total() -> None:
    """The count is asserted with its own wording rather than as a bare ``"12"``,
    which any key happening to contain those digits would satisfy; the
    summarizing suffix is asserted too -- it is the whole reason the listing is
    allowed to be incomplete.
    """
    definitions = tuple(_definition(f"flow-{index:02d}", "run") for index in range(12))
    catalogue = WorkflowCatalogue(definitions)

    with pytest.raises(KeyError) as excinfo:
        catalogue["flow-00"]

    message = str(excinfo.value)
    listed = [definition.key for definition in definitions if definition.key in message]
    assert listed == [definition.key for definition in definitions[:10]]
    assert "flow-10/run" not in message
    assert "flow-11/run" not in message
    assert "12 known key(s)" in message
    assert "(+2 more)" in message


def test_lookup_miss_at_the_cap_lists_every_key_without_summarizing() -> None:
    definitions = tuple(_definition(f"flow-{index:02d}", "run") for index in range(10))
    catalogue = WorkflowCatalogue(definitions)

    with pytest.raises(KeyError) as excinfo:
        catalogue["flow-00"]

    message = str(excinfo.value)
    for definition in definitions:
        assert definition.key in message
    assert "10 known key(s)" in message
    assert "more)" not in message


def test_lookup_on_an_empty_catalogue_raises_key_error() -> None:
    """An empty catalogue has no keys to list, and "0 known key(s): " trailing
    off into nothing reads like a truncated message -- so the emptiness is
    stated.
    """
    catalogue = WorkflowCatalogue()

    with pytest.raises(KeyError) as excinfo:
        catalogue["infrahub-sync/run"]

    message = str(excinfo.value)
    assert "infrahub-sync/run" in message
    assert "0 known key(s)" in message
    assert "<none -- this catalogue is empty>" in message


# ---------------------------------------------------------------------------
# Membership, immutability, and the non-Mapping shape
# ---------------------------------------------------------------------------


def test_a_string_tests_key_membership() -> None:
    catalogue = WorkflowCatalogue(_definition("infrahub-sync", "run"))

    assert "infrahub-sync/run" in catalogue
    assert "infrahub-sync" not in catalogue
    assert "reports/nightly" not in catalogue


@pytest.mark.parametrize(
    "item",
    [
        pytest.param(None, id="none"),
        pytest.param(b"infrahub-sync/run", id="bytes-key"),
    ],
)
def test_other_object_types_are_simply_not_contained(item: object) -> None:
    catalogue = WorkflowCatalogue(_definition("infrahub-sync", "run"))

    assert item not in catalogue


@pytest.mark.parametrize("name", ["__setitem__", "update", "register"])
def test_no_mutator_methods_exist(name: str) -> None:
    catalogue = WorkflowCatalogue(_definition("infrahub-sync", "run"))

    assert not hasattr(catalogue, name)


def test_the_catalogue_is_deliberately_not_a_mapping() -> None:
    catalogue = WorkflowCatalogue(_definition("infrahub-sync", "run"))

    assert not isinstance(catalogue, Mapping)
    assert isinstance(iter(catalogue), Iterator)


def test_a_catalogue_survives_copy_and_pickle_round_trips() -> None:
    """Consumer structures holding a catalogue can be deep-copied and pickled."""
    definitions = (
        _definition("infrahub-sync", "run", tags=("sync",)),
        _definition("reports", "nightly"),
    )
    catalogue = WorkflowCatalogue(definitions)

    for clone in (
        copy.deepcopy(catalogue),
        pickle.loads(pickle.dumps(catalogue)),
    ):
        assert clone.keys() == catalogue.keys()
        assert list(clone) == list(definitions)
        assert clone["infrahub-sync/run"] == definitions[0]


# ---------------------------------------------------------------------------
# Import isolation
# ---------------------------------------------------------------------------
#
# Every test below pairs a ``sys.modules`` assertion with the sentinel itself:
# the sentinel raises at import time, so a *passing* test is already proof the
# operation never resolved the reference. A failed import leaves nothing behind
# in ``sys.modules``, so the
# absence assertion holds even after the explicit-load test has tripped it.


def _sentinel_definition() -> WorkflowDefinition:
    """A definition pointing at the module that explodes on import."""
    return _definition(
        "explodes-on-import",
        "run",
        module=SENTINEL_MODULE,
        function="anything",
        tags=("sync",),
    )


def test_construction_does_not_import_the_workflow_module() -> None:
    assert SENTINEL_MODULE not in sys.modules

    catalogue = WorkflowCatalogue(
        _sentinel_definition(),
        (_definition("also-explodes", module=SENTINEL_MODULE, function="anything"),),
    )

    assert len(catalogue) == 2
    assert SENTINEL_MODULE not in sys.modules


def test_reads_do_not_import_the_workflow_module() -> None:
    """Lookup, iteration, keys, membership, length, a lookup miss, and
    rendering the found definition all leave the reference unresolved.
    """
    assert SENTINEL_MODULE not in sys.modules
    definition = _sentinel_definition()
    catalogue = WorkflowCatalogue(definition)

    found = catalogue["explodes-on-import/run"]

    assert found is definition
    assert [item.module for item in catalogue] == [SENTINEL_MODULE]
    assert catalogue.keys() == ("explodes-on-import/run",)
    assert "explodes-on-import/run" in catalogue
    assert len(catalogue) == 1
    with pytest.raises(KeyError):
        catalogue["explodes-on-import"]
    assert found.to_deployment_input() == {"name": "run", "tags": ["sync"]}
    assert SENTINEL_MODULE not in sys.modules


def test_only_an_explicit_load_imports_the_workflow_module() -> None:
    """The other half of the proof: the explicit path *does* resolve.

    Matching the sentinel's own message is what stops this passing vacuously
    on a ``ModuleNotFoundError`` from a broken fixture path, and ``ImportError``
    is ruled out explicitly because ``load()`` propagates the target module's
    own exception unwrapped rather than re-raising it as an import error.
    """
    assert SENTINEL_MODULE not in sys.modules
    catalogue = WorkflowCatalogue(_sentinel_definition())

    with pytest.raises(RuntimeError) as excinfo:
        catalogue["explodes-on-import/run"].load()

    assert "sentinel module must never be imported by the catalogue" in str(
        excinfo.value
    )
    assert not isinstance(excinfo.value, ImportError)
    assert SENTINEL_MODULE not in sys.modules


# ---------------------------------------------------------------------------
# Split-identity round trip
# ---------------------------------------------------------------------------


def test_inventory_refresh_round_trips_through_lookup_and_rendering() -> None:
    definition = WorkflowDefinition(
        flow_name="inventory-refresh",
        deployment_name="scheduled",
        module=FLOWS_MODULE,
        function="my_sync_flow",
        tags=("inventory",),
        cron="0 2 * * *",
    )
    catalogue = WorkflowCatalogue((definition,))

    found = catalogue["inventory-refresh/scheduled"]
    payload = found.to_deployment_input()

    assert found is definition
    assert found.flow_name == "inventory-refresh"
    assert found.deployment_name == "scheduled"
    assert found.key == "inventory-refresh/scheduled"
    assert catalogue.keys() == ("inventory-refresh/scheduled",)
    assert payload["name"] == "scheduled"
    assert "inventory-refresh" not in payload.values()
    assert definition.flow_name == "inventory-refresh"


# ---------------------------------------------------------------------------
# Duplicate deployment identity refused
# ---------------------------------------------------------------------------
#
# ``DuplicateWorkflowError`` is imported inside each test function rather than at
# module level: a module-level import of a name that has gone missing fails
# collection of the whole file, which masks which individual tests fail.


def _compose_individually(
    first: WorkflowDefinition, second: WorkflowDefinition
) -> WorkflowCatalogue:
    """Supply both definitions as separate positional arguments."""
    return WorkflowCatalogue(first, second)


def _compose_within_one_group(
    first: WorkflowDefinition, second: WorkflowDefinition
) -> WorkflowCatalogue:
    """Supply both definitions inside a single definition group."""
    return WorkflowCatalogue((first, second))


def _compose_across_groups(
    first: WorkflowDefinition, second: WorkflowDefinition
) -> WorkflowCatalogue:
    """Supply each definition in a group of its own."""
    return WorkflowCatalogue((first,), (second,))


@pytest.mark.parametrize(
    "compose",
    [
        pytest.param(_compose_individually, id="supplied-individually"),
        pytest.param(_compose_within_one_group, id="within-one-group"),
        pytest.param(_compose_across_groups, id="across-groups"),
    ],
)
def test_a_duplicate_identity_is_refused_however_it_is_composed(
    compose: Callable[[WorkflowDefinition, WorkflowDefinition], WorkflowCatalogue],
) -> None:
    from opsmill_prefect_extras.workflows.catalogue import DuplicateWorkflowError

    first = _definition("infrahub-sync", "run")
    second = _definition("infrahub-sync", "run", function="my_async_flow")

    with pytest.raises(DuplicateWorkflowError) as excinfo:
        compose(first, second)

    assert excinfo.value.key == "infrahub-sync/run"
    assert "infrahub-sync/run" in str(excinfo.value)


def test_the_duplicate_error_is_a_value_error_naming_the_collision() -> None:
    """The same definition supplied twice is a duplicate too: composition never
    silently de-duplicates.
    """
    from opsmill_prefect_extras.workflows.catalogue import DuplicateWorkflowError

    definition = _definition("infrahub-sync", "run")

    # Caught as a plain ValueError on purpose: untyped callers must still be
    # able to catch this conventionally.
    with pytest.raises(ValueError) as excinfo:
        WorkflowCatalogue(definition, definition)

    error = excinfo.value
    assert isinstance(error, DuplicateWorkflowError)
    assert issubclass(DuplicateWorkflowError, ValueError)
    assert error.key == "infrahub-sync/run"
    message = str(error)
    assert "infrahub-sync/run" in message
    assert "flow_name/deployment_name" in message


def test_a_defaulted_deployment_name_collides_with_the_explicit_one() -> None:
    """Detection reads the resolved identity, not what was typed."""
    from opsmill_prefect_extras.workflows.catalogue import DuplicateWorkflowError

    defaulted = _definition("x")
    explicit = _definition("x", "x")

    with pytest.raises(DuplicateWorkflowError) as excinfo:
        WorkflowCatalogue(defaulted, explicit)
    assert excinfo.value.key == "x/x"


def test_definitions_differing_in_either_identity_half_compose_freely() -> None:
    catalogue = WorkflowCatalogue(
        _definition("reports", "nightly"),
        _definition("reports", "weekly"),
        _definition("infrahub-sync", "run"),
        _definition("ipam-sync", "run"),
    )

    assert catalogue.keys() == (
        "reports/nightly",
        "reports/weekly",
        "infrahub-sync/run",
        "ipam-sync/run",
    )


def test_refusing_a_duplicate_does_not_import_the_workflow_module() -> None:
    from opsmill_prefect_extras.workflows.catalogue import DuplicateWorkflowError

    assert SENTINEL_MODULE not in sys.modules

    with pytest.raises(DuplicateWorkflowError) as excinfo:
        WorkflowCatalogue(_sentinel_definition(), (_sentinel_definition(),))

    assert excinfo.value.key == "explodes-on-import/run"
    assert SENTINEL_MODULE not in sys.modules
