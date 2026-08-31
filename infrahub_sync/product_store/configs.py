"""The shared ``configs`` application service.

This module owns validation invocation, finding ordering, store access, and record projection.
Internal callers receive typed records; the HTTP service projects them into neutral wire models.
Neither boundary constructs a finding, sorts one, or decides what a failure means.

**One error vocabulary, mapped twice.** Everything this module refuses raises a
:class:`ConfigsError` carrying a ``family``. The CLI renders it through
``print_error_and_abort`` and the HTTP service translates it into its public error envelope.
Both read the same ``family`` from the same exception.

**Total by construction, not by enumeration.** Every public operation carries
:func:`_service_boundary`, so no exception leaves this module outside that vocabulary. The
arms inside an operation still decide the family and the message wherever they know what was
being read; the boundary is only what makes the claim hold for what they did not name.

**Scope.** This remains the internal configuration application service. Public Python callers use
the Sync HTTP client rather than importing this module.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, ParamSpec, TypeVar

import yaml
from pydantic_core import PydanticCustomError

from infrahub_sync.configuration import (
    ConfigurationPackage,
    ConfigurationPackageParseError,
    CredentialConfigurationError,
    ValidationFinding,
    collect_findings,
    parse_configuration_package,
    sort_findings,
)
from infrahub_sync.configuration.models import (
    _MAX_FINDING_TEXT_LENGTH,
    _POINTER_CHARACTER_ESCAPES,
    _require_json_native,
    safe_pointer_component,
)
from infrahub_sync.configuration.schema_validation import (
    DestinationSchemaOptions,
    collect_destination_schema_findings,
)
from infrahub_sync.configuration.validation import _location_digest
from infrahub_sync.execution import REDACTED, redact
from infrahub_sync.product_store.standalone import ProductCacheLocationError, resolve_product_cache_location
from infrahub_sync.product_store.store import (
    ConfigurationNotFoundError,
    ConfigurationVersionAllocationError,
    DuplicateConfigurationError,
    ProductProjection,
    ProductStoreProviderError,
    local_product_projection,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from infrahub_sync.product_store.models import ConfigurationSummary, ConfigurationVersion


class ConfigsError(Exception):
    """Base of the one error vocabulary both ``configs`` interfaces map.

    ``family`` is the declared vocabulary. It is what an interface maps, so a new failure
    mode joins an existing family or adds one here — never at a boundary.
    """

    family: ClassVar[str] = "configs"
    # Declared on the base so a boundary reads findings off any refusal without naming a
    # narrow type. Only a validation refusal ever carries them.
    findings: tuple[ValidationFinding, ...] = ()


class ConfigsRequestError(ConfigsError):
    """The caller's own input is unusable: a bad store location or unparseable content."""

    family: ClassVar[str] = "request"


class ConfigsValidationError(ConfigsError):
    """A declared package was refused by validation and therefore was not persisted."""

    family: ClassVar[str] = "validation"

    def __init__(self, message: str, *, findings: tuple[ValidationFinding, ...] = ()) -> None:
        super().__init__(message)
        self.findings = findings


class ConfigsNotFoundError(ConfigsError):
    """The requested configuration or version is not in the registry.

    ``reason`` is the machine-readable half of the refusal: a stable value naming *which*
    absence this is, so service-boundary can map a missing configuration and a missing
    version of an existing configuration to two different status codes without parsing the
    message. The store's own version lookup blends the two cases into one reason, so the
    distinction is made here, where the two-step read (configuration first, then version)
    knows which step failed. It defaults to the family itself for refusals that predate the
    distinction and have exactly one absence to name.
    """

    family: ClassVar[str] = "not-found"

    def __init__(self, message: str, *, reason: str = "not-found") -> None:
        super().__init__(message)
        self.reason = reason


class ConfigsStorageError(ConfigsError):
    """The durable store refused the operation for a reason the caller cannot fix by input."""

    family: ClassVar[str] = "storage"


class ConfigsInternalError(ConfigsError):
    """The service failed for a reason it cannot classify: the boundary's documented default.

    Neither the caller's input nor the store — a defect in this service, or a dependency
    raising something no arm here has met. It exists so the vocabulary can be total without
    any refusal having to guess a family it does not know, and so an operator reading
    ``internal`` looks for a bug to report rather than at their own file or their own disk.
    """

    family: ClassVar[str] = "internal"


_P = ParamSpec("_P")
_R = TypeVar("_R")

# The inventory of guarded operations, written by the boundary itself as the module is
# imported. Coverage is therefore not a list anyone maintains by hand: the service's own tests
# compare this against the module's public functions, so an operation added without the
# boundary fails them.
_GUARDED_OPERATIONS: set[str] = set()


def _service_boundary(operation: Callable[_P, _R]) -> Callable[_P, _R]:
    """Make one public service operation total over the declared vocabulary.

    The arms inside an operation stay, and stay authoritative: they know what they were
    reading, so they give the precise family and the message an operator can act on. This is
    what makes the vocabulary *total* — it catches what no arm named, so nothing leaves as a
    raw traceback outside the one vocabulary both interfaces map. Enumerating exception types
    at each call site does not: every site left unenumerated is another escape.

    Classification is by ``except`` clause only, which matches the exception's actual type
    without touching the instance. ``isinstance`` is itself inspection — it consults the
    instance's ``__class__``, a read a hostile property executes on — and an exception no
    arm named can have been constructed by a hostile caller argument (a
    ``product_cache_location`` whose ``__str__`` raises it), so a runtime classification
    table lets a hostile value escape this module as a raw untyped error. Nothing reaches
    the text from the exception either: one fixed message per operation.

    The boundary infers only two families from an exception no arm named, and nothing
    else. A filesystem or SQLite error can only have come from the store, and this module
    parses no YAML but a caller's, so a parse error can only be about caller content. A
    decode or a recursion error is deliberately *not* inferred: either can come from a
    defect in this module as easily as from a caller's file, and guessing ``request`` for
    an unattributable failure blames the operator's input for something they cannot fix.
    An arm that knows what it was reading names those; whatever is left is unclassified
    and says so.

    Three rules it keeps:

    * A :class:`ConfigsError` already raised propagates untouched and is never re-wrapped, so
      an arm's family and message always win over this mapping.
    * ``BaseException`` is not caught. ``SystemExit`` and ``KeyboardInterrupt`` still
      propagate, which is the decision the run boundary in ``execution.py`` already made.
    * Coverage is not a list kept in step by hand: what this records in
      :data:`_GUARDED_OPERATIONS` is what the service's own tests enumerate, so a public
      operation added without the boundary fails them.
    """
    # A ``Callable`` carries no ``__name__`` in the type system. Everything this decorates is a
    # plain module-level function, so the fallback never fires.
    name = getattr(operation, "__name__", "operation")
    _GUARDED_OPERATIONS.add(name)
    refusal = f"configs {name} failed"

    @wraps(operation)
    def guarded(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        try:
            return operation(*args, **kwargs)
        except ConfigsError:
            raise
        except (OSError, sqlite3.Error, ProductStoreProviderError):
            raise ConfigsStorageError(refusal) from None
        except yaml.YAMLError:
            raise ConfigsRequestError(refusal) from None
        except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught  # The totality rule: re-raised typed, nothing read.
            raise ConfigsInternalError(refusal) from None

    return guarded


# Every escape ``_escape_pointer_character`` can write, read backwards. "~0" and "~1" are the
# only two the pointer grammar cares about, but inverting only those leaves a declared key
# holding any of the others unmatched against a collected value, and so unredacted. ":" is the
# one that decides this: ``_add_url_userinfo`` collects a credential as "user:password", which
# makes the most common collected-secret shape the one an escape-blind match cannot find.
_POINTER_ESCAPE_DECODINGS = {escaped: character for character, escaped in _POINTER_CHARACTER_ESCAPES.items()}
# Longest first. Defensive, not load-bearing against the table as it stands: the only entries
# longer than two characters are "\\u003a" and "\\u003b", and the two characters they open with
# are "\\u", which is not itself an entry — so no shorter entry can be matched inside a longer
# one and today the scan decodes the same text in either order. What the order rules out is a
# future entry that *is* a prefix of a longer one, which would otherwise be consumed first and
# leave the rest of the longer escape as literal text. Nothing can observe this ordering until
# such an entry exists, so nothing tests it.
_POINTER_ESCAPE_LENGTHS = sorted({len(escaped) for escaped in _POINTER_ESCAPE_DECODINGS}, reverse=True)
# The table names two codepoints explicitly; every other unprintable one is written in the same
# form with its own hexadecimal value, and parsing it is what tells an escaped backslash apart
# from the backslash that opens an escape.
_GENERIC_POINTER_ESCAPE = re.compile(r"\\u(?P<short>[0-9a-f]{4})|\\U(?P<long>[0-9a-f]{8})")
_MAX_CODEPOINT = 0x10FFFF


def _decode_pointer_component(component: str) -> str:
    """Return the declared key one escaped pointer component stands for.

    Every escape, not only the two separators. A left-to-right scan, not a sequence of
    replacements: the escapes overlap, and one leading backslash opens three different
    readings — an escaped backslash, an escaped colon, and an unprintable character written in
    hexadecimal. Only consuming a whole escape before looking at what follows it tells them
    apart, and only then is the result the declared key a collected value can be matched
    against.

    An escape the scan does not recognise is passed through one character at a time, which is
    what a location the core did not build can contain.
    """
    decoded: list[str] = []
    index = 0
    while index < len(component):
        escaped = next(
            (
                component[index : index + length]
                for length in _POINTER_ESCAPE_LENGTHS
                if component[index : index + length] in _POINTER_ESCAPE_DECODINGS
            ),
            None,
        )
        if escaped is not None:
            decoded.append(_POINTER_ESCAPE_DECODINGS[escaped])
            index += len(escaped)
            continue
        generic = _GENERIC_POINTER_ESCAPE.match(component, index)
        codepoint = _MAX_CODEPOINT + 1 if generic is None else int(generic["short"] or generic["long"], 16)
        if generic is not None and codepoint <= _MAX_CODEPOINT:
            decoded.append(chr(codepoint))
            index = generic.end()
            continue
        decoded.append(component[index])
        index += 1
    return "".join(decoded)


def _encode_pointer_component(key: str) -> str:
    """Return the escaped pointer component one declared key is written as.

    The one escaping function the core itself uses, so the pointer this rebuilds is written the
    way the pointer it took apart was, and ``_decode_pointer_component`` inverts it exactly.
    """
    return safe_pointer_component(key)


def redact_pointer(location: str, secrets: Sequence[str]) -> str:
    """Redact the declared keys a finding pointer quotes, tagged so it stays distinguishable.

    A pointer is built from declared keys, so it can carry a collected value. The value is
    matched against each *decoded* key — decoded through the whole escape table, because
    ``_add_url_userinfo`` collects a credential as "user:password" and a pointer writes that
    colon as "\u003a", so an escape-blind match misses the most common collected shape there
    is. The result is escaped again on the way back, so no replacement can orphan a "~0"/"~1"
    escape or introduce a separator: the escapes in the returned pointer are written after
    redaction, not edited by it. That is also why a value
    carrying "/" or "~" is caught — it is invisible in a component's escaped text — and why a
    value that only appears across the "/" between two components is not: that "/" is
    structure, not declared content.

    Redaction is lossy, so it obeys the rule truncation already obeys in ``validation.py``: two
    distinct pointers may not collapse into one. A redacted pointer therefore ends with the
    redaction marker and the same short digest a truncated pointer carries — one mechanism, not
    a second one to keep in step. Nothing is appended when no key was replaced.

    **The digest is taken over the whole unredacted pointer, not over the component that was
    replaced.** In the case this exists for, the replaced component *is* a current credential
    value, so digesting the component alone would publish an unsalted hash of a secret: the same
    tag wherever that value appears, in any report, joinable against a precomputed table of
    candidate values. Digesting the pointer binds the value to the declared path it was found
    at, so the tag is not a portable fingerprint. It is still a confirmation oracle for someone
    who guesses the value and can see the surrounding path, which is accepted here: a tag over
    the pointer's *position* rather than its content is identical for the two findings this
    exists to separate and would leave them indistinguishable. Both interfaces redact through
    this function, so the tag cannot differ between them.

    The round-trip does not preserve ``validation.py``'s truncation marker: a redacted pointer
    shows a literal "…" where an unredacted one shows the six-character marker, so there that
    character is a hint that truncation happened rather than proof of it. Nothing collides: the
    tag appended here carries its own digest.
    """
    components: list[str] = []
    replaced = False
    for component in location.split("/"):
        key = _decode_pointer_component(component)
        cleaned = redact(key, secrets)
        replaced = replaced or cleaned != key
        components.append(_encode_pointer_component(cleaned))
    if not replaced:
        return location
    redacted = "/".join(components)
    tag = REDACTED + _location_digest(location)
    room = _MAX_FINDING_TEXT_LENGTH - len(tag)
    if len(redacted) > room:
        # The replacement can be longer than what it replaced, so the field's own bound is
        # re-applied. This cut is the one place a "~" can still be separated from the character
        # that completes it, so a trailing "~" is dropped; the tag survives the cut because it
        # is appended after it.
        redacted = redacted[:room].rstrip("~")
    return redacted + tag


# The forms a declared key reaches a finding *message* in. A message is not a pointer: it is
# free text that already carries "/" as structure and quotation as prose, so it cannot be
# decoded and re-encoded the way ``redact_pointer`` takes a pointer apart. The secret is
# encoded instead, which is exact — and one derivation covers both producers, because both
# start from ``safe_pointer_component``:
#
# * ``_bounded_location`` embeds an escaped pointer directly, so a key appears exactly as
#   ``safe_pointer_component`` writes it: one backslash before "u003a".
# * ``_render_setting_name_list`` escapes and then hands the result to ``json.dumps``, so the
#   same key appears with every backslash doubled and every non-ASCII character re-escaped.
#
# The second form is therefore ``json.dumps`` of the first, not a second rule to keep in step
# with it: whatever the escaping table becomes, both forms follow it.
#
# A third *renderer* — not a third spelling of the same table — does exist, and it is Python's
# own ``repr``. Several producers write caller-declared text through "!r": an adapter name, a
# store type, a credential reference name, a config_id, a path. For a printable character
# ``repr`` agrees with a form already covered, but for a non-printable one it does not: the
# pointer table writes the six characters "\u0001" and ``repr`` writes "\x01", so an
# escaped-form match never finds it. The third form is therefore ``repr`` applied to the
# secret, derived the same way the JSON form is — by running the real renderer, not by
# restating what it emits.
#
# What this returns is therefore four *known* renderings of a collected value, and not every
# rendering of it. Nobody has shown the producer set is closed, and one producer outside it is
# already known: ``_render_context_pointer`` in ``configuration/models.py`` escapes a parse
# diagnostic's pointer a fifth way, and a value holding ":" together with a '"' or a non-ASCII
# character reaches a parse refusal in a form none of the four match. Enumerating renderings is
# the wrong mechanism at that point and escaping belongs at a single chokepoint instead, which
# is a change this function does not make.
def _message_secret_forms(secrets: Sequence[str]) -> tuple[str, ...]:
    """Return every collected value together with the encodings a message writes it as."""
    forms: set[str] = set()
    for secret in secrets:
        escaped = _encode_pointer_component(secret)
        # Quotes stripped in both derived forms: what is wanted is the content between the
        # renderer's own quotes, which is what a message embeds. ``repr`` chooses its quote
        # character from the content — a value holding "'" is written in double quotes — so the
        # pair is stripped by position rather than by naming one of them.
        forms.update(
            (
                secret,
                escaped,
                json.dumps(escaped, ensure_ascii=True)[1:-1],
                repr(secret)[1:-1],
            )
        )
    # Longest first, matching ``collect_secret_values``, so an encoded form is replaced before
    # any shorter form contained inside it.
    return tuple(sorted(forms, key=lambda form: (-len(form), form)))


def redact_message(text: str, secrets: Sequence[str]) -> str:
    """Remove the known renderings of every collected value from one piece of free text.

    **The one entry point for free text, so a surface that renders it cannot redact it a
    weaker way.** The surfaces that show an operator or a caller text the core built from
    declared content go through here: a finding's message, the refusal the command line logs,
    and the ``message`` of the public boundary error. The expansion is what makes it
    escape-aware, and applying it here rather than at each surface is what stops one surface
    from drifting behind :func:`redact_finding`.

    **It matches renderings; it does not escape at the point of writing, and the difference is
    where its two known limits come from.** What is verified is that the four renderings
    :func:`_message_secret_forms` derives are removed at all seven enumerated exits, on the
    in-bound and the out-bound pass. What is not claimed is that those four are every
    rendering:

    * A parse diagnostic's pointer is escaped by ``_render_context_pointer``, which is a fifth
      escaping this does not derive. A collected value holding ":" together with a '"' or a
      non-ASCII character survives in a parse refusal.
    * ``_bounded_component`` cuts a declared key at 64 characters *before* escaping it, so a
      longer key reaches a message as a prefix of itself. Whole-value matching cannot find a
      prefix, and no matching rule can — this one is not closable here.
    """
    return redact(text, _message_secret_forms(secrets))


# The one never-redacted rule, held here and read by every boundary rather than restated at
# any of them. Each field named here carries a closed vocabulary the core chose rather than
# anything a caller supplied: a finding's ``code`` and ``severity``, and the ``family``,
# ``operation`` and ``outcome`` of a refusal. Redacting one rewrites a stable machine
# identifier into an environment-dependent one, and can produce a value the field's own
# pattern refuses — which turns a returned refusal into a raised validation error at one
# boundary and a corrupted code at the other.
#
# The rule covers a refusal's own fields as well as a finding's. In an environment holding a
# collected value equal to "storage", redacting them makes an error's ``family`` attribute say
# "storage" while the dump of the same error says "***", and makes the command line label the
# refusal "***" — the token both interfaces render for one error meaning, gone
# environment-dependent.
#
# Only a message and a pointer quote declared content. Whatever is not named here is redacted,
# so a field added later is redacted by default rather than exposed by it.
UNREDACTED_VOCABULARY_FIELDS = frozenset({"code", "severity", "family", "operation", "outcome"})


def is_closed_vocabulary_field(field: str) -> bool:
    """Return whether a public field's value is a vocabulary the core chose, not caller data.

    The predicate rather than the set, so a boundary that cannot call
    :func:`redact_public_field` — because it matches its caller-derived text a different way —
    still reads the exemption from here instead of naming the exempt fields again.
    """
    return field in UNREDACTED_VOCABULARY_FIELDS


def redact_public_field(field: str, value: str, secrets: Sequence[str]) -> str:
    """Redact one public field by the rule its *name* selects, wherever the field arrives.

    **The whole rule, in one function, so a finding or a refusal cannot be redacted two ways.**
    A finding reaches a boundary either as the model or as a serialized mapping, and both
    shapes are the same four fields under the same rule; both call this, so the two shapes
    have nothing to disagree about. A refusal's own fields are the identical rule one level
    up: ``family``, ``operation`` and ``outcome`` are closed vocabularies, and ``message`` is
    caller-derived free text.

    **Both caller-derived fields are matched escape-aware, and they have to be by two different
    mechanisms.** A finding message quotes the same declared key its pointer does, escaped by
    the same function — so a plain match over the message lets the credential the pointer
    hardening exists for through on the same output line. The pointer is structure and is taken
    apart (:func:`redact_pointer`); the message is free text and cannot be, so the secret is
    encoded into the forms a message writes it as instead (:func:`redact_message`).

    A field this does not name is free text and is redacted as such, which is what makes
    :data:`UNREDACTED_VOCABULARY_FIELDS` the exception list rather than the rule.
    """
    if is_closed_vocabulary_field(field):
        return value
    if field == "location":
        return redact_pointer(value, secrets)
    return redact_message(value, secrets)


def redact_finding(finding: ValidationFinding, secrets: Sequence[str]) -> ValidationFinding:
    """Return one finding with no current credential value in its caller-derived text.

    Which field gets which rule is :func:`redact_public_field`'s decision, not this function's,
    so the serialized-mapping shape at a boundary applies the identical rule. The fields are
    read off the finding rather than named here, so a string field added to
    :class:`ValidationFinding` later is redacted by default rather than exposed by omission.
    """
    return finding.model_copy(
        update={
            field: redact_public_field(field, value, secrets)
            for field, value in finding.model_dump().items()
            if isinstance(value, str)
        }
    )


def describe(error: ConfigsError, secrets: Sequence[str]) -> str:
    """Return the one operator-facing refusal text both interfaces render, already redacted.

    The family leads, so the vocabulary declared in this module is the vocabulary an operator
    sees at a command line and a caller reads off the public API error.

    **Redacted here, per field, rather than by the caller over the rendered line.** The line is
    a closed vocabulary followed by caller-derived free text, and a match over the whole line
    cannot tell them apart: an environment holding a collected value equal to "not-found"
    turns the label of every not-found refusal into "***". Each half therefore goes through
    :func:`redact_public_field` under its own name, which is the same rule the public error
    dump applies to the same two values.
    """
    family = redact_public_field("family", type(error).family, secrets)
    return f"{family}: {redact_public_field('message', str(error), secrets)}"


@dataclass(frozen=True, slots=True)
class RegisteredConfiguration:
    """A newly registered configuration together with its first version."""

    configuration: ConfigurationSummary
    version: ConfigurationVersion


@dataclass(frozen=True, slots=True)
class RegisteredVersion:
    """One configuration version, and whether this call is what created it."""

    version: ConfigurationVersion
    created: bool


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """Every declared defect in one registered version, already in contract order.

    ``destination_schema_fingerprint`` is the consumed-semantics identity of the
    destination schema snapshot the schema checks judged — ``None`` whenever no snapshot
    was read: the default path, a non-declaring destination, or a failed read. It is what
    makes "same package, same schema snapshot, same report" auditable rather than
    asserted, and it is the same projection a run records on its plan.
    """

    config_id: str
    registry_version: int
    package_checksum: str
    findings: tuple[ValidationFinding, ...]
    destination_schema_fingerprint: str | None = None


@_service_boundary
def load_package_content(path: str | Path) -> dict[str, Any]:
    """Read one declared package file as JSON-native content.

    ``yaml.safe_load`` reads JSON as well as YAML, so one code path serves both file forms.
    Whether the result is a legal package is :func:`parse_configuration_package`'s decision,
    not this function's.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        # UnicodeDecodeError is a ValueError, not an OSError: a package saved as CP1252 rather
        # than UTF-8 opens and reads and then fails on one byte. It is the likeliest of these
        # mistakes to reach an operator, and it is the caller's file either way, so it belongs
        # to the same refusal as a file that cannot be opened at all. The type is what names
        # which of the two happened.
        msg = f"declared package file {str(path)!r} could not be read: {type(exc).__name__}"
        raise ConfigsRequestError(msg) from None
    try:
        content = yaml.safe_load(text)
    except (yaml.YAMLError, RecursionError):
        # RecursionError is not a YAMLError: the parser recurses once per nesting level, so a
        # deeply nested file exhausts the interpreter stack instead of being reported as bad
        # syntax. Both are the same refusal - the caller's file is not readable as a package.
        msg = f"declared package file {str(path)!r} is not valid JSON or YAML"
        raise ConfigsRequestError(msg) from None
    if not isinstance(content, dict):
        msg = f"declared package file {str(path)!r} does not contain a package object"
        raise ConfigsRequestError(msg)
    if not _needs_json_coercion(content):
        return content
    # json round-trip: YAML admits scalars a declared package may not carry. ``default=str``
    # does not hand those to the parse boundary — it stringifies them first, so an unquoted
    # YAML date arrives as the string "2024-01-01" and is accepted wherever a string is legal.
    # Two consequences, both deliberate and both worth knowing about. The stored content is
    # then not byte-for-byte what the operator wrote; and the interfaces diverge, because the
    # API path is handed JSON-native content directly and refuses the same value with
    # ConfigurationPackageParseError. Reading a YAML date as text is conventional, so this
    # coerces rather than refuses — but it is coercion, not a precise report.
    try:
        return json.loads(json.dumps(content, default=str))
    except (TypeError, ValueError):
        # Not every dump failure is coercible, and the probe above cannot tell the difference:
        # it reads *any* refusal as "coercion needed", so the coercion itself is where the rest
        # have to be refused. Two shapes reach here, both legal YAML. An anchor that contains
        # itself describes an infinite structure and raises ValueError("Circular reference
        # detected"); a date used as a mapping key raises TypeError, because ``default=str``
        # stringifies values and not keys. The caller's file is what cannot be read as a
        # package, so this is the same request refusal as unparseable syntax.
        msg = f"declared package file {str(path)!r} describes content JSON cannot represent"
        raise ConfigsRequestError(msg) from None


def _needs_json_coercion(content: Mapping[str, Any]) -> bool:
    """Return whether `content` holds a value the JSON-native parse boundary would reject.

    Any dump failure reads as "coercion needed", including the ones no coercion can fix. The
    caller is where those are refused.
    """
    try:
        json.dumps(content, allow_nan=False)
    except (TypeError, ValueError):
        return True
    return False


def _projection(
    product_cache_location: str | Path | None,
    projection: ProductProjection | None,
) -> ProductProjection:
    """Open the configuration registry, refusing an absent or non-absolute store location.

    Absence is a refusal rather than a fallback: unlike a run, a registry has nowhere to live
    without an explicit store. Only the absoluteness half of the rule is shared with the
    run commands.
    """
    if projection is not None:
        if product_cache_location is not None:
            msg = "provide exactly one product projection or product_cache_location"
            raise ConfigsRequestError(msg)
        return projection
    if product_cache_location is None or not str(product_cache_location).strip():
        msg = "product_cache_location is required: the configuration registry has no store without one"
        raise ConfigsRequestError(msg)
    try:
        location = resolve_product_cache_location(product_cache_location)
    except ProductCacheLocationError as exc:
        raise ConfigsRequestError(str(exc)) from None
    try:
        return local_product_projection(location)
    except ValueError as exc:
        raise ConfigsStorageError(str(exc)) from None
    except OSError as exc:
        # Opening the registry creates the store's own directories, so the filesystem refuses
        # here before any query runs: a file where a directory belongs, or a cache root nothing
        # may write to. That is a storage refusal, not a raw traceback out of the one declared
        # vocabulary both interfaces map. Only the exception type is carried, matching
        # ``load_package_content`` - the errno text names paths the caller already supplied and
        # adds nothing an operator can act on.
        msg = f"product cache location {str(location)!r} could not be opened as a store: {type(exc).__name__}"
        raise ConfigsStorageError(msg) from None


def _standalone_projection(product_cache_location: Path) -> ProductProjection:
    """Open the explicit local compatibility seam for managed route tests."""
    return local_product_projection(product_cache_location)


# The two machine-readable absence values the read operations distinguish.
# ``CONFIGURATION_NOT_FOUND_REASON`` matches the store's own lookup reason for the same
# absence; the version value names the case the store cannot: the configuration exists and
# the requested version does not.
CONFIGURATION_NOT_FOUND_REASON = "configuration-not-found"
CONFIGURATION_VERSION_NOT_FOUND_REASON = "configuration-version-not-found"


def _require_configuration(projection: ProductProjection, config_id: str) -> ConfigurationSummary:
    """Return one registered configuration's summary, refusing absence by name.

    The first step of every scoped read: the store's version queries answer a missing
    configuration and a missing version identically (an empty tuple, or one blended
    not-found reason), so the configuration is resolved first and its absence gets its own
    machine-readable value. Deletion does not exist, so the two-step read cannot race.
    """
    summary = projection.lookup_configuration(config_id).value
    if summary is None:
        msg = f"configuration {config_id!r} is not registered"
        raise ConfigsNotFoundError(msg, reason=CONFIGURATION_NOT_FOUND_REASON)
    return summary


def _require_argument_type(value: object, *, name: str, expected: type) -> None:
    """Refuse a wrong-typed public argument as the caller's own input, before the store sees it.

    An identifier is passed to the registry as a query parameter, so a wrong-typed one reaches
    SQLite and comes back as ``sqlite3.ProgrammingError`` — which the boundary can only read as
    ``storage``, sending an operator to look at their disk for a defect in their own call. Type
    is all this checks: whether a well-typed identifier exists is the store's answer, and a
    missing one is already ``not-found``. The guard reads nothing from the value: it matches
    ``type(value)`` identity alone — not ``isinstance``, which consults the instance's
    ``__class__`` (a read a hostile property executes on) and would admit subclasses — and
    its refusal names nothing about the value either, not its class's ``__name__``, which a
    metaclass executes on. ``expected`` is this module's own trusted type object, so naming
    it is safe.
    """
    if type(value) is not expected:  # pylint: disable=unidiomatic-typecheck  # Exact type; isinstance reads the instance.
        msg = f"{name} must be {expected.__name__}"
        raise ConfigsRequestError(msg)


# The registry's whole allocatable range. Versions are allocated from 1 and stored as
# SQLite INTEGER — a signed 64-bit value — so no stored version can ever exceed this.
_MAX_REGISTRY_VERSION = 2**63 - 1


def _require_registry_version(value: object) -> None:
    """Refuse a registry_version outside the domain the registry can ever allocate.

    Exactly ``int`` — no subclass — and within ``1..=_MAX_REGISTRY_VERSION``, as the
    caller's own input rather than the store's answer, so every integer is classified
    before SQLite sees it. ``bool`` is an ``int`` subclass, so an ``isinstance`` guard lets
    ``registry_version=True`` reach SQLite, compare equal to 1, and silently resolve version
    1; the registry allocates versions from 1, so ``0`` and ``-1`` are not versions that
    happen to be absent — reporting them ``not-found`` claims the store answered a question
    it can never be asked; and the store's INTEGER is signed 64-bit, so an integer above
    the range is equally unaskable, and surfaces as SQLite's own ``OverflowError``.
    """
    if type(value) is not int:  # pylint: disable=unidiomatic-typecheck  # bool must not pass.
        # Fixed message: reading the class's __name__ executes a hostile metaclass.
        msg = "registry_version must be int"
        raise ConfigsRequestError(msg)
    if not 1 <= value <= _MAX_REGISTRY_VERSION:
        msg = f"registry_version must be in the registry's allocatable range 1..{_MAX_REGISTRY_VERSION}, not {value}"
        raise ConfigsRequestError(msg)


def _require_json_native_package(package: object) -> None:
    """Refuse anything but recursively exact JSON-native data, without invoking it.

    The closed acceptance property of the write boundary: exact ``dict``/``list``
    containers with exact ``str``/``int``/``float``/``bool``/``None`` leaves, judged by
    ``type(...)`` identity before any protocol operation runs on the untrusted value — no
    method call, no attribute read, no ``__class__`` consultation, no ``isinstance``
    against an ABC. A subclass of any of these can override every hook the service would
    otherwise invoke, so no subclass is in the domain. The refusal reads nothing either —
    not the class's ``__mro__`` or ``__name__``, both of which a metaclass executes on —
    so one fixed message covers every out-of-domain value, a prebuilt
    :class:`ConfigurationPackage` instance included. The recursive walk is the model
    layer's own :func:`_require_json_native`, so the service's structural acceptance and
    the parse boundary agree by construction; its refusals are value-free (location and
    reason only), so nothing here echoes the object either.
    """
    if type(package) is not dict:  # pylint: disable=unidiomatic-typecheck  # Exact dict only; no subclass.
        msg = "package must be a JSON-native dict of declared content"
        raise ConfigsRequestError(msg)
    try:
        _require_json_native(package)
    except PydanticCustomError as exc:
        msg = f"package must be recursively JSON-native declared content: {exc}"
        raise ConfigsRequestError(msg) from None


def _parse(package: Mapping[str, Any]) -> ConfigurationPackage:
    """Parse declared JSON-native content, requiring exactly that shape before anything else.

    Structural acceptance (:func:`_require_json_native_package`) runs first, so only a
    recursively exact JSON-native ``dict`` ever reaches ``parse_configuration_package`` —
    what the store persists is exactly what the shared parser validated, and a hostile or
    merely non-native value is refused as the caller's own input without being invoked.
    """
    _require_json_native_package(package)
    try:
        return parse_configuration_package(dict(package))
    except ConfigurationPackageParseError as exc:
        raise ConfigsRequestError(str(exc)) from None


def _validation_refusal(exc: CredentialConfigurationError, package: ConfigurationPackage) -> ConfigsValidationError:
    """Turn the store's single-message refusal into the full ordered finding set.

    The store raises on the first defect because registration must reject before it persists.
    Collecting here is what makes the same package report N findings rather than one.
    """
    return ConfigsValidationError(str(exc), findings=collect_findings(package))


@_service_boundary
def register(
    *,
    package: Mapping[str, Any],
    product_cache_location: str | Path | None = None,
    projection: ProductProjection | None = None,
) -> RegisteredConfiguration:
    """Register a brand-new declared configuration and return it with its first version.

    ``package`` is declared JSON-native content; a prebuilt package instance is refused
    (:func:`_parse`). Validation happens inside the store, before anything is persisted, so
    an invalid package raises and is never registered. The findings surface is
    :func:`validate`.
    """
    projection = _projection(product_cache_location, projection)
    parsed = _parse(package)
    try:
        version = projection.create_configuration(parsed)
    except CredentialConfigurationError as exc:
        raise _validation_refusal(exc, parsed) from None
    except DuplicateConfigurationError as exc:
        raise ConfigsStorageError(str(exc)) from None
    summary = projection.lookup_configuration(version.config_id).value
    if summary is None:
        msg = f"configuration {version.config_id!r} was registered but cannot be read back"
        raise ConfigsStorageError(msg)
    return RegisteredConfiguration(configuration=summary, version=version)


@_service_boundary
def create_version(
    *,
    config_id: str,
    package: Mapping[str, Any],
    product_cache_location: str | Path | None = None,
    projection: ProductProjection | None = None,
) -> RegisteredVersion:
    """Add one version to an existing configuration, or return the identical stored one.

    ``package`` is declared JSON-native content; a prebuilt package instance is refused
    (:func:`_parse`).
    """
    _require_argument_type(config_id, name="config_id", expected=str)
    projection = _projection(product_cache_location, projection)
    parsed = _parse(package)
    try:
        version, created = projection.add_configuration_version(config_id, parsed)
    except CredentialConfigurationError as exc:
        raise _validation_refusal(exc, parsed) from None
    except ConfigurationNotFoundError as exc:
        raise ConfigsNotFoundError(str(exc)) from None
    except ConfigurationVersionAllocationError as exc:
        raise ConfigsStorageError(str(exc)) from None
    return RegisteredVersion(version=version, created=created)


@_service_boundary
def list_configs(
    *, product_cache_location: str | Path | None = None, projection: ProductProjection | None = None
) -> tuple[ConfigurationSummary, ...]:
    """Return every registered configuration exactly once, oldest first with an ID tiebreak.

    The order is the store's own ``ORDER BY created_at, config_id`` — deterministic and total,
    never a re-sort in this layer. An empty registry is a real answer here, unlike the scoped
    reads: there is no identifier whose absence could make it a not-found.
    """
    projection = _projection(product_cache_location, projection)
    return projection.list_configurations()


@_service_boundary
def get_config(
    *,
    config_id: str,
    product_cache_location: str | Path | None = None,
    projection: ProductProjection | None = None,
) -> ConfigurationSummary:
    """Return one registered configuration's summary, refusing absence rather than guessing."""
    _require_argument_type(config_id, name="config_id", expected=str)
    projection = _projection(product_cache_location, projection)
    return _require_configuration(projection, config_id)


@_service_boundary
def list_versions(
    *,
    config_id: str,
    product_cache_location: str | Path | None = None,
    projection: ProductProjection | None = None,
) -> tuple[ConfigurationVersion, ...]:
    """Return every version of one configuration, ordered by ``registry_version`` ascending.

    A missing configuration refuses — never a silent empty tuple, which would be
    indistinguishable from a real answer about a registered configuration. The order is the
    store's own ``ORDER BY registry_version``, not a re-sort in this layer.
    """
    _require_argument_type(config_id, name="config_id", expected=str)
    projection = _projection(product_cache_location, projection)
    _require_configuration(projection, config_id)
    return projection.list_configuration_versions(config_id)


@_service_boundary
def get_version(
    *,
    config_id: str,
    registry_version: int,
    product_cache_location: str | Path | None = None,
    projection: ProductProjection | None = None,
) -> ConfigurationVersion:
    """Return one immutable registered version exactly as it was persisted.

    Two lookups, so the two absences stay distinct: the configuration first — its absence
    refuses with :data:`CONFIGURATION_NOT_FOUND_REASON` — then the version, whose absence
    on an existing configuration refuses with
    :data:`CONFIGURATION_VERSION_NOT_FOUND_REASON`. Deletion does not exist, so the two steps
    cannot race. The store's blended single-lookup reason is untouched; ``validate`` keeps it.
    """
    _require_argument_type(config_id, name="config_id", expected=str)
    _require_registry_version(registry_version)
    projection = _projection(product_cache_location, projection)
    _require_configuration(projection, config_id)
    stored = projection.lookup_configuration_version(config_id, registry_version).value
    if stored is None:
        msg = f"configuration {config_id!r} has no registered version {registry_version}"
        raise ConfigsNotFoundError(msg, reason=CONFIGURATION_VERSION_NOT_FOUND_REASON)
    return stored


@_service_boundary
def validate(
    *,
    config_id: str,
    registry_version: int,
    product_cache_location: str | Path | None = None,
    projection: ProductProjection | None = None,
    destination_schema: DestinationSchemaOptions | None = None,
) -> ValidationReport:
    """Report every declared defect in one registered version, in contract order.

    The version is re-read from the registry and validated against the *current* adapter
    declarations, which is why a package that was accepted at registration can report
    findings later.

    ``destination_schema`` is the explicit opt-in for the destination schema checks:
    ``None`` — the default — keeps the declared-content-only behavior byte-identical, with
    zero schema reads and zero network I/O. An explicit options object adds the destination
    schema checks, merges their findings under the same ``sort_findings`` contract, and
    records the judged snapshot's fingerprint.
    """
    _require_argument_type(config_id, name="config_id", expected=str)
    _require_registry_version(registry_version)
    if destination_schema is not None:
        _require_argument_type(destination_schema, name="destination_schema", expected=DestinationSchemaOptions)
    projection = _projection(product_cache_location, projection)
    lookup = projection.lookup_configuration_version(config_id, registry_version)
    stored = lookup.value
    if stored is None:
        msg = f"configuration {config_id!r} has no registered version {registry_version} ({lookup.reason})"
        raise ConfigsNotFoundError(msg)
    parsed = _parse(stored.declared_content)
    findings = collect_findings(parsed)
    fingerprint: str | None = None
    if destination_schema is not None:
        schema_validation = collect_destination_schema_findings(parsed)
        findings = sort_findings((*findings, *schema_validation.findings))
        fingerprint = schema_validation.schema_fingerprint
    return ValidationReport(
        config_id=stored.config_id,
        registry_version=stored.registry_version,
        package_checksum=stored.package_checksum,
        findings=findings,
        destination_schema_fingerprint=fingerprint,
    )
