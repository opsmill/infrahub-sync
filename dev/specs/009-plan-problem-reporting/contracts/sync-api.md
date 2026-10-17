# Sync API contract for spec 009

Spec 011 (operator UI) consumes this contract. Every route uses the bearer-token
authentication of the existing run routes (`service/app.py`). This contract does not mention
the object store; spec 013 owns it. Field definitions are in
[data-model.md](../data-model.md). Spec 011 may add filters and fields; it does not rename or
remove anything defined here.

## Plan summary: new counts

`GET /runs/{run_id}/plan` returns `summary` (`PlanSummaryResource`) with the counts of
data-model.md "Plan summary additions". Spec 011 FR-012 returns the same summary without the
operations.

## Problem list, left-out list and kept list

```text
GET /runs/{run_id}/plan/problems?offset=0&limit=500&problem_type=<name>&kind=<kind>
GET /runs/{run_id}/plan/left-out?offset=0&limit=500&kind=<kind>
GET /runs/{run_id}/plan/kept?offset=0&limit=500&kind=<kind>
```

Response, the same shape for the three routes:

```json
{
  "run_id": "20261015T1200-1a2b3c4d",
  "plan_checksum": "<64 hex>",
  "entries": [
    {
      "problem_type": "dropdown_value_not_in_choices",
      "record": {"kind": "DcimDevice", "identity": {"location": "...", "name": "rtr1"}, "source_id": "42"},
      "field": "status",
      "value": "planned",
      "allowed": ["active", "deprecated", "maintenance", "provisioning", "reserved"],
      "peer": null,
      "other_records": [],
      "reason": "DcimDevice rtr1: status 'planned' is not one of the choices of the Dropdown attribute status"
    }
  ],
  "offset": 0,
  "limit": 500,
  "total": 1,
  "next_offset": null
}
```

Rules:

- `limit` is 1 to 500. The order is the stable order of the plan report.
- `plan_checksum` names the plan the page comes from (spec 011 FR-015). A request with
  `expected_checksum=<hex>` that differs from the stored plan is refused with 409
  `plan-changed`.
- A plan saved in format 2 or 3 returns empty lists and zero counts.
- `problem_type` is one of the eight stable names in data-model.md:
  `dropdown_value_not_in_choices`, `duplicate_source_identity`,
  `destination_identity_collision`, `wrong_value_type`, `required_peer_excluded_by_filter`,
  `identities_merge_in_destination`, `peer_not_found_in_source`,
  `destination_identity_ambiguous`.
- A `destination_identity_ambiguous` entry carries `destination_ids`, the Infrahub node IDs
  that hold the identity. No other entry carries that key.
- `reason` is rendered from a fixed template per problem type; `value` is the source value.
  No entry holds a connection setting or the text of an error.
- Errors: 404 `run-not-found`; 410 `artifact-expired`; 503 `plan-unavailable`; 422 for an
  unknown `problem_type` (the stable names are listed above and in data-model.md).

## Run failure cause

`GET /runs/{run_id}` returns `run.failure` when the run failed or stopped, and `null`
otherwise:

```json
{
  "stage": "apply",
  "failure_type": "destination_rejected_write",
  "system": {"role": "destination", "adapter": "infrahub"},
  "error_code": "ATTRIBUTE_CONSTRAINT_VIOLATION",
  "node_kind": "DcimDevice",
  "attribute": "status",
  "record": null,
  "field": null,
  "infrahub_message": "planned is not a valid value for status",
  "summary": "Infrahub rejected a write to DcimDevice attribute status (ATTRIBUTE_CONSTRAINT_VIOLATION)",
  "error_class": "OperationApplyFailedError",
  "apply_progress": {
    "operations_total": 2291,
    "operations_written": 692,
    "failed_operation": {"operation_id": "op_518aada155d790ad", "action": "update", "kind": "DcimDevice", "identity": {"name": "rtr1"}},
    "failed_operation_may_have_written": true
  },
  "next_action": "Fix the cause, then create and review a new plan with `infrahub-sync diff`."
}
```

The example Infrahub message is illustrative. The written operation IDs are returned by
`GET /runs/{run_id}/results` under `apply_failure`, as the applied IDs are today.

Content rule: every field is built from the allowlisted fields of
[data-model.md](../data-model.md#run-failure-cause-fr-014-to-fr-020-fr-025). `infrahub_message`
is set only for `destination_rejected_write`. No field holds the text of an adapter's or an
HTTP library's error, an address, a header or a traceback. A source failure looks like this:

```json
{
  "stage": "plan",
  "failure_type": "token_rejected",
  "system": {"role": "source", "adapter": "netbox"},
  "error_code": null,
  "node_kind": null,
  "attribute": null,
  "record": null,
  "field": null,
  "infrahub_message": null,
  "summary": "The source (netbox) rejected the token",
  "error_class": "RunExecutionError",
  "apply_progress": null,
  "next_action": "Check the credential that the configuration references for netbox, then plan again."
}
```

The existing `results.{stage}_failure` keys (`stage`, `outcome`, `error_type`, `cause_type`,
`applied_operations`, `failed_operation`, `failed_operation_wrote`, `may_have_partially_written`)
stay, so existing scripts keep working.

## Apply request

`POST /runs/{run_id}/apply` body (`ApplyRunRequest`):

```json
{
  "expected_checksum": "<64 hex>",
  "confirm_writes": true,
  "confirm_left_out": true,
  "branch": "netbox-import",
  "reason": "first import, 12 records left out reviewed"
}
```

Checks, in order, all before any write:

1. `confirm_writes` (today, `service/service.py:315`): 409 `confirmation-required`.
2. Checksum (today, `service/service.py:317-330`): 409 `checksum-conflict`.
3. New: the plan's `left_out` is greater than zero and `confirm_left_out` is absent or false:
   409 `left-out-confirmation-required`, message "this plan leaves out 12 records; confirm with
   confirm_left_out=true", with `{"expected_left_out_count": 12}` in the error body. Audited
   with outcome `refused-left-out-not-confirmed`.
4. New: the run already admitted a write (`product_store/store.py:1827`): 409
   `new-plan-required`, message "This plan was already applied or its apply stopped; create
   and review a new plan".
5. New: the plan is format 2 or 3: 409 `plan-format-not-applicable`, message "plan again
   before applying".

`confirm_left_out` is a JSON boolean, false when absent; any other value is a 422. When
`left_out` is zero, it is accepted and ignored. The request carries no count: the plan checksum
of check 2 already binds the apply to the reviewed plan (decided on 2026-10-17).

## A `sync` run whose plan leaves out records

`POST /runs` with `operation: "sync"` stops after the plan when `left_out` is greater than zero.
The run ends with phase `planned` and outcome `left-out-review-required`, `failure` set with
`failure_type: "left_out_review_required"` and `stage: "sync"`. The team applies it with
`POST /runs/{run_id}/apply` and `confirm_left_out: true`. No request field skips this stop.

## Configuration validation

`POST /configs/{config_id}/versions/{registry_version}/validate?branch=<name>`

- Without `branch`: unchanged.
- With `branch`: adds the destination schema findings (`destination-schema-mismatch`, existing)
  and these new finding codes:
  - `destination-dropdown-value-not-in-choices`: a literal `static` value of a mapped Dropdown
    attribute is not a choice.
  - `credential-not-set-in-worker`: a `$credential` reference whose variable no live worker
    holds. The message names the variable, never a value.
  - `worker-environment-unknown` (warning): no live worker has published its credential names.

## Registration responses (FR-035)

`POST /configs` and `POST /configs/{config_id}/versions` return the `package_checksum` and no
`declared_content`.
