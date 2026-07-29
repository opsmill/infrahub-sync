"""The plan-artifact error taxonomy.

Every failure on a plan-artifact path is one of the classes below, and every one of
them tells the operator what to do next: `PlanArtifactError` declares `next_action`
on the base class, so a subclass cannot be added without one (AD059). The wording of
each class's next action is the taxonomy table in
`dev/specs/001-plan-artifact-saved-apply/contracts/plan-reader-api.md`.

Constructing an error whose class declares no non-empty `next_action` raises
`TypeError`: the guarantee is enforced where it can be observed rather than left to
review. Callers may override the declared wording with the `next_action` keyword,
which is how `SourcePeerUnresolvedError` routes its two conditions to two remedies
(AD082).

`SkippedDeleteOperation` is the one class here that is **not** a failure. It is the
control signal a destination write surface raises if it is ever handed a recorded delete,
which this release does not execute, and it deliberately sits outside `PlanArtifactError`
so no caller reads it as an error or asks it for a remedy (AD055).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from infrahub_sync.plan.models import ApplyRecord


class SkippedDeleteOperation(Exception):  # noqa: N818 — a control signal, not an error
    """A recorded delete a write surface declines to execute (FR-016, FR-017, AD055).

    The **defensive** half of the contract. Applying deletes is out of scope for this
    release, so a write surface handed a `delete` operation must raise this instead of
    touching the destination.

    In normal operation it is never raised, because the engine never dispatches a delete:
    the apply loop recognizes `operation.action == "delete"` itself, records the identifier
    and continues without calling the write surface at all
    (`infrahub_sync/potenda/__init__.py:580-582`). Nothing catches this class in product
    code. It exists so that an adapter reached by some other route still refuses rather than
    deletes.

    The outcome either way is the same: every non-delete operation in the plan is applied
    and the run ends `applied`, with the count and the skipped identifiers recorded on the
    apply record (`:612-616`, `:637-644`). It is a designed limitation reported as one,
    which is why this class is not part of the `PlanArtifactError` taxonomy and carries no
    `next_action`: there is nothing for the operator to repair.
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
    """The run holds no `plan/` directory at all, so its plan predates this format."""

    next_action = "Re-plan: re-run `diff` for this sync to produce a current-format plan artifact."


class PlanArtifactTornError(PlanArtifactError):
    """The artifact is present but incomplete, inconsistent, or unreadable as a record set."""

    next_action = "Re-run `diff` to rebuild the plan artifact; the partial one cannot be repaired."


class PlanFormatVersionError(PlanArtifactError):
    """The manifest declares a `format_version` this version of the tool does not support."""

    next_action = (
        "The artifact was written by a different version of infrahub-sync: re-plan with this "
        "version, or apply with the version that wrote it."
    )


class PlanArtifactUnreadableError(PlanArtifactError):
    """A permission or I/O failure stopped an artifact path from being read."""

    next_action = "Check permissions and ownership on the named path, then retry."


class UnknownRunIdentifierError(PlanArtifactError):
    """No run with the requested identifier is stored for this synchronization (FR-008, AD073).

    Two arms, because the audiences differ. When runs **do** exist the operator mistyped or
    is looking at the wrong sync, and the remedy is one of the identifiers the message
    lists. When the cache root is absent or holds no runs at all the audience is by
    construction the first-run operator, for whom "pick from the list" is a dead end, so
    that arm names the command that produces a plan — as the sibling `PlanFormatV1Error`
    row does. Use `no_runs()` for the second arm.
    """

    next_action = "Re-run naming one of the run identifiers listed above."

    NO_RUNS_NEXT_ACTION = "Run `infrahub-sync diff --name {sync_name}` for this sync to produce a plan first."

    @classmethod
    def no_runs(cls, message: str, *, sync_name: str) -> UnknownRunIdentifierError:
        """Build the arm for a sync whose cache root is absent or holds no run directories."""
        return cls(message, next_action=cls.NO_RUNS_NEXT_ACTION.format(sync_name=sync_name))


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

    next_action = "The artifact was produced by a tool this version does not understand: re-plan with this version."


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

    Keyedness is a property of the rendered mutation input, not of the assembled data, so
    it is read there. For a kind every one of whose human-friendly-ID components is a
    direct attribute, a render carrying neither `id` nor `hfid` can only mean the payload
    lost its identity components, so the write is refused. A kind whose components cross a
    relationship, and a kind that declares no human-friendly ID at all, are **warned**
    about and proceed — being unkeyed is expected for the first and a schema fact for the
    second (AD076).
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
    was never handed. Without it, FR-025's last-applied pointer could not survive a partial
    apply at all.

    Nothing is rolled back: what was written stays written, which is also what keeps a
    partial apply distinguishable from a completed one — neither clause of the knowability
    invariant holds for it, since the unattempted operations are in neither set. Applying one
    operation is not one write either: the base upsert precedes the relationship flush, so the
    failing operation may have changed the destination as well, and the record marks that.
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
