# 25. A warm source read admits its changed records and reads a kind in full after a problem

**Status**: Proposed
**Date**: 2026-11-12
**Source**: `dev/specs/019-plan-problem-reporting/research.md` (R22)

## Context

A plan leaves out the records that have a problem
([ADR 0020](0020-records-with-problems-are-left-out-of-a-plan.md)), and a full read of the
source finds the problems of each record in the admission step, `load_record`. A warm read
rebuilds each kind from the previous run's source snapshot and reads only the records changed
since the saved cursor. It reported fewer problems than a full read of the same records:

- `list_changed_since` filters, transforms and converts the changed records inside a
  generator, so the first record that fails ends the read instead of being left out.
- The source snapshot holds no source record ID, so a changed record cannot resolve a reference
  to an unchanged record, and a duplicate source identity cannot be told apart from a record
  the cursor overlap returns again.
- A record the previous run left out is in neither the snapshot nor the changed records.

## Decision

- `DiffSyncMixin` gains the hook `list_changed_records(model_name, element, cursor)`, which
  returns the raw changed records before any filter. The engine filters them and admits each
  one through `load_record`, after removing the stored record with the same source record ID.
  A source adapter with the read loop that does not implement it reads every kind in full.
  `list_changed_since` keeps its contract for the destination and for adapters without the
  read loop.
- The source snapshot of an adapter with the read loop carries a `local_id` column (the source
  record ID) and the Parquet metadata key `infrahub_sync.source_read`: `complete` when the read
  of the kind recorded no load report entry and no filtered record and every row has a source
  record ID, `incomplete` otherwise. A kind reads warm only from a `complete` snapshot. A
  snapshot without the key, written by an earlier release, causes one full read of the kind.
- A warm read of a kind that records a load report entry or a filtered record is discarded, and
  the kind is read in full. A kind whose read records an entry makes the later kinds that
  reference it read in full.
- Cursors, the plan format and the plan's own problem pass do not change.

## Consequences

- A warm read gives the same problems, left-out records and kept records as a full read of the
  same source records; `tests/plan/test_warm_read_problems.py` checks this on the problem
  fixture.
- A warm read saves reads only for the kinds whose records have no problem, and for the kinds
  that reference none of them.
- The source snapshot of an adapter with the read loop changes, so its digest in the plan
  manifest and the plan checksum differ from those of an earlier release for the same data.
  The operations do not change.
- A custom source adapter that defined `safe_cursor_before_load` and `list_changed_since` must
  implement `list_changed_records` to keep reading warm.
- An unchanged record whose peer changed identity keeps the reference stored in the snapshot,
  as before this decision. A warm destination read still records no
  `destination_identity_ambiguities`.
