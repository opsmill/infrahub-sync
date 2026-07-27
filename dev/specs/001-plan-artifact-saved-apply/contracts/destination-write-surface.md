# Contract: the destination planned-write surface and apply-time peer resolution

**Requirements**: FR-013, FR-014, FR-016, FR-017, FR-020, FR-023, FR-025. **Scope**: implemented on
the Infrahub destination adapter only, which is the brief's scope and is recorded as an accepted
Principle III tension in [plan.md](../plan.md#complexity-tracking).

## What exists today, verified

| Fact | Location |
|---|---|
| The engine already dispatches a stored plan per row to `destination.apply_cached_row(...)` and guards a missing surface with `NotImplementedError` naming the adapter class | `infrahub_sync/potenda/__init__.py:341-370` |
| `apply_cached_row` has **zero** adapter implementations anywhere in the repository | only `infrahub_sync/potenda/__init__.py`, `tests/cache/test_apply_plan.py:43-44`, `tasks/bench.py:413` |
| The convergent create path is `client.create(...)` then `save(allow_upsert=True)` | `infrahub_sync/adapters/infrahub.py:611-612` |
| `InfrahubModel.update` opens with `client.get(id=self.local_id, ...)`, and `local_id` is populated only by a destination load | `infrahub_sync/adapters/infrahub.py:622`, `:510` |
| The only verified replace-set in the tree is `update_node`'s `compare_lists` remove/add | `infrahub_sync/adapters/infrahub.py:149-175` |
| Peer resolution today reads the **loaded** SDK node store | `infrahub_sync/adapters/infrahub.py:57-94`, populated at `:454`, `:501`, `:613` |
| A zero-match peer is silently dropped with a warning | `infrahub_sync/adapters/infrahub.py:141-143`, `:212-214`, `:229-231` |
| A multi-match surfaces as a bare `IndexError("More than 1 node returned")` | `.venv/…/infrahub_sdk/client.py:566` |

The v1 dispatch is **replaced**, not kept alongside — leaving it wired would be the second apply path
FR-019 forbids, and it has zero implementations to break (PD-010).

## The adapter method

```python
class InfrahubAdapter(DiffSyncMixin, Adapter):
    def apply_planned_operation(
        self, *, operation: PlannedOperation, peers: PeerResolver
    ) -> str:
        """Execute one planned operation convergently. Returns the destination node id."""
```

An adapter that does not define `apply_planned_operation` causes the apply to fail **before any
write** with a clear, actionable error naming the adapter class and directing the operator to `sync` —
the shape the engine already has (FR-023). The check runs inside the same pre-write gate as the five
verification checks, not as a per-operation surprise.

### `delete`

Raises `UnsupportedPlannedOperationError` naming the operation identifier, the action and the kind. It
never touches the destination. The engine **collects** these rather than stopping at the first, because
SC-007 requires every non-delete operation in the same plan to still be applied; the run then ends
`failed` naming them (FR-016, FR-017).

### `create` and `update`

Both route through the same convergent upsert. Neither routes through `InfrahubModel.update`, whose
`local_id` keying needs a destination load FR-012 forbids (AD015).

```text
1. node_schema = client.schema.get(kind=operation.kind)            # must be a NodeSchemaAPI
2. data        = dict(operation.payload)                            # mapped fields only
3. for ref in operation.relationships:
       data[ref.field] = peers.resolve_one(ref) | peers.resolve_many(ref)   # destination node ids
4. create_data = client.schema.generate_payload_create(
       schema=node_schema, data=data,
       source=source_node.id, owner=owner_node.id, is_protected=True)       # parity with :608-610
5. node = client.create(kind=operation.kind, data=create_data)
6. node.save(allow_upsert=True)                                             # the convergence point
7. for ref in operation.relationships where cardinality == "many":
       _replace_relationship_set(node, ref.field, resolved_peer_ids)        # PD-005
8. peers.remember(operation.kind, operation.identity, node.id)
9. return node.id
```

| Clause | Rule | Requirement |
|---|---|---|
| Convergence key | The destination kind's human-friendly ID — the SDK's upsert mutation is keyed on `data["id"]` if set, else `data["hfid"]` (`.venv/…/infrahub_sdk/node/node.py:295-298`) | FR-013, AD017 |
| Payload authority | Authoritative for the mapped fields it carries; **must not** touch unmapped destination fields. "Full" means complete with respect to the configuration's field mapping, not the destination schema | FR-028.4 |
| Cardinality-many | **Replace-set**, enforced explicitly at step 7 rather than assumed of the upsert (PD-005). `peers: []` under `cardinality: "many"` means "empty the set", and the replace-set acts on it | FR-013, FR-028.2 |
| A create whose identity already exists | Converges onto the existing object through the same upsert. Whether that object's payload differs is not examined; conflict policies are out of scope | FR-013, AD025 |
| An update whose target was deleted out-of-band | Materializes as a create, because the upsert creates when no destination object matches the key. No conflict detection, freshness check or refusal path is built | FR-013, AD025 |
| Reporting | Either case is reported under the operation's **original** identifier and **original** action, so SC-005's review-to-apply link is unaffected | AD025 |

#### Why step 7 exists

AD015 cites `update_node` as the evidence that cardinality-many is replace-set — but `update_node` is
on the path AD015 forbids the planned write from using, and whether the *upsert* mutation replaces or
merges a relationship list cannot be determined without a live Infrahub (AD007). Step 7 makes the
semantics true by construction using the only verified implementation in the tree, extracted from
`update_node` into `_replace_relationship_set(node, rel_name, peer_ids)` behavior-preservingly. If the
upsert already replaces, `compare_lists` returns empty `existing_only` and `new_only` sets and step 7
is a no-op.

## `PeerResolver`

```python
class PeerResolver:
    def resolve(self, *, peer_kind: str, identity: dict[str, Any],
                referring_operation_id: str) -> str: ...
    def remember(self, kind: str, identity: dict[str, Any], node_id: str) -> None: ...
```

| Property | Rule | Requirement |
|---|---|---|
| Lifetime | One apply. Created at the start, discarded with it. Never persisted | FR-014 |
| Key | `(kind, canonical_identity(identity))` — the same canonical form the operation identifier hashes | FR-028.3 |
| Population | From each completed create/update (step 8), so an operation's own result resolves later operations that refer to it | FR-014 |
| Miss | Queries the destination (below) and memoizes the **successful** result | FR-014 |
| Negative caching | **Never.** A failed lookup and a failed write are not cached, so a later operation referring to the same peer re-attempts resolution rather than inheriting a negative result | FR-014, AD036 |
| No comparison store | The resolver never reads `client.store` or the DiffSync store — that is exactly the dependency `resolve_peer_node` has today (`infrahub_sync/adapters/infrahub.py:78,81`) and the one a saved-plan apply cannot satisfy | FR-014, DBR-007 |

### The destination query (PD-004)

The query is built from the **destination schema's own** `human_friendly_id` component paths
(`.venv/…/infrahub_sdk/schema/main.py:272`), which the adapter already caches wholesale
(`infrahub_sync/adapters/infrahub.py:345`):

| HFID component path | Value taken from | Filter kwarg |
|---|---|---|
| `<attr>__value` | the operation's identity mapping under `<attr>` | `<attr>__value=<v>` |
| `<rel>__<attr>__value` | the identity mapping recorded on the operation's relationship reference for `<rel>` | `<rel>__<attr>__value=<v>` |

A **schema path** is split; a **data value** never is. That distinction is the whole point: recovering
identifiers by splitting a DiffSync unique-id on `__` is the v1 flaw the brief names.

This matters on the qualified configuration, where seven kinds carry a relationship inside their
identity — `LocationRack.site`, `DcimDeviceType.manufacturer`, `DcimDevice.location`,
`Interface{Physical,Virtual,Lag}.device`, `IpamVLAN.vlan_group`, `IpamPrefix.vrf`,
`IpamIPAddress.vrf` (enumerated in [research.md](../research.md) PD-004).

```python
results = client.filters(kind=peer_kind, **filter_kwargs)
```

| Result count | Behavior | Message names | Requirement |
|---|---|---|---|
| exactly 1 | return `results[0].id`, memoize | — | FR-014 |
| **0** | raise `PeerNotFoundError`; refuse the operation and fail the run | peer kind, peer identity, **and the referring operation identifier** | FR-014, SC-016 |
| **> 1** | raise `PeerAmbiguousError`; refuse the operation and fail the run | peer kind, peer identity, **and the match count** | FR-014, SC-016 |

Neither is ever a silent skip. "A silent skip would make the applied set differ from the reviewed set"
is the brief's own reasoning for FR-017 and it governs a dropped relationship exactly as it governs a
dropped operation (AD016). This replaces the current warn-and-continue (V14) and the SDK's bare
`IndexError` (V17).

**Fallback for a kind whose HFID does not cover its plan identity**: resolve the reference component's
own peer first (recursively, through the same resolver) to a destination id, then filter
`<rel>__ids=[<id>]`. FR-024 already warns at plan time when the HFID is absent or incomplete, so this
path is the degraded case that warning is about, not a silent alternative.

**Not verifiable offline**: the exact GraphQL filter spelling for a nested `<rel>__<attr>__value`
argument. AD007 records that no live Infrahub is reachable here. It is asserted by the
`integration`-marked SC-008 and SC-016 tests, and the failure mode if the spelling is wrong is a zero
match — a loud refusal, never a silent drop.

### Tier ordering and its limits

Dependency-tier ordering guarantees a peer is written before anything referring to it, **for
references carried in the computed dependency graph**. Three cases the existing tier machinery cannot
express (`infrahub_sync/dependency_graph.py:33-34`, `:81-100`; `infrahub_sync/__init__.py:132-133`):

- a self-reference, excluded from write-order edges;
- a reference reachable only through an optional edge dropped to break a cycle;
- any reference in a configuration that supplies an explicit `order:`, which yields no tiers at all.

In those cases the peer may be unresolved at apply, and the zero-match arm above governs. The
qualification is safe precisely because the miss is loud (AD022).

## The apply loop

In `Potenda.apply_plan`, replacing the v1 body:

```text
1. load the artifact; classify v1 / torn / version (contracts/plan-reader-api.md)
2. run the five verification checks + the write-surface check   → refuse before any write
3. peers = PeerResolver(adapter)
4. applied: list[str] = []          # ORDERED — FR-020
   unsupported: list[PlannedOperation] = []
5. for operation in stored order:          # tier, then operation_id
       if operation.action == "delete":
           unsupported.append(operation); continue          # FR-016, FR-017
       try:
           adapter.apply_planned_operation(operation=operation, peers=peers)
       except (PeerNotFoundError, PeerAmbiguousError, DestinationRejected) as exc:
           record run failed naming operation.operation_id and exc; STOP     # AD027
       applied.append(operation.operation_id)
6. record `applied` on the run result, in order            # FR-020
7. if unsupported: run state failed, message names each identifier and action   # SC-007
   else:           run state applied
```

| Rule | Requirement |
|---|---|
| Stored order is executed exactly — no re-sorting, no recomputation, no extraction of either side | FR-012, SC-001 |
| `applied` is an **ordered** sequence; FR-025's "last operation reported as applied" is its final element, not a separate field | FR-020, AD036 |
| A destination rejection or transport failure stops at that operation; what was written stays written; no rollback | AD027, FR-025 |
| The partial-apply record is best-effort and explicitly **not** required to survive abnormal process termination | FR-025, AD011 |
| An empty plan applies as a successful no-op — but verification still runs first | FR-022, AD033 |
| A delete is never executed, and not because of a configuration setting: deletes never enter the comparison result the write path consumes, so the divergence is structural | FR-016, AD004 |

## Test split

| Evidence | Where | Marker |
|---|---|---|
| Payload construction, upsert invocation, replace-set reconciliation, memo population, negative-caching refusal, both peer refusals, delete → unsupported, missing surface, ordered applied set, fail-fast on rejection | `tests/adapters/test_infrahub_planned_write.py`, mocked `InfrahubClientSync` | local |
| SC-001 (no diff/sync call), SC-002 (converge on re-apply), SC-003 (per-class matrix, both crash windows), SC-007 (live counts before/after), SC-008 (peer sets read back), SC-016 (real ambiguity) | `tests/integration/test_saved_plan_apply_integration.py` | **`integration`** (`pyproject.toml:133-135`) |

</content>