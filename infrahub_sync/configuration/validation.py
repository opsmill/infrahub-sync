"""Accumulating validation over one declared configuration package.

Every check the shipped ``validate_package_credentials`` performed lives here as a finding
producer rather than a raise site, so a package carrying N independent defects yields N
findings.

Two orderings matter and they are deliberately different. ``_accumulate`` runs the checks in
the order the shipped code ran them, which is what makes the wrapper's first-error message
defined rather than incidental. ``collect_findings`` re-orders the same list into the stable
cross-interface order and bounds it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from hashlib import blake2b
from typing import TYPE_CHECKING, Any, Literal, cast
from urllib.parse import urlsplit

from pydantic import ValidationError

from .capabilities import BUILTIN_ADAPTER_CAPABILITIES
from .credentials import (
    _ENV_IDENTIFIER,
    _STORE_CAPABILITIES,
    _TRUNCATION_MARKER,
    CredentialConfigurationError,
    _bounded_component,
    _bounded_location,
    _render_setting_name_list,
)
from .models import (
    _MAX_FINDING_TEXT_LENGTH,
    ConfigurationPackage,
    CredentialReferenceNode,
    ValidationFinding,
    safe_pointer_component,
    sort_findings,
)
from .warnings import accumulate_intentional_omissions, accumulate_unqualified_optional_features

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from .capabilities import AdapterConfigurationCapabilities, AdapterRole

# Absolute settings name where to connect; relative ones name a path beneath it. Applying one
# rule to both would refuse every legitimate relative endpoint.
_ABSOLUTE_URL_SETTING_NAMES = frozenset({"base_url", "url"})
_RELATIVE_PATH_SETTING_NAMES = frozenset({"api_endpoint", "endpoint"})
_URL_SETTING_NAMES = _ABSOLUTE_URL_SETTING_NAMES | _RELATIVE_PATH_SETTING_NAMES
_ALLOWED_URL_SCHEMES = frozenset({"http", "https"})
# Matching models.py's own family of caps. A package with more findings than this is
# pathological, so bounding the work is not worth making the reported set depend on the
# order the defects were declared in.
_MAX_REPORTED_FINDINGS = 256

_CODE_ADAPTER_ROLE_MISMATCH = "adapter-role-mismatch"
_CODE_ADAPTER_VALIDATOR_FINDING = "adapter-validator-finding"
_CODE_CREDENTIAL_PATH_NOT_DECLARED = "credential-path-not-declared"
_CODE_ENDPOINT_NOT_ABSOLUTE = "endpoint-not-absolute"
_CODE_ENDPOINT_NOT_RELATIVE = "endpoint-not-relative"
_CODE_FINDING_LIMIT_REACHED = "finding-limit-reached"
_CODE_INLINE_CREDENTIAL_VALUE = "inline-credential-value"
_CODE_MALFORMED_CREDENTIAL_REFERENCE = "malformed-credential-reference"
_CODE_MISSING_ADAPTER = "missing-adapter"
_CODE_MISSING_STORE_CAPABILITIES = "missing-store-capabilities"
_CODE_SETTING_CONTAINS_CREDENTIAL_MATERIAL = "setting-contains-credential-material"
_CODE_SETTING_NOT_A_STRING = "setting-not-a-string"
_CODE_UNDECLARED_SETTING = "undeclared-setting"
_CODE_UNKNOWN_CREDENTIAL_PROVIDER = "unknown-credential-provider"
_CODE_UNKNOWN_CREDENTIAL_REFERENCE = "unknown-credential-reference"

# "~2" marks a truncated diagnostic pointer, and the finding pointer grammar refuses it: a "~"
# there must introduce "~0" or "~1". This marker is legal and, in a finding as the core emits
# one, unforgeable: safe_pointer_component escapes only unprintable characters into the
# "\uXXXX" form and U+2026 is printable, so a declared key can never render as one.
#
# That holds as far as redaction and no further. redact_pointer decodes every component and
# re-encodes it, and the decoder reads "\u2026" as an escape and writes the single printable
# character back, so a redacted pointer carries a literal "…" where this marker was — which a
# declared key containing "…" also renders as. Both interfaces redact through that one
# function, so they agree with each other. Nothing collides, because the redaction tag carries
# its own digest; what is lost is that in a *redacted* pointer the marker is a hint that the
# pointer was truncated rather than proof of it.
_LOCATION_TRUNCATION_MARKER = r"\u2026"
# Truncation is lossy at two bounds — a declared key past 64 characters and the whole pointer
# past 256 — so two different defects can otherwise arrive as one byte-identical finding twice.
# The digest is taken over the untruncated pointer, so distinct pointers stay distinct and the
# operator can still tell two findings apart. Hex only: the pointer grammar refuses "~" and "/".
_LOCATION_DIGEST_LENGTH = 8


# The checks whose findings the location-precedence rule arbitrates between. A role's own name
# is the check identity for everything judged against that role's declared surface, including
# its adapter-owned validator, so two problems that role reports at one pointer both survive.
_CHECK_CREDENTIALS = "credentials"
_CHECK_STORE = "store"
_CHECK_WALK = "walk"
_CHECK_OMISSIONS = "omissions"
_CHECK_OPTIONAL_FEATURES = "features"

# Where an adapter-owned validator has standing to report. Its own role's subtree, plus the
# package content that belongs to no role: a schema mapping is declared once for the package
# and the shipped genericrestapi validator legitimately reports at it under either role.
_ROLE_INDEPENDENT_VALIDATOR_PREFIXES = ("/configuration/schema_mapping",)


@dataclass(frozen=True, slots=True)
class _AccumulatedFinding:
    """One finding, the message the raise-first code produced for its site, and its check."""

    finding: ValidationFinding
    legacy_message: str
    check: str = ""


def _from_check(check: str, accumulated: list[_AccumulatedFinding]) -> list[_AccumulatedFinding]:
    """Name the check one group of findings came from, which is what precedence is decided on."""
    return [replace(item, check=check) for item in accumulated]


def _from_module(check: str, findings: Iterable[ValidationFinding]) -> list[_AccumulatedFinding]:
    """Adopt findings a sibling emission module built, each carrying its own message."""
    return [_AccumulatedFinding(finding=finding, legacy_message=finding.message, check=check) for finding in findings]


def _unbounded_component(name: object) -> str:
    """Escape one declared key for a pointer, with no length bound and so no information lost."""
    return safe_pointer_component(str(name))


def _location_digest(unbounded_location: str) -> str:
    """Return the short tag that keeps two truncated pointers distinguishable."""
    digest = blake2b(unbounded_location.encode(), digest_size=_LOCATION_DIGEST_LENGTH // 2)
    return digest.hexdigest()


def _marked_location(pointer: str, digested: str) -> str:
    """Tag one pointer a lossy transform rewrote, so it cannot collide with another pointer.

    ``digested`` is what the pointer looked like before the transform dropped anything. The
    marker keeps the tag unforgeable and the digest keeps two pointers the transform mapped
    together apart; the result is re-cut to the field bound because the tag is appended to it.
    """
    suffix = _LOCATION_TRUNCATION_MARKER + _location_digest(digested)
    cut = pointer[: _MAX_FINDING_TEXT_LENGTH - len(suffix)]
    # A cut can land inside an escape pair and leave a trailing "~", which the grammar refuses.
    return cut.rstrip("~") + suffix


def _finding_location(location: str, unbounded_location: str | None = None) -> str:
    """Return one diagnostic pointer in the exact form ``ValidationFinding.location`` accepts.

    ``unbounded_location`` is the same pointer built without either length bound. It is only
    read when the pointer was actually truncated, and only to digest.
    """
    safe = location.replace(_TRUNCATION_MARKER, _LOCATION_TRUNCATION_MARKER)
    # "~2" survives escaping only as a truncation marker, so its presence means a declared key
    # was cut; an over-long pointer is the other bound. Either way information was dropped.
    if _TRUNCATION_MARKER not in location and len(safe) <= _MAX_FINDING_TEXT_LENGTH:
        return safe
    return _marked_location(safe, location if unbounded_location is None else unbounded_location)


def _finding(*, code: str, severity: Literal["error"], location: str, message: str) -> ValidationFinding:
    """Build one finding whose stored pointer is still the pointer that was built.

    ``ValidationFinding`` normalizes what it stores — ``str_strip_whitespace`` trims a pointer
    whose last declared component ends in a space, and U+0020 is printable, so
    ``safe_pointer_component`` passes it through for the model to drop afterwards. That is a
    third lossy transform on a pointer alongside the two length bounds and redaction, and it
    obeys the same rule: no lossy transform may map two distinct pointers onto one. Left
    unmarked it deletes a finding outright, because ``_one_check_per_location`` then reads the
    two as one ``(code, location)`` pair.

    The check is the comparison rather than an escape for the space, so it holds for every
    normalization the model may grow, not only the one that bites today.
    """
    finding = ValidationFinding(code=code, severity=severity, location=location, message=message)
    if finding.location == location:
        return finding
    return ValidationFinding(
        code=code,
        severity=severity,
        # Marked from what the model stored, so the second construction is a fixed point.
        location=_marked_location(finding.location, location),
        message=message,
    )


def _finding_message(message: str) -> str:
    """Bound one finding message the way models.py bounds the text it renders."""
    if len(message) <= _MAX_FINDING_TEXT_LENGTH:
        return message
    return message[: _MAX_FINDING_TEXT_LENGTH - len(_TRUNCATION_MARKER)] + _TRUNCATION_MARKER


def _accumulated(
    *,
    code: str,
    location: str,
    message: str,
    legacy_message: str | None = None,
    unbounded_location: str | None = None,
) -> _AccumulatedFinding:
    """Build one error finding, carrying the shipped message when it differs from the finding's."""
    return _AccumulatedFinding(
        finding=_finding(
            code=code,
            severity="error",
            location=_finding_location(location, unbounded_location),
            message=_finding_message(message),
        ),
        legacy_message=message if legacy_message is None else legacy_message,
    )


def _setting_at_path(settings: Mapping[str, object], path: str) -> tuple[bool, object | None]:
    current: object = settings
    for component in path.split("."):
        if not isinstance(current, Mapping) or component not in current:
            return False, None
        current = cast("Mapping[str, object]", current)[component]
    return True, current


def _settings_pointer(prefix: str, path: str) -> str:
    """Build one settings pointer the same way the declared-content walk builds it.

    The only place a declared setting path becomes a pointer. An owned location and the walk
    must produce byte-identical strings, or a reference is accepted by one check and refused
    by the next; a second formatter is how that divergence gets reintroduced.
    """
    return prefix + "".join(f"/{_bounded_component(component)}" for component in path.split("."))


def _accumulate_reference_declarations(package: ConfigurationPackage) -> list[_AccumulatedFinding]:
    """Validate provider names and identifiers without resolving credential values."""
    accumulated: list[_AccumulatedFinding] = []
    # Insertion order, not sorted: it decides which defect the wrapper reports.
    for name, reference in package.credentials.items():
        location = f"/credentials/{_bounded_component(name)}"
        unbounded = f"/credentials/{_unbounded_component(name)}"
        if reference.provider != "env":
            accumulated.append(
                _accumulated(
                    code=_CODE_UNKNOWN_CREDENTIAL_PROVIDER,
                    location=location,
                    unbounded_location=unbounded,
                    message=(
                        f"credential reference {name!r} uses provider {reference.provider!r}, which is not installed"
                    ),
                )
            )
        if _ENV_IDENTIFIER.fullmatch(reference.identifier) is None:
            accumulated.append(
                _accumulated(
                    code=_CODE_MALFORMED_CREDENTIAL_REFERENCE,
                    location=location,
                    unbounded_location=unbounded,
                    message=f"credential reference {name!r} has an invalid environment identifier",
                )
            )
    return accumulated


def _accumulate_undeclared_settings(
    *,
    settings: Mapping[str, object],
    allowed_settings: frozenset[str],
    prefix: str,
    render: Callable[[Iterable[str]], str],
    owned_locations: list[str],
) -> list[_AccumulatedFinding]:
    """Report one finding per undeclared name, so N undeclared settings give N findings.

    A name the surface does not declare is condemned whole: this check owns that setting and
    everything under it. A name the surface does declare is not owned here, because judging
    the name says nothing about the value.
    """
    unsupported = set(settings) - allowed_settings
    if not unsupported:
        return []
    # The shipped code rendered the whole group into one message; that message stays the
    # wrapper's, and each name gets its own finding and its own pointer.
    legacy_message = render(unsupported)
    accumulated: list[_AccumulatedFinding] = []
    for name in sorted(unsupported):
        location = f"{prefix}/{_bounded_component(name)}"
        owned_locations.append(location)
        accumulated.append(
            _accumulated(
                code=_CODE_UNDECLARED_SETTING,
                location=location,
                unbounded_location=f"{prefix}/{_unbounded_component(name)}",
                message=render({name}),
                legacy_message=legacy_message,
            )
        )
    return accumulated


def _accumulate_reference_node(
    package: ConfigurationPackage,
    value: object,
    *,
    location: str,
) -> list[_AccumulatedFinding]:
    """Validate one node at a declared credential-bearing setting path."""
    if not isinstance(value, Mapping) or "$credential" not in value:
        return [
            _accumulated(
                code=_CODE_INLINE_CREDENTIAL_VALUE,
                location=location,
                message=f"{location} contains an inline credential value",
            )
        ]
    try:
        node = CredentialReferenceNode.model_validate(value)
    except ValidationError:
        return [
            _accumulated(
                code=_CODE_MALFORMED_CREDENTIAL_REFERENCE,
                location=location,
                message=f"{location} contains a malformed credential reference",
            )
        ]
    if node.reference_name not in package.credentials:
        return [
            _accumulated(
                code=_CODE_UNKNOWN_CREDENTIAL_REFERENCE,
                location=location,
                message=f"{location} names unknown credential reference {node.reference_name!r}",
            )
        ]
    return []


def _accumulate_credential_paths(
    package: ConfigurationPackage,
    settings: Mapping[str, object],
    paths: tuple[str, ...],
    prefix: str,
    owned_locations: list[str],
) -> list[_AccumulatedFinding]:
    """Judge every declared credential-bearing path present in these settings, and own it."""
    accumulated: list[_AccumulatedFinding] = []
    for path in paths:
        present, value = _setting_at_path(settings, path)
        if not present or value is None:
            continue
        location = _settings_pointer(prefix, path)
        owned_locations.append(location)
        accumulated.extend(_accumulate_reference_node(package, value, location=location))
    return accumulated


def _accumulate_url_setting(value: object, *, setting_name: str, location: str) -> list[_AccumulatedFinding]:
    """Prove one declared endpoint setting carries no credential material and no scheme surprise."""
    if not isinstance(value, str):
        return [
            _accumulated(
                code=_CODE_SETTING_NOT_A_STRING,
                location=location,
                message=f"{location} must be declared as a string",
            )
        ]
    try:
        parsed = urlsplit(value)
    except ValueError:
        parsed = None
    if parsed is None or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
        return [
            _accumulated(
                code=_CODE_SETTING_CONTAINS_CREDENTIAL_MATERIAL,
                location=location,
                message=f"{location} cannot contain user information, query parameters, or fragments",
            )
        ]
    if setting_name in _ABSOLUTE_URL_SETTING_NAMES:
        if parsed.scheme not in _ALLOWED_URL_SCHEMES or not parsed.netloc:
            return [
                _accumulated(
                    code=_CODE_ENDPOINT_NOT_ABSOLUTE,
                    location=location,
                    message=f"{location} must be an absolute http or https URL",
                )
            ]
    elif parsed.scheme or parsed.netloc:
        return [
            _accumulated(
                code=_CODE_ENDPOINT_NOT_RELATIVE,
                location=location,
                message=f"{location} must be a relative request path without a scheme or authority",
            )
        ]
    return []


def _is_finding_sequence(result: object) -> bool:
    """Return whether a validator returned findings. A str is a Sequence and is not findings."""
    if isinstance(result, (str, bytes)) or not isinstance(result, Sequence):
        return False
    return all(isinstance(item, ValidationFinding) for item in result)


def _revalidated_finding(item: ValidationFinding) -> ValidationFinding:
    """Rebuild one adapter-returned finding under the contract the core holds itself to.

    ``model_construct`` runs no field validator, so an object can satisfy
    ``isinstance(item, ValidationFinding)`` while carrying a severity outside the declared
    vocabulary, a non-string location, or text past the bound. Full model validation is what
    judges that, and the location and message go through the same bounds a core finding does.
    Anything unusable raises, and the caller contains it.
    """
    validated = ValidationFinding.model_validate(item.model_dump())
    if not (validated.location + validated.message).isprintable():
        # The core escapes an unprintable declared key rather than passing it through, so an
        # adapter may not put a raw control character into operator-facing text either.
        msg = "adapter finding carries an unprintable character"
        raise ValueError(msg)
    if validated.severity != "error":
        # The warning channel's emission rule is closed: only the two contract section 4
        # families in the warnings module report warnings. The wrapper never raises on a
        # warning, so an adapter allowed to return one could write a defect into the
        # non-blocking channel; the caller contains this like any other unusable result.
        msg = "adapter finding carries a severity the adapter has no standing to report"
        raise ValueError(msg)
    return _finding(
        code=validated.code,
        severity="error",
        location=_finding_location(validated.location),
        message=_finding_message(validated.message),
    )


def _contained_validator_failure(
    adapter_name: str,
    *,
    role: AdapterRole,
    detail: str,
) -> _AccumulatedFinding:
    """Report a validator that failed, without carrying anything the validator said."""
    return _accumulated(
        code=_CODE_ADAPTER_VALIDATOR_FINDING,
        location=f"/configuration/{role}",
        message=f"adapter {adapter_name!r} configuration validator {detail} for the {role} role",
    )


def _accumulate_adapter_validator(
    package: ConfigurationPackage,
    capabilities: AdapterConfigurationCapabilities,
    *,
    role: AdapterRole,
) -> list[_AccumulatedFinding]:
    """Run one adapter-owned validator, keeping its own codes and locations."""
    validator = capabilities.validator
    if validator is None:
        return []
    # Everything the validator can influence stays inside one contained block. Its shape check
    # iterates the returned object and an isinstance against an ABC can invoke a hostile
    # __instancecheck__; sorting and joining read fields model_construct never validated. Move
    # work into this block rather than out of it.
    #
    # "Everything" means every ordinary failure. SystemExit and KeyboardInterrupt derive from
    # BaseException and are deliberately not caught: a validator that calls sys.exit or an
    # operator pressing Ctrl-C ends the process, and turning either into one contained finding
    # would be worse than the containment is worth.
    detail = "failed"
    try:
        result = validator(package, role)
        # Past the call, anything that goes wrong is about what came back, not about the run.
        detail = "returned an unsupported result"
        if not _is_finding_sequence(result):
            return [_contained_validator_failure(capabilities.adapter_name, role=role, detail=detail)]
        findings = sort_findings([_revalidated_finding(item) for item in result])
        permitted = (f"/configuration/{role}", *_ROLE_INDEPENDENT_VALIDATOR_PREFIXES)
        if not all(_is_within(item.location, permitted) for item in findings):
            # An adapter owns its own role's declared surface and nothing else. Location
            # precedence accepts the first check to report at a pointer, so a finding placed
            # in the other role's subtree, the store's, or the credential declarations would
            # delete the core's own finding there — including a credential-safety one. That
            # is not precedence being wrong; it is a finding with no standing at that pointer.
            return [_contained_validator_failure(capabilities.adapter_name, role=role, detail=detail)]
        legacy_message = "; ".join(f"{_bounded_location(item.location)}: {item.message}" for item in findings)
    # A third-party validator may raise anything at all; containing it is the contract.
    except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        return [_contained_validator_failure(capabilities.adapter_name, role=role, detail=detail)]
    # One adapter can serve both roles, and a validator that cannot tell them apart reports the
    # same package-level defect twice. Which of the two survives is decided at the presentation
    # boundary, never here: the wrapper's message is element zero of this list.
    return [_AccumulatedFinding(finding=item, legacy_message=legacy_message) for item in findings]


def _accumulate_adapter(
    package: ConfigurationPackage,
    capabilities: AdapterConfigurationCapabilities,
    *,
    role: AdapterRole,
    settings: Mapping[str, object],
    owned_locations: list[str],
) -> list[_AccumulatedFinding]:
    """Run every check a declared adapter surface supports, in the shipped order."""
    accumulated: list[_AccumulatedFinding] = []
    if role not in capabilities.roles:
        accumulated.append(
            _accumulated(
                code=_CODE_ADAPTER_ROLE_MISMATCH,
                location=f"/configuration/{role}",
                message=f"adapter {capabilities.adapter_name!r} does not support the {role} role",
            )
        )
    prefix = f"/configuration/{role}/settings"
    accumulated.extend(
        _accumulate_undeclared_settings(
            settings=settings,
            allowed_settings=capabilities.allowed_settings,
            prefix=prefix,
            render=lambda names: (
                f"adapter {capabilities.adapter_name!r} contains unsupported declared settings "
                f"for the {role} role: {_render_setting_name_list(names)}"
            ),
            owned_locations=owned_locations,
        )
    )
    for setting_name in sorted(capabilities.allowed_settings & _URL_SETTING_NAMES):
        value = settings.get(setting_name)
        if value is None:
            continue
        location = _settings_pointer(prefix, setting_name)
        owned_locations.append(location)
        accumulated.extend(_accumulate_url_setting(value, setting_name=setting_name, location=location))
    accumulated.extend(_accumulate_adapter_validator(package, capabilities, role=role))
    paths = capabilities.credential_setting_paths
    accumulated.extend(_accumulate_credential_paths(package, settings, paths, prefix, owned_locations))
    return accumulated


def _accumulate_store(package: ConfigurationPackage, owned_locations: list[str]) -> list[_AccumulatedFinding]:
    """Refuse inline values and undeclared names at the declared store surface."""
    store = package.configuration.store
    if store is None:
        return []
    settings = store.settings or {}
    capabilities = _STORE_CAPABILITIES.get(store.type)
    if capabilities is None:
        if not settings:
            # An undeclared store type carrying nothing declares nothing unsafe. Shipped
            # behaviour, preserved deliberately.
            return []
        # Settings cannot be judged against a surface that does not exist. Whether one of them
        # is credential-bearing is precisely what an undeclared store type makes unknowable,
        # so the store reports exactly one finding, the same way a missing adapter does.
        owned_locations.append("/configuration/store/settings")
        return [
            _accumulated(
                code=_CODE_MISSING_STORE_CAPABILITIES,
                location="/configuration/store",
                message=f"store type {store.type!r} has no configuration capability declaration",
            )
        ]
    prefix = "/configuration/store/settings"
    accumulated = _accumulate_undeclared_settings(
        settings=settings,
        allowed_settings=capabilities.allowed_settings,
        prefix=prefix,
        render=lambda names: (
            f"store type {store.type!r} contains unsupported declared settings: {_render_setting_name_list(names)}"
        ),
        owned_locations=owned_locations,
    )
    paths = capabilities.credential_setting_paths
    accumulated.extend(_accumulate_credential_paths(package, settings, paths, prefix, owned_locations))
    return accumulated


def _is_within(location: str, prefixes: tuple[str, ...]) -> bool:
    """Return whether one pointer is one of `prefixes` or sits beneath one of them."""
    return any(location == prefix or location.startswith(f"{prefix}/") for prefix in prefixes)


def _accumulate_reference_nodes(
    value: object,
    *,
    location: str,
    unbounded_location: str,
    owned_locations: tuple[str, ...],
    accumulated: list[_AccumulatedFinding],
) -> None:
    """Report every use of the reserved ``$credential`` key no surface check has authority over.

    A surface check owns the setting it judged and everything beneath it, so a node under an
    owned pointer belongs to a defect that check already reported — or sits in a subtree no
    surface exists to judge. Reporting it here would give one defect a second, vaguer finding
    at a deeper pointer.
    """
    # The one question, asked once per node. Pruning here rather than at the "$credential"
    # test below is the same answer: every descendant of an owned pointer is within it too.
    if _is_within(location, owned_locations):
        return
    if isinstance(value, Mapping):
        if "$credential" in value:
            accumulated.append(
                _accumulated(
                    code=_CODE_CREDENTIAL_PATH_NOT_DECLARED,
                    location=location,
                    unbounded_location=unbounded_location,
                    message=f"{_bounded_location(location)} is not a credential-bearing setting",
                )
            )
            return
        # Insertion order, not sorted: it decides which defect the wrapper reports.
        for key, item in value.items():
            _accumulate_reference_nodes(
                item,
                location=f"{location}/{_bounded_component(key)}",
                unbounded_location=f"{unbounded_location}/{_unbounded_component(key)}",
                owned_locations=owned_locations,
                accumulated=accumulated,
            )
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _accumulate_reference_nodes(
                item,
                location=f"{location}/{index}",
                unbounded_location=f"{unbounded_location}/{index}",
                owned_locations=owned_locations,
                accumulated=accumulated,
            )


def _accumulate(package: ConfigurationPackage) -> tuple[_AccumulatedFinding, ...]:
    """Run every check in the shipped execution order, never raising for a declared defect."""
    accumulated: list[_AccumulatedFinding] = []
    # Every pointer a surface check has authority over. A check appends the setting it judged,
    # and a surface that turns out not to exist appends its whole settings subtree — the case
    # where a check could not judge. The walk below reports nowhere within them.
    owned_locations: list[str] = []
    accumulated.extend(_from_check(_CHECK_CREDENTIALS, _accumulate_reference_declarations(package)))
    accumulated.extend(_from_check(_CHECK_STORE, _accumulate_store(package, owned_locations)))
    source = package.configuration.source
    destination = package.configuration.destination
    source_capabilities = BUILTIN_ADAPTER_CAPABILITIES.get(source.name)
    destination_capabilities = BUILTIN_ADAPTER_CAPABILITIES.get(destination.name)
    roles: tuple[tuple[AdapterRole, Any, AdapterConfigurationCapabilities | None], ...] = (
        ("source", source, source_capabilities),
        ("destination", destination, destination_capabilities),
    )
    for role, adapter, capabilities in roles:
        if capabilities is None:
            # Settings cannot be judged against a surface that does not exist, so this role
            # reports exactly one finding and the rest of the package is unaffected.
            accumulated.extend(
                _from_check(
                    role,
                    [
                        _accumulated(
                            code=_CODE_MISSING_ADAPTER,
                            location=f"/configuration/{role}",
                            message=f"adapter {adapter.name!r} has no configuration capability declaration",
                        )
                    ],
                )
            )
            owned_locations.append(f"/configuration/{role}/settings")
            continue
        accumulated.extend(
            _from_check(
                role,
                _accumulate_adapter(
                    package,
                    capabilities,
                    role=role,
                    settings=adapter.settings or {},
                    owned_locations=owned_locations,
                ),
            )
        )
    # Last: the surface checks above give a more precise reason for a node on an unsupported
    # setting than "not credential-bearing" would.
    walked: list[_AccumulatedFinding] = []
    _accumulate_reference_nodes(
        package.declared_content(),
        location="",
        unbounded_location="",
        owned_locations=tuple(owned_locations),
        accumulated=walked,
    )
    accumulated.extend(_from_check(_CHECK_WALK, walked))
    # Contract section 4's warning families run last, appended after the shipped execution
    # order, so a package carrying any legacy defect keeps its shipped first-error message
    # at the wrapper. Warnings before an error here are what the wrapper's first-*error*
    # rule exists for.
    accumulated.extend(_from_module(_CHECK_OMISSIONS, accumulate_intentional_omissions(package)))
    accumulated.extend(_from_module(_CHECK_OPTIONAL_FEATURES, accumulate_unqualified_optional_features(package)))
    return tuple(accumulated)


def _one_check_per_location(accumulated: tuple[_AccumulatedFinding, ...]) -> list[ValidationFinding]:
    """Keep, at each location, only what the first check to report there said.

    At any one location one check is authoritative: a later check does not add a second finding
    where an earlier one already reported, and no code repeats at a location. Execution order
    decides which check that is. This runs at the presentation boundary and nowhere else — the
    wrapper reads the untouched accumulation, so its compatibility message cannot depend on it.
    """
    owner: dict[str, str] = {}
    seen: set[tuple[str, str]] = set()
    kept: list[ValidationFinding] = []
    for item in accumulated:
        finding = item.finding
        key = (finding.code, finding.location)
        if key in seen or owner.setdefault(finding.location, item.check) != item.check:
            continue
        seen.add(key)
        kept.append(finding)
    return kept


def collect_findings(package: ConfigurationPackage) -> tuple[ValidationFinding, ...]:
    """Return every declared defect as a finding, in the stable cross-interface order.

    Bounded at 256. Accumulation itself never stops early, because truncating in execution
    order would make the surviving findings depend on the order the defects were declared in;
    the cut happens after the sort instead. When it fires, one ``finding-limit-reached``
    finding goes in its own sort position, which is first — its location is the empty pointer
    and every real pointer begins with "/" — so the suppression is reported at the top rather
    than buried at the end.
    """
    # sort_findings orders by (location, severity, code), and _one_check_per_location has
    # already made (code, location) unique, so no two findings can tie there. Uniqueness is
    # structural, which is why nothing breaks a tie before the cut below.
    findings = sort_findings(_one_check_per_location(_accumulate(package)))
    if len(findings) <= _MAX_REPORTED_FINDINGS:
        return findings
    suppressed = findings[_MAX_REPORTED_FINDINGS:]
    # The maximum severity among the *suppressed* findings: a suppressed error must not
    # leave the presentation error-free, and warnings-only suppression must not turn an
    # error-free package into a refused one.
    sentinel_severity: Literal["error", "warning"] = (
        "error" if any(item.severity == "error" for item in suppressed) else "warning"
    )
    limit_reached = ValidationFinding(
        code=_CODE_FINDING_LIMIT_REACHED,
        severity=sentinel_severity,
        location="",
        message=f"{len(suppressed)} further findings were not reported",
    )
    return sort_findings([limit_reached, *findings[:_MAX_REPORTED_FINDINGS]])


def validate_package_credentials(package: ConfigurationPackage) -> None:
    """Prove bundled adapter settings contain references rather than credential values."""
    accumulated = _accumulate(package)
    for item in accumulated:
        # The first *error* of the untruncated, execution-ordered accumulation: the defect
        # the raise-first code reported, so the message is the one it produced. Errors
        # prevent execution and warnings do not, so a warning is never raised on — a
        # warning-only package passes.
        if item.finding.severity == "error":
            raise CredentialConfigurationError(item.legacy_message)
