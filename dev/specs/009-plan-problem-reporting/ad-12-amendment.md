# AD-12 amendment note (spec 009 FR-029)

**Status**: Proposed, needs product sign-off.

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

Sign-off: product owner name: ______ ; date: ______
