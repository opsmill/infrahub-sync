# Data model: an external object store, every adapter's credentials and a private CA

This specification adds no database table, no plan field and no API field. Its entities are
deployment settings and files.

## Object store settings

| Field | Source | Rule |
| --- | --- | --- |
| Endpoint URL | `INFRAHUB_SYNC_S3_ENDPOINT_URL` | absolute `http` or `https` URL without a user name or password; default `http://object-store:9000` |
| Addressing style | `INFRAHUB_SYNC_S3_ADDRESSING_STYLE` | `path` or `virtual`; default `path` |
| Bucket, key prefix, region | existing settings | unchanged |
| Access key, secret key | existing settings | unchanged; also the bundled store's root credential when it runs |
| Bundled store | `INFRAHUB_SYNC_BUNDLED_OBJECT_STORE` | `1` or `0`; default `1` |

Rule across fields: when the endpoint host is not `object-store`, the bundled store must be left out
(`0`); otherwise bootstrap stops with `bundled-object-store-not-left-out`.

## Bootstrap check key

`<prefix>/.infrahub-sync/bootstrap-check/<uuid4>`. Written twice with `If-None-Match: *`, then
deleted. Never read by any other part of Sync. A key left behind after a crash between the write and
the delete holds no data and is overwritten by no other path, because each check uses a new UUID.

## Credential variable list

24 names, listed in [contracts/environment-variables.md](contracts/environment-variables.md). Each
name maps to one bundled adapter and one credential setting of that adapter's capability record.

## CA bundle

| Item | Value |
| --- | --- |
| Host directory | `INFRAHUB_SYNC_CA_CERTIFICATES`; unset means an empty named volume |
| Mount | `/etc/infrahub-sync/ca`, read-only, on `sync-bootstrap`, `sync-api` and `sync-worker` |
| Files read | `*.pem` and `*.crt` in the directory, not in subdirectories |
| Written bundle | `/tmp/infrahub-sync/infrahub-sync-ca-bundle.pem`, mode `0600`, `certifi` roots first |
| Variables set | `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`, `AWS_CA_BUNDLE`, `INFRAHUB_TLS_CA_FILE`, each only when unset |

State: the bundle is written once per process start and lives on tmpfs, so it is gone after a
container restart and written again at the next start.
