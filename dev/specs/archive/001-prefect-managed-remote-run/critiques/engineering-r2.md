# Engineering Critique — Round 2 (verification)

**Feature**: `dev/specs/001-prefect-managed-remote-run` (branch `001-prefect-managed-remote-run-local-dp-001`)
**Scope**: bounded verification round. Closure verdicts for round-1 blocking engineering
findings (E1–E6, E8), targeted verification of the D009 mechanism now specified in
`contracts/execution-surface.md`, and new defects introduced by the remediation
(`c857406`). Settled decisions D001–D011 are not re-litigated; round-1
Recommended/Nit items are not re-opened.
**Reviewer stance**: fresh agent. Every verdict below was re-derived from the code at
`9edc1bc` and the current artifacts — not from round 1's write-up. Where a mechanism's
soundness depended on library behavior (ruff, ty, filelock, traceback rendering) it was
probed, not assumed; the probe command and output are cited.
**Severity vocabulary**: Must-Address (blocking), RETHINK (blocking, design-level),
Recommended, Nit.

---

## 1. Closure table — round-1 blocking findings

| ID | Verdict | Evidence |
|---|---|---|
| E1 — `__cause__`-keyed CLI error mapping | **CLOSED** | The mapping is gone. `contracts/execution-surface.md` §"Failure semantics" replaces it with identity-by-construction and states the reason (both load paths wrap into `ValueError`). Re-derived the three behaviors from `cli.py` myself: factory `ValueError` → prefixed abort (`cli.py:139-140`, `237-238`); serial-load `ValueError` → unprefixed abort (`cli.py:265-268`); everything else → broad mark-failed + bare re-raise (`cli.py:156-159`, `285-288`). The contract now reproduces each at its original site (wrapper factory / `_serial_load_error` / preserved step-6 except), and the per-stage CLI test list (T025/T026/T027) covers all five stages. Mechanism verification and its residues: §2 and E16/E18/E19. |
| E2 — step 6 vs "no broad except" claim | **CLOSED** | Both halves fixed honestly. Contract step 6 now *is* the preserved `9edc1bc` pattern (`except Exception: status="failed"; save(); raise`), with the run.json-never-stuck-at-`running` rationale; `plan.md` Constitution row IV was rewritten to "**PASS (with two documented broad-except sites, D009)**" and names both sites instead of claiming declared-boundaries-only. The compliance *mechanism* it prescribes is wrong — see E16 — but the design statement is now internally consistent. |
| E3 — `sys.modules` probe vs full-suite env | **CLOSED** | `contracts/prefect-flow.md` §6 and T028 now specify a subprocess-isolated probe (`subprocess.run([sys.executable, "-c", script])`) asserting over the child interpreter's `sys.modules`, with the static import-graph check kept in-process, and both artifacts state *why* (collection imports `tests/orchestration/test_flow.py`; `--dist loadscope`). Sound: the child imports only `infrahub_sync*` + typer (base deps), so no collection-time pollution can reach it; T040's full-suite mandate and T028 can now both hold. |
| E4 — log bridge ignores the source logger's level | **CLOSED** | Level ownership is now specified in four places consistently: spec clarification #4, `contracts/prefect-flow.md` §2 step 1–2 and §4, T014, and T016's new root-at-WARNING case. Mechanism is sound: setting `logging.getLogger("infrahub_sync").level = INFO` makes the effective level INFO regardless of ancestors (first non-`NOTSET` logger in the chain wins), and `grep -rn setLevel infrahub_sync/` shows only `cli.py:44` (package logger) and `potenda/__init__.py:82` (`diffsync`, outside the bridged hierarchy), so no child logger can re-gate the records. Residues: E20 (research.md row not carried forward), E21 (process-isolation assumption unrecorded). |
| E5 — `raise ... from exc` re-leaks the cause | **CLOSED** | `contracts/run-result-and-errors.md` §2 now makes whole-cause-chain redaction binding, with a testable property ("a full `traceback.format_exception(...)` rendering must contain no unredacted original message anywhere in the chain"), and T005/T022 carry it. Probed the prescribed mechanism: `raise RunExecutionError(redacted) from RuntimeError(redact(str(exc)))` renders only redacted text — `raise ... from ...` sets `__suppress_context__`, so the original `__context__` is not printed (probe output: `NO LEAK`). Implementable and self-consistent. |
| E6 — eager whole-directory config validation | **CLOSED (blocking halves)** | D010's tolerant per-file walk closes both blocking halves: blast radius (unrelated broken file → WARNING + skip, resolution continues) and the pydantic `input_value` leak (parse detail never chained verbatim; logical name + path only). Specified in `contracts/execution-surface.md::resolve_sync_instance`, data-model §1 step 4, plan Design Outline #1, T006, and T011's new tolerant-walk positive case; CLI `get_instance` explicitly untouched, so the r1 concern about CLI blast radius does not transfer. The new rule *set* is self-contradictory for unreadable/unparseable files — a new finding, E17, not a re-open of E6. |
| E8 — parity test / summary derivation | **CLOSED** | Single-source in-memory derivation is now binding in three places (contract step 7, data-model §2 "Derivation", T027's explicit NOTE) and the MagicMock trap is named. Verified the population survives it: `MagicMock()._diff_to_rows(...)` iterates empty (`list(rows) == []`, `len == 0`), and the `run_id == Path(artifact_path).name` invariant holds for the existing fakes (`run_id="test-run"`, `run_dir=.../from-netbox/test-run` in `tests/test_cli_parallel.py:24-25`, `tests/test_cli_full_extract.py:45-46`, `tests/cache/test_cli_sync_cache.py:20-21`), so `RunResult.__post_init__` cannot fail on the CLI path under T027. T029 now mandates a behavioral typed `FakePotenda` writing real rows through `cache.parquet_io.write_plan(*, run_dir, rows)` (signature confirmed, `parquet_io.py:81`) instead of the MagicMock pattern, plus the E7 nested-diff pin. The remaining gap is *how* the fake reaches the remote side — E18. |

**Round-1 blocking findings still open: 0.** All seven are closed on their own terms;
none regressed.

---

## 2. D009-mechanism verification (the flagged elaboration)

Checked construction-identity, implementability, and typing of the CLI wrapper factory +
`_serial_load_error` seam against `infrahub_sync/cli.py` at `9edc1bc`.

**Construction identity — verified item by item:**

| Claim in the contract | Verified against code | Verdict |
|---|---|---|
| Wrapper factory keeps the prefixed abort at the construction site, and DBA-009 patches stay effective | A function defined in `cli.py` calling the module global `get_potenda_from_instance` resolves it at call time, so `patch("infrahub_sync.cli.get_potenda_from_instance", ...)` (`tests/test_cli_parallel.py:42`, `tests/test_cli_full_extract.py:61`, `tests/cache/test_cli_sync_cache.py:33`) still intercepts. None of those tests asserts factory call kwargs (`grep assert_called/call_args` → no factory assertions), so the call shape is free. | OK |
| "No run.json exists yet at factory time" | `RunFile` is constructed only after the factory returns (`cli.py:146`, `cli.py:244`); the factory try/except sits above it (`cli.py:130-140`, `228-238`), inside `with pipeline_lock` in both commands | OK |
| Abort propagates out of `execute_run` untouched | Step 3 has no catch; step 6's try has not been entered; `pipeline_lock` re-raises after releasing (`cache/locks.py:29-33`) | OK |
| Factory `ImportError` surfaces as today's uncaught traceback | Today only `ValueError` is caught; `utils.py:193` raises `ImportError`. The wrapper catches only `ValueError`, so identical | OK |
| Serial-load seam reproduces `cli.py:263-268` **and** `285-288` | Today: mark failed → save → abort → outer broad except marks failed, saves again, re-raises → typer prints `Aborted.`, exit 1. New: same inner order, then step 6's preserved except marks/saves/re-raises → same exit. Two run.json saves in both, same content | OK |
| `filelock.Timeout` unchanged on the CLI path | Lock moves from the command body into step 2; nothing catches it in either design | OK (but see E21) |
| Lifecycle failures keep original type at the CLI | Step 6 bare re-raise == `cli.py:156-159`/`285-288` | OK |

**Typing — probed, not assumed.** A standalone probe of the exact seam shapes (wrapper
factory with a `NoReturn` abort in the `except` branch and no fallthrough return; `_serial_load_error:
Callable[[ValueError], NoReturn] | None`; the CLI's `lambda exc: print_error_and_abort(str(exc))`
passed at the call site; `PotendaFactory = Callable[..., Potenda]`) → `uv run ty check` **All checks
passed**, and `uv run ruff check --config pyproject.toml` flags nothing beyond `ANN401`
on `**kwargs: Any`, which is ignored for `infrahub_sync/**`. The seam is typed cleanly
and lint-clean *as written*.

**Holes found in the mechanism** (detailed below): **E16** (the `# noqa: BLE001` the
contract, plan and T007/T008/T039 all mandate is itself a lint error at one of the two
sites), **E18** (the mechanism gives the CLI two private seams but leaves
`run_remote_request` with none, and four tasks require injecting through it),
**E19** (`typer.Exit` named where the code raises `typer.Abort`), **E21/E22** (the CLI's
`with pipeline_lock` removal and the `run_dir is None` guard are not carried into the
refactor tasks).

Net: the D009 mechanism is construction-identical and implementable; three of the four
holes are artifact-text fixes of one to three lines each.

---

## 3. New findings (remediation-introduced)

### E16 — Must-Address: the mandated `# noqa: BLE001` is itself a lint failure at the step-6 site (RUF100), so T039's gate cannot pass as specified

**Artifacts**: `contracts/execution-surface.md` step 6 and `run_remote_request`
("with a targeted `# noqa: BLE001`"); `plan.md` Constitution row IV (both sites);
tasks T007 (6), T008, T039 ("the two documented D009 broad-except sites carry their
targeted `# noqa: BLE001`").

**Evidence (probed)**: ruff's BLE001 does not fire on a blind `except` whose handler
re-raises. Probes under the repo config (`select = ["ALL"]`, no `BLE` in
`ignore` or in the `infrahub_sync/**.py` per-file ignores; `RUF100` live):

- `except Exception:` + bare `raise` (step 6's preserved pattern) → **no BLE001**; with
  the mandated comment, `RUF100 Remove unused 'noqa' directive`, and
  `ruff check --diff .` (what `invoke lint` runs, `tasks/linter.py:51-52`) exits **1**.
- `except Exception as exc:` + `raise RuntimeError(msg) from exc` → **no BLE001** either.
- `except Exception as exc:` + `raise ... from None`, or `+ raise ... from <rebuilt
  sanitized cause>` (the contract's own §2 mechanism for `run_remote_request`) → **BLE001
  fires**, so the noqa *is* required there.
- Today's `cli.py` needs no noqa: `uv run ruff check --select BLE infrahub_sync/cli.py`
  → `All checks passed!`.

**Failure scenario**: an implementer follows the contract literally, adds both noqa
comments, and `uv run invoke lint` fails on RUF100 at the `execute_run` site — the exact
gate T039 requires to exit 0. Worse, the noqa is a false signal: it tells the next
reader BLE001 was suppressed at a site where the rule never applied, which is the
opposite of the ratchet discipline the plan row is trying to demonstrate.

**Recommendation**: drop the noqa mandate from step 6 (contract, plan row IV, T007,
T039) and state the real fact — *BLE001 does not flag a blind except that re-raises, so
the preserved pattern needs no suppression*; that is a stronger constitution-IV argument
than a suppression. Keep the noqa requirement in `run_remote_request` **conditioned on
the chosen chain mechanism** (required for a rebuilt/suppressed cause; not required if
the raise chains `from exc`), and say so, since §2 permits both.

### E17 — Must-Address: D010's per-file rules contradict each other for unreadable/unparseable files, and raw-name extraction is unspecified

**Artifacts**: `contracts/execution-surface.md::resolve_sync_instance` (per-file behavior,
binding); data-model §1 step 4; T006; T011's negative and tolerant-walk cases.

**Evidence**: the two binding rules are keyed on the file's *raw* `name:`, but rule 2
also swallows the case where that name cannot be obtained:

- Rule 1: "a file that fails to read or parse and whose raw `name:` does **NOT** match
  the request is skipped with a WARNING … and resolution continues."
- Rule 2: "A file whose raw `name:` matches the request but whose content is invalid —
  **or a file that is unreadable where the name may live** — raises RunValidationError."

Every file whose `name:` cannot be read is a file "where the name may live". So for an
`OSError`-unreadable neighbor (e.g. `chmod 000` on some unrelated `examples/*/config.yml`),
rule 1 says skip and rule 2 says raise — and under rule 2 one unrelated unreadable file
again blocks resolution of *every* name, which is the blast radius D010 exists to remove.
The same collision covers the most common real case: a half-edited `config.yml` raising
`yaml.YAMLError`, where no name is extractable at all (`utils.py:123-135` shows the only
existing extraction path is a full `yaml.safe_load` + `SyncConfig(**data)`), yet T011
explicitly requires "bad YAML … → `RunValidationError` naming the logical name and the
file path ONLY" — an assertion that presupposes an extraction mechanism the artifacts
never specify.

**Failure scenario**: two implementers read the same binding contract and ship opposite
behavior for the same input; T011's bad-YAML case passes for one and is unsatisfiable for
the other. The reading that "raise" wins re-introduces the r1 blast radius for
permission/IO-broken neighbors.

**Recommendation**: specify the extraction and make the rules total and disjoint:
(1) attempt `yaml.safe_load`; on success use `data.get("name")` for the match test even
when `SyncConfig(**data)` later fails; (2) on `YAMLError`/`OSError` the name is
*undeterminable* — pick one branch explicitly and say so (recommended: skip with a
WARNING naming the path and count it, then, if nothing matched, raise the not-found
`RunValidationError` mentioning that N file(s) under the directory could not be read —
never their contents). Delete the "unreadable where the name may live" clause from
rule 2, and align T011's bad-YAML case with the chosen branch.

### E18 — Must-Address: `run_remote_request` exposes no test seam, but four tasks require injecting a fake engine or a shortened lock timeout *through* it

**Artifacts**: `contracts/execution-surface.md::run_remote_request` ("the private seams
are never set"); tasks T011 (remote lock-contention case), T022 (canary scan), T023
(execution-fault case), T029 (parity comparison).

**Evidence**: the remote composition takes exactly `sync_name, operation, confirm_writes,
branch, *, config_directory` — no `potenda_factory`, no `_lock_timeout` — and D009 puts
the wrap boundary *inside* it, so any test that must observe a sanitized
`RunExecutionError` has to drive the real `run_remote_request`. Four tasks then specify
mechanics that do not exist:

- T022: "patch point: a fake `potenda_factory` plus the temp configuration directory
  ONLY … do not patch `run_remote_request` here" — there is no parameter to pass a fake
  factory through. This is the DBA-008/SC-005 canary evidence.
- T023: "one execution fault (unreachable system via patched factory) → `RunExecutionError`
  wrapped at the `run_remote_request` boundary" — same gap.
- T029: runs `run_remote_request(..., config_directory=...)` against the behavioral
  `FakePotenda` — same gap; this is E8's own remediation.
- T011: "pipeline-lock contention via the sanctioned `_lock_timeout` seam … and the same
  scenario through `run_remote_request` → `RunExecutionError`" — `run_remote_request`
  never sets `_lock_timeout`, so the remote half of that test waits the full 60 s. E15's
  seam was named for `execute_run` only; the D009 boundary move created a second need
  the contract did not follow up on.

**Failure scenario**: the tests get written with an improvised patch target
(`infrahub_sync.utils.get_potenda_from_instance` vs `infrahub_sync.execution.get_potenda_from_instance`
vs `infrahub_sync.execution.pipeline_lock`), and the choice silently determines whether
the CLI wrapper factory is also intercepted; or T011's remote case sleeps 60 s per run.

**Recommendation**: name the sanctioned seams in the contract's compatibility
constraints, next to the existing CLI module-global rule: `infrahub_sync/execution.py`
imports `get_potenda_from_instance` and `pipeline_lock` as module globals and resolves
them at call time, so tests monkeypatch `infrahub_sync.execution.get_potenda_from_instance`
and `infrahub_sync.execution.pipeline_lock` (patching the surface's names must not affect
the CLI's own global — state that too). Then update T011/T022/T023/T029 to name that
target instead of "a fake `potenda_factory`".

### E19 — Recommended: the contract names `typer.Exit` where the code raises `typer.Abort` — different exit codes

**Artifacts**: `contracts/execution-surface.md` §"Failure semantics", factory bullet
("The resulting `typer.Exit` propagates out of `execute_run` untouched") and serial-load
bullet ("the `typer.Exit` then passes through the preserved outer broad except").

**Evidence**: `print_error_and_abort` raises `typer.Abort` (`cli.py:72-74`), i.e.
click's `Abort` (a `RuntimeError` subclass — so the step-6 broad except does catch it,
and the control-flow claim itself holds). `typer.Abort` yields exit code 1 with
`Aborted.`; `typer.Exit` defaults to exit code **0**.

**Failure scenario**: an implementer or test author takes the contract literally and
raises/asserts `typer.Exit`, and a factory failure exits 0 — DBR-009's "exit codes
identical" broken in the direction that hides failures from CI.

**Recommendation**: replace both mentions with `typer.Abort` and have T025/T026 assert
the exit code (1) and the `Aborted.` output, not just the message wording.

### E20 — Recommended: `research.md` decision rows were not carried forward, so the design record now contradicts the binding contracts

**Artifacts**: `research.md` "Log bridging" row and "Secret redaction" row (file untouched
by `c857406` — `git show c857406 --stat` lists no `research.md`).

**Evidence**: the "Log bridging" row still describes only "Handler attached … removed in
`finally`", the exact mechanism E4 showed to be insufficient, with no level ownership;
the "Secret redaction" row still scopes candidates to "env `INFRAHUB_API_TOKEN`; values
of `token`/`password`/`secret`/`api_key` keys", i.e. pre-E10 (no `*_TOKEN`/`*_PASSWORD`/
`*_SECRET`/`*_API_KEY` name patterns) and pre-E5 (no cause-chain sanitization). Both were
named as affected artifacts in r1's findings.

**Failure scenario**: research.md is the rationale record later briefs and the extract/ADR
step read; two rows now teach the superseded mechanism, and an implementer who works from
research instead of the contract rebuilds the E4/E5/E10 defects.

**Recommendation**: amend the two rows (or append a dated "superseded by D004/E5/E10 —
see contracts/…" note in the same append-only style the checklists used) so no artifact
states the pre-remediation mechanism.

### E21 — Recommended: two lock facts the refactor tasks don't state — the CLI's `with pipeline_lock` must be removed, and the flow's global-logger mutation assumes process isolation

**Artifacts**: T025/T026 (CLI refactor); `contracts/execution-surface.md` step 2;
`contracts/prefect-flow.md` §§2–4.

**Evidence (a)**: the lock moves into `execute_run` step 2, but T025/T026 only say what
*stays* in the command body ("the existing `get_instance` resolution and `adapter_path`
merging"); neither says the `with pipeline_lock(sync_instance.name):` wrapper at
`cli.py:129` / `cli.py:227` is removed. Leaving it is not a no-op: probed two `FileLock`
objects on the same path in one process → the second times out (`TIMEOUT after 1.0s`),
so every CLI `diff`/serial `sync` would block 60 s and then raise `filelock.Timeout` —
T027's population fails slowly rather than obviously.

**Evidence (b)**: the flow mutates process-global logging state (`logging.getLogger("infrahub_sync")`
level + handler). That is safe only if each flow run occupies its own process. Nothing in
`research.md`, the contract, or the tasks records that assumption, and probe c₁ was a
single run. Two concurrent runs of *different* configurations (the per-configuration lock
does not exclude them) sharing a process would cross-attach bridges — one run's records,
including any adapter detail, forwarded into the other run's Prefect log — and one run's
`finally` would restore the level under the other.

**Recommendation**: (a) state the removal explicitly in T025/T026 ("delete the
`with pipeline_lock(...)` wrapper — `execute_run` owns it; keeping both self-deadlocks
for the lock timeout"). (b) Record the process-isolation assumption where D004 lives, and
verify it once with the existing probe rig (submit two concurrent runs of two
configurations, confirm distinct PIDs); if serve ever executes runs in-process, the
bridge must key on the current run and the level mutation must be reference-counted.

### E22 — Nit: today's `run_dir is None` guard is not carried into the surface contract

`cli.py:143-145` / `241-243` raise `RuntimeError("get_potenda_from_instance did not
allocate a run_dir")` before building the `RunFile`. Contract steps 4/5 and T007 don't
mention it, yet it is load-bearing twice: `Potenda.run_dir` is `Path | None`
(`potenda/__init__.py:51`), so `run_dir / "run.json"` needs the narrowing for T039's ty
gate, and the message is part of today's observable behavior for a misbehaving factory.
Name it in step 3/4 so it is preserved deliberately rather than rediscovered as a type
error.

### E23 — Nit: the kwarg set `execute_run` passes to `potenda_factory` is unpinned

Today the two commands call the factory with *different* kwargs: `diff` passes
`run_id` + `concurrent_load` and omits `continue_on_error` (`cli.py:131-138`); `sync`
passes `continue_on_error` + `concurrent_load` and omits `run_id` (`cli.py:229-236`).
`PotendaFactory = Callable[..., "Potenda"]` and the contract's step 3 leave the call
shape free, and the DBA-009 population asserts nothing about it, so the omitted values
default to exactly what the surface would pass explicitly — behaviorally identical for
`utils.get_potenda_from_instance`. Worth one line in step 3 pinning the single call
shape, since a custom or fake factory sees the difference.

---

## 4. Summary

| ID | Severity | One-line |
|---|---|---|
| E16 | Must-Address | The mandated `# noqa: BLE001` at `execute_run` step 6 is an RUF100 error (BLE001 never fires on a re-raising blind except) — T039's `invoke lint` exit-0 gate fails as specified; at `run_remote_request` the noqa is mechanism-dependent |
| E17 | Must-Address | D010's per-file rules collide for unreadable/unparseable files (skip vs raise), raw-`name:` extraction is unspecified, and T011's bad-YAML assertion presupposes it |
| E18 | Must-Address | `run_remote_request` exposes no factory/lock-timeout seam, yet T011/T022/T023/T029 (incl. the DBA-008 canary scan and E8's own parity fix) must inject through it |
| E19 | Recommended | Contract says `typer.Exit` where `print_error_and_abort` raises `typer.Abort` — exit 0 vs 1 if taken literally |
| E20 | Recommended | `research.md` "Log bridging" and "Secret redaction" rows still teach the pre-E4/E5/E10 mechanisms — design record contradicts the binding contracts |
| E21 | Recommended | T025/T026 never say the CLI's `with pipeline_lock` is removed (probed: same-process double acquire → 60 s timeout); the flow's global-logger mutation's process-isolation assumption is unrecorded |
| E22 | Nit | Today's `run_dir is None` RuntimeError guard not carried into the contract/T007 (needed for parity and for ty narrowing of `Path | None`) |
| E23 | Nit | The factory call shape `execute_run` uses is unpinned while today's two commands pass different kwargs |

**Round-1 blocking findings remaining open: 0** (E1, E2, E3, E4, E5, E6, E8 all CLOSED;
none REGRESSED).
**New blocking findings: 3** (E16, E17, E18 — all Must-Address, all artifact-text fixes;
no RETHINK, no settled decision needs re-opening).
