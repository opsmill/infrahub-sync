# Data model: NetBox demo ready to try

No new stored data. The command reads two bundled assets and writes through two existing APIs.

## Demo assets (read-only, bundled)

| Asset | Source in the repository | Location in the wheel |
| --- | --- | --- |
| Destination schema, 16 YAML files | `tests/data/nightly_schema/traditional-infrastructure-sot/` | `infrahub_sync/demo/assets/schema/` |
| NetBox package | `examples/netbox_to_infrahub/package.yml` | `infrahub_sync/demo/assets/netbox/package.yml` |

Rule: the package must contain each shipped URL line (`https://demo.netbox.dev`,
`http://localhost:8000`) exactly once, or preparation stops (R-2).

## Preparation result (command output)

| Field | Values | Notes |
| --- | --- | --- |
| `schema` | `loaded`, `unchanged` | From the schema load answer |
| `config_id` | string | The NetBox configuration's ID |
| `version` | integer | The registered version |
| `configuration` | `registered`, `versioned`, `unchanged` | `registered` when no configuration with that name existed |
| `netbox_url`, `infrahub_url` | URLs | The addresses written into the package |

No token appears in the result.

## State transitions of the NetBox configuration

```text
(none) --prepare--> registered v1
vN --prepare, same addresses--> vN (unchanged)
vN --prepare, other addresses--> vN+1 (versioned)
```
