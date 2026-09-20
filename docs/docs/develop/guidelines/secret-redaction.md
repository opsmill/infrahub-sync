---
title: "Secret redaction"
---

## Secret redaction

Failure messages can include credentials from an adapter, an HTTP library or a
configuration parser. When adding an error path, sanitize the text before returning it
through an API, forwarding it to Prefect or storing it for remote inspection. Never
include credentials in messages you write yourself.

The rules below describe the [released redaction implementation][execution-source].
They apply to both registered service runs and direct execution, with different callers
responsible for the final output.

### Redact at the boundary, not at the source

Keep failure translation at the boundary that exposes the message. Choose that boundary
from the route you are changing:

| Route | Failure handling and obligation |
| --- | --- |
| Registered CLI `diff`, `sync`, `apply` | The CLI calls `SyncClient` over HTTP. The client replaces transport failures with fixed errors and suppresses their causes. It constructs API errors from classification fields rather than returning the response's message text. Preserve those choices when adding client errors. |
| Sync HTTP API | `service/app.py` formats error responses and handles unhandled request failures. `RunService` redacts provider observation text before returning run resources. Keep response and observation handling safe independently of worker exception handling. |
| Registered service worker | `service_sync_run` calls `execute_run` for its stages. Its failure path records available evidence, then raises a sanitized exception to Prefect. Extend this worker boundary when adding stage failures. |
| Direct Python and direct Prefect | `execute_run` preserves lifecycle exceptions; callers must sanitize them before exposing them remotely. The direct Prefect flow calls `run_remote_request`, which resolves a local configuration and wraps execution failures. This integration executes plans only. |

See the [CLI][cli-source], [HTTP client][client-source], [API handlers][api-source],
[observation rendering][service-source] and [worker failure path][worker-source] for the
implementations. See the [shared execution surface](../knowledge/execution-surface.md)
for the operations each route supports.

The registered CLI is a remote API caller; having credentials in the local shell does
not make a returned failure safe to display. The adapter rule still applies:
[never include credentials in logs or exceptions](writing-an-adapter.md#log-with-structlog-never-secrets).
Boundary redaction also replaces collected values in messages from dependencies that the
adapter did not write.

### Redact the whole cause chain

A traceback can display both the wrapper message and the exception that caused it.
Reuse the helpers in `infrahub_sync/execution.py`:

- `collect_secret_values` gathers known values from the environment and, when supplied,
  a resolved `SyncInstance`.
- `redact` replaces exact occurrences of the supplied values with `***`.
- `sanitize_exception_chain` rebuilds the displayed cause or context chain as
  `RuntimeError` copies, preserving the original type names in redacted messages and
  suppressing the original contexts.

```python
# Bad: the traceback can still display the original cause.
raise RunExecutionError(redact(msg, secrets)) from exc

# Good: both the wrapper and the displayed cause are sanitized.
raise RunExecutionError(redact(msg, secrets)) from sanitize_exception_chain(exc, secrets)
```

When a cause adds no useful information, raise a safe summary `from None` instead.
Test the full `traceback.format_exception(...)` output, including nested causes and
implicit contexts, rather than checking only `str(error)`.

`run_remote_request` uses this wrapping pattern for execution failures and passes
`RunValidationError` through unchanged. Validation errors on that route must therefore
be safe when raised. The service worker uses its own boundary, not `run_remote_request`.

Logs need separate handling. `RunLoggerBridge` redacts the formatted message from the
`infrahub_sync` logger hierarchy before forwarding it to the Prefect run logger. The
direct flow installs this bridge; the service worker installs it when a Prefect run
context exists. This is not a filter for every logger, return value or artifact.
See [Prefect logging](../knowledge/orchestration-prefect.md#the-log-bridge).

A boundary that catches `Exception` to sanitize and re-raise it may need a targeted
`# noqa: BLE001` with a comment explaining the translation. Limit that lint suppression to
this boundary. It does not permit discarding a failure instead of re-raising it.

### Collect from the environment by name shape — and from every value's URL userinfo

Endpoint variables such as `NETBOX_ADDRESS` may contain credentials even though
nothing in their names refers to authentication. The collector checks environment values in two ways:

- A variable with a credential-shaped name contributes its whole value.
- Every variable contributes any URL userinfo and password found in its value,
  regardless of the variable name.

Environment name matching is case-insensitive. A name qualifies when it *contains*
`TOKEN`, `PASSWORD`, `PASSWD`, `SECRET`, `CREDENTIAL` or `APIKEY`, *ends with* `_KEY` or
`_AUTH`, or *equals* `KEY`, `AUTH` or `INFRAHUB_API_TOKEN`.

URL userinfo is the `username:password` portion of `scheme://username:password@host`.
The collector adds the whole userinfo string and the password separately, subject to
the [minimum length](#drop-values-below-a-length-floor). It also scans string values in
configuration settings for URL userinfo, except for entries under `*_env_vars` keys.
Those entries are variable names, not strings to scan for userinfo. The collector reads
the referenced values only when the key qualifies as a credential key, including through
inherited context.

### Match key names at a boundary, never as bare substrings

`KEY` and `AUTH` match by suffix or exact name. Matching them anywhere in a name would
also collect ordinary values from `KEYCHAIN`, `SSH_AUTH_SOCK` or these settings:

```yaml
response_key_pattern: "objects"
auth_method: "api-key"
```

Redacting those values would obscure useful diagnostics, such as which authentication
method requires a token.

For configuration settings, the collector converts each key to lowercase and checks whether it
*contains* `token`, `password`, `passwd`, `secret`, `credential`, `apikey` or
`authorization`, *ends with* `_key` or `_auth`, or *equals* `key` or `auth`.

### Walk settings recursively, with a cycle guard and a depth cap

Credentials can occur in nested `source`, `destination` and optional `store` settings.
The shared collector applies these rules to all three settings blocks:

- **Inherited context.** Values beneath a credential-shaped key remain candidates,
  including plainly named entries inside a `credentials` mapping.
- **Bounded traversal.** The collector tracks `(id(container), secret_context)` to stop cycles
  while allowing the same YAML alias under both ordinary and credential-shaped keys.
  It stops descending into containers at depth 64. Values beyond that limit may be missed.
- **Supported scalars.** The collector accepts strings and converts `int`, `float` and `Decimal`
  values to text. It skips `None`, boolean values and unsupported leaf objects.
- **Environment references.** For a qualifying `*_env_vars` key, the collector reads
  the values of the named environment variables, not their names.

`run_remote_request` and `service_sync_run` collect environment values before
configuration resolution, then refresh the collection with settings after resolution. Do not assume inline settings values
have been collected before that refresh.

### Drop values below a length floor

`collect_secret_values` drops candidate values shorter than six characters. This avoids
replacing every `1` in a diagnostic when a variable such as `SKIP_TOKEN=1` qualifies by
name. The returned values are ordered longest first so overlapping secrets are replaced
in that order.

**The cutoff is a collection limit, not a guarantee that short credentials are safe.**
A password shorter than six characters is not collected on its own. A longer URL
userinfo string containing it may be collected, but the standalone password can remain
visible elsewhere.

`redact` performs exact string replacement; it does not discover additional secrets or
decode transformed values. Do not assume that an encoded value, a value under an unmatched
settings key or a value omitted by the collection limits will be masked. Omit sensitive detail from
new messages and test the values the changed route can expose.

### Never chain a validation library's raw detail

A validation error can include the input it rejected. Validation happens before a usable
configuration exists. At that point, the collector has no settings values from the invalid
file. It cannot reliably sanitize credentials it has not collected.

For direct configuration resolution, `resolve_sync_instance` reports the logical name
and, for a matched invalid file, its path. It suppresses the original validation cause
with `from None`. Preserve that summary instead of chaining Pydantic's raw detail.
For HTTP request validation, the API returns a fixed `request-invalid` message rather
than the rejected body. See the [resolver][resolver-source] and [API handlers][api-source].

### Anti-patterns

| Anti-pattern | Do instead |
| --- | --- |
| Sanitizing only the wrapper message | Sanitize the displayed cause chain or suppress it with `from None`. |
| Treating a CLI failure as local engine output | Trace the HTTP client, API and worker boundaries separately. |
| Assuming collected values cover every credential | Test short, uncollected and transformed values at the changed output. |
| Relying on exception redaction for logs | Check the logger and forwarding path used by the failing code. |
| Returning validation detail from an invalid configuration | Return a bounded summary and suppress the original cause. |

### Verifying it

Use synthetic credential values in the environment and configuration, then exercise a
failure through the route you changed. Inspect every output that route exposes: rendered
tracebacks, API responses, Prefect logs, state messages and retained failure records.
A check of one wrapper string does not establish that the other outputs are safe.

Cover nested causes, URL userinfo in endpoint variables, nested settings and environment
references. Check that ordinary diagnostics remain readable, and make the collection
limits explicit in tests. Review changes to a collector against both missed values and
unnecessary replacements.

Existing examples are in [execution tests][execution-tests],
[service worker tests][worker-tests] and [direct Prefect tests][flow-tests].
Follow the [testing guidelines](testing.md) when changing those contracts.

### See also

- [The shared execution surface](../knowledge/execution-surface.md) — callers, operations and failure types.
- [Prefect orchestration](../knowledge/orchestration-prefect.md) — registered and direct flows, including log forwarding.
- [Writing an adapter](writing-an-adapter.md) — credential handling inside an adapter.

[execution-source]: https://github.com/opsmill/infrahub-sync/blob/f98a845986d1f03503d321ce5561b65a3946bf74/infrahub_sync/execution.py#L243-L486
[cli-source]: https://github.com/opsmill/infrahub-sync/blob/f98a845986d1f03503d321ce5561b65a3946bf74/infrahub_sync/cli.py#L519-L679
[client-source]: https://github.com/opsmill/infrahub-sync/blob/f98a845986d1f03503d321ce5561b65a3946bf74/infrahub_sync/client/client.py#L552-L628
[api-source]: https://github.com/opsmill/infrahub-sync/blob/f98a845986d1f03503d321ce5561b65a3946bf74/infrahub_sync/service/app.py#L101-L166
[service-source]: https://github.com/opsmill/infrahub-sync/blob/f98a845986d1f03503d321ce5561b65a3946bf74/infrahub_sync/service/service.py#L887-L946
[worker-source]: https://github.com/opsmill/infrahub-sync/blob/f98a845986d1f03503d321ce5561b65a3946bf74/infrahub_sync/service/flow.py#L805-L884
[resolver-source]: https://github.com/opsmill/infrahub-sync/blob/f98a845986d1f03503d321ce5561b65a3946bf74/infrahub_sync/execution.py#L600-L635
[execution-tests]: https://github.com/opsmill/infrahub-sync/blob/f98a845986d1f03503d321ce5561b65a3946bf74/tests/test_execution_surface.py
[worker-tests]: https://github.com/opsmill/infrahub-sync/blob/f98a845986d1f03503d321ce5561b65a3946bf74/tests/service/test_flow_and_prefect.py
[flow-tests]: https://github.com/opsmill/infrahub-sync/blob/f98a845986d1f03503d321ce5561b65a3946bf74/tests/orchestration/test_flow.py
