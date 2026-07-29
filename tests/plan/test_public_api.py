"""FR-029: reading a stored plan has exactly one supported entry point (T023, AD029).

The obligation is negative — no *second* reading surface may be exported — so these tests
enumerate `__all__` and fail when another read path appears in it, rather than only checking
that `read_saved_plan` is present. The package-namespace test is the one that catches the
likely regression: a later phase adding `from infrahub_sync.plan.reader import
load_plan_artifact` to `__init__.py` for convenience.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil

import pytest
from pydantic import BaseModel

import infrahub_sync.plan as plan_package
from infrahub_sync.plan.models import (
    PlanManifest,
    PlannedOperation,
    PlanSummary,
    RelationshipReference,
    SourceSnapshotRecord,
    VerificationFailure,
)
from infrahub_sync.plan.review import SavedPlan, read_saved_plan

# The whole supported surface. FR-029's "exactly one" is asserted by equality rather than by
# membership, so an addition to `__all__` fails here and has to be justified.
EXPECTED_ALL = [
    "PlanManifest",
    "PlanSummary",
    "PlannedOperation",
    "RelationshipReference",
    "SavedPlan",
    "SourceSnapshotRecord",
    "VerificationFailure",
    "read_saved_plan",
]

# A callable whose name begins with one of these reads a stored plan, so exporting a second
# one from the package would give FR-029's "exactly one entry point" a second entry point.
READING_PREFIXES = ("read_", "load_")


def _plan_submodules() -> list[str]:
    """Every module under `infrahub_sync.plan`, so the scan cannot miss a new one."""
    return [info.name for info in pkgutil.iter_modules(plan_package.__path__, f"{plan_package.__name__}.")]


def test_all_is_exactly_the_supported_surface() -> None:
    assert sorted(plan_package.__all__) == sorted(EXPECTED_ALL)


def test_all_names_exactly_one_reading_surface() -> None:
    reading = [name for name in plan_package.__all__ if name.startswith(READING_PREFIXES)]
    assert reading == ["read_saved_plan"], (
        f"FR-029 fixes exactly one supported entry point for reading a stored plan; `__all__` names {reading}."
    )


def test_read_saved_plan_is_the_only_exported_function() -> None:
    functions = [name for name in plan_package.__all__ if inspect.isfunction(getattr(plan_package, name))]
    assert functions == ["read_saved_plan"]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("read_saved_plan", read_saved_plan),
        ("SavedPlan", SavedPlan),
        ("PlanManifest", PlanManifest),
        ("PlannedOperation", PlannedOperation),
        ("PlanSummary", PlanSummary),
        ("RelationshipReference", RelationshipReference),
        ("SourceSnapshotRecord", SourceSnapshotRecord),
        ("VerificationFailure", VerificationFailure),
    ],
)
def test_every_exported_name_resolves_to_its_definition(name: str, expected: object) -> None:
    assert getattr(plan_package, name) is expected


def test_every_export_other_than_the_entry_point_is_a_record_type() -> None:
    """`__all__` is one reading function plus record types, and nothing else."""
    non_records = [
        name
        for name in plan_package.__all__
        if name != "read_saved_plan" and not issubclass(getattr(plan_package, name), (BaseModel, SavedPlan))
    ]
    assert non_records == []


def test_no_other_reading_surface_is_reachable_on_the_package() -> None:
    """No reading callable defined anywhere under `infrahub_sync/plan/` is re-exported.

    `load_plan_artifact` is the concrete case: it is the reader every path goes through, so
    it is the one a later phase is most likely to hoist into `__init__.py` for convenience.
    """
    offenders: list[str] = []
    for module_name in _plan_submodules():
        module = importlib.import_module(module_name)
        for name, value in vars(module).items():
            if name.startswith("_") or name == "read_saved_plan" or not name.startswith(READING_PREFIXES):
                continue
            if not callable(value):
                continue
            if name in plan_package.__all__ or getattr(plan_package, name, None) is value:
                offenders.append(f"{module_name}.{name}")
    assert offenders == [], f"a second reading surface is exported from infrahub_sync.plan: {offenders}"


def test_the_scan_would_catch_the_low_level_reader() -> None:
    """Guards the negative test above: prove the scan actually sees `load_plan_artifact`.

    Without this, `test_no_other_reading_surface_is_reachable_on_the_package` would pass
    just as happily if the scan matched nothing at all.
    """
    reader = importlib.import_module("infrahub_sync.plan.reader")
    candidates = [name for name in vars(reader) if name.startswith(READING_PREFIXES) and not name.startswith("_")]
    assert "load_plan_artifact" in candidates
    assert not hasattr(plan_package, "load_plan_artifact")
