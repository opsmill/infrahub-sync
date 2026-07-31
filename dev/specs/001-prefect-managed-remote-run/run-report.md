# Run Report: Prefect-Managed Remote Infrahub Sync Run (Developer Preview)

Evidence log for feature `001-prefect-managed-remote-run`, branch
`001-prefect-managed-remote-run-local-dp-001`. Each phase appends its measured evidence
here; nothing in this file is aspirational — every number below was observed locally.

## Phase 1 — Inherited baseline (T003)

**Trace**: R-4, R-5, SC-006 (baseline non-regression clause).
**Measured**: 2026-07-31T04:02Z (UTC), immediately after commits 1 (T001) and 2 (T002).
**Environment**: Python 3.12.2, uv 0.7.6, darwin (macOS 25.5.0), dependencies installed
via `uv sync --extra dev` (the R-1 command). Baseline commit: `e04f262`.

This is the *inherited* state — it is what the branch starts from, not a target. SC-006
non-regression is judged against these numbers, so later phases must not worsen them.

### Test suite

```bash
uv run pytest -q
```

Verbatim result line:

```text
================== 111 passed, 2 skipped, 3 warnings in 5.30s ==================
```

**Discrepancy vs tasks.md T003.** The task text expects `110 passed, 3 skipped`; the
measured baseline in this worktree is **111 passed, 2 skipped**. Totals agree (113
collected either way) — one test that the task text assumed was skipped now runs and
passes. The measured value governs; flagged upward rather than reconciled by editing the
ratified task text.

### Formatters

```bash
uv run invoke format   # exit 0
```

The Python half is clean: `ruff format` reports `67 files left unchanged` and
`ruff check --fix` reports `All checks passed!` — **no diffs**, matching the expectation.

**Hazard — the docs half is destructive on this feature's own artifacts.**
`invoke format` runs `docs.format` → `rumdl fmt .` *before* the Python formatters, and
`rumdl fmt` rewrites files under `dev/specs/`. On this feature directory it corrupts
`tasks.md`: line 262 is the prose continuation

```text
#3 (D003) → T007/T008; #4 (D004) → T014/T016.
```

which `rumdl` misparses as an ATX heading and rewrites to `## 3 (D003) → T007/T008; #4
(D004) → T014/T016` — dropping the leading `#`, dropping the trailing period, inserting
blank lines, and then cascading a demotion of every subsequent heading (`##`→`###`,
`###`→`####`) through the rest of the file. It also strips one blank line from
`spec.md`. Both edits were **reverted**; the ratified text is intact at `e04f262`.

Consequence for later phases: running `uv run invoke format` as the workflow instructs
will silently damage `tasks.md` again. Use `uv run invoke linter.format` (the Python
formatters alone) while this feature directory is present, or restore
`dev/specs/**` afterwards. Escalated as a governance item; not fixed here (out of
Phase 1 scope, and the fix belongs either in `rumdl` config exclusions or in the
prose line itself).

### Linters

```bash
uv run invoke lint   # exit 30 — NOT exit 0
```

**Corrected inherited lint baseline — RATIFIED (D014, Blake Ellis, 2026-07-31).**
The T003 task text expects exit 0. That expectation is wrong: the measured inherited
baseline is **exit 30**, and it is **INHERITED**, not caused by this run. Cause: pylint
reports **29 × `C0415`** (`import-outside-toplevel`) across six modules plus
**1 × `E0213`** (`infrahub_sync/__init__.py:98` — `convert_str_to_enum` should take
`self`), rating **9.60/10**. Proof that it is inherited: `pylint infrahub_sync/` is
pylint's only target (`tasks/linter.py:41`), and `git diff main..HEAD -- infrahub_sync/`
is empty — this run does not touch that directory. The gate for later phases is therefore
**no regression against exit 30 with that diagnostic set**, not exit 0 (see T039).

**Ordering hazard that masked this.** `invoke lint` runs `docs.lint` (rumdl) FIRST with no
`warn=True`, so any Markdown lint issue anywhere aborts the chain before
ruff/pylint/yamllint/ty run at all. At first measurement rumdl failed on three Markdown
issues in this feature's own committed planning artifacts, which hid the pylint status
entirely (exit 1 from rumdl, the Python linters never reached):

```text
dev/specs/001-prefect-managed-remote-run/spec.md:161:1: [MD076] Unexpected blank line between list items
dev/specs/001-prefect-managed-remote-run/tasks.md:262:2: [MD018] No space after # in heading [*]
dev/specs/001-prefect-managed-remote-run/plan.md:265:1: [MD036] Emphasis used instead of a heading: '(empty — no constitution violations)'

Issues: Found 3 issues in 3/70 files (25ms)
```

These arrived with planning commit `a65f568` ("[Spec Kit] Apply ratified gate
decisions"); they are absent from `main`, which carries no `dev/specs/001-*` content.
Note that `MD018` on `tasks.md:262` is the same prose line `rumdl fmt` corrupts (above),
so "just autofix it" was not available. All three were fixed by hand in this run's own
spec artifacts, which is what exposed the pylint status; `uv run rumdl check .` is clean
and must stay clean, because the pylint assertion is meaningless otherwise.

With rumdl clean the chain reaches the Python linters and stops at pylint (exit 30
propagates), so yamllint and ty must be asserted by direct invocation. Measured
individually:

| Task | Exit | Result |
|---|---|---|
| `linter.lint-ruff` | 0 | `67 files already formatted`, all checks passed |
| `linter.lint-pylint` | **30** | 29 × `C0415`, rated **9.60/10** |
| `linter.lint-yaml` | 0 | clean |
| `linter.lint-ty` | 0 | `Found 3 diagnostics` |

**Pylint fails the gate independently of rumdl, and that is the corrected baseline
(exit 30, rating 9.60/10 — D014).** `tasks/linter.py` calls `context.run(exec_cmd)` with
no `warn=True`, so a non-zero pylint exit propagates. Exit 30 is the bitmask for the
convention/refactor/warning/error classes present below; the two codes that define the
ratified baseline set are `C0415` (29) and `E0213` (1). Full inherited message population
over `infrahub_sync/` — all of it inherited, none of it this run's:

| Code | Count | Meaning |
|---|---|---|
| `C0415` | 29 | `import-outside-toplevel` |
| `C0413` | 9 | `wrong-import-position` |
| `R0917` | 5 | `too-many-positional-arguments` |
| `W0613` | 4 | `unused-argument` |
| `R0915` | 2 | `too-many-statements` |
| `R0912` | 2 | `too-many-branches` |
| `W0707` | 1 | `raise-missing-from` |
| `R1720` | 1 | `no-else-raise` |
| `R1705` | 1 | `no-else-return` |
| `C0412` | 1 | `ungrouped-imports` |
| `E0213` | 1 | `no-self-argument` — `infrahub_sync/__init__.py:98`, `convert_str_to_enum` should take `self`; part of the ratified inherited baseline set (D014) |

Refinement of the task text: the `import-outside-toplevel` warnings are **not** confined
to `infrahub_sync/potenda/__init__.py` as T003 states. They span `__init__.py`,
`dependency_graph.py`, `cli.py`, `utils.py`, `cache/`, and `potenda/__init__.py`. All are
inherited and left untouched.

`ty` (exit 0, 3 diagnostics) — all three are the same rule, all in one test file:

```text
warning[unused-ignore-comment]: Unused `ty: ignore` directive
  --> tests/adapters/test_nautobot_incremental.py:59:50
  --> tests/adapters/test_nautobot_incremental.py:101:50
  --> tests/adapters/test_nautobot_incremental.py:122:50
```

This matches the expected "3 diagnostics, exit 0". No `[[tool.ty.overrides]]` blocks are
present in `pyproject.toml` (constitution IV).

### R-5 — inherited no-op timeout marker (do not fix)

`tests/test_potenda_parallel.py:70` carries `@pytest.mark.timeout(5)`, but
`pytest-timeout` is **not** installed (`importlib.util.find_spec("pytest_timeout")` is
`None`) and the mark is not registered, so the marker is a **silent no-op** — the test
has no enforced time bound. Observed every run as:

```text
tests/test_potenda_parallel.py:70: PytestUnknownMarkWarning: Unknown pytest.mark.timeout - is this a typo?
```

Per R-5 this is recorded and **deliberately left as-is**: do not fix the marker, and do
not add a `pytest-timeout` dependency. It is one of the 3 warnings in the baseline count.

### Phase 1 commit boundaries

| Commit | Task | Contents |
|---|---|---|
| `a586ff8` | T001 (R-1) | `AGENTS.md` only — `uv sync` → `uv sync --extra dev` at the Setup and Required Development Workflow blocks |
| `e04f262` | T002 (R-2) | `examples/netbox_to_infrahub/` only — 4 regenerated files, +400/−342 |

**D007 resolved by symlink.** T001 was to update `AGENTS.md` *and* the mirror
`.github/copilot-instructions.md`. That path is a git-tracked **symlink** (mode `120000`)
to `../AGENTS.md`, so the two are the same file and the single edit satisfies D007;
`grep -rn "^uv sync$" AGENTS.md .github/copilot-instructions.md` returns no matches.
`.cursor/rules/dev-standard.mdc` does not exist in this worktree (nor does `.cursor/`),
so it needed no change as anticipated. `CLAUDE.md` contains no `uv sync` line — it
delegates to `AGENTS.md` via `@AGENTS.md`.

Post-T001 verification: `uv sync --extra dev` succeeded and
`uv run pytest -q --collect-only` reported `111 tests collected in 0.47s`.

### T002 open item — `generate` output is not reproducible over time

T002's stated verification is "re-run the same command and confirm
`git status --porcelain examples/` is empty afterward". **This verification fails**, and
the cause is a pre-existing defect, not the regeneration itself.

`infrahub-sync generate` renders from the **live** Infrahub schema
(`cli.py`: `client.schema.all()`, server at `localhost:8000`), and two of the three
template loops iterate that mapping **unsorted**:

| Template | Line | Loop | Ordering |
|---|---|---|---|
| `diffsync_adapter.j2` | 6 | `schema.items()|sort()` | deterministic (import block) |
| `diffsync_adapter.j2` | 26 | `schema.items()` | **server response order** (class body) |
| `diffsync_models.j2` | 29 | `schema.items()` | **server response order** (whole file) |

So the emitted order tracks whatever order the Infrahub API returns, which holds one value
for a stretch of calls and then shifts. Observed: the committed run produced adapter md5
`a9b76fb…`; every subsequent run in this worktree produced `cd9fcda…` — a pure reordering
(242 insertions / 242 deletions, zero content change). Restoring the exact pre-T002 file
state and regenerating still yields `cd9fcda…`, so this is not
previous-file-state dependence; it is response-order dependence. Runs 2–6 were
byte-identical to each other.

`e04f262` therefore records one honest `generate` run (as mandated) but is no longer *the*
fixed point: a fresh `generate` here now produces a 242-line reordering diff. The fixed
point exists — it has simply moved since T002 was committed. Resolving this needs a
decision that exceeds this chunk's authority, because the options differ in scope and
blast radius:

- **A** — add `|sort()` to `diffsync_adapter.j2:26` and `diffsync_models.j2:29`, making
  `generate` deterministic for every user. A real bug fix, but it is source change
  outside Phase 1 and it rewrites generated output repo-wide.
- **B** — commit the current stable fixed point as a follow-up regeneration commit.
  **Viable**: cheap and reversible, and it does yield a clean tree. But it splits T002
  across two commits (the plan binds commit 2 to T002 "alone"), it enshrines one server's
  ordering, and the clean tree lasts only until the order shifts again.
- **C** — leave `e04f262` as the baseline-hygiene commit and accept that T002's re-run
  check cannot pass until A lands.

**Resolved — RATIFIED (D014, option C, Blake Ellis, 2026-07-31).** Disposition is
**report, do not fix**: `infrahub-sync generate` is not idempotent over time, and this is
an **INHERITED** defect (`git diff main..HEAD -- infrahub_sync/generator/` is empty, so
the behavior comes from `main`).

**Correction — root's "no fixed point" measurement was malformed and is withdrawn.** An
earlier pass recorded here that root's later measurement superseded the "runs 2–6
byte-identical" observation above, and that there was **no stable fixed point** because
two consecutive `generate` runs differed from each other. That supersession is
**reversed**: the measurement compared a flattened directory copy in which
`infrahub/sync_adapter.py` collided with `netbox/sync_adapter.py`, and the collision — not
the generator — produced the spurious diff. **The worker's observation stands.** Root
re-ran `generate --name from-netbox --directory examples/` three consecutive times and
verified pairwise that run1 == run2 == run3, **byte-identical across all four generated
files**, independently confirming the "runs 2–6 byte-identical" finding.

**The fixed point exists but MOVES.** The committed T002 output (`e04f262`) differs from
today's stable output by **484 lines across 4 files** (22 + 220 + 22 + 220 — the same
churn as the 242-insertion / 242-deletion count above, counted as diff lines). The emitted
order therefore held one value when T002 was generated and committed, and a different —
internally stable — value hours later. The order is **stable within a window and shifts
across windows**. The cause of the shift is **unverified**; most plausibly a cold-vs-warm
server schema cache or a schema reload. It was not tested, because testing it would
require restarting the lab containers, which is out of bounds for this run.

**Why still C, given that.** Option **B** is **viable, not impossible** — committing the
current fixed point does produce a clean tree. B was re-offered to the decision-maker
after root corrected its error, and was **declined**: the clean tree holds only until the
order shifts again, at which point a clean-tree gate silently starts failing for someone
who changed nothing. A gate that passes today and fails later without anyone touching the
generator is worse than one documented as failing — it hides itself. **C** never pretends
the defect is fixed, so C remains ratified.

Unchanged and still correct: the churn is **ordering only**, at two levels (generated
model-block order, and member order inside `_attributes` tuples), with **no content lost**
(20 models and 20 classes in the committed state and in every fresh run, matching
attribute multisets), and no known correctness impact (diffsync matches attributes by
name; this feature's canonical plan fingerprint per D001 sorts plan rows explicitly, so it
is order-insensitive). Option **A** (sorting in the generator, then regenerating all
checked-in examples) is **deliberately deferred to a separate PR**, tracked as
`bug-generate-output-is-not-deterministic-across-runs.md` in the planning repo's
proposed-issues directory. T002 keeps its completed status and its commit `e04f262`;
T041 records the ~242-line churn relative to whatever is committed and restores it with
`git checkout -- examples/netbox_to_infrahub/` rather than committing it.

## Phase 3 (US1) live verification — T018 + T019

**Trace**: DBA-002 (T018); DBA-003 + DBA-004 (T019); DBR-001, DBR-002, DBR-003, DBR-011,
DBR-012; SC-001, SC-002.
**Measured**: 2026-07-31T10:55Z → 2026-07-31T11:00Z (UTC), on branch
`001-prefect-managed-remote-run-local-dp-001`.
**Environment**: darwin (macOS 25.5.0), Python 3.12, `uv sync --extra dev --extra prefect`
(`Resolved 180 packages`, `Audited 171 packages`), `prefect 3.8.1` / `redis 8.1.0`
confirmed by `uv run python -c "import prefect, redis; print(prefect.__version__, redis.__version__)"`.
Infrahub 1.9.8 at `http://localhost:8000` with `InfraDevice(name, type)` on `main`.
`INFRAHUB_ADDRESS` / `INFRAHUB_API_TOKEN` supplied from the session environment only —
neither value appears in any file, parameter, request body, or log line below.
`PREFECT_HOME` was pointed at a scratchpad directory
(`…/scratchpad/prefect-home-t018`) so the developer's real `~/.prefect` was never touched.

This section records only what was observed. Nothing was written to the destination in
either task: both are read-only (`operation=plan`). T021's confirmed write is a later task.

### Preconditions

Port 4200 free before starting anything:

```text
$ date -u +%Y-%m-%dT%H:%M:%SZ ; lsof -nP -iTCP:4200 -sTCP:LISTEN
2026-07-31T10:55:36Z
PORT4200_FREE
```

Destination at zero `InfraDevice` objects, read-only GraphQL against `main`
(token passed as a header from the environment, never printed):

```text
$ # POST $INFRAHUB_ADDRESS/graphql/main  {"query": "{ InfraDevice { count edges { node { name { value } } } } }"}
200 {
  "data": {
    "InfraDevice": {
      "count": 0,
      "edges": []
    }
  }
}
```

Prefect state was reset to empty before the run by pointing `PREFECT_HOME` at a **fresh**
scratchpad directory (an earlier planning-probe home held an unrelated
`probe-deployment`, so it was set aside rather than reused, to keep this evidence
unambiguous). Immediately after `prefect server start` against the fresh home:

```text
--- deployments ---   []
--- flow_runs count ---   0
--- work pools ---   []
```

### T018 — DBA-002: default server + locally served deployment, no external database, no worker

Documented commands, run verbatim from `quickstart.md` Scenario 1.

Terminal A (server), started 2026-07-31T10:56:58Z, healthy 2026-07-31T10:57:04Z:

```bash
export PREFECT_HOME=".../scratchpad/prefect-home-t018"    # test isolation only
export PREFECT_API_URL="http://127.0.0.1:4200/api"
uv run prefect server start
```

```text
 ___ ___ ___ ___ ___ ___ _____
| _ \ _ \ __| __| __/ __|_   _|
|  _/   / _|| _|| _| (__  | |
|_| |_|_\___|_| |___\___| |_|

Configure Prefect to communicate with the server with:

    prefect config set PREFECT_API_URL=http://127.0.0.1:4200/api

View the API reference documentation at http://127.0.0.1:4200/docs

Check out the dashboard at http://127.0.0.1:4200

$ curl -s $PREFECT_API_URL/health
true
```

**No external database.** The server used its default embedded SQLite, created inside
`PREFECT_HOME` by the start itself:

```text
$ uv run python -c "from prefect.settings import get_current_settings; \
    print(get_current_settings().server.database.connection_url.get_secret_value())"
sqlite+aiosqlite:////private/tmp/claude-501/.../scratchpad/prefect-home-t018/prefect.db
sqlite: True
inside scratchpad PREFECT_HOME: True

$ ls -la "$PREFECT_HOME"
-rw-r--r--  1 blake  wheel   737280 Jul 31 06:57 prefect.db
-rw-r--r--  1 blake  wheel    32768 Jul 31 06:57 prefect.db-shm
-rw-r--r--  1 blake  wheel  2587392 Jul 31 06:57 prefect.db-wal
```

No Postgres or other database process or container was started for Prefect. `docker ps`
before and after showed the same eight `infrahub-*` lab containers, all `Up 23–24 hours`
(`infrahub-database-1` is Infrahub's own database, pre-existing and untouched — no
container was stopped, restarted, modified, or exec'd into at any point).

Terminal B (served deployment), started 2026-07-31T10:57:12Z from the repository root:

```bash
# from the repository root — cwd=/Users/blake/repos/opsmill/infrahub-sync-dev-preview
export PREFECT_API_URL="http://127.0.0.1:4200/api"
export INFRAHUB_SYNC_CONFIG_DIRECTORY="$PWD/examples/custom_adapter"
# INFRAHUB_ADDRESS / INFRAHUB_API_TOKEN already present in the session environment
uv run python -m infrahub_sync.orchestration.serve
```

```text
Your flow 'infrahub-sync' is being served and polling for scheduled runs!

To trigger a run for this flow, use the following command:

        $ prefect deployment run 'infrahub-sync/run'

You can also run your flow via the Prefect UI: http://127.0.0.1:4200/deployments/deployment/44f5e3ac-8df4-4184-acf1-8fab42e8618a
```

`INFRAHUB_SYNC_CONFIG_DIRECTORY` was scoped to `examples/custom_adapter` per X6 — exactly
the qualified `custom-example` configuration, not all fourteen example configs.

Deployment record, 2026-07-31T10:57:40Z:

```text
$ curl -s -w "HTTP %{http_code}\n" "$PREFECT_API_URL/deployments/name/infrahub-sync/run"
HTTP 200
id: '44f5e3ac-8df4-4184-acf1-8fab42e8618a'
name: 'run'
flow_id: '94a4f54f-373f-452c-a6d0-700eec14d8dc'
status: 'READY'
enforce_parameter_schema: True
work_pool_name: None
work_queue_name: None
paused: False
entrypoint: 'infrahub_sync/orchestration/flow.py:infrahub_sync_run'
path: '.'
parameter_openapi_schema.properties:
{
  "sync_name":      {"position": 0, "title": "sync_name", "type": "string"},
  "operation":      {"default": "plan", "enum": ["plan", "sync"], "position": 1, "title": "operation", "type": "string"},
  "confirm_writes": {"default": false, "position": 2, "title": "confirm_writes", "type": "boolean"},
  "branch":         {"anyOf": [{"type": "string"}, {"type": "null"}], "default": null, "position": 3, "title": "branch"}
}
required: ['sync_name']
```

`status: "READY"` and `enforce_parameter_schema: true` as the contract requires
(contracts/prefect-flow.md §5), the four-parameter contract intact (DBR-003), and
`operation` carrying `enum: ["plan", "sync"]`. The deployment-name lookup path
`/deployments/name/infrahub-sync/run` (deployment named `run`, X12) returned 200 —
confirming the fastapi-0.141.x route behavior D006 was withdrawn over.

**No worker service.** `work_pool_name` and `work_queue_name` are `None`,
`POST /work_pools/filter` returned `[]`, and no worker process existed:

```text
$ pgrep -fl "prefect worker"
no 'prefect worker' process

$ ps -Ao pid,command | grep -iE "prefect|orchestration.serve"
 537 uv run prefect server start
 549 .../.venv/bin/python3 .../.venv/bin/prefect server start
 617 uv run python -m infrahub_sync.orchestration.serve
 621 .../.venv/bin/python3 -m infrahub_sync.orchestration.serve
```

Exactly two things were running: the server and the serve process. Nothing else.

**Serve-start validation failure**, demonstrated twice at 2026-07-31T10:56:25Z — one
error line naming the variable, non-zero exit, before any deployment is served (spec
clarification #2), and no traceback:

```text
$ env -u INFRAHUB_SYNC_CONFIG_DIRECTORY uv run python -m infrahub_sync.orchestration.serve
INFRAHUB_SYNC_CONFIG_DIRECTORY is not set: point it at the directory holding your sync configurations
exit=1

$ INFRAHUB_SYNC_CONFIG_DIRECTORY="$PWD/examples/custom_adapter/config.yml" \
    uv run python -m infrahub_sync.orchestration.serve
INFRAHUB_SYNC_CONFIG_DIRECTORY='/Users/blake/repos/opsmill/infrahub-sync-dev-preview/examples/custom_adapter/config.yml' is not an existing directory
exit=1
```

Neither refusal registered a deployment (the deployment list was still `[]` afterward).

**T018 result: PASS.** DBA-002 satisfied — the documented commands worked as written, with
the default embedded SQLite database, the built-in UI, and no work pool, no worker, and no
external database service.

### T019 — DBA-003 + DBA-004: remote `operation=plan` run

Documented REST calls from `quickstart.md` Scenario 1, Terminal C.

```bash
DEP_ID=$(curl -s "$PREFECT_API_URL/deployments/name/infrahub-sync/run" | jq -r .id)
curl -s -w "HTTP %{http_code}\n" -X POST "$PREFECT_API_URL/deployments/$DEP_ID/create_flow_run" \
  -H "Content-Type: application/json" \
  -d '{"parameters": {"sync_name": "custom-example", "operation": "plan"}}'
```

Response at 2026-07-31T10:57:56Z — the flow-run id arrives **synchronously** (SC-001):

```text
DEP_ID=44f5e3ac-8df4-4184-acf1-8fab42e8618a
HTTP 201
{
  "id": "6b1f21ba-f73d-4dc1-81c8-9b4d4f7658a9",
  "name": "imaginary-pudu",
  "state_type": "SCHEDULED",
  "state_name": "Scheduled",
  "parameters": {"sync_name": "custom-example", "operation": "plan"},
  "deployment_id": "44f5e3ac-8df4-4184-acf1-8fab42e8618a"
}
```

**Flow-run id: `6b1f21ba-f73d-4dc1-81c8-9b4d4f7658a9`** (`imaginary-pudu`).

Observed state transitions, from `GET /flow_run_states/?flow_run_id=$RUN_ID`:

| Timestamp (UTC) | type | name |
|---|---|---|
| 2026-07-31T10:57:56.387669Z | SCHEDULED | Scheduled |
| 2026-07-31T10:58:02.606154Z | PENDING | Pending |
| 2026-07-31T10:58:02.616371Z | PENDING | Submitting |
| 2026-07-31T10:58:03.994858Z | RUNNING | Running |
| 2026-07-31T10:58:04.606699Z | COMPLETED | Completed |

Polling `GET /flow_runs/{id}` showed the same progression
(`SCHEDULED` → `RUNNING` at 10:58:04Z → `COMPLETED` at 10:58:06Z). Flow-run record:

```text
$ curl -s "$PREFECT_API_URL/flow_runs/6b1f21ba-f73d-4dc1-81c8-9b4d4f7658a9"
{
  "id": "6b1f21ba-f73d-4dc1-81c8-9b4d4f7658a9",
  "name": "imaginary-pudu",
  "deployment_id": "44f5e3ac-8df4-4184-acf1-8fab42e8618a",
  "flow_version": "712c349b4489879f385e32fa6f8be9e6",
  "state_type": "COMPLETED",
  "state_name": "Completed",
  "state_message": null,
  "start_time": "2026-07-31T10:58:03.994858Z",
  "end_time": "2026-07-31T10:58:04.606699Z",
  "total_run_time": 0.611841,
  "parameters": {"sync_name": "custom-example", "operation": "plan"},
  "work_pool_name": null,
  "infrastructure_pid": null
}
```

Total run time 0.61 s (the ~6 s from create to RUNNING is the serve process's scheduled-run
poll interval, not execution).

#### Log lines retrieved through the Prefect API (DBA-004)

`POST /api/logs/filter` body
`{"logs": {"flow_run_id": {"any_": ["6b1f21ba-f73d-4dc1-81c8-9b4d4f7658a9"]}}, "limit": 200, "sort": "TIMESTAMP_ASC"}`
returned 14 records. Verbatim, `timestamp [level] name :: message` (level `20` = INFO,
preserved by the bridge):

```text
2026-07-31T10:58:04.010097Z [20] prefect.flow_runs :: Beginning flow run 'imaginary-pudu' for flow 'infrahub-sync'
2026-07-31T10:58:04.361186Z [20] prefect.flow_runs :: infrahub_sync | tier 0 (1): ['InfraDevice']
2026-07-31T10:58:04.362687Z [20] prefect.flow_runs :: infrahub_sync.potenda | Potenda tier 0 (1): ['InfraDevice']
2026-07-31T10:58:04.363877Z [20] prefect.flow_runs :: infrahub_sync.potenda | Load: Importing data from MockDB
2026-07-31T10:58:04.364447Z [20] prefect.flow_runs :: infrahub_sync.potenda | Load: Importing data from Infrahub
2026-07-31T10:58:04.552434Z [20] prefect.flow_runs :: infrahub_sync.cache.incremental | Incremental disabled: --full-extract requested
2026-07-31T10:58:04.553251Z [20] prefect.flow_runs :: infrahub_sync.cache.incremental | Incremental disabled: --full-extract requested
2026-07-31T10:58:04.596089Z [20] prefect.flow_runs :: infrahub_sync.adapters.infrahub | Infrahub: Loading all 0 InfraDevice
2026-07-31T10:58:04.598205Z [20] prefect.flow_runs :: infrahub_sync.potenda | Diff: Comparing data from MockDB to Infrahub
2026-07-31T10:58:04.599364Z [20] prefect.flow_runs :: infrahub_sync.potenda | diff: 5/5 models processed
2026-07-31T10:58:04.600916Z [20] prefect.flow_runs :: infrahub_sync.execution |
InfraDevice
  InfraDevice: core01 MISSING in Infrahub
  InfraDevice: core02 MISSING in Infrahub
  InfraDevice: core03 MISSING in Infrahub
  InfraDevice: edge01 MISSING in Infrahub
  InfraDevice: edge02 MISSING in Infrahub
2026-07-31T10:58:04.602074Z [20] prefect.flow_runs :: infrahub_sync.execution | Cached run 20260731T1058-07e1e25e at /Users/blake/repos/opsmill/infrahub-sync-dev-preview/.infrahub-sync-cache/custom-example/20260731T1058-07e1e25e
2026-07-31T10:58:04.602878Z [20] prefect.flow_runs :: run 20260731T1058-07e1e25e finished: status=planned changed=True summary=create:5,update:0,delete:0 artifact=/Users/blake/repos/opsmill/infrahub-sync-dev-preview/.infrahub-sync-cache/custom-example/20260731T1058-07e1e25e
2026-07-31T10:58:05.060643Z [20] prefect.flow_runs :: Finished in state Completed()
```

This is DBA-004 in full: the run lifecycle lines Prefect emits itself (`Beginning flow
run …`, `Finished in state Completed()`) **and** eleven bridged `infrahub_sync` lifecycle
lines, each carrying its origin logger name — `infrahub_sync`,
`infrahub_sync.potenda`, `infrahub_sync.cache.incremental`,
`infrahub_sync.adapters.infrahub`, `infrahub_sync.execution` — across the full
load → diff → plan → cache sequence, at INFO. The engine was not modified to produce
them: `RunLoggerBridge` forwards what `potenda` and the adapters already log, with the
flow owning the `infrahub_sync` logger's level for the duration of the run (E4). No
credential, token, or URL-with-secret appears in any line.

The summary line, verbatim (contracts/prefect-flow.md §2 step 3 fixed key=value format,
the supported remote observation surface; leading `%s` is `result.run_id` per X18):

```text
run 20260731T1058-07e1e25e finished: status=planned changed=True summary=create:5,update:0,delete:0 artifact=/Users/blake/repos/opsmill/infrahub-sync-dev-preview/.infrahub-sync-cache/custom-example/20260731T1058-07e1e25e
```

**`summary=create:5,update:0,delete:0`** — five creates, zero updates, zero deletes.
`artifact_path` = `/Users/blake/repos/opsmill/infrahub-sync-dev-preview/.infrahub-sync-cache/custom-example/20260731T1058-07e1e25e`.

**Five creates, not zero.** The X1/E11 CWD hazard did not trigger, because the serve
process was started from the repository root: the `./`-relative adapter spec and
`db_path` in `examples/custom_adapter/config.yml` resolved, `Load: Importing data from
MockDB` and `diff: 5/5 models processed` appear in the log, and the plan names all five
fixture devices individually. A zero-create "success" would have been treated as a
failure to investigate, not a pass.

#### Runner-local run directory

```text
$ ls -la .infrahub-sync-cache/custom-example/20260731T1058-07e1e25e
drwxr-xr-x  3 blake  staff    96 Jul 31 06:58 A
drwxr-xr-x  3 blake  staff    96 Jul 31 06:58 B
-rw-r--r--  1 blake  staff  2691 Jul 31 06:58 plan.parquet
-rw-------  1 blake  staff   135 Jul 31 06:58 run.json
-rw-------  1 blake  staff    12 Jul 31 06:58 schema-sub-hash.txt

$ jq . .infrahub-sync-cache/custom-example/20260731T1058-07e1e25e/run.json
{
  "finished_at": "2026-07-31T10:58:04.601639+00:00",
  "mode": "diff",
  "status": "dry-run",
  "summary": {
    "resources": 1
  }
}
```

`status: dry-run`, `mode: diff` — exactly as T019 requires. (`run.json`'s `summary` is the
cache layer's own resource count, `1` for the single `InfraDevice` resource; the per-action
create/update/delete counts are the `RunResult` summary carried on the flow's summary
line.)

`plan.parquet` — five rows, all `action=create`, one per fixture device:

```text
num_rows: 5
columns: ['action', 'resource', 'source_id', 'dest_id', 'attribute', 'old_value', 'new_value', 'owner', 'skip_reason', 'conflict_class']
{'action': 'create', 'resource': 'InfraDevice', 'source_id': 'core01', 'dest_id': '', 'attribute': '', 'old_value': '', 'new_value': '{"type": "juniper mx204"}',    'owner': '', 'skip_reason': '', 'conflict_class': ''}
{'action': 'create', 'resource': 'InfraDevice', 'source_id': 'core02', 'dest_id': '', 'attribute': '', 'old_value': '', 'new_value': '{"type": "arista 7504"}',      'owner': '', 'skip_reason': '', 'conflict_class': ''}
{'action': 'create', 'resource': 'InfraDevice', 'source_id': 'core03', 'dest_id': '', 'attribute': '', 'old_value': '', 'new_value': '{"type": "cisco asr9001"}',    'owner': '', 'skip_reason': '', 'conflict_class': ''}
{'action': 'create', 'resource': 'InfraDevice', 'source_id': 'edge01', 'dest_id': '', 'attribute': '', 'old_value': '', 'new_value': '{"type": "cisco nexus9000"}',  'owner': '', 'skip_reason': '', 'conflict_class': ''}
{'action': 'create', 'resource': 'InfraDevice', 'source_id': 'edge02', 'dest_id': '', 'attribute': '', 'old_value': '', 'new_value': '{"type": "cisco nexus9000"}',  'owner': '', 'skip_reason': '', 'conflict_class': ''}
```

#### Destination untouched

Re-queried after the run completed:

```text
POST-RUN destination InfraDevice count: 0
```

The plan changed nothing at the destination, as a read-only `operation=plan` must not.

#### Visible in the UI

`GET http://127.0.0.1:4200/` returned `HTTP 200 (text/html; charset=utf-8)`. The flow-run
page at `http://127.0.0.1:4200/runs/flow-run/6b1f21ba-f73d-4dc1-81c8-9b4d4f7658a9` was
loaded in a browser and rendered, titled
`Flow Run: imaginary-pudu • Prefect Server`, showing:

- breadcrumb `Runs / imaginary-pudu`, state badge **Completed**, duration `1s`,
  start `2026/07/31 06:58:03 AM` (local)
- Flow `infrahub-sync` → `/flows/flow/94a4f54f-373f-452c-a6d0-700eec14d8dc`
- Deployment `run` → `/deployments/deployment/44f5e3ac-8df4-4184-acf1-8fab42e8618a`
- the Logs tab listing all 14 records at `INFO` under `prefect.flow_runs`, including every
  bridged `infrahub_sync…` line and the summary line
  `run 20260731T1058-07e1e25e finished: status=planned changed=True summary=create:5,update:0,delete:0 artifact=…`

Equivalent API state listing (`POST /flow_runs/filter`, the UI's own backing call):

```text
imaginary-pudu 6b1f21ba-f73d-4dc1-81c8-9b4d4f7658a9 COMPLETED deployment=44f5e3ac-8df4-4184-acf1-8fab42e8618a
```

**T019 result: PASS.** DBA-003 satisfied — remote `operation=plan` returned a flow-run id
synchronously (HTTP 201), reached COMPLETED, and produced a plan of exactly five
`InfraDevice` creates with zero updates and zero deletes, backed by `run.json`
(`status: dry-run`, `mode: diff`) and a five-row `plan.parquet`. DBA-004 satisfied — the
run lifecycle and the bridged `infrahub_sync` lifecycle log lines are retrievable through
`POST /api/logs/filter` with levels and origin logger names preserved, and are visible in
the built-in UI.

No live-environment ceiling applies to T018 or T019: everything both tasks require was
exercised against the real lab.

### Teardown

Both processes this phase started were stopped at 2026-07-31T10:59:43Z:

```text
$ pkill -f "infrahub_sync.orchestration.serve"    # serve, pids 617/621
$ pkill -f "prefect server start"                 # server, pids 537/549
$ lsof -nP -iTCP:4200 -sTCP:LISTEN
PORT 4200 FREE
$ ps -Ao pid,command | grep -iE "prefect|orchestration.serve"
none
$ git status --porcelain
(clean)
```

No lab container was stopped, restarted, modified, or exec'd into. Nothing was written to
the destination. Prefect state stayed entirely inside the scratchpad `PREFECT_HOME`; the
developer's `~/.prefect` was never read or written.
