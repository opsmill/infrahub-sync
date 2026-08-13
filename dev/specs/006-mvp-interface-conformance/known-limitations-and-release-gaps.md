# Known limitations and release gaps

## Hard release gates not implemented by DB-006

1. **Configuration-package registration/version management is absent.** MVP records an
   immutable configuration fingerprint/reference. There is no registration API, version
   lifecycle, or package promotion contract.
2. **Prefect Extras is still an exact Git dependency.** The accepted staging pin is not a
   released dependency and must be replaced through its owning release process before
   PyPI publication or promotion to `main`.
3. **No release promotion occurred.** This branch was not merged, pushed, published, or
   promoted to `main`.

## Qualification limits

- The default DB-003 production-profile contract uses a PostgreSQL-paramstyle emulator and
  deterministic S3-compatible client. Live PostgreSQL/S3 evidence is recorded separately
  when isolated Docker services and temporary client dependencies are available.
- Independent live review generated the shipped NetBox-to-Infrahub example successfully.
  The integration suite passed 7 tests with 1 skip in 76.54 seconds only after an
  uncommitted public-demo device re-pin. The bounded DB-006 product-cache cycle passed
  against real extraction and destination writes. The unbounded shipped example remains
  unqualified because of pre-existing live-data and adapter hazards; neither the stale pin
  nor those hazards is part of this correction.
- Running-service evidence is bounded to available, isolated services. No existing
  container, cache, Prefect deployment, destination, or external service is mutated by
  this correction. The independent live environment was subsequently torn down under
  authorization: all eight assigned containers, five project volumes, and its compose
  network were removed, and post-teardown checks found no compose-project resources and
  ports 8000 and 6362 free. The receipt and pre-existing adapter/SDK issue disposition are
  recorded in `infrahub-sync-lab@2cd4010234608fee5d737f8cf051cb576516fd3d` at
  `.planning/investigations/2026-08-10-lag-peer-identity-rediff-crash.md`.
- The oracle proves complete ProductRun/artifact equality for the plan envelope and the
  executed CLI/Python/actor-free-managed-worker plan, apply, and sync envelopes, including
  actual returned counts/outcomes/operations and in-memory routing/boundary effects. The
  probe is fed by a stubbed core and does not prove real adapter writes; the bounded live
  cycle is the real-write evidence. Python and
  managed independent verify compare only their returned common fields plus durable
  verification evidence because the managed verify result has no action-count summary.
  Existing managed failure, cancellation, missing-detail, restart, and idempotency suites
  remain the owning evidence for those HTTP/Prefect-only behaviors.
- The CLI exposes saved-plan review, not an independent `verify` product operation. The
  matrix exercises CLI plan, Python verification, and CLI review against the exact same
  manifest bytes; adding a CLI verify operation would be new public scope.
- Managed HTTP necessarily records actor, audit, and Prefect correlation fields that
  standalone calls do not have. The lossless oracle retains these differences. The HTTP
  lifecycle is executed and scanned separately rather than deleting named product fields
  to manufacture equality.

## Product limitations retained

- Delete operations remain reviewable but are not executed.
- The public Python and CLI projection supports the existing local SQLite/filesystem
  provider only. Adding a provider selector is out of DB-006 scope; production
  PostgreSQL/S3 construction remains the DB-003 Python provider surface.
- `product_cache_location` is opt-in. Omitting it intentionally preserves legacy standalone
  run-directory/sidecar behavior for backward compatibility.
