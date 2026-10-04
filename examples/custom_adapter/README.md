# Custom adapter example

The `custom-example` package provides a deterministic source fixture. Its custom source
adapter reads five devices from `custom_adapter_src/mock_db.json`; a live, writable
Infrahub instance is the destination.

The CLI does not load this adapter from the caller's filesystem. A Sync service worker
must have the package and custom adapter installed in its execution environment.

## This package does not run as shipped

**`mockdb` has no adapter capability declaration, so this package fails validation.**
Configuration validation resolves the package's `source.name` against a closed registry that
holds only the adapters bundled with infrahub-sync, and `mockdb` is not one of them.

You can still store the package in Infrahub as a `SyncConfiguration`. `configs validate
CONFIG_ID` then reports the finding, `missing-adapter` at `/configuration/source` with the
message `adapter 'mockdb' has no configuration capability declaration`. A run refuses it with
`422 configuration-invalid` before it records a version, so nothing runs.

Installing the adapter somewhere a worker can import it does not change any of this, because
the refusal is about the missing declaration rather than about whether the class can be
imported.

## What this directory is for

Treat it as a shape reference and a deterministic source fixture:

| File | What it shows |
| --- | --- |
| `custom_adapter_src/custom_adapter.py` | `MockdbAdapter` and `MockdbModel` — the two classes, a client, `model_loader` and a full `obj_to_diffsync` |
| `custom_adapter_src/mock_db.json` | The five-device fixture the adapter reads |
| `config.yml` | The declared configuration |
| `examples/custom_adapter/package.yml` | The package envelope that wraps `config.yml` |

**No runnable plan-to-apply sequence is given here, because there is none for this
package.** It cannot enter that cycle until `mockdb` has a bundled capability declaration,
and adding one means adding the adapter to the distribution.

For the current command forms and the worked configure → validate → plan → review → apply →
verify flow, follow
[Adding an adapter](../../docs/docs/develop/guides/adding-an-adapter.md), which teaches the
supported in-repository route and records the reproduction of the refusal above.
[Run a sync](../../docs/docs/running-a-sync.mdx) covers wait, idempotency, delete and failure
behavior for a package that validates.
