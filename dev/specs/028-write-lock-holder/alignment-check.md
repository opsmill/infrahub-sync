# Alignment Check: spec 028 against the original request

## Source

The inline request given to `speckit-opsmill-prep` on 2026-11-23 (no URL). Its text:

> Name the run that holds a configuration's write lock (INFP-647 acceptance criterion 7). When a sync, apply or
> runs resume times out waiting for the configuration's PostgreSQL advisory write lock
> (infrahub_sync/service/apply_guard.py), the failure record and the CLI must name the run that actually holds the
> lock, without exposing secrets. Design agreed with the advisor: label the lock's own PostgreSQL session with
> application_name = 'infrahub-sync-guard:<run_id>', set when the session opens and before the lock request; on
> timeout, read the holder from pg_locks (granted, same advisory key halves as _OWNERSHIP, current database only)
> joined to pg_stat_activity, on the failing session before it is discarded. Do not store the holder in the run
> records (a row would go stale after a crash). Conditions: treat the label as untrusted (exact prefix plus run ID
> pattern, then confirm in the store that the run exists and belongs to this configuration; store only the run
> ID); a label cut short by the 63-byte limit gives 'not known'; a lookup failure never replaces the contention
> error; remove the earliest-claimed-write guess (other_write_in_progress) instead of keeping it as a fallback;
> when the holder's run has already ended, say so instead of telling the user to wait; correct the
> secret-redaction guideline (the session is now read; application_name is visible only to the same role or
> pg_read_all_stats); decide whether ADR 0021 needs amending for the new failure type and active_run_id field.
> Acceptance needs a real PostgreSQL test: A holds, B and C wait, C names A; plus a case where the label cannot be
> read gives 'not known'. Builds on branch fix/write-guard-names-active-run (commits cf9f13b6, a50d5780), which
> added failure_type write_guard_contended, active_run_id, and CLI output 'active run: <run_id>' with a runs show
> hint. Refusing a second write at submission is out of scope (ADR 0010).

## Verdict

⚠️ MINOR DRIFT (proceeding)

## Findings

| Severity | Category | Request reference | Spec reference | Description |
|---|---|---|---|---|
| Minor | added | "a lookup failure never replaces the contention error" | FR-006 | The spec adds a 2-second limit on the lookup. It comes from critique E2 and makes the request's condition hold when the lookup hangs. |
| Minor | added | "read the holder from pg_locks" | FR-006a | The spec names a run only when exactly one session holds the lock. It comes from critique E3 and narrows the "untrusted" rule. |
| Minor | added | "when the holder's run has already ended, say so" | US3, contract | The next action says the administrator must stop or restart the worker. This is a more specific wording of "say so instead of telling the user to wait" (critique P1). |
| None | — | every other condition | FR-001 to FR-012, research R1–R8, tasks T002–T027 | Present with the same meaning: label before the lock request, query filters, no stored holder, untrusted label with store confirmation, 63-byte rule, removal of `other_write_in_progress`, guideline correction, ADR 0021 decision (amend), real PostgreSQL tests for "C names A" and an unreadable label, submission refusal out of scope. |

No requirement of the request is missing, softened or contradicted.

## Action

Proceed. The three additions narrow the request's own conditions and were kept. No remediation pass was needed.
