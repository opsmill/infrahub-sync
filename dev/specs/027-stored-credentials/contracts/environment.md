# Environment contract

## Added

| Variable | Process | Rules |
| --- | --- | --- |
| `INFRAHUB_SYNC_ENCRYPTION_KEY` | `sync-api` only | Required. 32 bytes, base64-encoded. `sync-api` refuses to start without it or with another length, and names the variable. Changing it is not supported; losing it means re-entering every credential. |
| `INFRAHUB_SYNC_WORKER_TOKEN` | `sync-api` and every `sync-worker` | Required on both. The same length rule as `INFRAHUB_SYNC_ADMIN_ACCOUNT_TOKEN`, no `ifs_pat_` or `ifs_ses_` prefix, and not equal to the `admin` token. `sync-api` applies the `worker` account's token from it at each start; each worker sends it to the worker route. |
| `INFRAHUB_SYNC_API_URL` | every `sync-worker` | Required. The address of the Sync API, for example `http://sync-api:8000`. `https://` is accepted; the worker trusts the certificates in `INFRAHUB_SYNC_CA_DIRECTORY`. |

Generate the key with `python -c "import base64,os;print(base64.b64encode(os.urandom(32)).decode())"`.

## Removed

- Every `INFRAHUB_SYNC_CREDENTIAL_*` variable, on `sync-api` and on `sync-worker`.

## Files

- `docker-compose.yml`, `development/docker-compose.yml`,
  `infrahub_sync/testcontainers/docker-compose.yml`: the added variables, the removed ones.
- Helm chart (`opsmill/infrahub-helm`, `charts/infrahub-sync`): `credentials.encryptionKey` and
  `credentials.workerToken` in the credentials Secret; `credentials.infrahubApiToken`,
  `worker.credentials` and `worker.credentialsExistingSecrets` removed. Separate pull request.
