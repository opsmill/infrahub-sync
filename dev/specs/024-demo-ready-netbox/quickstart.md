# Quickstart: validate the NetBox demo

## Offline checks

```bash
uv run pytest tests/demo tests/dev_stack tests/test_netbox_tasks.py tests/test_task_inventory.py -q
uv build --wheel && unzip -l dist/infrahub_sync-*.whl | grep -c "infrahub_sync/demo/assets/"   # expect 17
uv run infrahub-sync demo prepare --help
```

## Scenario 1: local demo from a fresh clone (US1, SC-001)

Prerequisites: Docker, `uv`, Python 3.11 to 3.13, no development stack running.

```bash
time uv run invoke dev.demo
```

Expected: under 15 minutes; the output lists the web interface address, the configuration ID and
the plan command. Then:

```bash
export INFRAHUB_ADDRESS=http://localhost:8000 INFRAHUB_API_TOKEN=<printed admin token>
export INFRAHUB_SYNC_API_URL=http://127.0.0.1:8001 INFRAHUB_SYNC_API_TOKEN=infrahub-sync-dev-token
uv run infrahubctl branch create netbox-demo-1
uv run infrahub-sync plan --config-id <config-id> --version 1 --branch netbox-demo-1 --reason "demo"
```

Expected: the plan finishes with creates across the package's kinds (about 1,687 on 2026-09-29).
Open the web interface, review the plan, apply it; the objects appear on `netbox-demo-1`.

## Scenario 2: run it again (US1 scenario 3, SC-004)

```bash
uv run invoke dev.demo
```

Expected: `schema: unchanged`, `configuration: unchanged`.

## Scenario 3: the image path (US2, SC-002)

With the stack from scenario 1 running, run the command from the built image, with no source
mount, on the stack's network, with another NetBox address:

```bash
docker run --rm --network <stack network> \
  -e INFRAHUB_API_TOKEN -e INFRAHUB_SYNC_API_URL=http://sync-api:8000 -e INFRAHUB_SYNC_API_TOKEN \
  infrahub-sync:dev infrahub-sync demo prepare \
  --netbox-url https://demo.netbox.dev --infrahub-url http://infrahub-server:8000
```

Expected: `configuration: versioned`, version 2. No token in the output.

## Scenario 4: schema conflict (US2 scenario 4)

Against an Infrahub that holds the smoke schema (`dev.preview`), run `demo prepare`. Expected: exit 1,
the conflict named, no configuration registered.

## Scenario 5: pre-release image (US3)

Follow `docs/docs/development/guides/publishing-an-image.md` with `ref: feature/v3-develop` and a tag
such as `3.0.0-rc.1`. Expected: both platforms, signature, SBOM; `latest` unchanged. This is a
maintainer action; agents do not dispatch it.
