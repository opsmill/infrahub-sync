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
- The shipped NetBox-to-Infrahub example requires a fresh Infrahub instance, matching
  schema library, current public NetBox demo token/data, and `pynetbox`. Missing any one is
  an external qualification precondition, not authority to add a new adapter or release
  capability.
- Running-service evidence is bounded to available, isolated services. No existing
  container, cache, Prefect deployment, destination, or external service is mutated.
- The oracle proves complete ProductRun/artifact equality for the plan envelope and the
  same saved-plan lifecycle for local plan/verify/apply/sync. Existing managed failure,
  cancellation, missing-detail, restart, and idempotency suites remain the owning evidence
  for those HTTP/Prefect-only behaviors.

## Product limitations retained

- Delete operations remain reviewable but are not executed by this release.
- The public Python and CLI projection supports the existing local SQLite/filesystem
  provider only. Adding a provider selector is out of DB-006 scope; production
  PostgreSQL/S3 construction remains the DB-003 Python provider surface.
- `product_cache_location` is opt-in. Omitting it intentionally preserves legacy standalone
  run-directory/sidecar behavior for backward compatibility.
