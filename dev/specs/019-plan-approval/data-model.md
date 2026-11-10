# Data Model: Approve a plan without applying it, and a `verify` that checks the destination

## RunApproval (new, part of `ProductRun`)

| Field | Type | Rule |
| --- | --- | --- |
| `approved_by` | actor name, non-empty | The authenticated actor of the approve request. |
| `approved_at` | timestamp with time zone | Set by the service when the approval is stored. |
| `plan_checksum` | 64 lowercase hex characters | Equal to the published plan's `checksum` at approval time. |
| `reason` | text or `None` | The optional audit reason of the request. |

- At most one per run (FR-006). Never changed or removed once stored.
- Stored as four nullable columns on `product_runs`: `approved_by`, `approved_at`, `approved_checksum`,
  `approval_reason`. All four absent, or `approved_by`, `approved_at` and `approved_checksum` present together; the
  model validator refuses a partial record, as it does for the configuration binding.
- Added by the store's idempotent start-up column migration; existing rows read as "no approval".

## ProductRun (existing)

- Gains `approval: RunApproval | None = None`.
- No change to `phase` or `outcome`: an approved run stays in phase `planned`. Approval is not a phase.

### When a run can be approved

```text
phase == "planned"
and no service execution of the run is active
and approval is None
and the published plan resource has checksum_ok == true
and request.expected_checksum == plan.checksum
```

Any other state refuses with the reason (see [contracts/http-api.md](contracts/http-api.md)).

## Run phase `cancelled` (existing value, new way to reach it)

```text
planned --(runs cancel, no execution in progress, no write admitted)--> cancelled
```

- Set by `cancel_planned_run` under the run's write-first row lock: `phase = 'cancelled'`,
  `outcome = 'cancelled'`, `finished_at = <cancel time>`. `approval`, `summary`, `results` and artifacts are kept.
- The other ways to reach `cancelled` (Prefect acknowledges the cancellation of an execution) are unchanged.
- A run in phase `cancelled` cannot be approved, cannot admit an `apply` reservation, and cannot be cancelled again.
  It can still be read and verified.

## Verification result (existing `results.verification`, extended)

The `verify` stage already merges `results.verification` with `checksum`, `checksum_ok` and `verification_notes`.
It gains:

| Field | Type | Meaning |
| --- | --- | --- |
| `checks` | list of check names, in the order run | Every pre-write check that passed. |
| `destination_checked` | bool | `true` when the destination checks ran; `false` only for a legacy run without a registered configuration. |

A failed check does not produce a `results.verification`; the stage fails and the run's failure records the check
name, the expected value and the found value, as `apply` does.

Check names, in order: `checkpoint`, `run_binding`, `plan_checksum`, `source_snapshot`, `config_version`,
`format_version`, `destination_schema`, `destination_binding`, `write_surface`, `planned_fields`, `lineage`.

## Approval-required setting (new, service configuration)

| Name | Values | Default |
| --- | --- | --- |
| `INFRAHUB_SYNC_REQUIRE_APPROVAL` | `true`/`1`/`yes`/`on`, `false`/`0`/`no`/`off` (case-insensitive) | off |

Read when each `apply` or `sync` request is admitted. A malformed value stops the service at start-up.

## Audit events (existing `AuditEvent`, new operation)

`operation = "approve"`, outcomes: `accepted`, `replayed`, `refused-authorization`, `refused-not-approvable`,
`refused-checksum`, `refused-already-approved`, `refused-plan-unverified`.

`operation = "apply"` and `"sync"` gain the outcome `refused-approval-required`; `"apply"` and `"approve"` gain
`refused-cancelled`.

`operation = "cancel"` gains the outcomes `accepted-withdrawn` (a finished plan was cancelled) and
`refused-cancelled` (the run was already cancelled).
