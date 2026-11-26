"""The removed credential format: the ``credentials`` block and ``$credential`` references (spec 027, FR-021).

Both were removed with no migration path. A package that uses either is refused when it is
parsed, and a version that was stored before the removal and uses either is refused when a run
of it starts. Each text here is fixed: it names what replaces the removed format, and nothing
of the refused package.

The build before the removal wrote an empty ``credentials`` block into every version, also into
one that declared no reference. Such a stored version uses nothing that was removed, so reading
it ignores the empty block (:func:`without_empty_credentials_block`). New input gets no such
allowance: a package sent for registration or validation is refused for any ``credentials``
key.
"""

from __future__ import annotations

from typing import Any, Final, cast

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


def without_empty_credentials_block(value: object) -> tuple[object, bool]:
    """Return stored package content without its empty ``credentials`` block, and whether it held one.

    Only an exact empty JSON object at the root is ignored. A block that holds an entry, a block
    of another type and every ``$credential`` reference stay in the content, so the parse that
    follows refuses them. ``value`` is not changed.
    """
    if type(value) is not dict:  # pylint: disable=unidiomatic-typecheck  # Exact JSON object roots only.
        return value, False
    content = cast("dict[object, object]", value)
    block = content.get(_CREDENTIALS_BLOCK_KEY)
    if type(block) is not dict or block:  # pylint: disable=unidiomatic-typecheck
        return value, False
    return {key: item for key, item in content.items() if key != _CREDENTIALS_BLOCK_KEY}, True


def with_empty_credentials_block(declared_content: dict[str, Any]) -> dict[str, Any]:
    """Return declared content as the build before the removal stored it: with an empty ``credentials`` block."""
    return {**declared_content, _CREDENTIALS_BLOCK_KEY: {}}
