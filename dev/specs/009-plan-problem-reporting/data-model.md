# Data model: Plan problem reporting

Field names are the names the Sync API returns and the plan report stores. No field is built
from a connection setting or from the text of an error (research R13). Text fields come from
source data, schema names, Infrahub's own error message, or fixed templates.

## Problem type (stable names, FR-024)

| Name | Found when | Records named in `other_records` |
| --- | --- | --- |
| `dropdown_value_not_in_choices` | A mapped value of a Dropdown attribute is not one of the attribute's choices in the destination schema the plan read | none |
| `duplicate_source_identity` | Two or more source records of one kind have the same source identity | every other record with that identity |
| `destination_identity_collision` | Two or more creates would form the same Infrahub human-friendly ID | every other record in the group |
| `wrong_value_type` | The model refuses a value because of its type (for example `2.5` for a Number) | none |
| `required_peer_excluded_by_filter` | A required relationship's peer was removed by a configured filter | none; `peer` names the excluded peer |
| `identities_merge_in_destination` | Two or more source objects with different mapping identities would be stored as one Infrahub node | every other record in the group |
| `peer_not_found_in_source` | A source record refers to a peer that is not in the source at all: not loaded, not in the load report, not removed by a filter (research R4) | none; `peer` names the missing peer by kind and source ID |

The names are part of the public contract. A later release may add a type; an existing name is
never renamed or removed.

## Load report (internal, one per adapter)

Written by the admission step of the adapter base class (research R3). Not stored in the plan;
the problem pass reads it.

| Field | Type | Rule |
| --- | --- | --- |
| `kind` | string | The model name |
| `source_id` | string or null | `local_id` from the record data, when present |
| `identity` | object or null | The identifier fields and values, when every identifier field was converted |
| `reason` | `wrong_value_type`, `duplicate_source_identity`, `required_peer_excluded_by_filter`, `peer_not_found_in_source`, `peer_not_loaded` | |
| `field` | string or null | From the pydantic error location or the typed peer exception |
| `error_type` | string or null | The pydantic error type, for example `int_from_float` |
| `value` | JSON value or null | The value the model refused (pydantic `input`), strings limited to 256 characters |
| `peer` | Source record reference or null | For the peer reasons; for `peer_not_found_in_source`, `identity` is null |
| `loaded` | boolean | False when the record was not built. True for `peer_not_found_in_source` through an optional relationship: the record loads with a placeholder reference |
| `existing` | Source record reference or null | For `duplicate_source_identity`, the record kept in the store |

## Source record reference

| Field | Type | Rule |
| --- | --- | --- |
| `kind` | string | The destination kind of the mapping entry |
| `identity` | object or null | The record's source identity, as the plan operations show identity; null when the record could not be converted far enough to compute it |
| `source_id` | string or null | The adapter's record ID (`local_id`) |

## Plan problem (FR-003)

| Field | Type | Rule |
| --- | --- | --- |
| `problem_type` | string | One of the names above |
| `record` | Source record reference | The record with the problem |
| `field` | string or null | The attribute or relationship involved |
| `value` | JSON value or null | The source value involved; strings limited to 256 characters |
| `allowed` | list of strings or null | For `dropdown_value_not_in_choices`, the attribute's choices |
| `peer` | Source record reference or null | For `required_peer_excluded_by_filter`, the excluded peer. For `peer_not_found_in_source`, the missing peer, with `identity` null |
| `other_records` | list of Source record reference | For the identity types, at least one entry |
| `reason` | string | Rendered from a fixed template per problem type over the fields above; at most 512 characters |

A record with several problems has one entry per problem and counts once as left out.

A record whose only problem is `peer_not_found_in_source` through an optional relationship is
not left out. It has a problem entry and a kept entry, and is not counted in `left_out`
(FR-005).

## Left-out record (FR-005, FR-006)

| Field | Type | Rule |
| --- | --- | --- |
| `record` | Source record reference | |
| `cause` | `has_problem`, `depends_on_left_out` | |
| `problem_types` | list of string | When `cause` is `has_problem` |
| `depends_on` | Source record reference or null | When `cause` is `depends_on_left_out` |
| `relationship` | string or null | The relationship through which it depends |
| `loaded` | boolean | False when the load could not build the record (it is then absent from the comparison) |

## Kept record (FR-005, FR-023)

| Field | Type | Rule |
| --- | --- | --- |
| `record` | Source record reference | |
| `cause` | `peer_left_out`, `peer_not_found_in_source` | Why the relationship is not written |
| `relationship` | string | The optional relationship written without the peer |
| `left_out_peer` | Source record reference | The left-out record, or for `peer_not_found_in_source` the missing peer, with `identity` null |

## Plan summary additions (FR-007)

Added to `PlanSummaryResource` (`client/models.py:315`):

| Field | Type | Rule |
| --- | --- | --- |
| `left_out` | integer ≥ 0 | Records left out, each counted once |
| `left_out_with_problem` | integer ≥ 0 | |
| `left_out_dependent` | integer ≥ 0 | `left_out_with_problem + left_out_dependent = left_out` |
| `left_out_by_kind` | object kind → integer | Sums to `left_out` |
| `problems` | integer ≥ 0 | Problem entries |
| `problems_by_type` | object problem type → integer | |
| `kept` | integer ≥ 0 | Kept-record entries |

## Plan report (saved plan format 4)

`plan/report.jsonl`: one JSON object per line, canonical encoding (ADR 0001), each with an
`entry` field `problem`, `left_out` or `kept` and the fields above. Order: problems by
`problem_type`, `kind`, canonical identity, `source_id`; then left-out records by `kind`,
identity, `source_id`; then kept records by `kind`, identity, `relationship`. The order is
stable, so pages hold each entry once (spec 011 FR-016).

Manifest addition, `report` object: `digest` (SHA-256 hex), `size` (bytes), `problems`,
`left_out`, `left_out_with_problem`, `left_out_dependent`, `kept`. `format_version` is 4.

Validation: `report.left_out` equals the number of `left_out` lines, and the digest matches the
file. A mismatch is a `PlanVerificationError`; the plan is not applied.

## Plan step invariants (research R2)

| Step | Reads | Writes |
| --- | --- | --- |
| Load | source systems | the two stores, the load reports |
| Compare (`diff_from`) | the two stores | the diff |
| Derive | the diff, the stores, the load reports | operations, with placeholders for references to records not loaded and to peers not in the source |
| Find problems | load reports, source store, destination schema record, operations | plan report |
| Select operations | operations, plan report | selected operations, no placeholder |
| Check and write | selected operations, plan report | plan directory |

No step after Load writes to a store. No step edits the diff.

## Run failure cause (FR-014 to FR-020, FR-025)

Built by `build_failure_cause` (`infrahub_sync/failure_cause.py`), called only from
`_failure_evidence` (`service/flow.py:697`). Stored in the run's `{stage}_failure` result and
returned as `PublicRunResource.failure`.

| Field | Type | Rule |
| --- | --- | --- |
| `stage` | `plan`, `verify`, `apply`, `sync` | The values of `Operation` (`client/models.py:11`) |
| `failure_type` | string | One of the failure types below |
| `system` | object or null | `{"role": "source" or "destination", "adapter": <adapter name>}` |
| `error_code` | string or null | Infrahub's `extensions.code`, at most 128 characters |
| `node_kind` | string or null | At most 128 characters |
| `attribute` | string or null | At most 128 characters |
| `record` | Source record reference or null | From a typed Sync exception |
| `field` | string or null | From a typed Sync exception |
| `infrahub_message` | string or null | Only for `destination_rejected_write`: Infrahub's error `message`, control characters removed, at most 1,024 characters |
| `summary` | string | One sentence from a fixed template per failure type over the fields above |
| `error_class` | string | The class name recorded today (`error_type`), kept for scripts |
| `apply_progress` | Apply progress or null | For a stopped apply |
| `next_action` | string | Fixed text per failure type, for example "Fix the cause, then create and review a new plan" |

No field holds the text of an exception, an address, a header or a traceback.

Failure types (stable names): `destination_rejected_write`, `system_unreachable`,
`system_timeout`, `token_rejected`, `tls_certificate_rejected`, `record_load_failed`,
`transform_failed`, `credential_missing`, `verification_failed`, `apply_refused`,
`left_out_review_required`, `internal_error`.

## Apply progress (FR-020)

| Field | Type | Rule |
| --- | --- | --- |
| `operations_total` | integer | Operations in the plan |
| `operations_written` | integer | From `ApplyRecord.applied_operations` |
| `failed_operation` | object or null | `{"operation_id", "action", "kind", "identity"}` |
| `failed_operation_may_have_written` | boolean or null | From `ApplyRecord.failed_operation_wrote` (`plan/models.py:520`) |

The written operation IDs are returned by `GET /runs/{run_id}/results` under `apply_failure`,
as today.

## Apply request addition (FR-010, FR-026)

`ApplyRunRequest` (`client/models.py:77`) gains `confirmed_left_out_count: int | None`, `ge=0`.
When the plan's `report.left_out` is greater than zero, the value must equal it. When it is
zero, the field may be absent or zero.

## Run states for a `sync` run that stops after its plan (FR-012)

| From | Event | To |
| --- | --- | --- |
| `sync` run, phase `planning` | Plan finishes with `left_out` > 0 | phase `planned`, outcome `left-out-review-required`; no write admitted |
| `planned` with `left-out-review-required` | Apply request with the right count | apply runs as for a `plan` run |
| `planned` with `left-out-review-required` | Apply request with no count or a wrong count | refused, state unchanged |

## Worker environment record (FR-033)

| Field | Type | Rule |
| --- | --- | --- |
| `worker_id` | string | The worker's identity |
| `credential_names` | list of string | Names of set, non-empty `INFRAHUB_SYNC_CREDENTIAL_*` variables; never values |
| `observed_at` | timestamp | Refreshed on each poll |
