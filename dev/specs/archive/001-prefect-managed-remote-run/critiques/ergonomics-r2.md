# Ergonomics Critique — Round 2 (bounded verification)

**Lens**: consumer experience — remote API caller, serve-process operator, the DBA-011
stranger, contributor running the repo workflow, future brief authors.

**Reviewed**: `critiques/ergonomics-r1.md` (X1–X14), `critiques/collation-r1.md`
(dispositions, D009–D011), remediation commit `c857406`, and the CURRENT artifacts
(`spec.md`, `plan.md`, `tasks.md`, `quickstart.md`, `data-model.md`, `contracts/*`,
`checklists/*`), verified fresh against the working tree — including re-reading the
repository code the round-1 claims rested on (`examples/custom_adapter/config.yml`,
`custom_adapter.py:40-63`, `mockdb/sync_adapter.py:12`, `plugin_loader.py:235-238`,
`cache/paths.py:43`, `utils.py:123-148`). One empirical probe was run (X15). D001–D011
treated as settled; Recommended/Nit round-1 items re-checked only where the remediation
touched them.

Severity vocabulary: Must-Address (blocking) · RETHINK (blocking, design-level) ·
Recommended · Nit.

---

## 1. Closure table — round-1 blocking findings

| ID | Verdict | Evidence |
|---|---|---|
| X1 (CWD-dependent example silently yields zero-create "success") | **CLOSED** | Working-directory pin now appears at every surface a consumer touches: `quickstart.md:22-27` (Prerequisites: bold CWD rule with the causal explanation — `./` paths + `Path.cwd()/.infrahub-sync-cache`) and `quickstart.md:66-70` (repeated next to the Terminal B serve command); `contracts/prefect-flow.md` §3 "Working directory (binding for the qualified example)" prerequisite block requiring the README to state it both as a prerequisite AND next to the serve command; `tasks.md` T034 encodes exactly that dual placement plus the recognizably-wrong checkpoint (the `Loading 5 InfraDevice nodes` narration and `summary=create:5,update:0,delete:0`); T018 pins the live serve start "from the REPOSITORY ROOT". The recommended fixture hardening landed as new task T033a: `MockDBClient` logs a WARNING through a bridged `infrahub_sync.*` logger when the configured `db_path` does not exist, turning the silent empty plan into a remotely visible diagnosis. Underlying facts re-verified in tree: `config.yml` `./` paths (lines 6, 9), `plugin_loader.py:237` `Path(path).resolve()` CWD resolve, `custom_adapter.py:40-49` `self.data = {}` fallback guarded by `if filepath and Path(filepath).exists()`, `cache/paths.py:43` `Path.cwd() / ".infrahub-sync-cache"`. |
| X2 (eager config discovery: one broken neighbor bricks every remote run; contract promise unimplementable) | **CLOSED** | Decision D010 (collation-r1) ratified the tolerant per-file walk and the artifacts now carry it end-to-end: `contracts/execution-surface.md` `resolve_sync_instance` docstring — the round-1 "Semantics identical to `utils.get_instance`" language is gone, replaced by a binding per-file behavior list (unrelated broken file → WARNING naming the path only, resolution continues; matched-but-invalid → `RunValidationError` naming logical name + file path only, parse detail never chained verbatim); `data-model.md` §1 validation step 4 and the state-transition diagram ("unrelated broken neighbor: WARNING + skip"); `tasks.md` T006 (implementation, explicitly "NOT a reuse of `utils.get_all_sync`'s eager validate-everything pass") and T011 (both the matched-but-invalid negative AND the new tolerant-walk positive — an unrelated invalid config no longer blocks the requested name). The round-1 test-unwritability complaint is resolved: T011's cases are now satisfiable as specified. CLI untouched (`get_instance` per the caller-obligations table), as recommended. Eager behavior re-verified at `utils.py:128-133` (`SyncConfig(**config_data)` per discovered file before any name comparison). One residual stale sentence noted as X16 (Nit). |
| X3 (no-prefect-import test fails/flakes in the documented dev+prefect environment) | **CLOSED** | `tasks.md` T028 now specifies the subprocess-isolated probe exactly as recommended: a script importing `infrahub_sync`/`.cli`/`.execution`, running CLI sanity in-process, asserting over ITS OWN interpreter's `sys.modules`, executed via `subprocess.run([sys.executable, "-c", script])`; the static import-graph half stays in-process (collection-safe). `contracts/prefect-flow.md` §6 rewritten to match, including the honest statement of WHY in-process assertion is unsound (collection imports `tests/orchestration/test_flow.py`, `--dist loadscope` per-worker arbitrariness) and the note that quickstart Scenario 0 remains the authoritative SC-006 evidence. The round-1 contradiction between "passes regardless of the extra" and "environment where prefect is absent" no longer exists. |

**No REGRESSED verdicts.** All three round-1 blocking ergonomics findings are closed by
the current artifacts.

---

## 2. Spot-check results (remediation coherence)

### 2a. Deployment rename to `run` (X12) — PASS

`grep -rn "infrahub-sync/infrahub-sync\|deployments/name/infrahub-sync"` over the
artifacts (critiques excluded): every caller-typed occurrence uses
`/deployments/name/infrahub-sync/run` — `contracts/prefect-flow.md` §2
(`DEPLOYMENT_NAME = "run"` with the sibling-deployments rationale recorded as a code
comment, exactly as X12 asked) and §5 REST table; `quickstart.md:85` and `:91`;
`data-model.md` §7 (rename annotated `X12`); `tasks.md` T014 (constant + rationale),
T017 (integration lookup), T018 (live evidence URL). The only remaining
`infrahub-sync/infrahub-sync` text is the contract's own comment explaining what was
avoided. No stale doubled-name path anywhere a caller would type it.

### 2b. Fixed summary-line format (X7) — PASS

One format everywhere: `contracts/prefect-flow.md` §2 step 3 pins
`"run %s finished: status=%s changed=%s summary=create:%d,update:%d,delete:%d artifact=%s"`
and declares it the supported remote observation surface ("Any format change is a
breaking contract change"); §5 gains the previously missing "Read the result" row and
explicitly excludes Prefect result persistence from regression contract — the
either/or the round-1 finding demanded, decided as option (b). Consistent consumers:
`quickstart.md:102-104` (`summary=create:5,update:0,delete:0`), T014 (same format
string, "never a dict repr"), T017 (asserts the fixed-format line), T034 (README
checkpoint), T035 (docs page documents the format and that fields mirror `RunResult`).
No dict-repr remnant anywhere. Minor residue: the line's first `%s` placeholder is
semantically unnamed — see X18 (Nit).

### 2c. T033a fixture diagnosability vs the five-device outcome — PASS

T033a's two halves match the fixture as it exists in tree: (1) the WARNING on missing
`db_path` targets exactly the verified silent-fallback site
(`custom_adapter.py:40-49`), under a logger name (`infrahub_sync.examples.custom_adapter`)
that IS a child of the bridged `infrahub_sync` hierarchy — WARNING ≥ the flow-owned INFO
level, so it forwards; (2) the `print()` replacement covers the narration whose text
T034's checkpoint quotes — `custom_adapter.py:62` `print(f"Loading {len(nodes)} {model} nodes")`
renders precisely `Loading 5 InfraDevice nodes` for the intact five-device fixture, so
README checkpoint and fixture agree. "Five-device outcome unchanged" holds: both changes
are observability-only. The derived-adapter note is apt — `mockdb/sync_adapter.py:12`
subclasses the source adapter at runtime, so the logging change propagates without
regeneration and "regenerate/verify … stays consistent" is a cheap correctness check.
Ordering is coherent: Phase 7 dependencies place T033a after Phase 2 (logging
convention) and before T037, and T037's dependency list includes T033a, so the
clean-context walkthrough exercises the diagnosable fixture.

### 2d. T034 README content list — PASS (complete and internally consistent)

All six mandated additions present in one task: CWD pin (prerequisite line AND repeated
next to the serve command, with the causal `./`-paths/cache-root explanation);
scoped config dir (`$PWD/examples/custom_adapter` + the one-sentence scoping guidance —
verified in tree that `examples/custom_adapter` contains exactly one `config.yml`, so
the scoping claim in `quickstart.md:78-81` is factually right); empty-destination
prerequisite with verify command and reset pointer; port-4200 note with what a
collision looks like; "stopping and cleaning up" section (serve, server, `PREFECT_HOME`
SQLite, `.infrahub-sync-cache/`) plus the artifact-locality sentence; and the
install source — repo-checkout `pip install -e '.[prefect]'`, never PyPI, with the
stated reason. The install source is consistent across `contracts/prefect-flow.md` §1
(PyPI form "must not appear in the example README" — the F7 line), §3's serve
missing-extra message, and T015. Credentials-before-serve, placeholder sentinels
(T033 convention), and the trusted-environment caveat all carried forward. X4/X5/X6/X8/
X9/X10/X11/X13/X14 remediations were spot-checked incidentally and all landed where the
collation routed them (T015, T025+T011, quickstart+T018+T034, T017, T035, T016/T022/T023,
T034, T035, T033a respectively).

---

## 3. New findings (introduced or exposed by the remediation)

### X15 — The contracted flow return, `dataclasses.asdict(result)`, raises `TypeError` against the E14 mappingproxy `summary`; the success path cannot execute as written

**Severity**: Must-Address

**Locations**: `contracts/prefect-flow.md` §2 step 4 ("Return `dataclasses.asdict(result)`
(with `summary` materialized as a plain `dict` — the result's mappingproxy is not
deep-copyable)"); `tasks.md` T014 ("returns `dataclasses.asdict(result)` with `summary`
materialized as a plain dict"); `contracts/run-result-and-errors.md` §1 point 5 (same
wording); `tasks.md` T005 (the E14 `MappingProxyType` wrap that creates the collision);
T016 ("returns the `asdict` seven-key dict").

**Consumer affected**: every remote caller (the flow's SUCCESS path), and the T014
implementer following binding contract text.

**Failure scenario**: `dataclasses.asdict` deep-copies non-dataclass field values, and
`types.MappingProxyType` is not deep-copyable — verified empirically in this worktree's
environment: a frozen+slots dataclass whose `__post_init__` wraps `summary` in a
mappingproxy makes `dataclasses.asdict(instance)` raise
`TypeError: cannot pickle 'mappingproxy' object`. The contract's own parenthetical names
this exact fact ("the result's mappingproxy is not deep-copyable") yet still prescribes
the call that trips over it — there is no ordering in which you can run `asdict` first
and "materialize `summary` as a plain dict" second, because `asdict` never returns. A
literal implementation of T014 fails on the flow's success path: every successful remote
run ends FAILED at return time. T016's seven-key-dict assertion would catch it during
implementation, but binding contract text that prescribes an impossible call is a defect
regardless — the E14 remediation (mappingproxy wrap) and the pre-existing asdict return
were reconciled in words, not in mechanics.

**Recommendation**: replace the prescribed call in all three artifacts with explicit
construction, e.g.
`out = {f.name: getattr(result, f.name) for f in dataclasses.fields(result)}; out["summary"] = dict(result.summary)`
(a shallow, seven-key dict — the shape T016 already asserts), and describe the return as
"an asdict-shaped dict" rather than `dataclasses.asdict(...)`. No behavior change
anywhere else; T016 needs no edit.

### X17 — T011's remote-side lock-contention case cannot reach the sanctioned `_lock_timeout` seam it cites

**Severity**: Recommended

**Locations**: `tasks.md` T011 ("pipeline-lock contention via the sanctioned
`_lock_timeout` seam (held lock, shortened timeout — E15) … and the same scenario
through `run_remote_request` → `RunExecutionError` naming sync and timeout, bounded not
hanging"); `contracts/execution-surface.md` `run_remote_request` docstring ("the private
seams are never set" — binding) and the `_lock_timeout` seam comment ("sanctioned test
seam for lock-contention tests (T011)").

**Consumer affected**: the T011 implementer (contributor running the repo workflow —
the suite must stay fast).

**Friction**: the seam is only a parameter of `execute_run`; `run_remote_request`'s
contract forbids setting it. So "the same scenario through `run_remote_request`" either
waits the real 60-second default (unacceptable in a unit suite) or needs a mechanism the
task never names (monkeypatch `cache.locks.pipeline_lock` at the execution module, or
patch `execute_run` to raise `filelock.Timeout` and assert the wrap). The E15 remediation
sanctioned the seam precisely to avoid ad-hoc patching, then routed one required test
through the composition that cannot use it.

**Recommendation**: one sentence in T011 naming the sanctioned remote-side mechanism —
e.g. "the `run_remote_request` half patches `infrahub_sync.execution`'s lock acquisition
(or `execute_run`) to raise `filelock.Timeout`, asserting only the wrap" — so the
implementer isn't left choosing between a 60 s test and an unsanctioned patch.

### X16 — `data-model.md` §4 still attributes per-run re-resolution to `get_all_sync`, the mechanism D010 just replaced for the remote path

**Severity**: Nit

**Locations**: `data-model.md` §4 ("its **contents** are re-resolved on every run
(`get_all_sync` re-globs `**/config.yml`)"), vs `data-model.md` §1 step 4,
`contracts/execution-surface.md` `resolve_sync_instance`, and T006 (the remote path
performs its OWN tolerant walk and explicitly must NOT reuse `get_all_sync`).

**Friction**: an implementer skimming §4 for the re-resolution behavior is pointed at
the eager function the same document forbids two sections earlier. The per-run
re-resolution fact itself is unchanged and correct.

**Recommendation**: reword to "(`resolve_sync_instance` re-walks `**/config.yml` per
run — D010)".

### X18 — The now-contractual summary line's first placeholder is semantically unpinned

**Severity**: Nit

**Locations**: `contracts/prefect-flow.md` §2 step 3 (`"run %s finished: …"`; "its
fields mirror `RunResult` (`status`, `changed`, the three `summary` counts,
`artifact_path`)").

**Friction**: X7's remediation promoted this line to a parseable contract whose format
change is breaking — but the leading `%s` after "run" is the one substitution the
contract never names (`run_id`? `sync_name`?). Key=value parsers are unaffected (all
their fields are pinned), so this costs only the implementer a guess and the two sides
of T017's assertion a possible mismatch.

**Recommendation**: one word in §2 step 3: "the first `%s` is `run_id`" (mirroring
`RunResult` fully — six of seven fields would then appear on the line).

---

## Summary

| ID | Verdict / Severity | One-line |
|---|---|---|
| X1 | CLOSED | CWD pinned in quickstart prerequisites + serve command, contract §3, T034 (dual placement + checkpoint output), T018; T033a makes the missing-file case a bridged WARNING |
| X2 | CLOSED | D010 tolerant per-file walk fully propagated (contract docstring, data-model §1/state diagram, T006/T011); "identical to get_instance" language removed; T011 now writable |
| X3 | CLOSED | T028 + contract §6 specify the subprocess-isolated probe; import-graph half stays in-process; Scenario 0 remains authoritative |
| Spot 2a | PASS | Deployment rename to `run` propagated everywhere a caller types it; rationale recorded in the contract |
| Spot 2b | PASS | Fixed key=value summary format consistent across contract §2/§5, quickstart, T014/T017/T034/T035; result-persistence explicitly out of contract |
| Spot 2c | PASS | T033a matches the fixture's verified fallback and narration text; five-device outcome untouched; ordered before T037 |
| Spot 2d | PASS | T034 content list complete (CWD, scoping, empty-destination, port, cleanup, artifact locality, install source) and consistent with contract §1/§3 |
| X15 | **Must-Address** | `dataclasses.asdict(result)` raises `TypeError: cannot pickle 'mappingproxy' object` against the E14-wrapped `summary` (empirically verified) — the contracted flow return cannot execute; prescribe explicit dict construction in prefect-flow §2, run-result-and-errors §1.5, T014 |
| X17 | Recommended | T011's remote-side lock-contention case cites the `_lock_timeout` seam that `run_remote_request` is forbidden to set; name the sanctioned mechanism |
| X16 | Nit | data-model §4 still credits `get_all_sync` with the per-run re-resolution D010 moved to the tolerant walk |
| X18 | Nit | The contractual summary line's leading `%s` is never named (`run_id`) |

**Blocking findings remaining: 1** (X15 — new, remediation-introduced; X1/X2/X3 all
closed, none regressed). X15's fix is a three-artifact wording change with no design
impact; no RETHINK.
