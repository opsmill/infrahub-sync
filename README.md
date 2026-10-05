<!-- rumdl-disable MD041 -->
![Infrahub Logo](https://assets-global.website-files.com/657aff4a26dd8afbab24944b/657b0e0678f7fd35ce130776_Logo%20INFRAHUB.svg)
<!-- rumdl-enable MD041 -->

# Infrahub Sync

[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE.txt)

Infrahub Sync synchronizes infrastructure data between Infrahub and other systems, such
as NetBox, Nautobot, IP Fabric, and Slurp'it. You describe the source, the destination,
and the field mapping in one YAML configuration package. The Sync service plans the
changes, you review the plan, and the service applies it.

This branch, `feature/v3-develop`, is V3, which is in testing. V3 runs as a service: a
Sync API, a worker, Prefect, PostgreSQL, and object storage. The `infrahub-sync`
command-line client sends requests to that service.

- **V3 documentation:** [feature-v3-develop.infrahub-sync.pages.dev](https://feature-v3-develop.infrahub-sync.pages.dev/),
  built from this branch.
- **OpsMill staff testing a V3 pre-release:** start at the internal
  [V3 start page](https://github.com/opsmill/infrahub-sync-process#readme).
- **V2** is the version on [PyPI](https://pypi.org/project/infrahub-sync/) and on the
  `main` branch. Its documentation is at [docs.infrahub.app/sync](https://docs.infrahub.app/sync).
  `pip install infrahub-sync` installs V2, not V3.

Infrahub Sync is open source under Apache 2.0 and maintained by [OpsMill](https://opsmill.com)
as part of the [Infrahub](https://github.com/opsmill/infrahub) ecosystem.

---

## What You Can Do With It

- **Migrate from an existing source of truth, one model at a time.** Move data from
  NetBox, Nautobot, or another system into Infrahub while the existing system keeps
  running.
- **Keep Infrahub in sync with another system.** Run the same configuration again. Each
  run plans the differences between the source and Infrahub, and apply writes only those
  differences.
- **Build inventory from discovered equipment.** Read devices and addressing from a
  network discovery tool (IP Fabric, Slurp'it) into Infrahub.
- **Translate between data models.** Map fields in YAML with identifiers, references,
  static values, filters, and Jinja transforms.
- **Review changes before they are written.** `infrahub-sync diff` saves a plan, and
  `infrahub-sync apply` writes exactly the plan you reviewed, bound to its checksum.

---

## Two Ways to Run V3

- **Run from source** — to try the latest changes or use a Mac. Build and start the
  service from a checkout of `feature/v3-develop`, on any platform Docker supports,
  including Apple Silicon.
- **Run the container image** — to run a fixed version. Every release publishes a signed
  image for Linux amd64 and arm64 to `registry.opsmill.io/opsmill/infrahub-sync`, and the
  release tag's `docker-compose.yml` runs the whole service from it.

Both run the same Sync service. A source build has passed the pull request checks, but
not the release checks that each published image passes.

To run the container image, follow
[Install Infrahub Sync](https://feature-v3-develop.infrahub-sync.pages.dev/installation).
To run from source, continue below.

---

<a id="run-it-locally-with-docker"></a>

## Run From Source

You need Docker, [uv](https://docs.astral.sh/uv/), and Python 3.11 to 3.13. The
development stack, `development/docker-compose.dev.yml`, builds the image from your checkout, for your host's own architecture, and
runs the Sync API, the worker, PostgreSQL, object storage, and Prefect:

```bash
uv sync --extra dev --extra prefect --extra service   # once
uv run invoke build
uv run invoke start
```

`start` builds the image only when no `infrahub-sync:dev` image exists. Run `build` first
whenever you test a new revision; otherwise `start` reuses an image built from an older
checkout. After a code change, run `uv run invoke build && uv run invoke start`.

| | |
|---|---|
| **Sync API** | `http://127.0.0.1:8030` — bearer token `infrahub-sync-dev-token` |
| **Prefect UI** | `http://127.0.0.1:4230` |
| **PostgreSQL** | `127.0.0.1:5440` |

```bash
curl -sf http://127.0.0.1:8030/version
curl -sf -H 'Authorization: Bearer infrahub-sync-dev-token' http://127.0.0.1:8030/status
```

The stack starts with no source and no destination. To continue to a first sync from a
local NetBox into a local Infrahub, follow
[Run from source](https://feature-v3-develop.infrahub-sync.pages.dev/development-stack#run-from-source)
in the documentation. In short:

- Infrahub's own Compose file starts a local Infrahub on `http://localhost:8000`: in a
  directory outside the checkout, run `curl https://infrahub.opsmill.io > docker-compose.yml`
  and `docker compose -p infrahub up -d --wait`. `uv run invoke start` connects the worker
  to Infrahub's network, where it reaches Infrahub at `http://infrahub-server:8000`.
- `uv run invoke netbox.seed --dataset demo` starts a local NetBox with the official demo
  data. The worker joins NetBox's network and reaches it at `http://netbox:8080`.
- Set `INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN` and `INFRAHUB_SYNC_CREDENTIAL_NETBOX_TOKEN`, then run `uv run invoke start` again, so
  the worker has both tokens.

When you are finished, run `uv run invoke destroy` to stop the stack and delete its data.

This build keeps its own BuildKit cache, so the first build starts with an empty cache.

---

## Example: NetBox → Infrahub

The repository includes a NetBox configuration package at
`examples/netbox_to_infrahub/package.yml`. This example registers it with a deployed Sync
service; edit the package's URLs for your NetBox and Infrahub first. For the local NetBox and
Infrahub above, run `uv run invoke netbox.demo-package --dev-stack` instead, then follow
[Run a first sync](https://feature-v3-develop.infrahub-sync.pages.dev/development-stack#run-a-first-sync).
Register the package once, then address the immutable configuration version returned by the
service:

```bash
export INFRAHUB_SYNC_API_URL=https://sync.example.com
export INFRAHUB_SYNC_API_TOKEN=<token>

uv run infrahub-sync configs register examples/netbox_to_infrahub/package.yml \
  --reason "register NetBox import"
uv run infrahub-sync diff --config-id <config-id> --version <version> \
  --branch netbox-import --reason "review NetBox import"
uv run infrahub-sync runs plan <run-id> --detail
uv run infrahub-sync apply <run-id> --expected-checksum <checksum> \
  --branch netbox-import --reason "apply reviewed NetBox import"
```

The worker uses the source and destination URLs in the registered package. Environment
variables such as `NETBOX_URL` or `INFRAHUB_ADDRESS` do not change them; only the tokens
come from the worker's environment. Read the
[example prerequisites](examples/netbox_to_infrahub/README.md) before you register it.

For a complete walkthrough, see the
[NetBox-to-Infrahub tutorial](https://feature-v3-develop.infrahub-sync.pages.dev/tutorials/netbox-demo-to-infrahub).

---

## Operations

**Scheduling.** There is no built-in schedule. Start each run with the CLI or the Sync API,
for example from cron or a CI job. The service keeps a durable record of each run, so a
caller can use `--no-wait`, disconnect, and read the same run later.

**Observability.** Sync runs write lifecycle and adapter logs through Python logging.
Public Python API lifecycle records include structured attributes such as the run
identifier, operation, stage, and outcome. Forward these records to the logging system
your scheduler or runtime uses.

**Failure handling.** After a failed write, inspect the run record and the destination,
then create a fresh plan. A failed operation can have written part of its change, unless
the run records that it wrote nothing. See
[Run a sync](https://feature-v3-develop.infrahub-sync.pages.dev/running-a-sync) for
recovery.

**Deletes.** A plan records the destination objects that have no match in the source.
Apply never executes those deletes.

---

## What's Included

### Pre-configured adapters

| Adapter | Direction supported |
|---|---|
| Infrahub | source or destination |
| NetBox | NetBox → Infrahub |
| Nautobot | Nautobot → Infrahub |
| IP Fabric | IP Fabric → Infrahub |
| Cisco ACI | Cisco ACI → Infrahub |
| Peering Manager | Peering Manager → Infrahub |
| Prometheus | Prometheus → Infrahub |
| Slurp'it | Slurp'it → Infrahub |
| Generic REST API | external system → Infrahub |

The IP Fabric and Slurp'it adapters need a worker image that installs their SDKs. The
default Sync image does not include them; see each adapter's page in the documentation.

### Choosing your adapter

- If your source is in the table above, use that adapter.
- If your source has a REST API but no dedicated adapter (ServiceNow, Infoblox, internal
  IPAM, and others), start with the Generic REST API adapter. The `examples/` directory
  includes Generic REST API configurations for LibreNMS, Observium, Device42, and PeeringDB.
- A registered package must name an adapter bundled with Infrahub Sync. Running a custom
  adapter from a registered package is not qualified in this release; see the
  [local adapters guide](https://feature-v3-develop.infrahub-sync.pages.dev/adapters/local-adapters)
  and the template at `examples/custom_adapter/`.

### Components

- **Sync service.** The Sync API registers configuration packages, admits runs, and keeps
  run records, saved plans, and artifacts. The worker reads the source and destination and
  writes the reviewed plan.
- **Declarative YAML configuration.** Per-field mapping with 14 filter operations
  (including `regex` and `is_ip_within`), per-field transforms, custom Jinja filters, and
  cross-reference resolution. When `order` is omitted, the write order comes from the
  mapping's references.
- **Typer-based CLI.** Register and inspect configuration packages, create plan or sync
  runs, review saved plans, and apply a reviewed checksum through the Sync API.
- **Custom CA certificates.** Trust an internal CA for the CLI's connection to the Sync API. A
  Compose worker cannot trust a custom CA yet; see the custom certificates guide.

### Execution surfaces

| Surface | Use it for | Runtime requirements |
|---|---|---|
| CLI | Configuration registration, plan review, run admission, and reviewed-plan apply | Base installation and Sync API access |
| Python client | Typed access to every shipped Sync API resource | Base installation and Sync API access |
| Direct Prefect deployment | Starting and observing one read-only plan through Prefect's API | `prefect` extra and a Prefect server |
| Sync HTTP API | Authenticated remote runs, durable records and artifacts, reviewed apply, idempotency, and cancellation | `service` extra, Prefect, a work pool, a worker, and shared durable storage |

See the [Python API](https://feature-v3-develop.infrahub-sync.pages.dev/reference/python-api),
[Prefect remote run](https://feature-v3-develop.infrahub-sync.pages.dev/reference/prefect-remote-run), and
[Sync HTTP API](https://feature-v3-develop.infrahub-sync.pages.dev/reference/sync-http-api) references
for their contracts and setup. For a live plan and apply from a checkout, follow
[Run a first sync](https://feature-v3-develop.infrahub-sync.pages.dev/development-stack#run-a-first-sync),
which imports the NetBox demo data into a local Infrahub.

---

## Going Deeper

| | |
|---|---|
| **Install and run** | [Install Infrahub Sync](https://feature-v3-develop.infrahub-sync.pages.dev/installation) · [Create a sync project](https://feature-v3-develop.infrahub-sync.pages.dev/creating-a-sync-project) · [Run a sync](https://feature-v3-develop.infrahub-sync.pages.dev/running-a-sync) |
| **Full documentation** | [Infrahub Sync V3 docs](https://feature-v3-develop.infrahub-sync.pages.dev/) |
| **All adapters** | [Choose an adapter](https://feature-v3-develop.infrahub-sync.pages.dev/adapters/choosing-an-adapter) |
| **Configuration reference** | [Sync instance configuration](https://feature-v3-develop.infrahub-sync.pages.dev/reference/config) · [CLI reference](https://feature-v3-develop.infrahub-sync.pages.dev/reference/cli) |
| **Custom CA certificates** | [Custom certificates guide](https://feature-v3-develop.infrahub-sync.pages.dev/custom-certificates) |
| **Contribute** | [Contributing guide](https://feature-v3-develop.infrahub-sync.pages.dev/contributing) — development environment, tests, code standards · [Local development stack](https://feature-v3-develop.infrahub-sync.pages.dev/development-stack) — both local stacks |

---

## Questions or Contributing?

- **Report a bug or request a feature** — [GitHub Issues](https://github.com/opsmill/infrahub-sync/issues)
- **Discuss with the community** — [discord.gg/opsmill](https://discord.gg/opsmill)
- **Contribute code or docs** — see the [Contributing guide](https://feature-v3-develop.infrahub-sync.pages.dev/contributing)

---

## About Infrahub

[Infrahub](https://github.com/opsmill/infrahub) is an open source infrastructure data management and automation platform (Apache 2.0), developed by [OpsMill](https://opsmill.com). Infrastructure teams use it as a schema-driven source of truth with built-in version control and native integrations with Git, Ansible, and Terraform.
