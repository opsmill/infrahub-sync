# Operating an Infrahub Sync deployment

This document ships inside the deployment bundle, so a host that has only the
archive has the procedure. It is the operator's copy of the Compose deployment
guide; the guide on the documentation site covers the same lifecycle with more
context.

One host runs everything: the Sync API, the Sync worker, a Prefect server,
PostgreSQL, and S3-compatible object storage. The host needs Docker Compose
2.17.3 or later and nothing else — no product checkout, no Python, no `uv`. Every
command that needs an interpreter runs inside the Sync image.

## Verify what you have

The archive and its checksum are two files from one release. Check the archive
before extracting it:

```bash
sha256sum -c infrahub-sync-compose-<version>.tar.gz.sha256
tar -xzf infrahub-sync-compose-<version>.tar.gz
cd infrahub-sync-compose-<version>
```

The checksum is a claim about the archive as shipped and about nothing
afterwards. Preparing a deployment writes `operator.env`, which is expected to
leave the extracted tree different from the archive.

The image is named by digest, never by a tag. A tag can be re-pointed between
the qualification that trusted an image and the run that uses it, so the
lifecycle entry point refuses one before it creates anything. Confirm what a
local image actually is:

```bash
docker image inspect --format '{{.Id}}' <reference>
```

That identifier is the image's configuration digest, which is the form the
release record names and the form `INFRAHUB_SYNC_IMAGE` takes.

## Prepare

```bash
./infrahub-sync-compose init
```

That generates `.instance`, `secrets/postgres-admin-password`, and
`operator.env`, with passwords for the two database owner roles, the object
store, and one API principal. One value is yours to supply in `operator.env`
before the first start:

```bash
INFRAHUB_SYNC_IMAGE=sha256:<64 hex>          # or <registry>/<repository>@sha256:<64 hex>
```

A configuration is not a startup input. The deployment starts with an empty
registry and no destination, and you register a declared package through the
Sync API once it is running. `configuration/qualification.yaml` in this bundle is
an example of that package's shape, and nothing loads it on your behalf. A
package holds credential *references*, never values.

The credentials a registered package references are yours to add to
`operator.env` before the run that needs them, and `init` leaves each one
commented:

```bash
INFRAHUB_API_TOKEN=<your Infrahub token>
```

A container reads its environment once, at start. After changing any value in
`operator.env`, run `./infrahub-sync-compose start` again: that recreates the
services whose environment changed, where `restart` would replace the processes
inside containers that keep the environment they were created with.

### Reading from NetBox or Nautobot

A package that reads from one of those declares the endpoint and a reference to
the token, and `init` leaves both names commented in `operator.env`. Uncomment
only the one your package names:

```bash
NETBOX_TOKEN=<your NetBox token>
```

```yaml
configuration:
  source:
    name: netbox
    settings:
      url: "http://netbox.example.net:8080"
      token:
        $credential: netbox-token
credentials:
  netbox-token:
    provider: env
    identifier: NETBOX_TOKEN
```

Nautobot is the same shape with `nautobot`, `nautobot-token` and
`NAUTOBOT_TOKEN`.

Four things decide whether this works:

- **The URL is resolved inside a container, not on your host.** `localhost`
  there is the worker itself. Name a host the Compose network can reach, or the
  host's own address; `127.0.0.1` and `localhost` will not do.
- **The declared `url` is the only one used.** Exporting `NETBOX_ADDRESS`,
  `NETBOX_URL`, `NAUTOBOT_ADDRESS` or `NAUTOBOT_URL` in your shell changes
  nothing — a registered run reads what the package declares.
- **A missing or empty token fails that run, not the deployment.** The worker
  refuses with the environment variable's name, never its value. Both tokens are
  optional, so a deployment reading from neither source starts normally, and a
  package that names one source does not need the other's token.
- **The worker reads its environment once, at start.** After changing either
  value in `operator.env`, run `./infrahub-sync-compose start`; `restart` keeps
  the environment each container already has.

Only the worker is given these tokens. The API, the bootstrap job, PostgreSQL,
the object store and the Prefect server never receive one: registration and the
default validation judge declared content without resolving a source secret.

That both adapters are installed and import is a packaging fact. It is not a
statement that any particular NetBox or Nautobot version has been qualified
against this release.

## Start

```bash
./infrahub-sync-compose preflight
./infrahub-sync-compose start
```

`preflight` refuses before anything is created. Each refusal is one family name
and a fixed sentence, and none of them renders a credential value:

| Family | What to do |
| --- | --- |
| `compose-unreadable`, `compose-too-old` | Install Docker Compose 2.17.3 or later. |
| `no-docker`, `docker-unavailable` | Docker is absent from `PATH`, or it could not enumerate or inspect this instance. Neither is evidence that the deployment is stopped. |
| `no-instance` | This bundle has no identity yet. Run `init`. |
| `no-operator-settings` | `operator.env` does not exist. Run `init`. |
| `path-unwritable`, `path-unreadable`, `path-missing` | The bundle directory or `secrets/` is not usable by this user. |
| `credentials-missing` | A required setting is empty or still holds `REPLACE-ME`. |
| `image-not-immutable` | `INFRAHUB_SYNC_IMAGE` names a tag or a malformed digest. |
| `image-unresolvable` | Docker cannot find that digest locally or in a registry. |
| `port-unset` | `INFRAHUB_SYNC_BIND_ADDRESS`, or one of the two port settings, names nothing. |
| `port-occupied`, `port-unprovable` | A required loopback bind is held, or the bind probe could not run. |
| `cli-usage` | `cli --package` was given no file. |
| `package-unusable` | The named package is not a readable regular file, or its copy could not be staged for that call. The refusal names the path and never its content. |
| `foreign-resource` | A resource under this name carries another instance's label. Nothing was changed. |
| `not-ready` | `start` brought the deployment up and no live worker registered inside `INFRAHUB_SYNC_READY_TIMEOUT` seconds. |
| `confirmation-required` | `reset` was not given this deployment's exact identity. |

`start` runs `preflight`, brings the deployment up, and returns once the Sync API
reports a worker that has registered. Repeating it is safe: bootstrap converges
the two databases and their owners, the product schema, the artifact bucket, the
Prefect work pool, and the installed deployment, and a second run creates none of
them twice. It registers nothing, so a repeat also leaves every configuration you
registered exactly as it was.

`READY` is a statement about this deployment's own dependencies and a live
worker. It says nothing about whether a configuration is registered, whether one
is valid, or whether its destination answers — a deployment reaches `READY` with
an empty registry, no credentials and no reachable destination.

Both published surfaces bind to loopback: the Sync API on `127.0.0.1:8000`, the
Prefect UI and API on `127.0.0.1:4200`.

## Register a configuration and run it

`cli` runs the shipped CLI against this deployment, in a container of the same
image and on the deployment's own network. It authenticates with
`INFRAHUB_SYNC_API_TOKEN`, which `init` wrote as the same value as the principal
in `INFRAHUB_SYNC_SERVICE_BEARER_TOKENS`; those two settings are one credential,
so a hand edit to either has to be made to both.

Each call is one container, removed when the command ends. It publishes nothing,
starts no service as a side effect, and keeps no volume.

`--package` gives one call one file: its bytes are copied into a private
directory, mounted read-only at `/input/package.yaml`, and removed afterwards.
Your own file is never mounted and never modified.

```bash
./infrahub-sync-compose init
# Set the immutable image in operator.env; no configuration or destination is needed yet.
./infrahub-sync-compose start
./infrahub-sync-compose cli configs list
# Add destination/source credentials to operator.env before planning; recreate affected services
# using start after changed environment; restart alone retains the old container environment.
./infrahub-sync-compose start
./infrahub-sync-compose cli --package ./package.yml -- configs register /input/package.yaml --reason 'register my configuration'
./infrahub-sync-compose cli configs show CONFIG_ID
./infrahub-sync-compose cli configs versions CONFIG_ID
./infrahub-sync-compose cli configs validate CONFIG_ID 1
./infrahub-sync-compose cli diff --config-id CONFIG_ID --version 1 --branch main --reason 'review initial sync'
./infrahub-sync-compose cli runs plan RUN_ID --detail
./infrahub-sync-compose cli apply RUN_ID --expected-checksum CHECKSUM --branch main --reason 'apply reviewed initial sync'
./infrahub-sync-compose cli runs show RUN_ID
./infrahub-sync-compose cli runs results RUN_ID
./infrahub-sync-compose cli diff --config-id CONFIG_ID --version 1 --branch main --reason 'verify unchanged source'
# Versioning is explicit, and never rewrites a registered version:
./infrahub-sync-compose cli --package ./edited-package.yml -- configs version CONFIG_ID /input/package.yaml --reason 'register edited configuration'
```

`CONFIG_ID`, `RUN_ID` and `CHECKSUM` are results the previous commands printed.
`--` separates this wrapper's own options from the CLI's arguments.

Four things worth knowing before the first plan:

- **`READY` is not a statement about a configuration.** It says the Sync-owned
  dependencies answer and a worker is live. Whether a registered package is
  valid is what `configs validate` answers; whether its destination answers is
  what the first run against it answers.
- **A package's URLs are resolved inside a container.** `localhost` there is the
  container itself, so name an address the Compose network can reach.
- **A package's credentials are references.** Their values live in
  `operator.env`, so no secret is registered, stored or printed. Add one before
  the run that needs it and run `start` again.
- **Registered content is preserved.** It lives in PostgreSQL, and the file you
  registered from is not mounted by anything afterwards. A registered version is
  immutable: register an edit as the next version with `configs version`.

## Status and logs

```bash
./infrahub-sync-compose status
./infrahub-sync-compose logs sync-worker
```

| State | Exit | What it means |
| --- | --- | --- |
| `READY` | 0 | Dependencies answer, the API answers, and a registered worker is heartbeating. |
| `DEGRADED` | 3 | Something owned exists, but not all of that is true. |
| `STOPPED` | 4 | No container of this instance is running. |

Container state establishes absence and nothing else. A paused or hung worker
keeps looking healthy to Docker and still reaches `DEGRADED`, because readiness
comes from what the API reports about the Prefect registry. The log tail is
bounded — 200 lines per service, or `INFRAHUB_SYNC_LOG_LINES`.

## Stop, restart, reset

```bash
./infrahub-sync-compose stop                    # processes down, every volume untouched
./infrahub-sync-compose restart                 # replace the API and worker processes
./infrahub-sync-compose reset <instance identity>   # remove this instance and its volumes
```

Each resolves its targets by explicit name and checks that every one carries this
instance's label before changing anything. A resource that exists under another
label is refused and the operation stops before its first mutation. `reset`
additionally requires you to repeat the identity it displays; there is no forcing
flag. It leaves `operator.env` and `secrets/` in place.

## Replace this alpha

This alpha promises no in-place state migration and no backup or restore. The
replacement procedure is a reset followed by a fresh deployment of the same
bundle and the same image:

```bash
./infrahub-sync-compose reset <instance identity>
./infrahub-sync-compose init          # a new identity; the old one labelled volumes that are gone
./infrahub-sync-compose start
```

The start that follows is a cold bootstrap, not a resumption: the databases, the
product schema, the bucket, the work pool, and the deployment are created from
nothing, and the deployment reaches `READY` with an empty registry, no runs and
no artifacts behind it. Prior run history, retained plans, registered
configurations, and artifacts do not survive. Keep anything you need outside the
deployment before you reset, and register your package again afterwards.

## When the outcome of a write is uncertain

A managed write is admitted once and applied against the checksum of the plan you
reviewed. If a write ends without proving what reached the destination, the
deployment does not retry it. Repeating a write whose outcome is unknown is the
one thing that could turn an uncertain state into a wrong one, so the run is left
terminal and the record carries what is known.

Read the run itself first. `reconciliation_required` is on the run, not buried in
its evidence, so deciding whether a run needs reconciling never requires parsing
a failure:

Both reads are CLI commands, so nothing here needs a token on a command line or
in your shell — `cli` presents the one `init` generated:

```bash
./infrahub-sync-compose cli runs show RUN_ID
./infrahub-sync-compose cli runs results RUN_ID
```

Two records mean an uncertain write, and they are not the same thing:

| What happened | `phase` | `outcome` | Also on the record |
| --- | --- | --- | --- |
| The write execution ended without reporting — a worker that may have written and did not come back | `interrupted` | `ambiguous` | `reconciliation_required` is true |
| The apply began writing and then failed | `apply-failed` | `failed` | `summary.may_have_partially_written` is true, and `results.apply_failure` names the `stage`, the `error_type`, the `applied_operations`, and the `failed_operation` |

`reconciliation_required` is a write-only verdict: an interrupted `plan` or
`verify` cannot have written, so it is not set for one. Nothing sets it back to
false.

Read that record, inspect the destination, and when you know what is there, take
a fresh plan run. A new plan reads the destination as it now is, so the operations
it proposes are the ones still outstanding. The interrupted run stays terminal:
nothing reopens it, and no later run inherits its admission.

A failure that never began writing is an ordinary failed run — no reconciliation
flag, no partial-write marker — and needs only a new plan.

## What this topology does not do

- One host. There is no multi-host deployment and no clustering.
- No public ingress and no TLS termination. Both surfaces bind to loopback.
- No in-place state migration, and no backup or restore.
- The API and the worker share no mount, no volume, and no scratch directory.
  Run state travels between them through PostgreSQL and the object store.
- One Sync image serves the API, the worker, the bootstrap job, and the CLI.
- The CLI reaches the Sync API and nothing else. It is given no storage, Prefect,
  source or destination credential, and no Docker socket.
- Credentials live in `operator.env` and `secrets/`, generated per deployment.
  Neither is part of the archive, and nothing prints their values.
