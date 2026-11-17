# Feature Specification: Serve the web interface from the Sync API

**Feature Branch**: `023-serve-web-interface`

**Created**: 2026-11-14

**Status**: Draft

**Input**: Issue [#421](https://github.com/opsmill/infrahub-sync/issues/421), "service: serve the web interface from
the backend", and the idea brief "Serve the web interface from the Sync API", grilled with the user on 2026-11-14.
Every decision in the brief was confirmed by the user. Updated on 2026-11-15 with the findings of
[`critiques/critique-20261115.md`](./critiques/critique-20261115.md), which the user approved.

## Background

The web interface in `frontend/`, added in #410, runs only through the Vite development server. A team that deploys
the Sync service from the released image or the root `docker-compose.yml` cannot open it: the image does not contain
it, and the Sync API does not serve it. To use it, an operator runs a Node development server next to the service,
with a Sync API bearer token in that server's environment.

Five facts of the current code shape this feature:

- The browser sends every API request to `/api` on its own origin. The development server removes the `/api` prefix
  and adds the bearer token, because the Sync API serves its routes at the root.
- Three page paths of the web interface are also Sync API routes: `/runs`, `/runs/{run_id}` and
  `/configs/{config_id}`. One address cannot serve both at the root.
- Every Sync API route except `/status`, `/version`, `/docs` and `/openapi.json` requires a bearer token that
  resolves to a principal from `INFRAHUB_SYNC_SERVICE_BEARER_TOKENS`. The Sync API has no sessions and no sign-in.
- Each write records the principal's actor name: in the audit log, on registered configuration versions and on plan
  approvals. Idempotency keys are stored per actor.
- A principal that is not an administrator can act only on runs that its own actor started.

The Sync service is an alpha (`3.0.0a5`). No stable release serves the API at the root.

### Terms used in this specification

- **Web interface**: the built pages of `frontend/`.
- **Sync API address**: the scheme, host and port at which an operator reaches the Sync API, for example
  `http://127.0.0.1:8001`. This is the value of `INFRAHUB_SYNC_API_URL`.
- **Principal**: an actor name and administrator flag that one bearer token resolves to, from
  `INFRAHUB_SYNC_SERVICE_BEARER_TOKENS`.
- **Anonymous access setting**: an operator setting that names one principal. When it is set, a request that carries
  no `Authorization` header acts as that principal, subject to the host and origin checks below.
- **Allowed hosts**: the host names that a request without a token must name in its `Host` header to act as the
  anonymous principal. By default `localhost`, `127.0.0.1` and `::1`.
- **Stored token**: a bearer token that a person pastes into the web interface, and that the browser keeps until the
  person deletes it.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Open the web interface from a deployment without a token (Priority: P1)

An operator deploys the Sync service from the released image with `docker-compose.yml` and sets the anonymous access
setting to the principal `web-ui`. A person opens the Sync API address in a browser. The configuration list loads
without a separate server and without entering a token. The person starts a run, and the audit log records the
actor `web-ui`.

**Why this priority**: This is the need in issue #421. Without it, no deployment from the image can use the web
interface.

**Independent Test**: Start the image with the anonymous access setting, open the Sync API address in a browser, and
start a run from a configuration page. The run starts, and the audit log names the configured actor.

**Acceptance Scenarios**:

1. **Given** a deployment from the released image with the anonymous access setting naming `web-ui`, **When** a
   person opens `http://127.0.0.1:8001/`, **Then** the web interface shows the configuration list and the top bar
   shows the actor `web-ui`.
2. **Given** the same deployment, **When** the person starts a run on a configuration, **Then** the run starts and
   the audit log records the actor `web-ui`.
3. **Given** the same deployment, **When** the person reloads the page of a run at `/runs/{run_id}`, **Then** the
   same run page is shown.
4. **Given** a deployment where `web-ui` is not an administrator, **When** the person views the top bar, **Then** it
   shows that the actor is not an administrator, as the CLI's `whoami` reports.
5. **Given** the same deployment, **When** the operator reads the `sync-api` start-up log, **Then** one line states
   that anonymous access is on, with the actor `web-ui` and the allowed hosts.

---

### User Story 2 - Keep the CLI and other clients working after the API moves (Priority: P1)

An operator who uses the `infrahub-sync` CLI with `INFRAHUB_SYNC_API_URL=http://127.0.0.1:8001` upgrades to the
release that moves the Sync API under `/api`. Every CLI command works with no change to the environment. The compose
health checks report the service as healthy.

**Why this priority**: Moving the API is required for story 1, because page paths and API routes collide. The move
must not break the CLI or the deployment's health checks in the same release.

**Independent Test**: Run the smoke suite against the development stack built from this feature, with
`INFRAHUB_SYNC_API_URL` set to the Sync API address without `/api`. Start `docker-compose.yml` and wait for `sync-api`
to become healthy.

**Acceptance Scenarios**:

1. **Given** `INFRAHUB_SYNC_API_URL=http://127.0.0.1:8001`, **When** the operator runs `infrahub-sync runs list`,
   **Then** the CLI lists runs from `http://127.0.0.1:8001/api/runs`.
2. **Given** a deployment from `docker-compose.yml`, **When** the containers start, **Then** `sync-api` becomes
   healthy.
3. **Given** a script that calls `GET http://127.0.0.1:8001/runs` with a bearer token, **When** it runs against this
   release, **Then** it does not receive the run list, and the release note tells the script's owner to add `/api`.

---

### User Story 3 - Use the web interface with a personal token (Priority: P2)

An operator deploys the Sync service without the anonymous access setting. A person opens the Sync API address and
views a page that explains why requests without a token are refused and names the setting that allows them. The
person pastes their own bearer token. The web interface acts as their principal, in every tab and after reloads,
until they select **Forget token**.

**Why this priority**: It gives the default deployment, where the setting is off, a working web interface for anyone
who holds a token, and it tells a new operator what to change.

**Independent Test**: Start the image without the anonymous access setting, open the Sync API address, paste the
`operator` token, reload the page and open a second tab, then select **Forget token**.

**Acceptance Scenarios**:

1. **Given** a deployment without the anonymous access setting, **When** a person opens the Sync API address,
   **Then** the web interface shows one page that explains that requests without a token are refused, names the
   anonymous access setting, links to its documentation and offers a token field.
2. **Given** that page, **When** the person pastes the `operator` token, **Then** the top bar shows the actor
   `operator` and the configuration list loads.
3. **Given** a stored token, **When** the person reloads the page or opens the Sync API address in a new tab,
   **Then** the web interface acts as `operator` without asking for the token again.
4. **Given** a stored token, **When** the person selects **Forget token**, **Then** the browser no longer holds the
   token and the next request carries no token.
5. **Given** a stored token that the operator has since removed from `INFRAHUB_SYNC_SERVICE_BEARER_TOKENS`, **When**
   the person opens the web interface, **Then** it shows that the stored token was refused and offers to replace or
   forget it.
6. **Given** the token page, **When** the person pastes a token that the Sync API refuses, **Then** the page shows the
   refusal and the browser does not store the token.

---

### Edge Cases

- **Any client without a token**: The Sync API cannot tell a browser from another client. When the anonymous access
  setting is set, any request without a token from an allowed host acts as the named principal, including `curl` and
  a CLI with no `INFRAHUB_SYNC_API_TOKEN`. The documentation states this.
- **Another web page in the operator's browser**: A page on another site can send requests to the Sync API address
  from the operator's browser. With DNS rebinding, such a page can make its own host name resolve to `127.0.0.1`, so
  the browser treats the Sync API as the page's own origin. A request without a token acts as the anonymous principal
  only when its `Host` header names an allowed host, so a rebound host name is refused. A request without a token
  whose `Origin` header names another host is refused with 403.
- **The Sync API behind a host name**: An operator who publishes the Sync API at `sync.example.net` adds that name to
  the allowed hosts. Until then, requests without a token through that name are refused with 401, and requests with a
  token work.
- **The anonymous principal is an administrator**: Allowed. Everyone who reaches the address can then register
  configuration versions and act on every run. The Sync API logs a warning at start-up that names the actor.
- **Runs belong to the actor that started them**: A run started without a token belongs to the anonymous principal. A
  person who later pastes a personal token that is not an administrator cannot cancel, verify or apply that run, and
  the reverse also holds. The documentation states this.
- **A refused token with the setting on**: A request that carries a token that the Sync API refuses is refused. It
  does not act as the anonymous principal, so the audit log never records an actor that the person did not choose.
  After **Forget token**, requests carry no token and act as the anonymous principal.
- **The setting names an unknown principal**: When the anonymous access setting names an actor that is not in
  `INFRAHUB_SYNC_SERVICE_BEARER_TOKENS`, the Sync API refuses to start and names the setting in its error. The error
  does not contain any token.
- **A stored token on a shared computer**: The token stays in the browser until a person deletes it. The
  documentation states this and names **Forget token**.
- **The web interface in a frame**: Another site cannot show the web interface in a frame, so it cannot trick a person
  into selecting **Apply**.
- **An unknown API path**: A request to a path under `/api` that matches no route returns the Sync API's JSON error
  with status 404, not the web interface. The web interface then shows the API error instead of a generic
  "Sync API unavailable" message.
- **A page path that does not exist**: A browser that loads a path outside `/api` that the web interface does not
  define receives the web interface, which shows its own "page not found" view. A request outside `/api` that does
  not accept HTML, such as a script that forgot the `/api` prefix, receives 404.
- **A Sync API started from the Python package**: That installation has no built web interface. A request to `/`
  returns a short message that names the released image as the way to get the web interface. Every route under
  `/api` works.
- **A health check from another platform**: A Kubernetes probe or load balancer check on `/version` or `/status`
  stops receiving the version or status JSON. The release note names the new paths `/api/version` and `/api/status`.
- **Audit log searches**: The request paths recorded in the audit log start with `/api` from this release on.
- **Rolling back the image**: An operator who rolls back to an earlier image after moving scripts and health checks
  to `/api` must move them back. The release note states this.

## Requirements *(mandatory)*

### Functional Requirements

#### Serving the web interface

- **FR-001**: The Sync API MUST serve the web interface at the root of the Sync API address.
- **FR-002**: The released image MUST contain the built web interface. Its runtime stage MUST NOT contain Node or the
  frontend source files.
- **FR-003**: A request outside `/api` that accepts HTML, for any page path such as `/runs/abc`, MUST return the web
  interface, so that loading or reloading a page shows that page.
- **FR-004**: A request for a path under `/api` that matches no route MUST return the Sync API's JSON error with
  status 404.
- **FR-005**: A Sync API that has no built web interface, as when started from the Python package, MUST answer `/`
  with a message that names the released image as the way to get the web interface.
- **FR-006**: Every web interface response MUST forbid display in a frame on another site, and MUST forbid the
  browser from running scripts other than the web interface's own files.

#### Moving the Sync API under `/api`

- **FR-007**: The Sync API MUST serve every route under `/api`, including `/api/version`, `/api/status`, `/api/docs`
  and `/api/openapi.json`. It MUST NOT keep the old paths at the root as aliases.
- **FR-008**: The `infrahub-sync` CLI and every other client in this repository MUST keep reading
  `INFRAHUB_SYNC_API_URL` as the Sync API address without `/api`, and add `/api` to each request.
- **FR-009**: The `sync-api` health checks in `docker-compose.yml` and `development/docker-compose.yml` MUST request
  `/api/version`.
- **FR-010**: The development server of the web interface MUST forward `/api` requests to the Sync API without
  removing the prefix, so that development and the image use the same paths.
- **FR-011**: Moving the Sync API under `/api` MUST NOT stop any work that the Sync API runs in the background, such
  as the run liveness checks.

#### Access without a token

- **FR-012**: Operators MUST be able to name, with one setting, a principal from `INFRAHUB_SYNC_SERVICE_BEARER_TOKENS`
  that requests without an `Authorization` header act as. The principal MAY be an administrator.
- **FR-013**: When the anonymous access setting is not set, the Sync API MUST refuse every request without a token on
  every route that requires one today, with status 401.
- **FR-014**: A request that carries an `Authorization` header MUST act as its bearer token's principal, or be
  refused with status 401 when the header holds no bearer token that resolves. It MUST NOT act as the anonymous
  principal.
- **FR-015**: A request without a token MUST act as the anonymous principal only when its `Host` header names an
  allowed host. Operators MUST be able to set the allowed hosts. Otherwise the request MUST be refused with status 401.
- **FR-016**: A request without a token MUST be refused with status 403 when it carries an `Origin` header that
  names a host or port other than the one in its `Host` header, or that has the value `null`.
- **FR-017**: When the anonymous access setting names an actor that is not in `INFRAHUB_SYNC_SERVICE_BEARER_TOKENS`,
  the Sync API MUST refuse to start and name the setting in its error.
- **FR-018**: When the anonymous access setting is set, the Sync API MUST log one line at start-up that names the
  actor and the allowed hosts. When that principal is an administrator, the line MUST be a warning that says so.
- **FR-019**: The Sync API MUST NOT write any token value to its logs.

#### Token in the browser

- **FR-020**: When the Sync API refuses the web interface's request for the current actor with status 401, the web
  interface MUST show one page that explains that requests without a token are refused, names the anonymous access
  setting, links to its documentation and offers a field for a bearer token.
- **FR-021**: After a person enters a token that the Sync API accepts, the web interface MUST send it as a bearer
  token on every request and show the token's actor in the top bar.
- **FR-022**: The web interface MUST NOT store a token that the Sync API refuses.
- **FR-023**: The web interface MUST keep an accepted token in the browser across reloads and tabs until the person
  deletes it.
- **FR-024**: The top bar MUST offer **Forget token** whenever a token is stored. It MUST delete the stored token at
  once.
- **FR-025**: When the Sync API refuses a stored token with status 401, the web interface MUST show the token page
  with a message that the stored token was refused, and offer to replace or forget it.

#### Documentation

- **FR-026**: `docs/docs/compose-deployment.mdx` and `docs/docs/reference/sync-http-api.mdx` MUST describe:
    - the new `/api` paths and how to open the web interface
    - the anonymous access setting, that it applies to every client without a token, and the allowed hosts
    - that runs belong to the actor that started them, and what that means when a person switches between the
      anonymous principal and a personal token
    - that a stored token stays in the browser until **Forget token**
- **FR-027**: The release note MUST state:
    - that every Sync API route moved under `/api`, with no aliases
    - `/api/version` and `/api/status` for health checks outside the compose files
    - that request paths recorded in the audit log now start with `/api`
    - that rolling back to an earlier image requires the old paths

### Key Entities

- **Principal** (existing): an actor name and administrator flag that one bearer token resolves to. The anonymous
  access setting refers to one principal by its actor name and adds no new kind of identity.
- **Anonymous access setting** (new setting, no new stored data): the actor name of the principal that requests
  without a token act as. Unset by default.
- **Allowed hosts setting** (new setting, no new stored data): the host names a request without a token must name.
  Defaults to `localhost`, `127.0.0.1` and `::1`.
- **Stored token** (new, in the browser only): a bearer token that a person pasted and the Sync API accepted. The
  Sync API does not store it.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: From a new deployment of the released image with `docker-compose.yml` on its default loopback
  publication, an operator opens a working web interface with no extra process and no step beyond setting one
  environment variable.
- **SC-002**: Every CLI command that worked before this change works after it, with no change to the operator's
  environment: the smoke suite passes against the development stack built from this feature.

## Assumptions

- Operators who publish the Sync API address beyond `127.0.0.1` put their own TLS and network controls in front of
  it. The compose file publishes it on `127.0.0.1` by default (`INFRAHUB_SYNC_BIND_ADDRESS`).
- User accounts, sign-in, single sign-on and per-person identity in the web interface are a later feature. Until then,
  the anonymous access setting and stored tokens are the only ways the web interface authenticates.
- The commit of `frontend/vendor/infrahub` recorded in this repository fixes the design system code for each build,
  so two builds of the same revision produce the same web interface.
- The release-intent label of the pull request is chosen when the pull request opens.
- `ci-docker-image.yml` checks out the `frontend/vendor/infrahub` submodule so that the image build can build the web
  interface. Only `workflow-linter.yml` checks it out today.

### Out of scope

- Shipping the web interface in the Python package published to PyPI.
- User accounts, sign-in, single sign-on and per-person identity.
- Roles or permissions per actor in the web interface beyond the existing administrator flag.
- Aliases or a transition period for the old API paths at the root.
- Serving the web interface under a sub-path behind a reverse proxy, for example `https://host/sync/`.
- Any change to the Prefect UI.
- Marking audit records of requests without a token. Audit records are stored in the product database, and the
  brief keeps this feature free of database changes. The start-up log line (FR-018) states that anonymous access is
  on.
