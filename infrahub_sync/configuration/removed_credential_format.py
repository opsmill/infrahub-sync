"""The removed credential format: the ``credentials`` block and ``$credential`` references (spec 027, FR-021).

Both were removed with no migration path. A package that uses either is refused when it is
parsed, and a version that was stored before the removal is refused when a run of it starts.
Each text here is fixed: it names what replaces the removed format, and nothing of the refused
package.
"""

from __future__ import annotations

from typing import Final

CREDENTIAL_FORMAT_REMOVED_REASON: Final = "credential-format-removed"
"""The code of the refusal of a package or a stored version in the removed format."""

CREDENTIAL_FORMAT_REMOVED_MESSAGE: Final = (
    "the package uses the removed `credentials` block or `$credential` references; "
    "name a stored credential in each adapter block with `credential: <name>`"
)
"""What the refusal of a registration says."""

STORED_VERSION_FORMAT_REMOVED_MESSAGE: Final = (
    "the registered configuration version uses the removed `credentials` block or `$credential` references; "
    "register a new version whose adapter blocks name stored credentials with `credential: <name>`"
)
"""What the refusal of a run of a version stored before the removal says."""

_CREDENTIALS_BLOCK_KEY: Final = "credentials"
_CREDENTIAL_REFERENCE_KEY: Final = "$credential"
# A parse failure is a location and a reason. No reason holds ";", which separates two failures
# in the message of a parse error.
_CREDENTIALS_BLOCK_FAILURE: Final = (
    "/credentials",
    "the `credentials` block is removed, name a stored credential in each adapter block with `credential: <name>`",
)
_CREDENTIAL_REFERENCE_FAILURE: Final = (
    "/configuration",
    "`$credential` references are removed, name a stored credential in the adapter block with `credential: <name>`",
)
REMOVED_CREDENTIAL_FORMAT_FAILURES: Final = (_CREDENTIALS_BLOCK_FAILURE, _CREDENTIAL_REFERENCE_FAILURE)
"""The two parse failures of the removed format, each a fixed location and a fixed reason."""


def removed_credential_format_failures(value: object) -> tuple[tuple[str, str], ...]:
    """Return one fixed failure for each part of the removed credential format that ``value`` uses.

    ``value`` is recursively JSON-native content: the caller has checked it, so its depth is
    bounded and it holds no cycle. A ``credentials`` key at the root is the removed block, empty
    or not. A ``$credential`` key in any mapping is a removed reference. Neither failure holds
    a name, a provider, an identifier or a pointer built from the content.
    """
    if type(value) is not dict:  # pylint: disable=unidiomatic-typecheck  # Exact JSON object roots only.
        return ()
    failures: list[tuple[str, str]] = []
    if _CREDENTIALS_BLOCK_KEY in value:
        failures.append(_CREDENTIALS_BLOCK_FAILURE)
    pending: list[object] = [item for key, item in value.items() if key != _CREDENTIALS_BLOCK_KEY]
    while pending:
        node = pending.pop()
        if type(node) is dict:  # pylint: disable=unidiomatic-typecheck
            if _CREDENTIAL_REFERENCE_KEY in node:
                failures.append(_CREDENTIAL_REFERENCE_FAILURE)
                break
            pending.extend(node.values())
        elif type(node) is list:  # pylint: disable=unidiomatic-typecheck
            pending.extend(node)
    return tuple(failures)
