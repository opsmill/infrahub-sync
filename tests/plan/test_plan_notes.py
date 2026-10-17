"""The plan says which checks and deletes it could not make (review #3 and #8).

- A record the load could not build and whose identity is not known may be any Infrahub node of
  its kind, so the plan computes no delete of that kind. The summary counts those records per
  kind (`deletes_withheld_by_kind`), so a reviewer reads why a kind has no delete.
- A run without a destination schema snapshot, such as a direct run, cannot check Dropdown
  values. The manifest records `dropdown_values_checked: false` and the summary carries it, so
  the plan output says the check was not made.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from infrahub_sync.plan.reader import load_plan_artifact
from infrahub_sync.plan.review import SavedPlan
from infrahub_sync.plan.writer import MANIFEST_FILE_NAME, PLAN_DIR_NAME
from tests.plan.problem_fixtures import Variant
from tests.plan.problem_plan import PlannedFixture, plan_fixture

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from infrahub_sync.plan.models import PlanSummary


def _summary(planned: PlannedFixture) -> PlanSummary:
    loaded = load_plan_artifact(planned.run_dir)
    return SavedPlan(
        manifest=loaded.manifest,
        operations=loaded.operations,
        checksum_ok=True,
        verification_notes=[],
        report=loaded.report,
    ).summary()


def _manifest(planned: PlannedFixture) -> dict[str, object]:
    return json.loads((planned.run_dir / PLAN_DIR_NAME / MANIFEST_FILE_NAME).read_bytes())


def test_the_summary_counts_the_records_without_identity_that_withhold_deletes(tmp_path: Path) -> None:
    """Records not built before their identity was known: no delete of their kind is planned.

    The VRF `q-in-q` failed its transform. The interfaces `mgmt0` and `eth9` were not built,
    because their required device was filtered out or is not in the source, and the device is
    part of their identity.
    """
    summary = _summary(plan_fixture(tmp_path / "run"))

    assert summary.deletes_withheld_by_kind == {"InterfacePhysical": 2, "IpamVRF": 1}


def test_a_plan_without_such_records_withholds_no_delete(tmp_path: Path) -> None:
    assert _summary(plan_fixture(tmp_path / "run", Variant.NO_PROBLEMS)).deletes_withheld_by_kind == {}


def test_a_run_with_a_schema_snapshot_checks_dropdown_values(tmp_path: Path) -> None:
    planned = plan_fixture(tmp_path / "run")

    assert "dropdown_values_checked" not in _manifest(planned)
    assert _summary(planned).dropdown_values_checked is True


def test_a_run_without_a_schema_snapshot_says_the_dropdown_check_was_not_made(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="infrahub_sync.plan.problems"):
        planned = plan_fixture(tmp_path / "run", schema_snapshot=False)

    assert _manifest(planned)["dropdown_values_checked"] is False
    assert _summary(planned).dropdown_values_checked is False
    assert not [item for item in planned.report.problems if item.problem_type == "dropdown_value_not_in_choices"]
    assert "Dropdown values were not checked: no destination schema snapshot in this run" in caplog.text
