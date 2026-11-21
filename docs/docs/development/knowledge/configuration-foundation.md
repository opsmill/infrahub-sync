---
title: "Configuration foundation"
---

## Configuration foundation

The v3 configuration foundation separates declared configuration from runtime secrets.
`infrahub_sync.configuration.ConfigurationPackage` is a strict versioned envelope. Its
checksum covers only declared JSON content, including the name of the stored credential that
each adapter block declares and behavior-affecting package metadata. Generated code, local paths, registry identity, and
resolved secret values never enter the checksum.

Durable or public callers parse untrusted declarations with
`parse_configuration_package()`. This boundary converts Pydantic failures into locations
and reason codes without including rejected values. Calling Pydantic's low-level
`ConfigurationPackage.model_validate()` directly is reserved for already-trusted data.

A package never holds a credential value. Each adapter block names one stored credential with
the optional key `credential`, next to `name` and `settings`:

```yaml
source:
  name: netbox
  credential: netbox-token
  settings:
    url: https://netbox.example.com
```

The name matches `CREDENTIAL_NAME_PATTERN` in `configuration/models.py`. The key is serialized
only when the block declares it, so a package that names no credential keeps the declared
content and the checksum it had before the key existed. Pointing a block to another credential
changes the checksum; changing the stored value does not.

The Sync API stores the value, encrypted, and releases it to the worker for a run in progress.
[ADR 0054](https://github.com/opsmill/infrahub-sync/blob/feature/v3-develop/dev/adr/0054-the-sync-api-stores-credentials-and-releases-them-to-the-worker-account.md)
records the decision. The parts in `infrahub_sync/configuration/` are:

- **Which stored field fills which setting.** `credential_field_map` in `capabilities.py` returns,
  for an adapter and its settings, the stored fields (`token`, `username`, `password`) that the
  block needs and the setting each one fills. An adapter with an `auth_method` setting needs the
  fields of that method.
- **A credential setting is refused in `settings`.** A block that names a credential and also
  declares one of its credential settings is refused when the package is parsed. A block that
  names none and holds a value at a credential setting path gets the finding
  `inline-credential-value`. A block that names a credential that its adapter and `auth_method`
  do not use gets `credential-not-used`.
- **The named credential must exist.** `stored_credential_findings` in `credential_admission.py`
  reports `credential-not-found`, `credential-field-missing` and `credential-method-unknown`. It
  reads the field names of a credential through a callable and never a value.
- **Run time.** `resolve_runtime_instance` in `runtime.py` takes the values that the Sync API
  released for the run and writes them into the credential settings of each block. It marks the
  settings as registered, and `select_runtime_credential` in `credentials.py` then reads the
  setting only, never an environment variable.

Outside a registered package, `select_runtime_credential` still reads the adapter's environment
variables, such as `NETBOX_TOKEN`, before the setting. The direct Prefect flow uses that branch:
`run_remote_request` builds its instance from a v2 file without the registered marker
(`ADAPTER_CREDENTIAL_ENV_VARS` in `infrahub_sync/execution.py` lists the variables). The Sync
service and its worker never take that branch for a registered package.

### The removed credential format

An earlier build declared a top-level `credentials` block with an `env` provider, and wrote a
`{"$credential": "<name>"}` node at each credential setting. Both are removed
(`removed_credential_format.py`):

- `parse_configuration_package` refuses new input that holds a `credentials` key, empty or not,
  or a `$credential` key in any mapping. The reason names `credential: <name>` and nothing of the
  refused package.
- Content read from the product store goes through `parse_stored_configuration_package` in
  `stored_package.py`. The earlier build wrote an empty `credentials` block into every version,
  and the stored checksum covers it. The function ignores an empty block and returns the checksum
  of the content with the block, so such a version keeps the checksum that its runs, its saved
  plans and its approvals hold. Compare that checksum, not `package.checksum()`, with the stored
  one.
- Stored content with a `credentials` block that holds an entry, or with a `$credential`
  reference, raises `RemovedCredentialFormatError`. The routes that start a stage answer
  `422 credential-format-removed`.

Exactly one function turns a declared setting path into a finding location: `_settings_pointer`
in `validation.py`. Any further component that renders these pointers calls that function
rather than formatting its own.

### Registry persistence and identity

The product store owns an append-only registry. A configuration has a server-generated
`config_id` and immutable versions numbered by an integer `registry_version` starting at 1 and
scoped to that `config_id`. A version row also carries the `package_checksum` and the declared
content that produced it. Nothing updates or deletes a version.

`registry_version` is deliberately not called `config_version`. That name already belongs to the
saved-plan value in `infrahub_sync/plan/config_version.py`: an opaque printable-ASCII string,
compared for equality and never parsed, which `product_runs.configuration_reference` already
stores. Runs and plans bind to `(config_id, registry_version, package_checksum)`; the saved-plan
manifest keeps its own `config_version` field. Two names, two concepts, one table — keep them
apart.

Registering a checksum that already exists under a configuration returns the existing version and
writes no row. A new checksum takes the next integer, allocated with the store's existing
idiom: read `MAX + 1`, insert, catch the uniqueness violation, retry within a bounded budget,
raise a typed error on exhaustion. Uniqueness is enforced by indexes on `(config_id,
registry_version)` and `(config_id, package_checksum)`, so identical content under two different
configurations is accepted — configurations are independent, not deduplicated against each other.
Because a losing writer recomputes `MAX + 1` on a fresh connection and an exhausted one never
commits, the sequence cannot develop gaps.

Credential validation happens at the `ProductProjection` boundary, not in the store classes,
matching where redaction already lives. A caller reaching for `SQLiteRunStore` or
`PostgreSQLRunStore` directly bypasses it; neither is part of the package's public surface.

#### Schema creation and dialect

Construction does not migrate columns or data. It creates each table, index, trigger and
constraint that is missing, also on a table that already exists, and does not add a column to a
table that already exists. There is no schema version table. A
database that an earlier version created is not upgraded: start with an empty database.
Construction is idempotent, so a process restart re-runs it safely.

The store selects its constraint statements from an explicit `dialect` argument
that each provider passes. Never infer the dialect from the placeholder string or by running a
statement to see whether it fails: the test suite's PostgreSQL profile is a SQLite file behind a
`%s`-to-`?` adapter, so a placeholder check misidentifies it, and provoking an error on a
connection mid-DDL aborts the transaction and discards uncommitted `CREATE TABLE` statements.

#### The run-binding columns are inert on purpose

`product_runs` carries nullable `config_id`, `registry_version`, and `package_checksum` columns
that **no code path reads or writes**, and `ProductRun` has no matching fields. They are not dead
code and not an oversight: the columns exist so the write-time constraint below can exist before
any writer does. The later change that binds registered runs owns the model fields and the
behavior.

A run row is valid in exactly three states: all three binding columns NULL with a non-empty
`configuration_reference` (pre-registry and unregistered rows), or all three non-NULL (a
registered row). Partial binding is refused at write time — SQLite uses a pair of `BEFORE INSERT`
and `BEFORE UPDATE` triggers, PostgreSQL a `CHECK` constraint. Refusing the partial state removes
a whole family of later "which field wins" ambiguities. Both triggers must be present for SQLite
enforcement to hold, so the existence check requires both before it skips recreation.

### Declaring adapter configuration capabilities

Every adapter accepted by the registry needs an
`AdapterConfigurationCapabilities` record. The declaration is connection-free: it names
the adapter's allowed roles, closed set of allowed settings, credential-bearing setting
paths, qualified destination write operations, schema-validation availability, and an
optional bounded configuration validator. Credential paths must be inside the allowed
setting set. It must never contain clients, connections, credentials, or orchestration
objects.

Bundled declarations live in `infrahub_sync.configuration.capabilities`. Package
validation refuses adapters without a bundled declaration because Sync cannot prove
which of their settings are credentials. A future adapter-extension and registration
surface can contribute the same record through plugin metadata; it must preserve
contract version 1 and the rules for credential settings above.

The version-1 envelope refuses `adapters_path` and per-role `adapter` overrides. Those
fields select machine-local or arbitrary adapter code whose credential-bearing settings
cannot be proven by a bundled capability declaration. Because they are always absent from
an accepted package, they are excluded from the declared content the checksum covers.
Adapter names match the registered declaration exactly: a case variant is refused rather
than folded, because the name is hashed as declared and every consumer resolves it
verbatim. V3 keeps sync data in memory and refuses every non-null `store` block,
including Redis. Remove the block before registration. For an already registered
package, register a new version without the block and use that version for new runs.

Registered version-1 packages also refuse adapter settings
outside each bundled declaration, including custom HTTP headers and request parameters
on Prometheus, GenericRESTAPI, and its PeeringManager subclass. Declared URLs, base URLs,
and endpoint paths cannot contain user information, query parameters, or fragments.
Declared `url` and `base_url` must be absolute `http` or `https` URLs; `api_endpoint` and
`endpoint` must be relative paths carrying neither scheme nor authority.
GenericRESTAPI and PeeringManager packages cannot select alternate URL or credential
environment variables; their schema-mapping request endpoints must be relative paths
without authority, user information, queries, or fragments. Name a stored credential in the
block instead. These restrictions apply to the registered
package contract without changing the legacy `SyncConfig` runtime surface.

The bundled `examples/` remain legacy filesystem `SyncConfig` inputs, not package
declarations ready for registration. Before registration, remove credential-bearing values
and placeholders from `settings` and name a stored credential in each block. Convert non-secret settings and
placeholders, such as ACI `url` and `verify`, to valid declared literal values or another
mechanism supported by a future contract. Explicitly remove fields that legacy `SyncConfig`
ignored, or deliberately model their behavior in a later contract version; do not silently
carry them forward. For example, legacy parsing drops the top-level `description` in
`examples/aci_to_infrahub/config.yml`; version 1 does not add an inert field to retain it.

The declaration is intentionally narrower than the complete adapter conformance profile.
It provides the durable seam configuration validation needs without claiming that every
adapter behavior has been qualified.
