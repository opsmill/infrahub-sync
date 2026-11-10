# Implementation Report: Preserve incremental extraction after diff

**Status**: DONE

**Spec directory**: `dev/specs/001-diff-incremental`

**Implementation base**: `77c431980626b36305904e205276c132d50e52fb`

**Implementation head**: `6a28673e` (report checkpoint follows)

**Feature base**: `88d011023a438524634b232cae3d60af5aaf8884` (`origin/main` at start)

## Chunk ledger

| Chunk | Tasks | Done / partial / blocked | Commit | Decisions |
| --- | --- | --- | --- | --- |
| Setup and foundations | T001 (1) | 1 / 0 / 0 | 6a28673e | Baseline captured before production edits |
| US1: preserve baseline | T002–T003 (2) | 2 / 0 / 0 | 6a28673e | Retain cursor-bearing dry runs; skip cursorless applied runs too |
| US2: explain fallback | T004–T005 (2) | 2 / 0 / 0 | 6a28673e | Unsupported tier takes precedence when both conditions apply |
| Documentation and verification | T006–T007 (2) | 2 / 0 / 0 | 6a28673e plus report checkpoint | Existing lint and backend limitations recorded below |

Seven tasks completed. All phases were executed in this worker, following the dispatch instruction to do the work personally without helper workers; the implementation changes share one scoped checkpoint.

## Tasks not completed

None.

## Local-pass evidence

All 27 targeted cases are listed, including unchanged cases exercised alongside every added or modified test. No network/integration/E2E tests were added. The full suite reported `225 passed, 3 skipped, 15 warnings in 6.70s` with `uv run pytest -q`.

| Test id | Type | Run command | Passed at (ISO 8601) | Environment context | Verbatim pass line |
| --- | --- | --- | --- | --- | --- |
| `tests/cache/test_incremental_helpers.py::test_returns_most_recent_applied_run` | unit | `uv run pytest -v tests/cache/test_incremental_helpers.py tests/cache/test_incremental_engine.py` | 2026-11-10T08:01:46Z | n/a; synthetic adapters and tmp_path, Python 3.13.18 | `tests/cache/test_incremental_helpers.py::test_returns_most_recent_applied_run PASSED` |
| `tests/cache/test_incremental_helpers.py::test_returns_none_when_no_successful_runs` | unit | `uv run pytest -v tests/cache/test_incremental_helpers.py tests/cache/test_incremental_engine.py` | 2026-11-10T08:01:46Z | n/a; synthetic adapters and tmp_path, Python 3.13.18 | `tests/cache/test_incremental_helpers.py::test_returns_none_when_no_successful_runs PASSED` |
| `tests/cache/test_incremental_helpers.py::test_returns_none_on_empty_cache` | unit | `uv run pytest -v tests/cache/test_incremental_helpers.py tests/cache/test_incremental_engine.py` | 2026-11-10T08:01:46Z | n/a; synthetic adapters and tmp_path, Python 3.13.18 | `tests/cache/test_incremental_helpers.py::test_returns_none_on_empty_cache PASSED` |
| `tests/cache/test_incremental_helpers.py::test_skip_when_force_full` | unit | `uv run pytest -v tests/cache/test_incremental_helpers.py tests/cache/test_incremental_engine.py` | 2026-11-10T08:01:46Z | n/a; synthetic adapters and tmp_path, Python 3.13.18 | `tests/cache/test_incremental_helpers.py::test_skip_when_force_full PASSED` |
| `tests/cache/test_incremental_helpers.py::test_skip_when_no_prev_run` | unit | `uv run pytest -v tests/cache/test_incremental_helpers.py tests/cache/test_incremental_engine.py` | 2026-11-10T08:01:46Z | n/a; synthetic adapters and tmp_path, Python 3.13.18 | `tests/cache/test_incremental_helpers.py::test_skip_when_no_prev_run PASSED` |
| `tests/cache/test_incremental_helpers.py::test_skip_when_subhash_mismatch` | unit | `uv run pytest -v tests/cache/test_incremental_helpers.py tests/cache/test_incremental_engine.py` | 2026-11-10T08:01:46Z | n/a; synthetic adapters and tmp_path, Python 3.13.18 | `tests/cache/test_incremental_helpers.py::test_skip_when_subhash_mismatch PASSED` |
| `tests/cache/test_incremental_helpers.py::test_use_incremental_when_subhash_matches` | unit | `uv run pytest -v tests/cache/test_incremental_helpers.py tests/cache/test_incremental_engine.py` | 2026-11-10T08:01:46Z | n/a; synthetic adapters and tmp_path, Python 3.13.18 | `tests/cache/test_incremental_helpers.py::test_use_incremental_when_subhash_matches PASSED` |
| `tests/cache/test_incremental_helpers.py::test_load_cursors_empty` | unit | `uv run pytest -v tests/cache/test_incremental_helpers.py tests/cache/test_incremental_engine.py` | 2026-11-10T08:01:46Z | n/a; synthetic adapters and tmp_path, Python 3.13.18 | `tests/cache/test_incremental_helpers.py::test_load_cursors_empty PASSED` |
| `tests/cache/test_incremental_helpers.py::test_persist_then_load_roundtrip` | unit | `uv run pytest -v tests/cache/test_incremental_helpers.py tests/cache/test_incremental_engine.py` | 2026-11-10T08:01:46Z | n/a; synthetic adapters and tmp_path, Python 3.13.18 | `tests/cache/test_incremental_helpers.py::test_persist_then_load_roundtrip PASSED` |
| `tests/cache/test_incremental_helpers.py::test_hydrate_replays_prior_rows` | unit | `uv run pytest -v tests/cache/test_incremental_helpers.py tests/cache/test_incremental_engine.py` | 2026-11-10T08:01:46Z | n/a; synthetic adapters and tmp_path, Python 3.13.18 | `tests/cache/test_incremental_helpers.py::test_hydrate_replays_prior_rows PASSED` |
| `tests/cache/test_incremental_helpers.py::test_hydrate_missing_resource_returns_zero` | unit | `uv run pytest -v tests/cache/test_incremental_helpers.py tests/cache/test_incremental_engine.py` | 2026-11-10T08:01:46Z | n/a; synthetic adapters and tmp_path, Python 3.13.18 | `tests/cache/test_incremental_helpers.py::test_hydrate_missing_resource_returns_zero PASSED` |
| `tests/cache/test_incremental_helpers.py::test_force_full_when_cadence_exceeded` | unit | `uv run pytest -v tests/cache/test_incremental_helpers.py tests/cache/test_incremental_engine.py` | 2026-11-10T08:01:46Z | n/a; synthetic adapters and tmp_path, Python 3.13.18 | `tests/cache/test_incremental_helpers.py::test_force_full_when_cadence_exceeded PASSED` |
| `tests/cache/test_incremental_helpers.py::test_skips_newer_cursorless_runs[applied]` | unit | `uv run pytest -v tests/cache/test_incremental_helpers.py tests/cache/test_incremental_engine.py` | 2026-11-10T08:01:46Z | n/a; synthetic adapters and tmp_path, Python 3.13.18 | `tests/cache/test_incremental_helpers.py::test_skips_newer_cursorless_runs[applied] PASSED` |
| `tests/cache/test_incremental_helpers.py::test_skips_newer_cursorless_runs[dry-run]` | unit | `uv run pytest -v tests/cache/test_incremental_helpers.py tests/cache/test_incremental_engine.py` | 2026-11-10T08:01:46Z | n/a; synthetic adapters and tmp_path, Python 3.13.18 | `tests/cache/test_incremental_helpers.py::test_skips_newer_cursorless_runs[dry-run] PASSED` |
| `tests/cache/test_incremental_helpers.py::test_returns_none_when_only_cursorless_success_exists[applied]` | unit | `uv run pytest -v tests/cache/test_incremental_helpers.py tests/cache/test_incremental_engine.py` | 2026-11-10T08:01:46Z | n/a; synthetic adapters and tmp_path, Python 3.13.18 | `tests/cache/test_incremental_helpers.py::test_returns_none_when_only_cursorless_success_exists[applied] PASSED` |
| `tests/cache/test_incremental_helpers.py::test_returns_none_when_only_cursorless_success_exists[dry-run]` | unit | `uv run pytest -v tests/cache/test_incremental_helpers.py tests/cache/test_incremental_engine.py` | 2026-11-10T08:01:46Z | n/a; synthetic adapters and tmp_path, Python 3.13.18 | `tests/cache/test_incremental_helpers.py::test_returns_none_when_only_cursorless_success_exists[dry-run] PASSED` |
| `tests/cache/test_incremental_helpers.py::test_cursor_bearing_dry_run_is_eligible` | unit | `uv run pytest -v tests/cache/test_incremental_helpers.py tests/cache/test_incremental_engine.py` | 2026-11-10T08:01:46Z | n/a; synthetic adapters and tmp_path, Python 3.13.18 | `tests/cache/test_incremental_helpers.py::test_cursor_bearing_dry_run_is_eligible PASSED` |
| `tests/cache/test_incremental_engine.py::test_falls_back_to_full_load_when_no_prior_run` | unit | `uv run pytest -v tests/cache/test_incremental_helpers.py tests/cache/test_incremental_engine.py` | 2026-11-10T08:01:46Z | n/a; synthetic adapters and tmp_path, Python 3.13.18 | `tests/cache/test_incremental_engine.py::test_falls_back_to_full_load_when_no_prior_run PASSED` |
| `tests/cache/test_incremental_engine.py::test_uses_incremental_when_prior_run_matches` | unit | `uv run pytest -v tests/cache/test_incremental_helpers.py tests/cache/test_incremental_engine.py` | 2026-11-10T08:01:46Z | n/a; synthetic adapters and tmp_path, Python 3.13.18 | `tests/cache/test_incremental_engine.py::test_uses_incremental_when_prior_run_matches PASSED` |
| `tests/cache/test_incremental_engine.py::test_cursor_persisted_after_load` | unit | `uv run pytest -v tests/cache/test_incremental_helpers.py tests/cache/test_incremental_engine.py` | 2026-11-10T08:01:46Z | n/a; synthetic adapters and tmp_path, Python 3.13.18 | `tests/cache/test_incremental_engine.py::test_cursor_persisted_after_load PASSED` |
| `tests/cache/test_incremental_engine.py::test_sync_diff_sync_preserves_incremental_baseline` | unit | `uv run pytest -v tests/cache/test_incremental_helpers.py tests/cache/test_incremental_engine.py` | 2026-11-10T08:01:46Z | n/a; synthetic adapters and tmp_path, Python 3.13.18 | `PASSED` |
| `tests/cache/test_incremental_engine.py::test_kind_fallback_logs_reason[case0-A]` | unit | `uv run pytest -v tests/cache/test_incremental_helpers.py tests/cache/test_incremental_engine.py` | 2026-11-10T08:01:46Z | n/a; synthetic adapters and tmp_path, Python 3.13.18 | `tests/cache/test_incremental_engine.py::test_kind_fallback_logs_reason[case0-A] PASSED` |
| `tests/cache/test_incremental_engine.py::test_kind_fallback_logs_reason[case0-B]` | unit | `uv run pytest -v tests/cache/test_incremental_helpers.py tests/cache/test_incremental_engine.py` | 2026-11-10T08:01:46Z | n/a; synthetic adapters and tmp_path, Python 3.13.18 | `tests/cache/test_incremental_engine.py::test_kind_fallback_logs_reason[case0-B] PASSED` |
| `tests/cache/test_incremental_engine.py::test_kind_fallback_logs_reason[case1-A]` | unit | `uv run pytest -v tests/cache/test_incremental_helpers.py tests/cache/test_incremental_engine.py` | 2026-11-10T08:01:46Z | n/a; synthetic adapters and tmp_path, Python 3.13.18 | `tests/cache/test_incremental_engine.py::test_kind_fallback_logs_reason[case1-A] PASSED` |
| `tests/cache/test_incremental_engine.py::test_kind_fallback_logs_reason[case1-B]` | unit | `uv run pytest -v tests/cache/test_incremental_helpers.py tests/cache/test_incremental_engine.py` | 2026-11-10T08:01:46Z | n/a; synthetic adapters and tmp_path, Python 3.13.18 | `tests/cache/test_incremental_engine.py::test_kind_fallback_logs_reason[case1-B] PASSED` |
| `tests/cache/test_incremental_engine.py::test_kind_fallback_logs_reason[case2-A]` | unit | `uv run pytest -v tests/cache/test_incremental_helpers.py tests/cache/test_incremental_engine.py` | 2026-11-10T08:01:46Z | n/a; synthetic adapters and tmp_path, Python 3.13.18 | `tests/cache/test_incremental_engine.py::test_kind_fallback_logs_reason[case2-A] PASSED` |
| `tests/cache/test_incremental_engine.py::test_kind_fallback_logs_reason[case2-B]` | unit | `uv run pytest -v tests/cache/test_incremental_helpers.py tests/cache/test_incremental_engine.py` | 2026-11-10T08:01:46Z | n/a; synthetic adapters and tmp_path, Python 3.13.18 | `tests/cache/test_incremental_engine.py::test_kind_fallback_logs_reason[case2-B] PASSED` |

### Regression evidence

Before the selection fix: `5 failed, 16 passed in 3.91s`, including `test_sync_diff_sync_preserves_incremental_baseline`. Before the logging fix: `6 failed, 4 deselected in 0.33s`, all six missing-cursor/unsupported-tier cases. After both fixes: `27 passed in 0.30s`.

The sequential regression performs a synthetic engine sync, persists both cursor sides, performs a later diff without writing cursors, then loads and syncs with force_full_extract=False (the --no-full-extract engine setting). Both sides query deltas, neither full loader runs, the original snapshot row is restored, the destination delta is included, and the diff-only snapshot row is absent.

### Verification results

| Check | Result |
| --- | --- |
| `uv sync` and `uv sync --extra dev` | PASS |
| `uv run invoke format` | Exit 0; Python formatting and Ruff pass; existing MD041 pointer warning remains |
| `uv run invoke linter.lint-ruff` | Exit 0; 73 files formatted; checks pass |
| `uv run ty check .` | Exit 0; three existing unused-ignore warnings in untouched Nautobot tests; no errors |
| `uv run invoke lint` | Exit 1 before Python lint because `.github/copilot-instructions.md` is a plain `../AGENTS.md` pointer and violates MD041 on main too |
| `uv run invoke linter.lint-pylint` | Exit 30, matching the documented untouched core baseline |
| Touched production Pylint comparison | 21 messages before / 21 after; identical `(path, symbol, message)` multiset, ignoring shifted line numbers; zero added or removed |
| `uv run invoke linter.lint-yaml` | Exit 0 |
| Changed Markdown/MDX checks | Exit 0 for all ten spec artifacts plus reference documentation and CLAUDE.md; towncrier fragment excluded by repository configuration |
| `uv run infrahub-sync --help` | Exit 0 |
| `uv run infrahub-sync list --directory examples/` | Exit 0 |
| Targeted example generate | Attempted; aborts because configured destination schema is unavailable locally; no generated example changes retained |
| `uv run invoke docs.generate` | Exit 0; only unrelated optional-command usage drift generated, restored before committing |
| Docusaurus build | PASS: `[SUCCESS] Generated static files in "build".` via `npm exec --yes --package=pnpm@11.6.0 -- uv run invoke docs.docusaurus` after frozen-lockfile install |
| `git diff --check` | PASS |

Pylint baseline was captured before code edits at the original feature base. The two touched production files are identical in local main and origin/main before this change. Full lint baselines were left unchanged rather than expanding scope.

## Review findings

Reviewed the implementation diff against the implementation base using the configured code, comments, tests, errors, types and simplify lenses in this worker. No high-severity findings remain.

| Severity | File | Finding | Disposition |
| --- | --- | --- | --- |
| Low | tests/cache/test_incremental_engine.py | Typing the helper exposes previous generic-model attribute assumptions and optional run directory | Fixed with public model accessors and an explicit non-None assertion |
| Informational | infrahub_sync/cache/incremental.py | Applied and dry-run statuses still need cursor presence; presence does not validate sidecar contents | Existing parsing contract retained; malformed cursor recovery remains outside this fix |
| Informational | Repository validation | Existing Pylint and Markdown pointer failures | Documented; no unrelated repairs |

Code review: eligibility is filtered before selecting the newest run; there is no mixing of snapshots across runs. Comment review: helper and reference describe the actual eligibility rule. Test review: failed-first regressions cover successful dry runs, cursorless applied/diff runs, failed runs, both sides and both fallback reasons. Error review: no new catches or swallowed failures. Type review: no new production types; ty exits clean. Simplification review: existing branches are retained and no new abstraction is needed.

## Autonomous decisions

- Used dev/specs explicitly because this checkout materializes specs and constitution aliases as plain pointer files.
- Used the coordinator-approved hand-named feature branch. The branch hook reported it already existed because the approved rename had already completed; no second branch was created.
- Kept existing logging conventions in the touched engine instead of broad logging refactoring.
- Ran all work and review lenses personally per the dispatch constraint; no subagents or helper workers were launched.
- Retained existing schema/cadence safeguards and partial-cursor fallback behavior. No adapter, cache format, dependency or CLI flag changes.
- Installed the pinned docs package manager through npm exec rather than changing global tooling or repository lockfiles.
- Prep alignment: ALIGNED, zero remediation passes. Prep status: READY.

## Suggested next steps

Obtain coordinator approval for the complete draft PR text and push, then publish a draft against main with Fixes #403 and changes/patch. Reviewers should account for the documented pre-existing validation failures and unavailable example destination schema.

STATUS: DONE | SPEC_DIR: /home/pierrick/orca/workspaces/infrahub-sync/fix-diff-breaks-incremental/dev/specs/001-diff-incremental | REASON: n/a
