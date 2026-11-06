# Research: Webex source adapter

## HTTP and authentication

Decision: use existing requests with explicit GET, disabled redirects, HTTPS same-origin endpoint validation and POST only for OAuth refresh at `/v1/access_token`. Retry 429 using Retry-After (seconds or HTTP date), timeouts and connection failures up to configured retries; keep response and request exception text out of errors. Service-app tokens refresh initially and before expiry, and once after a 401. Fixed tokens never refresh. In-memory refreshed tokens are not persisted or printed.

Rationale: generic RestApiClient supports bearer neither OAuth nor pagination and includes unsafe raw exception text. Reusing its transport implementation is unsuitable; subclass its interface and reuse the adapter conversion instead.

[Service apps](https://developer.webex.com/create/docs/service-apps) documents typical 14-day access and 90-day refresh lifetimes; deployment must maintain a valid refresh token or reauthorize. Refreshed pairs are retained during the process only, so operators must separately maintain the environment/secret manager across scheduled runs; this adapter does not promise perpetual unattended authorization. [Authentication](https://developer.webex.com/create/docs/authentication) describes the refresh grant. No real credentials are read during validation.

## Pagination and query values

Decision: default max=100; per-resource page_size may select 1000 for workspaces. Follow the server's same-origin Link rel=next unchanged until absent, including empty intermediate pages; reject cycles. Initial before/after cursors are rejected to avoid partial inventories. Mapping params override source params; pagination URLs control continuation. Booleans serialize lower-case and lists comma-separated. Values may be literal, `{env: NAME}` or `{parent: field.path}`. Organization ID is an optional source setting (including environment reference), applied as orgId; endpoints that do not accept orgId can remove it with a null resource parameter.

Rationale: [REST basics](https://developer.webex.com/meeting/docs/basics) specifies Link pagination, including empty pages. The supplied fixture facts establish locations max=100 and workspaces max=1000. Parameter names remain unrestricted. The referenced Calling workspace page could not be fetched (size limit); supported parameters for the delivered `/v1/workspaces` example are independently demonstrated by the provided verified facts, and no unverified endpoint-specific parameter allowlist is introduced.

## Mapping options

Decision: keep Webex-specific options under source.settings.resources keyed by schema model name. SchemaMappingModel does not accept arbitrary mapping options, and adding shared fields would widen the core contract unnecessarily. `mapping` remains the resource path. Parent options name a schema mapping and use loaded raw, filtered/transformed records; placeholder values are URL-escaped. Parent mappings must precede their dependents in explicit `order` because the dependency graph only understands peer reference edges. Details merge before field transforms.

Alternatives: adding new core schema fields or using endpoint-specific adapter logic were rejected; both add scope and reduce configuration flexibility.

## Existing engine behavior and scope

Generic REST scalar peer values are already unique keys. Dict values containing id resolve source local_id and raise IndexError when missing. The meeting-room example uses scalar Webex location IDs. Existing SKIP_UNMATCHED_DST preserves destination-only nodes, while reference-only Webex source resources use the existing IGNORE model flag to avoid site diff operations.

Optional null/blank Text identifiers can pass existing destination validation and collapse unrelated sites into an empty identity. Existing continue-on-error does not reliably skip those optional values. This is a known limitation for later work; existing Infrahub loading and CLI behavior are unchanged. Site ID preparation and its matching conventions belong to a separate workflow and are not included as an example.

## Follow-ups

Empty destination site identifiers, cross-side parent preflight, room rename/removal reconciliation and durable OAuth rotation remain separate work. No live service calls, schema migration, new dependencies, publication or existing core changes are included.
