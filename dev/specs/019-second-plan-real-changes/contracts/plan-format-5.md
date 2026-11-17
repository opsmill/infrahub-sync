# Contract: saved plan format 5

Plan format 5 is plan format 4 with one change: a one-peer reference of an update can hold no
peer. Every other file, field and rule of format 4 is unchanged, including the checksum.

## `manifest.json`

`format_version` is `5`.

## `operations.jsonl`

An update that empties the LAG of an interface:

```json
{"action": "update", "kind": "InterfacePhysical",
 "identity": {"device": "edge-01", "name": "et-0/0/1"},
 "payload": {},
 "relationships": [{"field": "lag", "peer_kind": "InterfaceLag",
                    "cardinality": "one", "peers": []}]}
```

Rules:

| Rule | Refusal |
| --- | --- |
| `peers: []` with `cardinality: "one"` only on an `update` | The plan is refused when read: "a create cannot empty relationship `<field>`". |
| `peers: []` with `cardinality: "one"` not on an identity field | The plan is refused when read. |
| In a format 4 plan, a one-peer reference holds exactly one peer | The plan is refused when read, as today. |

## Readers

| Reader | Format 2 or 3 | Format 4 | Format 5 | Other |
| --- | --- | --- | --- | --- |
| `runs plan`, plan review | Read | Read | Read | Refused, the message names the version and the supported versions |
| `verify`, pre-write checks of `apply` and `runs resume` | Refused at apply, as today | Accepted | Accepted | Refused |
| A release before this one | As today | As today | Refused, the message names version 5 | Refused |

## Apply

An emptied one-peer reference is written in the Upsert of the operation as `<field>: null`. The
write is refused before it is sent if the rendered input holds anything else for that field, or
renders a relationship that the operation does not name.

## Verify

An emptied one-peer reference is a planned peer set with no member. The destination check
compares it with the destination peer set like any other relationship.
