# Research: NetBox demo ready to try

## R-1. How the image gets the schema and the package without a checkout

- **Decision**: ship both inside the wheel with hatchling `force-include`:
  `tests/data/nightly_schema/traditional-infrastructure-sot/` → `infrahub_sync/demo/assets/schema/`
  and `examples/netbox_to_infrahub/package.yml` → `infrahub_sync/demo/assets/netbox/package.yml`.
  `infrahub_sync/demo/assets.py` reads them with `importlib.resources`; when the package runs from
  the source tree (editable install, where `force-include` does not apply), it falls back to the two
  repository paths. The `Dockerfile` copies the two source paths into the build context before the
  wheel build.
- **Rationale**: one copy of each file in the repository. The schema copy is already pinned, its
  bytes are checked against a recorded SHA-256 digest by the nightly runner, and the status
  translation test reads it; moving it would churn those checks. The CLI from PyPI also gets the
  command, which a later "existing environment" path can use.
- **Alternatives considered**: (a) copy files into `infrahub_sync/demo/assets/` in git: two copies
  that drift. (b) `COPY` into the image only, at `/opt/infrahub-sync/demo`: the command would not
  work from a wheel, and the path becomes an image contract. (c) fetch from the marketplace at run
  time: refused by FR-007, and the documented `ReadTimeout` makes it unreliable.

## R-2. Rewriting the package addresses

- **Decision**: move `local_package_text` (and its two shipped-URL constants) from `tasks/netbox.py`
  to `infrahub_sync/demo/package.py`; `tasks/netbox.py` imports it. The function already refuses a
  package whose two `url:` lines are not each present exactly once.
- **Rationale**: the image cannot import `tasks/`; one implementation serves the task and the command.
- **Alternatives considered**: parse and rewrite the YAML: it would reorder keys and drop comments,
  so the registered package would differ from the shipped one for no reason.

## R-3. Registering idempotently

- **Decision**: `list_configs(name=<package name>)`. None → `register_config`. Otherwise
  `create_config_version` on the newest; the client documents it as "create or retrieve an identical
  configuration version", and the route answers 200 for an identical version and 201 for a new one.
  The idempotency key is per operation and target: `demo-prepare-register-<sha12>` or
  `demo-prepare-version-<config_id>-<sha12>`, so that a retry after a lost answer replays instead
  of creating a second configuration, and a key is never reused for another route or body (the
  service answers 409 `idempotency-conflict` to that).
- **Rationale**: uses the API's own identity rules; no new endpoint.
- **Alternatives considered**: always `register_config`: registering changed content creates a new
  configuration ID at version 1 (documented in "Run a first sync"), which breaks FR-008.
- **Edge**: two configurations with the same name (made by hand). The command versions the newest and
  says which one it chose.

## R-4. Loading the schema

- **Decision**: `infrahub_sdk.InfrahubClientSync(address, config=Config(api_token=...))`,
  `client.schema.load(schemas=[...], wait_until_converged=True)` on the 16 files read as YAML;
  `response.schema_updated` decides `loaded` or `unchanged`; `response.errors` stops the command with
  exit code 1 and the error text. The token is read from `INFRAHUB_API_TOKEN`, the variable
  `infrahubctl` already uses.
- **Rationale**: `infrahub-sdk` is a base dependency; `infrahubctl` (the `ctl` extra) is only in the
  `service` extra and should not be shelled out to.
- **Alternatives considered**: shell out to `infrahubctl schema load`: extra process, output parsing,
  and not available in the base install.

## R-5. Tokens

- **Decision**: the Sync API token and URL come from the CLI's existing global options and their
  environment variables (`INFRAHUB_SYNC_API_URL`, and the token variable the CLI already reads). The
  Infrahub token comes from `INFRAHUB_API_TOKEN`. No token is accepted as a command argument. Errors
  from both clients are printed through the CLI's existing redacting error handler.
- **Rationale**: FR-010; `docs/docs/development/guidelines/secret-redaction.md`.

## R-6. Pre-release images

- **Decision**: no code. `ci-docker-image.yml` `workflow_dispatch` takes `ref`, `tags`, `labels` and
  `version`, and builds both platforms, signs and attaches the SBOM.
  `docs/docs/development/guides/publishing-an-image.md` documents "a pre-release image for testers,
  under a tag such as `3.0.0a6-rc1`" and "Why a dispatch leaves `latest` alone".
- **Rationale**: FR-012 and FR-013 are already met; the work is to point to the guide.

## R-7. Why a new `demo` preset instead of `preview`

- **Decision**: add a `demo` preset: local Infrahub and local NetBox, no smoke seed.
- **Rationale**: `preview` writes the smoke schema and the `core01` device into the same Infrahub, and
  the tutorial warns that loading the destination schema over another schema can fail. The demo
  Infrahub must hold only the destination schema (spec clarification). `preview` stays as it is for
  the smoke suite.
- **Alternatives considered**: a flag on `preview`: two meanings for one preset name.

## R-8. Where feedback goes

- **Decision** (revised after critique P2): the page ends with a "Send feedback" section that asks
  for feedback through the channel named in the invitation to the round, and lists what to include:
  the way of trying the demo used, the step where they got stuck, the command or screen, and the
  message shown. The page names no public tracker.
- **Rationale**: solution architects think of customers while trying the demo, and feedback can
  name them; a public issue tracker would publish that. The spec leaves the destination to the
  maintainer, who names it in the invitation.
- **Alternatives considered**: public GitHub issues (rejected, above).

## R-9. The web interface in the development stack

- **Decision**: rely on the image's `INFRAHUB_SYNC_WEB_DIRECTORY` (set in the `Dockerfile`); the Sync
  API of the development stack, built from the same `Dockerfile`, serves the interface at its root,
  `http://127.0.0.1:8001`. Verify during implementation; if the development image does not include
  the built interface, `dev.demo` prints the `dev.frontend` instruction instead.
- **Verified (2026-11-18, T014)**: `uv run invoke dev.build` built `infrahub-sync:dev`; the image
  sets `INFRAHUB_SYNC_WEB_DIRECTORY=/opt/infrahub-sync/web` and holds `index.html` there. The
  development file's `sync-api` keeps the image command (`python -m infrahub_sync.service.serve`),
  does not set the variable, and mounts only the Python packages, not the web directory. With the
  development source mount, `create_site(FastAPI(), web_directory())` in that image answers
  `GET /` with `Accept: text/html` with 200 and the entry page. A full stack was not started, because
  a stack that this session did not start exists on this machine under the same project names. So `dev.demo` prints the Sync API address as the
  web interface address.
- **Build prerequisite found**: the image build needs the `frontend/vendor/infrahub` submodule, and
  the `Dockerfile` stops without it. A fresh clone does not fetch it, so `dev.demo` fetches it when
  it is missing, as `dev.frontend` does.
