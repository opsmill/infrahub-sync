# Quickstart: check the external store, the adapter credentials and the private CA

These checks prove the specification on a host with Docker Compose 2.24 or later and a Sync image
built from this branch (`uv run invoke dev.build`, or `docker build -t infrahub-sync:test .`).
Settings are in [contracts/environment-variables.md](contracts/environment-variables.md); failure
names and log lines are in [contracts/bootstrap-and-startup.md](contracts/bootstrap-and-startup.md).

## Offline checks

```bash
uv run pytest tests/compose/test_compose_contract.py tests/compose/test_image_pins.py -q
uv run pytest tests/service/test_trust_store.py tests/service/test_compose_bootstrap.py tests/service/test_storage.py -q
```

Expected: every test passes, including the credential list test, the receiver list test and the
digest test over every compose file.

## 1. External store with path-style and virtual-hosted-style requests (SC-001, SC-002)

1. Start an S3-compatible server outside the deployment with TLS from a test CA, create the bucket,
   and create a credential that may read and write objects but may not create buckets.
2. In `.env`, set `INFRAHUB_SYNC_S3_ENDPOINT_URL`, `INFRAHUB_SYNC_S3_BUCKET`,
   `INFRAHUB_SYNC_S3_REGION`, `INFRAHUB_SYNC_S3_ACCESS_KEY`, `INFRAHUB_SYNC_S3_SECRET_KEY`,
   `INFRAHUB_SYNC_S3_ADDRESSING_STYLE=path`, `INFRAHUB_SYNC_BUNDLED_OBJECT_STORE=0` and
   `INFRAHUB_SYNC_CA_CERTIFICATES=./ca`.
3. Run `docker compose up -d --wait`.
4. Expected: `docker compose ps -a` lists no `object-store-init` or `object-store`;
   `docker compose logs sync-bootstrap` shows `bucket present`; `docker compose images` lists two
   images.
5. Register the NetBox example package, plan, approve if required, and apply. Expected: the run's
   objects are in the external bucket.
6. Repeat with `INFRAHUB_SYNC_S3_ADDRESSING_STYLE=virtual` against a server that serves bucket
   host names.

## 2. Half-set external store (FR-002)

Set the endpoint to the external server and leave `INFRAHUB_SYNC_BUNDLED_OBJECT_STORE` unset.
Expected: `sync-bootstrap` exits with `refused: bundled-object-store-not-left-out`.

## 3. A store that ignores create-only writes (FR-004)

Point the endpoint at a test S3 server that accepts a second create-only write. Expected:
`sync-bootstrap` exits with `refused: object-store-unsupported`, and no key under
`.infrahub-sync/bootstrap-check/` remains.

## 4. Credentials of every bundled adapter (SC-003)

Set the two `CISCO_APIC_*` variables in `.env`, start the deployment, register
`examples/aci_to_infrahub/package.yml`, and plan against a recorded ACI test server. Expected: the
worker resolves both references; `docker inspect` of every other service (read by the test, not
printed) holds neither name.

## 5. Private CA (SC-004, FR-012, FR-013)

1. With the CA directory of step 1, check `docker compose logs sync-worker`. Expected: one line with
   the certificate count and the bundle path.
2. Add a file that holds a private key to the directory and restart `sync-worker`. Expected: the
   service exits with an error that names the file.
3. Plan against a NetBox and an Infrahub that use certificates from the test CA. Expected: the plan
   completes with certificate checks on.

## 6. Leak checks and lifecycle tier (SC-005, SC-006)

```bash
uv run pytest -m compose tests/compose --compose-zero-skip
```

Expected: the tier passes, and the leak checks report no credential name. In CI, dispatch the
nightly workflow with a repeat count of 10 on linux/amd64 and on linux/arm64. Expected: 20 passing
runs.

## 7. Release notes digest (SC-007)

On a release candidate, read the GitHub Release body. Expected: a "Container image" section with
`registry.opsmill.io/opsmill/infrahub-sync:<version>@sha256:...` and a `cosign verify` command whose
identity is `ci-docker-image.yml` at a release tag. Run that command; it verifies the signature.
