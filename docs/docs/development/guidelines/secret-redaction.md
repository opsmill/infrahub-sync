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

<!-- Extracted from dev/specs/archive/011-prefect-run-observability on 2026-11-09 (Registered service worker row) -->

| Route | Failure handling and obligation |
| --- | --- |
| Registered CLI `plan`, `sync`, `apply` | The CLI calls `SyncClient` over HTTP. The client replaces transport failures with fixed errors and suppresses their causes. It constructs API errors from classification fields rather than returning the response's message text. Preserve those choices when adding client errors. |
| Sync HTTP API | `service/app.py` formats error responses and handles unhandled request failures. `RunService` redacts provider observation text before returning run resources. Keep response and observation handling safe independently of worker exception handling. A route that returns text read from Prefect, such as the lines of `GET /runs/{run_id}/logs`, redacts each message with the Sync API's secrets, removes control characters other than line breaks and tabs, and caps the length after the redaction, so a cut cannot leave the start of a secret. |
| Registered service worker | `service_sync_run` calls `execute_run` for its stages. Its failure path records available evidence. A recognized failure then ends the flow run with a returned `Failed` state whose message is the redacted ERROR line; any other failure raises a sanitized exception to Prefect. A refused execution claim also returns a `Failed` state; its message is fixed text and the run ID, and a run ID that is not of the product run ID form is replaced by the fixed placeholder `<run-id>`. Extend this worker boundary when adding stage failures. Build a worker log line or a Prefect artifact only from named run record fields and fixed templates, never from an exception's text, and pass the finished text through `redact`. Write every line of the service flow through `SyncFlowLines`, which redacts the line and starts it with the logger name `infrahub_sync.service.flow`, then a vertical bar between two spaces; `GET /runs/{run_id}/logs` leaves out a line that does not start this way. The one exception is the ERROR line of a recognized failure, which repeats the `cause` that `operator_cause` recorded: redacted, stripped of control characters and capped. Any other failure's ERROR line, including a failure that was not recorded, holds only the exception's class name. The Prefect artifact never reads a failure's `cause`. A settings check that rejects a value names the setting and never repeats the value. |
| Direct Prefect | `execute_run` preserves lifecycle exceptions; a caller must sanitize them before exposing them remotely. The direct Prefect flow calls `run_remote_request`, which resolves a local configuration and wraps execution failures. This integration executes plans only. |
| Compose start-up (`sync-bootstrap`, the CA trust store, the Compose file) | Keep credentials out of text by construction, not by masking. Pass each credential variable in `docker-compose.yml` only to the services that read it; a new credential goes to the service that reads it and to no other. `bootstrap.py` reports each failure as one fixed name (`refused: <name>`), and a settings check logs a fixed message that names the setting or the error class and never the value; add a new failure as a new fixed name, never as a provider message, an endpoint or a key. A trust store error names the file, and for a file it cannot read the error class, never the file's content. Documentation tells operators to run `docker compose config --quiet`, never a command that prints interpolated values; `tests/compose/test_documentation.py` fails on such a command in an operator page. |

See the [CLI][cli-source], [HTTP client][client-source], [API handlers][api-source],
[observation rendering][service-source] and [worker failure path][worker-source] for the
implementations. See the [shared execution surface](../knowledge/execution-surface.md)
for the operations each route supports.

### Build a run's failure record from a fixed set of fields

A failed run's `results.<stage>_failure` record follows
[ADR 0021](https://github.com/opsmill/infrahub-sync/blob/feature/v3-develop/dev/adr/0021-a-failure-record-holds-a-structured-cause.md).
The keys the record held before that decision (such as `outcome`, `error_type`, `cause`,
`next_action` and the apply record keys) keep their values. Beyond them, the record holds the class name of the error
and of its immediate cause, and only these fields:

- the stage and a failure type from a fixed list
- the system involved: `source` or `destination`, and the adapter name
- Infrahub's error code, node kind, field name and own message, for a write that Infrahub
  rejected
- the permission or message Infrahub returns with a 403, as before
- the kind, the source record and the field that a typed Sync error names
- the operations written, the operation count of the plan and the operation that failed
- for a write that could not take the configuration's write guard, `active_run_id`: the run ID
  read from the label (`application_name`) of the database session that holds the guard, and
  stored only when the run records hold a run with that ID for the same configuration. The
  label is neither private nor trusted. On PostgreSQL 16, a session of another account that is
  not a superuser, connected to the same database, read the holder's label (PostgreSQL's
  `pg_stat_activity` rules decide whether it can), so a guard label must never hold anything
  but a run ID. Any session that can connect to that database can set
  a false label; the check in the run records limits it to a real run of the same
  configuration. Only the run ID is stored, never the label
- text from a fixed template filled with the fields above, setting names, kind names, node
  names, account names, branch names and IDs

`failure_record` and `operator_cause` in `infrahub_sync/service/failure_cause.py` build these
fields. They read exception classes, HTTP status codes and typed attributes, never the text of
an exception. When you add a failure, add a typed attribute to the error and a template to
`failure_record`; never record `str(exc)`, an address, a request header or a traceback. A
`TransformError` message holds the transform expression and its error, so read its `field`
attribute only. An error class without a template records `internal_error` and no cause.

This protection does not depend on replacing values in text, so it also holds for a
credential shorter than the six characters the masking pass needs. The masking pass itself is
unchanged: the cause, the next action and Infrahub's message still go through `redact`. The
worker log and the Prefect logs keep the masked error text until version 3.0.

The registered CLI is a remote API caller; having credentials in the local shell does
not make a returned failure safe to display. The adapter rule still applies:
[never include credentials in logs or exceptions](writing-an-adapter.md#log-with-structlog-never-secrets).
Boundary redaction also replaces collected values in messages from dependencies that the
adapter did not write.

### Build a plan problem's reason from its template

Each problem in a plan, and each line of `plan/report.jsonl`, has a `reason` that the team
reads in `runs plan --problems` and in the Sync API. Render it from the fixed template of its
problem type in `infrahub_sync/plan/problems.py` (`_REASON_TEMPLATES`), filled only with source
values, kind, attribute and relationship names, source record IDs, identities, the choices of a
Dropdown attribute, a count of Infrahub nodes, and the position of a transform or a filter in
its mapping entry, such as `transforms[0]`. Never fill it from the text of an exception, from
connection settings, or from the expression of a transform or a filter. When you add a problem
type, add its template, and extend `tests/plan/test_report_credentials_absent.py` so that a
short credential and a credential that holds `"`, `\` and `,` appear in no file under `plan/`
and in no report entry.

### Store only the error's class name in a progress document

A document that the worker stores while a stage runs, such as the read state of a side
(`prefect_executions.source_read` and `destination_read`), is stored as the worker built it: the
store method takes no list of secrets to mask. The Sync API returns it in the run response. When
such a document records a failure, store `type(exc).__name__` and nothing else from the
exception: no message, no address, no traceback. Log the same failure by class name only. A
class name is a fixed name in the code, so it holds no secret. The reason for the failure
belongs in the run's failure record, which the rules above cover.

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

A `structlog` event reaches the bridge because `infrahub_sync/log.py` delivers it through the
standard `logging` logger of the same name, with each key-value pair written into the message
as `key=value`. A string value is written as it is, so the bridge finds a collected secret in
it. A string inside a mapping, a list, a tuple or a set is also written as it is, without
quotes. Any other value is written with `str`; when the `str` of a value escapes the strings it
holds, as the `str` of a `dataclass` instance does, the bridge can miss a secret that contains a quote, a
backslash or a control character, so pass such a value as a string. An application that
calls `structlog.configure` itself replaces this delivery, and then the bridge receives only
what that configuration sends to standard `logging`.
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
name.

`redact` does not depend on the order it receives. It drops empty values and repeated values, then
replaces the longest value first. A value that contains another is therefore masked whole.
Merging secret lists, for example with `tuple(dict.fromkeys(...))`, does not need to
preserve any order. A new function that replaces values one after another must pass its
values through `redaction_order` for the same reason.

**The cutoff is a collection limit, not a guarantee that short credentials are safe.**
A password shorter than six characters is not collected on its own. A longer URL
userinfo string containing it may be collected, but the standalone password can remain
visible elsewhere.

`redact` performs exact string replacement; it does not discover additional secrets or
decode transformed values. Do not assume that an encoded value, a value under an unmatched
settings key or a value omitted by the collection limits will be masked. Omit sensitive detail from
new messages and test the values the changed route can expose.

A public artifact is the exception, because its bytes are serialized. A JSON serializer
escapes a quote, a backslash or a control character, and can re-encode a non-ASCII
letter. The raw value then never appears in the bytes. `publish_artifact` therefore
decodes a JSON artifact and redacts the decoded strings, mapping keys and numbers. It
checks numbers against both their original JSON token and their normalized Python JSON
spelling. A match anywhere in either spelling replaces the whole numeric value with the
string `"***"`, preventing secrets from corrupting numeric tokens during the JSON pass.
Numbers without a match remain numeric. It re-serializes the document only when a value was
replaced. Every public artifact then gets a byte pass over the raw value and its JSON
string forms, which `json_string_forms` derives. A secret that spans a number and
following punctuation can still corrupt JSON during this byte pass; for example,
`"234567,"` can change `{"a":1234567,"b":2}` into invalid JSON. The recorded digest and size are
computed from the redacted bytes that are stored. Internal artifacts are never redacted; see
[ADR 0011](https://github.com/opsmill/infrahub-sync/blob/feature/v3-develop/dev/adr/0011-internal-run-bundles-are-private-and-byte-stable.md).

### Never chain a validation library's raw detail

A validation error can include the input it rejected. Validation happens before a usable
configuration exists. At that point, the collector has no settings values from the invalid
file. It cannot reliably sanitize credentials it has not collected.

For direct configuration resolution, `resolve_sync_instance` reports the logical name
and, for a matched invalid file, its path. It suppresses the original validation cause
with `from None`. Preserve that summary instead of chaining Pydantic's raw detail.
For HTTP request validation, the API returns a fixed `request-invalid` message rather
than the rejected body. See the [resolver][resolver-source] and [API handlers][api-source].

The same applies to a parser. A YAML error message can quote the line that failed, and that
line can hold a token. When a command reports a parse error in a file that can hold a
credential, report only the position, as `configs convert` does: "error: config.yml is not
valid YAML at line 12, column 3". Take the line and the column from the error's mark, and drop
the message.

### Check every free-text field of a request for token values

<!-- Extracted from dev/specs/archive/022-local-accounts on 2026-11-18 -->

The product store holds only the SHA-256 digest of each API token and session token, so no
collector can list their values. A free-text field that a record keeps, such as `reason` or the
name of an API token, can therefore hold a token that redaction would not find.

When you add a free-text field to a request of the Sync API:

- pass it through `is_free_text` in `infrahub_sync/service/service.py` before anything records
  it. The function refuses a known secret value and the prefixes `ifs_pat_` and `ifs_ses_`;
- refuse it with `422 secret-parameter-refused`, and write the refusal event without the
  refused text;
- give it a maximum length; every `reason` field has at most 512 characters
  (`REASON_MAX_LENGTH` in `infrahub_sync/client/models.py`).

Do not add the password of `admin` to a list of secret values. `serve.py` removes it from the
process environment after it applies it, so that no free-text check can be used to test
candidate passwords. See [Local accounts and sign-in](../knowledge/local-accounts.md).

### Register a stored credential value before you return it

The values of stored credentials are in no environment variable and in no configuration
setting, so `collect_secret_values` cannot find them. A value enters a process in one of three
ways, and each one adds it to a redaction registry before any other code receives it ([ADR 0057](https://github.com/opsmill/infrahub-sync/blob/feature/v3-develop/dev/adr/0057-the-sync-api-stores-credentials-and-releases-them-to-the-worker-account.md)):

- **A request that creates a credential or changes its value.** `CredentialActions` in
  `service/credential_actions.py` calls `register_secret_values` with the submitted values
  before any other check, so every message that the request writes afterwards is redacted.
- **A decryption in the Sync API.** `CredentialCipher.decrypt` in
  `service/credential_crypto.py` adds each decrypted value before it returns the fields.
- **A release to the worker.** `release_run_credentials` in `service/credential_client.py` adds
  every released value before it checks the answer and before it returns.

The registry is one list, bound with `execution.redaction_scope`. `service/app.py` binds a new
one for each request of the Sync API, and `service_sync_run` binds one for the whole flow run;
the worker's log bridge and its failure boundary read that list at each use. A long-lived
service holds a `LiveSecretValues`, which adds the values of the current registry to its fixed
values at each read.

Rules for new code:

- Call `register_secret_values` before the function that decrypted or received a value returns
  it. Outside a scope it raises `NoRedactionScopeError` and the caller must not use the value.
  The credential client makes that call before it sends its request, so no value is received
  without a registry.
- Bind the scope above every thread or task that can add a value. A thread started without the
  context has no registry.
- A value shorter than `MIN_SECRET_LENGTH` is not added, so it is not redacted. Keep such a
  value out of text by construction; see [Drop values below a length floor](#drop-values-below-a-length-floor).
- Never build a message from the text of an answer of the worker route. `CredentialUnavailableError`
  writes its message from the reason, the refusal code and the names that the refusal gives.
- Do not give the worker route that releases values an `Idempotency-Key`: a mutation receipt
  stores the whole answer. The receipt of a credential request for accounts stores the credential
  resource, which holds no value, and a keyed fingerprint of the request
  (`CredentialCipher.request_fingerprint`).
- In a test, compare digests of values and never put a value in an assertion message.
  `decrypt_stored_credential` in `tests/helpers/stored_credentials.py` returns values that are
  in no redaction list after it returns.

`INFRAHUB_SYNC_WORKER_TOKEN` and `INFRAHUB_SYNC_ENCRYPTION_KEY` are environment variables with
credential-shaped names, so the environment collector takes their values in a process that
holds them. `StoreAccountResolver.secret_values` also gives the tokens of `admin` and `worker`
to the boundary redaction of the Sync API.

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
