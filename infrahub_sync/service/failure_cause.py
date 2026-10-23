"""The cause a failed run records for the operator, beside the error's class name.

The service worker records a failure's class name and the class of its immediate cause. For a
refused token, a missing permission and a lineage refusal, it also records text the operator
can act on, which `operator_cause` returns. Every text is redacted, stripped of control
characters and capped, and none comes from a destination's response except the permission or
server message `InfrahubErrorClassification.describe` quotes.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from infrahub_sync.execution import redact
from infrahub_sync.infrahub_errors import classify_infrahub_error_chain
from infrahub_sync.plan.errors import LINEAGE_REFUSALS

if TYPE_CHECKING:
    from collections.abc import Sequence

_MAX_RECORDED_TEXT = 1000
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def _recordable(text: str, secrets: Sequence[str]) -> str:
    """Return text fit to record and print: redacted, without control characters, capped."""
    cleaned = " ".join(_CONTROL_CHARACTERS.sub(" ", redact(text, secrets)).split())
    if len(cleaned) > _MAX_RECORDED_TEXT:
        cleaned = cleaned[: _MAX_RECORDED_TEXT - 1] + "…"
    return cleaned


def operator_cause(exc: BaseException, secrets: Sequence[str]) -> dict[str, Any]:
    """The cause an operator acts on, from the first link of the cause chain that has one.

    A plan wraps every load failure in a `ValueError` raised `from` the original, and an apply
    raises the original, so the walk covers both stages.

    - A lineage refusal (`LINEAGE_REFUSALS`) records its `message` as `cause` and its
      `next_action` (FR-004, FR-011, FR-012). This repository writes those messages, and they
      name settings, kinds, node names, account names and ids only.
    - A refused token or a missing permission records `infrahub_error` and its description as
      `cause` (FR-008). The description quotes only the permission Infrahub names, or a
      capped server message, never the request or the token.

    Both texts are redacted, stripped of control characters and capped, like every value a
    run records. Anything else records nothing here: its text can be a destination's response.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, LINEAGE_REFUSALS):
            message = getattr(current, "message", None) or str(current)
            next_action = getattr(current, "next_action", None)
            cause: dict[str, Any] = {"cause": _recordable(message, secrets)}
            if isinstance(next_action, str) and next_action:
                cause["next_action"] = _recordable(next_action, secrets)
            return cause
        current = current.__cause__ or (None if current.__suppress_context__ else current.__context__)
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
