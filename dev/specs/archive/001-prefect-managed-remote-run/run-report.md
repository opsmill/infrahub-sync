# Run Report: Prefect-Managed Remote Infrahub Sync Run

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
# from the repository root — cwd=<repo-root>
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
INFRAHUB_SYNC_CONFIG_DIRECTORY='<repo-root>/examples/custom_adapter/config.yml' is not an existing directory
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
2026-07-31T10:58:04.602074Z [20] prefect.flow_runs :: infrahub_sync.execution | Cached run 20260731T1058-07e1e25e at <repo-root>/.infrahub-sync-cache/custom-example/20260731T1058-07e1e25e
2026-07-31T10:58:04.602878Z [20] prefect.flow_runs :: run 20260731T1058-07e1e25e finished: status=planned changed=True summary=create:5,update:0,delete:0 artifact=<repo-root>/.infrahub-sync-cache/custom-example/20260731T1058-07e1e25e
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
run 20260731T1058-07e1e25e finished: status=planned changed=True summary=create:5,update:0,delete:0 artifact=<repo-root>/.infrahub-sync-cache/custom-example/20260731T1058-07e1e25e
```

**`summary=create:5,update:0,delete:0`** — five creates, zero updates, zero deletes.
`artifact_path` = `<repo-root>/.infrahub-sync-cache/custom-example/20260731T1058-07e1e25e`.

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

---

## Phase 4 (US2) live verification — T021

**Trace**: DBA-005; SC-003; DBR-003, DBR-004; Constitution I/II.
**Measured**: 2026-07-31T11:10Z → 2026-07-31T11:13Z (UTC), on branch
`001-prefect-managed-remote-run-local-dp-001`.
**Environment**: darwin (macOS 25.5.0), Python 3.12.2,
`uv sync --extra dev --extra prefect` (`Resolved 180 packages`, `Audited 171 packages`).
Infrahub 1.9.8 at `http://localhost:8000` with `InfraDevice(name, type)` on `main`.
`INFRAHUB_ADDRESS` / `INFRAHUB_API_TOKEN` supplied from the session environment only —
neither value appears in any file, parameter, request body, or log line below.
`PREFECT_HOME` pointed at a fresh scratchpad directory
(`…/scratchpad/prefect-home-t021`), so the developer's `~/.prefect` was never touched.

**This is the first and only task in this delivery that WRITES to the destination** —
that write is exactly what DBA-005 verifies. The two runs below were submitted strictly
sequentially: the sync run was terminal (`COMPLETED` at 11:11:53.99Z) before the
follow-up plan run was created (11:12:35Z), so no two destination-writing runs ever
overlapped (spec edge case 5).

### Preconditions

Port 4200 free and no stray processes before starting anything:

```text
$ date -u +%Y-%m-%dT%H:%M:%SZ ; lsof -nP -iTCP:4200 -sTCP:LISTEN ; pgrep -fl "prefect|orchestration.serve"
2026-07-31T11:10:44Z
PORT4200_FREE
NO_PREFECT_PROCS
```

**Destination at zero `InfraDevice` objects — verified, NOT reset.** Read-only GraphQL
against `main` (token passed as a header from the environment, never printed):

```text
$ # POST $INFRAHUB_ADDRESS/graphql/main  {"query": "{ InfraDevice { count edges { node { name { value } type { value } } } } }"}
2026-07-31T11:10:49Z
HTTP 200
{
  "data": {
    "InfraDevice": {
      "count": 0,
      "edges": []
    }
  }
}
```

The destination was already empty, so **nothing was deleted** — the authorised
disposable-destination reset was not needed and was not performed.

Prefect state empty immediately after `prefect server start` against the fresh
`PREFECT_HOME` (server healthy at 2026-07-31T11:11:11Z):

```text
--- deployments ---   []
--- flow_runs count ---   0
--- work pools ---   []
```

Serve process started **from the repository root** (the X1/E11 CWD hazard: the fixture's
`./`-relative adapter spec and `db_path` resolve against the serving process's CWD), with
the configuration directory scoped to the qualified fixture:

```bash
export INFRAHUB_SYNC_CONFIG_DIRECTORY="$PWD/examples/custom_adapter"
uv run python -m infrahub_sync.orchestration.serve
```

Deployment READY at 2026-07-31T11:11:32Z from
`GET /api/deployments/name/infrahub-sync/run`:

```text
{
  "id": "466d904b-f014-4d46-ad43-01a8cf7179cd",
  "name": "run",
  "status": "READY",
  "enforce_parameter_schema": true,
  "work_pool_name": null
}
```

### Step 1 — remote `operation=sync` with `confirm_writes=true`

```bash
curl -s -w "\nHTTP %{http_code}\n" -X POST "$PREFECT_API_URL/deployments/$DEP_ID/create_flow_run" \
  -H "Content-Type: application/json" \
  -d '{"parameters": {"sync_name": "custom-example", "operation": "sync", "confirm_writes": true}}'
```

Response at 2026-07-31T11:11:43Z — the flow-run id arrives **synchronously**:

```text
HTTP 201
{
  "id": "57e6cfeb-46e3-4b9b-a209-bd0b82b1b85c",
  "name": "evasive-kelpie",
  "state_type": "SCHEDULED",
  "state_name": "Scheduled",
  "parameters": {"sync_name": "custom-example", "operation": "sync", "confirm_writes": true},
  "deployment_id": "466d904b-f014-4d46-ad43-01a8cf7179cd"
}
```

**Sync flow-run id: `57e6cfeb-46e3-4b9b-a209-bd0b82b1b85c`** (`evasive-kelpie`).

State transitions, from `GET /flow_run_states/?flow_run_id=$RUN_ID`:

| Timestamp (UTC) | type | name |
|---|---|---|
| 2026-07-31T11:11:43.725375Z | SCHEDULED | Scheduled |
| 2026-07-31T11:11:50.959597Z | PENDING | Pending |
| 2026-07-31T11:11:50.972560Z | PENDING | Submitting |
| 2026-07-31T11:11:52.179872Z | RUNNING | Running |
| 2026-07-31T11:11:53.990084Z | COMPLETED | Completed |

Flow-run record — `state_type: COMPLETED`, `state_message: null`, no worker and no work
pool (the served process ran it in-process):

```text
{
  "id": "57e6cfeb-46e3-4b9b-a209-bd0b82b1b85c",
  "name": "evasive-kelpie",
  "flow_version": "d4c26edc762e8aeb717cb8107873661c",
  "state_type": "COMPLETED",
  "state_name": "Completed",
  "state_message": null,
  "start_time": "2026-07-31T11:11:52.179872Z",
  "end_time": "2026-07-31T11:11:53.990084Z",
  "total_run_time": 1.810212,
  "parameters": {"sync_name": "custom-example", "operation": "sync", "confirm_writes": true},
  "work_pool_name": null,
  "infrastructure_pid": null
}
```

`POST /api/logs/filter` returned 17 records. Verbatim, `timestamp [level] name :: message`
(level `20` = INFO, preserved by the bridge):

```text
2026-07-31T11:11:52.194282Z [20] prefect.flow_runs :: Beginning flow run 'evasive-kelpie' for flow 'infrahub-sync'
2026-07-31T11:11:52.532300Z [20] prefect.flow_runs :: infrahub_sync | tier 0 (1): ['InfraDevice']
2026-07-31T11:11:52.533721Z [20] prefect.flow_runs :: infrahub_sync.potenda | Potenda tier 0 (1): ['InfraDevice']
2026-07-31T11:11:52.534788Z [20] prefect.flow_runs :: infrahub_sync.potenda | Load: Importing data from MockDB
2026-07-31T11:11:52.535403Z [20] prefect.flow_runs :: infrahub_sync.potenda | Load: Importing data from Infrahub
2026-07-31T11:11:52.711118Z [20] prefect.flow_runs :: infrahub_sync.cache.incremental | Incremental disabled: --full-extract requested
2026-07-31T11:11:52.711808Z [20] prefect.flow_runs :: infrahub_sync.cache.incremental | Incremental disabled: --full-extract requested
2026-07-31T11:11:52.733346Z [20] prefect.flow_runs :: infrahub_sync.adapters.infrahub | Infrahub: Loading all 0 InfraDevice
2026-07-31T11:11:52.739127Z [20] prefect.flow_runs :: infrahub_sync.potenda | Diff: Comparing data from MockDB to Infrahub
2026-07-31T11:11:52.740458Z [20] prefect.flow_runs :: infrahub_sync.potenda | diff: 5/5 models processed
2026-07-31T11:11:52.742648Z [20] prefect.flow_runs :: infrahub_sync.execution |
InfraDevice
  InfraDevice: core01 MISSING in Infrahub
  InfraDevice: core02 MISSING in Infrahub
  InfraDevice: core03 MISSING in Infrahub
  InfraDevice: edge01 MISSING in Infrahub
  InfraDevice: edge02 MISSING in Infrahub
2026-07-31T11:11:52.743369Z [20] prefect.flow_runs :: infrahub_sync.potenda | Sync: Importing data from MockDB to Infrahub based on Diff
2026-07-31T11:11:53.982669Z [20] prefect.flow_runs :: infrahub_sync.potenda | sync: 5/5 models processed
2026-07-31T11:11:53.983615Z [20] prefect.flow_runs :: infrahub_sync.execution | Sync: Completed in 1.240181874949485 sec
2026-07-31T11:11:53.985119Z [20] prefect.flow_runs :: infrahub_sync.execution | Sync run 20260731T1111-3290d1b4 at <repo-root>/.infrahub-sync-cache/custom-example/20260731T1111-3290d1b4
2026-07-31T11:11:53.986137Z [20] prefect.flow_runs :: run 20260731T1111-3290d1b4 finished: status=applied changed=True summary=create:5,update:0,delete:0 artifact=<repo-root>/.infrahub-sync-cache/custom-example/20260731T1111-3290d1b4
2026-07-31T11:11:54.224198Z [20] prefect.flow_runs :: Finished in state Completed()
```

The serial-sync lifecycle is visible end to end: `Load` → the five-`MISSING` diff →
`Sync: Importing data from MockDB to Infrahub based on Diff` → `sync: 5/5 models
processed` → the timing line (`Sync: Completed in …`, emitted only because the diff had
changes) → the cache line.

**Summary line, verbatim** (contracts/prefect-flow.md §2 step 3 fixed key=value format;
leading `%s` is `result.run_id` per X18):

```text
run 20260731T1111-3290d1b4 finished: status=applied changed=True summary=create:5,update:0,delete:0 artifact=<repo-root>/.infrahub-sync-cache/custom-example/20260731T1111-3290d1b4
```

`status=applied`, `changed=True`, **`summary=create:5,update:0,delete:0`** — exactly the
DBA-005 expectation. Runner-local run directory:

```text
$ ls -la .infrahub-sync-cache/custom-example/20260731T1111-3290d1b4
drwxr-xr-x  3 blake  staff    96 Jul 31 07:11 A
drwxr-xr-x  3 blake  staff    96 Jul 31 07:11 B
-rw-r--r--  1 blake  staff  2691 Jul 31 07:11 plan.parquet
-rw-------  1 blake  staff   157 Jul 31 07:11 run.json
-rw-------  1 blake  staff    12 Jul 31 07:11 schema-sub-hash.txt

$ jq . .infrahub-sync-cache/custom-example/20260731T1111-3290d1b4/run.json
{
  "finished_at": "2026-07-31T11:11:53.984856+00:00",
  "mode": "sync",
  "status": "applied",
  "summary": {
    "mode": "serial",
    "resources": 1
  }
}
```

`mode: "sync"`, `status: "applied"`, `summary.mode: "serial"` — the serial-sync sidecar
shape T020 pins in unit form, observed here on the real engine.

### Step 2 — the destination observed directly, by name

Read-only GraphQL against `main` at 2026-07-31T11:12:23Z, independent of Prefect and of
the sync's own reporting:

```text
HTTP 200
count = 5

name    type              id
core01  juniper mx204     18c75b6f-d370-b3dd-3049-c514cc4183d7
core02  arista 7504       18c75b6f-f65f-5899-304f-c51c92c7d7f1
core03  cisco asr9001     18c75b70-00ea-cab4-3048-c512dfb97231
edge01  cisco nexus9000   18c75b70-038a-1574-304c-c517ce1592df
edge02  cisco nexus9000   18c75b70-05cb-56a2-3045-c518258d60fe
```

**Exactly the five expected objects — `core01`, `core02`, `core03`, `edge01`, `edge02` —
and nothing else** (`count: 5`), each carrying the `type` value mapped from the fixture's
`device_type` field. The write is real and observable at the destination, not merely
reported by the run.

### Step 3 — follow-up `operation=plan` converges to no-change

Created at 2026-07-31T11:12:35Z, **after** the sync run was terminal:

```text
HTTP 201
{"id":"53b76035-96d3-4fa9-a34a-7591eb23beb0","name":"valiant-guillemot","state_type":"SCHEDULED","state_name":"Scheduled","parameters":{"sync_name":"custom-example","operation":"plan"}}
```

**Plan flow-run id: `53b76035-96d3-4fa9-a34a-7591eb23beb0`** (`valiant-guillemot`).

| Timestamp (UTC) | type | name |
|---|---|---|
| 2026-07-31T11:12:35.715617Z | SCHEDULED | Scheduled |
| 2026-07-31T11:12:39.820083Z | PENDING | Pending |
| 2026-07-31T11:12:39.830946Z | PENDING | Submitting |
| 2026-07-31T11:12:41.067748Z | RUNNING | Running |
| 2026-07-31T11:12:41.780427Z | COMPLETED | Completed |

`{"state_type":"COMPLETED","state_message":null,"total_run_time":0.712679}`. Logs:

```text
2026-07-31T11:12:41.080809Z [20] prefect.flow_runs :: Beginning flow run 'valiant-guillemot' for flow 'infrahub-sync'
2026-07-31T11:12:41.417234Z [20] prefect.flow_runs :: infrahub_sync | tier 0 (1): ['InfraDevice']
2026-07-31T11:12:41.419290Z [20] prefect.flow_runs :: infrahub_sync.potenda | Potenda tier 0 (1): ['InfraDevice']
2026-07-31T11:12:41.421019Z [20] prefect.flow_runs :: infrahub_sync.potenda | Load: Importing data from MockDB
2026-07-31T11:12:41.421815Z [20] prefect.flow_runs :: infrahub_sync.potenda | Load: Importing data from Infrahub
2026-07-31T11:12:41.612675Z [20] prefect.flow_runs :: infrahub_sync.cache.incremental | Incremental disabled: --full-extract requested
2026-07-31T11:12:41.613413Z [20] prefect.flow_runs :: infrahub_sync.cache.incremental | Incremental disabled: --full-extract requested
2026-07-31T11:12:41.769787Z [20] prefect.flow_runs :: infrahub_sync.adapters.infrahub | Infrahub: Loading all 5 InfraDevice
2026-07-31T11:12:41.772202Z [20] prefect.flow_runs :: infrahub_sync.potenda | Diff: Comparing data from MockDB to Infrahub
2026-07-31T11:12:41.773112Z [20] prefect.flow_runs :: infrahub_sync.potenda | diff: 10/10 models processed
2026-07-31T11:12:41.774800Z [20] prefect.flow_runs :: infrahub_sync.execution |
(no diffs)
2026-07-31T11:12:41.775928Z [20] prefect.flow_runs :: infrahub_sync.execution | Cached run 20260731T1112-40e8cdc2 at <repo-root>/.infrahub-sync-cache/custom-example/20260731T1112-40e8cdc2
2026-07-31T11:12:41.776595Z [20] prefect.flow_runs :: run 20260731T1112-40e8cdc2 finished: status=no-change changed=False summary=create:0,update:0,delete:0 artifact=<repo-root>/.infrahub-sync-cache/custom-example/20260731T1112-40e8cdc2
2026-07-31T11:12:42.119997Z [20] prefect.flow_runs :: Finished in state Completed()
```

**Summary line, verbatim:**

```text
run 20260731T1112-40e8cdc2 finished: status=no-change changed=False summary=create:0,update:0,delete:0 artifact=<repo-root>/.infrahub-sync-cache/custom-example/20260731T1112-40e8cdc2
```

`status=no-change`, `changed=False`, **all-zero summary** — Constitution II idempotency.
Two independent lines prove the convergence is real rather than a stalled read:
`Infrahub: Loading all 5 InfraDevice` (the plan loaded the five objects the sync had just
written — the same line read `all 0 InfraDevice` before the write) and `(no diffs)` where
the pre-sync plan had printed five `MISSING` rows. The plan run's own sidecar keeps the
diff shape:

```text
$ jq . .infrahub-sync-cache/custom-example/20260731T1112-40e8cdc2/run.json
{
  "finished_at": "2026-07-31T11:12:41.775315+00:00",
  "mode": "diff",
  "status": "dry-run",
  "summary": {
    "resources": 1
  }
}
```

### Secret hygiene

The `INFRAHUB_API_TOKEN` value was grep-scanned (`grep -qF`) against the whole
Prefect-visible surface — every log record (`POST /api/logs/filter`, limit 500) and every
flow-run object incl. parameters and state messages (`POST /api/flow_runs/filter`):

```text
NO_TOKEN_VALUE in <all logs>
NO_TOKEN_VALUE in <all flow runs>
```

No credential value appears anywhere in this section; both values were passed to `curl`
as environment references and never echoed.

### Outcome

DBA-005 satisfied in full, live: a remote caller submitting `operation=sync` +
`confirm_writes=true` through the REST API got a run that reached `COMPLETED` with
`status=applied` and `summary=create:5,update:0,delete:0`; the five expected
`InfraDevice` objects exist at the destination, confirmed by name through a direct
read; and a follow-up remote plan reports `status=no-change`, `changed=false`, all-zero
summary. DBR-004's write half is exercised — the confirmed remote write reached the
destination through the same shared surface the CLI serial branch uses, with no CLI
subprocess involved.

No live-environment ceiling applies to T021: every leg DBA-005 requires ran against the
real lab.

### Teardown

Both processes this task started were stopped at 2026-07-31T11:13:13Z (each reported
exit 144, i.e. the SIGTERM sent below):

```text
$ pkill -f "infrahub_sync.orchestration.serve"    # serve
$ pkill -f "prefect server start"                 # server
$ lsof -nP -iTCP:4200 -sTCP:LISTEN
PORT4200_FREE
$ pgrep -fl "prefect|orchestration.serve"
NO_PREFECT_PROCS
```

No lab container was stopped, restarted, modified, or exec'd into. Prefect state stayed
entirely inside the scratchpad `PREFECT_HOME`.

**Final destination state — the five devices REMAIN, deliberately**: they are the
evidence for DBA-005 and were not cleaned up.

```text
{"count":5,"names":["core01","core02","core03","edge01","edge02"]}
```

A later task needing an empty destination must reset it first (the destination is
disposable per the brief's R-3).

---

## Phase 5 (US3) live verification — T024

**Trace**: DBA-006, DBA-007, DBA-008 (live transcript grep); SC-004; DBR-004, DBR-005,
DBR-008; research probe d₁; spec edge case 1.
**Measured**: 2026-07-31T11:32Z → 2026-07-31T11:37Z (UTC), on branch
`001-prefect-managed-remote-run-local-dp-001`.
**Environment**: darwin (macOS 25.5.0), Python 3.12.2,
`uv sync --extra dev --extra prefect` (`Resolved 180 packages`, `Audited 171 packages`),
prefect 3.8.1. Infrahub 1.9.8 at `http://localhost:8000` with `InfraDevice(name, type)` on
`main`. `INFRAHUB_ADDRESS` / `INFRAHUB_API_TOKEN` supplied from the session environment
only — neither value appears in any file, parameter, request body, or log line below.
`PREFECT_HOME` pointed at a fresh scratchpad directory
(`…/scratchpad/t024/prefect-home`).

**This task writes nothing.** Every request below is refused; the destination is read
twice, read-only, to prove it.

### Destination baseline — the unchanged-five reading

The destination arrived holding the five `InfraDevice` objects T021 deliberately left as
DBA-005 evidence. T024's obligation is that a refused request leaves the destination
UNCHANGED, so **the five were asserted unchanged rather than reset to zero** — the cheaper
reading of the same property, and it preserves T021's evidence instead of destroying it.
Recorded before AND after, and not only by count: the five object **ids are identical**
across the two reads, so nothing was deleted and recreated either.

```text
$ # POST $INFRAHUB_ADDRESS/graphql/main  {"query": "{ InfraDevice { count edges { node { name { value } type { value } id } } } }"}
BEFORE 2026-07-31T11:32:53Z  HTTP 200
count = 5
names = ['core01', 'core02', 'core03', 'edge01', 'edge02']

AFTER  2026-07-31T11:36:40Z  HTTP 200
count = 5
  core01  juniper mx204     18c75b6f-d370-b3dd-3049-c514cc4183d7
  core02  arista 7504       18c75b6f-f65f-5899-304f-c51c92c7d7f1
  core03  cisco asr9001     18c75b70-00ea-cab4-3048-c512dfb97231
  edge01  cisco nexus9000   18c75b70-038a-1574-304c-c517ce1592df
  edge02  cisco nexus9000   18c75b70-05cb-56a2-3045-c518258d60fe
```

### Preconditions

```text
$ date -u +%Y-%m-%dT%H:%M:%SZ ; lsof -nP -iTCP:4200 -sTCP:LISTEN ; pgrep -fl "prefect|orchestration.serve"
2026-07-31T11:32:42Z
PORT4200_FREE
NO_PREFECT_PROCS
```

Prefect state empty immediately after `prefect server start` against the fresh
`PREFECT_HOME` (`GET /api/health` → `true` at 2026-07-31T11:33:07Z):

```text
deployments: []
flow_runs count: 0
```

Serve process started **from the repository root** (X1/E11 — the fixture's `./`-relative
paths resolve against the serving process's CWD):

```bash
export INFRAHUB_SYNC_CONFIG_DIRECTORY="$PWD/examples/custom_adapter"
uv run python -m infrahub_sync.orchestration.serve
```

Deployment READY at 2026-07-31T11:33:26Z from `GET /api/deployments/name/infrahub-sync/run`:

```text
{
  "id": "a4f1c38a-bdb2-4aa1-9357-dadfbaff8ec8",
  "name": "run",
  "flow_id": "20bdcae8-c50d-41c1-bdf0-ab99a977ad83",
  "status": "READY",
  "enforce_parameter_schema": true,
  "work_pool_name": null
}
```

### Leg (a) — DBA-006: `operation=sync` without `confirm_writes`

```bash
curl -s -X POST "$PREFECT_API_URL/deployments/$DEP_ID/create_flow_run" \
  -H "Content-Type: application/json" \
  -d '{"parameters": {"sync_name": "custom-example", "operation": "sync"}}'
```

Create response at 2026-07-31T11:34:00Z — accepted, because a missing confirmation is a
*surface* refusal, not a parameter-schema violation:

```text
HTTP 201
{"id": "cb5405e4-dae9-41da-aa22-46e3ab0b3d9a", "name": "smart-mastodon",
 "state_type": "SCHEDULED", "state_name": "Scheduled",
 "parameters": {"sync_name": "custom-example", "operation": "sync"}}
```

| Timestamp (UTC) | type | name |
|---|---|---|
| 2026-07-31T11:34:00.814982Z | SCHEDULED | Scheduled |
| 2026-07-31T11:34:00.955977Z | PENDING | Pending |
| 2026-07-31T11:34:00.965675Z | PENDING | Submitting |
| 2026-07-31T11:34:02.206885Z | RUNNING | Running |
| 2026-07-31T11:34:02.237998Z | FAILED | Failed |

**State message, verbatim:**

```text
Flow run encountered an exception: RunValidationError: confirm_writes=true is required to run operation=sync
```

All three log records for the run — the refusal happens between "Beginning flow run" and
"Finished in state Failed", with **no adapter-load line in between**:

```text
2026-07-31T11:34:02.222430Z [20] prefect.flow_runs :: Beginning flow run 'smart-mastodon' for flow 'infrahub-sync'
2026-07-31T11:34:02.230085Z [40] prefect.flow_runs :: Encountered exception during execution: RunValidationError('confirm_writes=true is required to run operation=sync')
2026-07-31T11:34:02.406922Z [20] prefect.flow_runs :: Finished in state Failed('Flow run encountered an exception: RunValidationError: confirm_writes=true is required to run operation=sync')
```

The traceback in that ERROR record terminates at `execution.py:400` inside `execute_run`'s
step-1 gate, reached through `run_remote_request` — i.e. **before** the
`potenda_factory` call on line 415, so neither adapter was constructed. The run took
**31 ms** of run time (`start_time` 11:34:02.206885Z → `end_time` 11:34:02.237998Z); a run
that loaded both adapters takes ~1.8 s (T021, measured on the same fixture).

### Leg (b) — probe d₁: `"operation": "apply"` is rejected at run creation

```text
$ # POST .../create_flow_run  {"parameters": {"sync_name": "custom-example", "operation": "apply"}}
HTTP 409
{"detail": "Error creating flow run: Validation failed for field 'operation'. Failure reason: 'apply' is not one of ['plan', 'sync']"}
```

**No flow run was created** — the run inventory (`POST /api/flow_runs/count`) read `1`
immediately before the request and `1` two seconds after it. Consequently there is no
`RunResult`, no log record, and no new run directory for this request: the `Literal`
parameter type on the flow, enforced by `enforce_parameter_schema: true`, refuses the
value before the deployment's runner ever sees it.

### Leg (c) — DBA-007 / SC-004: the six negative `sync_name` values

Every value was sent as a literal JSON string (the request bodies were built in Python, so
no shell ever expanded `$(touch /tmp/pwned)`).

| # | `sync_name` | create HTTP | flow-run id | terminal state | run inventory |
|---|---|---|---|---|---|
| 1 | `nope` | 201 | `cccbaf2c-632b-4f3a-8341-6e1a398f4566` | FAILED | 1 → 2 |
| 2 | `../custom-example` | 201 | `d405dae4-b498-4570-a324-26d4ed05c9ff` | FAILED | 2 → 3 |
| 3 | `/etc/passwd` | 201 | `6ba53a55-0135-499d-927c-16ddabcb90d1` | FAILED | 3 → 4 |
| 4 | `a/b` | 201 | `374da002-c32c-404e-8214-e93a20e92b67` | FAILED | 4 → 5 |
| 5 | `--help` | 201 | `7d7c8ea1-eedc-4ed1-8da9-cad1388325ec` | FAILED | 5 → 6 |
| 6 | `$(touch /tmp/pwned)` | 201 | `501ed5cb-9851-4dd9-8346-ef4c33386c5b` | FAILED | 6 → 7 |

State messages, verbatim — each **names the logical name and nothing else**: no directory
listing, no file contents, no interpretation of the value as a path, flag, or command:

```text
Flow run encountered an exception: RunValidationError: No sync configuration named 'nope' was found in the configured directory
Flow run encountered an exception: RunValidationError: No sync configuration named '../custom-example' was found in the configured directory
Flow run encountered an exception: RunValidationError: No sync configuration named '/etc/passwd' was found in the configured directory
Flow run encountered an exception: RunValidationError: No sync configuration named 'a/b' was found in the configured directory
Flow run encountered an exception: RunValidationError: No sync configuration named '--help' was found in the configured directory
Flow run encountered an exception: RunValidationError: No sync configuration named '$(touch /tmp/pwned)' was found in the configured directory
```

Every one of the six raises from `execution.py:270` — the `resolve_sync_instance`
no-match branch — so the tolerant walk found nothing and never constructed a path from the
requested value. `/etc/passwd` is refused with exactly the wording `nope` gets: the value
is compared as a string against each discovered configuration's `name` key, never joined
to the configured directory.

The command-substitution value started no subprocess:

```text
$ ls -la /tmp/pwned
ls: /tmp/pwned: No such file or directory
```

**No new run directory** for any of the seven refused runs — the newest entry under
`.infrahub-sync-cache/custom-example/` is still T021's follow-up plan at 07:12 local
(11:12Z), an hour and twenty minutes before this task ran:

```text
$ ls -latd .infrahub-sync-cache/custom-example/*/ | head -5
drwxr-xr-x@ 7 blake  staff  224 Jul 31 07:12 .infrahub-sync-cache/custom-example/20260731T1112-40e8cdc2/
drwxr-xr-x@ 7 blake  staff  224 Jul 31 07:11 .infrahub-sync-cache/custom-example/20260731T1111-3290d1b4/
drwxr-xr-x@ 7 blake  staff  224 Jul 31 06:58 .infrahub-sync-cache/custom-example/20260731T1058-07e1e25e/
drwxr-xr-x@ 7 blake  staff  224 Jul 30 15:24 .infrahub-sync-cache/custom-example/20260730T1924-1489db53/
drwxr-xr-x@ 7 blake  staff  224 Jul 30 14:58 .infrahub-sync-cache/custom-example/20260730T1858-17aa1f1c/
```

Across all 21 log records of the seven refused runs, the only logger name present is
`prefect.flow_runs`: **zero bridged `infrahub_sync` lifecycle lines**, because no lifecycle
ever started.

### DBA-008 live half — canary/secret grep over the collected transcripts

Every request and response of this task was written verbatim to
`…/scratchpad/t024/transcripts/` — the eight per-leg records (create response, state
history, flow-run object, per-run log payload), plus whole-server dumps
`POST /api/flow_runs/filter` (7 runs), `POST /api/logs/filter` (21 records) and
`POST /api/deployments/filter`. 20 JSON files, 248 177 bytes, plus the serve and server
process logs. Scanned with `grep -F` for the `INFRAHUB_API_TOKEN` value (read from the
environment, never echoed):

```text
$ HITS=$(cat transcripts/*.json serve.log server.log | grep -cF -- "$INFRAHUB_API_TOKEN")
token-value occurrences: 0
$ # per-file form, over all 22 files
TOKEN_SCAN_COMPLETE: NO_TOKEN_VALUE in all 22 scanned files
```

No credential value appears anywhere in the Prefect-visible surface this task produced:
flow-run records, parameters, state messages, or log payloads.

### Outcome

DBA-006 satisfied live: `operation=sync` without `confirm_writes` reached FAILED with a
state message naming the requirement, in 31 ms, before either adapter was constructed, and
the destination is byte-for-byte the same five objects (same ids) afterward. DBA-007's
remote form satisfied live: all six SC-004 negative `sync_name` values were refused by
logical name with no out-of-directory read, no subprocess (`/tmp/pwned` absent), and no run
directory. Probe d₁ confirmed at 3.8.1: an `operation` outside the `Literal` is rejected
with **HTTP 409 and no flow run created**. DBA-008's live half satisfied: zero occurrences
of the credential value across 248 KB of collected REST transcripts.

No live-environment ceiling applies to T024: every leg ran against the real lab.

### Teardown

```text
$ date -u +%Y-%m-%dT%H:%M:%SZ ; pkill -f "infrahub_sync.orchestration.serve" ; pkill -f "prefect server start"
2026-07-31T11:37:01Z
$ lsof -nP -iTCP:4200 -sTCP:LISTEN
PORT4200_FREE
$ pgrep -fl "prefect|orchestration.serve"
NO_PREFECT_PROCS
$ docker ps --format '{{.Names}} {{.Status}}'
infrahub-server-1 Up 24 hours (healthy)      # …and the other seven, all "Up 24 hours"
```

No lab container was stopped, restarted, modified, or exec'd into — all eight report the
same 24-hour uptime after the task as before it.

**Final destination state — the five devices REMAIN, unchanged**: they are still DBA-005's
evidence, and their survival across seven refused runs is DBA-006's.

```text
{"count":5,"names":["core01","core02","core03","edge01","edge02"]}
```

### Incidental finding — `PREFECT_LOCAL_STORAGE_PATH` does not follow `PREFECT_HOME`

Not a defect in this delivery, recorded because it changed a test fixture. Prefect
persists a run's result (including a failed run's exception) to
`settings.results.local_storage_path`, which is resolved from the developer's profile and
**not** from `PREFECT_HOME`. Measured:

```text
$ PREFECT_HOME=/tmp/ph-probe uv run python -c "…get_current_settings()…"
home: /tmp/ph-probe
local_storage_path: ~/.prefect/storage      # ← did NOT follow PREFECT_HOME
$ PREFECT_HOME=/tmp/ph-probe PREFECT_LOCAL_STORAGE_PATH=/tmp/ph-probe/storage uv run python -c "…"
local_storage_path: /tmp/ph-probe/storage
```

So the X10 isolation fixture in `tests/orchestration/test_flow.py` now sets
`PREFECT_LOCAL_STORAGE_PATH` alongside `PREFECT_HOME`. Verified: a full
`uv run pytest -q` leaves the developer's `~/.prefect/storage` file count unchanged
(84 → 84). Residue from the runs made before the fixture was fixed — roughly a dozen
pickled result files written into `~/.prefect/storage` between 11:25Z and 11:37Z by the
flow-executing unit tests and by this task's serve process — was left in place rather than
deleted: the directory is shared with the developer's own Prefect use and the files are
indistinguishable by name. None of them can carry a credential (a successful run's result
is the seven-key `RunResult` dict; a failed run's is the already-sanitized
`RunExecutionError`).

---

## Phase 6 (US4) live verification — T030, T031

Environment: macOS 25.5.0, Python 3.12.2, Infrahub 1.9.8 at `http://localhost:8000`,
Prefect 3.8.1, repository root `<repo-root>`,
branch `001-prefect-managed-remote-run-local-dp-001`. Credential values are never printed
anywhere below; only variable NAMES appear.

### T030 — DBA-001 / SC-006: clean venv WITHOUT the extra (quickstart Scenario 0)

Built at 2026-07-31T11:56:19Z in the session scratchpad — the repository's own environment
was never mutated:

```bash
uv venv "$SCRATCH/base-venv"
uv pip install -p "$SCRATCH/base-venv/bin/python" .
```

```text
### 1. prefect NOT in the installed distribution list ###
  (no prefect distribution installed)

### 2. import + sys.modules probe ###
prefect NOT importable (expected): No module named 'prefect'
BASE-VENV-OK: package + cli + execution imported, prefect absent

### 3. infrahub-sync --help ###
 Usage: infrahub-sync [OPTIONS] COMMAND [ARGS]...
 Infrahub-sync: synchronize data between infrastructure sources and destinations.
exit=0

### 4. infrahub-sync list --directory examples/ ###
INFO | infrahub_sync.cli | from-netbox | netbox >> infrahub | examples/netbox_to_infrahub
… 14 configurations listed, including
INFO | infrahub_sync.cli | custom-example | mockdb >> infrahub | examples/custom_adapter

### 5. infrahub-sync diff --help (the refactored command loads) ###
 Usage: infrahub-sync diff [OPTIONS]
```

The probe script imported `infrahub_sync`, `infrahub_sync.cli` AND
`infrahub_sync.execution` and asserted its own `sys.modules` carries no `prefect*` entry;
a subsequent `import prefect` failed loudly with `ModuleNotFoundError`. Completed
2026-07-31T11:56:36Z. **DBA-001 / SC-006 PASS** — the base install imports and the CLI
runs, including the two commands this phase refactored, with Prefect unavailable.

### T031 — DBA-009 / SC-007: CLI `diff` versus remote `operation=plan`

**Destination reset (authorised by R-3 — the lab destination is disposable).** With the
five `InfraDevice` objects left by T021 in place, both sides would have reported
`no-change`, which is a degenerate oracle. So the destination was returned to zero first.

Before, 2026-07-31T11:56:59Z — `count: 5`, deleted by id via `InfraDeviceDelete`:

| name | id | delete |
|---|---|---|
| core01 | `18c75b6f-d370-b3dd-3049-c514cc4183d7` | `ok: true` |
| core02 | `18c75b6f-f65f-5899-304f-c51c92c7d7f1` | `ok: true` |
| core03 | `18c75b70-00ea-cab4-3048-c512dfb97231` | `ok: true` |
| edge01 | `18c75b70-038a-1574-304c-c517ce1592df` | `ok: true` |
| edge02 | `18c75b70-05cb-56a2-3045-c518258d60fe` | `ok: true` |

After, 2026-07-31T11:57:08Z: `{"data": {"InfraDevice": {"count": 0}}}`. No `infrahub-*`
container was stopped, restarted, modified, or exec'd into; only the GraphQL API was used.

**Leg 1 — CLI `diff`**, 2026-07-31T11:57:12Z, MockDB fixture unmodified:

```bash
uv run infrahub-sync diff --name custom-example --directory examples/
```

```text
INFO | infrahub_sync.execution |
InfraDevice
  InfraDevice: core01 MISSING in Infrahub
  InfraDevice: core02 MISSING in Infrahub
  InfraDevice: core03 MISSING in Infrahub
  InfraDevice: edge01 MISSING in Infrahub
  InfraDevice: edge02 MISSING in Infrahub
INFO | infrahub_sync.execution | Cached run 20260731T1157-7e78e2c4 at …/.infrahub-sync-cache/custom-example/20260731T1157-7e78e2c4
```

**Leg 2 — remote `operation=plan`** on the same still-zero destination. Port 4200 was
verified free before starting; `PREFECT_HOME` and `PREFECT_LOCAL_STORAGE_PATH` both pointed
inside the scratchpad; served from the repository root with
`INFRAHUB_SYNC_CONFIG_DIRECTORY="$PWD/examples/custom_adapter"`.

```text
server healthy      2026-07-31T11:57:30Z   GET /api/health → true
deployment READY    2026-07-31T11:57:44Z   c5888d04-7c87-40bf-9144-3394d3a72443
flow run created    2026-07-31T11:57:52Z   19aefcb4-3410-45a3-a932-09e51879fbf6  (SCHEDULED)
                    {"parameters": {"sync_name": "custom-example", "operation": "plan"}}
terminal state      2026-07-31T11:58:01Z   COMPLETED
```

Flow-run log (via `POST /api/logs/filter`), closing lines:

```text
2026-07-31T11:58:01.610960Z [20] infrahub_sync.execution |
InfraDevice
  InfraDevice: core01 MISSING in Infrahub
  … core02, core03, edge01, edge02 …
2026-07-31T11:58:01.612233Z [20] infrahub_sync.execution | Cached run 20260731T1158-154693d8 at …/.infrahub-sync-cache/custom-example/20260731T1158-154693d8
2026-07-31T11:58:01.612978Z [20] run 20260731T1158-154693d8 finished: status=planned changed=True summary=create:5,update:0,delete:0 artifact=…/20260731T1158-154693d8
```

**Comparison.** Both run directories, both `run.json` files, and the canonical fingerprint
computed by the shared helper (`cache.fingerprint.compute_plan_fingerprint`, never a
reimplementation) over each:

```text
### run.json — CLI diff (…/custom-example/20260731T1157-7e78e2c4/run.json) ###
{ "finished_at": "2026-07-31T11:57:14.713279+00:00", "mode": "diff",
  "status": "dry-run", "summary": { "resources": 1 } }

### run.json — remote plan (…/custom-example/20260731T1158-154693d8/run.json) ###
{ "finished_at": "2026-07-31T11:58:01.611756+00:00", "mode": "diff",
  "status": "dry-run", "summary": { "resources": 1 } }

### result fields + canonical fingerprints ###
cli-diff     run_id=20260731T1157-7e78e2c4
             status=planned changed=True summary={'create': 5, 'update': 0, 'delete': 0}
             fingerprint=669cdb370f8a3c6e0c91b78a18ba03999901ae57e0d38f0b8b4b8778f32c2780
remote-plan  run_id=20260731T1158-154693d8
             status=planned changed=True summary={'create': 5, 'update': 0, 'delete': 0}
             fingerprint=669cdb370f8a3c6e0c91b78a18ba03999901ae57e0d38f0b8b4b8778f32c2780

fingerprints equal: True
```

The remote side's `status` / `changed` / `summary` are the flow's own reported
`RunResult` values (the summary log line above). The CLI discards its `RunResult`, so its
three fields were derived from its materialized plan rows through the same shared helper
the surface uses (`execution._summarize_rows` over `cache.parquet_io.read_plan`) — the
same rows `write_plan` received. Both `run.json` files are byte-identical apart from their
timestamps. **DBA-009 / SC-007 PASS.**

### Teardown

```text
2026-07-31T11:58:31Z
$ pkill -f "infrahub_sync.orchestration.serve"    # exit 0
$ pkill -f "prefect server start"                 # exit 0
$ pgrep -fl prefect
  none
$ lsof -ti :4200
  free
$ docker ps --format '{{.Names}} {{.Status}}'
infrahub-server-1 Up 24 hours (healthy)           # …and the other seven, all "Up 24 hours"
```

**Final destination state: ZERO `InfraDevice` objects** — the five were deleted for the
reset above and both compared runs are read-only previews, so nothing recreated them.
`{"data": {"InfraDevice": {"count": 0}}}` at 2026-07-31T11:58:31Z. T021's DBA-005 write
evidence is unaffected (it is the transcript recorded in Phase 4, not the live rows); a
future run needing the five present must re-run `operation=sync` with
`confirm_writes=true`.

### DBR-009 deviation found and NOT resolved — the rendered logger NAME (open decision)

Byte-comparing the CLI's observable output before and after the refactor (same fake
engine, same fixture, normalized timestamps and paths) shows exactly one difference, in
both directions of the moved lifecycle lines:

```diff
 ===== diff exit=0 =====
-INFO | infrahub_sync.cli |
+INFO | infrahub_sync.execution |
 DIFF-BODY
-INFO | infrahub_sync.cli | Cached run test-run at <CACHE>/from-netbox/test-run
+INFO | infrahub_sync.execution | Cached run test-run at <CACHE>/from-netbox/test-run
```

Everything else is identical: level, message text, ordering, exit codes, and `run.json`
contents (`status`, `mode`, `summary`, `finished_at` fields all unchanged — see the
comparison above).

The cause is structural, not incidental. `cli.py`'s `_setup_logging` formatter is
`"%(levelname)s | %(name)s | %(message)s"`, so the record's origin logger name is
rendered; the lifecycle log calls now execute in `infrahub_sync/execution.py`, which logs
via `logging.getLogger(__name__)` per the binding logging convention (tasks.md line 18)
and as already built by T005–T008.

It is not resolvable inside T025/T026's pinned structures, which are silent on the logger
name — nothing `cli.py` can do changes a record emitted by another module. The three
available resolutions all touch ratified ground:

1. **Accept the deviation** and scope DBR-009's "log lines" to level + message + ordering.
   Costs nothing; makes the byte-identity claim non-absolute.
2. **Make `execution.py` log through `logging.getLogger("infrahub_sync.cli")`.** Restores
   byte-identity, but contradicts tasks.md line 18, misnames the module for the flow
   caller, breaks the six `caplog.at_level(..., logger="infrahub_sync.execution")`
   assertions already passing in `tests/test_execution_surface.py`, and contradicts the
   T019/T021/T024 live evidence above, which records `infrahub_sync.execution` as the
   origin logger name in the Prefect run log.
3. **Add a caller-supplied logger seam to `execute_run`.** New surface not in
   `contracts/execution-surface.md` — a contract change.

Left as implemented (option 1's behavior) and surfaced as an open decision rather than
silently accepted or silently "fixed". Everything else DBR-009 names is proven identical:
`tests/test_cli_execution_mapping.py`'s nine per-stage tests pass UNMODIFIED against both
`9edc1bc`'s `cli.py` and the refactored one, and the DBA-009 population passes unmodified.

---

## Phase 7 (US5) — example, docs, hygiene scan, and the clean-context walkthrough

**Trace**: DBR-013; DBA-008 (example-corpus half), DBA-011; SC-005, SC-009; R-3 (schema
YAML half). **Tasks**: T032, T033, T033a, T034, T035, T036, T037.
**Measured**: 2026-07-31T12:12Z → 2026-07-31T12:27Z (UTC), branch
`001-prefect-managed-remote-run-local-dp-001`, macOS 25.5.0, Python 3.12.2,
`uv sync --extra dev --extra prefect` (prefect 3.8.1 / redis 8.1.0), Infrahub 1.9.8 at
`http://localhost:8000`. `INFRAHUB_ADDRESS` / `INFRAHUB_API_TOKEN` came from the session
environment only; no value appears in any file or transcript (scan below).

### What shipped

| Task | Artifact |
|---|---|
| T032 | `examples/prefect_remote_run/schemas/infra_device.yml` — loadable `InfraDevice(name, type)`, using `human_friendly_id` / `order_by` and NOT the deprecated `display_labels` / `default_filter` the lab warned about |
| T033 | `examples/prefect_remote_run/requests/` — four request bodies (`create-plan-flow-run.json`, `create-sync-flow-run-confirmed.json`, `filter-flow-run-logs.json`, `create-invalid-operation-flow-run.json`) plus `requests/README.md` noting method + endpoint + expected response for all six interactions of contracts/prefect-flow.md §5, including "Read the result" |
| T033a | `examples/custom_adapter/custom_adapter_src/custom_adapter.py` — narration moved from `print()` to `logging.getLogger("infrahub_sync.examples.custom_adapter")`, plus a WARNING when a configured `db_path` does not exist (naming the path, its absolute resolution, and the CWD it resolved against) |
| T034 | `examples/prefect_remote_run/README.md` |
| T035 | `docs/docs/reference/prefect-remote-run.mdx`, registered in `docs/sidebars.ts`; Prefect subsection of `docs/docs/orchestration.mdx` now leads with the packaged integration and demotes hand-wrapping the CLI |
| T036 | `tests/test_example_hygiene.py` — 38 tests, no prefect dependency |
| T037 | the walkthrough below |

### T033a — the five-device outcome is unchanged, the narration is now bridgeable

CLI `diff` at 2026-07-31T12:12:51Z, MockDB fixture unmodified, destination at zero:

```text
INFO | infrahub_sync.examples.custom_adapter | Loading 5 devices nodes
INFO | infrahub_sync.examples.custom_adapter | MockDB: Loading 5/5 InfraDevice
INFO | infrahub_sync.adapters.infrahub | Infrahub: Loading all 0 InfraDevice
INFO | infrahub_sync.potenda | diff: 5/5 models processed
INFO | infrahub_sync.execution | Cached run 20260731T1212-a325c16e at …/custom-example/20260731T1212-a325c16e
```

Five creates, as before the change; the adapter's own lifecycle lines now carry a logger
name inside the `infrahub_sync` hierarchy, so the DBR-012 bridge forwards them (confirmed
remotely in the T037 log below). The derived `examples/custom_adapter/mockdb/` adapter
needed no change: it only subclasses the class resolved from
`custom_adapter_src/custom_adapter.py`, and the run above exercises that resolution
end-to-end. A regeneration was attempted and **reverted**: `infrahub-sync generate`
re-rendered `sync_models.py` with unrelated churn, which is the pre-existing T002
non-reproducibility recorded above, not a consequence of this change.

Note the actual narration text is `Loading 5 devices nodes` (the line interpolates the
mapping's resource name, `devices`) plus `MockDB: Loading 5/5 InfraDevice`, not the
`Loading 5 InfraDevice nodes` that tasks.md T033a/T034 assumed. The README documents what
the run really prints.

### T036 — example hygiene scan (DBA-008 corpus half)

`tests/test_example_hygiene.py`, 38 tests over every file in
`examples/prefect_remote_run/` (README, `requests/`, `schemas/`): no seeded canary and no
`ZZ-`-shaped canary; every credential-shaped assignment is a sentinel, a `$VAR` reference,
or a documented variable NAME; every `Bearer` value is exactly `Bearer <your-api-token>`;
no credentials embedded in a URL; no ≥24-character opaque literal on any line mentioning
a credential; the README uses `<your-api-token>` and `<your-infrahub-address>` verbatim;
no request body carries a credential field.

The rules were mutation-probed before being trusted — synthetic violations are flagged and
the example's real content is not:

```text
FLAGGED | real token assign      | ['assign:INFRAHUB_API_TOKEN="181a4c9e-…-deadbeef0000"', 'long:…']
FLAGGED | real token unquoted    | ['assign:INFRAHUB_API_TOKEN=181a4c9eabcd…', 'long:…']
FLAGGED | yaml token             | ['assign:token=s3cr3tvalue']
FLAGGED | bearer real            | ['bearer:abc123realtoken']
FLAGGED | url creds              | ['urlcreds']
FLAGGED | canary                 | ['canary:ZZ-FLOW-ENV-INFRAHUB-TOKEN-0001', …]
clean   | ok sentinel            | []
clean   | ok env ref             | []
clean   | ok bearer              | []
clean   | ok prose               | []
```

Prefect independence proven rather than asserted — the file runs in a fresh interpreter
and loads no prefect module:

```text
$ python -c "<pytest.main on tests/test_example_hygiene.py, then scan sys.modules>"
38 passed in 0.03s
exit 0 | prefect modules loaded: []
```

### T037 — clean-context walkthrough (DBA-011, SC-009)

Performed by a **fresh agent session with no exposure to the implementation**, under a
hard prohibition on reading anything outside `examples/prefect_remote_run/`. Full verbatim
transcript: `…/scratchpad/t037-walkthrough.md` (406 lines). Scope: README steps 1–6 and 8;
step 7 (the confirmed write) was deliberately excluded to keep the walkthrough read-only
and leave the destination in its documented zero-object state.

Attestation, in the walker's own words: *"I consulted only
`examples/prefect_remote_run/README.md` and its linked files inside
`examples/prefect_remote_run/`: `requests/README.md`, `requests/*.json`, and
`schemas/infra_device.yml`. No other file in the repository was read, grepped, or
opened."*

| Step | Documented command | Result |
|---|---|---|
| 1 | `uv sync --extra prefect`; version check | PASS — `3.8.1` |
| 2 | `uv run infrahubctl schema load examples/prefect_remote_run/schemas/infra_device.yml --branch main` | PASS — `1 schema processed in 6.701 seconds`, **no deprecation warning** (T032's key choice) |
| 2 | destination verify | PASS — `{"data":{"InfraDevice":{"count":0}}}`, the documented starting state |
| 3 | runner environment, port 4200 free | PASS — `PORT4200_FREE` before anything started |
| 4 | `uv run prefect server start` | PASS — banner, then `GET /api/health` → `true` at 12:22:34Z |
| 5 | `uv run python -m infrahub_sync.orchestration.serve` from the repository root | PASS — `Your flow 'infrahub-sync' is being served and polling for scheduled runs!`; deployment `89ec10ee-74af-4e82-8d7c-100c64fe2b13` `status: "READY"`, `enforce_parameter_schema: true`, `operations: ["plan","sync"]` |
| 6 | create plan run from `requests/create-plan-flow-run.json` | PASS — flow-run id **`4457ef22-bb74-4b33-8ac4-d2acc373c46e`** returned synchronously at 12:23:18Z |
| 6 | state polling | PASS — `SCHEDULED → PENDING → RUNNING → COMPLETED` |
| 6 | read the result via `requests/filter-flow-run-logs.json` | PASS — see below |
| 8 | stop serve, stop server, confirm port released | PASS |

The run log retrieved by the documented `POST /api/logs/filter` call, matching the
README's checkpoint block line for line and in order:

```text
infrahub_sync.potenda | Load: Importing data from MockDB
infrahub_sync.examples.custom_adapter | Loading 5 devices nodes
infrahub_sync.examples.custom_adapter | MockDB: Loading 5/5 InfraDevice
infrahub_sync.adapters.infrahub | Infrahub: Loading all 0 InfraDevice
infrahub_sync.potenda | diff: 5/5 models processed
infrahub_sync.execution |
InfraDevice
  InfraDevice: core01 MISSING in Infrahub
  InfraDevice: core02 MISSING in Infrahub
  InfraDevice: core03 MISSING in Infrahub
  InfraDevice: edge01 MISSING in Infrahub
  InfraDevice: edge02 MISSING in Infrahub
run 20260731T1223-efb89811 finished: status=planned changed=True summary=create:5,update:0,delete:0 artifact=<repo-root>/.infrahub-sync-cache/custom-example/20260731T1223-efb89811
Finished in state Completed()
```

**`summary=create:5,update:0,delete:0` — five creates, not zero.** The T033a bridged
adapter narration is visible remotely, which is what X14 asked for. No live-environment
ceiling applies to T037: the whole walkthrough ran against the real lab.

#### Two README defects the walkthrough exposed, and the fixes

Both were found by the walker and fixed in the README rather than worked around:

1. **Missing state in the documented progression.** The README said the state goes
   `SCHEDULED`, then `RUNNING`, then `COMPLETED`; the run really passes through `PENDING`
   in between, so a reader watching for exactly three states could read `PENDING` as a
   fault. Fixed: the expected progression now names `PENDING` and says it is normal.
2. **An artifact-listing command that lists every historical run.** The README said
   `ls .infrahub-sync-cache/custom-example/*/` to find "the run's artifact directory"; in a
   used checkout that glob matched nine directories from earlier runs. Fixed: the command
   is now `ls .infrahub-sync-cache/custom-example/<run-id>/`, with one sentence saying the
   run id comes from the summary line.

The walker also noted it could not independently confirm the step-5 claim about starting
the serve process outside the repository root, since verifying it would mean deliberately
breaking the setup. That claim is separately evidenced: it is X1/E11, recorded in
contracts/prefect-flow.md §3 and re-stated in the T019 section above.

#### Secret hygiene

```text
$ python -c "print(open('t037-walkthrough.md').read().count(os.environ['INFRAHUB_API_TOKEN']))"
token-value occurrences in transcript: 0
```

Only variable NAMES appear in the transcript, the README, and the request corpus.

### Gates after this chunk

```text
uv run invoke linter.format      → 82 files, formatted clean
uv run ruff check .              → All checks passed!
uv run invoke lint               → exit 30; 54 pylint diagnostics in infrahub_sync/ (baseline, unchanged; rating 9.68/10, +0.00)
uv run yamllint infrahub_sync    → clean
uv run ty check .                → Found 4 diagnostics (baseline, unchanged)
uv run rumdl check .             → Success: No issues found in 73 files
uv run pytest -q                 → 263 passed, 4 skipped (baseline 225 + 4; +38 from tests/test_example_hygiene.py)
uv run infrahub-sync list --directory examples/  → 14 configurations, unchanged (the new example ships no config.yml)
```

`docs/` verification is limited to `rumdl check` over the new and revised MDX; no
Docusaurus build was run.

### Teardown

```text
2026-07-31T12:25:49Z
$ lsof -nP -iTCP:4200 -sTCP:LISTEN          → PORT4200_FREE
$ pgrep -fl "prefect|orchestration.serve"   → NO_PREFECT_PROCS
$ docker ps --format '{{.Names}} {{.Status}}'
infrahub-task-worker-1 Up 25 hours          # …and the other seven, all "Up 25 hours"
```

No `infrahub-*` container was stopped, restarted, modified, or exec'd into — all eight
report the same 25-hour uptime. **Final destination state: ZERO `InfraDevice` objects**
(`{"data":{"InfraDevice":{"count":0}}}` at 12:24:07Z), the documented starting state for
the next walkthrough. Prefect state stayed inside a scratchpad `PREFECT_HOME` with
`PREFECT_LOCAL_STORAGE_PATH` set alongside it, so the developer's `~/.prefect` was
untouched.

---

## Phase 8 — governance gates, scope audit, and delivery evidence (T038–T042)

Run 2026-07-31 on the final tree at `25dc955`, in the `uv sync --extra dev --extra prefect`
environment (`Resolved 180 packages`, `Audited 171 packages` — already in sync, no
dependency change made by this chunk). No feature code was written in this chunk: it
verifies and records. The lab was read only — no container touched, no destination write.

### T038 — scope audit (DBR-014)

Final diff shape:

```text
$ git diff 9edc1bc --shortstat
 58 files changed, 12519 insertions(+), 451 deletions(-)

$ git rev-list --count 9edc1bc..HEAD
23

$ git diff 9edc1bc --name-status -- infrahub_sync/
A       infrahub_sync/cache/fingerprint.py
M       infrahub_sync/cli.py
A       infrahub_sync/execution.py
A       infrahub_sync/orchestration/__init__.py
A       infrahub_sync/orchestration/flow.py
A       infrahub_sync/orchestration/serve.py
```

Six files under `infrahub_sync/`: four new modules, one new cache helper, and the CLI
delegation edit. Every backlog item checked by name against that diff:

| Backlog item | Claim | Result |
|---|---|---|
| B-001 — custom HTTP service / Sync-shaped REST resource model | not shipped | **CONFIRMED absent.** `grep -rniE 'fastapi\|uvicorn\|flask\|starlette\|APIRouter\|@app\.(get\|post\|put\|delete)'` over `infrahub_sync/` and `pyproject.toml` → no match. The remote API is Prefect's own deployment/flow-run REST surface (DBR-011) |
| B-002 — saved-plan browse / approve / apply-by-run-ID remote workflow | not shipped | **CONFIRMED absent.** `grep -rniE 'apply_by_run_id\|apply-by-run\|approve\|approval\|browse\|list_plans\|saved_plan\|plan_id'` over `orchestration/` + `execution.py` → no match. The flow signature at `orchestration/flow.py:85` takes EXACTLY four parameters — `sync_name`, `operation`, `confirm_writes`, `branch` — with no run-ID, plan-ID, or `apply` parameter. The pre-existing `apply` CLI command is untouched and unreachable from the flow |
| B-003 — per-stage Prefect tasks | not shipped | **CONFIRMED absent.** `grep -rnE '@task\|prefect\.task'` over `infrahub_sync/` → no match. The only prefect symbols imported anywhere in the package are two, both in `orchestration/flow.py`: `from prefect import flow` (line 26) and `from prefect.logging import get_run_logger` (line 27). One flow, zero tasks |
| B-004 — work pools / workers / retries / crash recovery / PostgreSQL / Redis-as-broker / object storage / Kubernetes | not shipped | **CONFIRMED absent.** `grep -rniE 'work_pool\|worker\|retries\|retry_delay\|postgres\|psycopg\|kubernetes\|k8s\|s3\|boto3\|minio'` over `orchestration/` + `execution.py` returns exactly ONE hit, and it is a negative assertion in a docstring: `orchestration/serve.py:3` — "A locally served deployment — no work pool and no separate worker process." Confirmatory, not an implementation. **Auditor's note honoured:** the base `redis>=4.3,<9` declaration is D005 option D — the client diffsync's store already required, now declared directly because `utils.py` imports `RedisStore` unconditionally and the `[redis]` extra's `redis<5.0` cap is gone. It is NOT a Redis backing service for Prefect; Prefect's state lives in the embedded SQLite `prefect.db` (T018 evidence) |
| B-005 — schedules / overlap policies / event triggers / notifications | not shipped | **CONFIRMED absent.** `grep -rniE 'schedule\|cron\|interval=\|trigger\|notification\|automation\|webhook'` over `orchestration/` + `execution.py` → no match. `serve.py` serves the deployment with no schedule argument |
| B-006 — production auth / audit / HA / backup / upgrade guarantees | not shipped | **CONFIRMED absent.** `grep -rniE 'oauth\|authent\|rbac\|high.avail\|failover\|backup\|upgrade.guarantee\|audit_log\|audit-log'` over `orchestration/` + `execution.py` → no match. The shipped posture is the opposite: a MANDATORY trusted-development-environment caveat in both the README and the reference page |
| B-007 — custom operator UI, configuration registration / versioning | not shipped | **CONFIRMED absent.** `grep -rniE 'jinja\|template\|html\|render_template\|register_config\|config_version\|versioned'` over `orchestration/` + `execution.py` → no match. Observation is Prefect's own UI and API; configurations are discovered from `INFRAHUB_SYNC_CONFIG_DIRECTORY` at call time with no registry and no version field |

Two further audit assertions:

```text
prefect absent from base dependencies (parsed from pyproject.toml, not grepped):
  base dependencies = infrahub-sdk[all], structlog, diffsync>=2.1,<3.0, redis>=4.3,<9,
                      netutils, tqdm, pyarrow, fsspec, filelock
  "prefect" in base dependencies?  → False
  optional-dependencies groups     → ['prefect', 'dev']
  extra 'prefect'                  → ['prefect==3.8.1']          ← single pin, optional

parallel CLI sync branch untouched:
  $ git diff 9edc1bc --stat -- infrahub_sync/potenda/     → (empty)
  $ git diff 9edc1bc -- infrahub_sync/ | grep -E '^[+-].*sync_in_tiers'  → (no match)
  sync_in_tiers still defined at potenda/__init__.py:449 and still called from
  cli.py:256 with parallel=True — neither the definition nor the call site is in the diff.
```

**T038 verdict: PASS.** No backlog item B-001–B-007 is implemented; prefect is an optional
extra only; the parallel `sync_in_tiers` branch is byte-unchanged.

### T039 — required workflow on the final tree

Leg 1, the Python formatters alone (NOT `uv run invoke format`, which runs `rumdl fmt` and
corrupts Markdown per D014 — `rumdl fmt` was never run in this chunk):

```text
$ uv run invoke linter.format
 - [INFRAHUB-SYNC] Check code with ruff
82 files left unchanged
All checks passed!
 - [INFRAHUB-SYNC] All formatters have been executed!
exit=0
$ git status --porcelain     → (empty)
```

No Python-formatter diffs. Leg 2, Markdown:

```text
$ uv run rumdl check .
Success: No issues found in 73 files (42ms)
exit=0
```

Clean — which is what makes the pylint assertion below meaningful, since `invoke lint` runs
rumdl first with no `warn=True` and any Markdown issue would abort the chain before ruff and
pylint ran at all.

Leg 3, the lint chain:

```text
$ uv run invoke lint
 - [docs] Lint docs with rumdl      → Success: No issues found in 73 files
                                      82 files already formatted
 - [INFRAHUB-SYNC] Check code with ruff     → (no findings)
 - [INFRAHUB-SYNC] Check code with pylint   → 54 diagnostics
Your code has been rated at 9.68/10 (previous run: 9.68/10, +0.00)
exit=30
```

**Exit 30 is the expected inherited state, not a regression** (D014's companion correction
to the brief's R-4 claim of exit 0). The gate is no regression against the inherited
baseline, and this chunk proved that DIRECTLY rather than relying on the stale proof
recorded in T039's own text.

That stale proof matters and is corrected here. T039 says the inherited set is provable
because "`git diff main..HEAD -- infrahub_sync/` is empty" — true when Phase 1 measured it,
**false for the final tree**, which adds four modules and edits `cli.py`. So the baseline
was re-measured from scratch, at commit `9edc1bc`, in the SAME dev+prefect venv, by
extracting the old package into a scratch directory (no branch switch, no `git stash`, no
worktree — the working tree was never disturbed):

```text
$ git archive 9edc1bc infrahub_sync/ pyproject.toml | tar -x -C <scratch>/base
$ cd <scratch>/base && <repo>/.venv/bin/pylint infrahub_sync/
Your code has been rated at 9.60/10
exit=30
BASELINE TOTAL = 56 diagnostics

FINAL TREE      = 54 diagnostics, rating 9.68/10, exit 30
```

Exact set difference over normalized `file CODE` pairs with counts:

```text
ONLY IN BASELINE (removed by this run):
  1 infrahub_sync/cli.py R0912   (too-many-branches)
  1 infrahub_sync/cli.py R0915   (too-many-statements)

ONLY IN FINAL TREE (new — required to be empty):
  (empty)
```

**Zero new pylint diagnostics attributable to this run, and two fewer than baseline** — the
two `try/except` blocks the T025/T026 refactor lifted out of the CLI command bodies. Rating
improved 9.60 → 9.68. Every remaining diagnostic is identical in file and code to the
baseline: 20 × `C0415` in `potenda/__init__.py`, 7 × `C0413` + 4 × `W0613` + `E0213` +
`W0707` + `C0415` + `C0412` in `__init__.py`, 4 × `C0415` + `R1720` + `R0917` + `R0915` in
`utils.py`, 3 × `R0917` + 3 × `C0415` in `cli.py`, 2 × `C0413` in `cache/parquet_io.py`,
`R0917` in `potenda/__init__.py`, `R1705` + `R0912` in `plugin_loader.py`, `C0415` in
`dependency_graph.py`.

This also corrects the brief's baseline *count*. The brief records the inherited baseline as
"29 × `C0415` + 1 × `E0213`, rating 9.60/10" — 30 diagnostics. The same commit in the
dev+prefect environment yields **56**. The rating matches exactly (9.60/10), confirming it is
the same commit; the count differs because the richer environment resolves imports (pyarrow,
redis, prefect) that a base-only `uv sync` leaves unresolvable, so pylint can analyse
`cache/parquet_io.py`, `plugin_loader.py`, and more of `utils.py`. **Environment difference,
not regression** — and the binding no-regression comparison above is environment-matched on
both sides.

Because pylint's non-zero exit propagates and stops the chain, the remaining legs were
asserted by direct invocation:

```text
$ uv run invoke linter.lint-yaml
 - [INFRAHUB-SYNC] Format yaml with yamllint
exit=0
```

Leg 4, types:

```text
$ uv run ty check .
Found 4 diagnostics
exit=0
```

All four are `warning[unused-ignore-comment]`; **zero `error` diagnostics**, which is why
`ty check` still exits 0:

| Location | Inherited? | Note |
|---|---|---|
| `tests/adapters/test_nautobot_incremental.py:59:50` | inherited | pre-existing |
| `tests/adapters/test_nautobot_incremental.py:101:50` | inherited | pre-existing |
| `tests/adapters/test_nautobot_incremental.py:122:50` | inherited | pre-existing |
| `infrahub_sync/adapters/prometheus.py:10:41` | **expected, new in this environment** | installing prefect pulls in `prometheus-client`, which resolves the optional import the `# ty: ignore[unresolved-import]` suppresses. The comment remains NECESSARY in a base/no-prefect install, so **the adapter file was NOT edited** — out of scope for this run, recorded rather than fixed |

The observed absolute count is **4** (3 inherited + 1 prometheus), not the 5 T039's text
anticipated; T039 states the binding assertion is the SET and that the task records the
absolute numbers it observes, so this is the recorded value. `grep -n 'tool.ty.overrides'
pyproject.toml` → no match: no overrides block was added. The four new modules carry no
`# ty: ignore` directive at all, so there is no new directive needing a TODO.

Ruff-clean and E16 BLE001 configuration, verified as implemented rather than assumed —
`uv run ruff check` reporting nothing is the arbiter, and it reports nothing:

```text
$ grep -nE 'except Exception' infrahub_sync/execution.py infrahub_sync/orchestration/*.py
execution.py:445:        except Exception:                       ← inside execute_run (def at 363)
execution.py:536:    except Exception as exc:  # noqa: BLE001 - boundary translation, always re-raised typed
                                                                ← inside run_remote_request (def at 480)
```

Exactly the gate-satisfying configuration T039 specifies: the targeted `# noqa: BLE001`
**present** on `run_remote_request`'s line, and **absent** at `execute_run`'s step-6 site
(blind `except` + bare `raise`, which does not fire BLE001 — a directive there would report
`RUF100 Unused noqa directive` and fail this very gate). Both D009 sites carry an explanatory
comment; `execute_run`'s records that the broad except is the verbatim pre-existing CLI
pattern (`cli.py:156-159 / 285-288`) preserved so a lifecycle failure can never leave
`run.json` at `status="running"`.

**T039 verdict: PASS** — formatters clean, rumdl clean, yamllint clean, ruff clean, ty exit 0
with the expected set, and pylint at the inherited baseline with zero new diagnostics.

### T040 — full suite

```text
$ uv run pytest -q
263 passed, 4 skipped, 3 warnings in 29.98s
exit=0
```

No regression versus the 111-passed / 2-skipped inherited reference: all inherited tests
still pass, and the population grew by this run's new tests. Three warnings, all
pre-existing and none a failure: two `UserWarning`s about `local_id` shadowing a parent
attribute in test-local models, and — third — the **R-5 disclosure, directly visible in this
run's own output**:

```text
tests/test_potenda_parallel.py:70: PytestUnknownMarkWarning: Unknown pytest.mark.timeout -
  is this a typo?
    @pytest.mark.timeout(5)
```

`pytest-timeout` is absent, so `@pytest.mark.timeout(5)` is a silent no-op. Pre-existing,
deliberately NOT fixed by this run, and reported rather than hidden.

**T040 verdict: PASS.**

### T041 — CLI sanity, all three commands

Leg (a):

```text
$ uv run infrahub-sync --help
Usage: infrahub-sync [OPTIONS] COMMAND [ARGS]...
  Commands: list, diff, sync, apply, generate
exit=0
$ git status --porcelain     → (empty)   ← clean, as required
```

Leg (b):

```text
$ uv run infrahub-sync list --directory examples/
INFO | infrahub_sync.cli | from-netbox | netbox >> infrahub | examples/netbox_to_infrahub
… 14 configurations listed …
INFO | infrahub_sync.cli | custom-example | mockdb >> infrahub | examples/custom_adapter
exit=0
$ git status --porcelain     → (empty)   ← clean, as required
```

Discovery is unbroken and unchanged at 14 configurations: the new
`examples/prefect_remote_run/` ships no `config.yml` (only README, `schemas/`, `requests/`),
so it is correctly absent from the list rather than breaking the walk.

Leg (c) — the D014 leg, which **does not leave the tree clean and is not expected to**:

```text
$ uv run infrahub-sync generate --name from-netbox --directory examples/
INFO | infrahub_sync.cli | Rendered template diffsync_models.j2  to examples/netbox_to_infrahub/netbox/sync_models.py
INFO | infrahub_sync.cli | Rendered template diffsync_adapter.j2 to examples/netbox_to_infrahub/netbox/sync_adapter.py
INFO | infrahub_sync.cli | Rendered template diffsync_models.j2  to examples/netbox_to_infrahub/infrahub/sync_models.py
INFO | infrahub_sync.cli | Rendered template diffsync_adapter.j2 to examples/netbox_to_infrahub/infrahub/sync_adapter.py
exit=0

$ git status --porcelain
 M examples/netbox_to_infrahub/infrahub/sync_adapter.py
 M examples/netbox_to_infrahub/infrahub/sync_models.py
 M examples/netbox_to_infrahub/netbox/sync_adapter.py
 M examples/netbox_to_infrahub/netbox/sync_models.py

$ git diff --stat -- examples/netbox_to_infrahub/
 .../infrahub/sync_adapter.py |  22 +--
 .../infrahub/sync_models.py  | 220 ++++++-------
 .../netbox/sync_adapter.py   |  22 +--
 .../netbox/sync_models.py    | 220 ++++++-------
 4 files changed, 242 insertions(+), 242 deletions(-)
```

**242 changed lines across 4 files — matching D014's predicted ~242 exactly.** Content loss
was checked structurally rather than by eyeballing the diff. A first attempt sorted whole
lines, which cannot see through member reordering *inside* an `_attributes` tuple (one of
the two churn levels D014 names) and produced a false positive; the decisive check parses
both revisions with `ast` and compares each class's fields **order-insensitively**:

```text
netbox/sync_models.py    : 20 classes — ALL structurally IDENTICAL (order-insensitive)
infrahub/sync_models.py  : 20 classes — ALL structurally IDENTICAL (order-insensitive)
netbox/sync_adapter.py   :  1 class   — ALL structurally IDENTICAL
infrahub/sync_adapter.py :  1 class   — ALL structurally IDENTICAL
VERDICT: pure ordering churn, ZERO content loss
```

20 models and 20 classes before and after, with matching attribute multisets per class.
Churn restored, NOT committed:

```text
$ git checkout -- examples/netbox_to_infrahub/
$ git status --porcelain     → (empty)   ← tree restored clean
```

Stated plainly, per the brief's completion condition and D014: this is a **pre-existing
baseline failure reported rather than hidden**. It is INHERITED, not introduced here —
`git diff main..HEAD -- infrahub_sync/generator/` is empty, so the behavior comes from
`main`. Cause, measured and not re-derived: `generate` renders from the live Infrahub schema,
and `generator/templates/diffsync_adapter.j2:26` and `diffsync_models.j2:29` both iterate
`schema.items()` unsorted, so emitted order tracks API response order. A fixed point exists
(three consecutive runs byte-identical) but it MOVES — the committed T002 output at `e04f262`
differs from today's stable output by 484 lines. Option B, committing the current fixed
point, is viable but was **declined** under D014 in favour of option C, because a gate that
passes today and silently starts failing later for someone who changed nothing hides itself,
whereas C never pretends the defect is fixed. No known correctness impact: diffsync matches
attributes by name, and this feature's canonical plan fingerprint sorts plan rows explicitly
(D001), so it is order-insensitive. The fix — sorting in the generator, then regenerating all
checked-in examples — is deliberately deferred to a separate PR, tracked as
`bug-generate-output-is-not-deterministic-across-runs.md` in the planning repo's
`proposed-issues/`.

**T041 verdict: PASS with the D014 expected state** — (a) and (b) clean as required, (c)
churned exactly as predicted, verified content-lossless, and restored.

### T042 — disclosed deviations

Four ratified deviations, recorded here as this run's honest output rather than omissions.

**D015 (RATIFIED) — the one DBR-009 byte-identity deviation.** The only difference in the
CLI's observable output across the refactor is the rendered **logger name** on the lifecycle
lines that moved into the shared surface:

```diff
-INFO | infrahub_sync.cli       | Cached run test-run at <CACHE>/from-netbox/test-run
+INFO | infrahub_sync.execution | Cached run test-run at <CACHE>/from-netbox/test-run
```

Cause is structural: `_setup_logging`'s formatter is `"%(levelname)s | %(name)s |
%(message)s"`, so the record's origin logger name is rendered, and the lifecycle calls now
execute in `execution.py`, which logs via `getLogger(__name__)` per the binding convention.
Message text, level, ordering, exit codes, `run.json` contents, artifacts are all unchanged,
and the CLI-vs-remote canonical plan fingerprints are **identical** (`669cdb37…f32c2780`,
T031). Accepted and disclosed; the two alternatives both touch ratified ground (renaming the
logger contradicts the logging convention and the live T019/T021/T024 evidence; a
caller-supplied logger seam is a contract change).

**D014 (RATIFIED) — `generate` non-idempotence.** Full evidence under T041 above.

**Corrected inherited baseline — `invoke lint` exits 30, not 0.** This contradicts the
brief's R-4 claim of exit 0. Independently re-measured at `9edc1bc` in the same environment:
exit 30, 56 diagnostics, rating 9.60/10. The final tree is exit 30, 54 diagnostics, 9.68/10 —
zero new. Detail and the count correction under T039 above.

**R-5 — `pytest-timeout` absent**, so `@pytest.mark.timeout(5)` at
`tests/test_potenda_parallel.py:70` is a silent no-op. Pre-existing, deliberately not fixed;
visible in T040's own warning output above.

Three issues filed to the planning repo's `proposed-issues/`, all present on disk:

```text
bug-generate-output-is-not-deterministic-across-runs.md
bug-invoke-format-corrupts-markdown-and-invoke-lint-masks-pylint.md
housekeeping-stale-dependency-caps-hold-back-current-releases.md
```

The dependency-caps issue also notes that `uv.lock` pins the **yanked** `diffsync==2.2.2`
(`uv.lock:676-677`), inherited from `main`.

### T042 — final gate table

| Gate | Command | Inherited baseline (same env, `9edc1bc`) | Final tree (`25dc955`) | Verdict |
|---|---|---|---|---|
| Python formatters | `uv run invoke linter.format` | no diffs | 82 files unchanged, exit 0, tree clean | PASS |
| Markdown | `uv run rumdl check .` | clean | clean, 73 files, exit 0 | PASS |
| Lint chain | `uv run invoke lint` | exit 30, 56 diagnostics, 9.60/10 | exit 30, **54** diagnostics, 9.68/10, **zero new / two removed** | PASS (no regression) |
| Ruff | (first leg of the chain) | clean | clean, no `RUF100` | PASS |
| YAML | `uv run invoke linter.lint-yaml` | clean | exit 0 | PASS |
| Types | `uv run ty check .` | 3 diagnostics, exit 0 | **4** diagnostics, exit 0, zero errors, no overrides | PASS (expected +1, prometheus) |
| Tests | `uv run pytest -q` | 111 passed, 2 skipped | **263 passed, 4 skipped**, exit 0 | PASS |
| CLI (a) | `uv run infrahub-sync --help` | — | exit 0, tree clean | PASS |
| CLI (b) | `uv run infrahub-sync list --directory examples/` | 14 configs | 14 configs, exit 0, tree clean | PASS |
| CLI (c) | `uv run infrahub-sync generate --name from-netbox --directory examples/` | churns | 242 lines / 4 files, content-lossless, restored | PASS per D014 |

### T042 — live-environment ceiling

**None.** Every live verification in this run executed in full against the lab — T018, T019,
T021, T024, T030, T031, T037 — so no criterion needed substitute local evidence. No entry is
required under the spec's informed default for DBA-002–005, DBA-008, DBA-009's paired
comparison, or DBA-011.

### Phase 8 environment discipline

```text
No infrahub-* container stopped, restarted, modified, or exec'd into.
No prefect server or serve process started in this chunk (none needed).
Destination state, read-only verification at the end of this chunk:
  InfraDevice count in destination = 0        ← unchanged; nothing written
No secret value printed: credentials were probed for PRESENCE and LENGTH only
  (INFRAHUB_ADDRESS: SET, length 21; INFRAHUB_API_TOKEN: SET, length 36).
No `git stash` and no `--amend` used at any point in this chunk. The pylint baseline
  was measured via `git archive` into a scratch directory, leaving the working tree
  untouched, precisely to avoid the stash/checkout pattern two earlier chunks had to
  disclose.
Final tree state: clean.
```
