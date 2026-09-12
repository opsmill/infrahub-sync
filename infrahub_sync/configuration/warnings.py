"""The warning families over one declared configuration package.

The declared-content core (``validation.py``) emits errors only and its frozen code
enumeration stays untouched; this module owns the warning channel's finding codes and
their emission, mirroring the schema module's separation. The emission rule is closed:
warnings are limited to intentional omissions and explicitly unqualified optional
features, and nothing else — an adapter-returned warning is contained at the core's
revalidation boundary, and a missing capability needed to determine safety stays an
error, never a warning.

The declared-omission family: one ``intentional-omission`` warning per ``omissions``
entry, at the declaration itself, so the validation report states declared intent rather
than silence. An omission that names content a schema mapping also maps is a package
defect, not a preference — the ``omission-contradicts-mapping`` **error** replaces the
warning at that location, which keeps the omissions section from silently rotting as
mappings evolve.

The unqualified-optional-feature family: a package declaring the optional ``incremental``
feature against a source whose capability declaration does not qualify it silently runs
full extraction, and ``optional-feature-unqualified`` names that declaration. A source
with no capability declaration at all stays the core's own error — a missing capability
needed to determine safety is an error, not a warning — so this family reports nothing
where that error already owns the role.

Messages are fixed templates. The only declared content that reaches one is the omission
``reason``, and it is replaced whole when it carries a collected value, before the finding
is built. That ordering is the point: ``ValidationFinding`` declares
``str_strip_whitespace``, so a reason ending in whitespace is stored one character shorter
than it was declared, and a whole-value match at any later boundary would then look for a
string the message no longer contains. Its model bound keeps template plus reason inside
the finding-text limit, so nothing here needs the core's message truncation. Locations are
fixed literals and list indices, never declared keys, so nothing here needs the core's
pointer bounding either.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .capabilities import BUILTIN_ADAPTER_CAPABILITIES
from .models import REDACTED, ConfigurationPackage, ValidationFinding

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from .models import _OmissionDeclaration

_CODE_INTENTIONAL_OMISSION = "intentional-omission"
_CODE_OMISSION_CONTRADICTS_MAPPING = "omission-contradicts-mapping"
_CODE_OPTIONAL_FEATURE_UNQUALIFIED = "optional-feature-unqualified"

_OMISSION_MESSAGE = "declared content is intentionally omitted from synchronization"
_CONTRADICTION_MESSAGE = "omission names content a schema mapping also maps"
_UNQUALIFIED_INCREMENTAL_MESSAGE = (
    "incremental extraction is declared, but the source adapter does not declare support for it"
)


def _mapped_field_names(package: ConfigurationPackage) -> dict[str, frozenset[str]]:
    """Return every mapped kind with the field names its mapping entries declare."""
    mapped: dict[str, set[str]] = {}
    for mapping in package.configuration.schema_mapping:
        field_names = mapped.setdefault(mapping.name, set())
        field_names.update(field.name for field in mapping.fields)
    return {kind: frozenset(field_names) for kind, field_names in mapped.items()}


def _contradicts(omission: _OmissionDeclaration, mapped: Mapping[str, frozenset[str]]) -> bool:
    """Return whether one omission names content a schema mapping also maps."""
    mapped_fields = mapped.get(omission.kind)
    if mapped_fields is None:
        return False
    if omission.fields is None:
        # The whole kind is declared omitted, and a mapping maps that kind.
        return True
    return any(field_name in mapped_fields for field_name in omission.fields)


def _redacted_reason(reason: str | None, secrets: Sequence[str]) -> str | None:
    """Replace a declared reason whole when it carries a collected value.

    The same rule the declared-key renderer applies, on free text rather than on a pointer
    component: no escaping and no length bound, because a reason is prose the model already
    bounds and the pointer escaping would rewrite it. Replacing it *whole* here is what makes
    the later whole-value boundaries able to agree with this one — the model stores a stripped
    copy of what it is given, so a secret still present at that point is one no whole-value
    match can find afterwards.
    """
    if reason is None:
        return None
    return REDACTED if any(secret in reason for secret in secrets) else reason


def accumulate_intentional_omissions(
    package: ConfigurationPackage, secrets: Sequence[str] = ()
) -> tuple[ValidationFinding, ...]:
    """Report each declared omission at its declaration: a warning, or the contradiction error.

    Declaration order, matching the core's insertion-order rule: when a contradiction is
    the first error in execution order, its position here decides what the wrapper raises.

    ``secrets`` are the collected values the reason must not disclose. The reason is replaced
    whole before the finding is constructed, because the model normalizes what it stores.
    """
    mapped = _mapped_field_names(package)
    findings: list[ValidationFinding] = []
    for index, omission in enumerate(package.omissions):
        location = f"/omissions/{index}"
        if _contradicts(omission, mapped):
            findings.append(
                ValidationFinding(
                    code=_CODE_OMISSION_CONTRADICTS_MAPPING,
                    severity="error",
                    location=location,
                    message=_CONTRADICTION_MESSAGE,
                )
            )
            continue
        reason = _redacted_reason(omission.reason, secrets)
        message = _OMISSION_MESSAGE if reason is None else f"{_OMISSION_MESSAGE}: {reason}"
        findings.append(
            ValidationFinding(
                code=_CODE_INTENTIONAL_OMISSION,
                severity="warning",
                location=location,
                message=message,
            )
        )
    return tuple(findings)


def accumulate_unqualified_optional_features(package: ConfigurationPackage) -> tuple[ValidationFinding, ...]:
    """Warn where a declared optional feature has no capability that qualifies it.

    One optional feature exists today: ``incremental``. A source adapter with no
    capability declaration reports nothing here — the core's missing-adapter error owns
    that role and its subtree is unevaluable.
    """
    if package.configuration.incremental is None:
        return ()
    capabilities = BUILTIN_ADAPTER_CAPABILITIES.get(package.configuration.source.name)
    if capabilities is None or capabilities.incremental_extraction:
        return ()
    return (
        ValidationFinding(
            code=_CODE_OPTIONAL_FEATURE_UNQUALIFIED,
            severity="warning",
            location="/configuration/incremental",
            message=_UNQUALIFIED_INCREMENTAL_MESSAGE,
        ),
    )
