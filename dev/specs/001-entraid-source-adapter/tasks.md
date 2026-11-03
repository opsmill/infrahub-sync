---

description: "Task list for the Microsoft Entra ID source adapter"
---

# Tasks: Microsoft Entra ID source adapter

**Input**: Design documents from `specs/001-entraid-source-adapter/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/config-contract.md](contracts/config-contract.md),
[quickstart.md](quickstart.md)

**Tests**: Requested by the spec (FR-026). Each story phase lists its tests.

**Organization**: Grouped by user story. Most implementation lands in one file,
`infrahub_sync/adapters/entraid.py`, so tasks on that file are sequential.

## Rules for every task

- Read `plan.md` (Design section), `research.md` (R1 to R13) and `data-model.md` before writing code. The prototype
  in `/home/ubuntu/src/entraid-seed/` (`adapter.py`, `graph.py`, `settings.py`) shows working request, paging and
  settings code; reuse its logic, but not its delta, device, static-token or `scope` features, nor any reference to
  its mock server or README.
- Never run `uv sync` (it removes `msgraph-sdk` and `azure-identity`). If `uv run python -c "import msgraph"` fails,
  run `uv pip install msgraph-sdk azure-identity`.
- Do not modify `pyproject.toml`, `uv.lock` or `infrahub_sync/adapters/infrahub.py`.
- Use made-up data only (`example.com`, zero GUIDs). Never name a customer, company, person or tenant.
- `_ty_probe.py` at the repository root is an empty leftover from planning: never stage or commit it.
- Python 3.10 compatible (no `asyncio.TaskGroup`, no `except*`). `from __future__ import annotations`.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1 to US5)

---

## Phase 1: Setup

**Purpose**: Confirm the environment and the baseline.

- [ ] T001 Confirm the optional SDK is importable (`uv run python -c "import msgraph, msgraph_core, azure.identity.aio, kiota_http"`), reinstalling with `uv pip install msgraph-sdk azure-identity` if needed, and record the baseline: `uv run pytest -q` gives 210 passed, 3 skipped; `uv run invoke lint` findings are in `/home/ubuntu/src/overnight/lint-baseline.txt`. No file changes.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Settings, Graph client and model base class in `infrahub_sync/adapters/entraid.py`, plus the shared
test helpers. Every story depends on this phase.

- [ ] T002 Create `infrahub_sync/adapters/entraid.py` with: module docstring; `from __future__ import annotations`; standard imports; SDK imports grouped into as few statements as possible, each with `# ty: ignore[unresolved-import]  # pylint: disable=import-error` (`msgraph.GraphRequestAdapter`, `GraphServiceClient`, `msgraph.graph_request_adapter.options`, `ODataError`, `msgraph_core.GraphClientFactory`, `kiota_abstractions` `APIError` / `RequestConfiguration` / `RequestInformation`, `kiota_http.middleware.options.RetryHandlerOption`, `kiota_authentication_azure...AzureIdentityAuthenticationProvider`, `azure.identity.aio` `ClientSecretCredential` / `DefaultAzureCredential`, `azure.core.exceptions.ClientAuthenticationError`); `logger = logging.getLogger(__name__)` and the `structlog.wrap_logger` bound logger `log` of research R10; constants (resources `users`/`groups`, reserved names, `MEMBERS_KEY = "members"`, `USER_ODATA_TYPE = "#microsoft.graph.user"`, `REQUIRED_PERMISSIONS = ("User.Read.All", "Group.Read.All", "GroupMember.Read.All")`, `DEFAULT_GRAPH_URL`, `FALLBACK_RETRY_SECONDS = 5.0`, `MAX_RETRY_WAIT_SECONDS = 120.0`, `MEMBER_CONCURRENCY = 4`, env var names); `_sleep = asyncio.sleep` seam; exceptions `EntraidConfigError(ValueError)`, `EntraidGraphError(RuntimeError)` with `status`, `code`, `path` attributes, `EntraidPermissionError(EntraidGraphError)`, `EntraidAuthenticationError(EntraidGraphError)`, each with a docstring.
- [ ] T003 In `infrahub_sync/adapters/entraid.py`, add frozen dataclasses `Collection` and `EntraidSettings` exactly as in `data-model.md`, with `EntraidSettings.from_settings(raw: Mapping[str, Any] | None, environ: Mapping[str, str] | None = None)` (validation of every field, unknown keys refused with the allowed list, `client_secret` key refused with a message naming `ENTRAID_CLIENT_SECRET`, env vars win over `tenant_id`/`client_id`, missing tenant/client/secret for `auth: client_secret` named by their environment variables, `graph_url` must be `https://`, reserved collection names refused, collection keys validated), `client_secret` field with `repr=False`, properties `token_scope` and `graph_host` derived from `graph_url`, and `resolve_collection(name) -> Collection` raising `EntraidConfigError` that lists the valid names (sorted collection names plus `users`, `groups`).
- [ ] T004 In `infrahub_sync/adapters/entraid.py`, add `build_credential(settings: EntraidSettings) -> Any` returning `ClientSecretCredential(tenant_id, client_id, client_secret)` for `client_secret` and `DefaultAzureCredential()` for `default` (research R9).
- [ ] T005 In `infrahub_sync/adapters/entraid.py`, add `EntraidGraphClient` (async context manager; `__init__(settings, *, transport: httpx.AsyncBaseTransport | None = None, credential: Any | None = None)`): `httpx.AsyncClient(transport=transport, timeout=settings.timeout)` passed through `GraphClientFactory.create_with_default_middleware(client=..., options={**sdk_default_options, RetryHandlerOption.get_key(): RetryHandlerOption(should_retry=False)})` (use the returned client); `AzureIdentityAuthenticationProvider(credential, scopes=[settings.token_scope], allowed_hosts=[settings.graph_host])`; `GraphRequestAdapter` with `base_url = settings.graph_url`; `GraphServiceClient`; counters `requests`, `retries`, `member_requests`; `__aexit__` closes the HTTP client and the credential (when it has `close`). Add `_send(info: RequestInformation) -> dict[str, Any]` implementing the error mapping table of `plan.md` (retry 429/503 up to `max_retries` waiting `min(Retry-After or FALLBACK, MAX_RETRY_WAIT)` through `_sleep`, one WARNING log per wait; 401/403 → `EntraidPermissionError` naming `REQUIRED_PERMISSIONS` and the status; other `APIError` → `EntraidGraphError` with status and Graph error code/message; `httpx.TimeoutException` → `EntraidGraphError` naming path and timeout; `ClientAuthenticationError` → `EntraidAuthenticationError` naming the `auth` mode and, for `client_secret`, the three `ENTRAID_*` variables, for `default`, the Azure CLI login / managed identity / workload identity). Messages carry the URL path and query only, never headers or tokens.
- [ ] T006 In `infrahub_sync/adapters/entraid.py`, add `EntraidGraphClient.list_collection(collection: Collection, select: Sequence[str]) -> list[dict[str, Any]]`: builder `sdk.users` or `sdk.groups`; first request with the builder's `...GetQueryParameters(select=list(select), top=page_size, filter=collection.filter or None, count=True if advanced else None)`; when `advanced_query`, add header `ConsistencyLevel: eventual` to the first and every next-page request (research R4); follow `@odata.nextLink` through `builder.with_url(link).to_get_request_information()` until absent, including empty pages; return the concatenated `value` items. Add `user_member_ids(group_id: str, *, transitive: bool) -> list[str]`: builder `sdk.groups.by_group_id(group_id).members` or `.transitive_members`, `select=["id"]`, `top=page_size`, paging as above, keep items whose `@odata.type == USER_ODATA_TYPE`, return sorted unique IDs as `str`; increment `member_requests`.
- [ ] T007 In `infrahub_sync/adapters/entraid.py`, add `EntraidModel(DiffSyncModelMixin, DiffSyncModel)` with docstring; `create`, `update` and `delete` raise `NotImplementedError("The Entra ID adapter is read-only: ...")`.
- [ ] T008 Create `tests/adapters/entraid_fakes.py` (not collected as tests; it must start with `pytest.importorskip("msgraph")` before importing the adapter): `FakeCredential` (async `get_token(*scopes, **kwargs)` returning `azure.core.credentials.AccessToken("fake-token", far future)`, async `close`; option to raise `ClientAuthenticationError`); `GraphRouter` that serves an `httpx.MockTransport`, records every `httpx.Request`, and answers by path from registered page lists (support `@odata.nextLink`, queued error responses with status, headers and an OData error body); `SleepRecorder` to replace `entraid._sleep`; helpers to build `SyncConfig` objects and DiffSync model subclasses of `EntraidModel` (for example `IdentityEmployee`, `IdentityServiceAccount`, `IdentityGroup` with `members: list[str] = []`) and an `EntraidAdapter` subclass with `top_level` and model attributes, constructed with `target="source"`, `transport=router.transport`, `credential=FakeCredential()`. Graph IDs use zero-padded GUID-like strings; names use `example.com`.

**Checkpoint**: The Graph client and settings exist; story work can begin.

---

## Phase 3: User Story 1 - Sync users and groups from Entra ID into Infrahub (Priority: P1) 🎯 MVP

**Goal**: Unfiltered `users` and `groups` load into DiffSync models with only the needed properties requested.

**Independent Test**: `uv run pytest -q tests/adapters/test_entraid_graph.py tests/adapters/test_entraid_adapter.py -k "paging or select or empty or destination or load"`.

- [ ] T009 [US1] In `infrahub_sync/adapters/entraid.py`, add `EntraidAdapter(DiffSyncMixin, Adapter)` with `type = "Entraid"`: `__init__(target, adapter, config, *args, transport=None, credential=None, **kwargs)` raising `EntraidConfigError("The Entra ID adapter can only be the source of a sync ...")` when `target != "source"`; parse settings with `EntraidSettings.from_settings(adapter.settings)`; resolve the collection of every `schema_mapping` entry with a `mapping` (errors before any request); compute the request plan of `data-model.md` (per collection: `select` list and `needs_members`); keep `transport`/`credential` for the client. Add `_run(coro)` (critique E2), `_read_all()` (one `EntraidGraphClient`; `list_collection` per planned collection; INFO log per collection; one summary INFO log), lazy `_records_by_collection` cache, `model_loader` (copy records with `dict(r)`, `model.filter_records`, `model.transform_records`, `record_to_diffsync`, `self.add`, INFO log with loaded and read counts), and `record_to_diffsync` (`local_id` from `id`, `EntraidConfigError` when `id` is missing; `static`; plain `mapping` through `infrahub_sync.adapters.utils.get_value`, a list value raises `EntraidConfigError` asking for a transform). Do not override `cursor_tier_for`.
- [ ] T010 [P] [US1] Create `tests/adapters/test_entraid_graph.py` (start with `pytest.importorskip("msgraph")`) with tests on `EntraidGraphClient` through `GraphRouter`: three-page paging returns every record in order; an empty page with a next link is followed; an empty result returns `[]`; the first request carries `$select` with the given properties and `$top` equal to `page_size`.
- [ ] T011 [P] [US1] Create `tests/adapters/test_entraid_adapter.py` (start with `pytest.importorskip("msgraph")`) with tests: users and groups load from bare `users`/`groups` mappings with mapped attribute values and `local_id`; `$select` is built from field mappings and filters plus `id` plus `settings.select`, and excludes keys produced by transforms; an empty tenant loads zero objects; `target="destination"` raises `EntraidConfigError` mentioning "source"; a list-valued property mapped to a single attribute raises `EntraidConfigError` mentioning "transform"; `EntraidModel.create`/`update`/`delete` raise `NotImplementedError`; `cursor_tier_for` returns `CursorTier.NONE`.

**Checkpoint**: MVP: users and groups load.

---

## Phase 4: User Story 2 - Split one Entra ID query into several Infrahub kinds (Priority: P1)

**Goal**: Named collections with filters and advanced queries, read once, feeding several kinds.

**Independent Test**: `uv run pytest -q tests/adapters/ -k "filter or advanced or shared or transform or unknown_collection"`.

- [ ] T012 [US2] In `infrahub_sync/adapters/entraid.py`, confirm and complete collection handling in `EntraidAdapter`: several `schema_mapping` entries reading one collection share one Graph read (cache keyed by collection name; `select` is the union over entries); records are copied per entry before filters and transforms so one entry's transform never changes another entry's records; collections not named by any entry are not read.
- [ ] T013 [P] [US2] In `tests/adapters/test_entraid_graph.py`, add tests: a collection with `filter` sends `$filter` unchanged and no `ConsistencyLevel` header and no `$count`; a collection with `advanced_query: true` sends `$count=true` and `ConsistencyLevel: eventual` on the first and on every next-page request.
- [ ] T014 [P] [US2] In `tests/adapters/test_entraid_adapter.py`, add tests: two entries (`IdentityEmployee` with filter `userPrincipalName` regex `^(?!svc-)`, `IdentityServiceAccount` with regex `^svc-`) reading one `accounts` collection get disjoint records and Graph receives one request series for the collection; a transform (for example lower-case name from `userPrincipalName`) sets the stored value and does not leak into the other entry; an entry naming an unknown collection raises `EntraidConfigError` at construction listing the valid names, with zero Graph requests; a collection named `users` is refused.

**Checkpoint**: Collections work.

---

## Phase 5: User Story 3 - Keep group membership in Infrahub (Priority: P2)

**Goal**: Groups carry their user members as references to loaded users.

**Independent Test**: `uv run pytest -q tests/adapters/test_entraid_adapter.py -k member`.

- [ ] T015 [US3] In `infrahub_sync/adapters/entraid.py`: start-up validation that a groups entry field with `mapping: members` has a `reference` (critique E1); in `_read_all`, for each groups collection with `needs_members`, call `user_member_ids` for every group with `asyncio.Semaphore(MEMBER_CONCURRENCY)` and `asyncio.gather`, `transitive = settings.membership == "transitive"`, and store the list under `record["members"]`; in `model_loader`, build one index per referenced kind (`{obj.local_id: obj.get_unique_id() for obj in self.store.get_all(model=reference)}`) before converting records; in `record_to_diffsync`, resolve `mapping` + `reference` fields through the index (list field: sorted list of unique IDs; single field: first sorted or omitted), count dropped IDs; at the end of `model_loader`, log one WARNING with the kind, field and dropped count when it is above zero.
- [ ] T016 [P] [US3] In `tests/adapters/test_entraid_adapter.py`, add tests: direct membership requests `/groups/{id}/members` and keeps only `#microsoft.graph.user` members; `membership: transitive` requests `/groups/{id}/transitiveMembers`; a user reached twice appears once; members not loaded (filtered out, or in another users kind) are dropped and exactly one WARNING record (via `caplog`, logger `infrahub_sync.adapters.entraid`) carries the dropped count; no member requests are sent when no entry maps `members`; `mapping: members` without `reference` raises `EntraidConfigError` naming the field.

**Checkpoint**: Membership works.

---

## Phase 6: User Story 4 - Authenticate from a laptop and from a CI pipeline (Priority: P2)

**Goal**: Both `auth` modes build the right credential; secrets never leak.

**Independent Test**: `uv run pytest -q tests/adapters/test_entraid_settings.py -k "auth or credential or secret"`.

- [ ] T017 [P] [US4] Create `tests/adapters/test_entraid_settings.py` (start with `pytest.importorskip("msgraph")`) with tests: `auth: client_secret` with the three `ENTRAID_*` variables builds a `ClientSecretCredential` with those tenant and client IDs (monkeypatch `entraid.ClientSecretCredential` with a recorder, or inspect the real object); tenant and client IDs fall back to settings when the variables are absent, and variables win when both exist; a `client_secret` key in settings raises `EntraidConfigError` naming `ENTRAID_CLIENT_SECRET`; a missing secret raises naming `ENTRAID_CLIENT_SECRET`; `auth: default` builds `DefaultAzureCredential` (recorder) with no tenant, client or secret; `repr(settings)` does not contain the secret value; the credential is closed when `EntraidGraphClient` exits.

**Checkpoint**: Authentication works.

---

## Phase 7: User Story 5 - Understand and recover from failures (Priority: P3)

**Goal**: Throttling, permission, timeout, credential and settings failures give clear messages.

**Independent Test**: `uv run pytest -q tests/adapters/ -k "retry or permission or timeout or authentication or invalid"`.

- [ ] T018 [P] [US5] In `tests/adapters/test_entraid_graph.py`, add tests: a 429 with `Retry-After: 2` followed by a 200 succeeds after one recorded wait of 2 seconds; a 503 without `Retry-After` waits the fallback; a `Retry-After` above the cap waits the cap; 429 on every attempt raises `EntraidGraphError` after `max_retries + 1` requests, with status 429 in the message; 401 and 403 raise `EntraidPermissionError` whose message names `User.Read.All`, `Group.Read.All` and `GroupMember.Read.All` (parametrize over the two statuses); a transport that raises `httpx.ReadTimeout` raises `EntraidGraphError` naming the path; a `FakeCredential` that raises `ClientAuthenticationError` raises `EntraidAuthenticationError` naming the `auth` mode; no error message contains `fake-token`.
- [ ] T019 [P] [US5] In `tests/adapters/test_entraid_settings.py`, add one parametrized test of invalid settings, each raising `EntraidConfigError` whose message names the setting: `page_size` 0 and 1000 and `"abc"`, `membership: nested`, `auth: token`, `graph_url: http://graph.example.com/v1.0`, `timeout: 0`, `max_retries: 11`, unknown top-level key, collection without `resource`, collection with `resource: devices`, collection with an unknown key, `advanced_query: "yes"`, `select` with key `devices`.

**Checkpoint**: All stories done.

---

## Phase 8: Polish & Cross-Cutting Concerns

- [ ] T020 [P] Create `examples/entraid_to_infrahub/schema.yml` (Infrahub schema, version `"1.0"`, namespace `Identity`: `IdentityEmployee` and `IdentityServiceAccount` with attributes `entra_id` (Text, unique), `name` (Text), `display_name` (Text, optional), `email` (Text, optional), `enabled` (Boolean, optional); `IdentityGroup` with `entra_id`, `name`, `description` (optional), and relationship `members` to `IdentityEmployee`, cardinality many, optional) and `examples/entraid_to_infrahub/config.yml` (`name: from-entraid`; source `entraid` with settings `auth: client_secret`, `membership: direct`, collections `accounts` (`users`, filter `accountEnabled eq true`) and `security_groups` (`groups`, filter `securityEnabled eq true and NOT groupTypes/any(c:c eq 'Unified')`, `advanced_query: true`); destination `infrahub` at `http://localhost:8000`; `schema_mapping` per research R13 with identifiers `["entra_id"]`, the two regex filters, a lower-case `name` transform, and `IdentityGroup.members` with `mapping: members`, `reference: IdentityEmployee`). Comments explain the env vars and that service-account members are counted as dropped. Must pass `uv run yamllint` and `uv run infrahub-sync list --directory examples/`.
- [ ] T021 Generate `examples/entraid_to_infrahub/entraid/` and `examples/entraid_to_infrahub/infrahub/` (`__init__.py`, `sync_adapter.py`, `sync_models.py`) with the repository generator: a one-off script, kept outside the repository commit, loads `schema.yml`, builds `infrahub_sdk.schema.main.NodeSchemaAPI` objects (see `tests/test_generator.py` for construction), loads the `SyncInstance` from `config.yml` with `infrahub_sync.utils.get_instance`, and calls `infrahub_sync.utils.render_adapter(sync_instance=..., schema=...)`; then run `uv run ruff format examples/entraid_to_infrahub/` like the other examples. Do not hand-edit the output. Compare shape with `examples/peeringdb_to_infrahub/`.
- [ ] T022 [P] Create `docs/docs/adapters/entraid.mdx` with the content listed in `plan.md` ("Documentation page content"), following the structure of `docs/docs/adapters/prometheus.mdx` (front matter, `## Requirements`, `## Sync directions supported`, `## Configuration`, `### Environment variables`, `### Settings reference`, `## Schema mapping`, example, `## Common issues and troubleshooting`). Must pass `uv run rumdl check docs/docs/adapters/entraid.mdx`.
- [ ] T023 [P] Add `'adapters/entraid'` to `docs/sidebars.ts` in alphabetical order (after `adapters/device42`); add `entraid` to the built-in adapter lists in `AGENTS.md` ("Available adapters") and `dev/guides/adding-an-adapter.md` ("When to add an adapter"); add a row or entry to `docs/docs/adapters/choosing-an-adapter.mdx` if it lists adapters.
- [ ] T024 [P] Create `changelog/+entraid-adapter.added.md` with one or two sentences describing the new `entraid` source adapter (users, groups, membership from Microsoft Entra ID through Microsoft Graph; optional install of `msgraph-sdk` and `azure-identity`).
- [ ] T025 Run and fix: `uv run invoke format`; `uv run ruff check .`; `uv run ty check .` (only allowed new diagnostics: `unused-ignore-comment` warnings on the SDK import lines, see plan "Design notes"); `uv run invoke lint` compared with `/home/ubuntu/src/overnight/lint-baseline.txt` (no new pylint findings); `uv run rumdl check .`; `uv run pytest -q` (baseline plus the new tests, all passing); `uv run infrahub-sync --help`; `uv run infrahub-sync list --directory examples/`.
- [ ] T026 Run the validation steps of `quickstart.md` (sections 1 to 4) and record the results in the implementation report.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: none.
- **Foundational (Phase 2)**: T002 → T003 → T004 → T005 → T006 → T007 (same file, sequential); T008 after T007.
- **User stories (Phases 3 to 7)**: after Phase 2. US1 (T009) is the base of the adapter class; US2 (T012) and US3
  (T015) extend it and run after T009, in that order (same file). US4 and US5 are test-only tasks on Phase 2 code
  and can run any time after T008.
- **Polish (Phase 8)**: T020 before T021. T022, T023, T024 any time. T025 and T026 last.

### Within Each User Story

- Implementation task on `entraid.py` first, then its test tasks (marked [P] because they touch other files).

## Parallel Example: after Phase 2

```text
Task: "T010 [US1] paging/select tests in tests/adapters/test_entraid_graph.py"
Task: "T017 [US4] credential tests in tests/adapters/test_entraid_settings.py"
Task: "T022 docs page in docs/docs/adapters/entraid.mdx"
Task: "T024 changelog fragment"
```

## Implementation Strategy

### MVP First

Phases 1, 2, 3: users and groups load from a mocked Graph. Then US2 (collections), US3 (membership), US4 and US5
(tests on existing code), then Polish.

## Notes

- Commit after each phase with an imperative subject and the `Co-Authored-By` trailer.
- Stage explicit paths only; never `_ty_probe.py`.
