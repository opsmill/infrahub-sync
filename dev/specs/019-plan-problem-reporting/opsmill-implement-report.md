# Spec 019 implementation report

## T072 — from-netbox check

The `from-netbox` example check ran on 2026-11-12 on the pinned `demo` dataset
(`netbox-demo-v4.7.sql`, SHA-256
`e9846c189c4c5055abfe52d2ca0b55ff089d815c9b0c20614faba94b9c8f5180`), following
[The `from-netbox` example check](../../../docs/docs/development/guidelines/testing-tiers.md#the-from-netbox-example-check).
Each side used a fresh preview stack with new volumes, the same NetBox and the same schema
snapshot (`tests/data/nightly_schema/`, identical on both commits).

- After: branch `019-plan-problem-reporting` at `d80ed043`.
- Before: `feature/v3-develop` at `ea83a248`, in a temporary detached worktree with its own
  `uv sync --extra dev --extra prefect --extra service`. The worktree was removed afterwards.

### Ports and environment

Port 8080 belongs to an unrelated container, so each worktree had a
`development/preview.local.env` (ignored by Git, deleted afterwards) with
`PREVIEW_INFRAHUB_PORT=8085` and its own `COMPOSE_PROJECT_NAME`
(`infrahub-sync-preview-t072` after, `infrahub-sync-preview-t072before` before). The other
ports kept their defaults and were free: NetBox 8082, Sync API 8010, Prefect 4210, Sync
PostgreSQL 5439, MinIO 9010.

### Commands

```bash
uv run invoke netbox.seed --dataset demo
INFRAHUB_SYNC_CREDENTIAL_NETBOX_TOKEN="<token printed by netbox.seed>" uv run invoke preview.up
export INFRAHUB_ADDRESS="http://localhost:8085"
export INFRAHUB_API_TOKEN="06438eb2-8019-4776-878c-0941b1f1d1ec"
uv run infrahubctl schema load tests/data/nightly_schema --wait 120
uv run invoke netbox.demo-package --infrahub-url http://localhost:8085
export INFRAHUB_SYNC_API_URL="http://localhost:8010"
export INFRAHUB_SYNC_API_TOKEN="preview-tester-token-0001"
uv run infrahub-sync configs register .netbox/from-netbox.local.yml --reason "register the local NetBox demo import"
uv run infrahubctl branch create netbox-import
uv run infrahub-sync plan --config-id <config-id> --version 1 --branch netbox-import --reason "review the local NetBox demo import"
uv run infrahub-sync runs show <run-id>
uv run infrahub-sync runs plan <run-id> --problems
uv run infrahub-sync runs plan <run-id> --left-out
uv run infrahub-sync apply <run-id> --expected-checksum <plan-checksum> --branch netbox-import --reason "apply reviewed NetBox plan"
uv run invoke preview.down --volumes
uv run invoke netbox.down
```

The before side ran `preview.up` to `plan` and `runs show` from its own worktree against the
same NetBox, with its own shipped package (the package without the `device_status`
transform). It did not apply.

### Plan time before and after

One plan on each side, each the first plan on a fresh stack (a full read on both sides).
Times come from the run record (`source_read` and `destination_read`) and from the Prefect
worker log (start and end of the flow run `Plan sync of from-netbox`).

| Measure | Before (`ea83a248`) | After (`d80ed043`) |
| --- | --- | --- |
| Run ID | `20261112T1107-06e558fc` | `20261112T1102-48d53acb` |
| Plan flow run, start to end | 9.17 s | 9.10 s |
| Source read (NetBox) | 5.99 s | 6.43 s |
| Destination read (Infrahub) | 3.13 s | 3.21 s |
| `plan` command, submit to summary | 15.2 s | 13.0 s |

The two sides differ by less than half a second on each measure. Each side ran once, so this
check does not show whether that difference is larger than the difference between two runs
of the same commit.

### Operations, problems and left-out records

Both sides planned the same operations: 1,688 operations, 1,687 creates and one delete (the
`IpamNamespace` `default` node, which apply does not execute). This equals the count in
`testing-tiers.md`.

Operations per kind: `BuiltinTag` 26, `DcimCircuit` 29, `DcimDevice` 44, `DcimDeviceType` 14,
`DcimPlatform` 3, `InterfaceLag` 26, `InterfacePhysical` 1,119, `IpamAggregate` 4,
`IpamIPAddress` 180, `IpamNamespace` 7, `IpamPrefix` 90, `IpamRouteTarget` 12, `IpamVLAN` 24,
`IpamVLANGroup` 7, `IpamVRF` 6, `LocationRack` 42, `LocationSite` 24,
`OrganizationManufacturer` 14, `OrganizationProvider` 9, `OrganizationRIR` 8.

The after plan's summary:

- problems: 0, so there is no problem type to report
- left out: 0 (with a problem: 0, depending on one: 0)
- kept without a relationship: 0

`runs plan <run-id> --problems` and `runs plan <run-id> --left-out` exited 0 and printed
nothing, because both lists are empty.

### The #374 cases in the pinned dataset

These results come from queries on the NetBox database of the restored dump (72 devices) and
from the after plan.

- Two devices with the same name: the dataset holds one name used three times, `PP:MDF`, at
  three sites (Grinnells Lab, Butler Communications, D. S. Weaver Labs), each in a different
  rack. The mapping does not read them: both `DcimDevice` entries of `package.yml` leave out
  every device whose name contains `PP:` (6 devices). The plan therefore neither writes nor
  lists them, and `runs plan <run-id> --detail --kind DcimDevice` does not name them. The other
  44 named devices have distinct names, and 22 devices have no name and are left out by the
  mapping's filter. The plan has 44 `DcimDevice` creates, equal to these 44 devices. So the
  check does not show which problem type a same-named pair gets on live data; the unit test
  `tests/plan/test_problems.py::test_destination_identity_collision` remains the only
  evidence.
- Rack position that is not a whole number: no device has one. No `wrong_value_type` problem
  is reported.
- Device status other than `active`: all 72 devices are `active`. The `device_status`
  transform changes no value in this dataset, so the before and after plans are the same.

### Apply

The plan left out no records, so `apply` without `--confirm-left-out` was not refused and
applied the plan: the run ended with `phase: applied`, `outcome: applied`. The Prefect flow run
`Apply sync plan of from-netbox` took 63.3 s. There was nothing to confirm, so the check did
not run `apply --confirm-left-out` or `sync`, and it did not observe the refusal message.

Object count per kind on the `netbox-import` branch after apply (GraphQL `count` of each kind):
every kind equals its planned operations, 1,688 objects in total. `IpamNamespace` has 7
objects: the 6 created namespaces and the `default` namespace, which the delete operation
names and apply does not delete.

### Product bugs

None found.

### Teardown

Both preview stacks were removed with `preview.down --volumes` from the worktree that started
them, and NetBox with `netbox.down`. Before each command, the containers and volumes of its
Compose project were listed; none belonged to another project. Afterwards, no container or
volume of `infrahub-sync-preview-t072`, `infrahub-sync-preview-t072before` or
`infrahub-sync-netbox` remains. `netbox.seed` removes the `infrahub-sync-netbox` database
volume before it restores the dump, so the NetBox volume from an earlier run was replaced.
The `hivemind*` containers were not stopped or changed.

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

- four strict `xfail` tests in `tests/plan/test_warm_read_problems.py`, which recorded that a warm
  incremental read did not record problems (T081, deferred then; done on 2026-11-12, research
  R22, and the markers are removed);
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
This chunk does not start shared stacks, so T077 is not ticked: its offline half passed, and its live half needs the same stacks as T072. "T072 — from-netbox check" above lists the
commands that start them; that check ran on 2026-11-12 but did not run these quickstart steps.
When they run, the steps are:

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
