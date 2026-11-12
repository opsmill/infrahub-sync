# Fixture validation and operator quickstart

The shipped project maps Webex locations, workspaces and devices to `WebexLocation`, `WebexWorkspace` and `WebexDevice`. Before live generation, load `examples/webex_workspaces_to_infrahub/schema.yml` into the destination Infrahub instance and set its address in the example configuration. Each kind uses the Webex `id` as its `webex_id` identifier. Workspaces reference locations, and devices reference workspaces.

```bash
uv sync --extra dev
uv run pytest -q tests/adapters/test_webex_client.py tests/adapters/test_webex.py tests/adapters/test_webex_examples.py
uv run infrahub-sync list --directory examples/
```

The offline tests generate models from a fixture schema, create 25 devices and their location/workspace peers in an in-memory destination, check mapped attributes and peer write order, and verify convergence after a fresh destination load. Separate projects test organization isolation. The deletion test proves that removing one device produces only its delete diff; persisted Infrahub deletion remains a core limitation.

For live validation, supply `WEBEX_TOKEN` and `INFRAHUB_API_TOKEN` through the environment or secret manager, prepare the destination schema and connection settings, then run:

```bash
uv run infrahub-sync generate --config-file examples/webex_workspaces_to_infrahub/config.yml
uv run infrahub-sync diff --config-file examples/webex_workspaces_to_infrahub/config.yml
```

Review the diff before approving a sync. The example creates inventory nodes and has no destination-retention flag. Display-name changes update existing nodes because Webex IDs identify them. See the [adapter documentation](../../../docs/docs/adapters/webex.mdx) for authentication, resource options and the persisted-deletion limitation.
