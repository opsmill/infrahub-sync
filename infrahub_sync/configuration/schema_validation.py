"""Destination schema validation — the explicit opt-in checks outside the declared-content core.

The declared-content core (``validation.py``) judges declared content only and stays
untouched; this module owns the schema-path checks and their finding codes. Four error
families live here, each behind its own ``_CODE_`` constant and frozen by this module's
own exact-set and reachability tests:

* the destination-schema-mismatch family — exactly four predicates judged against the
  destination schema snapshot: a mapping kind the snapshot does not declare, a field name
  that is neither an attribute nor a relationship of its kind, a relationship reference on
  a name the schema declares as an attribute, and a declared static value whose shape
  (list versus scalar) disagrees with the relationship's declared cardinality — plus the
  capability gate for an explicit request against a destination that does not declare
  schema validation (a missing capability needed to determine safety is an error). Two
  further checkable predicates are out of scope here: a static value's type against the
  attribute's declared kind, and a reference against the relationship's declared peer kind;
* the unsupported-destination-write family — the operations one configuration requests,
  derived through the one shared SYNC-78 effective-operation rule, judged against the
  destination's declared write operations;
* the closed-domain family — a snapshot the accessor delivered but the shared runtime
  schema domain refuses. Registered execution refuses the same snapshot, so validation
  reports the same defect rather than silently withholding the fingerprint;
* the Dropdown literal family (spec 019 FR-034) — a literal ``static`` value of a mapped
  Dropdown attribute that is not one of the attribute's choices;
* the destination-access family (spec 019 FR-036) — a permission the import account lacks on
  the branch to view, create or update a mapped kind, or to look up a named ``source`` or
  ``owner``, and a named ``source`` or ``owner`` that matches no node or more than one node;
* the schema-read-failure family — a schema read the accessor reports as failed becomes a
  typed error finding rather than a generic service-boundary refusal. The bundled accessor
  classifies SDK-raised errors, HTTP transport and status failures, an unresolvable declared
  credential, and a declared client configuration the SDK refuses; an exception outside the
  accessor contract is not intercepted here.

Checks accumulate: a failed check never suppresses an independent one, and an unevaluable
subtree — an unknown destination adapter, an unknown kind, an unknown field — suppresses
only its own deeper checks, mirroring the core's rule. Nothing here runs on the default
``validate`` path: only the explicit opt-in reaches this module, and the accessor is the only
thing that may perform I/O.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from infrahub_sync import requested_destination_write_operations
from infrahub_sync.generator import get_identifiers
from infrahub_sync.plan.keying import writable_convergence_reason
from infrahub_sync.runtime_schema import (
    UnsupportedSchemaSemanticsError,
    compute_consumed_schema_fingerprint,
    normalize_destination_schema,
)

from .capabilities import BUILTIN_ADAPTER_CAPABILITIES, DestinationAccessAccessor, DestinationSchemaReadError
from .models import ValidationFinding, sort_findings
from .runtime import effective_destination_branch
from .validation import _finding_message

if TYPE_CHECKING:
    from infrahub_sdk.schema import NodeSchema

    from infrahub_sync.runtime_schema import DestinationSchemaSnapshot

    from .models import ConfigurationPackage

_CODE_DESTINATION_SCHEMA_MISMATCH = "destination-schema-mismatch"
_CODE_DESTINATION_SCHEMA_READ_FAILED = "destination-schema-read-failed"
_CODE_DESTINATION_SCHEMA_UNSUPPORTED_SEMANTICS = "destination-schema-unsupported-semantics"
_CODE_DESTINATION_SCHEMA_VALIDATION_UNSUPPORTED = "destination-schema-validation-unsupported"
_CODE_UNSUPPORTED_DESTINATION_WRITE = "unsupported-destination-write"
# Spec 019 FR-034: a literal Dropdown value outside the attribute's choices.
_CODE_DESTINATION_DROPDOWN_VALUE_NOT_IN_CHOICES = "destination-dropdown-value-not-in-choices"
# Spec 019 FR-036: what the import account may do on the branch, and the source or owner it names.
_CODE_DESTINATION_PERMISSION_MISSING = "destination-permission-missing"
_CODE_DESTINATION_LINEAGE_NODE_NOT_FOUND = "destination-lineage-node-not-found"
_CODE_DESTINATION_LINEAGE_NODE_AMBIGUOUS = "destination-lineage-node-ambiguous"

_DESTINATION_LOCATION = "/configuration/destination"
_CARDINALITIES = frozenset({"one", "many"})


@dataclass(frozen=True, slots=True)
class DestinationSchemaOptions:
    """Explicit request for destination schema validation on ``validate``.

    Presence is the opt-in: ``None`` on the operation keeps today's declared-content-only
    behavior byte-identical, with zero schema reads. ``branch`` is the Infrahub branch the
    team names (`configs validate --branch`, spec 019 FR-034); ``None`` reads the package's
    declared destination branch.
    """

    branch: str | None = None


@dataclass(frozen=True, slots=True)
class DestinationSchemaValidation:
    """Every schema-path finding for one package, with the judged snapshot's identity.

    ``schema_fingerprint`` is the consumed-semantics identity of the snapshot the content
    checks actually judged — ``None`` whenever no snapshot was read: a non-declaring
    destination, an unknown adapter, or a failed read. Validation and registered worker
    construction compute it through the same projection; recording it on a plan, and
    comparing it before an apply writes, belong to the plan-guard unit that follows.
    """

    findings: tuple[ValidationFinding, ...]
    schema_fingerprint: str | None


def resolve_declared_destination_branch(package: ConfigurationPackage) -> str:
    """Resolve the destination branch validation reads, through the shared rule.

    Validation judges declared content and has no run request, so it passes no run
    branch: the result is the declared branch or ``"main"``. Execution calls the same
    resolver with the run's branch.
    """
    return effective_destination_branch(package.configuration.destination.settings, None)


def _finding(*, code: str, location: str, message: str) -> ValidationFinding:
    """Build one error finding under the core's own message bound.

    The locations this module writes are built from fixed literals and list indices —
    never from declared keys — so only the message needs bounding.
    """
    return ValidationFinding(code=code, severity="error", location=location, message=_finding_message(message))


def _schema_content_findings(
    package: ConfigurationPackage,
    snapshot: Mapping[str, Any],
) -> list[ValidationFinding]:
    """Judge every declared schema mapping against the snapshot, accumulating.

    An unknown kind makes its mapping entry unevaluable, so its fields are judged nowhere
    deeper; an unknown field ends that field's own checks the same way. Entries whose
    snapshot shape is not a mapping are treated as undeclared, never crashed on.
    """
    findings: list[ValidationFinding] = []
    for index, mapping in enumerate(package.configuration.schema_mapping):
        entry = snapshot.get(mapping.name)
        if not isinstance(entry, Mapping):
            findings.append(
                _finding(
                    code=_CODE_DESTINATION_SCHEMA_MISMATCH,
                    location=f"/configuration/schema_mapping/{index}/name",
                    message=f"destination schema declares no kind {mapping.name!r}",
                )
            )
            continue
        raw_attributes = entry.get("attributes")
        raw_relationships = entry.get("relationships")
        attributes: Mapping[str, Any] = raw_attributes if isinstance(raw_attributes, Mapping) else {}
        relationships: Mapping[str, Any] = raw_relationships if isinstance(raw_relationships, Mapping) else {}
        for field_index, field in enumerate(mapping.fields or ()):
            field_location = f"/configuration/schema_mapping/{index}/fields/{field_index}"
            relationship = relationships.get(field.name)
            if field.name not in attributes and relationship is None:
                findings.append(
                    _finding(
                        code=_CODE_DESTINATION_SCHEMA_MISMATCH,
                        location=f"{field_location}/name",
                        message=(
                            f"destination kind {mapping.name!r} declares no attribute or relationship {field.name!r}"
                        ),
                    )
                )
                continue
            if field.reference is not None and relationship is None:
                findings.append(
                    _finding(
                        code=_CODE_DESTINATION_SCHEMA_MISMATCH,
                        location=f"{field_location}/reference",
                        message=(
                            f"field {field.name!r} declares a relationship reference, but destination "
                            f"kind {mapping.name!r} declares an attribute"
                        ),
                    )
                )
                continue
            if relationship is None or field.static is None:
                continue
            cardinality = relationship.get("cardinality") if isinstance(relationship, Mapping) else None
            declared_many = isinstance(field.static, (list, tuple))
            if cardinality in _CARDINALITIES and declared_many != (cardinality == "many"):
                shape = "list" if declared_many else "single"
                findings.append(
                    _finding(
                        code=_CODE_DESTINATION_SCHEMA_MISMATCH,
                        location=f"{field_location}/static",
                        message=(
                            f"field {field.name!r} declares a {shape} static value, but the "
                            f"destination relationship has cardinality {cardinality!r}"
                        ),
                    )
                )
    return findings


def _identity_findings(package: ConfigurationPackage, normalized: DestinationSchemaSnapshot) -> list[ValidationFinding]:
    """Report each mapped kind whose selected identity cannot key a create (spec 019 FR-037).

    The plan keys each create on the fields the generator selects as identifiers
    (`get_identifiers`: the mapping's `identifiers`, or else its mapped unique attributes),
    and refuses a create that has no complete writable destination key. Validation judges
    the same fields through the same rule, so the two give one answer (research R16).
    """
    references = {
        mapping.name: {field.name: field.reference for field in mapping.fields or () if field.reference is not None}
        for mapping in package.configuration.schema_mapping
    }
    findings: list[ValidationFinding] = []
    for index, mapping in enumerate(package.configuration.schema_mapping):
        node = normalized.kinds.get(mapping.name)
        if node is None:
            continue
        mapped_fields = {field.name for field in mapping.fields or ()}
        identifiers = get_identifiers(node=cast("NodeSchema", node), config=package.configuration) or ()
        reason = writable_convergence_reason(
            node=node,
            identity_fields={field for field in identifiers if field in mapped_fields},
            mapped_fields=mapped_fields,
            schemas=normalized.kinds,
            references=references,
        )
        if reason:
            findings.append(
                _finding(
                    code=_CODE_DESTINATION_SCHEMA_MISMATCH,
                    location=f"/configuration/schema_mapping/{index}/identifiers",
                    message=f"destination kind {mapping.name!r}: {reason}",
                )
            )
    return findings


def _dropdown_findings(package: ConfigurationPackage, normalized: DestinationSchemaSnapshot) -> list[ValidationFinding]:
    """Report each literal ``static`` value of a mapped Dropdown attribute that is not a choice.

    A value mapped from the source is checked by the plan, record by record (spec 019
    research R4). A Dropdown whose choices the snapshot does not record is not judged. A kind
    or field the snapshot does not declare is reported by the mismatch checks instead.
    """
    findings: list[ValidationFinding] = []
    for index, mapping in enumerate(package.configuration.schema_mapping):
        node = normalized.kinds.get(mapping.name)
        if node is None:
            continue
        choices_by_name = {attribute.name: attribute.choices for attribute in node.attributes}
        for field_index, field in enumerate(mapping.fields or ()):
            choices = choices_by_name.get(field.name)
            if choices is None or not isinstance(field.static, str) or field.static in choices:
                continue
            findings.append(
                _finding(
                    code=_CODE_DESTINATION_DROPDOWN_VALUE_NOT_IN_CHOICES,
                    location=f"/configuration/schema_mapping/{index}/fields/{field_index}/static",
                    message=(
                        f"destination kind {mapping.name!r}: {field.static!r} is not one of the choices of the "
                        f"Dropdown attribute {field.name!r}: {', '.join(choices)}"
                    ),
                )
            )
    return findings


def _access_findings(
    package: ConfigurationPackage, accessor: DestinationAccessAccessor | None, branch: str
) -> list[ValidationFinding]:
    """Report the permissions the import account lacks on `branch` and the lineage names it cannot use.

    Spec 019 FR-036: one finding per mapped kind the account cannot view, create or update,
    and one per named `source` or `owner` that the account cannot look up, that matches no
    node, or that matches more than one node.
    """
    if accessor is None:
        return []
    try:
        access = accessor(package, branch)
    except DestinationSchemaReadError as exc:
        return [
            _finding(
                code=_CODE_DESTINATION_SCHEMA_READ_FAILED,
                location=_DESTINATION_LOCATION,
                message=f"destination access for branch {branch!r} could not be read: {exc.describe()}",
            )
        ]
    findings = [
        _finding(
            code=_CODE_DESTINATION_PERMISSION_MISSING,
            location=f"/configuration/schema_mapping/{index}/name",
            message=(
                f"the import account lacks {', '.join(access.missing_by_kind[mapping.name])} on branch "
                f"{branch!r}, which writing kind {mapping.name!r} needs"
            ),
        )
        for index, mapping in enumerate(package.configuration.schema_mapping)
        if mapping.name in access.missing_by_kind
    ]
    for check in access.lineage:
        location = f"{_DESTINATION_LOCATION}/settings/{check.setting}"
        named = f"the destination setting {check.setting} names {check.kind} {check.name!r}"
        if check.outcome == "permission_missing":
            code = _CODE_DESTINATION_PERMISSION_MISSING
            message = (
                f"the import account lacks {', '.join(check.missing)} on branch {branch!r}, which looking up "
                f"the {check.setting} {check.kind} {check.name!r} needs"
            )
        elif check.outcome == "ambiguous":
            code = _CODE_DESTINATION_LINEAGE_NODE_AMBIGUOUS
            message = f"{named}, which matches more than one node on branch {branch!r}"
        elif check.outcome == "kind_missing":
            code = _CODE_DESTINATION_LINEAGE_NODE_NOT_FOUND
            message = f"{named}, and the schema of branch {branch!r} has no kind {check.kind}"
        else:
            code = _CODE_DESTINATION_LINEAGE_NODE_NOT_FOUND
            message = f"{named}, which branch {branch!r} does not hold"
        findings.append(_finding(code=code, location=location, message=message))
    return findings


def collect_destination_schema_findings(
    package: ConfigurationPackage, options: DestinationSchemaOptions | None = None
) -> DestinationSchemaValidation:
    """Run every destination schema check for one explicit opt-in request.

    Accumulating and total over declared defects: a non-declaring destination, a failed
    schema read, and an unsupported write request each become findings, never raises. An
    unknown destination adapter adds nothing here — the core's own finding already names
    that defect and its subtree is unevaluable — and checks independent of a failed read
    still report. Findings return in the stable ``sort_findings`` order.

    The branch read is ``options.branch`` when given (spec 019 FR-034), else the package's
    declared destination branch.
    """
    destination = package.configuration.destination
    capabilities = BUILTIN_ADAPTER_CAPABILITIES.get(destination.name)
    if capabilities is None:
        return DestinationSchemaValidation(findings=(), schema_fingerprint=None)
    findings: list[ValidationFinding] = []
    requested = requested_destination_write_operations(package.configuration.diffsync_flags)
    unsupported = requested - capabilities.supported_destination_write_operations
    if unsupported:
        rendered = ", ".join(sorted(unsupported))
        findings.append(
            _finding(
                code=_CODE_UNSUPPORTED_DESTINATION_WRITE,
                location=_DESTINATION_LOCATION,
                message=(
                    f"adapter {destination.name!r} does not support requested destination write operations: {rendered}"
                ),
            )
        )
    fingerprint: str | None = None
    # The registration-time invariant on the seam makes the accessor and the declaration
    # one fact, so presence of the accessor is the declaration.
    accessor = capabilities.destination_schema_accessor
    if accessor is None:
        findings.append(
            _finding(
                code=_CODE_DESTINATION_SCHEMA_VALIDATION_UNSUPPORTED,
                location=_DESTINATION_LOCATION,
                message=f"adapter {destination.name!r} does not declare destination schema validation",
            )
        )
    else:
        branch = (
            options.branch if options is not None and options.branch else resolve_declared_destination_branch(package)
        )
        try:
            snapshot = accessor(package, branch)
        except DestinationSchemaReadError as exc:
            findings.append(
                _finding(
                    code=_CODE_DESTINATION_SCHEMA_READ_FAILED,
                    location=_DESTINATION_LOCATION,
                    message=f"destination schema for branch {branch!r} could not be read: {exc.describe()}",
                )
            )
        else:
            try:
                normalized = normalize_destination_schema(snapshot)
                fingerprint = compute_consumed_schema_fingerprint(
                    configuration=package.configuration, snapshot=normalized
                )
                findings.extend(_identity_findings(package, normalized))
                findings.extend(_dropdown_findings(package, normalized))
            except UnsupportedSchemaSemanticsError:
                # The worker refuses this snapshot too, so validation names the same
                # defect rather than reporting only a missing fingerprint.
                findings.append(
                    _finding(
                        code=_CODE_DESTINATION_SCHEMA_UNSUPPORTED_SEMANTICS,
                        location=_DESTINATION_LOCATION,
                        message=(
                            f"destination schema for branch {branch!r} declares semantics outside the "
                            "supported schema domain"
                        ),
                    )
                )
            findings.extend(_schema_content_findings(package, snapshot))
            findings.extend(_access_findings(package, capabilities.destination_access_accessor, branch))
    return DestinationSchemaValidation(findings=sort_findings(findings), schema_fingerprint=fingerprint)
