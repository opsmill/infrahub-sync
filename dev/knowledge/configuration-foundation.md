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

## Declaring adapter configuration capabilities

Every adapter accepted by the registry needs an
`AdapterConfigurationCapabilities` record. The declaration is connection-free: it names
the adapter's allowed roles, credential-bearing setting paths, qualified destination
write operations, schema-validation availability, and an optional bounded configuration
validator. It must never contain clients, connections, credentials, or orchestration
objects.

Bundled declarations live in `infrahub_sync.configuration.capabilities`. Package
validation refuses adapters without a bundled declaration because Sync cannot prove
which of their settings are credentials. A future adapter-extension and registration
surface can contribute the same record through plugin metadata; it must preserve
contract version 1 and the exact credential-reference rules.

The version-1 envelope refuses `adapters_path` and per-role `adapter` overrides. Those
fields select machine-local or arbitrary adapter code whose credential-bearing settings
cannot be proven by a bundled capability declaration. Redis accepts only its declared
store settings; its URLs, usernames, and passwords follow the same credential-reference
rule as adapter settings. Version 1 also refuses Prometheus custom headers because their
arbitrary values cannot be proven free of inline credentials; use the declared token,
username, and password reference settings instead.

The declaration is intentionally narrower than the complete adapter conformance profile.
It provides the durable seam configuration validation needs without claiming that every
adapter behavior has been qualified.
