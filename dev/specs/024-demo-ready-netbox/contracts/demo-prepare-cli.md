# Contract: `infrahub-sync demo prepare`

```text
infrahub-sync [--api-url URL] demo prepare
    --netbox-url URL          NetBox address written into the package (the worker's view)
    --infrahub-url URL        Infrahub address written into the package (the worker's view)
    [--infrahub-address URL]  Infrahub address this command uses for the schema load;
                              defaults to --infrahub-url
    [--schema-timeout SECONDS] Wait for schema convergence; default 120
    [--reason TEXT]           Audit reason for the registration
    [--json]                  Print the result as one JSON object
```

Environment:

- `INFRAHUB_API_TOKEN` (required): token for the schema load.
- The Sync API address from `--api-url` or `INFRAHUB_SYNC_API_URL`, and its token from
  `INFRAHUB_SYNC_API_TOKEN`.

Behaviour:

1. Missing `INFRAHUB_API_TOKEN` → exit 2, message names the variable, nothing written.
2. `--netbox-url`, `--infrahub-url` or `--infrahub-address` carries `user:password@` → exit 2,
   message names the option and not its value, nothing loaded or registered.
3. Schema load error → exit 1, message names the cause, nothing registered.
4. Schema loaded but not applied within `--schema-timeout` seconds (rounded up) → exit 1, message
   says to re-run with a larger `--schema-timeout`, nothing registered.
5. Sync API error after the schema load → exit 1 through the CLI's existing error handler, then
   a note that the schema is already loaded and that a second run completes the registration.
6. Success → exit 0 and the result described in [data-model.md](../data-model.md).
7. A second run with the same addresses → exit 0, `schema: unchanged`, `configuration: unchanged`,
   also with a different `--reason`. The idempotency keys include the reason.
8. A package that a version of the configuration already holds → `configuration: unchanged` and
   that version's number. Only a version above the configuration's latest version before the
   call is `versioned`.
9. No token value in standard output, standard error or the registered package.
10. Never plans, applies, approves, or changes `INFRAHUB_SYNC_REQUIRE_APPROVAL`.

Text output example:

```text
schema: unchanged (16 files)
configuration: registered from-netbox, config_id 20261118T1200-1234abcd, version 1
netbox: http://netbox:8080
infrahub: http://infrahub-server:8000
```
