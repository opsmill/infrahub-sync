# Quickstart: validate plan problem reporting

These scenarios prove the spec's user stories end to end. Contracts:
[sync-api.md](contracts/sync-api.md), [cli.md](contracts/cli.md),
[plan-artifact-format-4.md](contracts/plan-artifact-format-4.md). Fields:
[data-model.md](data-model.md).

## Prerequisites

```bash
uv sync --extra dev --extra prefect --extra service
uv run invoke tests.tests-unit
```

The live scenarios need the development stack, a local NetBox with the pinned `demo` dataset
and the schema library, as described in
[The `from-netbox` example check](../../../docs/docs/development/guidelines/testing-tiers.md#the-from-netbox-example-check).

## Scenario 1: one plan lists six problems (User Story 1, SC-002)

1. Load the fixture source that holds one record of each problem type, valid records, a
   device with a problem and five interfaces that belong to it, and an IP address that refers to
   that device only through an optional relationship.
2. Register the fixture configuration and run `infrahub-sync diff`.
3. Expected:
   - the run finishes; the summary shows `left_out` equal to the six problem records plus their
     dependents, with the split between "with a problem" and "depending on one";
   - `infrahub-sync runs problems <run_id>` prints six lines, one per problem type, each with
     kind, identity, type and reason;
   - `infrahub-sync runs left-out <run_id>` names each interface with the device it depends on;
   - `infrahub-sync runs kept <run_id>` names the IP address, the relationship and the device;
   - the Infrahub branch has no new commit (no write).

## Scenario 2: apply needs the confirmed count (User Story 2, SC-006)

1. `infrahub-sync apply <run_id> --expected-checksum <hex>`: refused before any write, and the
   message states the count.
2. The same request through the API with `confirmed_left_out_count` absent, then wrong: 409
   `left-out-confirmation-required` with `expected_left_out_count`.
3. `--confirm-left-out <count>`: the apply writes only the plan's operations. `runs show`
   prints the operations written and the left-out count.
4. A plan with no records left out applies without the option.
5. `POST /runs` with `operation: "sync"` on the fixture: the run stops with outcome
   `left-out-review-required`; applying it with the count writes the operations.

## Scenario 3: the cause of a failed run (User Story 3, SC-003)

Run one plan or apply per failure type and check that `infrahub-sync runs show <run_id>` and
`GET /runs/{run_id}` show `failure` with the expected `failure_type`, `system` and message:

| Setup | Expected `failure_type` |
| --- | --- |
| Stop the NetBox container | `system_unreachable`, source `netbox` |
| Set a wrong NetBox token | `token_rejected`, source `netbox` |
| Set a wrong Infrahub token | `token_rejected`, destination `infrahub` |
| A transform that raises | `plan_failed`, message names the transform error |
| Plan with the Dropdown check turned off in a test build, then apply a value outside the choices | `destination_rejected_write`, `error_code` from Infrahub, `node_kind`, `attribute` |

## Scenario 4: a stopped apply and the next plan (User Story 4, SC-004)

1. Apply a plan of N operations against a test destination that rejects operation K.
2. `runs show` reports K-1 written, the failed operation with kind and identity, and whether it
   may have written.
3. Applying the same run again: 409 `new-plan-required`.
4. `infrahub-sync diff` again with no source change: the new plan's operation count is
   N - (K - 1), less any operation the destination now already matches.

## Scenario 5: credential masking (SC-005, SC-007)

Run Scenarios 1 to 4 with a referenced credential of five characters and a referenced credential
holding `"`, `\` and `,`. Search every cause, problem reason, report entry, published plan, Sync
API response and run log for both values: no match. Every published plan parses as JSON.

## Scenario 6: the NetBox example on the `demo` dataset (SC-001, SC-008)

1. Plan `examples/netbox_to_infrahub/package.yml` against the pinned `demo` dataset.
2. Expected: no run failure; device statuses outside the five choices are translated; the
   device at position `2.5` is listed as `wrong_value_type`; the two devices named alike at two
   sites are listed as `destination_identity_collision`.
3. Plant the five problems recorded on 2026-10-05 (#369): one plan lists all five.

## Scenario 7: `configs validate` before a run (SC-009)

1. Register a configuration that maps an attribute missing from the destination schema and
   references `$credential` `INFRAHUB_SYNC_CREDENTIAL_UNSET_TOKEN`, which the worker does not
   have.
2. `infrahub-sync configs validate <config_id> <version> --branch main` reports
   `destination-schema-mismatch` and `credential-not-set-in-worker` naming the variable, with no
   run started.

## Gates

```bash
uv run invoke format
uv run invoke lint
uv run infrahub-sync --help
uv run infrahub-sync configs --help
uv run infrahub-sync runs --help
```
