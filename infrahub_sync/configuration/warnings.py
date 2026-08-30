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
``reason``, verbatim: its model bound keeps template plus reason inside the finding-text
limit, so nothing here needs the core's message truncation. Locations are fixed literals
and list indices, never declared keys, so nothing here needs the core's pointer bounding
either.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .capabilities import BUILTIN_ADAPTER_CAPABILITIES
from .models import ConfigurationPackage, ValidationFinding

if TYPE_CHECKING:
    from collections.abc import Mapping

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


def accumulate_intentional_omissions(package: ConfigurationPackage) -> tuple[ValidationFinding, ...]:
    """Report each declared omission at its declaration: a warning, or the contradiction error.

    Declaration order, matching the core's insertion-order rule: when a contradiction is
    the first error in execution order, its position here decides what the wrapper raises.
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
        message = _OMISSION_MESSAGE if omission.reason is None else f"{_OMISSION_MESSAGE}: {omission.reason}"
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
