# Progress ledger — Spec 002 (INFP-653 landing)

Orchestrator ledger. Removed with the spec before the PR (per the gates checklist).

## Pre-flight facts (2026-07-29)

- PR #143: **OPEN, not merged** → WI-000 slice 4 is a pure byte-exact restore, no re-apply.
- `main` == `origin/main` == `9edc1bc` == merge-base with the run branch → no interim `main`
  drift; all six slices restore byte-exact with no exceptions.
- No `speckit-run/*` tag exists yet — WP-0 creates and pushes it.

## WP status

| WP | Status | Agent | Commits | Gates | Notes |
|----|--------|-------|---------|-------|-------|
| WP-0 | **done** (2026-07-29) | agent 1 | `92b0e51` `9646da7` `758c62a` `5034044` `e271cc7` `2853309` | format clean (0 files changed); pytest 715/11/1 exact baseline match; ty exit 0; yamllint 0; pylint exit 28 (see deviation 2) | Byte-exact verified per slice; slice 6 = exactly the 15 design artifacts; CLAUDE.md kept at main's; residual diff vs run branch = exhaust + CLAUDE.md + spec-002 only. Tag created locally (see deviation 1). |
| WP-1 | **done** (2026-07-29) | agent 2 | `aeac2e4` (FIX-008) `54c848b` (FIX-009) `54407c3` (LOC-06) `3b1ece2` (LOC-03) `1ee3b31` (LOC-04) `376dd9e` (LOC-05) `ee6fb28`/`19f42e7` (FIX-009 lint follow-ups) | format clean; ruff/yamllint/ty clean; pylint 9.77 (only reductions); pytest 718/11/1; fail-before evidence captured for FIX-008/009 tests | `utils.py` touched (FIX-009's own finding names it). Design note: `verify_plan` takes the raw-bytes bundle, not `LoadedPlan` — torn artifacts must still be verifiable (FR-009); analysis allows "equivalent immutable bundle". |
| WP-2 | **done** (2026-07-29) | agent 4 | `76bde6b` (LOC-10) `77d88e2` (FIX-003+RIG-07) `b04f9e3` (FIX-005+SIM-07) | format clean; ruff/yamllint/ty clean; pylint no new messages; pytest 761/12/1 (+39); fail-before evidence captured | Manifest field `destination_binding` `{url, branch}` (absent when unbound, checksum-covered); flag `--allow-destination-change`; check name `destination_binding` in the new `VerificationCheck` TypeAlias. Declared deviations: one-kwarg touch in `potenda/__init__.py` (only seam into the writer; accepted), `utils.py` seam work, `b04f9e3` amended pre-push to fold regenerated cli.mdx (disclosed, no remote branch). |
| WP-3 | dispatched | agent 6 | — | — | on the branch |
| WP-4 | blocked on WP-3 (shared cli.py) | — | — | — | |
| WP-5 | **done** (2026-07-29, landed) | agent 3 | on-branch: `418de6d` (MIN-012) `0664c7d` (FIX-002) `729fbf0` (FIX-001) `5f4012b` (MIN-010 pin) | worktree gates clean (pylint 9.76, no new msgs; pytest 719/12/1); cherry-picked clean; combined-tree gates below | MIN-010 **resolved** as FIX-001 by-product (flush now stamps peer lineage; PR #143 parity). MIN-009 **moot** — flush path now issues zero destination reads; WP-10 records "resolved by FIX-001", no perf issue. Live shrink test written (`tests/integration/test_infrahub_replace_set_shrink_integration.py`), runs in WP-8. `tests/plan/test_apply_conformance.py` touched with stated reason. |
| WP-6 | **done** (2026-07-29, landed) | agent 5 | on-branch: `f1c1dba` (MIN-015) `81fbe6c` (FIX-013) `352d2d3` (MIN-024) `00cf432` (MIN-001) `92fbb54` (MIN-002) `f014272` (MIN-003) `67ddaac` (MIN-025) | worktree gates clean (760/12/1, +38); import-block conflicts vs WP-2 in models.py/verify.py resolved at landing (orchestrator); merged-tree: format clean, ruff/yamllint/ty clean, pylint 9.79, pytest **799/12/1** | FIX-013's apply-level evidence showed the mismatched record was previously dispatched to the destination; MIN-003's showed an out-of-run-dir snapshot previously verified clean. |
| WP-7 | blocked on WP-0 | — | — | — | |
| WP-8 | blocked on WP-1…7 | — | — | — | gates the PR (DISC-003) |
| WP-9 | blocked on WP-1…7 | — | — | — | |
| WP-10 | blocked on WP-8, WP-9 | — | — | — | drafts only; human posts |

## Deviations

1. **Tag push blocked.** `speckit-run/001-plan-artifact-saved-apply` was created locally
   (points at `2a98449`, the run branch tip) but `remote.origin.pushurl` is set to `DISABLED`
   in this checkout — a deliberate push guard the agents will not override. Pending human
   action: `git push origin speckit-run/001-plan-artifact-saved-apply`.
2. **Lint baseline drift.** `uv run invoke lint` exits 28 (pylint 4.0.5 C/R/W warnings;
   invoke aborts before yamllint/ty). Reproduced byte-identically against the run branch tree
   with the same venv, so the spec's "lint exit 0" baseline came from a different resolved
   toolchain — not an intake regression. Individually: ruff clean, `yamllint .` exit 0,
   `uv run ty check .` exit 0. Working gate definition for WP-1…9: ruff/yamllint/ty clean and
   **no new pylint warnings vs this baseline**; MIN-014 (WP-7) addresses the touched-code
   pylint warnings and the optional `--fail-on=E` severity gate.
   **Correction (WP-2):** the earlier "lint exit 0 after WP-1" note was an orchestrator
   measurement error (a pipe captured `tail`'s exit code, not invoke's). `invoke lint`
   still exits 28 from pre-existing potenda C0415 messages; the working gate stands:
   ruff/yamllint/ty clean + zero new pylint messages, until WP-7 (MIN-014) restores exit 0.

## Escalations

(none)
