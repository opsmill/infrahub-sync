# Configuration foundation

The v3 configuration foundation separates declared configuration from runtime secrets.
`infrahub_sync.configuration.ConfigurationPackage` is a strict versioned envelope. Its
checksum covers only declared JSON content, including credential references and
behavior-affecting package metadata. Generated code, local paths, registry identity, and
resolved secret values never enter the checksum.

Durable or public callers parse untrusted declarations with
`parse_configuration_package()`. This boundary converts Pydantic failures into locations
and reason codes without including rejected values. Calling Pydantic's low-level
`ConfigurationPackage.model_validate()` directly is reserved for already-trusted data.

Credential-bearing adapter settings use an exact reference node:

```yaml
token:
  $credential: netbox-token
```

The package declares what that name means without carrying its value:

```yaml
credentials:
  netbox-token:
    provider: env
    identifier: NETBOX_TOKEN
```

A `$credential` node is only accepted where a credential is actually resolved: the
credential setting paths of the adapter filling that role, and those of the declared
store. The same node anywhere else — a non-credential setting, a schema-mapping static
value — is refused, because nothing would resolve it and the adapter would receive the
node itself.

Exactly one function turns a declared setting path into a pointer: `_settings_pointer` in
`credentials.py`. The declared-content walk builds the same pointer incrementally through
the same component escaping. This is a contract, not a tidiness preference: the
allowed-location set and the walk must produce byte-identical strings, or a reference is
accepted by the per-path check and then refused by the walk as not credential-bearing.
Any further component that renders these pointers — registry persistence, an API
response, a CLI diff — calls that function rather than formatting its own.

## Registry persistence and identity

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

### Schema migration and dialect

Migration is additive: the store introspects the existing column set and emits
`ALTER TABLE ADD COLUMN` only for what is missing. There is no schema version table, no rebuild,
and no backfill. Construction is idempotent, so a process restart re-runs it safely.

The store selects its introspection and constraint statements from an explicit `dialect` argument
that each provider passes. Never infer the dialect from the placeholder string or by running a
statement to see whether it fails: the test suite's PostgreSQL profile is a SQLite file behind a
`%s`-to-`?` adapter, so a placeholder check misidentifies it, and provoking an error on a
connection mid-DDL aborts the transaction and discards uncommitted `CREATE TABLE` statements.

### The run-binding columns are inert on purpose

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

## Declaring adapter configuration capabilities

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
contract version 1 and the exact credential-reference rules.

The version-1 envelope refuses `adapters_path` and per-role `adapter` overrides. Those
fields select machine-local or arbitrary adapter code whose credential-bearing settings
cannot be proven by a bundled capability declaration. Because they are always absent from
an accepted package, they are excluded from the declared content the checksum covers.
Adapter names match the registered declaration exactly: a case variant is refused rather
than folded, because the name is hashed as declared and every consumer resolves it
verbatim. Redis accepts only its declared
store settings; its URLs, usernames, and passwords follow the same credential-reference
rule as adapter settings. Registered version-1 packages also refuse adapter settings
outside each bundled declaration, including custom HTTP headers and request parameters
on Prometheus, GenericRESTAPI, and its PeeringManager subclass. Declared URLs, base URLs,
and endpoint paths cannot contain user information, query parameters, or fragments.
Declared `url` and `base_url` must be absolute `http` or `https` URLs; `api_endpoint` and
`endpoint` must be relative paths carrying neither scheme nor authority.
GenericRESTAPI and PeeringManager packages cannot select alternate URL or credential
environment variables; their schema-mapping request endpoints must be relative paths
without authority, user information, queries, or fragments. Use the declared
credential-reference settings instead. These restrictions apply to the registered
package contract without changing the legacy `SyncConfig` runtime surface.

The bundled `examples/` remain legacy filesystem `SyncConfig` inputs, not package
declarations ready for registration. Before registration, replace credential-bearing values
and placeholders with declared credential references. Convert non-secret settings and
placeholders, such as ACI `url` and `verify`, to valid declared literal values or another
mechanism supported by a future contract. Explicitly remove fields that legacy `SyncConfig`
ignored, or deliberately model their behavior in a later contract version; do not silently
carry them forward. For example, legacy parsing drops the top-level `description` in
`examples/aci_to_infrahub/config.yml`; version 1 does not add an inert field to retain it.

The declaration is intentionally narrower than the complete adapter conformance profile.
It provides the durable seam configuration validation needs without claiming that every
adapter behavior has been qualified.
