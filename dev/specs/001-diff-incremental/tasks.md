# Tasks: Preserve incremental extraction after diff

## Phase 1: Setup and foundations

- [X] T001 Record baseline Pylint diagnostics for infrahub_sync/cache/incremental.py and infrahub_sync/potenda/__init__.py and validate local test tooling.

## Phase 2: User Story 1 — Preserve baseline (P1)

Goal: cursorless previews do not displace a reusable sync baseline.
Independent test: sync → diff → incremental sync loads snapshots and deltas without a full read.

- [X] T002 [US1] Add failing eligibility regressions in tests/cache/test_incremental_helpers.py and sequential-run regression in tests/cache/test_incremental_engine.py.
- [X] T003 [US1] Require cursors.json for successful baseline selection in infrahub_sync/cache/incremental.py; observe all changed tests pass.

## Phase 3: User Story 2 — Explain fallback (P2)

Goal: operators see why a kind is read in full.
Independent test: missing cursor and unsupported tier each emit side, resource, and reason.

- [X] T004 [US2] Add failing fallback log regressions in tests/cache/test_incremental_engine.py.
- [X] T005 [US2] Log missing cursor and unsupported tier in infrahub_sync/potenda/__init__.py; observe changed tests pass.

## Phase 4: Documentation and verification

- [X] T006 Update docs/docs/reference/incremental-extraction.mdx and add changelog/403.fixed.md.
- [X] T007 Run required format, lint, CLI sanity, docs validation, ty, full pytest suite, and touched-file Pylint comparison; record evidence in opsmill-implement-report.md.

## Dependencies and delivery

Execute phases in order. US2 depends only on the existing engine; US1 is the MVP. No parallel file edits are needed; helper tests and engine tests could be read independently. No adapter modifications or live server writes are permitted.
