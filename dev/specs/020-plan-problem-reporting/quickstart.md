# Quickstart: check plan problem reporting

Setup and the shell commands are in [AGENTS.md](../../../AGENTS.md) ("Setup", "Required
Development Workflow"). The live checks use the `from-netbox` tier in
[testing tiers](../../../docs/docs/development/guidelines/testing-tiers.md).

## Offline checks

```bash
uv run invoke tests.tests-unit
uv run invoke lint
uv run ruff check .
```

Expected: the unit tier passes, including the new tests listed in `tasks.md`:

- one plan over a fixture with the nine problem types finishes and lists nine problems (SC-002)
- the apply of that plan is refused without the confirmation, and writes only the plan's operations
  with it (SC-006)
- each failure type gives a record with `failure_type` and no credential value, with a 5-character
  credential and one holding `"`, `\` and `,` (SC-003, SC-005, SC-007)
- planning again after a stopped apply holds only the remaining operations (SC-004)
- `configs validate --branch` reports a missing attribute, an unset credential and a missing
  permission (SC-009, SC-011)

## Live check on the pinned NetBox `demo` dataset

1. Start the stacks of the `from-netbox` check (testing tiers page).
2. Register the NetBox example package and plan:

   ```bash
   uv run infrahub-sync configs register examples/netbox_to_infrahub
   uv run infrahub-sync configs validate <config> --branch main
   uv run infrahub-sync plan <config>
   ```

   Expected: validation reports no error finding; the plan finishes; the summary shows
   `left_out:` and `problems:` lines; each #374 case is written or listed (SC-008).
3. List the problems and the left-out records:

   ```bash
   uv run infrahub-sync runs plan <run_id> --problems
   uv run infrahub-sync runs plan <run_id> --left-out
   ```

4. Apply without and with the confirmation:

   ```bash
   uv run infrahub-sync apply <run_id> --expected-checksum <hex>
   uv run infrahub-sync apply <run_id> --expected-checksum <hex> --confirm-left-out
   ```

   Expected: the first is refused with the count when records are left out; the second applies.
5. Start a `sync` on data with a planted bad record. Expected: the run stops after the plan, and
   `runs show` prints the `left_out_review_required` hint with the run ID and checksum.

## Contracts

- [Sync API](contracts/sync-api.md)
- [CLI](contracts/cli.md)
- [Plan format 4](contracts/plan-artifact-format-4.md)
