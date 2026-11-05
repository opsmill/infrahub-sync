"""The cause a failed run records for the operator, beside the error's class name.

The service worker records a failure's class name and the class of its immediate cause. For a
refused token, a missing permission and a lineage refusal, it also records text the operator
can act on, which `operator_cause` returns, and for every refusal this repository writes a
next action for, it records that next action. Every text is redacted, stripped of control
characters and capped, and none comes from a destination's response except the permission or
server message `InfrahubErrorClassification.describe` quotes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from infrahub_sync.client.models import MAX_FAILURE_CAUSE_TEXT
from infrahub_sync.execution import redact
from infrahub_sync.infrahub_errors import cause_chain, classify_infrahub_error_chain, quotable_text
from infrahub_sync.plan.errors import LINEAGE_REFUSALS, PlanArtifactError

if TYPE_CHECKING:
    from collections.abc import Sequence


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
      (a `PlanArtifactError`) records that
      `next_action`, and never its message.

    The search for a next action follows only the explicit `raise ... from` links, never an
    error that was being handled when another was raised. The Infrahub classification below
    walks the whole chain.
    - A refused token or a missing permission records `infrahub_error` and its description as
      `cause`. The description quotes only the permission Infrahub names, or a
      capped server message, never the request or the token.

    Every text is redacted, stripped of control characters and capped, like every value a
    run records. Any other message records nothing here: its text can be a destination's
    response.
    """
    # The explicit `raise ... from` links only: an unrelated error that was being handled when
    # `exc` was raised (its `__context__`) must not lend `exc` its next action.
    links = tuple(cause_chain(exc, follow_context=False))
    for link in links:
        if isinstance(link, LINEAGE_REFUSALS):
            return {
                "cause": _recordable(link.message, secrets),
                "next_action": _recordable(link.next_action, secrets),
            }
    recorded: dict[str, Any] = {}
    next_action = next(
        (link.next_action for link in links if isinstance(link, PlanArtifactError) and link.next_action), ""
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
