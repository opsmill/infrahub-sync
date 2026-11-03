# Data model: Microsoft Entra ID source adapter

All types live in `infrahub_sync/adapters/entraid.py`.

## EntraidSettings (frozen dataclass)

Built from `source.settings` by `EntraidSettings.from_settings(settings, environ=os.environ)`. Every check fails
with `EntraidConfigError` and a message that names the setting.

| Field | Type | Default | Validation |
| --- | --- | --- | --- |
| `auth` | `str` | `client_secret` | `client_secret` or `default` |
| `tenant_id` | `str \| None` | `ENTRAID_TENANT_ID`, else setting | required with `client_secret` |
| `client_id` | `str \| None` | `ENTRAID_CLIENT_ID`, else setting | required with `client_secret` |
| `client_secret` | `str \| None` (`repr=False`) | `ENTRAID_CLIENT_SECRET` only | required with `client_secret`; a `client_secret` key in settings is refused |
| `graph_url` | `str` | `https://graph.microsoft.com/v1.0` | must start with `https://`; trailing `/` removed |
| `page_size` | `int` | 999 | integer, 1 to 999 |
| `timeout` | `float` | 60.0 | number greater than 0 |
| `max_retries` | `int` | 5 | integer, 0 to 10 |
| `membership` | `str` | `direct` | `direct` or `transitive` |
| `select` | `dict[str, tuple[str, ...]]` | `{}` | keys `users` or `groups`; values a string or a list of strings |
| `collections` | `dict[str, Collection]` | `{}` | see Collection |

Unknown keys in `settings` are refused, and the message lists the allowed keys.

Derived: `token_scope` = `<scheme>://<host>/.default` of `graph_url`; `graph_host` = host of `graph_url`.

## Collection (frozen dataclass)

| Field | Type | Default | Validation |
| --- | --- | --- | --- |
| `name` | `str` | key in `collections` | must not be `users` or `groups` |
| `resource` | `str` | required | `users` or `groups` |
| `filter` | `str \| None` | `None` | string when set; sent unchanged as `$filter` |
| `advanced_query` | `bool` | `False` | boolean |

Unknown keys in a collection are refused. The bare names `users` and `groups` resolve to
`Collection(name=<resource>, resource=<resource>)`.

`EntraidSettings.resolve_collection(name) -> Collection` returns the collection, or raises `EntraidConfigError` whose
message lists the valid names (defined collections plus `users` and `groups`).

## Graph record

A `dict[str, Any]` in the Graph wire format (for example `{"id": "…", "displayName": "…",
"userPrincipalName": "…"}`), as returned in the `value` array of a page. `@odata.*` keys stay as Graph returns them.
For a groups collection whose members are needed, the adapter adds `members: list[str]`: the sorted, unique IDs of
the user members.

## Request plan (computed when the adapter starts)

For each collection read by at least one `schema_mapping` entry:

- `select`: `id`, then the top-level property of every field `mapping` without `reference`, and of every `filters`
  field, of every entry that reads the collection, excluding keys produced by that entry's `transforms` and the
  `members` key; then the extra properties in `settings.select[<resource>]`. Order is stable, duplicates removed.
- `needs_members`: true when the resource is `groups` and an entry that reads the collection has a field whose
  `mapping` is `members` and that has a `reference`.

## EntraidGraphClient

Async context manager that owns the `httpx.AsyncClient`, the credential and the SDK request adapter.

| Method | Returns | Behavior |
| --- | --- | --- |
| `list_collection(collection, select)` | `list[dict]` | first request with `$select`, `$top`, `$filter` (when set), `$count=true` and `ConsistencyLevel: eventual` (when advanced); follows `@odata.nextLink`, re-adding the header |
| `user_member_ids(group_id, transitive)` | `list[str]` | `/members` or `/transitiveMembers` with `$select=id`; keeps `@odata.type == "#microsoft.graph.user"`; sorted, unique |
| `requests`, `retries` | `int` | counters for the summary log line |

Errors:

| Exception | Base | Raised when |
| --- | --- | --- |
| `EntraidConfigError` | `ValueError` | invalid setting, unknown collection, a `client_secret` key in settings, use as a destination, a list value mapped to a single attribute, a record without `id` |
| `EntraidGraphError` | `RuntimeError` | a request failed: retries for 429/503 exhausted, timeout, or another HTTP error. Carries `status`, `code`, `path` |
| `EntraidPermissionError` | `EntraidGraphError` | HTTP 401 or 403. Message names `User.Read.All`, `Group.Read.All` or `GroupMember.Read.All` |
| `EntraidAuthenticationError` | `EntraidGraphError` | the credential raised `azure.core.exceptions.ClientAuthenticationError` (includes `CredentialUnavailableError`). Message names the `auth` mode and the environment variables or login to check |

Start-up validation also refuses a groups field with `mapping: members` and no `reference`.

No message or `repr` contains a token or the client secret.

## EntraidAdapter(DiffSyncMixin, Adapter)

- `type = "Entraid"`.
- `__init__(target, adapter, config, *args, transport=None, credential=None, **kwargs)`: raises
  `EntraidConfigError` when `target != "source"`; parses settings; resolves the collection of every
  `schema_mapping` entry that has a `mapping` (unknown name → error before any request); computes the request plan.
  `transport` and `credential` exist for tests.
- `model_loader(model_name, model)`: reads all collections once (R7), copies the records of the entry's collection,
  applies `filter_records` then `transform_records`, converts each record with `record_to_diffsync`, adds it, and logs
  the loaded and read counts. Logs one warning when member references were dropped.
- `record_to_diffsync(record, mapping, model, peers)`: sets `local_id` to the Graph `id`; for each field: `static`
  value; `mapping` + `reference` → peer unique IDs (list field: sorted list; single field: first sorted peer); plain
  `mapping` → `get_value(record, mapping)`, a list value fails with a message asking for a transform.
- `cursor_tier_for` is not overridden (`CursorTier.NONE`).

## EntraidModel(DiffSyncModelMixin, DiffSyncModel)

Base class of the generated models. `create`, `update` and `delete` raise `NotImplementedError` with the message
that the Entra ID adapter is read-only.
