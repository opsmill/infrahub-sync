# Contract: `entraid` source configuration

The adapter's public interface is the `source` block of a sync `config.yml`, the environment variables, the
`schema_mapping` conventions and the error messages.

## Source block

```yaml
source:
  name: entraid
  settings:
    auth: client_secret            # client_secret (default) | default
    tenant_id: "00000000-0000-0000-0000-000000000000"   # optional; ENTRAID_TENANT_ID wins
    client_id: "11111111-1111-1111-1111-111111111111"   # optional; ENTRAID_CLIENT_ID wins
    graph_url: "https://graph.microsoft.com/v1.0"       # default
    page_size: 999                 # 1-999
    timeout: 60                    # seconds
    max_retries: 5                 # 0-10, for HTTP 429 and 503
    membership: direct             # direct (default) | transitive
    select:                        # extra Graph properties per resource
      users: [department]
    collections:
      accounts:
        resource: users
        filter: "accountEnabled eq true"
      security_groups:
        resource: groups
        filter: "securityEnabled eq true and NOT groupTypes/any(c:c eq 'Unified')"
        advanced_query: true
```

## Environment variables

| Variable | Used when | Content |
| --- | --- | --- |
| `ENTRAID_TENANT_ID` | `auth: client_secret` | Directory (tenant) ID; overrides `tenant_id` |
| `ENTRAID_CLIENT_ID` | `auth: client_secret` | Application (client) ID; overrides `client_id` |
| `ENTRAID_CLIENT_SECRET` | `auth: client_secret` | Client secret; the only accepted source |
| `AZURE_*` and the Azure CLI login | `auth: default` | Read by `DefaultAzureCredential` |

## schema_mapping conventions

- `mapping`: a collection name, or `users` / `groups` for an unfiltered read.
- Field `mapping`: a Graph property name; dots reach nested properties
  (`onPremisesExtensionAttributes.extensionAttribute1`).
- Field `mapping: members` with `reference: <users kind>` on a groups entry: the group's user members.
- `filters` and `transforms`: the shared behavior of every adapter, applied to a copy of each Graph record.
- A Graph property used only inside a transform must be added with `settings.select`.

## Graph requests

| Purpose | Request |
| --- | --- |
| Collection | `GET {graph_url}/{users\|groups}?$select=…&$top={page_size}[&$filter=…][&$count=true]`, header `ConsistencyLevel: eventual` on every page when `advanced_query` |
| Next page | `GET {@odata.nextLink}` |
| Direct members | `GET {graph_url}/groups/{id}/members?$select=id&$top={page_size}` |
| Transitive members | `GET {graph_url}/groups/{id}/transitiveMembers?$select=id&$top={page_size}` |

## Errors

| Condition | Exception | Message contains |
| --- | --- | --- |
| Used as destination | `EntraidConfigError` | "can only be the source" |
| Unknown or invalid setting | `EntraidConfigError` | the setting name and allowed values |
| `client_secret` in settings | `EntraidConfigError` | `ENTRAID_CLIENT_SECRET` |
| Missing tenant, client ID or secret | `EntraidConfigError` | the missing environment variable names |
| Unknown collection in `schema_mapping` | `EntraidConfigError` | the entry name and the valid names |
| HTTP 401 / 403 | `EntraidPermissionError` | `User.Read.All`, `Group.Read.All`, `GroupMember.Read.All` |
| Credential cannot get a token | `EntraidAuthenticationError` | the `auth` mode and the variables or login to check |
| Groups field `mapping: members` without `reference` | `EntraidConfigError` | the entry and field name |
| SDK not installed | `ModuleNotFoundError` (Python) | documented with `pip install msgraph-sdk azure-identity` |
| HTTP 429 / 503 after `max_retries` | `EntraidGraphError` | status and request path |
| Timeout | `EntraidGraphError` | request path and timeout |
| List property mapped to a single attribute | `EntraidConfigError` | the field and "transform" |

## Logging

Logger `infrahub_sync.adapters.entraid`, through `structlog`. INFO: per collection read (records, requests,
retries); one summary after all reads (collections, member requests, total requests, retries); per kind loaded (loaded, read). WARNING: once per kind with dropped member references (count); each retry
wait. Never tokens, secrets or authorization headers.
