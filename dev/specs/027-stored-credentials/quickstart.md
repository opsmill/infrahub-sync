# Quickstart: validate stored credentials

Prerequisites: the full development profile (`uv sync --extra dev --extra prefect --extra service`), and
for scenarios 3 to 6 the development stack (`uv run invoke dev.start`) and the NetBox demo
(`uv run invoke dev.demo`). The contracts are in [contracts/](contracts/).

## 1. Offline checks

```bash
uv run invoke tests.tests-unit
uv run invoke lint
uv run ruff check .
```

Expected: every test passes, including the marker search (scenario 6 at unit level) and the
parametrized refusal of the worker account on every route outside `/worker/`.

## 2. The CLI shows the new group

```bash
uv run infrahub-sync credentials --help
uv run infrahub-sync configs --help
```

## 3. Create once, use from any worker (P1, SC-001, SC-003)

```bash
uv run infrahub-sync login admin
printf '%s' "$NETBOX_TOKEN" | uv run infrahub-sync credentials create netbox-token --token-stdin
printf '%s' "$INFRAHUB_TOKEN" | uv run infrahub-sync credentials create infrahub-token --token-stdin
uv run infrahub-sync configs register examples/netbox_to_infrahub/package.yml
uv run infrahub-sync plan netbox-to-infrahub
```

Start a second worker (`docker compose -f development/docker-compose.yml up -d --scale sync-worker=3`)
and start 6 plans. Expected: every plan completes; no worker has an `INFRAHUB_SYNC_CREDENTIAL_*`
variable (`docker compose exec sync-worker env | grep INFRAHUB_SYNC_CREDENTIAL_` prints nothing).

## 4. Change once (P2, SC-002)

```bash
printf '%s' "$NEW_TOKEN" | uv run infrahub-sync credentials set netbox-token --token-stdin
uv run infrahub-sync configs list        # no new version
uv run infrahub-sync plan netbox-to-infrahub
uv run infrahub-sync runs show <run_id>  # credentials_used shows revision 2
```

## 5. Refusals (FR-004, FR-005, FR-008, FR-010, FR-012)

- `credentials delete netbox-token` while the package names it: exit 1, lists `netbox-to-infrahub`.
- A non-administrator token: `credentials list` works; `credentials create x` is refused.
- Calling `POST /api/worker/runs/<finished run>/credentials` with the worker token: `409`.
- Calling the worker route with the admin token: `403`.

## 6. Marker search (SC-004, FR-002, FR-007)

Create a credential whose token is a marker such as `MARKER-7f3c9e`, run a plan and an apply, then:

```bash
docker compose -f development/docker-compose.yml logs | grep -c MARKER-7f3c9e        # 0
docker compose -f development/docker-compose.yml exec postgres pg_dump -U sync sync | grep -c MARKER-7f3c9e  # 0
```

Search the CLI output and the run records in the same way. Expected: 0 everywhere.

## 7. Conversion (P3, SC-005)

```bash
uv run infrahub-sync configs convert examples/netbox_to_infrahub/config.yml --output /tmp/package.yml
grep -c 'credential:' /tmp/package.yml   # one per authenticated block
grep -c 'credentials:' /tmp/package.yml  # 0
```

The report lists `netbox-token` and `infrahub-token` with the command to create each.
