# Contract: Object store

## Two modes

| Mode | `.env` | Containers | Endpoint |
| --- | --- | --- | --- |
| Bundled (default) | `INFRAHUB_SYNC_BUNDLED_OBJECT_STORE` unset or `1`; no endpoint setting | `object-store-init`, `object-store` from `cgr.dev/chainguard/minio:latest-dev@sha256:d7c906993247627c19f37fc1fa302c34cf2d209ae0e7dc7d52fb0be6ac2849ba` | `http://object-store:9000` |
| External | `INFRAHUB_SYNC_BUNDLED_OBJECT_STORE=0`, `INFRAHUB_SYNC_S3_ENDPOINT_URL` and the other settings below | none for the object store | the team's store |

Compose cannot tie the two settings together. An external endpoint set without
`INFRAHUB_SYNC_BUNDLED_OBJECT_STORE=0` would start an unused MinIO with the external store's
credential as its root credential. The documentation tells the operator to set both, and
`sync-bootstrap` stops with family `bundled-object-store-not-left-out` when the endpoint host is
not `object-store` while the name `object-store` resolves on the Compose network, so the
deployment does not start in that state and the operator corrects `.env`.

## Settings

| Setting | Required for external | Default | Rule |
| --- | --- | --- | --- |
| `INFRAHUB_SYNC_S3_ENDPOINT_URL` | yes | `http://object-store:9000` | absolute `http` or `https` URL; a user name or password in it is refused with the fixed message "INFRAHUB_SYNC_S3_ENDPOINT_URL must be an absolute http or https URL without userinfo" (`storage.py:198`) |
| `INFRAHUB_SYNC_S3_BUCKET` | yes | `infrahub-sync` | the bucket may already exist |
| `INFRAHUB_SYNC_S3_PREFIX` | no | `infrahub-sync` | key prefix; leading and trailing `/` removed (`storage.py:219`) |
| `INFRAHUB_SYNC_S3_REGION` | yes | `us-east-1` | passed as the client region and, when a bucket is created outside `us-east-1`, as its location constraint |
| `INFRAHUB_SYNC_S3_ACCESS_KEY`, `INFRAHUB_SYNC_S3_SECRET_KEY` | yes | none (required) | Sync's credential for the bucket |
| `INFRAHUB_SYNC_S3_ADDRESSING_STYLE` | no | `path` | `path` or `virtual`; any other value stops the process with a fixed message that names the setting |
| `INFRAHUB_SYNC_CA_CERTIFICATES` | when the store's certificate comes from a private CA | unset | see [environment-variables.md](environment-variables.md) |

## Operations and behaviours Sync relies on

| Operation | Required behaviour |
| --- | --- |
| `PutObject` | single request, whole body; accepts botocore 1.43.83's default request checksums |
| `PutObject` with `If-None-Match: *` | refuses a write to an existing key with 412 `PreconditionFailed`; 409 `ConditionalRequestConflict` is retried |
| `GetObject` | returns the body; a missing key answers `NoSuchKey` with status 404 |
| `HeadObject` | returns `Content-Length`; a missing key answers status 404 |
| `CopyObject` | copies a key within the bucket |
| `DeleteObject` | deletes one key |
| `HeadBucket` | answers 2xx for an existing bucket, 404 for an absent one, 403 when refused |
| `CreateBucket` | only when `HeadBucket` answers 404 |
| Authentication | Signature Version 4 with an access key and a secret key |

Sync does not list objects, use multipart uploads, presign URLs, or rely on versioning or object
lock.

## Bootstrap results

| Situation | Result |
| --- | --- |
| Bucket exists | completes; no `CreateBucket` call |
| Bucket absent, credential may create it | creates it, completes |
| `HeadBucket`, `CreateBucket`, `PutObject` or `DeleteObject` refused (403) | stops with family `object-store-access-refused`; the check key is deleted |
| A request the store does not implement (501 or `NotImplemented`) | stops with family `object-store-unsupported`; the check key is deleted |
| Store unreachable, or another error (a 301 region redirect included) | stops with family `object-store-unavailable` (existing) |
| Second create-only write accepted | stops with family `object-store-unsupported`; the check key is deleted |
| External endpoint while the bundled store runs | stops with family `bundled-object-store-not-left-out`, before any object store request |
| The shared S3 client builder refuses a setting (endpoint, addressing style, blank region) | stops with family `object-store-setting-invalid`, followed by the builder's text, which names the setting only |

Each family is printed alone: no endpoint, bucket, key or credential value. The one addition is
`object-store-setting-invalid`, printed as `refused: object-store-setting-invalid: <setting> must
be ...`; the text names the setting and never its value. When a write of the check fails and the
delete fails too, the write's family is the one printed.

## Tested stores

OpsMill tests with the bundled MinIO build and with a MinIO instance from the same image that
runs outside the deployment's object store services, over HTTPS with a test CA, with both
addressing styles. No other product is named as supported.
