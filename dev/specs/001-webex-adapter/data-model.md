# Data model

## Connection settings

`url` defaults to the commercial HTTPS origin. `auth_method` selects token or service_app. Credential settings name environment variables only. Positive timeout, nonnegative retries, positive page_size, optional org_id and arbitrary params are validated. `resources` maps model names to resource options. Unknown settings and unsafe URLs are rejected.

## Resource options

`response_key` defaults to items (null selects a single object or bare list); `params` override connection params; `page_size` overrides the connection default. Optional `parent` names a previously loaded mapping. Endpoint/detail templates use `{field.path}`; query references use `{env: NAME}` or `{parent: field.path}`. `details` specifies a per-record GET endpoint. `read_only` marks source models with the existing DiffSync IGNORE flag.

## Meeting-room example

Reference-only LocationSite: webex_location_id ← id. LocationMeetingRoom: identifier name ← displayName, room_number ← final slash segment, parent ← locationId as a scalar site unique key. Only meetingRoom workspaces with a location ID survive the existing filters. Destination-only rooms remain; renamed names create new rooms.

## Destination prerequisites and limitation

Sites already exist and their Webex location IDs are prepared separately. Every relevant destination site identifier must be populated and unique. Existing Infrahub loading of optional null/blank identifiers is a known limitation and is unchanged; no skipping or uniqueness guarantee is added by this adapter.
