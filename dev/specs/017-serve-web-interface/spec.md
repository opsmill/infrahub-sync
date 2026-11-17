# Feature Specification: Open the web interface from the address of the Sync API

**Feature Branch**: `017-serve-web-interface`

**Created**: 2026-11-16

**Status**: Draft

**Input**: GitHub issue [#421](https://github.com/opsmill/infrahub-sync/issues/421), "service:
serve the web interface from the backend", and two decisions the maintainer made on 2026-11-16
(see "Decisions").

Sources:

- Issue [#421](https://github.com/opsmill/infrahub-sync/issues/421): the need and the context.
- Pull request [#410](https://github.com/opsmill/infrahub-sync/pull/410), which added the web
  interface in `frontend/`.
- [`frontend/README.md`](../../../frontend/README.md), section "Calling the Sync API".
- The decision records in [`dev/adr/`](../../adr/README.md), in particular
  [ADR 0016](../../adr/0016-the-compose-deployment-is-one-tag-pinned-file.md).

## Background

### What a team can do today

- A team deploys the Sync service from the released image, or from the root
  `docker-compose.yml`. The `sync-api` service is the only one that publishes a host port.
- A team runs plans, verifies, approves and applies them with the `infrahub-sync` CLI, which
  reads the address of the Sync API from `INFRAHUB_SYNC_API_URL` and a token from
  `INFRAHUB_SYNC_API_TOKEN`.
- A developer views runs and configurations in the web interface by starting a development
  server next to the service (`pnpm dev`, or `uv run invoke dev.frontend`). The development
  server forwards the browser's requests to the Sync API and adds the token, which it reads from
  its own environment.

### What a team cannot do today

- **Open the web interface from a deployment.** The released image does not contain the web
  interface, and the Sync API does not serve it. An operator who wants it has to run a Node
  development server next to the service, with a Sync API token in that server's environment.
- **Use the web interface as themselves.** The development server adds one token for every
  person who can reach it. Every person who opens the page acts with that token, including when
  they start a verify, an apply or a cancel.

### Why the address of every Sync API route changes

Three page paths of the web interface are the same as current Sync API routes: `/runs`,
`/runs/{run_id}` and `/configs/{config_id}`. One address cannot answer both a browser that opens
the page and a client that calls the route. The issue resolves this: the web interface is served
at the root of the address, and the Sync API moves under `/api`.

## Decisions

- **D-1, the web interface is served at the root and the Sync API under `/api`.** Stated in the
  issue.
- **D-2, a person enters a Sync API token in the web interface.** Decided by the maintainer on
  2026-11-16. The web interface asks for a token, keeps it for the browser tab only, and sends it
  with every API request. The service keeps no browser session and sets no cookie. The token is
  never part of the files the service sends to the browser. The alternatives were a login route
  with a session cookie, and no browser authentication in this feature.
- **D-3, the CLI adds `/api` to the configured address.** Confirmed by the maintainer on
  2026-11-16 (see "Clarifications"). `INFRAHUB_SYNC_API_URL` keeps its meaning, "the address of
  the Sync service", so the values in Compose files and in operators' environments stay valid
  after an upgrade of both the service and the CLI. A value that already ends in `/api` is used
  as it is. This matches the Infrahub Python SDK, whose address is the root of the Infrahub
  server and which adds `/api` itself
  ([`infrahub_sdk/client.py`](https://github.com/opsmill/infrahub-sdk-python/blob/main/infrahub_sdk/client.py)).

## Clarifications

### Session 2026-11-16

- Q: Does the CLI add `/api` to `INFRAHUB_SYNC_API_URL`, refuse a value ending in `/api`, or
  require operators to add `/api` themselves? → A: The CLI adds `/api` and uses a value that
  already ends in `/api` as it is (option A), as the Infrahub SDK does with its address.
- Q: Does the "Requests sent as" line in the header of the web interface stay, now that each
  person enters their own token? → A: Remove it in this feature. The status menu keeps the
  service status and gains "Remove token".

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Open the web interface from a deployment (Priority: P1)

An operator deploys the Sync service from the released image or the root `docker-compose.yml`.
They open the address of the Sync service in a browser and view the web interface. They do not
install Node, start a second server, or publish a second port.

**Why this priority**: This is the need in the issue. Without it, the web interface is available
to developers only.

**Independent Test**: Start the Compose deployment from a locally built image, open the address
of `sync-api` in a browser, and confirm the web interface loads, including when a page path is
opened directly.

**Acceptance Scenarios**:

1. **Given** a deployment started from the released image, **When** an operator opens the root
   of the Sync service address in a browser, **Then** the browser displays the web interface.
2. **Given** the web interface is open on the page of one run, **When** the operator reloads the
   page or opens its address in a new tab, **Then** the browser displays the same page, not an
   API response and not a "not found" page.
3. **Given** a deployment started from the released image, **When** the operator lists its
   running processes and published ports, **Then** there is no Node process and no port other
   than the one `sync-api` already published.
4. **Given** the web interface was updated by a new release, **When** an operator opens it after
   the upgrade, **Then** the browser displays the new release without the operator clearing the
   browser cache.

---

### User Story 2 - Act in the web interface with one's own token (Priority: P1)

A person opens the web interface and enters their Sync API token. From then on, they view runs
and configurations and start actions, and the Sync service treats every request as coming from
that token. A person without a valid token views nothing.

**Why this priority**: The web interface can start verify, apply and cancel. Serving it without
authentication in the browser would either show nothing (every request refused) or require one
shared token. It is needed for User Story 1 to be usable.

**Independent Test**: Open the web interface in a new browser tab, confirm it asks for a token,
enter a valid one and view the list of runs; then enter an invalid one and confirm it is
refused.

**Acceptance Scenarios**:

1. **Given** a browser tab with no token, **When** a person opens any page of the web interface,
   **Then** the web interface asks for a Sync API token and requests no run or configuration
   data.
2. **Given** the token entry screen, **When** the person enters a valid token, **Then** the web
   interface displays the page they first asked for.
3. **Given** the token entry screen, **When** the person enters a token the Sync service does
   not accept, **Then** the web interface stays on the token entry screen and states that the
   token was refused.
4. **Given** a person is using the web interface, **When** the Sync service refuses their token
   (for example after an operator removed it), **Then** the web interface removes the token from
   the tab and returns to the token entry screen.
5. **Given** a person entered a token, **When** they close the browser tab and open the web
   interface in a new one, **Then** the web interface asks for a token again.
6. **Given** a person entered a token, **When** they choose to remove it, **Then** the web
   interface removes the token from the tab and returns to the token entry screen.
7. **Given** two people with different tokens, **When** each starts an action in the web
   interface, **Then** the Sync service records each action against that person's token, as it
   does for the CLI.

---

### User Story 3 - Keep using the CLI and the Compose deployment after the upgrade (Priority: P1)

An operator upgrades the Sync service and the `infrahub-sync` CLI to the release that contains
this feature. They do not change `INFRAHUB_SYNC_API_URL`. Their CLI commands, the direct Prefect
flow and the health checks of the Compose deployment work as before. An operator who runs an
older CLI, or a script that calls the old route addresses, receives an error that says what to
change.

**Why this priority**: Every existing client calls routes whose address changes. If the upgrade
silently breaks them, the feature costs more than it gives.

**Independent Test**: Against an upgraded service, run `infrahub-sync whoami`, `configs list`
and `runs list` with an unchanged `INFRAHUB_SYNC_API_URL`; then call an old route address with a
plain HTTP client and read the answer.

**Acceptance Scenarios**:

1. **Given** an upgraded service and CLI and an unchanged `INFRAHUB_SYNC_API_URL`, **When** an
   operator runs any `infrahub-sync` command that calls the Sync API, **Then** the command
   succeeds as it did before the upgrade.
2. **Given** an upgraded Compose deployment, **When** it starts, **Then** `sync-api` becomes
   healthy and the services that wait for it start.
3. **Given** an upgraded service, **When** a script that is not a browser calls an old route
   address such as `/version` or `/runs`, **Then** it receives an error in the Sync API's error
   format that states the Sync API is now under `/api`, not a page of the web interface.
4. **Given** an upgraded service and a CLI from before the upgrade, **When** an operator runs a
   command, **Then** the command fails with a message from which the operator can tell that the
   CLI must be upgraded.
5. **Given** an upgraded service, **When** a client calls an address under `/api` that is not a
   route, **Then** it receives a "not found" error in the Sync API's error format, never a page
   of the web interface.

---

### User Story 4 - Build and develop with the web interface included (Priority: P2)

A maintainer builds the Sync image from a checkout and the image contains the web interface. A
developer who installs the Python package without building the web interface still starts the
service and runs the tests. A developer who works on the web interface keeps the development
server, which now reaches the Sync API under `/api`.

**Why this priority**: It is how the first three stories reach a release and stay maintainable,
but an operator does not experience it directly.

**Independent Test**: Build the image from a fresh checkout with the submodule fetched and
confirm the web interface is in it; start the service from a Python install without the built
web interface and confirm the API answers under `/api`.

**Acceptance Scenarios**:

1. **Given** a checkout with the design system submodule fetched, **When** a maintainer builds
   the image, **Then** the image contains the built web interface and no Node runtime.
2. **Given** a checkout without the submodule, **When** a maintainer builds the image, **Then**
   the build fails with a message that names the missing submodule.
3. **Given** a Python install with no built web interface, **When** a developer starts the
   service, **Then** the Sync API answers under `/api`, and the root address answers with a
   message that the web interface is not included in this install.
4. **Given** the development stack, **When** a developer runs the development server of the web
   interface, **Then** the pages load data from the Sync API and the developer enters a token as
   in a deployment.
5. **Given** the release workflow, **When** it publishes the image, **Then** the published image
   contains the web interface for both published architectures.

### Edge Cases

- A browser opens a path that is neither a page of the web interface nor a file of it, for
  example `/unknown/path`: the web interface loads and displays its own "not found" page.
- A browser asks for a file of the web interface that does not exist, for example a script from
  an earlier release: the service answers "not found", not the entry page, so the browser does
  not run a page as a script.
- A person pastes a token with a space or a line break around it: the web interface removes the
  surrounding white space before it uses the token.
- The Sync service cannot be reached when the person submits a token: the web interface states
  that the Sync service did not answer, and does not state that the token was refused.
- The Sync service answers 403 (the token is valid but may not do the action): the web interface
  keeps the token and displays the refusal, as it does today.
- The browser blocks tab storage (a private window with storage disabled): the web interface
  keeps the token in memory for the page and asks again after a reload.
- A request path contains `..` or an encoded separator: the service never sends a file from
  outside the built web interface.
- An operator configured `INFRAHUB_SYNC_API_URL` with a trailing `/` or already ending in
  `/api`: the CLI calls the same routes in both cases and does not add `/api` twice.
- The interactive API documentation and the OpenAPI document move with the API and keep
  answering without a token.
- The browser cannot reach the font host of the design system (`rsms.me`, set in the design
  system's own style sheet), for example in an isolated network: the web interface displays with
  the browser's fallback font and works otherwise.

## Requirements *(mandatory)*

### Functional Requirements

#### Addresses

- **FR-001**: The Sync service MUST serve the web interface at the root of its address, from the
  same host and port as the Sync API.
- **FR-002**: The Sync service MUST serve every Sync API route under `/api`, including `/status`,
  `/version`, the interactive documentation and the OpenAPI document. No Sync API route remains
  at its earlier address.
- **FR-003**: The routes that answer without a token today (`/status`, `/version`, the
  interactive documentation, the OpenAPI document) MUST keep answering without a token at their
  new addresses. Every other route MUST keep requiring a bearer token.
- **FR-004**: A request under `/api` that matches no route MUST receive a "not found" error in
  the Sync API's error format.
- **FR-005**: A request for a page path of the web interface, at any depth, MUST receive the
  entry page of the web interface, so that a page address opened directly or reloaded displays
  that page.
- **FR-006**: A request outside `/api` for a missing file of the web interface MUST receive a
  "not found" answer, not the entry page.
- **FR-007**: A request outside `/api` from a client that does not accept HTML MUST receive an
  error in the Sync API's error format that states the Sync API is served under `/api`.
- **FR-008**: The Sync service MUST send only files that are inside the built web interface.
- **FR-009**: The entry page MUST be sent so that a browser checks for a new version on every
  load. Files whose name changes with their content MAY be cached by the browser.
- **FR-010**: The OpenAPI document MUST describe the routes so that a client generated from it
  calls the addresses under `/api`.

#### Authentication in the browser

- **FR-011**: The web interface MUST ask for a Sync API token before it requests any data that
  requires one.
- **FR-012**: The web interface MUST check an entered token against the Sync service before it
  accepts it, and MUST distinguish "the token was refused" from "the Sync service did not
  answer".
- **FR-013**: The web interface MUST keep the token for the browser tab only. It MUST NOT keep it
  in storage that outlives the tab, in a cookie, or in a page address.
- **FR-014**: The web interface MUST send the token as a bearer header on every request to the
  Sync API, and to no other address.
- **FR-015**: When the Sync service answers 401 to any request, the web interface MUST remove
  the token from the tab, discard the data it holds, and return to the token entry screen.
- **FR-016**: The web interface MUST give the person a way to remove their token from the tab.
- **FR-017**: The files the Sync service sends to the browser MUST NOT contain a token. The
  service MUST NOT read a token from its environment to give to the browser.
- **FR-018**: The Sync service MUST NOT set a cookie and MUST NOT keep a browser session.
- **FR-019**: The web interface MUST NOT name a user. The token entry screen does not display the
  name of the token's actor, before or after the token is checked, and the "Requests sent as"
  line in the header of the web interface is removed.
- **FR-020**: The development server of the web interface MUST stop adding a token to forwarded
  requests, so that development and a deployment authenticate the same way.
- **FR-034**: Every answer the Sync service sends outside `/api` MUST tell the browser to run only
  scripts from the Sync service itself (and the inline scripts of the built entry page, identified
  by their content), to send requests only to the Sync service, to load fonts only from the Sync
  service and the font host the web interface uses today, and never to display the web interface
  inside a frame of another page.

#### Existing clients

- **FR-021**: The `infrahub-sync` CLI and the Python client MUST call the routes under `/api`
  when `INFRAHUB_SYNC_API_URL` holds the address of the Sync service, with or without a trailing
  `/`, and MUST NOT add `/api` twice when the value already ends in `/api`.
- **FR-022**: The direct Prefect flow, the development tasks, and the health checks in the root
  and development Compose files MUST call the routes at their new addresses.
- **FR-023**: When a CLI receives an answer that is not in the Sync API's format (for example a
  page of the web interface, from a service older or newer than the CLI expects), it MUST fail
  with a message that states the address did not answer as a Sync API and that names the address
  it called. It MUST NOT print the body of the answer. A successful artifact download, whose
  content is not JSON by design, is not such an answer.

#### Build and install

- **FR-024**: The released image MUST contain the built web interface and MUST NOT contain a
  Node runtime or the sources of the web interface.
- **FR-025**: The image build MUST produce the web interface from the committed lock file of the
  web interface and from the pinned revision of the design system submodule, with every external
  base image pinned by digest.
- **FR-026**: The image build MUST fail with a message that names the submodule when the
  submodule is not fetched.
- **FR-027**: The workflows that build or publish the image MUST fetch the submodule.
- **FR-028**: The development build MUST rebuild the image when a file of the web interface
  changes.
- **FR-029**: The Sync service MUST start when no built web interface is present. In that case
  the Sync API MUST work under `/api`, and the root address MUST answer that the web interface
  is not included in this install.
- **FR-030**: The location of the built web interface MUST be fixed by the image, with one
  environment variable that a developer can set to serve a local build.

#### Records

- **FR-031**: The user documentation MUST describe how an operator opens the web interface, how
  an operator gives each person their own Sync API token (through the service's configured list
  of bearer tokens), how a person enters and removes a token, and the new address of the Sync
  API, including what a script that calls the API has to change.
- **FR-032**: A changelog fragment MUST state that the address of every Sync API route changed,
  that the CLI and the service must be upgraded and rolled back together, and the error an older
  CLI prints against the new service (`error: compatibility` with no server version).
- **FR-033**: A decision record MUST state why the API moved under `/api`, why the browser holds
  a token for the tab, and why the CLI adds `/api`.

### Key Entities

- **Built web interface**: The set of files a browser needs to display the web interface: one
  entry page and files whose names change with their content. It is produced at image build time
  and is read-only in a deployment.
- **Sync API token**: A bearer token from the service's configured list of principals. In this
  feature it is also what a person enters in the web interface. Its permissions are unchanged.
- **Token for the tab**: The copy of a Sync API token that the web interface keeps while a
  browser tab is open.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An operator who starts the Compose deployment from the released image opens the
  web interface with one address and no additional process, port or setting.
- **SC-002**: Every page of the web interface displays when its address is opened directly in a
  new tab (4 page paths today: `/`, `/runs`, `/runs/{run_id}`, `/configs/{config_id}`).
- **SC-003**: A person with a valid token goes from opening the address to viewing the list of
  runs in under 30 seconds, with one input: the token.
- **SC-004**: 100% of the requests the web interface sends to the Sync API after token entry
  carry that person's token, and none of the files served to the browser contains a token.
- **SC-005**: After an upgrade of the service and the CLI together, every `infrahub-sync`
  command that worked before works with an unchanged `INFRAHUB_SYNC_API_URL`.
- **SC-006**: A call to any earlier route address from a client that is not a browser receives an
  error that names `/api`, for 100% of the earlier routes.
- **SC-007**: The released image grows by less than 10 MB and contains no Node runtime.
- **SC-008**: The unit tests and the base-install check pass on an install that has no built web
  interface.

## Assumptions

- The web interface in `frontend/` is owned by another maintainer. This feature changes it only
  where the need requires it: the token entry screen, the bearer header, the handling of 401,
  the development server's forwarding, the status menu in the header, and the README.
- The header of the web interface displays the actor of the token ("Requests sent as",
  `frontend/src/routes/__root.tsx`). With one shared development token this named no person;
  once each person enters their own token it would. It conflicts with the repository rule never
  to name a user in the interface, so this feature removes it (see "Clarifications").
- A file that the web interface offers for download (a plan or a run artifact) is fetched with the
  token and saved from the browser, because a plain link cannot send a bearer header.
- The permissions of a token are unchanged. A person who enters a token in the web interface can
  do what that token can do through the CLI, including verify, apply and cancel. Approval rules
  (`INFRAHUB_SYNC_REQUIRE_APPROVAL`) apply in the same way.
- A script in the page can read a token kept for the tab. The web interface loads no script from
  another origin, and the token is not kept after the tab closes. This risk was accepted with
  decision D-2.
- The token entry screen does not limit the number of attempts. This matches the Sync API today:
  each refused token is recorded by the service, and configured tokens have at least 16
  characters.
- An operator who exposes the Sync service beyond a trusted network terminates TLS in front of
  it, as for the API today. This feature does not add TLS to the service.
- The service and the CLI are upgraded together. A CLI from before this feature does not work
  against a service with it, and the reverse. FR-023 makes the failure readable; it does not
  make the two versions compatible.
- No earlier route address keeps working as an alias. The page paths that collide with them make
  a full set of aliases impossible, and a partial set would hide the change.
- Sign-in with a user name and password, single sign-on, and token creation in the web interface
  are out of scope.
- Serving the web interface under a path prefix other than the root (for example behind a
  reverse proxy at `/sync/`) is out of scope.
- The design system submodule stays a submodule pinned to a revision. Replacing it with a
  published package is out of scope.
