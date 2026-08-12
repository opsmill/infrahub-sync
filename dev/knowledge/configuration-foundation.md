# Configuration foundation

The v3 configuration foundation separates declared configuration from runtime secrets.
`infrahub_sync.configuration.ConfigurationPackage` is a strict versioned envelope. Its
checksum covers only declared JSON content, including credential references and
behavior-affecting package metadata. Generated code, local paths, registry identity, and
resolved secret values never enter the checksum.

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

Bundled declarations live in `infrahub_sync.configuration.capabilities`. A custom adapter
without a declaration is refused by registration because Sync cannot prove which of its
settings are credentials. A future adapter-extension surface can contribute the same
record through plugin metadata; it must preserve contract version 1 and the exact
credential-reference rules.

The declaration is intentionally narrower than the complete adapter conformance profile.
It provides the durable seam configuration validation needs without claiming that every
adapter behavior has been qualified.
