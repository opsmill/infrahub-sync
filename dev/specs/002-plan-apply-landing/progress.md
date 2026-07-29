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
| WP-1 | dispatched | agent 2 | — | — | |
| WP-2 | blocked on WP-1 | — | — | — | |
| WP-3 | blocked on WP-1 | — | — | — | |
| WP-4 | blocked on WP-1 | — | — | — | |
| WP-5 | blocked on WP-0 | — | — | — | live shrink test deferred to WP-8 |
| WP-6 | blocked on WP-0 | — | — | — | coordinate with WP-2 on models.py/verify.py |
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

## Escalations

(none)
