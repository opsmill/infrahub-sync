# Extraction Record

**Extracted on**: 2026-11-14
**Extracted by**: speckit.opsmill.extract

## What this directory is

This directory is not a specification. It is the review evidence for work item DB-006, "MVP
interface conformance", added by pull request #165 and last changed by pull request #173 on
2026-08-14. It has no `spec.md`, `research.md` or `tasks.md`.

It describes the code as it was in August 2026. Do not read it as a description of the current
code:

- The standalone product projection (`infrahub_sync/product_store/standalone.py`) and the
  `product_cache_location` setting were deleted in commit `6fa2c653`.
- The embedded Python API (`infrahub_sync/api/v1/`) was deleted in commit `9cfe36c3`.
- The `managed` package and extra are now named `service`.
- The interface matrix and its adapters under `tests/conformance/` were deleted in commit
  `9cfe36c3`. Only `oracle.py` and its test remain.
- Several commands in `reviewer-setup.md` no longer work, for example
  `uv sync --extra managed` and `pytest tests/api/test_v1.py`.
- The link to the NetBox tutorial in `known-limitations-and-release-gaps.md` points to a heading
  that no longer exists. The path of the link was updated for the archive directory; the anchor
  was left as it was.

## ADRs Created

None.

## Knowledge Updated

None.

## Guidelines Updated

None.

## Not extracted

- "Saved-plan apply records delete operations but does not execute them": already ADR 0004 and
  `docs/docs/development/knowledge/planned-write-and-apply.md`.
- The release gaps "configuration-package registration" and "released Prefect Extras dependency":
  both closed since. Configuration packages are registered with `infrahub-sync configs`, and
  Prefect Extras is vendored at `opsmill_prefect_extras/`.
- The normalization paths in `canonical-field-map.md`: still the paths in
  `tests/conformance/oracle.py`, which is their source.
- Everything else: evidence for one revision, with no durable content.

## Open at archive time

The directory has no `tasks.md`, so it has no open task. These statements of
`known-limitations-and-release-gaps.md` were not checked against the current code when the
directory was archived:

- The release gap "qualification of the unbounded shipped NetBox example", not tracked. Whether
  the shipped NetBox example is qualified today is not known. The current check of that example
  is described in `docs/docs/development/guidelines/testing-tiers.md` ("The `from-netbox`
  example check").
- The interface and LAG defect that made a second `diff` fail after a sync, not tracked. Whether
  the defect still exists is not known.
- The statement that the managed verify result has no action-count summary, not tracked. Whether
  the `verify` result of the Sync service holds action counts today is not known.
- The statement that the default DB-003 production-profile contract uses a PostgreSQL emulator
  and a deterministic S3-compatible client, not tracked. Whether the store tests still do so is
  not known.

## Archive

The directory moved to `dev/specs/archive/004-mvp-interface-conformance/` as a historical
record.
