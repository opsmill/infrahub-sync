# Research: Lineage of the data an import writes

**Feature**: [spec.md](./spec.md) | **Plan**: [plan.md](./plan.md) | **Date**: 2026-10-22

Path prefixes used below:

- `SDK/` is infrahub-sdk 1.23.2 as installed by `uv.lock:1149-1150`
  (`.venv/lib/python3.11/site-packages/infrahub_sdk/`).
- `../infrahub/backend/infrahub/` is shortened to `BE/` (checkout at `398ad1c66d`, 2026-10-20).
- `../infrahub/python_sdk/` is the SDK checkout (281 commits after the `v1.23.2` tag); it is used
  only for the changelog and for changes that are not released.
- Paths without a prefix are in this repository, on `feature/v3-develop`.

## R-1. Oldest Infrahub version a V3 import supports, and the plan before 1.7.0

**Findings**

- No code checks the Infrahub server version. Every version check in `infrahub_sync/` concerns the
  Sync HTTP API (`infrahub_sync/service/compatibility.py:14`, `infrahub_sync/client/client.py:525-550`).
  The destination is checked only through the schema fingerprint
  (`infrahub_sync/runtime_schema/projection.py:1`).
- The SDK range is `infrahub-sdk>=1.17,<2` (`pyproject.toml:18`, `:88`); the lock file pins
  1.23.2 (`uv.lock:1149-1150`).
- One Infrahub version is pinned for development and tests: 1.10.6
  (`development/docker-compose.infrahub.yml:254`, `development/preview.env`,
  `tests/compose/conftest.py:393`). CI has no matrix over Infrahub versions
  (`.github/workflows/workflow-tests.yml:49` varies only Python).
- The documentation says: "The measured destination is Infrahub 1.10.6 … Other Infrahub …
  versions are not measured, and they are not excluded"
  (`docs/docs/operations/supported-platforms-and-limits.mdx:28-37`).
- Infrahub records attribute and relationship `updated_by` and `updated_at` from 1.7.0 (spec,
  Assumptions). A query that selects these fields on an older server asks for fields its GraphQL
  schema does not have, so the server refuses the whole query. This is inferred from GraphQL
  validation rules; it was not run against an older server.
- The SDK reads the server version with `InfrahubInfo { version }` (`SDK/client.py:526-529`).

**Decision**

- The oldest supported Infrahub version stays not known: the repository neither enforces nor
  documents one, and this feature does not set one. The measured version stays 1.10.6.
- At the start of a plan, the Infrahub adapter reads the server version once with
  `client.get_version()`. On 1.7.0 or later it requests metadata (R-2). Before 1.7.0, or when the
  version cannot be parsed, it does not request metadata, and every proposed change shows
  "not recorded" for `updated_by` and `updated_at` (spec FR-007, Clarifications Q1). The plan
  continues in both cases.

**Alternatives considered**

- Request metadata always and retry without it after a `GraphQLError`: rejected, because it doubles
  the first read and hides other query errors behind the retry.
- Refuse Infrahub before 1.7.0: rejected by the product owner (Clarifications Q1 = A).

## R-2. Reading `updated_by` and `updated_at` without a second read

**Findings**

- The destination side of a plan reads each kind with
  `client.all(kind=…, include=identifier_and_attribute_fields(model), populate_store=True)`
  (`infrahub_sync/adapters/infrahub.py:815-817`), with the SDK default page size of 50 (the
  adapter's SDK configuration at `:699-711` does not change it). The incremental read uses
  `client.filters(…, prefetch_relationships=True, node_metadata__updated_at__after=…)` (`:771-776`).
  Cardinality-many relationships are read with `rel_manager.fetch()` (about `:1074`).
- `include_metadata` is a parameter of `client.all`, `client.filters` and `client.get`
  (`SDK/client.py:1124`, `:1240`, `:738`); it arrived in SDK 1.18.0
  (`../infrahub/python_sdk/CHANGELOG.md:202-207`).
- With `include_metadata=True`, the same query also selects:
  - on each attribute, `updated_at` and `updated_by { id display_label __typename }`
    (`SDK/node/attribute.py:212-215`, `SDK/node/constants.py:8`);
  - on each relationship edge, `relationship_metadata { updated_at updated_by {…} }`
    (`SDK/node/related_node.py:309-313`, `SDK/node/relationship.py:153-155`);
  - on each node, `node_metadata` (`SDK/node/node.py:698-699`).
- One boolean turns all three on; there is no attribute-only option (`SDK/node/node.py:1489`,
  `:1523`).
- The query is built once and run once for each page (`SDK/client.py:1284-1302`), so the number
  of requests does not change.
- Values are read on the SDK node: `node.<attribute>.updated_at` and `.updated_by`
  (`SDK/node/attribute.py:121-135`), and `related_node.get_relationship_metadata()`
  (`SDK/node/related_node.py:243-253`).
- Server cost, read from the code and not measured:
  - on the default and global branches, two extra properties are read from each attribute vertex
    in the same Cypher query (`BE/core/query/node.py:889-892`);
  - on other branches, one subquery for each attribute finds the latest edge
    (`BE/core/query/node.py:908-925`);
  - account details are resolved by a DataLoader: one `get_many` for the distinct accounts of a
    batch (`BE/graphql/loaders/account.py:100-121`), and none for the system user (`:126-127`).

**Decision**

- Pass `include_metadata=True` (only when R-1 allows it) to the existing reads at
  `infrahub_sync/adapters/infrahub.py:815-817` and `:771-776`, and to the read of
  cardinality-many relationships (`rel_manager.fetch()`).
- Binding condition (product owner, 2026-10-22): the plan must not show "not recorded" for a value
  Infrahub holds (FR-007). Task T001 finds a read that carries metadata for cardinality-many edges
  within the existing requests. If no such read exists, work on User Story 3 stops and an
  amendment of FR-007 goes to the product owner; the plan does not fall back to "not recorded".
- Keep the metadata out of the DiffSync models. The adapter records it in a separate map keyed by
  destination node id and field name. Putting it into model attributes would make every value
  that a person changed look different on every plan, and would break idempotency (constitution
  principle II).
- Cost on a 10,000-node plan: the number of requests stays 10,000 / 50 = 200 pages for each kind
  read, the same as today. Each response grows by the metadata fields, and the server does more
  work for each attribute. The size of the growth is not known. Tasks measure it with the
  existing benchmark runner (`docs/docs/development/guides/netbox-benchmark-runner.md`) on the
  pinned 1.10.6 stack, on the default branch and on another branch, and record the destination
  read time with and without metadata in the plan's notes. The sources set no threshold, so the
  result is information only and does not block the merge.

**Alternatives considered**

- A second query for metadata, only for nodes with proposed changes: rejected, because it adds
  requests and a second view of the data that may differ from the first.
- `property=True`: rejected, because it returns `source`, `owner` and `is_protected` but not
  `updated_by` (`SDK/node/attribute.py:204-210`).

## R-3. Learning the token's own account without permission to read accounts

**Findings**

- The GraphQL query `AccountProfile` returns the caller's own account
  (`BE/graphql/resolvers/resolver.py:32-50`, registered at `BE/graphql/manager.py:575`).
- It needs no permission on accounts:
  - the query analyzer records no node kind for it (`BE/graphql/analyzer.py:720-734`), so no
    `object:…:view` permission is requested
    (`BE/graphql/auth/query_permission_checker/object_permission_checker.py:39-50`);
  - `AccountManagerPermissionChecker` lets an operation through when it is only `AccountProfile`
    (`object_permission_checker.py:69`, `:104`).
- The SDK helper is `client.get_user()` (`SDK/client.py:540-542`, query at `SDK/queries.py:46-83`).
- The brief records that this was checked against a running Infrahub on 2026-10-20.
- Selecting `updated_by { display_label }` should not need permission on accounts either, because
  `updated_by` is not a schema relationship (`BE/graphql/analyzer.py:851-866`). This is read from
  the code and not tested; tasks test it with an account that cannot read accounts (SC-002).

**Decision**

- At the start of a plan and at the start of an apply, the Infrahub adapter calls
  `client.get_user()` once and uses the returned account id as the default source. The plan
  records this import account in the manifest's `lineage.import_account`. Apply compares it with
  its own import account and refuses before any write when they differ, naming both accounts
  (FR-012). The current
  lookup of a `CoreAccount` named after the source system
  (`infrahub_sync/adapters/infrahub.py:716-721`) is removed (FR-005).
- A named `{kind, name}` is looked up with one `client.get(kind=…, name__value=…)` for each named
  node, before the source system is read. `NodeNotFoundError` stops the run with a cause that names
  the setting, the kind and the name; there is no fallback (FR-004), replacing the warnings at
  `:727-731` and `:738-742`. Apply repeats the lookup and stops before any write (FR-011).
- The stop is logged with `structlog`, with the setting, kind and name as context and no secret
  (constitution, "Logging").

**Alternatives considered**

- Keep the lookup by the source system's name: rejected by FR-005.
- Ask the operator to name the import account in the package: rejected, because the token already
  identifies it, and a second setting could name a different account from the one that writes.

## R-4. Stored plan format and product store

**Findings**

- One proposed change is a `PlannedOperation` (`infrahub_sync/plan/models.py:105`). Its field
  set is closed (`extra="forbid"`, `:108`). It holds the desired source-side values only
  (`infrahub_sync/plan/derive.py:636`, `:653-657`); no current destination value is stored.
- A plan is `plan/operations.jsonl` plus `plan/manifest.json` (`infrahub_sync/plan/writer.py:42-43`,
  `:178`). `PlanManifest` allows extra fields (`infrahub_sync/plan/models.py:377-407`).
- The checksum is the SHA-256 of the canonical manifest, without `plan_checksum`, `run_id` and
  `created_at`, followed by the bytes of `operations.jsonl`
  (`infrahub_sync/plan/checksum.py:59-67`, `models.py:51`).
- Format rules (`dev/adr/0001-saved-plan-artifact-format.md:84-112`):
  - an additive manifest field that the checksum covers, and that is absent on older plans, needs
    no new format version;
  - a new `PlannedOperation` field needs a new format version, and apply accepts only the current
    version (`models.py:33-42`).
- The service publishes a review document, `PlanResource` (`infrahub_sync/client/models.py:341`,
  published by `infrahub_sync/service/flow.py:178-187`), and a checkpoint
  (`infrahub_sync/product_store/bundle.py:64`).
- The product store database keeps no plan content; plans are object-store artifacts that
  `artifact_refs` points to (`infrahub_sync/product_store/store.py:48-114`).

**Decision**

- The stored plan needs new data, and the product store database does not.
- Add a third plan file, `plan/last_changes.jsonl`, with one record for each proposed change to an
  attribute or a relationship (see [data-model.md](./data-model.md)).
- Add two additive manifest fields, both covered by the checksum and absent on older plans:
  - `last_changes`: the file's path, SHA-256 digest and record count;
  - `lineage`: the import account, the resolved source, the named owner (or none), and `protect`.
- `PlannedOperation` and `PLAN_FORMAT_VERSION` do not change. Plans made before this feature can
  still be reviewed; apply refuses them because they record no import account (FR-012, product
  owner decision of 2026-10-22).
- `PlanResource` gains two optional fields that carry the same records and the owner notice
  (FR-009). This changes the Sync API response (governance gate "API and plan format" in the
  spec); the field is optional, so older clients ignore it.
- Tasks check whether the checkpoint artifact (`infrahub_sync/product_store/bundle.py:64`) copies
  the whole plan directory or a fixed list of files, and add the new file if it uses a list.

**Alternatives considered**

- Add a `last_changes` field to `PlannedOperation` (format 4): rejected, because the data is for
  display only (D-2) and a new format version would add reader changes this feature does not need.
- Keep the data only in `PlanResource`: rejected, because `runs plan` reads the stored plan and
  would not show it.
- Leave the sidecar outside the checksum: rejected, because a reviewer would then approve a
  checksum that does not cover what they viewed.

## R-5. Telling a missing permission from a refused token

**Findings**

- SDK 1.23.2 raises `AuthenticationError` for both HTTP 401 and 403 (`SDK/client.py:1439-1446`).
  The exception holds only the message (`SDK/exceptions.py:178-181`), and the message of a 403
  contains the server's text. It is raised `from` the `httpx.HTTPStatusError`
  (`SDK/client.py:1446`), so `__cause__.response.status_code` still holds the status.
- The server's texts are `"You do not have the following permission: {permission}"` and
  `"You do not have one of the following permissions: …"` (`BE/permissions/manager.py:57-90`).
  The error is `PermissionDeniedError` with `HTTP_CODE = 403` (`BE/exceptions.py:344-350`).
- A denial inside a resolver comes back in the GraphQL `errors` list and the SDK raises
  `GraphQLError` (`SDK/client.py:1453-1462`).
- Today the run reports every `AuthenticationError` as "an authentication failure"
  (`infrahub_sync/potenda/__init__.py:220-238`), and configuration validation reports 401 and 403
  as `unauthorized` (`infrahub_sync/configuration/capabilities.py:258-264`).
- A typed error with the HTTP status is in the SDK checkout but not released
  (`../infrahub/python_sdk/changelog/+error-catalogue-envelope.changed.md`).

**Decision**

- Classify an `AuthenticationError` by the status of its `__cause__`: 403 is a missing permission,
  401 is a refused token. A 403 quotes the permission text after "You do not have … permission(s):"
  when present, and the whole server message otherwise. A `GraphQLError` whose message starts with
  "You do not have" is also a missing permission. An `AuthenticationError` without an HTTP cause
  is a refused token, which only a 401 path produces in 1.23.2. The classification lives in one
  function with unit tests for each case (FR-008).
- When the SDK releases the typed error, the function switches to it; this is a follow-up and not
  part of this feature.

**Alternatives considered**

- Classify on the message text alone: rejected, because a 403 with another message would be
  reported as a refused token, against SC-006.
- Read the HTTP status through a custom SDK requester: rejected, because it replaces SDK
  transport code when the exception chain already carries the status.

## R-6. Writing source, owner and protection

**Findings**

- `generate_payload_create` puts the same `source`, `owner` and `is_protected` on every attribute
  and every relationship edge, and leaves out `is_protected` when it is `None`
  (`SDK/schema/__init__.py:222-255`).
- Today every write passes `is_protected=True` (`infrahub_sync/adapters/infrahub.py:1336`), and
  an update is written through the same path with `node.save(allow_upsert=True)` (`:1364`).
- On the server, an attribute changes `is_protected`, `source` or `owner` only when the key is
  present and differs (`BE/core/attribute.py:729-738`). A relationship property is written only
  when it is not `None` (`BE/core/relationship/model.py:494-499`).
- No V3 write path allocates from a resource pool: no `from_pool` is used in `infrahub_sync/`. The
  server sets the pool as the source of an allocated edge (`BE/core/relationship/model.py:643`).
- Reading the SDK suggests that a node fetched with `include_metadata=True` and then saved with
  `do_full_update=True` could send `updated_at`, which the server's input types do not accept
  (`SDK/node/attribute.py:180-199`, `SDK/node/constants.py:4-5`). The write path builds fresh
  payloads and does not save fetched nodes, so it is not exposed; tasks add a test that keeps it so.

**Decision**

- `source`: the import account's id, or the named source's id, on every attribute and edge written.
- `owner`: only on a create, and on relationship edges that an update adds (peers that the
  destination node does not have yet). Never on attributes of an existing node (D-8, FR-002).
- `is_protected`: `True` when `protect` is on; otherwise `None`, so the key is not sent (FR-006).
- If a later feature allocates from a resource pool, that edge carries none of the three
  (Clarifications Q2). A unit test asserts the rule on the payload builder so the exception holds
  when allocation is added.

## R-7. Smallest permission set of the import account

**Findings**

- The spec relies on "the documented permissions" of the import account (SC-002, FR-010), but no
  source lists them yet.
- The run calls `InfrahubInfo` (`SDK/client.py:526-529`) and `AccountProfile`
  (`SDK/client.py:540-542`). The brief records that `AccountProfile` works without permission on
  accounts; for `InfrahubInfo` this is not known.

**Decision**

- Task T005 finds the smallest permission set on the pinned Infrahub 1.10.6, for the default
  branch and for another branch, by running plan and sync with an account that starts with no
  permission and gains one permission at a time. It includes the `InfrahubInfo` and
  `AccountProfile` queries and the read of each allowed named kind. The result is recorded here as
  a table, T019 tests against it, and T051 documents it.
- Result: not known yet.
