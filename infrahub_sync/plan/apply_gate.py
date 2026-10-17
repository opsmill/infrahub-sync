"""The plan checks an apply makes after it parses the plan and before its first write.

Two rules of spec 009 (research R11, ADR 0022):

- Only a format 4 plan is applied. A format 2 plan records no destination id on its
  updates, so its updates cannot be keyed; a format 3 plan records no report, so it does
  not say which records it leaves out. Both are read and reviewed, and refused here with
  the instruction to plan again.
- A plan that leaves out records (`manifest.report.left_out` greater than zero) is applied
  only when the apply confirms it. The confirmation is yes or no: the plan checksum already
  binds the apply to the reviewed plan. When the plan leaves out nothing, the confirmation is
  accepted and ignored.

`Potenda.apply_plan` calls `require_applyable_plan` before the first ownership proof, so a
refusal writes nothing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from infrahub_sync.plan.errors import LeftOutConfirmationError, PlanFormatApplyUnsupportedError
from infrahub_sync.plan.models import DESTINATION_ID_FORMAT_VERSION, REPORT_FORMAT_VERSION

if TYPE_CHECKING:
    from infrahub_sync.plan.models import PlanManifest


def require_applyable_plan(manifest: PlanManifest, *, run_id: str, confirm_left_out: bool) -> None:
    """Refuse a plan saved before format 4, then an unconfirmed plan that leaves out records.

    Raises:
        PlanFormatApplyUnsupportedError: the plan is format 2 or 3.
        LeftOutConfirmationError: the plan leaves out records and the apply did not confirm them.
    """
    _require_report_format(manifest, run_id=run_id)
    left_out = 0 if manifest.report is None else manifest.report.left_out
    if left_out == 0 or confirm_left_out:
        return
    msg = (
        f"The plan of run {run_id!r} leaves out {left_out} records, and the apply did not confirm them. "
        f"Nothing was written to the destination."
    )
    raise LeftOutConfirmationError(msg, expected_left_out_count=left_out)


def _require_report_format(manifest: PlanManifest, *, run_id: str) -> None:
    version = manifest.format_version
    if version >= REPORT_FORMAT_VERSION:
        return
    if version < DESTINATION_ID_FORMAT_VERSION:
        reason = "its updates carry no recorded destination id, so they cannot be keyed"
    else:
        reason = "it records no plan report, so it does not say which records it leaves out"
    msg = (
        f"The plan artifact of run {run_id!r} declares format version {version}, which this version of "
        f"infrahub-sync can read and review but cannot apply: {reason}. Plan again before applying. "
        f"Nothing was written to the destination."
    )
    raise PlanFormatApplyUnsupportedError(msg)
