# 14. V3 writes through saved-plan apply

**Status**: Accepted
**Date**: 2026-09-28

## Context

The v3 Sync API builds a saved plan, verifies it, and applies its recorded operations. The direct
`Potenda.sync` method instead called DiffSync's `sync_from`, which dispatched to model `create` and
`update` methods. No v3 product entry point called that method. Keeping it and its Infrahub write
helpers made an unreachable path appear to be a supported way to change destination data.

## Decision

V3 destination writes execute only through saved-plan apply. The CLI `sync` command requests the
service's plan, verify, and apply lifecycle; it does not call DiffSync's `sync_from`. The direct
Prefect flow refuses a `sync` request. `Potenda.sync` and the Infrahub adapter's direct-write
helpers are removed. The load and diff paths still build the plan, and `apply_plan` still dispatches
recorded operations through `apply_planned_operation`.

## Consequences

An Infrahub destination write has a reviewed plan and the apply path's validation and record. Tests
of model `create` and `update` no longer describe v3 behavior. DiffSync still supplies those model
methods through inheritance, but v3 does not dispatch to them. The remaining adapters' legacy
write hooks are addressed separately.
