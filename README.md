<!-- rumdl-disable MD041 -->
![Infrahub Logo](https://assets-global.website-files.com/657aff4a26dd8afbab24944b/657b0e0678f7fd35ce130776_Logo%20INFRAHUB.svg)
<!-- rumdl-enable MD041 -->

# Infrahub Sync

[![PyPI version](https://img.shields.io/pypi/v/infrahub-sync.svg)](https://pypi.org/project/infrahub-sync/)
[![Python versions](https://img.shields.io/pypi/pyversions/infrahub-sync.svg)](https://pypi.org/project/infrahub-sync/)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE.txt)

Infrahub Sync is the data synchronization layer for Infrahub. It connects your existing infrastructure tools — NetBox, Nautobot, IP Fabric, Slurp'it, ServiceNow-style systems, and others — to Infrahub with pre-configured adapters and a short YAML config, so you can unify infrastructure data without writing integration code.

Built on the [`diffsync`](https://github.com/networktocode/diffsync) framework, distributed on [PyPI](https://pypi.org/project/infrahub-sync/) under Apache 2.0, maintained by [OpsMill](https://opsmill.com) as part of the [Infrahub](https://github.com/opsmill/infrahub) ecosystem.

---

## What You Can Do With It

- **Migrate from an existing source of truth, gradually.** Move data from NetBox, Nautobot, or another existing system into Infrahub one model at a time. The legacy system keeps running while your team migrates the automation pipelines, scripts, and dashboards that read from it to Infrahub.
- **Keep two systems continuously in sync.** Synchronize from IPAM, ITSM, monitoring, network discovery, or in-house databases on a recurring schedule managed by your existing automation tooling. Only deltas apply on each run.
- **Publish inventory to downstream systems.** Once Infrahub holds the authoritative inventory, push it to monitoring, observability, or CMDB tools that need a current view of your infrastructure.
- **Build inventory from deployed equipment.** Use a network discovery adapter (IP Fabric, Slurp'it) to populate Infrahub from what is actually deployed in the network, rather than curating inventory by hand.
- **Translate between different data models.** Map fields declaratively in YAML — identifiers, references, static values, transforms, and custom Jinja filters — instead of writing custom transformation code.
- **Preview changes before applying them.** Run `infrahub-sync diff` to see exactly what a sync would change in the destination before any data moves.

---

## Prerequisites

- A running [Infrahub](https://github.com/opsmill/infrahub) instance
- A running Sync API and worker
- Python 3.10–3.13
- Credentials and network access for the source and destination systems

---

## Quick Start

```bash
# Install Infrahub Sync from PyPI into a virtual environment
python3 -m venv .venv
source .venv/bin/activate
pip install infrahub-sync

# Verify the install
infrahub-sync --help
```

→ For step-by-step setup, see [Install Infrahub Sync](https://docs.infrahub.app/sync/installation), [Create a sync project](https://docs.infrahub.app/sync/creating-a-sync-project), and [Run a sync](https://docs.infrahub.app/sync/running-a-sync).

---

## Example: NetBox → Infrahub

The repository includes a NetBox configuration package at
`examples/netbox_to_infrahub/package.yml`. Register it once, then address the immutable
configuration version returned by the service:

```bash
export INFRAHUB_SYNC_API_URL=https://sync.example.com
export INFRAHUB_SYNC_API_TOKEN=<token>

infrahub-sync configs register examples/netbox_to_infrahub/package.yml \
  --reason "register NetBox import"
infrahub-sync diff --config-id <config-id> --version <version> \
  --reason "review NetBox import"
infrahub-sync runs plan <run-id> --detail
```

Before applying the plan, read the
[example prerequisites and current limitations](examples/netbox_to_infrahub/README.md).
The public demo data and destination schema can change independently of this repository,
and the complete example is not an unconditional write-success fixture.

For setup and a complete walkthrough, see the
[NetBox-to-Infrahub tutorial](https://docs.infrahub.app/sync/tutorials/netbox-demo-to-infrahub).

---

## Day 2 Operations

**Scheduling.** The CLI submits runs to the Sync API. The service owns admission, durable
run records, and worker execution, so a caller can disconnect after using `--no-wait` and
inspect the same run later.

**Observability.** Sync runs emit lifecycle and adapter logs through Python logging. Public
Python API lifecycle records include structured attributes such as the run identifier,
operation, stage, and outcome. Forward these records to the logging system used by your
scheduler or runtime.

**Failure handling.** After a failed write, inspect the run record and destination, then
calculate a fresh plan. A failed operation can have written part of its change, and safe
re-application is not established for destination kinds whose identity crosses a
relationship. See [Run a sync](https://docs.infrahub.app/sync/running-a-sync) for the
recovery and convergence boundaries. The three `diffsync_flags` (`SKIP_UNMATCHED_DST`,
`SKIP_UNMATCHED_SRC`, `SKIP_MODIFIED`) and per-mapping filters control what each project
may change.

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
| Peering Manager | Peering Manager → Infrahub · Infrahub → Peering Manager |
| Prometheus | Prometheus → Infrahub |
| Slurp'it | Slurp'it → Infrahub |
| Generic REST API | external system → Infrahub |

### Choosing your adapter

- If your source is in the table above, use that adapter directly.
- If your source has a REST API but no dedicated adapter (ServiceNow, Infoblox, internal IPAM, etc.), start with the Generic REST API adapter — most teams do not need anything more.
- If your source has a non-standard API or you need custom logic, write a local custom adapter using the template at `examples/custom_adapter/`.

### Under the hood

- **Sync engine.** Built on `diffsync` with three sync flags (`SKIP_UNMATCHED_DST` default, `SKIP_UNMATCHED_SRC`, `SKIP_MODIFIED`) and optional Redis-backed store for stateful sync.
- **Declarative YAML configuration.** Per-field mapping with 14 filter operations (including `regex` and `is_ip_within`), per-field transforms, custom Jinja filters, and ordered cross-reference resolution.
- **Typer-based CLI.** Register and inspect configuration packages, create plan or sync
  runs, review service-owned plans, and apply a reviewed checksum through the Sync API.
- **Custom adapters and certificates.** Load custom adapters from filesystem paths, Python module paths, or installed entry points (`INFRAHUB_SYNC_ADAPTER_PATHS`); custom CA certificate support for internal PKI.
- **Example library.** Sample YAML configurations under `examples/` cover every pre-configured adapter plus additional targets (Device42, PeeringDB) and a custom adapter template. Review each example's adapter, schema, credential, and destination prerequisites before running it.

### Execution surfaces

| Surface | Use it for | Runtime requirements |
|---|---|---|
| CLI | Configuration registration, plan review, run admission, and reviewed-plan apply | Base installation and Sync API access |
| Python client | Typed access to every shipped Sync API resource | Base installation and Sync API access |
| Direct Prefect deployment | Starting and observing one plan or confirmed sync through Prefect's API | `prefect` extra and a Prefect server |
| Sync HTTP API | Authenticated remote runs, durable records and artifacts, reviewed apply, idempotency, and cancellation | `service` extra, Prefect, a work pool, a worker, and shared durable storage |

See the [Python API](https://docs.infrahub.app/sync/reference/python-api),
[Prefect remote run](https://docs.infrahub.app/sync/reference/prefect-remote-run), and
[Sync HTTP API](https://docs.infrahub.app/sync/reference/sync-http-api) references
for their contracts and setup. For a bounded checkout-based live review, follow the
[`custom-example` plan and apply guide](examples/custom_adapter/README.md). Its source
fixture is deterministic; the review still uses a live, writable Infrahub destination.

---

## Going Deeper

| | |
|---|---|
| **Install and run** | [Install Infrahub Sync](https://docs.infrahub.app/sync/installation) · [Create a sync project](https://docs.infrahub.app/sync/creating-a-sync-project) · [Run a sync](https://docs.infrahub.app/sync/running-a-sync) |
| **Full documentation** | [Infrahub Sync docs](https://docs.infrahub.app/sync) |
| **All adapters** | [Adapter reference](https://docs.infrahub.app/sync#adapters) |
| **Configuration reference** | [Sync instance configuration](https://docs.infrahub.app/sync/reference/config) · [CLI reference](https://docs.infrahub.app/sync/reference/cli) |
| **Custom CA certificates** | [Custom certificates guide](https://docs.infrahub.app/sync/custom-certificates) |
| **Local custom adapters** | [Local adapters guide](https://docs.infrahub.app/sync/adapters/local-adapters) |
| **Contribute** | [Contributing guide](https://docs.infrahub.app/sync/contributing) — development environment, tests, code standards · [Local development stack](https://docs.infrahub.app/sync/development-stack) — the full stack on your machine |

---

## Questions or Contributing?

- **Report a bug or request a feature** — [GitHub Issues](https://github.com/opsmill/infrahub-sync/issues)
- **Discuss with the community** — [discord.gg/opsmill](https://discord.gg/opsmill)
- **Contribute code or docs** — see the [Contributing guide](https://docs.infrahub.app/sync/contributing)

---

## About Infrahub

[Infrahub](https://github.com/opsmill/infrahub) is an open source infrastructure data management and automation platform (Apache 2.0), developed by [OpsMill](https://opsmill.com). Infrastructure teams use it as a schema-driven source of truth with built-in version control and native integrations with Git, Ansible, and Terraform.
