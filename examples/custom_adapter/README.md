# Custom adapter example

The `custom-example` package provides a deterministic source fixture. Its custom source
adapter reads five devices from `custom_adapter_src/mock_db.json`; a live, writable
Infrahub instance is the destination.

The CLI does not load this adapter from the caller's filesystem. A Sync service worker
must have the package and custom adapter installed in its execution environment.

## Register and review

Connect the CLI to that service and register the package:

```bash
export INFRAHUB_SYNC_API_URL=https://sync.example.com
export INFRAHUB_SYNC_API_TOKEN=<token>

uv run infrahub-sync configs register examples/custom_adapter/package.yml \
  --reason "register custom adapter example"
uv run infrahub-sync diff --config-id <config-id> --version <version> \
  --reason "review custom adapter plan"
uv run infrahub-sync runs plan <run-id> --detail
```

An empty destination produces five `InfraDevice` creates. Copy the `plan_checksum` value
from the review output.

## Apply the reviewed plan

```bash
uv run infrahub-sync apply <run-id> \
  --expected-checksum <plan-checksum> \
  --reason "apply custom adapter plan"
```

The service verifies the reviewed checksum before worker execution. The CLI does not read
the source or a local plan.

## Verify convergence

Create a new plan over the same registered version. It should report zero creates,
updates, and deletes. If it does not, inspect the worker's installed adapter, the
destination schema, and `custom_adapter_src/mock_db.json` before applying another plan.

See [Run a sync](../../docs/docs/running-a-sync.mdx) for wait, idempotency, delete, and
failure behavior.
