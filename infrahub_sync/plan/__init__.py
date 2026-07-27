"""Saved plan artifact: the on-disk plan a sync run writes and an apply run reads back.

Holds the artifact's canonical form, its checksums, the reader and its pre-apply
verification, and the review surface that renders a saved plan without touching a
source or a destination.

`__all__` is the package's **supported** surface and it is deliberately narrow: FR-029
requires that "reading a stored plan MUST have exactly one supported entry point", so
`read_saved_plan` is the only reading function named here and the rest of `__all__` is
record types. Everything else under `infrahub_sync/plan/` — the writer, the low-level
`load_plan_artifact`, the pre-apply verifier, the canonicalizer, the checksums — is
internal to this outcome and imported from its own module by the engine and the CLI, so a
second reading path cannot be reached through the package namespace by accident (AD029).
"""

from infrahub_sync.plan.models import (
    PlanManifest,
    PlannedOperation,
    PlanSummary,
    RelationshipReference,
    SourceSnapshotRecord,
    VerificationFailure,
)
from infrahub_sync.plan.review import SavedPlan, read_saved_plan

__all__ = [
    "PlanManifest",
    "PlanSummary",
    "PlannedOperation",
    "RelationshipReference",
    "SavedPlan",
    "SourceSnapshotRecord",
    "VerificationFailure",
    "read_saved_plan",
]
