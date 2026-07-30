"""The plan-artifact error taxonomy.

Every failure on a plan-artifact path is one of the classes below, and each tells the
operator what to do next: `PlanArtifactError` declares `next_action` on the base class and
constructing a subclass without a non-empty one raises `TypeError` (AD059). The taxonomy
table in `dev/specs/archive/001-plan-artifact-saved-apply/contracts/plan-reader-api.md`
fixes the wording. Callers may override it with the `next_action` keyword, which is how
`SourcePeerUnresolvedError` routes its two conditions to two remedies (AD082).

`SkippedDeleteOperation` is the one class here that is **not** a failure: it is the control
signal a destination write surface raises if it is ever handed a recorded delete. It sits
outside `PlanArtifactError` so no caller reads it as an error or asks it for a remedy
(AD055).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from infrahub_sync.plan.models import ApplyRecord


class SkippedDeleteOperation(Exception):  # noqa: N818 — a control signal, not an error
    """A recorded delete a write surface declines to execute (FR-016, FR-017, AD055).

    The **defensive** half of the contract: applying deletes is out of scope for this
    release, so a write surface handed a `delete` must raise this rather than touch the
    destination. Nothing catches it in product code and the apply loop never raises it —
    `Potenda.apply_plan` recognizes a delete itself, records the identifier and continues.

    The skip accounting is the **apply loop's**, not this signal's: a caller that dispatches
    a delete straight to a write surface gets this raise and nothing more — no identifier
    recorded, no run completed. It is a designed limitation rather than a failure, so it sits
    outside `PlanArtifactError` and carries no `next_action`.
    """


class PlanArtifactError(Exception):
    """Base class for every plan-artifact failure.

    Subclasses declare `next_action` in their own body. The message a subclass is
    constructed with names the cause; the next action is appended to it, so a
    caller that prints the exception shows the remedy too.
    """

    # Subclasses MUST override this with a non-empty, class-level declaration.
    next_action: str = ""

    def __init__(self, message: str, *, next_action: str | None = None) -> None:
        effective = type(self).next_action if next_action is None else next_action
        if not effective:
            msg = (
                f"{type(self).__name__} declares no next_action. Every member of the plan-artifact "
                "taxonomy must declare a non-empty class-level `next_action` (AD059), or be "
                "constructed with the `next_action` keyword."
            )
            raise TypeError(msg)
        self.message = message
        self.next_action = effective
        super().__init__(f"{message} Next action: {effective}")


class PlanFormatV1Error(PlanArtifactError):
    """The run holds no `plan/` directory at all.

    Usually because the run predates this format, but the same verdict covers a `plan/` that
    was never written or has since been removed — a run directory archived without it, most
    often. The remedy is the same for all three, which is why they are one class.
    """

    next_action = "Re-run `diff` for this sync to rebuild the plan artifact in the current format."


class PlanArtifactTornError(PlanArtifactError):
    """The artifact is present but incomplete, inconsistent, or unreadable as a record set."""

    next_action = "Re-run `diff` for this sync to rebuild the plan artifact; the partial one cannot be repaired."


class PlanFormatVersionError(PlanArtifactError):
    """The manifest declares a `format_version` this version of the tool does not support."""

    next_action = (
        "Re-run `diff` for this sync to rebuild the plan artifact with this version of "
        "infrahub-sync, or apply it with the version that wrote it."
    )


class PlanArtifactUnreadableError(PlanArtifactError):
    """An artifact path could not be turned into the bytes or rows it should hold.

    Two conditions, neither of which is the path being *absent* — that is a separate verdict
    with a separate remedy (AD036):

    - a permission or I/O failure stopped the path from being read at all;
    - the path was read, but its bytes are not the Parquet table they claim to be.

    The class-level next action fits the first; the second overrides it at the raise site.
    """

    next_action = "Check permissions and ownership on the named path, then retry."


class UnknownRunIdentifierError(PlanArtifactError):
    """No run with the requested identifier is stored for this synchronization (FR-008, AD073).

    Two arms, because the remedies differ. When runs **do** exist, the remedy is one of the
    identifiers the message lists. When the cache root is absent or holds no runs, "pick from
    the list" is a dead end, so that arm names the command that produces a plan instead —
    use `no_runs()` for it.
    """

    next_action = "Re-run naming one of the run identifiers listed above."

    NO_RUNS_NEXT_ACTION = "Run `infrahub-sync diff --name {sync_name}` for this sync to produce a plan first."

    @classmethod
    def no_runs(cls, message: str, *, sync_name: str) -> UnknownRunIdentifierError:
        """Build the arm for a sync whose cache root is absent or holds no run directories."""
        return cls(message, next_action=cls.NO_RUNS_NEXT_ACTION.format(sync_name=sync_name))


class PlanGenerationExistsError(PlanArtifactError):
    """The run already holds a committed plan generation, which is never overwritten.

    A run id whose `plan/manifest.json` exists names a plan a human may already have
    reviewed and approved, and the checksum of a rewritten generation proves the integrity
    of the *new* files rather than identity with the ones that were approved. So re-planning
    into an occupied run id is refused and re-planning means a fresh run id.

    The condition is `manifest.json`'s presence and nothing else, because the manifest is
    the artifact's commit point (AD014): a run whose operations file was written but whose
    manifest never was holds no committed generation, so it stays retryable under the same
    run id.
    """

    next_action = (
        "Re-run `diff` without `--run-id` so the new plan is written under a fresh run id. To use the "
        "existing plan instead, review it with `diff --from-plan <run-id>` or apply it with "
        "`apply --run-id <run-id>`."
    )


class UnsafeRunIdentifierError(PlanArtifactError):
    """A run identifier is not a single path segment.

    A value carrying `/` or `..`, or an absolute path, is rejected by the cache-layout guard
    (`infrahub_sync/cache/paths.py`) with a `ValueError` — which reached the operator as a raw
    traceback out of two commands whose every other bad-identifier verdict is one designed
    line. It is easy to produce: pasting a run *path* where a run *id* goes, which the
    `Cached run <id> at <dir>` line invites. Inside the taxonomy it carries its own remedy
    like every other refusal (AD059).
    """

    next_action = (
        "Pass only the run identifier — the last component of the run directory's path — with no '/' or '..' segments."
    )


class UnknownPlanKindError(PlanArtifactError):
    """A `kind` filter selects nothing, from either of the two conditions that can cause it.

    **Undeclared** — the configuration does not declare that kind — is raised by the reader.
    **Declared but unrepresented** — the configuration declares it and the plan simply holds
    no operation for it — is raised by the **renderer**, because the reader answers `[]`
    there by design: FR-029 requires a programmatic caller to consume the result as data,
    and forcing one to catch an exception to learn a count is a presentation rule leaking
    into the data interface (AD058).

    One class, because the operator's remedy is the same in both — pick a kind the plan
    holds, which both messages list — while the messages themselves stay distinguishable so
    the operator can tell a typo from an empty class of work.
    """

    next_action = "Re-run naming one of the destination kinds listed above."


class UnsupportedOperationActionError(PlanArtifactError):
    """An operation record's `action` falls outside the closed action vocabulary."""

    next_action = (
        "Re-run `diff` for this sync to rebuild the plan artifact with this version of infrahub-sync, "
        "which is the only version that can be relied on to interpret it."
    )


class DuplicateOperationIdError(PlanArtifactError):
    """Two operations in one plan share an operation identifier."""

    next_action = (
        "The plan is pathological — two operations address the same object with the same action: "
        "correct the schema mapping that produced them."
    )


class UnserializablePayloadValueError(PlanArtifactError):
    """A value reached `canonical_value` whose Python type is outside its table."""

    next_action = (
        "Narrow the field's mapping, or add the type to the canonical-value table; the artifact "
        "cannot be written deterministically without it."
    )


class PeerNotFoundError(PlanArtifactError):
    """A peer identity matches no object at the destination.

    A **destination** miss, resolved at apply time. A peer missing from the loaded
    **source** store at plan time is `SourcePeerUnresolvedError`, whose remedy is a
    different one (AD071).
    """

    next_action = "Create the peer at the destination, or re-plan so the same plan creates it."


class PeerAmbiguousError(PlanArtifactError):
    """A peer identity matches more than one object at the destination."""

    next_action = (
        "The destination kind's identity is not unique for these values: de-duplicate at the "
        "destination, or narrow the mapping's identifiers."
    )


class UnaccountedIdentityComponentError(PlanArtifactError):
    """A destination kind's human-friendly-ID component is not accounted for (AD051).

    The apply-time counterpart of FR-024's plan-time warning, raised before the write is
    issued so an AD042-class regression fails loudly instead of duplicating silently. The
    message names the kind and **which** component is missing, which is the whole reason
    this check is defined per component rather than as a single keyedness test.
    """

    next_action = (
        "Re-plan so the plan's identity for that kind supplies the named component, or add it to that "
        "kind's `identifiers` in the schema mapping."
    )


class UnkeyedWriteRefusedError(PlanArtifactError):
    """The rendered mutation carries no key for a kind whose HFID is all-direct (AD066).

    Keyedness is a property of the rendered mutation input rather than of the assembled
    data, so it is read there. For a kind every one of whose human-friendly-ID components is
    a direct attribute, a render carrying neither `id` nor `hfid` can only mean the payload
    lost its identity components, so the write is refused. A kind whose components cross a
    relationship, and a kind declaring no human-friendly ID at all, are **warned** about and
    proceed instead (AD076).
    """

    next_action = (
        "Re-plan and re-apply: the operation's payload must carry the identity components. If a fresh "
        "plan renders the same way, report it — the payload is losing them between derivation and write."
    )


class PlanVerificationError(PlanArtifactError):
    """One or more pre-apply checks failed, so the apply is refused."""

    next_action = "Address each failed check named above; every failure carries its own next action."


class OperationApplyFailedError(PlanArtifactError):
    """The destination rejected an operation, or transport failed while applying it (AD027).

    Carries the **partial** apply record — every operation applied before this one, every
    delete skipped before it, and this operation's own identifier under `failed_operation` —
    because the CLI is the single writer of the run record (AD069) and cannot record what it
    was never handed.

    Nothing is rolled back. Applying one operation is not one write either: the base upsert
    precedes the relationship flush, so the failing operation may have changed the
    destination as well, which is what `may_have_partially_written` marks.
    """

    next_action = (
        "Nothing was rolled back: the operations applied before this one stay written, and this one "
        "may have written part of its own change. Resolve the underlying error at the destination, "
        "then re-run `diff` and apply the new plan — re-applying an operation that already succeeded, "
        "in whole or in part, converges rather than duplicating."
    )

    def __init__(self, message: str, *, apply_record: ApplyRecord, next_action: str | None = None) -> None:
        super().__init__(message, next_action=next_action)
        self.apply_record = apply_record


class ApplyRecordInvariantError(PlanArtifactError):
    """A completed apply's record does not account for every operation in the plan (AD062).

    The knowability invariant DBR-016 protects: on a **completed** apply the applied and
    skipped-delete identifiers together are exactly the plan's identifier set, and their
    counts sum to `operations_count`. A violation means the record cannot be compared
    against what was reviewed, which is the difference between a disclosed skip and a
    silent one — so it is raised rather than asserted, and only after the loop, since a
    partial apply breaks both clauses by construction.

    Carries the record it is complaining about, for the same reason
    `OperationApplyFailedError` carries its partial one: this is raised *after* every
    non-delete operation was written, so recording an empty record against it would tell an
    operator that a run which wrote everything applied nothing — and invite a re-apply
    against a populated destination.
    """

    next_action = (
        "Do not re-apply this run before checking the destination: the apply reported a set of "
        "operations that does not match the plan it read. Report the run identifier and this message, "
        "then re-run `diff` to rebuild the plan."
    )

    def __init__(self, message: str, *, apply_record: ApplyRecord, next_action: str | None = None) -> None:
        super().__init__(message, next_action=next_action)
        self.apply_record = apply_record


class UnformableDestinationIdentityError(PlanArtifactError):
    """No destination identity could be formed for an operation while deriving the plan (AD071)."""

    next_action = (
        "Add the missing attribute to that kind's `identifiers` in the schema mapping, or drop the "
        "kind from the mapping."
    )


class UnwalkedDiffChildrenError(PlanArtifactError):
    """A comparison element carries child elements, which plan derivation does not walk.

    Derivation walks `diff.children` one level deep, exactly as `Potenda._diff_to_rows` does.
    No model this repository generates declares `_children`, so no comparison it produces
    nests — but a custom adapter whose models do would have every child change dropped from
    the plan silently, which is an incomplete plan presented as a complete one (FR-001).

    So the condition is refused rather than warned about, on the same reasoning as AD047:
    a derivation that degrades to warn-and-drop emits an artifact whose reviewer cannot see
    what is missing from it. Recursing into children is not in this release's scope; the
    guard names the limitation at the one place where it would otherwise be invisible.
    """

    next_action = (
        "Plan derivation does not descend into child objects in this release: remove `_children` from "
        "that model, or synchronize the child kinds as top-level kinds of their own."
    )


class SourcePeerUnresolvedError(PlanArtifactError):
    """A relationship peer could not be resolved against the loaded source store (AD071, AD082).

    Two conditions reach this class and their remedies differ, so each arm carries its
    own next action: **absent** — the peer is in none of the candidate kinds' buckets;
    **ambiguous** — the bounded kind probe found it in more than one, so its kind cannot
    be established. Use `absent()` / `ambiguous()` rather than the bare constructor, so
    the operator is routed at the condition they actually have.
    """

    ABSENT_NEXT_ACTION = (
        "Add the peer's kind to the configuration so it is loaded, or remove the relationship from the schema mapping."
    )
    AMBIGUOUS_NEXT_ACTION = (
        "Disambiguate the field's `reference` across the schema-mapping entries that declare the "
        "owning kind, so exactly one candidate kind remains."
    )

    next_action = ABSENT_NEXT_ACTION

    @classmethod
    def absent(cls, message: str) -> SourcePeerUnresolvedError:
        """Build the arm for a peer absent from every candidate kind's store bucket."""
        return cls(message, next_action=cls.ABSENT_NEXT_ACTION)

    @classmethod
    def ambiguous(cls, message: str) -> SourcePeerUnresolvedError:
        """Build the arm for a peer whose unique-id resolved in more than one candidate bucket."""
        return cls(message, next_action=cls.AMBIGUOUS_NEXT_ACTION)
