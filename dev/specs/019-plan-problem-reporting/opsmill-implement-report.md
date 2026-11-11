# Spec 019 implementation report

## T072 — not run

Task T072 runs the `from-netbox` example check on the pinned `demo` dataset and records two
results (research R18, R19):

- whether the two same-named devices are written or listed as problems
- the plan time before and after spec 019

The check was not run on 2026-11-11, so T072 is not ticked.

### Why the check was not run

The services the check needs were not running, and the implementation chunk does not start,
stop or reset shared containers or volumes:

- Local NetBox (`http://localhost:8082`): port closed.
- Sync API of the preview stack (`http://localhost:8010`): port closed. Prefect of the preview
  stack refused connections too.
- Preview Infrahub: not running. Port 8080 answers, but it belongs to an unrelated container
  (`hivemind-waf-1`), not to the preview stack.
- `.netbox/` does not exist in the worktree, so the `demo` dump has not been downloaded here.

### Commands that run the check

From the repository root, following
[The `from-netbox` example check](../../../docs/docs/development/guidelines/testing-tiers.md#the-from-netbox-example-check):

1. Load the `demo` dataset. The task prints the NetBox URL and the development token.

   ```bash
   uv run invoke netbox.seed --dataset demo
   ```

2. Start a fresh preview stack with the NetBox token from step 1.

   ```bash
   uv run invoke preview.down --volumes
   INFRAHUB_SYNC_CREDENTIAL_NETBOX_TOKEN="<token printed by netbox.seed>" uv run invoke preview.up
   ```

3. Load the pinned schema library snapshot into the preview Infrahub.

   ```bash
   export INFRAHUB_ADDRESS="http://localhost:8080"
   export INFRAHUB_API_TOKEN="06438eb2-8019-4776-878c-0941b1f1d1ec"
   uv run infrahubctl schema load tests/data/nightly_schema --wait 120
   ```

4. Write the local package and register it.

   ```bash
   uv run invoke netbox.demo-package
   export INFRAHUB_SYNC_API_URL="http://localhost:8010"
   export INFRAHUB_SYNC_API_TOKEN="preview-tester-token-0001"
   uv run infrahub-sync configs register .netbox/from-netbox.local.yml --reason "register the local NetBox demo import"
   ```

5. Create the branch, then plan and review the problems and left-out records.

   ```bash
   uv run infrahubctl branch create netbox-import
   uv run infrahub-sync plan --config-id <config-id> --version <version> --branch netbox-import --reason "review the local NetBox demo import"
   uv run infrahub-sync runs plan <run-id> --problems
   uv run infrahub-sync runs plan <run-id> --left-out
   uv run infrahub-sync sync --config-id <config-id> --version <version> --branch netbox-import --reason "import the local NetBox demo data"
   ```

   After a plan that leaves out records, `sync` stops before apply. Apply the reviewed plan with
   `apply <run-id> --expected-checksum <checksum> --confirm-left-out --branch netbox-import`.

6. Remove both stacks and their data volumes.

   ```bash
   uv run invoke preview.down --volumes
   uv run invoke netbox.down
   ```

### What to record when the check runs

- Whether the pinned `demo` dataset holds two devices with the same name, a device at a rack
  position that is not a whole number, and a device status other than `active`. The results
  from #374 came from the public NetBox demo; for the pinned dataset they are not known.
- For the same-named devices: whether the plan writes them or lists them, and the problem type.
  The unit tests give `destination_identity_collision` for two creates with the same name
  (`tests/plan/test_problems.py::test_destination_identity_collision`). Research R18 expected
  `identities_merge_in_destination`; the live check settles which type the demo data gives.
- The plan time before and after spec 019 (research R19):
  - Before: the same steps on a checkout of `main` (or the merge base of
    `019-plan-problem-reporting`).
  - After: the steps on `019-plan-problem-reporting`.
  - Read the plan time from the run record (`runs show <run-id>`, the plan stage's start and
    end times), and each side's read time from the read progress spec 018 records. Use the same
    tier, the same dataset and a fresh preview stack for both runs.
- The operation count. `testing-tiers.md` states 1,688 operations (1,687 creates and one
  delete) for the mapping before the status translation. A different count needs an
  explanation in the pull request.
