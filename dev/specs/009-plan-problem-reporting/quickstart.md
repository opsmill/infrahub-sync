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

## Scenario 1: one plan lists seven problems (User Story 1, SC-002)

1. Load the fixture source that holds one record of each of the seven problem types, valid
   records, a device with a problem and five interfaces that belong to it, and an IP address
   that refers to that device only through an optional relationship. The
   `peer_not_found_in_source` record refers to its missing peer through a required
   relationship.
2. Register the fixture configuration and run `infrahub-sync diff`.
3. Expected:
   - the run finishes; the summary shows `left_out` equal to the seven problem records plus their
     dependents, with the split between "with a problem" and "depending on one";
   - `infrahub-sync runs problems <run_id>` prints seven lines, one per problem type, each with
     kind, identity, type and reason;
   - `infrahub-sync runs left-out <run_id>` names each interface with the device it depends on;
   - `infrahub-sync runs kept <run_id>` names the IP address, the relationship and the device;
   - the Infrahub branch has no new commit (no write).

## Scenario 1b: a peer that is not in the source (User Story 1 scenario 8, SC-010)

1. For each bundled source adapter that resolves references while loading (NetBox, Nautobot,
   ACI, generic REST, Slurp'it; research R4), load a fixture where a device refers to a site
   that the source does not return, through a required relationship, and an IP address refers
   to a VRF that the source does not return, through an optional relationship.
2. Run `infrahub-sync diff`.
3. Expected:
   - the run finishes; no adapter fails the plan or drops the reference without a problem;
   - `infrahub-sync runs problems <run_id> --type peer_not_found_in_source` prints two lines,
     each with the kind, the identity, the relationship and the missing peer's source ID;
   - `infrahub-sync runs left-out <run_id>` names the device;
   - `infrahub-sync runs kept <run_id>` names the IP address, the relationship `vrf` and the
     missing peer, and the plan holds no change to that relationship.

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
`GET /runs/{run_id}` show `failure` with the expected `failure_type` and `system`:

| Setup | Expected `failure_type` |
| --- | --- |
| Stop the NetBox container | `system_unreachable`, source `netbox` |
| Set a wrong NetBox token | `token_rejected`, source `netbox` |
| Set a wrong Infrahub token | `token_rejected`, destination `infrahub` |
| A transform that raises | `transform_failed`, with the kind, the record and the field, and no transform text |
| Plan with the Dropdown check turned off in a test build, then apply a value outside the choices | `destination_rejected_write`, `error_code` from Infrahub, `node_kind`, `attribute` |

## Scenario 4: a stopped apply and the next plan (User Story 4, SC-004)

1. Apply a plan of N operations against a test destination that rejects operation K.
2. `runs show` reports K-1 written, the failed operation with kind and identity, and whether it
   may have written.
3. Applying the same run again: 409 `new-plan-required`.
4. `infrahub-sync diff` again with no source change: the new plan's operation count is
   N - (K - 1), less any operation the destination now already matches.

## Scenario 5: credentials stay out of causes, problems and plans (SC-005, SC-007)

1. Configure a referenced credential of five characters and one that holds `"`, `\` and `,`.
2. For each failure type of Scenario 3, raise the failure in a unit test with error text that
   holds both values: an address with a user name and password, a request header, and a
   traceback.
3. Expected: neither value, and no part of the error text, appears in the run record's
   `failure`, the CLI output, any problem reason, `report.jsonl`, `operations.jsonl`,
   `manifest.json` or a Sync API response. The test asserts absence; it does not rely on value
   replacement.

## Scenario 5b: DiffSync compares the data as loaded (research R2)

1. Plan the fixture of Scenario 1.
2. Expected:
   - a canonical dump of the source and destination stores taken before the comparison equals
     the dump taken after operations are selected;
   - the IP address that refers to the left-out device holds that reference in the source
     store, and its operation in the plan does not;
   - no runtime model declares `_children`, and the engine calls only `diff_from`;
   - with the same fixture loaded by a test adapter that implements only `list_records` and
     `record_to_data`, the plan lists the same problems.

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
