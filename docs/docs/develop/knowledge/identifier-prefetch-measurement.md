---
title: "Identifier prefetch measurement"
---

## Identifier prefetch measurement

> Part of: Develop > Knowledge | Related: [Adapter anatomy](adapter-anatomy.md), [Planned writes and apply](planned-write-and-apply.md)

When the Infrahub adapter loads a model in bulk, it asks the SDK for the ordered union of the
model's identifiers and mapped attributes (`identifier_and_attribute_fields` in
`infrahub_sync/adapters/infrahub.py`). This page records what that wider request costs
against a live Infrahub, and whether an attributes-only request would be cheaper. It
recommends keeping the union.

### What was compared

- **Union** is the shipped behavior: `include` carries identifiers and attributes.
- **Attributes-only** is a measurement baseline. It patches the field selection down to
  `model._attributes`. It is not a safe replacement: a server or SDK that returns only the
  requested fields would then drop the identifiers a model is keyed on.

Both variants run through the real `InfrahubAdapter.model_loader` on the same data, with a
fresh SDK store for each load. The model classes come from the product's own
`build_runtime_models`, so `_identifiers` and `_attributes` are the runtime projection of the
configuration. A wrapper around the SDK's `_post` records each GraphQL request: its query
text, response bytes and latency. A bounded peer fetch is a request whose query filters by
`ids`. The `list_existing_ids` scan is measured separately, because no product entry point
calls it.

A **field count** is the number of names in the query's selection sets. The operation name,
arguments, aliases and directives are not counted. Every query measured is stored, with the
path of every selected field, in the report the script writes.

### Two scenarios

Both run through
[`development/identifier_prefetch_study.py`](https://github.com/opsmill/infrahub-sync/blob/feature/v3-develop/development/identifier_prefetch_study.py).

| Scenario | Data | What it shows |
| --- | --- | --- |
| `netbox` | The `from-netbox` example import of the pinned NetBox demo dataset, read from the Infrahub branch it populated | Relationship density and identities of the qualified NetBox-to-Infrahub import. The script writes nothing. |
| `synthetic` | 18 generated kinds, 420 objects at scale 1 and 5,040 at scale 12 | A scale probe, and the scalar, relationship and generic identity shapes in isolation. |

#### The NetBox scenario

The scenario is the
[`from-netbox` example check](../guidelines/testing-tiers.md#the-from-netbox-example-check):
`netbox.seed --dataset demo`, a fresh preview stack, the schema snapshot in
`tests/data/nightly_schema/`, and the package that `netbox.demo-package` writes. A `diff`
then a `sync` run into a new branch. The study then reads that branch with the package's
mapping.

The earlier qualified scenario is described as 420 destination objects in 18 kinds. This
repository records no dataset or mapping for it, so it was not reconstructed. On the pinned
dataset (`netbox-demo-v4.7.sql`) and the current mapping, the import plans 1,688 operations:
1,687 creates and one delete that apply does not execute. It populates 20 of the 21 mapped
kinds. `InterfaceVirtual` is mapped but has no objects in the demo data.

| Identity shape | Kinds in this scenario |
| --- | --- |
| Scalar only (`name`) | `BuiltinTag`, `LocationSite`, `OrganizationManufacturer`, `IpamVRF` |
| Relationship plus name, such as `InterfaceLag(device, name)` | `InterfaceLag`, `InterfacePhysical`, `DcimDevice(location, name)`, `LocationRack(name, site)`, `DcimDeviceType(name, manufacturer)` |
| Relationship to a generic | `DcimDevice.location`, whose peer kind is the `LocationHosting` generic |
| Two relationships | `IpamVLAN(name, vlan_id, vlan_group)`, `IpamPrefix(prefix, ip_namespace)` |

#### The synthetic scenario

| Identity shape | Kinds |
| --- | --- |
| Scalar only (`name`) | `Site`, `Vendor`, `Platform`, `Role`, `Vlan`, `Tag`, `Prefix` |
| Relationship plus name, such as `InterfaceLag(device, name)` | `Device`, `Rack`, `Pdu`, `Circuit`, `Interface`, `InterfaceLag`, `Module`, `Psu`, `Fan`, `Port` |
| Relationship to a generic, `Mount(host, name)` | `Mount`, whose peers are `Rack` nodes through the `Hosting` generic |

Each relationship-valued object points at one peer, spread evenly over the peer kind. The
runtime projection matches the identity above: `BenchInterfaceLag` has identifiers
`(device, name)` and attributes `(description, serial)`, so its selection is
`device, name, description, serial`. `BenchSite` has identifiers `(name)` and its selection is
`name, description, serial`.

### Setup

| Item | Value |
| --- | --- |
| Infrahub server | 1.10.6, from `development/docker-compose.infrahub.yml` (preview stack for the NetBox scenario) |
| Infrahub SDK | 1.23.2 |
| Page size | 50 (the SDK default) |
| Machine | One host; Infrahub, Neo4j and the client share it |
| Branch | NetBox scenario: the import branch, read only. Synthetic: one branch created for the run. `main` is never written |
| Cache conditions | One untimed warm-up load; then timed loads alternate between variants; the SDK store starts empty each time |
| Repeats | Five loads per variant for the NetBox scenario and synthetic scale 1; three for scale 12; three for the reversed and scan runs |

```bash
INFRAHUB_ADDRESS=http://localhost:8080 INFRAHUB_API_TOKEN=<token> \
  uv run python development/identifier_prefetch_study.py --scenario netbox \
  --package .netbox/from-netbox.local.yml --branch netbox-import --output out.json

INFRAHUB_ADDRESS=http://127.0.0.1:8000 INFRAHUB_API_TOKEN=<token> \
  uv run python development/identifier_prefetch_study.py --scale 1 --repeats 5 --output out.json
```

### What the query looks like

Attributes-only selects an identifier relationship as the peer's `id`, `hfid`,
`display_label` and `__typename`. The union also selects the peer's own attributes and
nested relationships. Only kinds whose identity or attributes cross a relationship change.

For `InterfaceLag` in the NetBox scenario, the union query has 178 fields and 8,412
characters. Attributes-only has 155 fields and 7,269. Under `device`, the union adds:

```graphql
device {
    node {
        id hfid display_label __typename
        description { value }
        name { value }
        tags { edges { node { id hfid display_label __typename } } }
        platform { node { id hfid display_label __typename } }
        primary_address { node { id hfid display_label __typename } }
    }
}
```

Attributes-only selects `device { node { id hfid display_label __typename } }`. Every other
selection of `InterfaceLag` is the same. These kinds differ; the others are identical in both
variants:

| Kind | Chars, union | Chars, attributes-only | Fields, union | Fields, attributes-only |
| --- | --- | --- | --- | --- |
| `DcimDeviceType` | 2,263 | 942 | 51 | 26 |
| `LocationRack` | 3,153 | 1,434 | 69 | 37 |
| `IpamVLAN` | 1,619 | 1,146 | 41 | 31 |
| `InterfaceLag` | 8,412 | 7,269 | 178 | 155 |
| `InterfaceVirtual` | 7,825 | 6,682 | 164 | 141 |
| `InterfacePhysical` | 6,220 | 5,077 | 135 | 112 |
| `DcimDevice` | 5,842 | 5,751 | 134 | 132 |
| `IpamPrefix` | 3,675 | 3,486 | 88 | 84 |
| `IpamIPAddress` | 3,154 | 2,965 | 75 | 71 |

In the synthetic scenario, `BenchInterfaceLag` grows from 726 characters and 20 fields to
1,291 characters and 32 fields. The 12 extra fields are the peer `BenchDevice`'s
`description`, `name`, `serial` and a nested `site` selection. `BenchSite`, which has a
scalar identity, is identical in both variants (489 characters, 14 fields). The `ids`-filtered
peer queries and the `list_existing_ids` queries are captured the same way in the report.

### Results: NetBox scenario, dependency order

The destination is loaded with no `order` configured, so the engine computes a dependency
order: peers before the kinds that reference them. This is the order a user run reaches when
the configuration has no `order`. Medians of five loads. 1,688 objects, 47 bulk requests.

| Measure | Union | Attributes-only |
| --- | --- | --- |
| Requests | 218 | 256 |
| Peer fetches | 171 | 209 |
| Response bytes | 1,639,280 | 1,418,878 |
| Load seconds | 41.6 | 45.2 |
| Peak Python memory, MiB | 46.7 | 46.2 |
| Server errors | 0 | 0 |

The union saves 38 peer fetches here and sends 15.5% more bytes. It finishes about 8% sooner.
Attributes-only also fetches `IpamNamespace` and `LocationSite` peers by `ids`; the union does
not. Both variants fetch `DcimDevice`, `InterfaceLag` and `LocationRack` peers. Why peers are
missing from the store in a dependency-ordered load of this data was not investigated.
Python memory is measured with `tracemalloc` on one separate load, so it is an estimate and the
two figures are the same within noise.

### Results: NetBox scenario, consumers before peers

An explicit `order` in the configuration is returned unchanged by
`SyncConfig.compute_order_and_tiers` (`infrahub_sync/__init__.py:169`), and the engine installs
it as `top_level` on both adapters (`infrahub_sync/utils.py:272`, `infrahub_sync/potenda/__init__.py:306`). A user can therefore put
consumers before their peers. This case reverses the dependency order and passes it as an
explicit order. Three loads.

| Measure | Union | Attributes-only |
| --- | --- | --- |
| Requests | 333 | 416 |
| Peer fetches | 286 | 369 |
| Response bytes | 1,705,906 | 1,514,270 |
| Load seconds (median) | 53.5 | 57.1 |

The union sends 12.7% more bytes, makes 22% fewer peer fetches and finishes 6% sooner.

### Results: NetBox scenario, `list_existing_ids` scan (no product caller)

The scan does not populate the SDK store, so both variants fetch each peer once.

| Measure | Union | Attributes-only |
| --- | --- | --- |
| Requests | 426 | 426 |
| Peer fetches | 379 | 379 |
| Response bytes | 1,749,666 | 1,505,733 |
| Seconds (median) | 66.4 | 62.9 |

Prefetching identifiers did not save a fetch here. Do not read these numbers as a cost of any
user run.

### Results: synthetic scale probe, dependency order

Peers load before the kinds that reference them. Medians of five loads at scale 1 and three
at scale 12.

| Measure | Scale 1, union | Scale 1, attributes-only | Scale 12, union | Scale 12, attributes-only |
| --- | --- | --- | --- | --- |
| Objects | 420 | 420 | 5,040 | 5,040 |
| Requests | 21 | 21 | 107 | 107 |
| Peer fetches | 0 | 0 | 0 | 0 |
| Response bytes | 269,145 | 183,181 | 3,273,099 | 2,224,945 |
| Load seconds | 6.1 | 5.2 | 64.7 | 43.2 |
| Peak Python memory, MiB | 12.7 | 12.5 | 58.4 | 44.8 |
| Server errors | 0 | 0 | 0 | 0 |

In this dataset every peer is already in the SDK store when its consumer loads, so the union
saves no request and costs about 47% more bytes. The NetBox scenario shows that real data
does not always reach that state.

### Results: synthetic scale probe, consumers before peers

An explicit `order`, as above.

| Measure | Scale 1, union | Scale 1, attributes-only | Scale 12, union | Scale 12, attributes-only |
| --- | --- | --- | --- | --- |
| Requests | 26 | 56 | 167 | 527 |
| Peer fetches | 5 | 35 | 60 | 420 |
| Response bytes | 270,600 | 199,156 | 3,290,759 | 2,418,965 |
| Load seconds (median) | 6.2 | 8.3 | 64.3 | 82.0 |

The union sends 36% more bytes and makes 86% fewer peer fetches, and finishes 22% to 26%
sooner.

### Results: synthetic scale probe, `list_existing_ids` scan (no product caller)

| Measure | Scale 1, union | Scale 1, attributes-only | Scale 12, union | Scale 12, attributes-only |
| --- | --- | --- | --- | --- |
| Requests | 56 | 56 | 527 | 527 |
| Peer fetches | 35 | 35 | 420 | 420 |
| Response bytes | 285,120 | 199,156 | 3,467,119 | 2,418,965 |
| Seconds (median) | 10.3 | 8.1 | 96.8 | 79.2 |

### Convergence of the NetBox import

The `from-netbox` import ran against a new branch of a disposable Infrahub and was reread
with a fresh client.

| Step | Result |
| --- | --- |
| Plan | 1,688 operations: 1,687 creates, one delete that apply does not execute (`IpamNamespace`) |
| Apply | `applied` |
| Repeat plan | One operation, the same unexecuted delete; no creates and no updates |
| Repeat apply | `applied`; nothing written |
| Fresh reread | 1,688 objects through the adapter and 1,688 through the SDK's per-kind count |

The destination has the objects the plan created plus the default `IpamNamespace` that every
Infrahub instance holds, which is why `IpamNamespace` is 7 against 6 created. The reread
counts match the plan for every kind, and each loaded object has a distinct identity:

| Kind | Objects | Relationship identifier | Objects with an empty identifier |
| --- | --- | --- | --- |
| `BuiltinTag` | 26 | none | |
| `LocationSite` | 24 | none | |
| `OrganizationManufacturer`, `OrganizationProvider`, `OrganizationRIR` | 14, 9, 8 | none | |
| `IpamNamespace`, `IpamRouteTarget`, `IpamVRF`, `IpamVLANGroup` | 7, 12, 6, 7 | none | |
| `DcimCircuit`, `DcimPlatform`, `IpamAggregate` | 29, 3, 4 | none | |
| `DcimDeviceType` | 14 | `manufacturer` | 0 |
| `LocationRack` | 42 | `site` | 0 |
| `DcimDevice` | 44 | `location` | 0 |
| `InterfaceLag` | 26 | `device` | 0 |
| `InterfacePhysical` | 1,119 | `device` | 0 |
| `InterfaceVirtual` | 0 | `device` | 0 |
| `IpamVLAN` | 24 | `vlan_group` | 0 |
| `IpamIPAddress` | 180 | `ip_namespace` | 0 |
| `IpamPrefix` | 90 | `ip_namespace` | 0 |

For example, an `InterfaceLag` is read back with the identity
`8:DM-Akron:Comms closet__dm-akron__dmi01-akron-rtr01__Po1`, which carries its device's
identity. The full per-kind report, with a sample identity for each kind, is the `convergence_reread`
section of the script's output.

### Conclusion

- On the NetBox import, the union costs about 15% more bytes and finishes sooner, because it
  avoids 38 peer fetches in dependency order and 83 when consumers load first. Memory is
  unchanged within noise.
- On the synthetic data in dependency order, the union saves no request and costs about 47%
  more bytes and 18% to 50% more time. This is the cost when every peer is already loaded. The
  synthetic dataset has one peer per object and no nested peers of peers, so it is a scale
  probe, not a prediction for a user's data.
- When consumers load before their peers, through an explicit `order`, the union saves
  between 22% and 86% of peer fetches and finishes sooner in every dataset.
- The union saves fetches when a peer is not yet in the SDK store. A peer kind that the
  configuration does not map is not loaded, so it would also need a fetch. This was not
  measured. A source-side filter does not change a destination load, because only the source
  filters records.
- No timeout, server error or complexity limit appeared. The widest query was 8,412
  characters and 178 fields at page size 50, and the largest load was 5,040 objects. Other
  servers, schemas and page sizes were not tried.
- Attributes-only is not a safe alternative. A strict server contract would drop the
  identifiers. An alternative that selects identifier relationships only for peers loaded
  later would depend on load order, which the engine computes. No such alternative is
  justified by these numbers.

**Recommendation:** keep the union. This changes no configuration key and no compatibility
expectation.

### Not measured

- **The 420-object scenario.** No dataset or mapping for it is recorded in this repository.
  The NetBox scenario above (1,688 objects, 20 populated kinds) replaces it, and the synthetic
  scenario matches its object and kind counts only.
- **Why peers are missing from the store in dependency order.** The NetBox data shows 171
  fetches with the union. Which relationships cause them was not traced.
- **Other servers and larger pages.** One server version, one SDK version and page size 50.
- **Cleanup of the synthetic branch.** After the scale-12 population, the server did not
  answer the branch-delete request within 120 seconds (an earlier run was refused by Neo4j's
  transaction memory limit). The study stack was removed with `docker compose down -v`, and
  the NetBox stack with `netbox.down`, which discard the data.
