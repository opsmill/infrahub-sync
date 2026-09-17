# Custom adapter example

The `custom-example` package provides a deterministic source fixture. Its custom source
adapter reads five devices from `custom_adapter_src/mock_db.json`; a live, writable
Infrahub instance is the destination.

The CLI does not load this adapter from the caller's filesystem. A Sync service worker
must have the package and custom adapter installed in its execution environment.

## This package does not register as shipped

**`mockdb` has no adapter capability declaration, so registering this package is refused.**
Configuration validation resolves the package's `source.name` against a closed registry that
holds only the adapters bundled with infrahub-sync, and `mockdb` is not one of them. The
refusal happens before any configuration or version row is written, so nothing is registered.

What the CLI shows you is the service's fixed refusal envelope — HTTP `422`, code
`configs-validation`, family `validation`, no `reason`, and the message `the configuration
service refused the request`. The specific cause is an *internal* finding, `missing-adapter` at
`/configuration/source` with the message `adapter 'mockdb' has no configuration capability
declaration`; that detail reaches a client through `configs validate` on a stored version, not
through the refused registration.

Refusing is not the same as writing nothing: the request's idempotency receipt is reserved,
then released because the refusal is proven to precede any effect, and a durable audit event is
recorded for the attempt with outcome `unavailable`.

Installing the adapter somewhere a worker can import it does not change any of this, because
the refusal is about the missing declaration rather than about whether the class can be
imported.

Treat this directory as a shape reference and a deterministic source fixture. The commands
below are the correct current forms for the register-to-apply cycle, and they are what you
would run for a package whose adapter does ship with infrahub-sync. For the supported route
and the recorded reproduction of this refusal, see
[Adding an adapter](../../docs/docs/develop/guides/adding-an-adapter.md).

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

An empty destination that matches the mapping produces five `InfraDevice` creates — one per
record in `custom_adapter_src/mock_db.json`. A destination that already holds those devices
produces fewer, or none. Copy the `plan_checksum` value from the review output.

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
