# Quickstart: validate fewer images in the Compose deployment

Scenarios that prove the change end to end. Contracts:
[compose-services.md](contracts/compose-services.md),
[environment-variables.md](contracts/environment-variables.md),
[object-store.md](contracts/object-store.md), [ports.md](contracts/ports.md).

No command below prints a credential value. Do not replace `--quiet` or `--no-interpolate`
with a plain `docker compose config`, and do not use `docker inspect`: both print every
credential the deployment holds.

## Prerequisites

- Docker Engine with Compose 2.24 or later, on linux/amd64 or linux/arm64.
- The full development profile: `uv sync --extra dev --extra prefect --extra service`.
- An image built from the branch: `docker build -t infrahub-sync:compose-test .`
- A `.env` beside `docker-compose.yml` with every required credential
  (`docs/docs/compose-deployment.mdx`, "The `.env` file"), plus
  `INFRAHUB_SYNC_DOCKER_IMAGE=infrahub-sync` and `VERSION=compose-test`.

## 1. Offline contract checks

```bash
uv run pytest tests/compose/test_compose_contract.py tests/compose/test_compose_minimum.py \
  tests/preview/test_preview_configuration.py tests/service/test_prefect_server.py \
  tests/service/test_trust_store.py tests/service/test_storage.py \
  tests/service/test_compose_bootstrap.py
```

Expected: all pass. The contract tests find no `prefecthq/prefect` reference, no non-Sync image
without a digest, and no credential outside its receivers.

## 2. Three images, one published port (US1, US2, SC-001, SC-003)

```bash
docker compose up -d --wait
docker compose ps --format '{{.Service}} {{.Image}} {{.Publishers}}'
docker compose config --images | sort -u
```

Expected:

- `prefect-server` is healthy and its image is `infrahub-sync:compose-test`.
- `config --images` lists the Sync image, `postgres:16-alpine@sha256:…` and
  `cgr.dev/chainguard/minio:latest-dev@sha256:d7c90699…`.
- Only `sync-api` shows a publisher.

## 3. One Prefect version (SC-002)

```bash
docker compose exec -T sync-worker python -c "import prefect; print(prefect.__version__)"
docker compose exec -T sync-worker python -c "import base64,os,httpx; a=os.environ['PREFECT_API_AUTH_STRING']; print(httpx.get('http://prefect-server:4200/api/admin/version', headers={'Authorization': 'Basic '+base64.b64encode(a.encode()).decode()}).json())"
```

Expected: both print `3.8.6`.

## 4. Each credential reaches only its receivers (FR-002, FR-023, FR-035)

```bash
docker compose config --no-interpolate --format json \
  | jq -r '.services | to_entries[] | "\(.key): \(.value.environment // {} | keys | map(select(test("PASSWORD|SECRET|TOKEN|AUTH_STRING|CREDENTIAL|DATABASE_URL|CONNECTION_URL|ACCESS_KEY"))) | join(","))"'
```

Expected: the names per service match the receiver table in
[environment-variables.md](contracts/environment-variables.md). `prefect-server` lists only its
database connection string and its API credential.

## 5. Plan and apply (US1 AS2)

Register the NetBox example package, plan, approve and apply as in
`docs/docs/compose-deployment.mdx`, "The operator sequence". Expected: the run completes as with
the stock Prefect image. The lifecycle tier automates this:
`uv run pytest -m compose tests/compose -x --compose-zero-skip`.

## 6. Prefect opt-in (US2, SC-007)

Write `prefect-ui.yml` from [ports.md](contracts/ports.md), then:

```bash
docker compose -f docker-compose.yml -f prefect-ui.yml up -d --wait
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:4200/api/health   # 200
```

Open `http://127.0.0.1:4200` in a browser, enter `INFRAHUB_SYNC_PREFECT_AUTH_STRING`, and check
that the Sync work pool and deployment are listed. Then:

```bash
docker compose up -d --wait
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:4200/api/health   # 000: closed
```

## 7. Update keeps the bundled store's data (US3 AS2, SC-006)

On a deployment started from the previous release's `docker-compose.yml` that has run at least
one plan, replace the file with the new one and run `docker compose up -d --wait`. Then:

```bash
docker compose run --rm --no-deps -T cli runs show <earlier run id>
```

Expected: the plan and artifacts of the earlier run are read, with no reset.
`tests/compose/test_object_store_upgrade.py` automates this.

## 8. The team's own S3-compatible store (US3, SC-012)

Prepare a store outside the deployment, reachable from the Compose network over HTTPS, with a
bucket created beforehand and a credential for it. Add to `.env`:

```text
INFRAHUB_SYNC_BUNDLED_OBJECT_STORE=0
INFRAHUB_SYNC_S3_ENDPOINT_URL=https://<store host>
INFRAHUB_SYNC_S3_BUCKET=<bucket>
INFRAHUB_SYNC_S3_REGION=<region>
INFRAHUB_SYNC_S3_ADDRESSING_STYLE=path
INFRAHUB_SYNC_S3_ACCESS_KEY=<access key>
INFRAHUB_SYNC_S3_SECRET_KEY=<secret key>
INFRAHUB_SYNC_CA_CERTIFICATES=./ca-certificates
```

```bash
docker compose up -d --wait
docker compose ps -a --format '{{.Service}}' | grep -c '^object-store'   # 0
docker compose logs sync-bootstrap
```

Expected: no object store container; the bootstrap log holds no endpoint, bucket or credential
value; a plan and apply complete, and their objects are under the prefix in the team's bucket.
Repeat with `INFRAHUB_SYNC_S3_ADDRESSING_STYLE=virtual` against a store that serves
virtual-hosted-style requests. `tests/compose/test_external_object_store.py` automates both.

Negative checks:

- Remove `INFRAHUB_SYNC_BUNDLED_OBJECT_STORE=0`: `sync-bootstrap` stops with
  `bundled-object-store-not-left-out`.
- Point at a store that accepts a second create-only write: `sync-bootstrap` stops with
  `object-store-unsupported`.

## 9. Credentials and custom CA (US5, SC-008, SC-009)

Add to `.env`:

```text
INFRAHUB_SYNC_CREDENTIAL_ACI_USERNAME=<user>
INFRAHUB_SYNC_CREDENTIAL_ACI_PASSWORD=<password>
INFRAHUB_SYNC_CA_CERTIFICATES=./ca-certificates
```

Put the CA certificate in `./ca-certificates/company-ca.pem`, then `docker compose up -d --wait`.

```bash
docker compose exec -T sync-worker python -c "import os; print(bool(os.environ.get('INFRAHUB_SYNC_CREDENTIAL_ACI_USERNAME')))"
docker compose logs sync-worker | grep -i "ca bundle"
```

Expected: `True`; one log line with the certificate count and bundle path, and no certificate
content. A plan against a source whose certificate that CA signed connects with certificate
checks on. `tests/compose/test_custom_ca.py` automates this with a test HTTPS server. Put a
private key file in the directory: each Sync service stops, naming the file. Without
`INFRAHUB_SYNC_CA_CERTIFICATES`, every service starts as before.

## 10. No credential in output (SC-013)

```bash
uv run pytest -m compose tests/compose/test_credential_leaks.py
```

Expected: the test reports zero credential names found in documented command output, container
logs, and the environment of non-receiver services.

## 11. Development and preview stacks (US4)

```bash
uv run invoke start
curl -s http://127.0.0.1:4230/api/health
uv run invoke destroy
uv run invoke preview.up
curl -s http://127.0.0.1:4210/api/health
grep -c PREVIEW_PREFECT_IMAGE development/preview.env   # 0
```

## 12. Lifecycle reliability (SC-005, SC-010)

Dispatch `workflow-nightly-e2e.yml` with `compose_repeat: 10`. Expected: ten passes on the
amd64 leg and ten on the arm64 leg. Record any failure in plan.md, "Lifecycle failure causes".

## 13. Release digest and SBOM (SC-011)

After the first pre-release that includes `record_image`:

```bash
gh release view <version> --json body --jq .body | grep -A3 "Container image"
docker buildx imagetools inspect registry.opsmill.io/opsmill/infrahub-sync@<digest>
gh release view <version> --json assets --jq '.assets[].name'   # the two SBOM files
```
