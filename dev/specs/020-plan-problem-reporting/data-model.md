# Data model: Plan problem reporting

Field names are the names the Sync API returns and the plan report stores. No field is built from
a connection setting or from the text of an error ([research R12, R13](research.md)). Text fields
come from source data, schema names, Infrahub's own message, or fixed templates.

## Problem type (stable names, FR-002, FR-025)

| Name | Found when | Extra fields |
| --- | --- | --- |
| `dropdown_value_not_in_choices` | A mapped value of a Dropdown attribute is not one of its choices in the destination schema the plan read | `allowed` |
| `duplicate_source_identity` | Two or more source records of one kind have the same source identity | `other_records` |
| `destination_identity_collision` | Two or more creates would form the same Infrahub human-friendly ID | `other_records` |
| `wrong_value_type` | The model refuses a value because of its type | `value` |
| `required_peer_excluded_by_filter` | A configured filter removed a required relationship's peer | `peer`, `config_entry` (`filters[0]`) when known |
| `identities_merge_in_destination` | Two or more source records with different mapping identities would be one Infrahub node | `other_records` |
| `peer_not_found_in_source` | A record refers to a peer that is not in the source at all | `peer` (kind and source ID, identity null) |
| `destination_identity_ambiguous` | Two or more Infrahub nodes of one kind hold the same configured identity, on a full read | `destination_ids` |
| `transform_failed` | A configured transform fails for one record | `config_entry` (`transforms[0]`), `value` null |

A later release may add a name. An existing name is never renamed or removed.

## Source record reference

| Field | Type | Rule |
| --- | --- | --- |
| `kind` | string | The destination kind of the mapping entry |
| `identity` | object or null | The source identity, as plan operations show it; null when it cannot be computed |
| `source_id` | string or null | The adapter's record ID (`local_id`) |

## Plan problem (FR-003)

| Field | Type | Rule |
| --- | --- | --- |
| `problem_type` | string | One of the names above |
| `record` | Source record reference | The record with the problem |
| `field` | string or null | The attribute or relationship involved |
| `value` | JSON value or null | The source value; strings at most 256 characters; null for `transform_failed` |
| `allowed` | list of strings or null | Dropdown choices |
| `peer` | Source record reference or null | Excluded or missing peer |
| `other_records` | list of Source record reference | At least one entry for the identity types |
| `destination_ids` | list of strings, absent otherwise | Infrahub node IDs |
| `config_entry` | string, absent otherwise | Position of the transform or filter, never its expression |
| `reason` | string | Fixed template per type over the fields above; at most 512 characters |

A record with several problems has one entry per problem and counts once as left out. A record
whose only problem is `peer_not_found_in_source` through an optional relationship has a problem
entry and a kept entry, and is not left out.

## Left-out record (FR-005, FR-006)

| Field | Type | Rule |
| --- | --- | --- |
| `record` | Source record reference | |
| `cause` | `has_problem` or `depends_on_left_out` | |
| `problem_types` | list of strings | When `cause` is `has_problem` |
| `depends_on` | Source record reference or null | When `cause` is `depends_on_left_out` |
| `relationship` | string or null | The required relationship it depends through |
| `loaded` | boolean | False when the record could not be built |

## Kept record (FR-005, FR-024)

| Field | Type | Rule |
| --- | --- | --- |
| `record` | Source record reference | |
| `cause` | `peer_left_out` or `peer_not_found_in_source` | |
| `relationship` | string | The optional relationship written without the peer |
| `left_out_peer` | Source record reference | The left-out record, or the missing peer with `identity` null |

## Plan summary additions (FR-007)

Added to `PlanSummaryResource` (`infrahub_sync/client/models.py:502-509`), next to `by_action`,
`by_kind`, `total`, `delete_operations_computed` and `deletes_not_executed`:

| Field | Type | Rule |
| --- | --- | --- |
| `left_out` | integer ≥ 0 | Each record once |
| `left_out_with_problem` | integer ≥ 0 | |
| `left_out_dependent` | integer ≥ 0 | `left_out_with_problem + left_out_dependent = left_out` |
| `left_out_by_kind` | object kind to integer | Sums to `left_out` |
| `problems` | integer ≥ 0 | Problem entries |
| `problems_by_type` | object problem type to integer | |
| `kept` | integer ≥ 0 | Kept entries |
| `deletes_withheld_by_kind` | object kind to integer | Per kind, the left-out records without a known identity plus the `destination_identity_ambiguous` identities; the plan computes no delete of these kinds (FR-008, research R21). Empty when there is none |
| `dropdown_values_checked` | boolean | False when the run had no destination schema snapshot |

A format 2 or 3 plan returns zeros and `dropdown_values_checked: false`.

## Plan report (format 4, FR-031)

`plan/report.jsonl`: one JSON object per line in the canonical encoding of ADR 0001, each with
`entry` set to `problem`, `left_out` or `kept` and the fields above. Order: problems by
`problem_type`, `kind`, canonical identity, `source_id`; then left-out records by `kind`, identity,
`source_id`; then kept records by `kind`, identity, `relationship`; ties by the canonical bytes of
the entry. The order does not depend on read order.

Manifest additions: `format_version: 4`; `report` with `digest` (SHA-256 hex), `size`,
`problems`, `left_out`, `left_out_with_problem`, `left_out_dependent`, `kept`;
`dropdown_values_checked: false` when the check could not run, absent otherwise. The plan checksum
covers the manifest, so it covers the report digest. A digest or count that does not match the
file is a `PlanVerificationError`, and the plan is not applied. Format 3 fields stay unchanged:
`destination_id` on updates, `lineage`, `plan/last_changes.jsonl`.

## Stage failure record additions (FR-014)

Added to `results.<stage>_failure` (written by `_failure_evidence`, `flow.py:805-847`). Existing
keys keep their meaning: `stage`, `outcome`, `error_type`, `cause_type`, `cause`, `next_action`,
`infrahub_error`, `recovery_action`, `reason`, `message`, and the apply record keys
(`applied_operations`, `failed_operation`, `failed_operation_wrote`, `may_have_partially_written`,
`skipped_delete_operations`, `skipped_delete_count`).

| Field | Type | Rule |
| --- | --- | --- |
| `failure_type` | string | One of the failure types below; always present |
| `system` | object or null | `{"role": "source" or "destination", "adapter": <name>}` |
| `error_code` | string or null | Infrahub's `extensions.code`, at most 128 characters |
| `node_kind` | string or null | Infrahub's node kind, or the kind of a typed Sync error; at most 128 characters |
| `attribute` | string or null | Infrahub's field name; at most 128 characters |
| `infrahub_message` | string or null | Only for `destination_rejected_write`; control characters removed; at most 1,000 characters (`MAX_FAILURE_CAUSE_TEXT`) |
| `record` | Source record reference or null | With `field`, from a typed Sync error |
| `field` | string or null | |
| `operations_total` | integer or null | For a stopped apply |
| `failed_operation_detail` | object or null | `{"action", "kind", "identity"}` of `failed_operation` |

Failure types: `destination_rejected_write`, `system_unreachable`, `system_timeout`,
`token_rejected`, `permission_missing`, `tls_certificate_rejected`, `record_load_failed`,
`transform_failed`, `credential_missing`, `verification_failed`, `apply_refused`,
`left_out_review_required`, `destination_peer_missing`, `destination_peer_ambiguous`,
`lineage_refused`, `destination_schema_unavailable`, `internal_error`.

## Stage result `failure` for a stopped `sync` (FR-012)

| Field | Value |
| --- | --- |
| `failure.failure_type` | `left_out_review_required` |
| `failure.stage` | `sync` |
| `failure.left_out` | the count |
| `failure.next_action` | "Review the plan with `infrahub-sync runs plan <run_id> --problems` and `--left-out`, then apply it with `infrahub-sync apply <run_id> --expected-checksum <checksum> --confirm-left-out`." |

Run state: phase `planned`, outcome `left-out-review-required`, no write admitted.

| From | Event | To |
| --- | --- | --- |
| `sync` run planning | Plan saved with `left_out` > 0 | phase `planned`, outcome `left-out-review-required` |
| `sync` run at the stop | Same commit as the stop's writeback, no operation dispatched | the run's write admission is released (research R11) |
| that state | Apply with checksum and `confirm_left_out: true` | a new write admission is reserved, and the apply runs as for a `plan` run |
| that state | Apply without `confirm_left_out: true` | 409 `left-out-confirmation-required`, state unchanged |
| after that apply was admitted | A second apply | 409 `run-execution-conflict` |
| `sync` run that dispatched any operation | Any apply | 409 `run-execution-conflict` (today) |

## Apply request addition (FR-010, FR-026)

`ApplyRunRequest.confirm_left_out: bool = False`, strict JSON boolean. Required true when the
plan's `report.left_out` is greater than zero; accepted and ignored otherwise.

## Worker environment record (FR-035)

New product-store table `worker_environments`.

| Field | Type | Rule |
| --- | --- | --- |
| `worker_id` | string | The worker's identity, as the store already records it |
| `credential_names` | list of strings | Names of set, non-empty `INFRAHUB_SYNC_CREDENTIAL_*` variables; never values |
| `observed_at` | timestamp | Refreshed on each poll; a worker counts as live by the liveness rule that `service/liveness.py` uses |

## Validation findings (FR-034 to FR-036)

New codes next to the existing `destination-schema-mismatch`:

| Code | Severity | Names |
| --- | --- | --- |
| `destination-dropdown-value-not-in-choices` | error | kind, attribute, value, choices |
| `credential-not-set-in-worker` | error | variable name |
| `worker-environment-unknown` | warning | none |
| `destination-permission-missing` | error | kind or lineage lookup, permission |
| `destination-lineage-node-not-found` | error | `source` or `owner`, kind, name |
| `destination-lineage-node-ambiguous` | error | `source` or `owner`, kind, name |
