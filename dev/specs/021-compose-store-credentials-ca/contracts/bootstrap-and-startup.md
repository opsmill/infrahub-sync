# Contract: bootstrap steps, failure names and start-up log lines

## `sync-bootstrap` steps, in order

1. Install the CA bundle (`install_custom_ca_bundle`, research R4).
2. Build the S3 client with `build_s3_client` (research R1): check the endpoint, the region and the
   addressing style. No S3 request is sent before this step passes.
3. Check that the bundled store and the endpoint agree (research R1, item 4): the bundled store is
   left out when the endpoint names another host, and the endpoint is set when the bundled store
   is left out.
4. Bucket: `HeadBucket`, then `CreateBucket` only when the answer is 404 (research R2).
5. Create-only check: two create-only writes of one key, then delete it (research R2).
6. Work pool, deployment and product schema, unchanged.

## Failure names

Every bootstrap failure is reported as one fixed name and nothing else
(`infrahub_sync/service/bootstrap.py:55-77`). New names:

| Name | When | Text the operator views |
| --- | --- | --- |
| `object-store-endpoint-invalid` | the endpoint is not an absolute `http` or `https` URL, or carries a user name or password | `refused: object-store-endpoint-invalid`, then the fixed message below |
| `bundled-object-store-not-left-out` | the endpoint names a host other than `object-store` and `object-store` resolves | `refused: bundled-object-store-not-left-out` |
| `object-store-endpoint-not-set` | the endpoint names `object-store` and that name does not resolve | `refused: object-store-endpoint-not-set` |
| `object-store-access-refused` | `HeadBucket` answers 403 | `refused: object-store-access-refused` |
| `object-store-unsupported` | the second create-only write of the check key succeeds, or the store answers 501 | `refused: object-store-unsupported` |
| `object-store-setting-invalid` | the addressing style is unknown, or the S3 client refuses the region | `refused: object-store-setting-invalid` |

With `object-store-endpoint-invalid`, bootstrap also logs the fixed message of the check that
failed. An endpoint that carries a user name or a password logs the message of
`infrahub_sync/service/storage.py:198`:
`INFRAHUB_SYNC_S3_ENDPOINT_URL must be an absolute http or https URL without userinfo`. An unknown
addressing style stops it with
`INFRAHUB_SYNC_S3_ADDRESSING_STYLE must be path or virtual`. Neither message holds a value.

## Start-up log lines of `sync-bootstrap`, `sync-api` and `sync-worker`

| Case | Level | Fields |
| --- | --- | --- |
| CA directory with certificates | info | `certificates` (count), `bundle` (path of the written bundle) |
| CA directory exists, no certificate file | info | `directory` |
| A trust variable was already set | warning | `variable` (one of `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`, `AWS_CA_BUNDLE`, `INFRAHUB_TLS_CA_FILE`) |
| A file holds a private key | error, then exit | `file` (file name) |
| A file does not load as a certificate | error, then exit | `file`, `error` (the `ssl` error class name) |

No line holds a certificate, a key, a credential value or the value of a trust variable.
