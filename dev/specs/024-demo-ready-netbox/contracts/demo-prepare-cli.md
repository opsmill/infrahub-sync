# Contract: `infrahub-sync demo prepare`

```text
infrahub-sync [--url URL] demo prepare
    --netbox-url URL          NetBox address written into the package (the worker's view)
    --infrahub-url URL        Infrahub address written into the package (the worker's view)
    [--infrahub-address URL]  Infrahub address this command uses for the schema load;
                              defaults to --infrahub-url
    [--reason TEXT]           Audit reason for the registration
    [--json]                  Print the result as one JSON object
```

Environment:

- `INFRAHUB_API_TOKEN` (required): token for the schema load.
- The Sync API URL and token through the CLI's existing global options and variables.

Behaviour:

1. Missing `INFRAHUB_API_TOKEN` → exit 2, message names the variable, nothing written.
2. Schema load error → exit 1, message names the cause, nothing registered.
3. Sync API error → exit 1 through the CLI's existing error handler; the schema may already be
   loaded, and a second run completes the registration.
4. Success → exit 0 and the result described in [data-model.md](../data-model.md).
5. A second run with the same arguments → exit 0, `schema: unchanged`, `configuration: unchanged`.
6. No token value in standard output, standard error or the registered package.
7. Never plans, applies, approves, or changes `INFRAHUB_SYNC_REQUIRE_APPROVAL`.

Text output example:

```text
schema: unchanged (16 files)
configuration: registered from-netbox, config_id 20261118T1200-1234abcd, version 1
netbox: http://netbox:8080
infrahub: http://infrahub-server:8000
```
