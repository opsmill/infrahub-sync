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
