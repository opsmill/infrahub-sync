# 54. The Sync API stores credentials and releases them to the worker account

**Status**: Accepted
**Date**: 2026-11-21
**Source**: `dev/specs/027-stored-credentials/research.md` (R1 to R7, R11, R12),
`dev/specs/027-stored-credentials/spec.md` (FR-004, FR-005, FR-007, FR-017, FR-021),
`dev/specs/027-stored-credentials/data-model.md`,
`dev/specs/027-stored-credentials/contracts/http-api.md`

## Context

A registered package named each secret as a `$credential` reference, and its `credentials` block
named one `INFRAHUB_SYNC_CREDENTIAL_*` environment variable for each reference. The worker that
ran a stage read the value from its own environment.

That design had these costs:

- Every worker needed every variable. A worker that lacked one failed the runs it took.
  [ADR 0039](0039-workers-publish-the-names-of-their-credential-variables.md) added a table in
  which each worker published the names of its variables, so that validation could report a
  missing one before a run.
- A change of a token meant a change of the environment and a restart of every worker.
- The Compose deployment passed a fixed list of variable names to the worker
  ([ADR 0026](0026-the-compose-deployment-takes-an-external-store-credentials-and-ca-from-env.md)),
  so two sources of one adapter with different credentials could not both run.
- An administrator needed shell access to the host to add a credential.

V3 has not been released, so the format can change with no migration path.

## Decision

### Credentials are stored by the Sync API

- **A stored credential** has a name that cannot change, a description, and one of three sets of
  fields: `token`; `username` and `password`; `username` and `token`. Each change of value creates
  the next revision. A change of description keeps the revision.
- **A package names a credential, never a value.** Each adapter block takes an optional
  `credential: <name>` next to its adapter name and its `settings`. A table in
  `configuration/capabilities.py` says which stored field fills which setting of each adapter. The
  credential settings of a block are refused in `settings`.
- **The checksum of a version covers the name.** Pointing a block to another credential needs a
  new version. Changing a value does not.
- **Administrators create and change credentials** through the Sync API, with the CLI group
  `infrahub-sync credentials`. Every signed-in account can list them and view one. No answer of
  these routes holds a value, including the answer to a creation. The checks name one
  permission, `manage-credentials`, which administrators hold.

### Values are encrypted with a key that only the Sync API holds

- AES-256-GCM from the `cryptography` package, which the `service` extra declares.
- The key is 32 bytes, base64-encoded in `INFRAHUB_SYNC_ENCRYPTION_KEY`. Only `sync-api` receives
  it. `sync-api` refuses to start with a value that is not base64 or does not decode to 32 bytes.
- Without the variable, `sync-api` starts, logs a warning, and answers every credential change
  with `503 credential-key-not-configured`. The operator Compose file makes the variable
  required: Compose stops before any container starts when it is missing.
- Each revision stores one ciphertext of the JSON object of its fields, with a random 12-byte
  nonce. The associated data names the credential identifier and the revision, so a ciphertext
  copied to another row or revision does not decrypt.
- Each revision also stores a key identifier: the first 8 bytes of the SHA-256 digest of the
  key. A value stored under another key is reported as "cannot be decrypted with the current
  key" before any decryption is tried.
- Changing the key is not supported. An administrator who loses the key sets the value of every
  credential again.

### The environment defines a `worker` account

- `INFRAHUB_SYNC_WORKER_TOKEN` defines the token of an account named `worker`, in the way
  `INFRAHUB_SYNC_ADMIN_ACCOUNT_TOKEN` defines the token of `admin`
  ([ADR 0049](0049-sign-in-uses-local-accounts-and-the-environment-defines-admin.md)). `sync-api`
  applies the account and its token at each start. The account has no password, cannot sign in,
  is not an administrator, and cannot be changed through the API.
- The token follows the rules of the `admin` token, and `sync-api` refuses to start when the two
  are equal. Without the variable, `sync-api` starts, applies no `worker` account and logs that;
  no worker can then get a value. The operator Compose file makes the variable required.
- Every worker receives the same token and the address of the Sync API
  (`INFRAHUB_SYNC_API_URL`). There is no identity for each worker.
- The worker account can call only the routes under `/api/worker/`. Every other route answers
  `403 forbidden` to it. The worker routes refuse every other account, including an
  administrator.

### The first worker route releases the values of a run in progress

`POST /api/worker/runs/{run_id}/credentials`, with the flow run identifier of the stage. The Sync
API releases values only when:

1. The caller is the worker account.
2. A `prefect_executions` row for the run and the flow run is claimed and not finished. The
   worker claims its execution before it asks.
3. The stored version of the run names the credentials. The answer holds only the blocks that
   the stage needs: an apply, a resume and a verify get the destination only.
4. Each named credential exists and holds the fields that its block needs.

Each release writes the credential name and revision of each block to `run_credential_uses` and
one audit event, with no value. The route takes no `Idempotency-Key` and writes no mutation
receipt, because a receipt stores the whole answer for a replay.

The worker adds each released value to the redaction list of the run before it uses it. A stage
that cannot get its values fails before it reads the source, with a cause that names the reason.

### The second worker route returns the states of a schedule's earlier proposed changes

`POST /api/worker/schedules/{schedule_id}/proposed-change-states`.

A scheduled start with branch review reads the state of the schedule's earlier proposed changes
before it creates its run: an approved, open proposed change skips the start
([ADR 0052](0052-a-scheduled-write-to-a-branch-that-sync-creates-needs-no-approval.md)). The
first route releases values only to a claimed execution of a run, and at this point no run
exists. So the worker cannot get the destination token for this read.

The Sync API does the read itself:

- The request has no body. The Sync API takes the destination address and the credential name
  from the schedule's stored version, and the proposed-change identifiers from the schedule's
  stored branch rows. A worker cannot make the Sync API send the token to an address that the
  worker chooses.
- The answer holds only the state of each proposed change, by the run of its branch row. It
  holds no value, no address and no proposed-change identifier.
- The route writes no audit event and no `run_credential_uses` row, because no run exists and no
  value leaves the Sync API. Each read of Infrahub writes one log line with the schedule, the
  credential name and its revision.

This route makes the worker boundary wider: the worker account can now make the Sync API read
Infrahub with a stored token, for any schedule identifier it sends.

### The earlier format is removed

- The `env` provider, the `credentials` block and `$credential` references are removed.
  Registration and validation refuse a package that holds a `credentials` key, empty or not, or
  a `$credential` reference. The refusal names `credential: <name>`.
- The earlier build wrote an empty block, `"credentials": {}`, into every stored version, and
  the stored checksum covers it. A stored version with only that empty block uses nothing that
  was removed. Reading it ignores the block and checks the stored checksum against the content
  with the block, so the version keeps its checksum, its saved plans and its approvals.
- A stored version whose `credentials` block holds an entry, or which holds a `$credential`
  reference, is refused with `422 credential-format-removed` by the routes that start a stage
  (plan, sync, apply, verify, approve, resume), by validation, by the creation and the change
  of a schedule, and by the two worker routes. The routes that read the version, its runs, their
  results, artifacts and logs still answer.
- The earlier build needed a `$credential` reference for every secret. So every earlier version
  that used a secret is refused, and its owner registers the package again with `credential:`
  names. Registering the same file again creates a new version with another checksum.
- Workers no longer publish the names of their credential variables. The validation findings
  `credential-not-set-in-worker` and `worker-environment-unknown` are removed, and no code reads
  or writes `worker_environments`. This supersedes ADR 0039.
- The Compose files pass no `INFRAHUB_SYNC_CREDENTIAL_*` variable. This replaces the fixed list
  of adapter credential variables of ADR 0026; its decisions on the external object store and on
  CA certificates stand.
- `configs convert` writes `credential: <name>` in each block that uses authentication and lists
  the credentials to create. This replaces the references and the variable names of
  [ADR 0040](0040-a-v2-configuration-converts-locally-by-v2-rules.md); its other rules stand.

### Start-up replaces the check on mutation receipts

The credential routes write mutation receipts with the resource kind `credential`. The check on
`mutation_receipts.resource_kind` of an earlier build refuses that kind. This change adds no
column, so a database of an earlier build starts under this build, and with the earlier check
it would refuse every credential change.

- The check has a new name, `mutation_receipts_resource_kinds` (on SQLite, the triggers
  `mutation_receipts_resource_kinds_insert` and `mutation_receipts_resource_kinds_update`).
- At start-up, the store drops the check of an earlier build by its old name, only when the
  catalog holds it, and creates the new one.
- The receipt of a credential request stores a keyed fingerprint of the request (HMAC-SHA-256,
  with a key derived from the encryption key under a label) and an answer that holds no value.
  An unkeyed digest of a short password could be searched.

This is an exception to the rule that start-up changes no existing table.

## Consequences

- An administrator creates a credential once, and every worker can run every package that names
  it. A worker needs no credential variable and no restart when a value changes.
- A copy of the database without the key reveals no value.
- Every run needs the Sync API to be reachable from its worker. When it is not, the run fails
  before it reads the source.
- The worker's request and the released values travel over plain HTTP inside the Compose
  network. `INFRAHUB_SYNC_API_URL` accepts `https://`, and the worker trusts the certificates in
  `INFRAHUB_SYNC_CA_DIRECTORY`.
- All workers share one token. A change of the token needs a restart of `sync-api` and of every
  worker. One worker cannot be revoked alone.
- **Limit of the execution check.** Workers write `prefect_executions` with the same database
  password as `sync-api`. A compromised worker can create an execution row for any run and get
  the values of that run. The check keeps a correct worker from receiving values for a finished
  stage; it is not a boundary against a worker that is compromised.
- **A credential is not bound to an address.** A package sends a value to the address that the
  package declares. Only administrators register packages and versions, and they can already
  change every credential.
- **The validate route.** Any signed-in account can validate a registered version with a
  branch. The Sync API then reads the destination schema with the destination token, at the
  address that an administrator registered. The value does not leave the Sync API.
- **Rollback.** An earlier build ignores the new tables, needs the `INFRAHUB_SYNC_CREDENTIAL_*`
  variables again, and cannot run a version that names a stored credential.
- **Two limits of the receipt check.**
    - An earlier build that starts later on the same database adds its old check back. On
      PostgreSQL this probably fails once a credential receipt exists, because the old check
      refuses that row. This was not tested.
    - An earlier build and this build that run on one database at the same time can leave both
      checks in place. Credential changes are then refused until this build starts again and
      drops the old check.
- **Upgrade.** A team that registered versions with `$credential` references registers each
  package again and changes each schedule to the new version. The saved plans of a refused
  version cannot be approved or applied.
- An existing `worker_environments` table stays in the database, unused. The store does not
  drop tables.
- The direct Prefect flow does not change. It runs a v2 file outside a registered package, and
  its adapters still read the address, the token, the username and the password from the
  environment variables of the serving process, such as `NETBOX_TOKEN`. A registered run reads
  no such variable.
- The Helm chart is in another repository. It needs the encryption key and the worker token,
  and no longer needs the worker credential values.

## Alternatives considered

- **Keep `$credential` on each setting and change only the provider.** Rejected: it keeps one
  name for each setting instead of one for each block, and it keeps the `credentials` block.
- **Fernet.** Rejected: it has no associated data, so a ciphertext is not bound to its row.
- **PostgreSQL `pgcrypto`.** Rejected: it puts the key in SQL statements and in the database
  logs, and the SQLite store of the tests does not have it.
- **A static worker token checked outside the account tables.** Rejected: it adds a second
  resolver and a second place that records refusals.
- **Release values by run identifier only.** Rejected: a finished stage of a run that has a
  later stage would still get values.
- **For the read before a scheduled start: release values to the worker before a run exists.**
  Rejected: it removes the rule that a value is released only to a run in progress.
- **For the same read: move it after the claim of the run.** Rejected: the result of the read determines whether
  a run is created.
- **For the receipt check: edit the check under its old name.** Rejected: start-up finds a
  check by its name and keeps it, so a database of an earlier build would keep the check that
  refuses `credential`.
- **For the receipt check: refuse a database of an earlier build at start-up.** Not chosen: it
  removes both limits above, and it means that a team cannot keep its database across this
  change.
- **Refuse every stored version that holds a `credentials` key.** Rejected: every version of
  the earlier build holds an empty one, so every schedule and every saved plan of a version that
  used no secret would stop.
- **An external secret store, such as Vault.** Not in this version. Because the Sync API
  resolves a value and the worker only receives it, a provider can be added in the Sync API
  later with no change on the worker.
