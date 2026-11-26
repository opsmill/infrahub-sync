# Data model

## Connection settings

`url` defaults to the commercial HTTPS origin. `auth_method` selects token or service_app. Credential settings name environment variables only. Positive timeout, nonnegative retries, positive page_size and max_retry_delay, optional org_id and arbitrary params are validated. `resources` maps model names to resource options. Unknown settings and unsafe URLs are rejected.

## Resource options

`response_key` defaults to items (null selects a single object or bare list); resource params override connection params and mapping-path queries. Null removes a parameter on every page. Repeated mapping-path query keys retain all values unless explicitly overridden. `page_size` overrides the connection default. Optional `parent` names a previously loaded mapping. Endpoint/detail templates use `{field.path}`; query references use `{env: NAME}` or `{parent: field.path}`. `details` specifies a per-record GET endpoint. `read_only` marks source models with the existing DiffSync IGNORE flag; the inventory example does not enable it.

## Inventory example

| Destination kind | Webex endpoint | Identifier | Peer |
| --- | --- | --- | --- |
| WebexLocation | /v1/locations | webex_id from id | None |
| WebexWorkspace | /v1/workspaces | webex_id from id | location from locationId, referencing WebexLocation |
| WebexDevice | /v1/devices | webex_id from id | workspace from workspaceId, referencing WebexWorkspace |

Locations map name; workspaces and devices map displayName to name. Devices also map serial, product, software, ip and connectionStatus to destination inventory attributes. The explicit order loads locations, then workspaces, then devices. Display-name changes preserve identity. Optional peers allow workspaces without a location and devices assigned to a person.

## Destination prerequisites and limitation

The accompanying `examples/webex_workspaces_to_infrahub/schema.yml` defines the three kinds, required unique Text webex_id attributes and optional peer relationships. Load that schema before generation. The project can create the complete inventory in an empty destination and does not preserve unmatched destination models through a retention flag.

The existing Infrahub destination implements create and update but no persisted delete. A removed device produces a delete diff and leaves the DiffSync store during sync; its Infrahub node remains and returns on the next load. The example tests and user documentation state that limitation.
