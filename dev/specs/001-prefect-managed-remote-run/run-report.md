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
uv run invoke lint   # exit 1 — NOT exit 0
```

**Discrepancy vs tasks.md T003.** The task text expects exit 0. The measured baseline is
**exit 1**, and the cause is *not* the pylint warnings the task text anticipated:
`invoke lint` runs `docs.lint` (rumdl) first, and rumdl fails on three Markdown issues
in this feature's own committed planning artifacts:

```text
dev/specs/001-prefect-managed-remote-run/spec.md:161:1: [MD076] Unexpected blank line between list items
dev/specs/001-prefect-managed-remote-run/tasks.md:262:2: [MD018] No space after # in heading [*]
dev/specs/001-prefect-managed-remote-run/plan.md:265:1: [MD036] Emphasis used instead of a heading: '(empty — no constitution violations)'

Issues: Found 3 issues in 3/70 files (25ms)
```

These arrived with planning commit `a65f568` ("[Spec Kit] Apply ratified gate
decisions"); they are absent from `main`, which carries no `dev/specs/001-*` content.
They are inherited by this chunk, not caused by it. Note that `MD018` on `tasks.md:262`
is the same prose line `rumdl fmt` corrupts (above), so "just autofix it" is not
available — flagged upward.

Because rumdl aborts the run, `invoke lint` never reaches the Python linters. Measured
individually:

| Task | Exit | Result |
|---|---|---|
| `linter.lint-ruff` | 0 | `67 files already formatted`, all checks passed |
| `linter.lint-pylint` | **30** | 29 × `C0415`, rated **9.60/10** |
| `linter.lint-yaml` | 0 | clean |
| `linter.lint-ty` | 0 | `Found 3 diagnostics` |

**Pylint would fail the gate independently of rumdl.** `tasks/linter.py` calls
`context.run(exec_cmd)` with no `warn=True`, so a non-zero pylint exit propagates. The
inherited message population over `infrahub_sync/`:

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
| `E0213` | 1 | `no-self-argument` — `infrahub_sync/__init__.py:98`, a pydantic validator false positive |

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

### T002 open item — `generate` output is not reproducible

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

So the emitted order tracks whatever order the Infrahub API returns, which is not stable
across calls. Observed: the committed run produced adapter md5 `a9b76fb…`; every
subsequent run in this worktree produced `cd9fcda…` — a pure reordering
(242 insertions / 242 deletions, zero content change). Restoring the exact pre-T002 file
state and regenerating still yields `cd9fcda…`, so this is not
previous-file-state dependence; it is response-order dependence. Runs 2–6 were
byte-identical to each other.

`e04f262` therefore records one honest `generate` run (as mandated) but is **not** a
fixed point: a fresh `generate` here now produces a 242-line reordering diff. Resolving
this needs a decision that exceeds this chunk's authority, because the options differ in
scope and blast radius:

- **A** — add `|sort()` to `diffsync_adapter.j2:26` and `diffsync_models.j2:29`, making
  `generate` deterministic for every user. A real bug fix, but it is source change
  outside Phase 1 and it rewrites generated output repo-wide.
- **B** — commit the current stable fixed point as a follow-up regeneration commit. Cheap
  and reversible, but it splits T002 across two commits (the plan binds commit 2 to T002
  "alone") and still enshrines one server's ordering.
- **C** — leave `e04f262` as the baseline-hygiene commit and accept that T002's re-run
  check cannot pass until A lands.

Left at **C** pending orchestrator direction; the working tree is clean at `e04f262`.
