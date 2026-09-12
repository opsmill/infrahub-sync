"""Declarative workflow definitions and catalogue.

Public import surface for the ``workflows`` feature: describe Prefect
deployments as immutable data, compose them explicitly into a catalogue, and
validate the whole catalogue offline -- no network, no Prefect server -- in one
call. Catalogue construction, lookup, iteration and rendering import no
workflow implementation module; the references are resolved only by explicit
``load()`` and validation calls, which is how validation detects a rename.

Example:
    The 60-second tour, for a consuming application called ``example_app``. Each
    block below is a complete module. They are parsed straight out of this
    docstring and executed verbatim by ``tests/workflows/test_example.py``, so
    an example that stopped working would fail the suite rather than rot.

    The flows are ordinary Prefect flows, written as they always are -- they
    know nothing about any catalogue::

        # example_app/inventory/flows.py
        from prefect import flow


        @flow(name="inventory-refresh")
        def refresh_inventory() -> str:
            return "refreshed"

    Beside them, the domain declares its workflows as data. This module
    imports nothing from ``example_app.inventory.flows``: it only names it::

        # example_app/inventory/workflows.py
        from opsmill_prefect_extras.workflows import WorkflowDefinition

        INVENTORY_REFRESH = WorkflowDefinition(
            flow_name="inventory-refresh",   # the flow's own name, and ...
            deployment_name="scheduled",     # ... a distinct deployment name
            module="example_app.inventory.flows",  # resolved on demand
            function="refresh_inventory",
            tags=("inventory",),
            cron="0 2 * * *",
            concurrency_limit=1,
            collision_strategy="CANCEL_NEW",
        )

        # A "definition group" is any iterable of definitions -- there is no
        # group class to subclass, no decorator, and no import-time
        # registration: the tuple below *is* the group.
        INVENTORY_WORKFLOWS = (INVENTORY_REFRESH,)

    A second domain package has exactly the same shape::

        # example_app/reports/flows.py
        from prefect import flow


        @flow(name="reports")
        def nightly_report() -> str:
            return "reported"

    ::

        # example_app/reports/workflows.py
        from opsmill_prefect_extras.workflows import WorkflowDefinition

        REPORT_WORKFLOWS = (
            WorkflowDefinition(
                flow_name="reports",
                deployment_name="nightly",
                module="example_app.reports.flows",
                function="nightly_report",
                tags=("reports",),
            ),
        )

    The application composes the groups once, at its own composition root.
    Composition, lookup and rendering import no flow module at all -- an
    author adding a workflow edits only their own package::

        # example_app/composition.py
        from example_app.inventory.workflows import INVENTORY_WORKFLOWS
        from example_app.reports.workflows import REPORT_WORKFLOWS
        from opsmill_prefect_extras.workflows import WorkflowCatalogue

        CATALOGUE = WorkflowCatalogue(INVENTORY_WORKFLOWS, REPORT_WORKFLOWS)

        definition = CATALOGUE["inventory-refresh/scheduled"]
        payload = definition.to_deployment_input()
        # {
        #     "name": "scheduled",
        #     "tags": ["inventory"],
        #     "schedules": [{"schedule": {"cron": "0 2 * * *"}}],
        #     "concurrency_limit": 1,
        #     "concurrency_options": {"collision_strategy": "CANCEL_NEW"},
        # }

    ``payload`` splats into Prefect's ``create_deployment`` alongside the
    server-assigned ``flow_id``, which the payload deliberately omits. Settings
    the definition does not carry -- ``entrypoint`` here -- are absent keys,
    never defaults invented by this library.

    One import wires the shipped check into the application's own test suite.
    It resolves every definition in the catalogue and fails naming every
    broken one at once, so a renamed flow function is one CI failure rather
    than a hunt::

        # tests/test_workflow_catalogue.py
        from example_app.composition import CATALOGUE
        from opsmill_prefect_extras.workflows import assert_valid_definitions


        def test_workflow_catalogue_resolves() -> None:
            assert_valid_definitions(CATALOGUE)

    Omitting ``deployment_name`` defaults it to ``flow_name``, giving the key
    ``name/name``; every other optional setting simply stays out of the
    rendering.
"""

from typing import TYPE_CHECKING

from opsmill_prefect_extras.workflows.definitions import WorkflowDefinition

if TYPE_CHECKING:
    from opsmill_prefect_extras.workflows.catalogue import (
        DuplicateWorkflowError,
        WorkflowCatalogue,
    )
    from opsmill_prefect_extras.workflows.validation import (
        DefinitionFailure,
        ValidationReport,
        assert_valid_definitions,
        validate_definitions,
    )

__all__: list[str] = [
    "DefinitionFailure",
    "DuplicateWorkflowError",
    "ValidationReport",
    "WorkflowCatalogue",
    "WorkflowDefinition",
    "assert_valid_definitions",
    "validate_definitions",
]


def __getattr__(name: str) -> object:
    """Load a workflow helper only when its public name is requested.

    Importing the definitions submodule is a common-core operation used by
    independent features.  Deferring the catalogue and validation facades
    keeps that import from initializing those sibling modules.

    Args:
        name: The requested module attribute.

    Returns:
        The requested public workflow helper.

    Raises:
        AttributeError: If ``name`` is not part of this module's public API.
    """
    if name in {"DuplicateWorkflowError", "WorkflowCatalogue"}:
        from opsmill_prefect_extras.workflows.catalogue import (
            DuplicateWorkflowError,
            WorkflowCatalogue,
        )

        return {
            "DuplicateWorkflowError": DuplicateWorkflowError,
            "WorkflowCatalogue": WorkflowCatalogue,
        }[name]
    if name in {
        "DefinitionFailure",
        "ValidationReport",
        "assert_valid_definitions",
        "validate_definitions",
    }:
        from opsmill_prefect_extras.workflows.validation import (
            DefinitionFailure,
            ValidationReport,
            assert_valid_definitions,
            validate_definitions,
        )

        return {
            "DefinitionFailure": DefinitionFailure,
            "ValidationReport": ValidationReport,
            "assert_valid_definitions": assert_valid_definitions,
            "validate_definitions": validate_definitions,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """Expose lazy public names to interactive and documentation tooling."""
    return sorted(set(globals()) | set(__all__))
