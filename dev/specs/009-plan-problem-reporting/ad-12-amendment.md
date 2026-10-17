# AD-12 amendment note (spec 009 FR-029)

**Status**: Accepted (2026-10-17).

**Target file**: `infrahub-sync-process/product/decisions.md`, outside this repository. The
product owner applies the row below to that file. This repository holds only this note.

**Engineering record**: [ADR 0020](../../adr/0020-records-with-problems-are-left-out-of-a-plan.md).

## Draft AD-12 row

> AD-12: Failed dependencies are visible and block dependent work. At the plan stage, records
> with problems and the records that depend on them through a required relationship are left
> out and counted; apply needs the person to confirm the left-out count, and a `sync` run whose
> plan leaves out records stops after the plan. The policy for runs that nobody watches is open
> for 3.0.

## Sign-off

Sign-off: Benoit Kohler, 2026-10-17

The row above was written to `product/decisions.md` in `infrahub-sync-process` on the branch
`product/ad-12-plan-stage-policy` (commit `f0486ff`). That branch waits for a pull request in
`infrahub-sync-process`.
