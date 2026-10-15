# Quickstart: validate fewer images in the Compose deployment

Scenarios that prove the change end to end. Contracts:
[compose-services.md](contracts/compose-services.md),
[environment-variables.md](contracts/environment-variables.md), [ports.md](contracts/ports.md).

## Prerequisites

- Docker Engine with Compose 2.24 or later, on linux/amd64 or linux/arm64.
- The full development profile: `uv sync --extra dev --extra prefect --extra service`.
- An image built from the branch: `docker build -t infrahub-sync:compose-test .`
- A `.env` beside `docker-compose.yml` with every required credential
  (`docs/docs/compose-deployment.mdx`, "The `.env` file"), plus
  `INFRAHUB_SYNC_DOCKER_IMAGE=infrahub-sync` and `VERSION=compose-test`.

## 1. Offline contract checks

```bash
uv run pytest tests/compose/test_compose_contract.py tests/preview/test_preview_configuration.py \
  tests/service/test_prefect_server.py tests/service/test_trust_store.py
```

Expected: all pass. The contract tests find no `prefecthq/prefect` reference and no non-Sync image
without a digest in the three compose files.

## 2. Three images, one published port (US1, US2, SC-001, SC-003)

```bash
docker compose up -d --wait
docker compose ps --format '{{.Service}} {{.Image}} {{.Publishers}}'
docker compose config --images | sort -u
```

Expected:

- `prefect-server` is healthy and its image is `infrahub-sync:compose-test`.
- `config --images` lists the Sync image, `postgres:16-alpine@sha256:…` and `pgsty/silo:…@sha256:…`.
- Only `sync-api` shows a publisher.

## 3. One Prefect version (SC-002)

```bash
docker compose exec -T sync-worker python -c "import prefect; print(prefect.__version__)"
docker compose exec -T sync-worker python -c "import base64,os,httpx; a=os.environ['PREFECT_API_AUTH_STRING']; print(httpx.get('http://prefect-server:4200/api/admin/version', headers={'Authorization': 'Basic '+base64.b64encode(a.encode()).decode()}).json())"
```

Expected: both print `3.8.6`.

## 4. Prefect database credential stays with the server (US1 AS4, FR-002)

```bash
for s in prefect-server db-bootstrap sync-api sync-worker sync-bootstrap; do
  printf '%s: ' "$s"; docker compose config --format json | jq -r --arg s "$s" \
    '.services[$s].environment | keys | map(select(test("PREFECT_SERVER_DATABASE|PREFECT_PASSWORD"))) | join(",")'
done
```

Expected: names only for `prefect-server` and `db-bootstrap`.

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

## 7. Object store update keeps the data (US3, SC-006)

On a deployment started from the previous release's `docker-compose.yml` that has run at least
one plan, replace the file with the new one and run `docker compose up -d --wait`. Then:

```bash
docker compose run --rm --no-deps -T cli runs show <earlier run id>
```

Expected: the plan and artifacts of the earlier run are read. `tests/compose/test_object_store_upgrade.py`
automates this.

## 8. Credentials and custom CA (US5, SC-008, SC-009)

Add to `.env`:

```text
INFRAHUB_SYNC_CREDENTIAL_ACI_USERNAME=<user>
INFRAHUB_SYNC_CREDENTIAL_ACI_PASSWORD=<password>
INFRAHUB_SYNC_CA_CERTIFICATES=./ca-certificates
```

Put the CA certificate in `./ca-certificates/company-ca.pem`, then `docker compose up -d --wait`.

```bash
docker compose exec -T sync-worker sh -c 'test -n "$INFRAHUB_SYNC_CREDENTIAL_ACI_USERNAME" && echo set'
docker compose logs sync-worker | grep -i "ca bundle"
```

Expected: `set`; one log line with the certificate count and bundle path, and no certificate
content. A plan against a source whose certificate that CA signed connects with certificate
checks on. `tests/compose/test_custom_ca.py` automates this with a test HTTPS server. Without
`INFRAHUB_SYNC_CA_CERTIFICATES`, every service starts as before.

## 9. Development and preview stacks (US4)

```bash
uv run invoke start
curl -s http://127.0.0.1:4230/api/health
uv run invoke destroy
uv run invoke preview.up
curl -s http://127.0.0.1:4210/api/health
grep -c PREVIEW_PREFECT_IMAGE development/preview.env   # 0
```

## 10. Lifecycle reliability (SC-005, SC-010)

Dispatch `workflow-nightly-e2e.yml` with `compose_repeat: 10`. Expected: ten passes on the
amd64 leg and ten on the arm64 leg. Record any failure in plan.md, "Lifecycle failure causes".

## 11. Release digest and SBOM (SC-011)

After the first pre-release that includes `record_image`:

```bash
gh release view <version> --json body --jq .body | grep -A3 "Container image"
docker buildx imagetools inspect registry.opsmill.io/opsmill/infrahub-sync@<digest>
gh release view <version> --json assets --jq '.assets[].name'   # the two SBOM files
```
