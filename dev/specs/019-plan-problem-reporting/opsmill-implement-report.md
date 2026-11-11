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

## T077 — verification

Run on 2026-11-11 in the worktree on branch `019-plan-problem-reporting`, with the full
development profile (`uv sync --extra dev --extra prefect --extra service`), after the
documentation and test changes of T074 to T079.

### Required workflow of AGENTS.md

| Command | Result |
| --- | --- |
| `uv run invoke format` | exit 0 |
| `uv run invoke lint` | exit 0; Pylint passed (0 diagnostics, score 10.0); ty reports the 20 `unused-ignore-comment` warnings that were there before spec 019's last chunks, and no error |
| `uv run ruff check .` | exit 0, `All checks passed!` |
| `uv run ty check .` | exit 0, 20 warnings (`unused-ignore-comment`, files not touched by spec 019), no error |
| `uv run invoke tests.tests-unit` | exit 0, `7365 passed, 4 skipped, 285 deselected, 5 xfailed, 9 warnings in 215.22s (0:03:35)`, finished 2026-11-11T06:57:19Z |
| `uv run rumdl check` on the changed Markdown files | `No issues found` |
| `uv run invoke docs.lint` | exit 0, rumdl `No issues found in 144 files` |
| `vale` on the changed user pages | 0 errors; 4 sentence-case warnings on headings that this chunk did not change |
| `cd docs && pnpm install --frozen-lockfile`, then `uv run invoke docs.docusaurus` | exit 0, `[SUCCESS] Generated static files in "build"`, no broken link or anchor reported |
| `uv run invoke docs.generate` | exit 0; `docs/docs/reference/cli.mdx` was already current (no diff). The task only regenerates the CLI reference; the Sync HTTP API reference is written by hand and was edited |

The five expected failures:

- four strict `xfail` tests in `tests/plan/test_warm_read_problems.py`, which record that a warm
  incremental read does not record problems (T081, deferred);
- `tests/compose/test_image_pins.py`, which belongs to the preview stack, not to spec 019.

### CLI sanity

| Command | Result |
| --- | --- |
| `uv run infrahub-sync --help` | exit 0 |
| `uv run infrahub-sync configs --help` | exit 0 |
| `uv run infrahub-sync runs --help` | exit 0 |

`runs plan --help` lists `--problems`, `--left-out`, `--kept` and `--type`, and `apply --help`
lists `--confirm-left-out`.

### Offline quickstart checks

The offline checks of `quickstart.md` are the three commands above (`tests.tests-unit`,
`invoke lint`, `ruff check .`); all passed. The tests behind each expectation:

- SC-002: `tests/plan/test_problems.py` plans the fixture with all problem types. The fixture
  gives ten problem entries for the nine problem types, because one scenario has two records
  that refer to a peer the source does not return (two `peer_not_found_in_source` entries).
  SC-002 and `quickstart.md` say "nine problems". This mismatch is recorded here and not
  changed in the specification.
- SC-006: `tests/plan/test_apply_gate.py`, `tests/service/test_apply_left_out_confirmation.py`
  and `tests/cli/test_apply_confirm_left_out.py`.
- SC-003, SC-005, SC-007: `tests/service/test_failure_cause.py`,
  `tests/service/test_failure_cause_credentials.py`, `tests/plan/test_report_credentials_absent.py`.
- SC-004: the US4 tests of planning again after a stopped apply (unit tier only).
- SC-009, SC-011: `tests/configuration/test_validate_permissions.py`,
  `tests/service/test_worker_environment.py`, `tests/service/test_config_routes.py`.

### Live quickstart checks — not run

The live steps 1 to 5 of `quickstart.md` were not run. The services they need were not
running on 2026-11-11 (checked with `nc -z 127.0.0.1 <port>`): NetBox on port 8082, the Sync
API of the preview stack on port 8010, Prefect on port 4200, and no Sync API on port 8000.
This chunk does not start shared stacks, so T077 is not ticked: its offline half passed, and its live half waits for the same stacks as T072. "T072 — not run" above lists the same services and
the commands that start them. When they run, the steps are:

```bash
uv run infrahub-sync configs register examples/netbox_to_infrahub
uv run infrahub-sync configs validate <config> <version> --branch main
uv run infrahub-sync plan --config-id <config> --version <version>
uv run infrahub-sync runs plan <run_id> --problems
uv run infrahub-sync runs plan <run_id> --left-out
uv run infrahub-sync apply <run_id> --expected-checksum <hex>
uv run infrahub-sync apply <run_id> --expected-checksum <hex> --confirm-left-out
uv run infrahub-sync sync --config-id <config> --version <version>   # on data with a planted bad record
uv run infrahub-sync runs show <run_id>
```

`quickstart.md` writes `configs validate <config> --branch main` and `plan <config>`; the CLI
takes `configs validate CONFIG VERSION` and `plan --config-id CONFIG --version VERSION`, as the
commands above show.

### Old 009 FR-004 and FR-013 (T079)

- FR-004, a plan writes nothing to the destination:
  - existing on `feature/v3-develop`, deletes only:
    `tests/test_effective_diffsync_flags.py::test_plan_records_delete_without_dispatching_model_delete`;
  - existing, for a review:
    `tests/plan/test_review.py::test_review_writes_nothing_even_when_the_plan_fails_verification`;
  - added, for creates, updates and problems:
    `tests/plan/test_plan_writes_nothing.py::test_a_plan_writes_nothing_to_the_destination[with-problems]`
    and `[no-problems]`.
- FR-013, a rejected write stops the apply:
  - existing on `feature/v3-develop`:
    `tests/cache/test_apply_plan.py::test_a_known_destination_failure_is_wrapped_with_the_operation_and_run_context[graphql-rejection]`
    (the rejected operation is the last one, so it does not show that a later operation is
    skipped) and
    `tests/adapters/test_infrahub_planned_write.py::test_a_refused_operation_stops_the_apply_and_leaves_the_prior_write_recorded`
    (the later operation is skipped, but the stop is a refusal of the create guard, not an
    Infrahub rejection);
  - added:
    `tests/cache/test_apply_plan.py::test_an_infrahub_rejection_stops_the_apply_before_the_next_operation`
    (Infrahub rejects the second of three operations; the third is never sent).
