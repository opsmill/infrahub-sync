# Feature Specification: Stored credentials managed through the Sync API

**Feature Branch**: `027-stored-credentials`

**Created**: 2026-11-20

**Status**: Draft

**Input**: Idea brief "Stored credentials managed through the Sync API" (2026-11-20). Seed: rework the
credential system so that credentials are separate from the configuration packages. The administrator
manages them (create, list, view, change, delete) with the CLI and the web interface, both through the
Sync API. Credentials must work the same way whichever worker takes a run.

## Summary

**Problem.** Today each configuration package declares its own credentials in a `credentials` block that
points to `INFRAHUB_SYNC_CREDENTIAL_*` variables in each worker's environment. As a result:

- A v2 file cannot be registered as it is, because each package needs a `credentials` block.
- The same Infrahub token is declared again in each package, so changing it means changing every
  package.
- Changing a value needs shell access to the host and a worker restart.
- With several workers, a run can land on a worker that lacks the variable. Branch validation combines
  the variable names of all live workers, so it does not catch a variable that only some workers hold.

**What is needed.** The administrator creates a credential once, in the CLI or the web interface. Every
package that names it uses it. The next run, on any worker, uses the current value with no restart. Values
are encrypted in the Sync database, and workers receive them only through a dedicated Sync API route.

**Not decided yet.** Nothing that blocks the plan. Changing the encryption key is out of scope for this
version (see Assumptions).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Create a credential once and use it in several packages (Priority: P1)

The administrator creates the credential `infrahub-netbox-sync` through the web interface or the CLI,
registers a package whose destination names it, and starts a plan. Whichever worker takes the run uses
the value. The administrator needs no shell access to the host and restarts no worker.

**Why this priority**: Without this journey, no registered package can authenticate to its source or
destination once the `env` provider is removed. Every other journey depends on it.

**Independent Test**: Start two workers with no `INFRAHUB_SYNC_CREDENTIAL_*` variable. Create a credential
with the CLI, register a package that names it, and start several plans. Each plan reads the source and
the destination, and the value appears nowhere except in the response of the worker route that releases values.

**Acceptance Scenarios**:

1. **Given** a stored credential `infrahub-netbox-sync` and a registered package whose destination names
   `credential: infrahub-netbox-sync`, **When** the administrator starts a plan, **Then** whichever worker
   takes the run reads the destination with that value, and no value appears in the package, the run
   record, the logs or any API response other than the worker route that releases values.
2. **Given** a signed-in account that is not an administrator, **When** it lists credentials and views
   one, **Then** it views each credential's name, description, which fields are set and its revision,
   and no value.
3. **Given** a signed-in account that is not an administrator, **When** it tries to create, change or
   delete a credential, **Then** the request is refused.
4. **Given** an administrator, **When** it creates a credential, **Then** the response to the creation
   does not contain the value.

---

### User Story 2 - Change a value in one place (Priority: P2)

The administrator rotates a token that several packages use. The administrator changes the value once,
and the next run of each package uses the new value without a new package version.

**Why this priority**: This removes the most frequent manual task of today's design: editing every
package or every worker's environment to rotate one token.

**Independent Test**: Register 5 packages that name `netbox-lab`. Change its token once. Start a run of
each package and check the revision that each run records.

**Acceptance Scenarios**:

1. **Given** 5 packages that name `netbox-lab`, **When** the administrator changes its token, **Then** no
   new version is registered, the next run of each package uses the new value, and each run records the
   credential's revision.
2. **Given** a plan read with revision 3 of a credential, **When** the administrator changes the value and
   then applies the plan, **Then** the apply uses revision 4, and the run details show both revisions.

---

### User Story 3 - Register a converted v2 file (Priority: P3)

The administrator converts a v2 file to a v3 package. The converted package names one credential in each
adapter block that authenticates, and the conversion lists the credentials to create. When the
administrator registers the package before the credentials exist, Sync names the missing credentials.

**Why this priority**: Converting v2 files is how existing users move to v3. It is lower than P1 and P2
because a package can also be written by hand.

**Independent Test**: Convert the NetBox example. Check the result for `credential:` in each adapter block
and the list of credentials to create. Register it before creating them, and check that the refusal names
each missing credential.

**Acceptance Scenarios**:

1. **Given** a v2 file, **When** the administrator converts it, **Then** the result has no `credentials`
   block and no `$credential` reference, names `credential: <name>` in each adapter block that uses
   authentication, and lists the stored credentials to create with the fields each one needs.
2. **Given** a converted package whose adapter blocks name `credential: netbox-token`, and `netbox-token`
   does not exist yet, **When** an administrator registers it with the CLI, **Then** registration is
   refused, and the refusal names `netbox-token` and the fields it needs.
3. **Given** the same package, **When** an administrator drops it in the web interface's drop area,
   **Then** the drop area offers to create `netbox-token` before registration. An account that is not an
   administrator is told to ask an administrator to create it.

---

### User Story 4 - Delete a credential that nothing current uses (Priority: P3)

The administrator removes a credential that no package uses any more. Sync refuses the deletion while the
latest version of a configuration or a schedule still names it, and lists them.

**Why this priority**: Deletion keeps the list of credentials clean. Refusing it while something current
names the credential prevents a run from failing later.

**Independent Test**: Create a credential named by the latest version of one configuration and by one
schedule, try to delete it, then point both to another credential and delete it again.

**Acceptance Scenarios**:

1. **Given** a credential named by the latest version of configuration `netbox-to-infrahub`, **When** the
   administrator deletes it, **Then** the deletion is refused and the refusal lists
   `netbox-to-infrahub`.
2. **Given** a credential named by a schedule that is not deleted, **When** the administrator deletes it,
   **Then** the deletion is refused and the refusal lists the schedule.
3. **Given** a credential named only by an older version of a configuration, **When** the administrator
   deletes it, **Then** the deletion succeeds, and a later run of that older version is refused before it
   starts.

---

### Edge Cases

- **A value changes between plan and apply.** The apply uses the current value, and the run records
  both revisions. The apply is not refused.
- **A credential is deleted while an older version names it.** A run of that version is refused before it
  starts (FR-008).
- **A credential loses a field the adapter needs, after registration.** The run is refused before it
  starts (FR-008).
- **The Sync API is down when a worker takes a run.** The run cannot get its values and fails before it
  reads the source, with a cause that names the Sync API as unreachable.
- **The worker token changes.** All workers need the new token and a restart, as in Infrahub.
- **The worker token is missing or wrong on a worker.** Every run that worker takes fails before it reads
  the source, with a cause that names the worker token as refused. No value is released.
- **The encryption key is lost or replaced.** The Sync API cannot decrypt stored values. A run that needs
  one fails before it reads the source, with a cause that says the stored value cannot be decrypted with
  the current key. The administrator re-enters every credential.
- **The encryption key is not set when the Sync API starts.** The Sync API refuses to start and names the
  missing setting.
- **A package still uses a `credentials` block or `$credential` references.** Registration is refused,
  and the refusal says that adapter blocks name a stored credential with `credential: <name>`.
- **Two adapter blocks of one package name the same credential.** Both use it, and the run records it
  once.

## Requirements *(mandatory)*

The identifiers FR-001 to FR-012 and SC-001 to SC-005 keep the numbers of the idea brief.

### Functional Requirements

#### Managing credentials

- **FR-001**: Users MUST be able to create, list, view, change the value of, and delete a stored
  credential, with the CLI and with the web interface, both through the Sync API.
  - Verification: an API test runs every operation, and a CLI test runs every operation through the
    same routes.
- **FR-002**: The system MUST NOT return a value in any response, CLI output, web page or log, including
  the response to the creation. The only exception is the worker route that releases values.
  - Verification: create a credential with a known marker value, then search every response and log line
    for the marker.
- **FR-007**: The system MUST store values encrypted. A copy of the database without the key MUST NOT
  reveal any value.
  - Verification: create a credential with a known marker value, dump the database, and search the dump
    for the marker.
- **FR-010**: The system MUST refuse to delete a credential that the latest version of any configuration,
  or any schedule that is not deleted, names. The refusal MUST list those configurations and schedules.
  Older versions do not block deletion.
  - Verification: one test with a latest version that names the credential, one with a schedule, and one
    with only an older version, which is allowed.
- **FR-011**: A credential's name MUST NOT be changeable.
- **FR-012**: Every signed-in account MUST be able to list credentials and view one, without its value.
  Only administrators MUST be able to create, change and delete credentials.
  - Verification: a non-administrator token gets a list without values, and is refused on create, change
    and delete.
- **FR-013**: A stored credential MUST hold one of three sets of the fields `username`, `password` and
  `token`: a token only, a username and a password, or a username and a token. The system MUST refuse
  any other set, including all three fields together. Listing and viewing MUST show which fields are
  set, never their values.
- **FR-014**: Each change of a credential's value MUST create a new revision. The description MAY change
  without a new revision.
- **FR-015**: The credential checks MUST name one credential-management permission, held by
  administrators, so that a later version can move it to a role without changing the routes.

#### Running with stored credentials

- **FR-003**: A run MUST record the name and revision of each credential it used, never the value.
  - Verification: change a value between two runs and check that the two runs record different
    revisions.
- **FR-004**: The API MUST release a value only to the worker account, only for a run in progress, and
  only for credentials that the run's package version names.
  - Verification: request a value for a finished run, for another run, and for a credential the package
    does not name. Each request is refused.
  - The Sync API MAY also use a value inside its own process, in two cases. It MUST NOT return that value
    in a response.
    - It reads the destination schema to validate a package.
    - It reads the state of a schedule's earlier proposed changes for the worker. A scheduled start with
      branch review needs these states before its run exists, so no value can be released for it. The
      worker calls a second worker route, `POST /worker/schedules/{schedule_id}/proposed-change-states`,
      with the schedule identifier only. The Sync API does the read itself and returns only the state of
      each proposed change. It takes the destination address and the proposed-change identifiers from the
      stored version and the stored branch rows of the schedule, never from the request.
  - Verification: call the second route and check that the response holds states only; send a request
    body that names another address and check that the stored address is read.
- **FR-005**: Each worker route MUST refuse every account session and every account API token, including
  an administrator's. There are two worker routes: the route that releases values to a run in progress,
  and the route that returns the states of a schedule's earlier proposed changes (FR-004).
  - Verification: call each route with an administrator token and expect a refusal.
- **FR-006**: A worker MUST be able to execute any run with no credential variable in its own
  environment.
  - Verification: start two workers with no `INFRAHUB_SYNC_CREDENTIAL_*` variables and check that both
    complete runs.
- **FR-008**: Registration MUST refuse a package whose adapter block names a stored credential that does
  not exist, or one that is missing a field the adapter's authentication method needs. A run MUST be
  refused before it starts if that is still true then, because the credential may have changed after
  registration.
  - Verification: one test where the credential is missing, and one where the credential has only a
    `token` but the adapter needs a `username` and a `password`. Each test checks both registration and
    run start.
- **FR-016**: Every stage that connects to a source or a destination MUST get its values in the same way
  and MUST record the revisions it used. The stages are plan, verify, apply, sync, resume of an
  interrupted apply, and scheduled runs. "A run in progress" in FR-004 means that one of these stages of
  the run is running.
- **FR-017**: The environment MUST define a `worker` account and its token, in the same way as the
  `admin` account. Every worker MUST use that token. The account MUST NOT be able to sign in, MUST NOT be
  changeable through the API, and MUST be able to call only the worker routes.
- **FR-018**: The worker MUST keep released values out of logs, failure causes, run records and saved
  plans, with the same redaction that applies to credential values today.
- **FR-019**: When a stage cannot get its values, the run MUST fail before it reads the source, with a
  cause that names the reason: the Sync API is unreachable, the worker token is refused, the credential
  is missing or lacks a field, or the stored value cannot be decrypted. The cause MUST NOT contain a
  value.

#### Package format and conversion

- **FR-009**: Converting a v2 file MUST write `credential: <name>` for each adapter block that uses
  authentication, and MUST list the stored credentials to create.
  - Verification: convert the NetBox example and check that the result has no `credentials` block, has
    `credential:` in each adapter block, and lists the credentials to create.
- **FR-020**: Each adapter block (source or destination) MAY name one stored credential with
  `credential: <name>`. The adapter MUST take from it the fields its authentication method needs.
  Pointing a block to a different credential MUST require
  registering a new package version. Changing a value MUST NOT.
- **FR-021**: The `env` provider, the `credentials` block and `$credential` references MUST be removed,
  with no migration path. Registration MUST refuse a package that uses them. A run of a version that
  was registered before this change and still uses them MUST be refused before it starts, with a cause
  that says to register a new version that names stored credentials.
- **FR-022**: Workers MUST stop publishing the names of their credential variables, and branch validation
  MUST stop reporting `credential-not-set-in-worker` and `worker-environment-unknown`.
- **FR-023**: Every v3 package example in `examples/` MUST name stored credentials and MUST NOT contain a
  `credentials` block, an `env` reference or a value.

#### Deployment and governance

- **FR-024**: The Compose deployment and the Helm chart MUST take the encryption key and the worker token,
  and MUST no longer take `INFRAHUB_SYNC_CREDENTIAL_*` variables, `worker.credentials` or
  `credentials.infrahubApiToken`. The Helm chart is in the `opsmill/infrahub-helm` repository, so its
  change is a separate pull request there.
- **FR-025**: The deployment documentation MUST say that changing the encryption key is not supported in
  this version, and that losing it means re-entering every credential.
- **FR-026**: Principle VI of the constitution MUST be amended to allow values that are encrypted in the
  Sync database and released only through the worker route that releases values. The constitution version and the matching
  line of `AGENTS.md` MUST change with it.

### Key Entities

- **Stored credential (new).** It has a name that cannot be changed, a description, and one to three
  fields: `username`, `password` and `token`. Each change of value creates a new revision. Values are
  encrypted, and only the Sync API holds the key.
- **Credential revision (new).** The number that identifies one set of values of a credential. Runs
  refer to it; it never exposes the value.
- **Package and package version (existing, changed).** Each adapter block names at most one credential
  with `credential: <name>`. The `credentials` block and the `$credential` references are removed.
- **Worker account (new, defined by the environment).** It is defined in the same way as `admin`. All
  workers share its token. It can call only the two worker routes and cannot sign in. One route releases
  the values of a run in progress. The other returns the states of a schedule's earlier proposed changes,
  which the Sync API reads itself; it returns no value.
- **Run (existing, changed).** For each stage, it records the name and revision of each credential it
  used.
- **Worker environment record (existing, removed).** The table in which each worker publishes the names
  of its credential variables.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: On a new deployment, an administrator creates a credential, registers a package that names
  it, and starts a plan in under 5 minutes. They use only the web interface or only the CLI, with no
  shell access to the host and no worker restart.
- **SC-002**: Changing a token that 5 packages name takes 1 operation and registers 0 new versions. The
  next run of each of the 5 packages uses the new value.
- **SC-003**: With 3 workers running, every run whose credentials are valid reads its source and
  destination, whichever worker takes it. 0 runs fail because a worker lacks a credential.
- **SC-004**: A known marker value, stored as a credential and then used in a plan and an apply, appears
  0 times in any of these:
  - API responses, except the worker route that releases values
  - CLI output
  - web pages
  - logs
  - run records
  - a database dump
- **SC-005**: Every v2 example in `examples/` converts to a v3 package that has no credential values and
  no `env` references.

## Assumptions

- **V3 has not been released**, so removing `env` needs no migration path.
- **The Sync API is reachable from every worker.** This is true in Compose and Helm today.
- **Changing the encryption key is out of scope for this version.** Losing the key means the
  administrator re-enters every credential, and the deployment documentation says so.
- **A plan records the revisions it read, and an apply records its own.** The run details show both, so
  a reviewer can view that a value changed between plan and apply. Sync does not warn separately and
  does not refuse the apply.
- **The drop area of journey P3, scenario 3, depends on spec 025** (register a configuration package
  from the web interface), which is not merged on this branch's base. The CLI path of P3 does not depend
  on it. If spec 025 is not merged when this feature is implemented, the drop-area offer moves to spec
  025's follow-up.
- **The package's `store` block is not covered.** Registered runs refuse a `store` block today
  (`UnsupportedSyncStoreError`), so it needs no stored credential. If registered runs support it later,
  it names a credential in the same way as an adapter block.
- **The direct Prefect flow is not affected.** It runs v2 configuration files from a directory with the
  serving process's own environment variables, not registered packages, so it never used the `env`
  provider.
- **A credential is not bound to an address.** A package names a credential and declares the address
  of its source and destination, so a package sends the value to the address it declares. Only
  administrators can register a package or a version, and they can already change every credential,
  so this gives them no new power. A test checks that a non-administrator cannot register a package
  that names a credential.
- **A later version will replace the administrator / non-administrator split with roles**: managing
  accounts, managing credentials, and running syncs. FR-015 prepares for it.

## Out of Scope

- External secret stores such as Vault or Kubernetes Secrets. These are planned for a 3.x release, as
  providers that the Sync API resolves.
- An identity for each worker, and revoking a single worker.
- Worker pools with different credential access.
- Roles beyond administrator and non-administrator.
- Pinning a package to a specific revision of a credential.
- Changing the encryption key.
- Renaming a credential.

## Governance Gates Crossed

- **Database change.** It adds the credential and revision tables and the credential use on runs, and
  removes the worker environment table of ADR 0039.
- **API change.** New credential routes and a worker route; a package format change (`credential:
  <name>` in each adapter block, the `credentials` block removed); `env` references refused.
- **Authentication change.** A worker account defined by the environment, and its token, in Compose and
  Helm.
- **New dependency, possibly.** An encryption library. The choice belongs to the plan.
- **Decision records.**
  - ADR 0039 (workers publish the names of their credential variables) is superseded.
  - ADR 0026 (the Compose deployment takes credentials from the environment) changes: the list of
    `INFRAHUB_SYNC_CREDENTIAL_*` variables is replaced by the encryption key and the worker token.
  - ADR 0040 (a v2 configuration converts locally by v2 rules) changes, because conversion output
    changes.
- **Deployment change.** In the Helm chart, `worker.credentials` and `credentials.infrahubApiToken` are
  removed, and the key and the worker token are added. `docker-compose.yml` changes in the same way.
- **Constitution amendment.** Principle VI (FR-026).
