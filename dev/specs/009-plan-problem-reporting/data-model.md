# Data model: Plan problem reporting

Field names are the names the Sync API returns and the plan report stores. Every text field
passes through redaction (research R11) before it is stored, returned or printed.

## Problem type (stable names, FR-024)

| Name | Detected when | Records named in `other_records` |
| --- | --- | --- |
| `dropdown_value_not_in_choices` | A mapped value of a Dropdown attribute is not one of the attribute's choices in the destination schema the plan read | none |
| `duplicate_source_identity` | Two or more source records of one kind have the same source identity | every other record with that identity |
| `destination_identity_collision` | Two or more creates would form the same Infrahub human-friendly ID | every other record in the group |
| `wrong_value_type` | A value does not fit the destination attribute's type (for example `2.5` for a Number) | none |
| `required_peer_excluded_by_filter` | A required relationship's peer was removed by a configured filter | none; `peer` names the excluded peer |
| `identities_merge_in_destination` | Two or more source objects with different mapping identities would be stored as one Infrahub node | every other record in the group |

The names are part of the public contract. A new type may be added in a later release; an
existing name is never renamed or removed.

## Source record reference

Names one source record in every entry below.

| Field | Type | Rule |
| --- | --- | --- |
| `kind` | string | The destination kind of the mapping entry |
| `identity` | object | The record's source identity: its `identifiers` fields and values, as the plan operations show identity. For a record that failed model construction, built from the raw data of the identifier fields |
| `source_id` | string or null | The adapter's record ID (`local_id`), when the adapter has one |

## Plan problem (FR-003)

| Field | Type | Rule |
| --- | --- | --- |
| `problem_type` | string | One of the names above |
| `record` | Source record reference | The record with the problem |
| `field` | string or null | The attribute or relationship involved |
| `value` | JSON value or null | The value involved, after redaction; strings bounded to 256 characters |
| `allowed` | list of strings or null | For `dropdown_value_not_in_choices`, the attribute's choices |
| `peer` | Source record reference or null | For `required_peer_excluded_by_filter`, the excluded peer |
| `other_records` | list of Source record reference | For the identity types, at least one entry |
| `reason` | string | A sentence naming the field and the value or peer, at most 512 characters |

A record with several problems has one entry per problem and counts once as left out.

## Left-out record (FR-005, FR-006)

| Field | Type | Rule |
| --- | --- | --- |
| `record` | Source record reference | |
| `cause` | `has_problem` or `depends_on_left_out` | |
| `problem_types` | list of string | When `cause` is `has_problem` |
| `depends_on` | Source record reference or null | When `cause` is `depends_on_left_out`: the left-out record this one refers to |
| `relationship` | string or null | The required relationship through which it depends |

## Kept record (FR-005, FR-023)

| Field | Type | Rule |
| --- | --- | --- |
| `record` | Source record reference | |
| `relationship` | string | The optional relationship written without its value |
| `left_out_peer` | Source record reference | The left-out record the relationship refers to |

## Plan summary additions (FR-007)

Added to the existing summary (`PlanSummaryResource`, `client/models.py:315-322`):

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
`problem_type`, `kind`, canonical identity; then left-out records by `kind`, identity; then kept
records by `kind`, identity, `relationship`. The order is stable, so pages hold each entry once
(spec 011 FR-016).

Manifest addition, `report` object: `digest` (SHA-256 hex), `size` (bytes), `problems`,
`left_out`, `left_out_with_problem`, `left_out_dependent`, `kept`. `format_version` is 4.

Validation: `report.left_out` equals the number of `left_out` lines; the digest matches the
file. A mismatch is a `PlanVerificationError` (the plan is not applied).

## Run failure cause (FR-014 to FR-020, FR-025)

Stored in the run's `{stage}_failure` result (where `_failure_evidence` writes today,
`service/flow.py:697-736`) and returned as `PublicRunResource.failure`.

| Field | Type | Rule |
| --- | --- | --- |
| `stage` | `plan`, `verify`, `apply`, `sync` | The values of `Operation` (`client/models.py:11`) |
| `failure_type` | string | One of the failure types below |
| `system` | object or null | `{"role": "source" or "destination", "adapter": <adapter name>}` |
| `error_code` | string or null | Infrahub's `extensions.code`, at most 128 characters |
| `node_kind` | string or null | At most 128 characters |
| `attribute` | string or null | At most 128 characters |
| `message` | string | Redacted, control characters removed, at most 1,024 characters |
| `error_class` | string | The class name recorded today (`error_type`), kept for scripts |
| `apply_progress` | Apply progress or null | For a stopped apply |
| `next_action` | string | What the team does next, for example "Create and review a new plan" |

Failure types (stable names): `destination_rejected_write`, `system_unreachable`,
`system_timeout`, `token_rejected`, `tls_certificate_rejected`, `credential_missing`,
`plan_failed`, `verification_failed`, `apply_refused`, `left_out_review_required`,
`internal_error`.

## Apply progress (FR-020)

| Field | Type | Rule |
| --- | --- | --- |
| `operations_total` | integer | Operations in the plan |
| `operations_written` | integer | From `ApplyRecord.applied_operations` |
| `written_operation_ids` | list of string | Returned in pages by the results route when large |
| `failed_operation` | object or null | `{"operation_id", "action", "kind", "identity"}` |
| `failed_operation_may_have_written` | boolean or null | From `ApplyRecord.failed_operation_wrote` (`plan/models.py:497-583`) |

## Apply request addition (FR-010, FR-026)

`ApplyRunRequest` (`client/models.py:77-83`) gains `confirmed_left_out_count: int | None`, with
`ge=0`. Rule: when the plan's `report.left_out` is greater than zero, the value must equal it;
when it is zero, the field may be absent or zero.

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
