# Implementation report — DONE (all 9 chunks, 44/44 tasks)

- **Feature:** Prefect-managed remote Infrahub Sync run (DB-001 / LOCAL-DP-001)
- **Spec dir:** `dev/specs/001-prefect-managed-remote-run`
- **Branch:** `001-prefect-managed-remote-run-local-dp-001`
- **Base commit (implementation start):** `a65f568`
- **Head commit:** `f643e2a`
- **Outcome:** complete. The run stopped after chunk 1 on a material decision, was unblocked by
  the owner's ratification of D014, then ran chunks 2–9 to completion and passed two
  review-and-remediation passes.

## Supersession

This report replaces the version committed at `7bdb8a8`, which read
"INCOMPLETE (stopped after chunk 1 of 9)" and ended `STATUS: INCOMPLETE`. That was accurate when
written: at `7bdb8a8` chunks 2–9 had not been dispatched. It stopped being accurate at
2026-07-31T~06:20, when D014 was ratified and the run resumed. The stop is kept in the record
below rather than erased — the blocking decision and its ratification are part of how this
feature was delivered.

## Chunk ledger

| # | Chunk | Tasks | Commit(s) |
|---|---|---|---|
| 1 | Phase 1: Setup (R-1, R-2, baseline) | T001, T002, T003 | `a586ff8`, `e04f262`, `161f6eb` |
| 2 | Phase 2: Foundational — shared surface + fingerprint | T004–T011 | `a6e3349` |
| 3 | Phase 3 (US1): code — extra, `orchestration/`, log bridge | T012, T012a, T013–T017 | `8c2e437` |
| 4 | Phase 3 (US1): live verification | T018, T019 | `8046690` |
| 5 | Phase 4 (US2): confirmed-write lifecycle | T020, T021 | `b8329f7` |
| 6 | Phase 5 (US3): refusals + secret hygiene | T022–T024 | `39dce1c` |
| 7 | Phase 6 (US4): CLI refactor onto the surface | T025–T031 | `8c5c7a8` |
| 8 | Phase 7 (US5): example, docs, hygiene scan, walkthrough | T032, T033, T033a, T034–T037 | `25dc955` |
| 9 | Phase 8: governance gates, scope audit, evidence | T038–T042 | `141ad14` |

Chunk splits chosen by the orchestrator: 9 chunks from 8 `tasks.md` phase headings, splitting
Phase 3 (US1) at the code/live-verification seam (T012–T017 vs T018–T019) because the live tasks
start a Prefect server and carry cleanup obligations that are better isolated from code commits.

Root and remediation commits outside the chunks:

| Commit | What it is |
|---|---|
| `5646661` | markdown-lint regressions this run's own planning commits introduced, fixed by hand |
| `7bdb8a8` | the original implement report (INCOMPLETE), superseded by this file |
| `730dc0d` + `2bb58f7` | review pass 1 remediation |
| `cc411f5` | delivery-record drift in `plan.md` / `tasks.md`, found by the post-remediation analyze |
| `52953bf` | review pass 2 remediation |
| `f643e2a` | flow-level session summary |

## The stop, and the resumption

**Chunk 1 returned a material decision.** `infrahub-sync generate` is not idempotent over time:
`diffsync_adapter.j2:26` and `diffsync_models.j2:29` iterate the live Infrahub schema unsorted, so
generated output tracks the server's response order. That falsifies the brief's readiness item
R-2, which asserts the command "deterministically rewrites four committed files" so that "later
sanity runs leave the tree clean". T002's mandated action was performed and committed
(`e04f262`, 4 files, +400/−342), but its verification — re-run and expect a clean tree — could not
pass. Under CHECKPOINT mode the run may not answer a post-gate material decision itself, so it
stopped rather than choosing for the owner.

**The owner ratified option C on 2026-07-31** (D014, Blake Ellis): report the churn as
**inherited** — `git diff main..HEAD -- infrahub_sync/generator/` is empty, so the behavior comes
from `main` — and defer the fix to a filed issue
(`bug-generate-output-is-not-deterministic-across-runs.md` in the planning repo's
`proposed-issues/`). Option B, committing the current fixed point, was viable and was offered
again after root corrected a malformed measurement of its own; it was declined because a
clean-tree gate that passes today and fails later without anyone touching the generator hides
itself. T041 and T039 had their gate wording corrected to the true inherited baselines.

**Chunks 2–9 then ran**, in order, each in a clean-context worker. One further material decision
arose mid-implementation and was returned rather than decided: D015, the rendered logger-name
deviation surfaced by chunk 7. It was ratified the same way, and the run resumed from it.

## Final state

- **44/44 tasks** complete (T001–T042 plus the inserted T012a and T033a); zero open.
- **115/115 checklist items** marked across the four checklists (interfaces 38, safety 32,
  traceability 29, requirements 16).
- **Tests: 111 → 327 passed, 4 skipped**; 295 passed / 5 skipped in a prefect-absent install,
  the extra skip being the flow suite's `importorskip`.
- **29 commits** on the branch (`9edc1bc..f643e2a`). Delivery diff measured at the final
  remediation commit `52953bf`: **62 files, +14,419/−454** (63 files / +14,495 once the
  session-summary commit `f643e2a` is included).
- Six files under `infrahub_sync/`: `execution.py`, `cache/fingerprint.py`,
  `orchestration/{__init__,flow,serve}.py`, and the `cli.py` delegation. The parallel
  `sync_in_tiers` branch is byte-unchanged.
- Every live acceptance criterion ran against the real lab. No live-environment ceiling was
  recorded for any of them.

## Local-pass evidence

Per-chunk totals below; `run-report.md` holds the verbatim transcripts, commands, timestamps, and
environment for every chunk, and is the source of truth for the live evidence. **Chunk 1 added no
tests** — its only test evidence is the inherited suite observed as the T003 baseline
(`111 passed, 2 skipped, 3 warnings in 5.30s`, 2026-07-31T04:02Z).

Suite size is stated as tests **collected**, re-measured for this report by archiving each chunk
commit with `git archive` into a scratch directory and running `pytest --collect-only` there — no
branch switch and no working-tree change. The collected figure excludes two modules that
`importorskip` at collection time (the pynetbox and pynautobot adapter suites) and includes the
two opt-in `integration` tests, which is why the recorded pass lines read two lower with two more
skips.

| Chunk | Commit | Tests added | Suite (collected) | What the chunk's local evidence proves |
|---|---|---|---|---|
| 1 | `161f6eb` | 0 | 111 | Inherited baseline recorded, including the corrected `invoke lint` exit 30 |
| 2 | `a6e3349` | +59 | 170 | Shared execution surface and canonical plan fingerprint, unit-level |
| 3 | `8c2e437` | +30 | 200 | `prefect==3.8.1` extra, `orchestration/` package, flow tests, redis-store compatibility |
| 4 | `8046690` | 0 | 200 | Live only — no new tests; see the T018/T019 transcripts |
| 5 | `b8329f7` | +7 | 207 | Confirmed-sync lifecycle and its idempotent second run |
| 6 | `39dce1c` | +5 | 212 | Flow-level refusals and the canary-redaction scan |
| 7 | `8c5c7a8` | +15 | 227 | CLI-to-surface mapping, CLI/remote parity, prefect-free import guard |
| 8 | `25dc955` | +38 | 265 | Example hygiene scan (prefect-independent) |
| 9 | `141ad14` | 0 | 265 | Verification and collation only; T040 recorded `263 passed, 4 skipped` |

Live evidence, one line each, all against the lab and all transcribed in `run-report.md`:

- **Chunk 3** ran T012a's redis-store round-trip **live against a throwaway `redis:8.8.0`
  server** rather than recording a live-environment ceiling for it.
- **Chunk 4** was the first live end-to-end: flow run `6b1f21ba-f73d-4dc1-81c8-9b4d4f7658a9`
  reached COMPLETED with `summary=create:5,update:0,delete:0`, and 11 bridged `infrahub_sync*`
  log records were retrieved through `POST /api/logs/filter`.
- **Chunk 5** performed the one authorised destination write of the whole run: sync run
  `57e6cfeb-46e3-4b9b-a209-bd0b82b1b85c` → `status=applied`, five devices then confirmed in the
  destination by name, followed by a plan that reported `no-change`.
- **Chunk 6** verified the refusals live — seven refused runs, plus HTTP 409 with **no flow run
  created** for an invalid `operation`, and `/tmp/pwned` never created — and mutation-probed its
  own redaction test instead of trusting it.
- **Chunk 7** matched CLI-vs-remote canonical plan fingerprints exactly
  (`669cdb370f8a3c6e0c91b78a18ba03999901ae57e0d38f0b8b4b8778f32c2780`) after resetting the
  destination to zero so the oracle was not degenerate.
- **Chunk 8**'s DBA-011 walkthrough was performed by a fresh agent barred from reading anything
  outside `examples/prefect_remote_run/`.
- **Chunk 9** re-ran the governance gates on the final tree: `linter.format` clean, `rumdl` clean,
  `ruff` clean, `yamllint` clean, `ty` exit 0 with the expected 4-diagnostic set, pylint at the
  inherited baseline with zero new diagnostics, and CLI sanity on all three commands.

## What the chunks found on their own initiative

Each of these came from the chunk worker or from root verifying it, not from the task text:

- **Chunk 6** discovered that `PREFECT_LOCAL_STORAGE_PATH` does **not** follow `PREFECT_HOME`, so
  the test fixtures had been leaking pickled result files into the developer's real `~/.prefect`.
  The fixture now sets both, and a full suite run leaves `~/.prefect/storage` unchanged (84 → 84).
- **Chunk 7** did the CLI refactor under byte-identity and proved the DBA-009 test population
  unmodified with an empty `git diff --stat 9edc1bc --` over the four population files. It
  returned D015 — the rendered logger-name deviation — rather than deciding it, and rejected the
  option of making `execution.py` log as `infrahub_sync.cli` on the ground that it would make the
  code misreport its origin.
- **Chunk 8**'s clean-context walkthrough exposed and fixed **two real README defects**: a state
  progression that omitted `PENDING`, and an artifact-listing command whose glob matched every
  historical run rather than the run just made.
- **Chunk 9** re-measured the pylint baseline **environment-matched**, via `git archive 9edc1bc`
  into a scratch directory, which is what revealed that the baseline count is
  environment-dependent (30 in a base-only env, 56 in dev+prefect at the same commit and the same
  9.60/10 rating). It also caught its own **false-positive content check** on the `generate`
  churn: a whole-line sort cannot see through member reordering inside an `_attributes` tuple, so
  it switched to an AST-based, order-insensitive class comparison, which proved the churn
  content-lossless.

## Review and remediation

Two review-and-remediation passes followed implementation; both are recorded in full in the run
ledger.

**Pass 1** produced 52 deduplicated findings, 15 of them blocking, closed by `730dc0d` and
`2bb58f7`. The highest-value one was structural: CI installed only `--extra dev`, so
`importorskip("prefect")` had been skipping the entire flow suite on all four Python versions, and
with prefect absent `ty` could not see the new `orchestration/` code at all. Suite 263 → 309.

**Pass 2** reviewed only `141ad14..2bb58f7` — the ~1,030 lines pass 1's own remediation added,
which no reviewer had seen. **It found a credential leak that pass 1's remediation had
introduced:** `_add_url_userinfo` ran only from the settings arm, so a password embedded in an
environment endpoint variable was never inspected and leaked verbatim into both the error message
and the rendered cause chain — and environment is the primary credential channel for the remote
model. Fixed, with the rest of pass 2's findings, in **`52953bf`**. Suite 309 → 327.

Root independently verified the two most consequential pass-2 fixes rather than trusting the
report: the env-embedded canary is now collected and does not survive redaction, and
`artifact_path` is absolute (a relative one is refused by `RunResult`).

## Disclosed deviations and deferred items

All ratified, none smoothed over:

- **D014** — `infrahub-sync generate` is not idempotent over time (inherited). Reported, not
  fixed; T041's CLI leg (c) leaves ~242 churned lines, verified content-lossless and restored with
  `git checkout --`.
- **D015** — the sole DBR-009 byte-identity deviation is a rendered logger name
  (`infrahub_sync.cli` → `infrahub_sync.execution`).
- **Corrected inherited baseline** — `uv run invoke lint` exits **30**, not the exit 0 the brief's
  R-4 claims, and the diagnostic count is environment-dependent.
- **R-5** — `pytest-timeout` is absent, so `@pytest.mark.timeout(5)` at
  `tests/test_potenda_parallel.py:70` is a silent no-op. Pre-existing, deliberately not fixed.
- **Review finding M1** deliberately deferred (reachable only via an internal-bug path, and it
  degrades conservatively).
- **Residual risk stated rather than looped on:** the 541 lines `52953bf` added are themselves
  unreviewed by an independent pass, because the protocol allows only two.

Three issues were filed upstream instead of being fixed here: generator non-determinism,
`invoke format` corrupting Markdown while `invoke lint` masks pylint, and stale dependency caps.

## Autonomous decisions

1. Chunk boundaries and the Phase 3 split described above.
2. Fixed the three markdown-lint violations in this run's own spec artifacts without asking — a
   regression this run introduced, with no product question attached. Fixed by hand rather than
   with `rumdl fmt`, which corrupts `tasks.md`.
3. Did **not** apply the generator sort fix, did not split T002, and did not choose option C at
   chunk 1 — returned the decision instead. Same treatment for D015 at chunk 7.
4. Stopped the loop after chunk 1 rather than continuing, since CHECKPOINT mode has no second
   checkpoint and option A would have changed files across `examples/` that later chunks touch.
5. Kept `flow.py` free of `from __future__ import annotations` after research finding F3 turned out
   to be version-specific to prefect 3.5.0 — harmless, now accurately commented, and removing it
   would be change without benefit.
6. Ran a second review-and-remediation pass over `141ad14..2bb58f7` rather than declaring the
   review complete on the strength of a green suite. That pass found the credential leak.

STATUS: DONE | SPEC_DIR: /Users/blake/repos/opsmill/infrahub-sync-dev-preview/dev/specs/001-prefect-managed-remote-run
