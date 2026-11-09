# Research

## Baseline eligibility

Decision: filter successful runs by cursor-sidecar presence, retaining applied and dry-run statuses.

Rationale: diff writes a dry-run run record without persisting cursors; sync dry-run can persist cursors. Status alone cannot distinguish reusable baselines.

Alternatives: excluding every dry-run would discard valid baselines; scanning per kind would combine snapshots from different runs and complicate consistency. Parsing every cursor file during selection duplicates existing validation and is outside this fix.

## Fallback observability

Decision: log missing cursor and unsupported tier at existing per-kind fallback branches using side/resource context.

Rationale: these are the only currently silent full-read branches after the global gate. No new fallback behavior or adapter changes are needed.

## Repository layout

Decision: use dev/specs/001-diff-incremental explicitly. The checkout stores specs and constitution aliases as plain text pointer files, so resolve their real dev/ paths without modifying repository infrastructure. The branch was renamed with coordinator approval; the mandatory feature hook reports it already exists, and its branch-creation purpose is already satisfied.
