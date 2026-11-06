feat(adapters): add Webex source adapter

## Summary

Teams that manage meeting rooms in Webex can now sync meeting-room workspaces into an existing Infrahub location hierarchy with the new `webex` source adapter. They configure JSON GET endpoints, query parameters and resource extraction in YAML, then use the usual `schema_mapping` fields, filters, transforms and peer references to map the records to Infrahub kinds.

## Key Changes

- Infrahub Sync can read locations, workspaces and other configured Webex JSON resources, read-only. The adapter refuses to be used as a destination. Every data request uses GET; the only POST is OAuth refresh at `/v1/access_token`.
- Users define endpoint paths in `schema_mapping` and extraction options in `resources`: alternate response keys, single objects, requests per parent and a details request per record. Parent mappings must load first through an explicit `order`; empty parent fields and per-parent HTTP 404 responses skip that request.
- Query parameters can come from configuration, environment variables or parent fields. Resource parameters override source parameters, and `null` omits a parameter. Pagination follows `Link` headers through the last page, including empty pages, while retaining configured parameters omitted by the next-page link.
- Two authentication modes, with credentials read only from environment variables:
    - `token`: a fixed bearer token from `WEBEX_TOKEN`, without renewal.
    - `service_app`: `WEBEX_CLIENT_ID`, `WEBEX_CLIENT_SECRET` and `WEBEX_REFRESH_TOKEN`; obtains an access token, refreshes on expiry and retries once after HTTP 401.
- Reads retry HTTP 429, server errors, timeouts and connection failures within a configured retry budget, honouring `Retry-After`. Authentication and permission failures name the resource. Errors exclude credentials and raw response bodies; requests stay within the configured HTTPS origin, and redirects and pagination cycles are rejected.
- The meeting-room example keeps `meetingRoom` workspaces with a nonempty `locationId`. It maps the complete `displayName` to the room's name and identifier, its final slash-separated segment to `room_number`, and `locationId` to an existing site's `webex_location_id`. Site models are reference-only, and `SKIP_UNMATCHED_DST` preserves destination-only rooms and sites.
- No dependencies are added. The implementation is in `webex.py` and `webex_client.py`; existing adapters, shared mapping behavior and CLI commands are unchanged.

## Related Context

Spec, plan, research, tasks and validation record: `dev/specs/001-webex-adapter/`.

## Documentation Updates

- New page `docs/docs/adapters/webex.mdx`, added to `docs/sidebars.ts`: authentication, read scopes, environment variables, connection and resource settings, pagination, meeting-room mapping and common errors.
- Example project `examples/webex_workspaces_to_infrahub/config.yml`, for rooms under existing sites whose Webex location IDs are prepared separately.
- Changelog fragment `changelog/+webex-adapter.added.md`.

## Test Plan

Results recorded in `dev/specs/001-webex-adapter/validation.md`:

- Unit tests with mocked HTTP and invented fixtures: `uv run pytest -q` gives 260 passed, 3 skipped. Webex tests cover token refresh, pagination, retries, permission errors, credential-safe tracebacks, query overrides, parent requests, details, filters, transforms and peer references.
- Example tests generate source and destination models against a fixture schema, create rooms under the correct existing site node IDs, perform no site writes, retain old room names and converge to a second diff with no changes. CLI generation uses a mocked destination schema; no servers are contacted.
- `uv sync --extra dev`, `uv run invoke format`, Ruff, yamllint and `uv run ty check .` pass. Pylint reports zero diagnostics for the two adapter modules and three Webex test modules. Full `uv run invoke lint` still exits 30 on 56 existing Pylint diagnostics in unchanged code.
- CLI help and project listing pass, with one Webex project listed. `uv run invoke docs.docusaurus`, `uv run invoke docs.rumdl` and `git diff --check` pass.
- Not run: a live Webex organization or a live Infrahub sync.

## Known Limitations

- Infrahub sites without `webex_location_id` can load with empty identities, collapsing unrelated sites or producing invalid parent matches. The existing `sync --continue-on-error` option does not reliably skip optional empty identifiers, and `diff` has no such flag. The example tests use populated, unique destination site IDs.
- Adding `LocationSite.webex_location_id` to the destination schema and writing Webex location IDs onto existing sites require a separate workflow. The example includes no site-enrichment configuration or matching convention.
- A room renamed in Webex stays in Infrahub under its old name, and the new name creates another room. Removed rooms are also retained under the example policy; rename/removal reconciliation is follow-up work.
- Refreshed Service App tokens are held only in memory and are not kept between runs. Maintaining valid credentials and persisting rotated refresh tokens requires an external workflow or secret manager.
