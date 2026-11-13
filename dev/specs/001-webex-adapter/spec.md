# Feature Specification: Webex Source Adapter

**Feature Branch**: `016-webex-adapter`

**Created**: 2026-11-05

**Status**: Draft

**Input**: User description: "I want to create an adapter for webex to be able to sync data from webex into other platforms. We need it to be flexible enough"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Load Webex devices into a destination and review the differences (Priority: P1)

A network or collaboration team manages Webex room devices, desk devices and phones in Webex Control Hub. They want the same inventory in their source of truth (Infrahub, or another supported destination) so that devices sit next to the locations, circuits and network devices they already track. The operator writes a sync project configuration that names Webex as the source, maps Webex devices to a destination node kind, and runs `list` and `diff` to view what a sync would create, update or delete before anything is written.

**Why this priority**: Reading Webex data and showing a diff is the smallest piece of work that gives the team value: they can compare Webex against their source of truth without changing either system. Every later story depends on this read path.

**Independent Test**: Configure a sync project with Webex as the source and one mapping (devices), point it at a Webex organization that has known devices, run `diff`, and check that every device in Webex appears as a node to create in the destination with the mapped attributes.

**Acceptance Scenarios**:

1. **Given** a sync project with Webex as the source and valid credentials, **When** the operator runs `list`, **Then** the project appears with Webex named as its source.
2. **Given** a Webex organization with 25 devices and an empty destination, **When** the operator runs `diff`, **Then** the diff shows 25 nodes to create, each with the attributes named in the mapping.
3. **Given** the same project, **When** the operator runs `sync` with approval, **Then** the destination holds 25 device nodes, and a second `diff` shows no changes.

---

### User Story 2 - Sync data from any Webex API through configuration only (Priority: P2)

Teams track different Webex data. One team needs devices only. Another needs locations, workspaces, devices and phone numbers, linked to each other. A third needs data from Webex Calling, Meetings or Messaging APIs. The operator adds or changes a mapping in the sync project configuration to pick a Webex API endpoint, the fields to copy, filters to skip records, and transforms to rewrite values. The operator does not change or release any code to read from a Webex API that no example uses.

**Why this priority**: The user asked for the adapter to support any Webex API. Webex APIs differ in how they return data: some return a list of records, some return one record, some place the records under a different key in the response, and some need the identifier of another record in the address (for example, the settings of each location). The mapping must cover all of these cases. This is what lets one adapter serve teams with different needs.

**Independent Test**: Starting from the project in User Story 1, add mappings for Webex locations and workspaces with no code change, set the device mapping to reference its workspace as a peer, run `diff`, and check that devices, workspaces and locations appear with their peer relationships set.

**Acceptance Scenarios**:

1. **Given** a mapping that names a Webex API endpoint not used in any example, **When** the operator runs `diff`, **Then** records from that endpoint load and appear in the diff with no code change.
2. **Given** a Webex API endpoint that returns its records under a key other than the default, **When** the operator names that key in the mapping and runs `diff`, **Then** the records under that key load.
3. **Given** a Webex API endpoint that returns one record instead of a list, **When** the operator maps it and runs `diff`, **Then** that one record loads.
4. **Given** a mapping whose endpoint address contains the identifier of a location, **When** the operator names the location mapping as its parent and runs `diff`, **Then** the adapter requests the endpoint once for each loaded location and loads the records from every request.
5. **Given** a workspace mapping for the Webex Calling "List Workspaces" endpoint with a fixed query parameter (for example, a workspace type), **When** the operator runs `diff`, **Then** the adapter sends that parameter on every request and only the matching workspaces load.
6. **Given** a workspace mapping whose location query parameter takes its value from the location mapping, **When** the operator runs `diff`, **Then** the adapter requests "List Workspaces" once for each loaded location, with that location's identifier as the parameter value.
7. **Given** a query parameter set once in the source settings (for example, the organization identifier), **When** the operator runs `diff`, **Then** the adapter sends it on every request of every mapping, and a value set in a mapping replaces it for that mapping.
8. **Given** a device mapping that asks for each device's full details with a second request per device, **When** the operator runs `diff`, **Then** each device record holds the fields from the details request.
9. **Given** a device mapping with a filter that keeps only devices whose connection status is "connected", **When** the operator runs `diff`, **Then** only connected devices appear.
10. **Given** a device mapping that references the workspace mapping, **When** the operator runs `sync`, **Then** each device node in the destination has its workspace node set as a peer, and workspaces are written before devices.
11. **Given** a mapping with a transform that rewrites a device name to lower case, **When** the operator runs `diff`, **Then** the diff shows the lower-case name.

---

### User Story 3 - Run the sync unattended on a schedule (Priority: P3)

Once the mappings are correct, the team runs the sync on a schedule (for example each night) so the destination follows changes in Webex: new devices, moved devices and removed devices. Nobody is present to refresh credentials or restart a failed run.

**Why this priority**: Scheduled runs keep the destination current, but they only matter after the read path and the mappings work.

**Independent Test**: Run the sync twice with no change in Webex and confirm the second run makes no changes. Then remove one device in Webex, run again, and confirm the destination removes only that device.

**Acceptance Scenarios**:

1. **Given** a destination already in line with Webex, **When** the scheduled sync runs, **Then** it reports no changes.
2. **Given** a Webex service app authorized for the organization, and a run that starts after the last access token has expired, **When** the scheduled sync runs, **Then** the adapter obtains a new access token and the run authenticates without a person entering a new token
3. **Given** Webex returns a rate-limit response during a run, **When** the adapter receives it, **Then** the adapter waits for the period Webex asks for and continues, and the run completes with every record loaded.

---

### User Story 4 - Name the Webex organization to read from (Priority: P4)

A partner or managed service provider administers several Webex customer organizations. They set up one sync project per customer and name that customer's Webex organization identifier in each project, so each project loads only that customer's data.

**Why this priority**: This matters only to teams whose credential can read more than one Webex organization. A team with one organization reads the organization the credential belongs to and does not set anything.

**Independent Test**: Configure two sync projects that name two different organization identifiers, run `diff` on each, and check that each diff holds only the records of its own organization.

**Acceptance Scenarios**:

1. **Given** a sync project that names an organization identifier, **When** the operator runs `diff`, **Then** only records from that organization load.
2. **Given** a sync project that names no organization identifier, **When** the operator runs `diff`, **Then** records from the organization the credential belongs to load.

---

### Edge Cases

- A Webex resource returns more records than fit in one page: the adapter follows every page until the last one, and the record count in the diff equals the count in Webex.
- A Webex resource returns zero records: the adapter loads nothing for that mapping and the run continues; in `sync`, the destination nodes for that mapping are deleted only according to the existing deletion rules of the sync engine.
- The service app's refresh token is expired or revoked: the run stops with a message that says the service app must be authorized again, without printing any credential.
- The access token is expired, revoked or wrong (Webex returns 401) and cannot be renewed: the run stops with a message that says authentication failed and names the source, without printing the token.
- The token lacks the permission for one resource (Webex returns 403): the run stops with a message that names the resource and states that the credential lacks permission for it.
- Webex returns a rate-limit response (429) with a wait period: the adapter waits and retries; if the limit continues past a configured number of retries, the run stops with a message that says so.
- Webex does not respond within the timeout: the adapter retries a configured number of times, then stops with a message that names the resource and the timeout.
- A mapping requests an endpoint once per parent record (for example, once per location in an organization with 500 locations): the run makes one request per parent, waits on rate-limit responses as for any other request, and logs the number of requests made.
- One of the per-parent requests fails with 404 because the parent has no data for that endpoint (for example, a location without Webex Calling): the adapter skips that parent, logs it, and continues.
- Webex rejects a query parameter (400): the run stops with a message that names the endpoint and repeats the error text Webex returned, without the credential.
- A query parameter takes its value from an environment variable that is not set: the run stops before sending any request, with a message that names the variable.
- A query parameter takes its value from a parent field that is empty for one parent record: the adapter skips the request for that parent and logs it.
- The record key named in the mapping is not in the response: the run stops with a message that names the endpoint and the missing key.
- A record lacks a field named in the mapping: the field is empty in the loaded record, and filters and transforms handle it as they do for other adapters.
- A record references a peer that the run did not load (for example a device whose workspace is filtered out): the adapter follows the existing sync engine behavior for missing peers.
- The organization uses a Webex service at a base address other than the commercial default (for example a government service): the operator sets the base address in the configuration.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Operators MUST be able to name Webex as the source of a sync project in the same way as the other built-in adapters.
- **FR-002**: Operators MUST be able to map any Webex API endpoint that returns data to a destination node kind by naming the endpoint in the mapping, with no code change. This includes endpoints from the Webex Admin, Devices, Calling, Meetings and Messaging APIs.
- **FR-002a**: Operators MUST be able to name the key in the response that holds the records, for endpoints that do not use the default key. The adapter MUST also load endpoints that return one record instead of a list.
- **FR-002b**: Operators MUST be able to name another mapping as the parent of a mapping, so that the adapter requests the endpoint once for each parent record and inserts the parent's identifier, or another field of the parent, into the endpoint address or into a query parameter.
- **FR-002c**: Operators MUST be able to ask for the full details of each record with a second request per record, and merge the fields from that request into the record.
- **FR-003**: The adapter MUST apply the mapping's fields, filters, transforms, identifiers and peer references in the same way as the other adapters in this project.
- **FR-004**: The adapter MUST read Webex credentials from environment variables or a secret manager, and MUST support two credential types, chosen in the configuration:
    - A Webex service app: the adapter reads the client ID, client secret and refresh token, obtains an access token, and obtains a new one when the current one expires or Webex returns 401 for an expired token. This is the credential type for scheduled runs.
    - A fixed access token supplied by the operator, for testing and short runs. The adapter does not renew it.
- **FR-005**: The adapter MUST NOT print, log or include credentials in error messages or tracebacks.
- **FR-006**: The adapter MUST follow Webex pagination until every record of a resource is loaded.
- **FR-007**: The adapter MUST wait and retry when Webex returns a rate-limit response, honoring the wait period Webex returns, up to a configured number of retries.
- **FR-008**: The adapter MUST stop with a clear message that names the source and the resource when Webex returns an authentication failure (401), a permission failure (403), or does not respond within the configured timeout.
- **FR-009**: Operators MUST be able to set the Webex base address, the request timeout and the number of retries in the configuration, with defaults that work for the commercial Webex service.
- **FR-010**: Operators MUST be able to set any query parameter that a Webex endpoint accepts, by name, in the mapping, so that Webex returns only the records they need. The adapter MUST send the parameter name and value as the operator wrote them, without checking them against a fixed list of names, so that parameters Webex adds later work with no code change. Example: the parameters of the Webex Calling "List Workspaces" endpoint (https://developer.webex.com/calling/docs/api/v1/workspaces/list-workspaces).
- **FR-010a**: A query parameter value MUST be able to come from a fixed value in the configuration, from an environment variable, or from a field of a parent record (FR-002b).
- **FR-010b**: Operators MUST be able to set query parameters once in the source settings so that the adapter sends them on every request. A parameter of the same name in a mapping MUST replace the source-level value for that mapping.
- **FR-010c**: The adapter MUST manage the pagination query parameters itself. Operators MUST be able to set the page size, and a page size or start position set by the operator MUST NOT stop the adapter from loading every page.
- **FR-010d**: The adapter MUST send boolean values and lists in the form Webex expects for query parameters, and MUST encode parameter values so that spaces and special characters reach Webex unchanged.
- **FR-011**: The adapter MUST read from one Webex organization per sync project. Operators MUST be able to name that organization's identifier in the configuration or in an environment variable; when they do not, the adapter reads the organization the credential belongs to. The adapter MUST send the identifier on every request to endpoints that accept it.
- **FR-012**: The adapter MUST send only read requests to Webex, whatever endpoint the operator names. No command (`list`, `diff`, `generate` or `sync`) changes data in Webex.
- **FR-013**: The project MUST include a sanitized example sync project that maps Webex locations, workspaces and devices to destination nodes, with peer references between them.
- **FR-014**: The project MUST include a documentation page for the Webex adapter that lists the configuration settings, the required environment variables, the Webex permissions needed for the example resources, and the expected error messages.

### Key Entities

These are the Webex resources the example and documentation cover. FR-002 allows data from any other Webex API endpoint to be mapped from configuration.

- **Organization**: the Webex customer organization that owns all other records.
- **Location**: a physical site in the organization, with an address and time zone. Workspaces and phone numbers belong to a location.
- **Workspace**: a room or shared space that holds devices. Belongs to a location.
- **Device**: a Webex room device, desk device or phone, with a serial number, model, software version, IP address and connection status. Belongs to a workspace or to a person.
- **Person**: a user in the organization. A device can belong to a person instead of a workspace.
- **Phone number**: a telephone number assigned in Webex Calling, belonging to a location and optionally to a person or workspace.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An operator who knows how to write a sync project adds data from a new Webex API endpoint to an existing project, by editing configuration only, in under 15 minutes.
- **SC-002**: For an organization with at least 1,000 devices, the number of device records loaded equals the number of devices Webex reports for that organization.
- **SC-003**: Running the sync a second time with no change in Webex reports zero changes.
- **SC-004**: When Webex returns rate-limit responses during a run, the run completes with every record loaded and no manual restart.
- **SC-005**: In tests that cover authentication failure, permission failure, timeout and rate limiting, every failure message names the source and the resource, and no output or log contains a credential.
- **SC-006**: The example sync project runs `list` and `diff` against a Webex organization and produces a diff with no further changes to the example.

## Assumptions

- The adapter reads from Webex only. Writing data into Webex (Webex as a destination) is out of scope because the request is to sync data "from webex into other platforms".
- "Webex" means the cloud service administered in Webex Control Hub. On-premises Cisco call control products are out of scope.
- The credential types (FR-004) and the one-organization-per-project rule (FR-011) were set on 2026-11-05, after the user supplied a Webex service app and an organization identifier for testing. Reading several organizations in one sync project is out of scope.
- If the service app's refresh token expires or is revoked, an administrator must authorize the service app again in Webex; the adapter cannot do this itself. The plan must confirm how long a service app refresh token stays valid.
- The query parameter requirements (FR-010 to FR-010d) were added on 2026-11-05 after the user asked for query parameter support, using "List Workspaces" as the example. The parameter names on that page were not reviewed when this spec was written, because the page could not be loaded; the plan must check them.
- "Flexible enough" means the adapter supports any Webex API: any endpoint that returns data can be mapped from configuration (FR-002 to FR-002c, FR-010). The user confirmed this on 2026-11-05 ("we want to be able to support any webex api").
- The adapter follows the structure, contract and documentation rules of existing adapters described in `dev/knowledge/adapter-anatomy.md`, `dev/guidelines/writing-an-adapter.md` and the project constitution, including the read-only `list` and `diff` path.
- Change tracking for incremental runs (only loading records changed since the last run) is out of scope. Each run reads every mapped resource in full.
- The operator holds a Webex administrator role with read access to the resources they map. The exact permissions per resource are documented under FR-014.
- Webex Meetings, Messaging and call history data can be mapped through FR-002, but the example sync project focuses on the inventory resources listed under Key Entities. The documentation includes at least one example each of a per-parent endpoint (FR-002b) and a per-record details request (FR-002c).
