# Research: Microsoft Entra ID source adapter

All decisions below were checked against the installed packages (`msgraph-sdk` 1.65.0, `msgraph-core` 1.5.2,
`microsoft-kiota-*` 1.16.0, `azure-identity` 1.27.0, `httpx` 0.28.1, `structlog` 26.1.0) with a probe that sent
requests through an `httpx.MockTransport`.

## R1. How the adapter calls Microsoft Graph

- **Decision**: Use the `msgraph-sdk` request builders (`GraphServiceClient.users`, `.groups`,
  `.groups.by_group_id(id).members`, `.transitive_members`) to build each `RequestInformation`, and send it with
  `GraphRequestAdapter.send_primitive_async(info, "bytes", {"XXX": ODataError})`. Parse the body with `json.loads`.
  The adapter works on plain dicts in the Graph wire format.
- **Rationale**: Field mappings name Graph property names (`displayName`, `userPrincipalName`,
  `onPremisesExtensionAttributes.extensionAttribute1`). Typed SDK models use snake_case attributes and turn some IDs
  into `uuid.UUID`. Raw JSON keeps the names and string IDs that the configuration uses. This is the approach of the
  prototype in `/home/ubuntu/src/entraid-seed/graph.py`, which works.
- **Probe result**: the request URL is
  `https://graph.microsoft.com/v1.0/users?$count=true&$filter=accountEnabled%20eq%20true&$select=id,displayName&$top=999`.
  An HTTP error raises `ODataError` (a subclass of `kiota_abstractions.api_error.APIError`) with
  `response_status_code`, `response_headers` (an `httpx.Headers`) and `error.code`.
- **Alternatives considered**: typed SDK models (rejected: property-name mismatch with the configuration);
  `httpx` calls without the SDK (rejected: the brief requires the SDK, and the SDK supplies authentication and
  national-cloud handling).

## R2. Retries for HTTP 429 and 503

- **Decision**: Turn off the SDK retry handler (`RetryHandlerOption(should_retry=False)` in the middleware options
  given to `GraphClientFactory.create_with_default_middleware`) and run one retry loop in the adapter. On 429 or 503
  the loop waits for `Retry-After` seconds (fallback 5 seconds when the header is missing or not a number, capped at
  120 seconds), then sends the request again. `max_retries` (default 5, range 0 to 10) bounds the number of retries.
  When the retries run out, the adapter raises `EntraidGraphError` that names the status and the request path.
  The wait is `asyncio.sleep`, which tests replace.
- **Rationale**: The SDK handler waits its own back-off (3 seconds plus exponential delay plus jitter) and stops
  retrying when `Retry-After` is 180 seconds or more (observed in the prototype README). Two stacked retry layers made
  the number of attempts and the wait time hard to predict and slow to test. One loop gives one bounded, testable
  behavior.
- **Probe result**: with `should_retry=False`, one 429 produces exactly one HTTP request and an `ODataError` with
  status 429 and the `retry-after` header.
- **Alternatives considered**: SDK retry handler plus extra rounds (the prototype; rejected for the reasons
  above); retry on 504 as well (the brief names 429 and 503 only; not added).

## R3. Request timeout

- **Decision**: Create the `httpx.AsyncClient` with `timeout=settings.timeout` (default 60 seconds). Catch
  `httpx.TimeoutException` around each send and raise `EntraidGraphError` that names the request path and the timeout
  value. A timeout is not retried.
- **Rationale**: The brief requires a timeout and bounded retries for 429 and 503 only. A clear error is enough for
  an operator to rerun.

## R4. Advanced queries

- **Decision**: For a collection with `advanced_query: true`, set `count=True` in the query parameters of the first
  request and add the `ConsistencyLevel: eventual` header to **every** page request.
- **Rationale / probe result**: `@odata.nextLink` keeps the query string (`$count`, `$filter`) but the request built
  from the link with `builder.with_url(link)` has no `ConsistencyLevel` header. Graph rejects later pages of an
  advanced query without it, so the adapter adds it to each page.

## R5. Paging

- **Decision**: Send `$top = page_size` (1 to 999, default 999) on the first request, then follow
  `@odata.nextLink` with `builder.with_url(link)` until a page has no link. Empty pages with a link are followed.

## R6. Group members

- **Decision**: For every group of a collection whose `schema_mapping` entries map a field named by the `members`
  key with a `reference`, read `/groups/{id}/members` (`membership: direct`) or `/groups/{id}/transitiveMembers`
  (`membership: transitive`) with `$select=id` and `$top`, follow paging, keep only members whose `@odata.type` is
  `#microsoft.graph.user`, remove duplicate IDs, and store the sorted list of user IDs under the key `members` of the
  group record. Member requests run with a fixed concurrency of 4 (`asyncio.Semaphore`).
- **Rationale**: The brief names these endpoints and says to keep only user members. Members of a directory object
  list carry `@odata.type`. The OData cast segment (`/members/microsoft.graph.user`) would also work, but the brief
  names the plain endpoints, and filtering on `@odata.type` keeps one code path for both modes.
- **Alternatives considered**: `$expand=members` (rejected: Graph caps expanded members at 20 per group);
  a `concurrency` setting (rejected: not asked for; a fixed value keeps the settings small).

## R7. Each collection is read once per run

- **Decision**: On the first `model_loader` call, the adapter reads every collection that a `schema_mapping` entry
  names, plus the members of groups where needed, in one `asyncio.run` with one Graph client, and keeps the records
  in a dict keyed by collection name. Each `model_loader` call copies the records (`dict(record)`) before
  `filter_records` and `transform_records`.
- **Rationale**: `transform_records` writes the transformed key into the record dict in place
  (`DiffSyncModelMixin.apply_transform` sets `item[field]`). Without a copy, a transform of one `schema_mapping` entry
  would leak into another entry that reads the same collection. The engine loads one side in one thread and calls
  `model_loader` one model at a time (`potenda.load_one_side`), so no lock is needed. On the incremental path the
  engine calls `model_loader` directly instead of `load()`, so the adapter does all its work in `model_loader`.

## R8. Membership references and the dropped-reference warning

- **Decision**: In `model_loader`, before converting records, build one index per referenced kind: Graph ID
  (`local_id`) → `get_unique_id()` of the objects already loaded in the store. Member IDs not in the index are
  dropped. At the end of the `model_loader` call for a kind, if any reference was dropped, log one warning with the
  kind, the field and the count.
- **Rationale**: Building the index once per call avoids the quadratic scan of the store that the prototype measured
  on large tenants. Logging at the end of `model_loader` (not in `load()`) also works on the engine's incremental
  path. A configuration usually has one groups kind, so a run logs one warning.
- **Order**: `SyncConfig.compute_order_and_tiers` orders kinds from `reference` edges, so the groups kind loads after
  the users kind it references. An explicit `order` that lists groups first makes every member reference drop, which
  the warning shows.

## R9. Authentication

- **Decision**: Use the async credentials from `azure.identity.aio`:
  - `client_secret`: `ClientSecretCredential(tenant_id, client_id, client_secret)`. Tenant and client ID come from
    `ENTRAID_TENANT_ID` / `ENTRAID_CLIENT_ID`, else from the `tenant_id` / `client_id` settings. The secret comes only
    from `ENTRAID_CLIENT_SECRET`. A `client_secret` key in settings is refused. Missing values fail when the adapter
    starts, with a message that names the environment variables.
  - `default`: `DefaultAzureCredential()`.
  The credential goes to `AzureIdentityAuthenticationProvider(credential, scopes=[<graph origin>/.default],
  allowed_hosts=[<graph host>])`. The scope is derived from `graph_url`, so national clouds work. `auth` defaults to
  `client_secret`. The credential is closed when the client closes.
- **Rationale**: Constructing either credential does not contact Azure, so tests can check construction. The async
  variants fit the async request adapter.
- **Constraint found**: the Azure authentication provider only sends tokens over HTTPS, so `graph_url` must be an
  `https://` URL; the adapter validates it when it starts.

## R10. Logging with structlog

- **Decision**: In `entraid.py`, define `logger = logging.getLogger(__name__)` and
  `log = structlog.wrap_logger(logger, wrapper_class=structlog.stdlib.BoundLogger,
  processors=[structlog.stdlib.filter_by_level, structlog.processors.KeyValueRenderer(key_order=["event"])])`.
  Log calls use key-value context (`log.info("collection read", collection=..., records=..., requests=...)`).
- **Rationale**: `structlog` is a declared dependency but the repository never calls `structlog.configure`, so
  `structlog.get_logger()` would print to standard output and ignore the CLI's `--verbosity`. Wrapping the
  `infrahub_sync.adapters.entraid` standard logger keeps the CLI's level and handler (`cli._setup_logging` configures
  the `infrahub_sync` logger), lets `caplog` capture records in tests, and keeps `logger = logging.getLogger` as
  `tests/test_logging.py` expects.

## R11. Optional dependency imports and linting

- **Decision**: Import `msgraph`, `msgraph_core`, `kiota_*` and `azure.identity.aio` at module top with
  `# ty: ignore[unresolved-import]` and `# pylint: disable=import-error`, as `netbox.py` does for `pynetbox`. The
  documentation gives `pip install msgraph-sdk azure-identity`.
- **Rationale**: In an environment without the packages (for example CI after `uv sync`, which removes packages not
  in `uv.lock`), `ty` and `pylint` would otherwise report the imports. Tests skip with
  `pytest.importorskip("msgraph")`.
- **Caution for implementers**: `uv sync` removes `msgraph-sdk` and `azure-identity` from `.venv`. Reinstall them
  with `uv pip install msgraph-sdk azure-identity` after any `uv sync`.

## R12. Example and generated code without a running Infrahub

- **Decision**: Ship `examples/entraid_to_infrahub/schema.yml` (an Infrahub schema with the example kinds) and
  produce `entraid/` and `infrahub/` with the repository's own generator (`infrahub_sync.utils.render_adapter`),
  given `NodeSchemaAPI` objects built from that schema in a one-off script that is not committed. Then format the
  output with `ruff format`, as the other examples are.
- **Rationale**: `infrahub-sync generate` reads the schema from a live Infrahub, which this environment does not
  have. Rendering through the same templates gives the same files that `generate` would write, so the code is still
  generated, not written by hand.

## R13. Example data model

- **Decision**: Kinds `IdentityEmployee`, `IdentityServiceAccount` and `IdentityGroup` (namespace `Identity`).
  - Collection `accounts` (`users`, filter `accountEnabled eq true`) feeds `IdentityEmployee`
    (mapping filter: `userPrincipalName` regex `^(?!svc-)`) and `IdentityServiceAccount` (regex `^svc-`).
  - Collection `security_groups` (`groups`, filter `securityEnabled eq true and NOT groupTypes/any(c:c eq 'Unified')`,
    `advanced_query: true`). Graph accepts `NOT` only as an advanced query, so the example shows why the flag exists.
  - `IdentityGroup.members` references `IdentityEmployee`; service-account members are dropped and counted.
  - A transform builds `IdentityEmployee.name` in lower case from `userPrincipalName`.
- **Rationale**: Meets FR-027 with three kinds and two collections, and uses only `example.com` data.
