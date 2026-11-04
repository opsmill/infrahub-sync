"""The cause a failed run records for the operator, beside the error's class name.

The service worker records a failure's class name and the class of its immediate cause. For a
refused token, a missing permission and a lineage refusal, it also records text the operator
can act on, which `operator_cause` returns. Every text is redacted, stripped of control
characters and capped, and none comes from a destination's response except the permission or
server message `InfrahubErrorClassification.describe` quotes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from infrahub_sync.client.models import MAX_FAILURE_CAUSE_TEXT
from infrahub_sync.execution import redact
from infrahub_sync.infrahub_errors import cause_chain, classify_infrahub_error_chain, quotable_text
from infrahub_sync.plan.errors import LINEAGE_REFUSALS

if TYPE_CHECKING:
    from collections.abc import Sequence


def _recordable(text: str, secrets: Sequence[str]) -> str:
    """Return text fit to record and print: redacted, without control characters, capped."""
    return quotable_text(redact(text, secrets), limit=MAX_FAILURE_CAUSE_TEXT)


def operator_cause(exc: BaseException, secrets: Sequence[str]) -> dict[str, Any]:
    """The cause an operator acts on, from the first link of the cause chain that has one.

    A plan wraps each side's load failure in a `ValueError` raised `from` the original, and the
    lineage resolution that starts a plan, and an apply, raise the original, so the walk covers
    both stages.

    - A lineage refusal (`LINEAGE_REFUSALS`) records its `message` as `cause` and its
      `next_action`. This repository writes those messages, and they
      name settings, kinds, node names, account names and ids only.
    - A refused token or a missing permission records `infrahub_error` and its description as
      `cause`. The description quotes only the permission Infrahub names, or a
      capped server message, never the request or the token.

    Both texts are redacted, stripped of control characters and capped, like every value a
    run records. Anything else records nothing here: its text can be a destination's response.
    """
    for link in cause_chain(exc):
        if isinstance(link, LINEAGE_REFUSALS):
            return {
                "cause": _recordable(link.message, secrets),
                "next_action": _recordable(link.next_action, secrets),
            }
    classification = classify_infrahub_error_chain(exc)
    if classification is None:
        return {}
    return {
        "infrahub_error": {
            "problem": classification.problem,
            "http_status": classification.http_status,
            "permissions": list(classification.permissions),
        },
        "cause": _recordable(classification.describe(), secrets),
    }
