# HTTP API contract: approval and the destination-checking `verify`

The API stays `v3-unstable`. Every change is additive except the two refusals that apply only when
`INFRAHUB_SYNC_REQUIRE_APPROVAL` is on.

## `POST /runs/{run_id}/approve` (new)

Headers: `Authorization: Bearer <token>`, `Idempotency-Key: <key>`.

Request body:

```json
{ "expected_checksum": "<64 hex>", "reason": "reviewed by the network team" }
```

`reason` is optional. Unknown fields are refused (422), as for the other request models.

Order of evaluation:

1. Authorization: only the run's actor or an administrator (403 `forbidden`).
2. Idempotency: a receipt for this actor and key replays the stored response; a different body with the same key
   is refused as for the other commands.
3. Approvable state: phase `planned` and no active execution, else 409 `run-not-approvable`, message names the phase.
4. Plan: the existing plan lookup (404 `run-not-found`, 410 `artifact-expired`, 503 `plan-unavailable`).
5. Plan checksum state: `checksum_ok == false` → 409 `plan-checksum-unverified`.
6. Checksum: `expected_checksum != plan.checksum` → 409 `checksum-mismatch`, message shows the expected and the
   given checksum.
7. Existing approval → 409 `already-approved`, the error `details` carry the existing approval.

Success: `200 OK`, body is the run resource (`RunResource`) with its `approval`, plus `plan_summary` with the
approved plan's counts (creates, updates, recorded deletes). The body is stored with the mutation receipt, so a
retry with the same key returns it unchanged. No Prefect flow run is submitted.

The `run-not-approvable` message names the run's phase, or the active stage when an execution is in progress.

## `GET /runs/{run_id}` and `GET /runs` (changed, additive)

The run resource gains:

```json
"approval": {
  "approved_by": "alice",
  "approved_at": "2026-11-10T14:03:11Z",
  "plan_checksum": "<64 hex>",
  "reason": "reviewed by the network team"
}
```

`approval` is `null` when the run has none. The run list carries the same field.

## `POST /runs/{run_id}/verify` (behaviour changed, request unchanged)

The worker stage runs every pre-write check (see [data-model.md](../data-model.md#verification-result-existing-resultsverification-extended)).
The service passes the run's stored `destination_branch` as the stage's branch.

- Success: `results.verification.checks` lists the checks that passed, `destination_checked` is `true`.
- A failed check: the stage fails; `GET /runs/{run_id}` reports the failure with the check name, as for a refused
  `apply`.
- Destination unreachable: the stage fails with the destination error; no check after it is reported as passed.
- The phase does not change.

## `POST /runs/{run_id}/apply` (unchanged unless the setting is on)

When `INFRAHUB_SYNC_REQUIRE_APPROVAL` is on, after authorization and idempotency replay and before the receipt is
reserved:

- no approval → 409 `approval-required`, message: approve the plan with `approve RUN_ID --expected-checksum ...`;
- approval checksum differs from `expected_checksum` → 409 `approval-checksum-mismatch`, message shows both.

## `POST /runs` with `operation = "sync"` (unchanged unless the setting is on)

When the setting is on → 409 `approval-required-for-sync` before the run record is created. Message: run `plan`,
`approve` and `apply` instead. `operation = "plan"` is never affected.

## `GET /me` (changed, additive)

```json
{ "actor": "alice", "administrator": false, "approval_required": true }
```
