# Data model: Convert a v1 or v2 configuration and prove the first V3 plan

No entity is stored. The conversion reads one file and writes one file. The gate writes one JSON
result per run.

## Input: v2 configuration

The keys the command reads, with the rule each one follows ([research R1](research.md#r1-which-v1-and-v2-file-shapes-the-command-reads)).

| Key | Rule | Spec |
| --- | --- | --- |
| `name` | copied | FR-019 |
| `source.name`, `destination.name` | copied when bundled; refused otherwise; destination must be `infrahub` | FR-015 |
| `source.adapter`, `destination.adapter` | dropped with a note when it names the bundled module; refused otherwise | FR-015, FR-016 |
| credential settings | replaced by a credential reference | FR-006 to FR-008 |
| `url` and other settings v2 read from the environment first | value from the variable, the file or a flag, with its origin | FR-010 to FR-013, FR-029 |
| `genericrestapi` `*_env_vars` settings | dropped with a note; the variables they name are not read | research R2, FR-030 |
| an address with a user name or password | refused; the address is not printed | FR-030 |
| other accepted settings | copied | FR-019 |
| other settings | refused | FR-015 |
| `store` | dropped with a warning | FR-017 |
| `adapters_path` | dropped with a warning | FR-017 |
| `incremental` | copied for `infrahub`, `nautobot`, `netbox` sources; dropped with a warning otherwise | FR-017, FR-019 |
| `order`, `diffsync_flags`, `schema_mapping` | copied | FR-019 |
| `format_version` present | input refused as already a V3 package | FR-021 |

## Output: V3 configuration package

```yaml
format_version: 1
configuration:
  name: <v2 name>
  source:
    name: <bundled adapter>
    settings:
      url: <resolved address>
      token:
        $credential: <adapter>-token
  destination:
    name: infrahub
    settings:
      url: <resolved address>
      token:
        $credential: infrahub-token
  # order, diffsync_flags, incremental, schema_mapping as copied
credentials:
  <adapter>-token:
    provider: env
    identifier: INFRAHUB_SYNC_CREDENTIAL_<ADAPTER>_TOKEN
  infrahub-token:
    provider: env
    identifier: INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN
```

Validation: the mapping MUST parse with `parse_configuration_package` and MUST have no error
finding from `collect_findings` (FR-020, [research R6](research.md#r6-which-checks-run-before-writing-fr-020)).

## Conversion result

What the pure conversion returns to the CLI.

- `package`: the package mapping, or nothing when refused.
- `refusals`: list of refusal entries. Not empty means `package` is nothing.
- `report`: the entries the CLI prints, in this order:
  1. **Credential entry**: setting location (`source.settings.token`), v2 origin (`literal`,
     `placeholder ${NAME}`, `variable NAME`, or `none`), reference name, V3 variable, and
     `worker_receives` (yes or no, from `COMPOSE_WORKER_CREDENTIAL_VARIABLES`). Never a value.
  2. **Setting origin entry**: setting location, value written, origin (`config.yml`,
     `variable NAME`, `--source-url` or `--destination-url`), the other value when they differed,
     and whether the value is `localhost` or `127.0.0.1`.
  3. **Visible variables entry**: the R2 variables that were set where the command ran, names
     only.
  4. **Dropped entry**: key location and reason.
  5. **Note entry**: free notes, for example "V3 has no generation step" or "`adapter:` names the
     bundled module".
  6. **Finding entry**: warning findings from `collect_findings`.
- **Refusal entry**: location, the value (never for a credential setting), and the reason. For an
  unbundled name, the reason lists the bundled names.

## Gate result (`gate-result.json`)

| Field | Content |
| --- | --- |
| `v2_version` | `2.0.2` |
| `infrahub_version` | the preview Infrahub version |
| `dataset` | the pinned `demo` dataset commit and SHA-256 |
| `counts` | `create`, `update`, `delete`, `left_out`, `problems` |
| `unexpected` | one entry per create, update, unlisted delete, left-out record or problem: kind, identity, and for an update each field with the v2 value and the V3 value |
| `expected_deletes_matched` | the expected-delete entries that the plan held |
| `passed` | true only when every count except listed deletes is 0 |

## Expected-delete list (`tests/data/v2_gate/expected_deletes.yml`)

One entry per expected delete: `kind`, `identity`, `reason`. It starts empty; an entry is added
only with a reason that a reviewer accepts in the pull request that adds it.
