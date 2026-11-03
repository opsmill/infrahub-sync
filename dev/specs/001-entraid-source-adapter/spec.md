# Feature Specification: Microsoft Entra ID source adapter

**Feature Branch**: `015-entraid-source-adapter`

**Created**: 2026-11-03

**Status**: Draft

**Input**: User description: "Microsoft Entra ID source adapter for Infrahub Sync" (full brief: source-only adapter
named `entraid` on the V2 line that reads users, groups and group membership from Microsoft Entra ID through
Microsoft Graph, with filtered collections, direct or transitive membership, client-secret or default Azure
authentication, bounded retries, unit tests with mocked HTTP, an example, a documentation page and a changelog
fragment).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Sync users and groups from Entra ID into Infrahub (Priority: P1)

A network or platform team keeps identities in Microsoft Entra ID. They write an Infrahub Sync configuration with
`entraid` as the source and Infrahub as the destination, map Entra ID users and groups to Infrahub kinds, run
`infrahub-sync diff` to view what would change, and then run `infrahub-sync sync` so that Infrahub holds the
same users and groups as Entra ID.

**Why this priority**: Without reading users and groups, the adapter delivers nothing. Every other story builds
on this one.

**Independent Test**: With a mocked Microsoft Graph that returns users and groups across several pages, load the
adapter with a configuration that maps `users` and `groups` to two kinds, and verify that every user and group is
loaded with the mapped attribute values.

**Acceptance Scenarios**:

1. **Given** a configuration whose `schema_mapping` entries name the bare resources `users` and `groups`, **When**
   the adapter loads, **Then** it reads every user and every group, follows every next-page link until the last
   page, and loads one object per record.
2. **Given** field mappings that name some Graph properties, **When** the adapter requests users or groups,
   **Then** the request asks only for the properties named by the mappings and filters, plus the object ID and
   any extra properties listed in the settings.
3. **Given** a tenant with no users, **When** the adapter loads, **Then** it loads zero objects and finishes
   without an error.
4. **Given** a sync configuration that names `entraid` as the destination, **When** Infrahub Sync starts,
   **Then** the run fails with a message that the Entra ID adapter can only be a source.

---

### User Story 2 - Split one Entra ID query into several Infrahub kinds (Priority: P1)

The team wants employees and service accounts as two different Infrahub kinds. They define one `users`
collection, and two `schema_mapping` entries that read it: one keeps users whose user principal name starts with
`svc-`, the other keeps the rest. Some identities can only be selected with a filter that Microsoft Graph
accepts only as an advanced query (for example on `onPremisesExtensionAttributes`), so they mark that collection
as an advanced query.

**Why this priority**: Filtering at the source and fanning one query out to several kinds is the main reason the
team picks this adapter over a generic REST adapter, and it controls how much of the tenant is read.

**Independent Test**: With a mocked Graph, configure one collection read by two `schema_mapping` entries with
different `filters`, and verify that Graph receives one request series for that collection and that each kind
receives only its own records.

**Acceptance Scenarios**:

1. **Given** a collection with a `filter`, **When** the adapter reads it, **Then** the Graph request carries
   that filter unchanged, and no consistency-level header or count parameter.
2. **Given** a collection with `advanced_query: true`, **When** the adapter reads it, **Then** every page request
   carries the `ConsistencyLevel: eventual` header and the count parameter.
3. **Given** two `schema_mapping` entries that read the same collection with different `filters`, **When** the
   adapter loads both kinds, **Then** Graph is queried once for that collection and each kind holds only the
   records that pass its own filters.
4. **Given** a `schema_mapping` entry with a `transforms` entry, **When** the adapter loads it, **Then** the
   transformed value is the one stored on the object.
5. **Given** a `schema_mapping` entry whose `mapping` names a collection that is not defined, **When** the
   adapter starts, **Then** it fails before any Graph request, and the message lists the valid names.

---

### User Story 3 - Keep group membership in Infrahub (Priority: P2)

The team wants each Infrahub group to point to its member users, so they can view who belongs to a group from
Infrahub. Some groups contain nested groups, and the team wants either only the direct members or all user
members through nested groups.

**Why this priority**: Membership is the second half of the goal ("which groups they belong to"), but users and
groups must exist first.

**Independent Test**: With a mocked Graph that returns groups with members of several types, verify that each
group's relationship holds only the users loaded in this run, for both membership modes.

**Acceptance Scenarios**:

1. **Given** the default `membership` setting, **When** the adapter loads groups whose mapping references the
   group members, **Then** it reads the direct members of each group and keeps only the members that are users.
2. **Given** `membership: transitive`, **When** the adapter loads groups, **Then** it reads the transitive
   members of each group and keeps only the members that are users.
3. **Given** a group whose members include users that this run did not load (filtered out, or in another
   collection), **When** the adapter loads the group, **Then** it drops those member references and logs one
   warning with the number of dropped references.

---

### User Story 4 - Authenticate from a laptop and from a CI pipeline (Priority: P2)

An engineer runs the sync from a laptop with an Azure CLI login, and the same configuration runs in a CI
pipeline with a managed or workload identity. Another team uses an app registration with a client secret held
in environment variables.

**Why this priority**: The adapter cannot read anything without authentication, but both modes reuse standard
Azure credential types, so the work is small and isolated.

**Independent Test**: Build the adapter's credential for each `auth` mode with controlled environment variables
and verify which credential type is built and with which tenant and client IDs, without contacting Azure.

**Acceptance Scenarios**:

1. **Given** `auth: client_secret` and the `ENTRAID_TENANT_ID`, `ENTRAID_CLIENT_ID` and `ENTRAID_CLIENT_SECRET`
   environment variables, **When** the adapter starts, **Then** it builds a client-secret credential from those
   values.
2. **Given** `auth: client_secret` with `tenant_id` and `client_id` in the settings and no matching environment
   variables, **When** the adapter starts, **Then** it uses the settings values for the tenant and client IDs.
3. **Given** `auth: client_secret` and a `client_secret` key in the settings, **When** the adapter starts,
   **Then** it fails with a message that the secret must come from `ENTRAID_CLIENT_SECRET`.
4. **Given** `auth: default`, **When** the adapter starts, **Then** it builds the default Azure credential chain
   and needs no tenant ID, client ID or secret.

---

### User Story 5 - Understand and recover from failures (Priority: P3)

When the app registration lacks a Graph permission, when Graph throttles the tenant, or when a setting is wrong,
the operator reads one message that names the cause and what to change.

**Why this priority**: These cases decide whether an operator can run the adapter unattended, but they do not
add data.

**Independent Test**: With a mocked Graph that returns 429, 503, 401 and 403 responses, and with invalid
settings, verify the retries and the error messages.

**Acceptance Scenarios**:

1. **Given** Graph answers HTTP 429 or 503 with a `Retry-After` header, **When** the adapter sends the request,
   **Then** it waits for the given time and sends it again, up to a bounded number of attempts, and then
   fails with an error that names the status.
2. **Given** Graph answers HTTP 401 or 403, **When** the adapter sends the request, **Then** it fails at once with
   an error that names the Graph application permissions the adapter needs (`User.Read.All`, `Group.Read.All` or
   `GroupMember.Read.All`).
3. **Given** an invalid setting (for example `page_size: 5000` or `membership: nested`), **When** the adapter
   starts, **Then** it fails with a message that names the setting and the allowed values.

### Edge Cases

- A Graph response has no `@odata.nextLink`: the adapter stops after that page.
- A page is empty but carries a `@odata.nextLink`: the adapter follows the link.
- `Retry-After` is missing or not a number: the adapter waits for a fixed fallback time, and never longer than a
  fixed maximum.
- A request does not answer within the timeout: the adapter fails with an error that names the request path,
  never the token.
- A collection is not read by any `schema_mapping` entry: the adapter does not query it.
- A group has a member that is a group, a device or a service principal: the member is not kept.
- The same user is a member of a group through two nested groups: the user appears once in that group's
  relationship.
- The groups kind is loaded before the users kind (wrong `order`): every member reference is dropped and counted
  in the warning, so the operator can detect the order problem.
- A Graph property mapped to a single-value attribute is a list: the adapter fails with a message asking for a
  transform.
- A collection is named `users` or `groups`: the adapter refuses it, because those names are reserved for the
  unfiltered resources.
- The credential cannot get a token (wrong tenant, wrong or expired client secret, no Azure CLI login or managed
  identity in `default` mode): the adapter fails with one message that names the `auth` mode and what to check,
  without the secret.
- A groups field maps `members` without a `reference`: the adapter fails when it starts and names the field.

## Requirements *(mandatory)*

### Functional Requirements

**Adapter shape**

- **FR-001**: The repository MUST provide a source adapter named `entraid` in `infrahub_sync/adapters/entraid.py`,
  with an adapter class `EntraidAdapter(DiffSyncMixin, Adapter)` and a model base class
  `EntraidModel(DiffSyncModelMixin, DiffSyncModel)`, following `dev/guides/adding-an-adapter.md`,
  `dev/guidelines/writing-an-adapter.md` and `dev/guidelines/testing-adapters.md`.
- **FR-002**: The adapter MUST log through `structlog`, MUST import the Graph SDK and Azure identity packages as
  optional dependencies (marked `# ty: ignore[unresolved-import]`), and MUST apply `filters` and `transforms` only
  through the shared `filter_records` and `transform_records` methods.
- **FR-003**: Using the adapter as a destination MUST fail with a clear error, and the model's create, update and
  delete operations MUST refuse to run.
- **FR-004**: The adapter MUST NOT use incremental extraction: `cursor_tier_for` stays `CursorTier.NONE`.

**Reading Microsoft Graph**

- **FR-005**: The adapter MUST read Graph through the Microsoft Graph SDK for Python and `azure-identity`. They
  MUST NOT be added to `pyproject.toml` or `uv.lock`; the documentation MUST give the install command.
- **FR-006**: The adapter MUST follow `@odata.nextLink` until the last page of every list request.
- **FR-007**: The adapter MUST request only the properties it needs, through `$select`, built from the field
  mappings and filters of every `schema_mapping` entry that reads the resource, plus the object ID, plus the extra
  properties listed per resource in the settings.
- **FR-008**: The adapter MUST retry HTTP 429 and 503 responses, waiting for the `Retry-After` time, with a
  bounded number of attempts, and MUST apply a request timeout.
- **FR-009**: The adapter MUST support a `page_size` setting between 1 and 999, sent as the page size of list
  requests, with a default of 999.

**Collections**

- **FR-010**: A `collections` setting MUST map a collection name to a resource (`users` or `groups`), an optional
  OData `filter` string, and an `advanced_query` flag (default false).
- **FR-011**: When `advanced_query` is true, every request of that collection MUST carry the
  `ConsistencyLevel: eventual` header and `$count=true`. When it is false, neither is sent.
- **FR-012**: The `mapping` of a `schema_mapping` entry MUST name either a collection or one of the bare resources
  `users` and `groups`, which read the resource without a filter.
- **FR-013**: Several `schema_mapping` entries MAY read the same collection, each with its own `filters` and
  `transforms`. Each collection MUST be read from Graph at most once per run.
- **FR-014**: A `schema_mapping` entry that names an unknown collection MUST make the adapter fail when it starts,
  before any Graph request, with a message that lists the valid names.

**Group membership**

- **FR-015**: Groups MUST be able to expose their user members as a relationship to synced users, through a field
  mapping with a `reference` to the users kind.
- **FR-016**: A `membership` setting MUST select `direct` (default, the group's members) or `transitive` (the
  group's transitive members). Only members that are users MUST be kept.
- **FR-017**: Members that point to users not loaded by this run MUST be dropped, and the adapter MUST log one
  warning with the number of dropped references.
- **FR-018**: The adapter MUST read group members only when a `schema_mapping` entry maps them.

**Authentication**

- **FR-019**: An `auth` setting MUST select `client_secret` or `default`.
- **FR-020**: With `client_secret`, the tenant ID and client ID MUST come from `ENTRAID_TENANT_ID` and
  `ENTRAID_CLIENT_ID`, falling back to the `tenant_id` and `client_id` settings. The secret MUST come only from
  `ENTRAID_CLIENT_SECRET`; a secret written in the settings MUST be refused.
- **FR-021**: With `default`, the adapter MUST use `azure.identity.DefaultAzureCredential`, which covers an Azure
  CLI login, a managed identity and a workload identity.
- **FR-022**: A `graph_url` setting MUST default to `https://graph.microsoft.com/v1.0`.
- **FR-023**: Tokens and secrets MUST NOT appear in logs, error messages or object representations.

**Errors**

- **FR-024**: HTTP 401 and 403 responses MUST raise a specific exception whose message names the Graph application
  permissions the adapter needs: `User.Read.All`, `Group.Read.All` or `GroupMember.Read.All`.
- **FR-025**: Invalid settings MUST fail when the adapter starts, with a message that names the setting.
- **FR-030**: A failure to get a token from the credential MUST raise a specific exception whose message names the
  `auth` mode and the environment variables or login to check, and never contains the secret.

**Tests, example, documentation**

- **FR-026**: Unit tests in `tests/adapters/test_entraid_*.py`, skipped with `pytest.importorskip("msgraph")` when
  the SDK is missing, MUST use mocked HTTP and no live tenant, and MUST cover: paging, `$select`, `$filter` with
  and without the advanced-query header, 429 retry, 401 and 403, empty results, two `schema_mapping` entries
  sharing one collection with different filters, a transform, direct and transitive membership, dropped member
  references, an unknown collection name, and both authentication modes (credential construction only).
- **FR-027**: An example `examples/entraid_to_infrahub/` MUST contain a `config.yml` and the generated code, in the
  same shape as the other examples, with made-up data only (`example.com`). It MUST show one `users` collection
  feeding two kinds through mapping filters (employees, and service accounts whose user principal name starts with
  `svc-`), one collection that uses `advanced_query`, and groups with members.
- **FR-028**: A page `docs/docs/adapters/entraid.mdx`, listed in `docs/sidebars.ts`, MUST cover the Graph
  application permissions, the environment variables, the settings, an example configuration, the eventual
  consistency of advanced queries, and the common errors.
- **FR-029**: A towncrier fragment `changelog/+entraid-adapter.added.md` MUST describe the new adapter.

### Key Entities

- **Collection**: a named Graph query, defined in settings. Attributes: name, resource (`users` or `groups`),
  optional filter, advanced-query flag. The bare names `users` and `groups` act as collections without a filter.
- **Graph record**: one user or group as Graph returns it, a set of Graph properties keyed by property name, with
  an object ID. Group records can carry the IDs of their user members.
- **Schema mapping entry**: links one Infrahub kind to one collection, with its own field mappings, filters and
  transforms.
- **Membership reference**: a link from a group object to a user object loaded in the same run.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An operator can go from an empty configuration to a working `diff` of users, groups and membership
  by following the documentation page and the example, without reading the adapter's source code.
- **SC-002**: For a configuration with N collections, a run sends exactly one list-request series per collection
  that a `schema_mapping` entry reads, whatever the number of entries that read it.
- **SC-003**: 100% of the test cases named in FR-026 pass with no network access and no Azure tenant.
- **SC-004**: Every failure named in this spec (permission, token acquisition, throttling exhausted, timeout,
  invalid setting, unknown collection, destination use) produces one message that names the cause, and no message contains a token
  or a secret.
- **SC-005**: The full unit test suite passes, and the new and changed files add no ruff, ty, pylint or rumdl
  findings compared with the `main` baseline.

## Assumptions

- The Infrahub schema used by the operator already has kinds for users and groups and a relationship from group
  to user. The example ships such a schema reference only through its `schema_mapping`; creating the Infrahub
  schema stays with the operator.
- Infrahub Sync computes the load order from `reference` fields, so a groups kind that references a users kind
  loads after it. An explicit `order` must also list the users kinds first; the documentation states this.
- A group's member relationship references one users kind. Members loaded into another users kind (for example
  service accounts) are counted as dropped for that relationship.
- Extra settings that the brief implies but does not name are kept small: `select` (extra properties per
  resource), `timeout` (seconds, default 60) and `max_retries` (attempts for 429 and 503, default 5). The number
  of concurrent member requests is a fixed internal value.
- The adapter disables the SDK's own retry handler and runs its own bounded retry loop, so that the number of
  attempts and the waiting time are predictable and testable.
- Collection names `users` and `groups` are reserved for the unfiltered resources.
- Non-goals, from the brief: delta queries and incremental extraction, devices and device owners, writing to
  Entra ID, changes to `pyproject.toml`, `uv.lock` or the Infrahub destination adapter, and the V3 line
  (`feature/v3-develop`).
- `/home/ubuntu/src/entraid-seed/` is a prototype whose request, paging, retry and settings logic is reused where
  sound. Its delta-query, device, static-token and scope features, its mock server references and its README are
  not carried over.
