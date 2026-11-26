# Configuration and HTTP contract

- Built-in source name: webex; destination use is rejected before credentials or requests.
- Credentials: WEBEX_TOKEN, or WEBEX_CLIENT_ID / WEBEX_CLIENT_SECRET / WEBEX_REFRESH_TOKEN with auth_method service_app; environment variable names are configurable.
- url: HTTPS origin, no user info, query, fragment or path. All endpoints resolve within that origin; redirects and external pagination fail.
- timeout: positive seconds, retries: nonnegative count (default 3), page_size: positive integer (default 100).
- org_id: literal or environment reference; sends orgId, removable per resource for endpoints not accepting it.
- params: arbitrary names and scalar/boolean/list values; lists join with commas; null omits a value; environment references must exist before any HTTP request, including OAuth.
- resources: keyed by model name; options response_key, params, page_size, parent, details, read_only.
- Data request method is always GET. Only service-app refresh may POST to /v1/access_token; inherited write helpers fail. No redirects are followed.
- HTTP 401 may trigger one service refresh; 403 fails with permission context. 429 honors Retry-After up to retries; timeout/connection errors use bounded backoff. 5xx retries boundedly. Per-parent 404 skips that parent's absent resource; other 404s fail.
- Error output includes Webex, configured resource/model and safe status/reason, excluding raw bodies, query values, URLs from responses, auth headers and underlying exception chains.
- Pagination reads items by default, follows next even through empty pages, and rejects loops and external links. Missing response key or malformed record shape fails visibly.
- Parent requests skip missing parent field values and log a count; parent mappings must be loaded first. Detail fields overwrite list-record fields, then shared filters/transforms run.
- Source reference-only resources use DiffSync IGNORE; other fields/filters/transforms/identifiers/references follow existing generic REST conversion.
