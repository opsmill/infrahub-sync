# Research: Stored credentials managed through the Sync API

**Date**: 2026-11-20 | **Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)

Each entry states a decision, the reason and the alternatives. File and line references are to the
branch `027-stored-credentials`, created from `feature/v3-develop` at commit `5c22b693`. The comparison
with Infrahub is from the idea brief, which checked `../infrahub` on branch `develop`, commit
`0dcbe157dd`.

## What the code does today

- **A package declares its credentials in three places.**
    - Each credential setting holds a reference `{"$credential": <name>}`
      (`CredentialReferenceNode`, `infrahub_sync/configuration/models.py:517`).
    - A `credentials` block maps each name to `{provider, identifier}` (`CredentialReference`,
      `models.py:505`, `ConfigurationPackage.credentials` at `:597`). The package checksum covers the
      block (`models.py:633-639`).
    - `env` is the only provider (`configuration/credentials.py:224`). It reads
      `INFRAHUB_SYNC_CREDENTIAL_*` from the process environment (`:200-221`).
- **Two processes resolve values.**
    - The worker resolves them in `_worker_execution_context` (`infrahub_sync/service/flow.py:897`)
      through `resolve_runtime_instance` (`configuration/runtime.py:33`). Apply, resume and verify do
      not resolve the source (`flow.py:941`). The scheduled branch review resolves the destination
      token separately (`flow.py:859-872`, `service/branch_review.py:171-177`).
    - The Sync API resolves the destination token to read the destination schema when it validates a
      package (`configuration/capabilities.py:195-232`). That is why `sync-api` also receives
      `INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN` (`docker-compose.yml:275`).
- **Each adapter already declares its credential settings** in `credential_setting_paths`
  (`configuration/capabilities.py:736-815`). The Redis store declares its own
  (`configuration/credentials.py:132-136`), but registered runs refuse a `store` block
  (`configuration/runtime.py:45`).
- **Workers publish the names of their credential variables** to `worker_environments`
  (`service/worker.py:382-437`, `product_store/store.py:180`). Validation reports
  `credential-not-set-in-worker` and `worker-environment-unknown` from it
  (`product_store/configs.py:827-857`). ADR 0039 records this.
- **Conversion writes the `credentials` block** with `provider: env`
  (`configuration/conversion.py:144-161`, `:747-780`, `:481`).
- **The product store has no migrations.** It creates tables with `CREATE TABLE IF NOT EXISTS`, and a
  new column on an existing table reaches only a new database (`store.py:235-244`).
- **The worker does not call the Sync API.** It reads and writes PostgreSQL directly and imports
  `httpx` only for Prefect errors (`service/worker.py:22`).
- **The word "credential" is already used** for account tokens and sessions
  (`service/credentials.py`, `ResolvedCredential` at `product_store/models.py:1000`).
- **A refused request body is not echoed.** The 422 handler answers a fixed message
  (`service/app.py:459-464`).
- **Only administrators register** a configuration or a version (`service/config_routes.py:484`,
  `:506`).

## R1. Where a package names a credential

**Decision**: Each adapter block takes an optional key `credential: <name>`, next to `adapter` and
`settings`. The package's `store` block is not covered: registered runs refuse it today
(`configuration/runtime.py:45`, `UnsupportedSyncStoreError`). The credential settings of that block (from `credential_setting_paths`)
are no longer accepted in `settings`; the worker fills them from the stored credential. The
`credentials` block and `{"$credential": …}` references are refused at parse time with a message that
names `credential: <name>`.

**Rationale**: One name per block matches the brief (decision 2). The checksum covers the block, so
pointing a block to another credential creates a new version, and changing a value does not.

**Alternatives**: Keep `$credential` on each setting and only change the provider: rejected, because the
brief removes it and it keeps one name per setting instead of per block.

## R2. Which stored field fills which setting

**Decision**: A table in `configuration/capabilities.py`, next to `credential_setting_paths`, maps each
adapter's settings to the three stored fields:

| Adapter | Setting ← stored field | Fields needed |
| --- | --- | --- |
| infrahub, netbox, nautobot | `token` ← `token` | `token` |
| aci | `username` ← `username`, `password` ← `password` | `username`, `password` |
| genericrestapi, peeringmanager | by `auth_method`: `token`, `x-auth-token`, `api-key`, `key` → `token`; `basic` → `username`, `password`; `none` → nothing | by method |
| prometheus | by `auth_method`: `bearer` → `token`; `basic` → `username`, `password`; `none` → nothing | by method |
| ipfabricsync | `auth` ← `token` | `token` |
| slurpitsync | `api_key` ← `token` | `token` |

The methods come from `conversion_authentication.py:30-65`, which already maps each v2 `auth_method` to
its settings. A block whose method needs no credential refuses `credential:` with a finding, so a
package does not name a credential it never uses.

**Rationale**: The brief keeps three fields and says the adapter takes the fields its method needs
(decision 8). IP Fabric and Slurpit hold a token under another setting name, so the map is per
adapter.

**Alternatives**: Free-form fields on a credential: rejected by the brief.

## R3. Encryption

**Decision**: AES-256-GCM from the `cryptography` package (`cryptography.hazmat.primitives.ciphers.aead.AESGCM`),
added to the `service` extra only. The key is 32 bytes, given base64-encoded in
`INFRAHUB_SYNC_ENCRYPTION_KEY` to the Sync API only. Each revision stores one ciphertext of the JSON
object of its fields, with a random 12-byte nonce. The associated data is
`infrahub-sync/credential/v1|<credential_id>|<revision>`, so a ciphertext copied to another row or
revision fails to decrypt. A key identifier (the first 8 bytes of SHA-256 of the key, hex) is stored
with each revision, so a wrong key is reported as "the stored value cannot be decrypted with the
current key" before any decryption is tried.

**Rationale**: The brief requires encryption at rest with a key only the API holds (decisions 5 and 7).
`cryptography` is already installed as an indirect dependency (`uv.lock:738`) and is the standard
library for this in Python. GCM binds the row through associated data, which Fernet cannot do.

**Alternatives**:

- Fernet: simpler, but has no associated data.
- PostgreSQL `pgcrypto`: puts the key in SQL statements and the database logs, and does not work on
  the SQLite store used by tests.
- A hand-written cipher on the standard library: rejected; the standard library has no authenticated
  cipher.

Changing the key is out of scope (spec Assumptions). The Sync API refuses to start without the key or
with a key that is not 32 bytes, and names the variable.

## R4. Worker authentication

**Decision**: `INFRAHUB_SYNC_WORKER_TOKEN` defines the `worker` account, in the same way as
`INFRAHUB_SYNC_ADMIN_ACCOUNT_TOKEN` defines the `admin` token (`service/serve.py:136-155`,
`auth.py:166`). At each start, `sync-api` applies an `accounts` row named `worker` with
`environment_managed = TRUE`, no password and not an administrator. The pair "named `worker` and
`environment_managed`" identifies it, so no column or table is added. If an account named `worker` that
is not environment-managed exists, `sync-api` refuses to start and names the conflict. Its token digest
goes to `account_tokens` as `environment_managed`.

The authenticator gains one rule: a principal whose account is the worker account passes only the
routes under `/api/worker/`, and every other route answers `403 forbidden`. The worker routes accept
only that principal and refuse every other account, including an administrator (FR-005).

The account cannot sign in: it has no password, and the sign-in check already uses a fixed hash for an
account without one (`account_actions.py:355-365`). Every account action already refuses an
`environment_managed` account (`account_actions.py:581`).

Every worker receives `INFRAHUB_SYNC_WORKER_TOKEN` and `INFRAHUB_SYNC_API_URL`.

The worker token follows the rules of the `admin` token (`service/settings.py:162-171`): a minimum
length, and no `ifs_pat_` or `ifs_ses_` prefix. `sync-api` also refuses to start when it equals the
`admin` token (critique E8).

**Rationale**: The brief copies Infrahub's shared `agent` account and Sync's `admin` account
(decision 6). Reusing the account and token tables keeps one resolver.

**Alternatives**: A separate static token checked outside the account tables: rejected, because it
duplicates the resolver and the audit of refusals.

## R5. The worker route and what it releases

**Decision**: `POST /api/worker/runs/{run_id}/credentials`, body `{"flow_run_id": "<uuid>"}`. The
Sync API releases values only when every check passes:

1. The caller is the worker account.
2. The run exists and a `prefect_executions` row for `(run_id, flow_run_id)` is claimed and not
   terminal. The worker claims its execution before it resolves settings (`flow.py:1941`).
3. The run's configuration version names the credentials. The route returns only the blocks the stage
   needs: apply, resume and verify get the destination only, as today (`flow.py:941`). Scheduled runs
   are `sync` stages and get both. No stage builds a `store` block (critique E2: registered runs refuse
   it at `configuration/runtime.py:45`).
4. Each named credential exists and has the fields the block needs (FR-008).

The answer holds, for each block, the credential name, revision and fields. The route writes one audit
event per release, with the names and revisions and no value. It takes no `Idempotency-Key` and writes no
`mutation_receipts` row, because a receipt stores the full answer for replay. It records each
`(run, flow_run_id, block, credential_id, credential name, revision)` in `run_credential_uses` in the
same transaction. The identifier keeps a revision unambiguous when a credential is deleted and created
again with the same name (critique E1). The Sync API logs `credential.released` with the run, the
stage, the names and the revisions through `structlog` (critique E12).

**Rationale**: FR-003, FR-004, FR-016. Checking the claimed execution proves the run is in progress
for this stage, not only for the run.

**Alternatives**: Release by `run_id` only: rejected, because a finished stage of a run that has a later
stage would still get values.

## R6. How the worker uses released values

**Decision**: A new module `infrahub_sync/service/credential_client.py` calls the worker route with
`httpx` (a base dependency).

- **Timeouts and retries (critique E3).** A 10-second timeout. Connection errors, timeouts and the
  answers `502`, `503` and `504` are retried three times, after 1, 2 and 4 seconds. Every other refusal
  is final at once. After the last attempt, the cause is `api-unreachable`.
- **Transport (critique E7).** `INFRAHUB_SYNC_API_URL` may be `https://`; the client trusts the
  certificates in `INFRAHUB_SYNC_CA_DIRECTORY`, as the rest of the worker does. Inside Compose the call
  is plain HTTP on the internal network, and the deployment documentation says so.
- **Redaction from the moment of release (critique E6).** The client adds every released value to the
  run's redaction list before it returns, so an error raised between the release and
  `collect_secret_values` (`flow.py:1166`) is redacted. The same rule applies in the Sync API: the
  decryption function adds each decrypted value to the request's redaction list before it returns it,
  and the schema read (`capabilities.py:195-232`) runs inside that list. A test raises an error that
  holds the value at each of these points and checks the log and the answer.

The rest of the module: `resolve_runtime_instance` takes the released values instead of a provider
and writes them into the block settings. The released values are added to the secret values that
`collect_secret_values` and the log bridge redact (`flow.py:1166`). A failure maps to a new
`CredentialUnavailableError` with a reason (`api-unreachable`, `worker-token-refused`,
`credential-missing`, `credential-field-missing`, `credential-undecryptable`) and becomes a run failure
cause before the source is read (FR-019, `service/failure_cause.py`). The scheduled branch review
reads the destination token from the same released values.

**Rationale**: One place resolves values; failure causes stay classified as today
(`failure_cause.py:261`).

## R7. Validation at registration and at run start

**Decision**: Registration checks that each named credential exists and has the fields the block needs
(R2), and reports `credential-not-found` or `credential-field-missing` as errors. The run routes
(`service.py:537`) run the same check before they start a run, and refuse with HTTP 422 and the same
codes. The worker route checks again (R5). The Sync API reads the destination schema with the
decrypted value in its own process (FR-004).

A version stored before this change that still has a `credentials` block or `$credential` references
cannot be parsed by the new models. The run routes refuse it with the code `credential-format-removed`
and a message that says to register a new version.

`credential-not-set-in-worker`, `worker-environment-unknown`, `publish_worker_environment`,
`held_credential_names` and `worker_credentials.py` are removed. The `worker_environments` table
statement is removed from `_SCHEMA`; an existing table stays unused and harmless, because the store
does not drop tables.

## R8. Deletion check

**Decision**: A credential is in use when the latest version of a configuration names it, or when the
version pinned by a schedule whose state is not `deleted` names it (`store.py:218`, schedules pin
`config_id` and `registry_version`). The link is stored in a new table
`configuration_version_credentials (config_id, registry_version, block, credential_name)`, written when a
version is registered, so the check is one query. The refusal is `409 credential-in-use` with the
configuration identifiers and schedule names in `details`.

A deletion and a registration that names the same credential run in transactions that lock the
credential row (`SELECT … FOR UPDATE` on PostgreSQL; SQLite serializes writes), so a registration
cannot pass its check while a deletion removes the credential (critique E4). The run-start check
(FR-008) covers any case that remains.

**Rationale**: FR-010. Reading every stored package on each deletion would parse every version.

## R9. Revisions recorded by a run

**Decision**: `run_credential_uses` holds one row per stage execution and block (R5). The run detail
route returns them as `credentials_used: [{stage, block, name, revision}]`. A plan and its apply
show their own rows, so a change between them is visible (spec Assumptions).

## R10. CLI and web interface

**Decision**:

- CLI group `infrahub-sync credentials` with `list`, `show NAME`, `create NAME`, `set NAME`, `delete
  NAME`. Values are read from a prompt or from `--token-stdin` / `--password-stdin`, never from an
  argument, as `login` does (`cli_accounts.py:120-147`). `--username` is an argument, because a
  username is not a secret. `delete` asks for confirmation.
- Web interface: a "Credentials" page listed in the navigation for every signed-in account; create,
  change and delete appear only for an administrator, as the Accounts page gates its actions
  (`frontend/src/routes/accounts.tsx:55-137`). Value inputs are password inputs and are cleared after
  each request.
- The drop area offer of journey P3 depends on spec 025 (spec Assumptions).

## R11. Removing `env` across the repository

**Decision**: One delivery step removes the provider, the `credentials` block, the worker variable
publishing and the variables in deployment files, because the tests cannot pass between them. A shared
test helper creates stored credentials through the store (unit tests) or the API (Compose, development
stack, testcontainers and the nightly script). `tasks/dev.py` creates the development credentials
through the API after the stack starts, instead of setting `INFRAHUB_SYNC_CREDENTIAL_*` variables.

## R12. Documentation, decision records, constitution

- **ADR 0054** "The Sync API stores credentials and releases them to the worker account" supersedes
  ADR 0039. ADR 0026 and ADR 0040 get a "Changed by 0054" note. ADR 0054 also records:
    - **Rollback (critique E5).** An earlier build ignores the new tables, needs the
      `INFRAHUB_SYNC_CREDENTIAL_*` variables again, and cannot run a version that names a stored
      credential.
    - **A later external store (critique P3).** Because the Sync API resolves and the worker only
      receives, a provider such as Vault can be added in the Sync API with no change on the worker.
    - **The validate route (critique E9).** Any signed-in account can validate a registered version
      with a branch, which sends the destination token to the address an administrator registered.
- **Messages (critique P4, P5).** A refusal for a missing credential names the command
  `infrahub-sync credentials create <name>` and says that an administrator runs it.
- **Pages to change**: `configuration-package.mdx`, `compose-deployment.mdx`,
  `kubernetes-deployment.mdx`, `v2-to-v3.mdx`, `reference/durable-product-records.mdx`,
  `reference/sync-http-api.mdx`, `guides/accounts.mdx`, `development-stack.mdx`, `running-a-sync.mdx`
  and each `docs/docs/adapters/*.mdx`. New guide `docs/docs/guides/credentials.mdx`. CLI reference
  through `uv run invoke docs.generate`.
- **Constitution 1.3.0** (MINOR, materially expanded guidance): Principle VI and the security
  requirements allow values encrypted in the Sync database and released only through the worker
  route. `AGENTS.md` "Configuration and Examples" changes the same line.
- **Helm**: a separate pull request in `opsmill/infrahub-helm` (`charts/infrahub-sync/values.yaml`
  `credentials.infrahubApiToken` at line 56 and `worker.credentials` at line 203).
