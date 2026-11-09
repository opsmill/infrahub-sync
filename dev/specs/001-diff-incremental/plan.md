# Implementation Plan: Preserve incremental extraction after diff

**Branch**: `fix/403-diff-breaks-incremental` | **Date**: 2026-11-09 | **Spec**: [spec.md](spec.md)

## Summary

Require a cursor sidecar when selecting the newest successful baseline. Keep both applied and dry-run statuses eligible when cursors exist. Add reason logging for the existing missing-cursor and unsupported-tier full reads in the shared engine.

## Technical Context

**Language/Version**: Python 3.10–3.13 (local Python 3.13).

**Primary Dependencies**: Existing DiffSync, PyArrow, pytest, logging; no new dependencies.

**Storage**: Existing run.json, cursors.json and Parquet snapshots; no format changes.

**Testing**: pytest helper and engine regressions, synthetic adapters, temporary filesystem.

**Target Platform**: Existing CLI environments.

**Project Type**: Python library and CLI.

**Performance Goals**: No full reads attributable to intervening cursorless previews.

**Constraints**: No adapter edits, no live sync, existing global safeguards retained.

**Scale/Scope**: Two shared modules and targeted tests; incremental reference documentation.

## Constitution Check

Pass before and after design: no live writes, no adapter changes, no dependencies, explicit types, local regressions, no secrets, scoped changes. Use existing module logging conventions rather than migrate logging systems. Known Pylint baseline is accepted only with no new messages on touched files.

## Project Structure

- `infrahub_sync/cache/incremental.py`: require cursor sidecar for baseline eligibility.
- `infrahub_sync/potenda/__init__.py`: log existing per-resource fallbacks with side and resource.
- `tests/cache/test_incremental_helpers.py`: applied/dry-run/failed/cursorless eligibility matrix.
- `tests/cache/test_incremental_engine.py`: synthetic sequential-run regression and fallback logs.
- `docs/docs/reference/incremental-extraction.mdx`: eligibility and reasons.
- `changelog/403.fixed.md`: release fragment.

## Design

Run selection must filter for cursors.json before selecting the maximum chronological directory. Do not remove dry-run from successful statuses: sync dry runs can persist cursors. Do not merge snapshots or cursors across run directories. Per-kind fallback logs distinguish missing cursor from unsupported tier and use the existing `Incremental disabled:` prefix. Existing global gate logging remains sufficient for side-wide full extraction.

## Validation

Observe failing regressions before the fix, then passing targeted tests and the full suite. Run uv sync, invoke format, invoke lint, explicit ruff and ty, CLI help/list/generate. Compare Pylint diagnostics on touched source files against main, normalizing only line numbers. Run documentation generation, build and Markdown checks; record environmental limitations explicitly.
