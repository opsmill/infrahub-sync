# Review the local plan and apply workflow

The `custom-example` project provides a deterministic source fixture for a bounded live
review of the local CLI and Python execution surfaces. Its custom source adapter reads
five devices from `custom_adapter_src/mock_db.json`; a live, writable Infrahub instance is
the destination.

Run every command from the repository root. The configuration uses repository-relative
adapter and fixture paths.

## Prerequisites

- Install the repository environment with `uv sync --extra dev --extra prefect`.
- Run an Infrahub instance that you may modify.
- Export `INFRAHUB_ADDRESS` and `INFRAHUB_API_TOKEN` for that instance.
- Install `infrahubctl` if it is not already available.
- Start with no `InfraDevice` objects when you want the plan to contain exactly five
  creates.

Load the schema used by the example:

```bash
uv run infrahubctl schema load \
  examples/prefect_remote_run/schemas/infra_device.yml \
  --branch main
```

Do not load this file over an incompatible existing `InfraDevice` kind. Use a disposable
Infrahub instance or inspect the existing schema first.

## Create and review a plan

Create a read-only plan:

```bash
uv run infrahub-sync diff --name custom-example --directory examples/
```

The output names the run identifier and cache directory. Review the saved artifact without
contacting the source or destination:

```bash
uv run infrahub-sync diff \
  --name custom-example \
  --directory examples/ \
  --from-plan <run-id> \
  --detail
```

An empty destination produces five `InfraDevice` creates. Copy the `plan checksum` value
from the review output.

## Apply the reviewed plan

Apply the exact artifact whose checksum you reviewed:

```bash
uv run infrahub-sync apply \
  --name custom-example \
  --directory examples/ \
  --run-id <run-id> \
  --expected-checksum <plan-checksum>
```

The apply verifies the artifact and destination binding before its first write. It does not
read the source or recompute the plan.

## Verify convergence

Create a new plan after the apply:

```bash
uv run infrahub-sync diff --name custom-example --directory examples/
```

The new plan should report zero creates, updates, and deletes. If it does not, inspect the
destination schema and the five source records in `custom_adapter_src/mock_db.json` before
applying another plan.

## Related execution surfaces

- Use the same `custom-example` project with the
  [Python API](../../docs/docs/reference/python-api.mdx).
- Follow the [Prefect remote-run walkthrough](../prefect_remote_run/README.md) to serve
  the project as a direct Prefect deployment.
- Read [Run a sync](../../docs/docs/running-a-sync.mdx) for plan format, partial-write,
  delete, and convergence behavior.
