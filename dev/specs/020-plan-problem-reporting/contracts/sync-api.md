# Sync API contract

Every route uses the bearer-token authentication of the existing run routes. Field definitions
are in [data-model.md](../data-model.md).

## Plan summary

`GET /runs/{run_id}/plan` returns `summary` with the additions of data-model.md "Plan summary
additions". Additive: older clients ignore the new fields.

## Problem, left-out and kept lists

```text
GET /runs/{run_id}/plan/problems?offset=0&limit=500&problem_type=<name>&kind=<kind>
GET /runs/{run_id}/plan/left-out?offset=0&limit=500&kind=<kind>
GET /runs/{run_id}/plan/kept?offset=0&limit=500&kind=<kind>
```

Response, the same shape for the three routes:

```json
{
  "run_id": "20261111T1200-1a2b3c4d",
  "plan_checksum": "<64 hex>",
  "entries": [
    {
      "problem_type": "dropdown_value_not_in_choices",
      "record": {"kind": "DcimDevice", "identity": {"location": "r1", "name": "rtr1"}, "source_id": "42"},
      "field": "status",
      "value": "planned",
      "allowed": ["active", "maintenance", "provisioning"],
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

The choices in the example are illustrative.

Rules:

- `limit` is 1 to 500. The order is the stable order of the plan report.
- `expected_checksum=<hex>` that differs from the stored plan: 409 `plan-changed`.
- A format 2 or 3 plan returns empty lists.
- Errors: 404 `run-not-found`, 410 `artifact-expired`, 503 `plan-unavailable`, 422 for an unknown
  `problem_type`.

## Failure record

`GET /runs/{run_id}` and `GET /runs/{run_id}/results` return `results.<stage>_failure` with the
additions of data-model.md. Example of a rejected write:

```json
{
  "stage": "apply",
  "outcome": "ambiguous",
  "error_type": "OperationApplyFailedError",
  "cause_type": "GraphQLError",
  "failure_type": "destination_rejected_write",
  "system": {"role": "destination", "adapter": "infrahub"},
  "error_code": "ATTRIBUTE_CONSTRAINT_VIOLATION",
  "node_kind": "DcimDevice",
  "attribute": "status",
  "infrahub_message": "planned is not a valid value for status",
  "cause": "Infrahub rejected a write to DcimDevice attribute status (ATTRIBUTE_CONSTRAINT_VIOLATION)",
  "next_action": "Fix the cause, then create and review a new plan with `infrahub-sync plan`.",
  "applied_operations": ["op_..."],
  "failed_operation": "op_518aada155d790ad",
  "failed_operation_wrote": null,
  "may_have_partially_written": true,
  "operations_total": 2291,
  "failed_operation_detail": {"action": "update", "kind": "DcimDevice", "identity": {"location": "r1", "name": "rtr1"}}
}
```

The error code and message are illustrative. Example of a source failure:

```json
{
  "stage": "plan",
  "outcome": "failed",
  "error_type": "RunExecutionError",
  "cause_type": "SideLoadError",
  "failure_type": "token_rejected",
  "system": {"role": "source", "adapter": "netbox"},
  "error_code": null,
  "node_kind": null,
  "attribute": null,
  "infrahub_message": null,
  "cause": "The source (netbox) rejected the token",
  "next_action": "Check the credential that the configuration references for netbox, then plan again."
}
```

## Apply request

`POST /runs/{run_id}/apply`, body `ApplyRunRequest`:

```json
{
  "expected_checksum": "<64 hex>",
  "confirm_writes": true,
  "confirm_left_out": true,
  "branch": "netbox-import",
  "reason": "first import, left-out records reviewed"
}
```

Checks, in order, all before any write:

1. `confirm_writes` false: 409 `confirmation-required` (today).
2. Checksum mismatch: 409 `checksum-conflict` (today).
3. Plan `left_out` > 0 and `confirm_left_out` not true: 409 `left-out-confirmation-required`,
   message "this plan leaves out 12 records; confirm with confirm_left_out=true", body field
   `left_out: 12`, audit outcome `refused-left-out-not-confirmed`.
4. Run already admitted a write: 409 `run-execution-conflict` (today, unchanged).
5. Plan format 2 or 3: refused before any write with `PlanFormatApplyUnsupportedError`'s next
   action, "Re-run `plan` …" (today's refusal for format 2, extended to format 3).

`confirm_left_out` is a JSON boolean; any other value is 422.

## A `sync` run whose plan leaves out records

`POST /runs` with `operation: "sync"` stops after the plan when `left_out` > 0: phase `planned`,
outcome `left-out-review-required`, and the stage result written to the run's `results` carries
`failure` as in data-model.md (the key `result_next_action` reads, `flow.py:888-897`). No request field
skips the stop.

## Configuration validation

`POST /configs/{config_id}/versions/{registry_version}/validate?branch=<name>`

- Without `branch`: unchanged.
- With `branch`: adds the destination schema findings (existing code
  `destination-schema-mismatch`) and the new codes of data-model.md "Validation findings".

## Registration responses (FR-038)

`POST /configs` and `POST /configs/{config_id}/versions` return the version reference with
`package_checksum` and no `declared_content`. `GET /configs/{config_id}/versions/{registry_version}`
is unchanged.
