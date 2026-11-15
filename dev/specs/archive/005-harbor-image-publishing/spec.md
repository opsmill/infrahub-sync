# Feature Specification: Publish the Sync image to the OpsMill registry

**Feature Branch**: `007-harbor-image-publishing`

**Created**: 2026-10-01

**Status**: Extracted

**Input**: User description: "I want to remove the crappy work done on the "image packaing" with github and use our docker hub (harbor) similar to ../infrahub-mcp and ../infrahub"

## Context

On `feature/v3-develop`, the Infrahub Sync container image never reaches a registry.
It is built in CI, exported as per-platform archives and an image layout, and moved
between jobs and to testers as GitHub Actions artifacts: a candidate workflow, a
tester packet, a clean-host handoff, and a Compose bundle that binds the image by a
digest record the host must satisfy by loading a tarball. That machinery is large,
specific to this repository, and unlike every other OpsMill product.

`infrahub` and `infrahub-mcp` publish their images to the OpsMill registry
(`registry.opsmill.io/opsmill/<repository>`), built for amd64 and arm64, signed, with
a bill of materials attached, through one reusable build-and-push workflow. Users run
`docker pull` against it. Infrahub Sync should work the same way.

## Clarifications

### Session 2026-10-01

- Q: Who can pull the image? → A: The registry project is private (authenticated pulls) until the first stable 3.0.0, then public.
- Q: Do pre-releases move `latest`? → A: No. A pre-release (alpha, beta, RC) pushes only its version tag. A stable release pushes its version tag and `latest`. This follows infrahub's pre-release split; infrahub-mcp moves `latest` on every release.
- Q: Which platforms do pull requests build? → A: Both linux/amd64 and linux/arm64 on every PR that changes image inputs, each on a native runner, in parallel. No label is needed.
- Q: Is the image checked before tags move? → A: Yes. Each platform image is smoke-tested (the CLI runs, and the default Sync API command starts healthy) before any tag is created or moved. A failure blocks tagging.
- Q: How does the Compose deployment get its image? → A: Same as infrahub and infrahub-mcp. `compose.yaml` is renamed `docker-compose.yml`, and its Sync services reference `${INFRAHUB_SYNC_DOCKER_IMAGE:-registry.opsmill.io/opsmill/infrahub-sync}:${VERSION:-<release version>}`. The default version is bumped in the release commit, and operators use the file from the release tag. The release bundle archive and its `image.bind` digest record are removed.
- Q: Which file becomes the root `docker-compose.yml`, and what happens to the wrapper? → A: The operator deployment becomes the root `docker-compose.yml`, as one self-contained file. The `infrahub-sync-compose` wrapper and `deploy/compose/` are removed, and operators use plain `docker compose`. The dev stack moves to `development/docker-compose.dev.yml`.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Pull a released Sync image from the OpsMill registry (Priority: P1)

An operator deploying Infrahub Sync pulls a released image by version (or `latest`)
from the OpsMill registry, the same way they pull Infrahub and the Infrahub MCP
server, and runs it on an amd64 or arm64 host.

**Why this priority**: This is the outcome the change exists for. Without a
published image, no other story delivers anything to users.

**Independent Test**: After a release, log in with tester credentials (before 3.0.0),
pull `registry.opsmill.io/opsmill/infrahub-sync:<version>` and `:latest` on an amd64
host and an arm64 host, then run `infrahub-sync --help` inside each.

**Acceptance Scenarios**:

1. **Given** a release of version X has been published, **When** an operator
   pulls the image tagged X from the OpsMill registry, **Then** the pull succeeds and
   the image reports version X and the release commit in its metadata labels.
2. **Given** X is a stable release, **When** an operator pulls `latest`, **Then** they
   get the same image (same digest) as tag X.
3. **Given** `latest` points at stable release S, **When** pre-release P (for example
   `3.1.0b1`) is published, **Then** tag P exists and `latest` still resolves to S's
   digest.
4. **Given** an arm64 host, **When** the operator pulls tag X, **Then** the registry
   serves the arm64 variant without the operator naming a platform.
5. **Given** a released image, **When** a consumer verifies its signature and its
   attached bill of materials with the standard verification tool, **Then** both
   verify against the published digest.

---

### User Story 2 - Publish an image on demand for a chosen ref and tags (Priority: P2)

A maintainer publishes an image for a specific commit with tags they choose (for
example a pre-release tag for testers), without cutting a release, by dispatching
the build workflow manually.

**Why this priority**: Testers currently get images through the tester packet. Once
that is removed, a manual dispatch is the replacement for any image that is not a
stable release.

**Independent Test**: Dispatch the build workflow with a ref, a tag, and publish
enabled, then pull that tag from the registry.

**Acceptance Scenarios**:

1. **Given** a maintainer with permission to run workflows, **When** they dispatch the
   build with a ref, one or more tags, and publish enabled, **Then** a multi-platform
   image for that ref is pushed under exactly those tags, signed, with its bill of
   materials attached.
2. **Given** the same dispatch with publish disabled, **When** it runs, **Then** the
   image is built for the requested platforms and nothing is pushed.
3. **Given** a dispatch that requests only one platform, **When** it runs, **Then**
   only that platform is built.

---

### User Story 3 - Pull requests prove the image still builds, without publishing (Priority: P2)

A contributor's pull request that touches the image inputs (the image definition,
dependencies, or package source) is checked to confirm the image still builds,
and nothing is pushed.

**Why this priority**: It replaces the PR-time image gate being removed, so a broken
image is caught before release instead of at release time.

**Independent Test**: Open a pull request that breaks the image definition and confirm
a required check fails. Open one that changes only docs and confirm the image build
does not run or is reported as not needed.

**Acceptance Scenarios**:

1. **Given** a pull request that changes an image input, **When** CI runs, **Then** the
   image is built for amd64 and arm64, the result is reported as a check, and no
   registry credential is used.
2. **Given** a pull request whose change breaks only the arm64 build, **When** CI runs,
   **Then** the check fails.
3. **Given** a pull request from a fork, **When** CI runs, **Then** the build check
   behaves the same as for an internal branch, because publishing never happens on
   pull requests.
4. **Given** a pull request that changes no image input, **When** CI runs, **Then** the
   build is skipped and the required check still passes.

---

### User Story 4 - Compose deployment uses the registry image (Priority: P3)

An operator takes `docker-compose.yml` from a release tag and runs
`docker compose up`. Docker pulls the Sync image for that release from the registry
like any other service image, the same way Infrahub and the Infrahub MCP server are
deployed, with no tarball to load and no bundle to unpack.

**Why this priority**: The current deployment depends on the bundle archive and the
image tarball being removed. It has to move to the sibling pattern to keep working,
though it is not the headline outcome.

**Independent Test**: On a clean host with no preloaded images, download
`docker-compose.yml` from release tag X and run `docker compose up`. The stack
starts on the registry image for X.

**Acceptance Scenarios**:

1. **Given** a clean host with registry access, **When** the operator runs
   `docker compose up` with the `docker-compose.yml` from release tag X, **Then** the
   Sync services run the image tagged X, without any manual load step.
2. **Given** the same file, **When** the operator sets `VERSION=Y` (or
   `INFRAHUB_SYNC_DOCKER_IMAGE`), **Then** the Sync services run that image instead.
3. **Given** the registry is unreachable, **When** the operator starts the stack,
   **Then** it fails with a message that names the image it could not pull.

---

### User Story 5 - The GitHub-artifact image machinery is gone (Priority: P3)

A maintainer reading the repository finds a single, conventional image pipeline that
matches the other OpsMill repositories, with no candidate workflow, tester packet,
image-archive handoff, or documentation describing them.

**Why this priority**: This is the cleanup half of the request. It can land with or
just after the new pipeline, but leaving it in place means two pipelines and
documentation that contradicts the product.

**Independent Test**: Search the repository for the removed workflows, tasks, archive
names, and tester-packet references, and confirm none remain outside the changelog,
archived specs, and historical release notes.

**Acceptance Scenarios**:

1. **Given** the change has merged, **When** a maintainer lists the CI workflows,
   **Then** the image-related ones are the reusable build-and-push workflow and its
   callers, and the candidate and archive-qualification workflows are gone.
2. **Given** the change has merged, **When** a reader opens the container image and
   deployment documentation, **Then** it tells them to pull from the OpsMill registry
   and describes no archive load, tester packet, or candidate artifact.

### Edge Cases

- **Registry credentials missing or rejected** when publishing: the run fails before
  pushing anything and says authentication to the registry failed, without printing
  the credential.
- **One platform fails to build or fails its smoke test**: no multi-platform tag is
  created or moved, so no user can pull a half-published or non-starting release.
- **Signing or attestation service is transiently unavailable**: the step retries a
  bounded number of times. If it still fails, the run fails visibly, and the
  unsigned digest is not reported as released.
- **A release tag is re-published** (workflow re-run): the version tag ends up on the
  newly built digest, `latest` follows it, and the run does not fail because the tag
  already exists.
- **Manual dispatch with an empty tag list** while publish is enabled: the run
  refuses to publish, because a pushed image needs a tag.
- **Pull request from a fork**: no secrets are available, and none are needed because
  pull requests never publish.
- **The required "Full qualification" check**: branch protection on
  `feature/v3-develop` requires it by name, and it currently depends on the image
  gate being removed. It must keep reporting, or the protection rule must change in
  the same rollout, or every PR is blocked.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The repository MUST have one reusable image workflow that builds the Sync
  image for linux/amd64 and linux/arm64 and, when asked to publish, pushes it to the
  OpsMill registry under the repository's name, following the build-and-push
  structure used by `infrahub-mcp` and `infrahub`.
- **FR-002**: A published image MUST be one multi-platform reference that resolves to
  the right variant for amd64 and arm64 hosts.
- **FR-003**: Every published image MUST be signed and MUST have a software bill of
  materials attached in both SPDX and CycloneDX formats, matching the sibling
  repositories.
- **FR-004**: Every release MUST publish the image from the release commit, tagged with
  the release version, as part of the existing release flow. Only a stable release
  (not an alpha, beta, release candidate, or dev version) MUST also move `latest`.
  A pre-release MUST leave `latest` where it was.
- **FR-005**: The image workflow MUST be manually dispatchable with a ref, a tag list,
  a platform list, and a publish switch. With publish disabled it MUST build without
  pushing.
- **FR-006**: Pull requests that change image inputs MUST build the image for both
  linux/amd64 and linux/arm64, each on a native runner of that architecture and in
  parallel, without pushing and without using registry credentials. They MUST report
  the result as a check. No label or opt-in is required.
- **FR-016**: Before any tag is created or moved, each platform image MUST pass a smoke
  test on a host of its own architecture: the CLI answers `--help`, and the image's
  default command (the Sync API) starts and reports healthy within a bounded time. If
  any platform fails, no tag MUST be pushed, and the run MUST fail naming the platform
  and the check. Pull-request builds MUST run the same smoke test.
- **FR-007**: Published images MUST carry the standard OCI labels for source, version,
  and revision.
- **FR-008**: Registry host and credentials MUST come from the organisation's existing
  registry settings and secrets, the same ones the sibling repositories use. None may
  be committed, and none may appear in logs.
- **FR-009**: The candidate workflow, the archive-based image qualification workflow,
  the tester packet, the image-archive and image-layout handoff, and the invoke tasks
  that exist only to serve them MUST be removed.
- **FR-010**: The image definition (Dockerfile), and any local build path a developer
  needs to build and run the image on their own machine, MUST be kept.
- **FR-011**: The operator deployment (today `deploy/compose/compose.yaml`) MUST become
  the root `docker-compose.yml`, matching infrahub and infrahub-mcp. It MUST be one
  self-contained file: no mounted scripts, no secret files, no wrapper. Its Sync
  services MUST reference
  `${INFRAHUB_SYNC_DOCKER_IMAGE:-registry.opsmill.io/opsmill/infrahub-sync}:${VERSION:-X}`,
  where X is the version of the commit the file is in, and MUST NOT require loading an
  image archive. Credentials MUST stay operator-supplied (through `.env` or the
  environment), and a missing one MUST stop `docker compose up` with a message
  naming it.
- **FR-019**: The `infrahub-sync-compose` wrapper and the rest of `deploy/compose/` MUST
  be removed. Operators use plain `docker compose` commands, documented for each
  former wrapper operation (start, status, logs, stop, reset, CLI).
- **FR-020**: The local development stack (today the root `compose.yaml`, which builds
  `infrahub-sync:dev`) MUST move to `development/docker-compose.dev.yml`. The
  `invoke` dev tasks and every reference to it MUST follow, so no `compose.yaml`
  remains at the root (Compose prefers `compose.yaml` over `docker-compose.yml`).
- **FR-017**: The release flow MUST set the default `VERSION` in `docker-compose.yml` to
  the release version inside the release commit, so the tagged commit carries the
  matching pin. The release MUST refuse to publish when the pin and the release
  version differ.
- **FR-018**: The release bundle archive, its `image.bind` digest record, and the
  `init` step that consumes it MUST be removed. Qualification gates and contract tests
  that exist only to verify the bundle or the archive handoff MUST be removed or
  rewritten against `docker-compose.yml` and the registry image.
- **FR-012**: The required "Full qualification" check MUST keep reporting a correct
  pass or fail after the image gate is replaced, so branch protection on
  `feature/v3-develop` keeps working without manual intervention.
- **FR-013**: User and developer documentation (container image, Compose quickstart
  and deployment, troubleshooting, tester and candidate guides, operating notes) MUST
  be updated to the registry flow. Guides that describe only removed machinery MUST
  be deleted.
- **FR-014**: The change MUST include a changelog fragment that tells users where the
  image is now published.
- **FR-015**: Until the first stable 3.0.0 release, the registry project MUST be private,
  with pulls requiring registry credentials issued to OpsMill staff and invited
  testers. From 3.0.0 onward it MUST allow anonymous pulls. Documentation MUST state
  which applies and, while the project is private, how a tester logs in.

### Key Entities

- **Published image**: one multi-platform image in the OpsMill registry, identified by
  digest and reachable by one or more tags (version, `latest`, or dispatch-chosen),
  with a signature and SBOM attestations attached.
- **Image tags**: human-facing names that point at a digest. Release tags come from
  the release version. Dispatch tags come from the maintainer.
- **`docker-compose.yml`** (formerly referred to as "the Compose deployment bundle"):
  the shipped deployment file at the repository root. It names the Sync image by
  registry repository and a default version, and both can be overridden by
  environment variables.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: After the first release with this change, an authorised tester can pull
  and run the Sync image on both amd64 and arm64 with one registry login and one pull
  command. From stable 3.0.0, anyone can do it with the pull command alone.
- **SC-007**: Before stable 3.0.0, an unauthenticated pull of any published tag is
  refused.
- **SC-002**: 100% of images published by release or dispatch pass signature and SBOM
  verification against their digest.
- **SC-003**: On a clean host, the Compose quickstart is reduced to fetching one
  `docker-compose.yml` from a release tag and starting it, with no image load and no
  bundle unpack step. A release's tagged `docker-compose.yml` always names that
  release's version.
- **SC-004**: The repository's image pipeline is one build-and-push workflow plus its
  callers, structurally aligned with `infrahub-mcp`'s, and image-specific workflow and
  task code is reduced by at least 75% in line count.
- **SC-005**: No pull request is blocked by the replaced gate. The required check
  reports on every pull request from the first merge onward.
- **SC-006**: No reference to the removed candidate workflow, tester packet, or image
  archives remains in current documentation, workflows, tasks, or tests.

## Assumptions

- The base branch is `feature/v3-develop`, which holds the image work. `main` has none.
- Scope is the publishing pipeline, the GitHub-artifact handoff, and the bundle archive.
  The Dockerfile and a Compose deployment stay. The deployment becomes a tag-pinned
  `docker-compose.yml` like the siblings', which trades digest pinning for the
  sibling convention; the published signature lets a cautious operator verify the
  digest behind a tag. The other development stacks under `development/` are out of
  scope, except where they reference renamed or removed files.
- Images are published on stable releases and on manual dispatch only. Merges to
  `develop` do not publish a dev image (user choice). This can be added later the
  way `infrahub` does it.
- The registry host, username, and password are already configured at the OpsMill
  organisation level (the `HARBOR_HOST` variable and `HARBOR_USERNAME` /
  `HARBOR_PASSWORD` secrets used by `infrahub-mcp`) and are available to this
  repository, and the `opsmill/infrahub-sync` project exists in the registry or is
  created by an administrator before the first publish.
- The smoke test's health check reaches a liveness signal that does not need the
  database or object store. If the Sync API cannot report healthy without them,
  the plan defines the smallest standalone configuration that lets it.
- Making the registry project private, issuing tester pull credentials, and flipping
  the project to public at 3.0.0 are registry-administrator actions, not code changes.
- Keyless signing through the organisation's existing workflow-identity setup works
  for this repository as it does for the sibling repositories.
- The in-image vulnerability scan and secret-canary checks are part of the
  GitHub-artifact qualification and go with it. The registry's own scanning, and the
  attached SBOM, replace them, as in the sibling repositories.
- Historical changelog fragments, archived specs under `dev/specs/archive`, and
  published release notes are history and are not edited.
- Approving the changelog wording and changing branch-protection settings, if needed,
  are maintainer actions outside the code change.
