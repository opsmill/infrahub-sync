# Contract: `infrahub-sync configs convert`

## Synopsis

```text
infrahub-sync configs convert PATH
    [--output PATH] [--force] [--yes]
    [--source-url URL] [--destination-url URL]
```

| Argument or option | Meaning |
| --- | --- |
| `PATH` | the v1 or v2 `config.yml` to read |
| `--output PATH` | where to write the package; default `package.yml` next to `PATH` |
| `--force` | replace an existing output file |
| `--yes` | write without asking |
| `--source-url URL` | the source address; overrides the file and the v2 variables |
| `--destination-url URL` | the Infrahub address; overrides the file and the v2 variables |
| `--live-schema` | read the destination schema without a token, and map each IP namespace that a uniqueness constraint needs and the file does not map to `default` |

The command needs no Sync API URL or token. It opens no network connection unless
`--live-schema` is given, and it never reads a credential value.

## Sequence

1. Read and parse `PATH`. On failure: exit 2.
2. If the output file exists and `--force` is absent: print "<output> exists; pass --force to
   replace it", exit 1.
3. Convert. On refusal: print every refusal to standard error, write nothing, exit 1.
4. Print the report to standard output.
5. Confirm:
   - with `--yes`: continue
   - with a terminal: ask `Write <output>? [y/N]`; `n` or empty: print "nothing written", exit 0
   - without a terminal and without `--yes`: print "nothing written; run again with --yes",
     exit 1
6. Write the file through a temporary file in the same directory and a rename. Print the next
   steps, exit 0.

## Exit status

| Status | When |
| --- | --- |
| 0 | the package was written, or the engineer answered `n` |
| 1 | the configuration was refused, the output exists without `--force`, or no terminal and no `--yes` |
| 2 | `PATH` cannot be read, is not YAML, is not a v1 or v2 configuration, is already a V3 package, or an option is invalid |

## Report layout (standard output)

Fixed section order, one line per entry. Sections with no entries are left out.

```text
Converting config.yml (v2 configuration "from-netbox")

Credentials (set these in the Sync deployment's .env file):
  source.settings.token        NETBOX_TOKEN (v2) -> INFRAHUB_SYNC_CREDENTIAL_NETBOX_TOKEN
  destination.settings.token   INFRAHUB_API_TOKEN (v2) -> INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN
  warning: config.yml still holds a literal value for source.settings.token; remove it once V3 works

Settings taken from the environment or the file:
  source.settings.url          https://netbox.example.com   from NETBOX_ADDRESS (config.yml had https://demo.netbox.dev)
  destination.settings.url     http://localhost:8000        from config.yml
  warning: destination.settings.url is localhost; the Sync worker runs in a container and may not reach it
  v2 variables visible here: NETBOX_ADDRESS

Dropped:
  store                        V3 keeps sync data in memory during a run

Notes:
  V3 has no generation step; the generated adapter and model files are not needed

Next steps:
  1. Set INFRAHUB_SYNC_CREDENTIAL_NETBOX_TOKEN and INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN in .env
  2. Restart the deployment so the Sync worker receives them
  3. infrahub-sync configs register package.yml
```

## Refusal layout (standard error)

```text
error: config.yml cannot be converted (2 reasons)
  source.adapter          ./custom_adapter.py:MockdbAdapter   custom adapters are not available in this release (INFP-654, planned for 3.0)
  destination.name        peeringmanager                      V3 does not write to peeringmanager yet; this release writes only to Infrahub
```

The value column is empty for a credential setting.
