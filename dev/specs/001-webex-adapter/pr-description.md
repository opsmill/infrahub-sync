# feat(adapters): add Webex source adapter

## Summary

Webex inventory can now be compared with a destination and synced through the usual configuration-driven workflow. The new `webex` source adapter reads configured JSON GET endpoints, applies shared field mappings, filters and transforms, and resolves peer relationships. The single example maps locations, workspaces and devices to Infrahub kinds identified by stable Webex IDs.

## Key Changes

- Configure list or singleton responses, alternate response keys, requests per parent and details per record. Explicit ordering loads locations before workspaces and workspaces before devices.
- Source and resource query settings support fixed, environment and parent values, booleans and lists. One organization is enforced per project; resources may omit unsupported `orgId`, but cannot select another organization. Endpoint and pagination scope changes are rejected too.
- Pagination follows every `Link` page, including empty intermediate pages. Initial `before`/`after` settings are accepted and normalized to a first-page inventory request so earlier records are included. Page size controls each request, not total inventory.
- Existing fixed-token and Service App authentication behavior is retained: credentials come from environment variables; Service App access is refreshed initially, on expiry and once after a data 401. Refreshed credentials remain process-local.
- HTTP 429, server errors, timeouts and connection failures have bounded retries, respecting `Retry-After`. Errors name Webex and the data resource even when token refresh fails; HTTP 400 includes sanitized Webex message text, and missing response keys name the endpoint and key. Credentials and raw response bodies are excluded from logs and displayed traceback chains.
- Request counters include retries, OAuth requests and failed parent requests, with per-mapping and cumulative totals.
- The example includes an accompanying destination schema and mappings from location to workspace to device:

```yaml
- name: WebexDevice
  mapping: /v1/devices
  identifiers: [webex_id]
  fields:
    - name: webex_id
      mapping: id
    - name: workspace
      mapping: workspaceId
      reference: WebexWorkspace
```

No dependencies or infrahub-sync core changes are included. Webex remains source-only; data requests use GET and only OAuth refresh uses POST.

## Related Context

The unchanged specification from `a5eb00bc`, implementation plan, updated compliance audit and validation evidence are in `dev/specs/001-webex-adapter/`. The engineer retains the decisions on FR-004 credential renewal and FR-012 OAuth read-only wording; this change does not revise either policy.

## Documentation Updates

- `docs/docs/adapters/webex.mdx`: authentication, environment variables, location/workspace/device read scopes, resource and query configuration, organization isolation, pagination, errors, counters and the core deletion limitation.
- `examples/webex_workspaces_to_infrahub/config.yml` and `schema.yml`: one complete inventory project with location and workspace peers, stable identifiers and default deletion diffs.
- `changelog/+webex-adapter.added.md`: records the adapter and complete inventory example.

## Test Plan

Full results, command output excerpts and the remaining-gap table are in `dev/specs/001-webex-adapter/validation.md` and `audit.md`.

- Invented fixtures cover exactly 25 device creates in an empty destination, mapped attributes, fixture sync, an empty second diff after reloading the destination, peer IDs and workspace-before-device writes. Removing one device produces exactly one delete diff and no other changes.
- Two projects load only their own organization's records. HTTP tests cover both start cursors, fixed Calling workspace parameters on every page, 1,001 records across three pages with a 429/`Retry-After` retry, and 500 parent requests whose counter includes retries and 404s.
- A ten-case failure matrix asserts source, data resource and credential-free logs/locals-enabled tracebacks. HTTP 400 tests preserve safe message text while removing current, earlier, rotated, encoded and bearer credentials and URLs.
- `uv run pytest -q`: 301 passed, 3 skipped. Ruff, yamllint, ty, Pylint on all five Webex Python files, CLI sanity, fixture generation and the docs build pass. Full `invoke lint` stops on the unchanged spec's two rumdl MD007 findings; standalone full Pylint retains 56 existing core diagnostics. Changed Markdown/MDX passes rumdl. Documentation fact/prose checks and the rules read are recorded in `validation.md`.
- Live Webex/Infrahub runs, the timed configuration exercise and the real organization count remain for the engineer.

## Known Limitations

- Persisted deletion is an existing infrahub-sync core limitation: `InfrahubModel` implements create and update but no delete. The diff correctly marks a removed device for deletion, and DiffSync removes its in-memory model, but the node stays in Infrahub and is loaded again on the next run. No core or example-local delete implementation is added.
- Service App refresh tokens and renewed access tokens remain in memory for one process. Valid credentials and rotated refresh-token persistence between runs require an external workflow or secret manager.
- The example requires its accompanying destination schema (or matching kinds and fields), credentials and destination connection settings. Its live organization counts and list/diff behavior have not been validated.
