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
fresh SDK store for each load. A wrapper around the SDK's `_post` records each GraphQL request:
its query text, response bytes and latency. A bounded peer fetch is a request whose query
filters by `ids`. The `list_existing_ids` scan is measured separately, because no product entry
point calls it.

### Setup

| Item | Value |
| --- | --- |
| Infrahub server | 1.10.6, from `development/docker-compose.infrahub.yml` |
| Infrahub SDK | 1.23.2 |
| Page size | 50 (the SDK default) |
| Machine | One host; Infrahub, Neo4j and the client share it |
| Branch | One branch created for the run; `main` is never written |
| Cache conditions | One untimed warm-up load; then timed loads alternate between variants; the SDK store starts empty each time |

The dataset has 18 kinds. It is synthetic and is not the NetBox-derived scenario.

| Identity shape | Kinds |
| --- | --- |
| Scalar only (`name`) | `Site`, `Vendor`, `Platform`, `Role`, `Vlan`, `Tag`, `Prefix` |
| Relationship plus name, such as `InterfaceLag(device, name)` | `Device`, `Rack`, `Pdu`, `Circuit`, `Interface`, `InterfaceLag`, `Module`, `Psu`, `Fan`, `Port` |
| Relationship to a generic, `Mount(host, name)` | `Mount`, whose peers are `Rack` nodes through the `Hosting` generic |

Scale 1 creates 420 objects. Scale 12 creates 5,040. Each relationship-valued object points at
one peer, spread evenly over the peer kind. The script
[`development/identifier_prefetch_study.py`](https://github.com/opsmill/infrahub-sync/blob/feature/v3-develop/development/identifier_prefetch_study.py)
builds the schema and data and runs every measurement below.

```bash
INFRAHUB_ADDRESS=http://127.0.0.1:8000 INFRAHUB_API_TOKEN=<token> \
  uv run python development/identifier_prefetch_study.py --scale 1 --repeats 5 --output out.json
```

### What the query looks like

For `BenchMount`, the union adds the generic peer's attributes and a nested `site` selection
to the query. Attributes-only selects the peer's `id`, `hfid`, `display_label` and
`__typename` only. The longest query grows from 726 to 1,291 characters.

### Results: bulk load in dependency order

Peers load before the kinds that reference them. This is the order the engine uses, so it is
the case a user run reaches. Medians of five loads at scale 1 and three at scale 12.

| Measure | Scale 1, union | Scale 1, attributes-only | Scale 12, union | Scale 12, attributes-only |
| --- | --- | --- | --- | --- |
| Objects | 420 | 420 | 5,040 | 5,040 |
| Requests | 21 | 21 | 107 | 107 |
| Peer fetches | 0 | 0 | 0 | 0 |
| Response bytes | 269,145 | 183,181 | 3,273,099 | 2,224,945 |
| Load seconds | 5.5 | 4.3 | 60.3 | 41.3 |
| Peak Python memory, MiB | 8.4 | 7.1 | 57.9 | 39.7 |
| Server errors | 0 | 0 | 0 | 0 |

The union sends about 47% more bytes and takes 28% to 46% longer. It saves no request,
because the peers are already in the SDK store when a consumer loads. Python memory is
measured with `tracemalloc` on a separate load, so it is an estimate.

### Results: consumers load before their peers

This is the worst case for the bounded peer fetch. It reverses the load order. It is not a
product order; it bounds how many fetches the union can save.

| Measure | Scale 1, union | Scale 1, attributes-only | Scale 12, union | Scale 12, attributes-only |
| --- | --- | --- | --- | --- |
| Requests | 26 | 56 | 167 | 527 |
| Peer fetches | 5 | 35 | 60 | 420 |
| Response bytes | 270,600 | 199,156 | 3,290,759 | 2,418,965 |
| Load seconds | 5.6 | 7.4 | 63.1 | 82.3 |

Here the union wins. It sends 30% to 36% more bytes but 80% to 87% fewer peer fetches, and
finishes sooner.

### Results: `list_existing_ids` scan (no product caller)

The scan does not populate the SDK store, so both variants fetch each peer once.

| Measure | Scale 1, union | Scale 1, attributes-only | Scale 12, union | Scale 12, attributes-only |
| --- | --- | --- | --- | --- |
| Requests | 56 | 56 | 527 | 527 |
| Peer fetches | 35 | 35 | 420 | 420 |
| Response bytes | 285,120 | 199,156 | 3,467,119 | 2,418,965 |
| Seconds | 8.7 | 6.7 | 96.8 | 75.6 |

Prefetching identifiers did not save a fetch here. Do not read these numbers as a cost of any
user run.

### Conclusion

- On the path a user run takes, the union costs more bytes, time and memory and saves no
  request. The extra cost is about 47% in bytes at both scales.
- The union saves fetches only when a peer loads after its consumer. A peer that the mapping
  excludes, or one that a filter removes on the source side, falls in that category.
- No timeout or server complexity limit was reached at 5,040 objects, so the wider query is
  not a correctness risk at this scale.
- Attributes-only is not a safe alternative. A strict server contract would drop the
  identifiers. An alternative that selects identifier relationships only for peers loaded
  later would depend on load order, which the engine computes. No such alternative is
  justified by these numbers.

**Recommendation:** keep the union. This changes no configuration key and no compatibility
expectation.

### Not measured

- **Convergence.** Plan, apply, repeat apply and fresh reread of the 420-object scenario were
  not run, so no destination counts or retained relationship identities are recorded.
- **The NetBox-derived scenario.** The dataset above matches its size and kind count, not its
  data. A generic or nested relationship identity is represented by `Mount` and by the nested
  `site` selection only.
- **Other servers and larger pages.** One server version, one SDK version and page size 50.
- **Cleanup of the study branch.** After population, Neo4j refused to delete the run's branch
  (its transaction memory limit, 2.7 GiB, was reached). The study stack was removed with
  `docker compose down -v`, which discards the data.
