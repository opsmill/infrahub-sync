"""The cause a failed run records for the operator, beside the error's class name.

The service worker records a failure's class name and the class of its immediate cause. For a
refused token, a missing permission and a lineage refusal, it also records text the operator
can act on, which `operator_cause` returns, and for every refusal this repository writes a
next action for, it records that next action. Every text is redacted, stripped of control
characters and capped, and none comes from a destination's response except the permission or
server message `InfrahubErrorClassification.describe` quotes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from infrahub_sync.client.models import MAX_FAILURE_CAUSE_TEXT
from infrahub_sync.execution import redact
from infrahub_sync.infrahub_errors import cause_chain, classify_infrahub_error_chain, quotable_text
from infrahub_sync.plan.errors import LINEAGE_REFUSALS, PlanArtifactError

if TYPE_CHECKING:
    from collections.abc import Sequence

RecoveryAction = Literal["rebuild", "compatible_version"]

# The next action for each recovery signal a refused registered plan carries. Fixed text, so the
# record never quotes the manifest values a damaged or hand-edited artifact may hold.
_RECOVERY_NEXT_ACTIONS: dict[RecoveryAction, str] = {
    "rebuild": "re-run diff for this sync, review the new plan, then apply that run",
    "compatible_version": (
        "re-run diff with this version and review the new plan, "
        "or apply the reviewed plan with the version that wrote it"
    ),
}


class RegisteredPlanVerificationError(ValueError):
    """A managed plan refusal with a fixed, safe recovery signal for the CLI.

    `next_action` is the fixed text for `recovery_action`, and empty when the refusal has no
    recovery signal.
    """

    def __init__(self, message: str, recovery_action: RecoveryAction | None) -> None:
        super().__init__(message)
        self.recovery_action = recovery_action
        self.next_action = "" if recovery_action is None else _RECOVERY_NEXT_ACTIONS[recovery_action]


# The errors whose `next_action` this repository writes, beside the lineage refusals that
# `operator_cause` returns for first. Only their next action is recorded: whether a message is
# safe to quote is decided by `LINEAGE_REFUSALS` alone.
_HINTED_ERRORS = (PlanArtifactError, RegisteredPlanVerificationError)


def _recordable(text: str, secrets: Sequence[str]) -> str:
    """Return text fit to record and print: redacted, without control characters, capped."""
    return quotable_text(redact(text, secrets), limit=MAX_FAILURE_CAUSE_TEXT)


def operator_cause(exc: BaseException, secrets: Sequence[str]) -> dict[str, Any]:
    """The cause and next action an operator acts on, from the cause chain.

    A plan wraps each side's load failure in a `ValueError` raised `from` the original, and the
    lineage resolution that starts a plan, and an apply, raise the original, so the walk covers
    both stages.

    - A lineage refusal (`LINEAGE_REFUSALS`) records its `message` as `cause` and its
      `next_action`. This repository writes those messages, and they
      name settings, kinds, node names, account names and ids only.
    - Otherwise, the first link that carries a non-empty `next_action` this repository writes
      (a `PlanArtifactError`, or a `RegisteredPlanVerificationError` with a recovery signal)
      records that `next_action`, and never its message.
    - A refused token or a missing permission records `infrahub_error` and its description as
      `cause`. The description quotes only the permission Infrahub names, or a
      capped server message, never the request or the token.

    Every text is redacted, stripped of control characters and capped, like every value a
    run records. Any other message records nothing here: its text can be a destination's
    response.
    """
    links = tuple(cause_chain(exc))
    for link in links:
        if isinstance(link, LINEAGE_REFUSALS):
            return {
                "cause": _recordable(link.message, secrets),
                "next_action": _recordable(link.next_action, secrets),
            }
    recorded: dict[str, Any] = {}
    next_action = next(
        (link.next_action for link in links if isinstance(link, _HINTED_ERRORS) and link.next_action), ""
    )
    if next_action:
        recorded["next_action"] = _recordable(next_action, secrets)
    classification = classify_infrahub_error_chain(exc)
    if classification is not None:
        recorded["infrahub_error"] = {
            "problem": classification.problem,
            "http_status": classification.http_status,
            "permissions": list(classification.permissions),
        }
        recorded["cause"] = _recordable(classification.describe(), secrets)
    return recorded
