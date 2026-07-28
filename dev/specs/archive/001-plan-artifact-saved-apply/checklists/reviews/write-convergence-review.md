# Review: Write Surface and Convergence Requirements Checklist

**Checklist**: `../write-convergence.md` (CHK001–CHK042)
**Spec under evaluation**: `../../spec.md` (567 lines)
**Brief (sole scope authority)**: `db-001-plan-artifact-saved-apply.md`, brief_version 5, batch-v3
**Reviewed**: 2026-07-26 — read-only requirements evaluation. No spec or checklist file was modified.

Every claim about existing repository behavior below was verified by reading the code and is
anchored `file:line`. Verdict counts: **10 SATISFIED / 10 DEFECT(BLOCKING) / 19 DEFECT(RECOMMENDED)
/ 3 DEFECT(NIT) / 0 NOT-APPLICABLE**. Five items carry a `PRODUCT-AMBIGUITY` label.

## Verdict table

| Item | Verdict | Severity | One-line reason |
|---|---|---|---|
| CHK001 | SATISFIED | — | FR-023 + the qualified-path assumption bound the obligation to Infrahub; other adapters fail loudly. |
| CHK002 | DEFECT | RECOMMENDED | Nothing says whether a planned update is authoritative or additive over destination attributes. `PRODUCT-AMBIGUITY` |
| CHK003 | DEFECT | BLOCKING | Cardinality-many write semantics (replace-set vs add-to-set) undefined, so SC-008's comparison is undecidable. `PRODUCT-AMBIGUITY` |
| CHK004 | DEFECT | BLOCKING | FR-014's destination-query fallback defines no behavior for a zero-match; today's code silently drops the relationship. `PRODUCT-AMBIGUITY` |
| CHK005 | DEFECT | BLOCKING | FR-014's unconditional tier guarantee is falsified by the existing tier machinery (self-edges, dropped edges, explicit `order`). |
| CHK006 | DEFECT | RECOMMENDED | FR-024 states no run outcome and no recording location for the warning. `PRODUCT-AMBIGUITY` |
| CHK007 | DEFECT | RECOMMENDED | The warning's output stream is unspecified and collides with FR-008/SC-010's scanned stdout. |
| CHK008 | SATISFIED | — | FR-020 + FR-002's per-operation action + AD002's derivation make SC-005 and SC-003 constructible. |
| CHK009 | DEFECT | RECOMMENDED | The "affects anything that renders it" claim contradicts AD004, under which the existing diff renderer does not change. |
| CHK010 | SATISFIED | — | FR-013 and SC-002 define convergence by observable postcondition, not mechanism. |
| CHK011 | DEFECT | BLOCKING | "No duplicate"/"same identity" is never tied to a notion of identity; the actual convergence key is Infrahub's HFID, not the plan's identity. |
| CHK012 | DEFECT | RECOMMENDED | "With no loaded comparison store" is internal state with no stated external check. |
| CHK013 | DEFECT | RECOMMENDED | Cache lifetime on failure, and whether a negative resolution is memoized, are unstated. |
| CHK014 | SATISFIED | — | "An operation's own result" denotes the destination object written, which exists for both create and update. |
| CHK015 | DEFECT | BLOCKING | "Relationship change" is an action value in Key Entities and a field of every operation in FR-002; the action vocabulary is not closed. |
| CHK016 | SATISFIED | — | AD004 states deletes are structurally incapable of reaching the destination, not configuration-suppressed. |
| CHK017 | DEFECT | RECOMMENDED | FR-015 presumes a complete destination load; the incremental hydrate path can fabricate or miss deletes. |
| CHK018 | DEFECT | BLOCKING | SC-003's create/update/relationship "write classes" are not expressible in FR-002's operation model, so DBA-003's matrix is unbuildable. |
| CHK019 | DEFECT | RECOMMENDED | Silence on whether `sync` mode — which plans and writes in one run — inherits FR-017's fail-on-delete obligation. |
| CHK020 | DEFECT | RECOMMENDED | SC-003 measures relationship-write convergence that no functional requirement obliges. |
| CHK021 | SATISFIED | — | Out of Scope excludes building batching; FR-026 only forbids precluding it. Consistent. |
| CHK022 | DEFECT | RECOMMENDED | "Clean-single-run counts" is used undefined, with no stated baseline. |
| CHK023 | DEFECT | BLOCKING | "Before it is recorded" names a per-operation record the spec excludes; FR-025 and the ledger exclusion cannot both hold across a crash. |
| CHK024 | DEFECT | RECOMMENDED | SC-008 names neither the relationships compared nor the ordering rule for peer lists. |
| CHK025 | DEFECT | RECOMMENDED | FR-013 is verified only transitively through the end-to-end SC-002; no adapter-surface criterion. |
| CHK026 | DEFECT | BLOCKING | FR-024 has no success criterion, no traceability row, and names an observable nothing in the tree can read. |
| CHK027 | DEFECT | RECOMMENDED | SC-007's before/after counts are not scoped to any kind set, and the surviving object is not asserted directly. |
| CHK028 | DEFECT | RECOMMENDED | SC-002's "same object identities" is not scoped to an enumerable kind set. |
| CHK029 | SATISFIED | — | FR-014 + AD003 are total for this case: deletes never populate the cache and the peer resolves from the destination. |
| CHK030 | SATISFIED | — | FR-017 + FR-016 + FR-022 fully determine a delete-only plan: no writes, and — **restated per AD055**, which superseded this row's original "failed run" — an **applied** run recording the skipped count and identifiers. |
| CHK031 | DEFECT | RECOMMENDED | The crash hazard specific to relationships (object created, peers unlinked) has no stated expectation. |
| CHK032 | DEFECT | BLOCKING | A peer identity matching more than one destination object has no stated behavior; today it raises a bare `IndexError`. `PRODUCT-AMBIGUITY` |
| CHK033 | DEFECT | RECOMMENDED | A planned update whose target vanished is unspecified; the only viable path silently turns it into a create. |
| CHK034 | DEFECT | RECOMMENDED | A planned create against a drifted destination object is unspecified. `PRODUCT-AMBIGUITY` |
| CHK035 | DEFECT | NIT | An unknown peer kind has no stated behavior; largely covered once CHK004's rule exists. |
| CHK036 | DEFECT | BLOCKING | The "existing identifier-keyed converging write path" assumption is factually wrong for updates and carries neither evidence nor an impact clause. |
| CHK037 | DEFECT | RECOMMENDED | The dependency on an existing, partial tier computation is presumed by FR-002/FR-014, never recorded. |
| CHK038 | SATISFIED | — | Out of Scope plus FR-014's "at apply time" plus FR-016 draw the boundary in requirement terms. |
| CHK039 | DEFECT | RECOMMENDED | The unique-constraint assumption's scope ("on the qualified path") does not say which kinds. |
| CHK040 | DEFECT | NIT | Per-FR `per AD00N` annotations make the dependency traceable, but no impact-if-not-ratified statement exists. |
| CHK041 | DEFECT | NIT | The relationship-bearing-kind precondition for SC-008 is true of the named config but unrecorded. |
| CHK042 | SATISFIED | — | Open Design Decisions states exactly what is fixed (requirement, warning content) and what is open (mechanism). |

## Reference: what the Infrahub write path can do today

Anchors used repeatedly below.

- **Create** — `InfrahubModel.create` (`infrahub_sync/adapters/infrahub.py:588-615`) resolves relationship
  peers from the client store via `diffsync_to_infrahub` (`infrahub.py:180-234`), builds a payload with
  `schema.generate_payload_create`, then `client.create(...)` + `node.save(allow_upsert=True)`
  (`infrahub.py:611-612`).
- `save(allow_upsert=True)` routes to `create(allow_upsert=True)`
  (`.venv/lib/python3.12/site-packages/infrahub_sdk/node/node.py:1528-1536`), which issues a
  `<Kind>Upsert` mutation with `exclude_hfid=False` (`node.py:1838-1846`). The mutation key is
  `data["id"]` if the node has an id, else `data["hfid"]` (`node.py:295-298`). `hfid` is computed from
  the **destination schema's `human_friendly_id`** and is `None` when the schema declares none or any
  component is missing (`node.py:127-138`). **So an identifier-keyed convergent upsert genuinely
  exists — but it is keyed on the destination kind's HFID, not on the plan's destination identity, and
  it degrades to an unkeyed create when the HFID is absent or incomplete.**
- **Update** — `InfrahubModel.update` (`infrahub.py:617-628`) begins
  `adapter.client.get(id=self.local_id, kind=...)`. `local_id` is populated **only** from a loaded
  destination node (`infrahub.py:510`: `data = {"local_id": str(node.id)}`; declared at
  `infrahub_sync/__init__.py:232`). **The existing update path therefore cannot work without a
  destination load** — and the plan deliberately carries no destination-assigned identifier
  (spec.md:270-271, AD003 at spec.md:58-60).
- `update_node` (`infrahub.py:97-177`) sets only the attributes present in `attrs`
  (`infrahub.py:116-124`) — partial/merge for attributes — and for cardinality-many computes
  `compare_lists(existing_peer_ids, new_peer_ids)` then removes `existing_only` and adds `new_only`
  (`infrahub.py:166-175`) — **replace-the-set** for relationships.
- Peer resolution reads the client store and returns `None` on a miss (`infrahub.py:57-94`); every
  caller logs a warning and `continue`s, silently dropping the relationship (`infrahub.py:141-143`,
  `infrahub.py:212-214`, `infrahub.py:229-231`). A destination query that matches more than one node
  raises `IndexError("More than 1 node returned")`
  (`.venv/.../infrahub_sdk/client.py:565-566`).
- **The planned-write surface has zero implementations.** `apply_cached_row` appears only in
  `infrahub_sync/potenda/__init__.py:344-370`, `tests/cache/test_apply_plan.py:43-44`, and
  `tasks/bench.py:413`. `Potenda.apply_plan` guards its absence with `NotImplementedError`
  (`potenda/__init__.py:354-360`) and dispatches lossy rows whose `dest_id` and `attribute` are
  empty strings (`potenda/__init__.py:317-330`).
- **Nothing in the repository reads a uniqueness constraint.** No occurrence of
  `uniqueness_constraints` or `human_friendly_id` exists in `infrahub_sync/`; `hfid=` appears only as
  a `client.get` filter for `CoreAccountGroup` (`infrahub.py:326`, `infrahub.py:337`). The data is
  reachable — `NodeSchemaAPI.human_friendly_id` / `.uniqueness_constraints` exist
  (`.venv/.../infrahub_sdk/schema/main.py:272,274`) and the adapter already holds the full destination
  schema (`infrahub.py:345`) — so FR-024 detection is feasible but entirely unbuilt.
- **Tiers** are kind-level, derived from `SchemaMappingField.reference`; self-edges are excluded
  (`infrahub_sync/dependency_graph.py:25-36` and the module docstring at lines 3-5); non-identifier
  ("optional") edges are dropped to break cycles (`dependency_graph.py:39-53`, `64-102`); and tiers
  are `None` whenever a configuration declares an explicit `order:`
  (`infrahub_sync/__init__.py:132-133`).
- **The run result is not per-operation.** `apply_cmd` writes `run.json` once before
  `apply_plan()` and once after (`infrahub_sync/cli.py:322-323`, `345-351`); `RunFile` carries
  `status/mode/summary/finished_at` and saves atomically in one shot
  (`infrahub_sync/cache/sidecars.py:69-90`).

## Detailed defect blocks

### CHK002 — update payload semantics undefined (RECOMMENDED, `PRODUCT-AMBIGUITY`)

**Anchor**: spec.md:267-272 (FR-002, "the required source values as a full payload"); spec.md:315-317
(FR-013).

**Evidence**: FR-002 requires a *full* payload; FR-013 constrains only duplication ("repeating an
operation does not create a second object"). No requirement, edge case, or clarification states what
happens to destination attributes that exist at the destination but are absent from the plan payload.
Both existing write paths happen to be additive — `update_node` touches only keys present in `attrs`
(`infrahub.py:116-124`), and the upsert payload built by `generate_payload_create` carries only mapped
fields (`infrahub.py:602-612`) — but nothing in the spec inherits that. No SC reads attribute values
back after an apply, so the gap is invisible to the current criteria while being user-visible in the
destination.

**Minimum fix**: add a clause to FR-013 stating whether a planned update is authoritative (destination
attributes not present in the payload are cleared) or additive (they are left untouched). Do not
decide it in review — it is a product decision with data-loss consequences under the authoritative
reading. **Brief-level gap**: the brief's *In scope* line ("A destination write surface … capable of
executing a planned create or update convergently") and its *Edge cases and failure behavior* section
should have carried the answer; neither mentions payload semantics.

### CHK003 — cardinality-many relationship write semantics undefined (BLOCKING, `PRODUCT-AMBIGUITY`)

**Anchor**: spec.md:318-323 (FR-014); spec.md:419-422 (SC-008); AD003 at spec.md:60-62.

**Evidence**: AD003 fixes the plan-side *shape* ("a cardinality-many reference is a list ordered
canonically by the peer identity") and FR-014 fixes *resolution*, but no requirement says what the
write does with the resolved list: set the destination's peer set to exactly that list, or add to it.
SC-008 then asserts the destination relationships "match those the plan specified" — a predicate that
is undecidable for a destination holding the plan's peers *plus* an extra one. This makes DBA-008
untestable as worded. The existing engine answers it (`update_node` removes `existing_only` and adds
`new_only`, `infrahub.py:166-175` — replace-set), but the planned-write surface is new and inherits
nothing automatically.

**Minimum fix**: state the semantics in FR-014 — naming replace-set costs nothing because it is the
existing engine's behavior at `infrahub.py:166-175` — and restate SC-008 to compare unordered sets of
(peer kind, peer identity) pairs. If the intent is additive, that is a product decision and must be
recorded as one. **Brief-level gap**: the brief's *In scope* bullet "Apply-time relationship peer
resolution without a loaded comparison store" covers resolution only; the *Edge cases* section should
have carried the write semantics that DBA-008 measures.

### CHK004 — peer resolution defines no zero-match behavior (BLOCKING, `PRODUCT-AMBIGUITY`)

**Anchor**: spec.md:318-323 (FR-014, "MUST fall back to querying the destination for that identity on
a miss").

**Evidence**: FR-014 stops at the fallback query. Nothing states what happens when the query returns
nothing — fail the operation, fail the run, or write the object without that relationship. The
question is not academic: on the qualified path, references such as `DcimDevice.primary_address →
IpamIPAddress` are non-identifier ("optional") edges (`examples/netbox_to_infrahub/config.yml`
device block), so a peer can legitimately be absent from both the plan and the destination. Today's
behavior is a silent skip with a log warning (`infrahub.py:141-143`, `212-214`, `229-231`), which
(a) violates the project constitution's "never crash opaquely or silently skip records"
(`dev/constitution.md`, Principle II) and (b) makes SC-008 flaky rather than failing, because the
relationship simply never lands. FR-017 does not cover it: a resolution failure is not an
"unsupported operation".

**Minimum fix**: add to FR-014 the required behavior on a zero-match — which of {fail the operation
and fail the run, fail the run before any further write, apply the object and report the unresolved
reference} — and forbid a silent skip explicitly, as FR-017 does for unsupported operations.
**Brief-level gap**: the brief's *Edge cases and failure behavior* section names the plan-time
non-unique-identifier case but has no apply-time peer-resolution case at all.

### CHK005 — FR-014's tier guarantee is falsified by the tier machinery it relies on (BLOCKING)

**Anchor**: spec.md:322 ("Dependency-tier ordering MUST guarantee a peer is written before anything
referring to it"); spec.md:267-272 (FR-002's per-operation dependency tier).

**Evidence**: the guarantee is unconditional, and the mechanism cannot deliver it in three concrete
ways. (1) Self-references are deliberately excluded from the graph —
`build_dependency_graph` skips `field.reference == sm.name` (`dependency_graph.py:33-35`) and the
module docstring states self-references "are not write-order edges and are excluded"
(`dependency_graph.py:3-5`); a hierarchical kind (e.g. `LocationGeneric.parent`) therefore has
intra-kind ordering that kind-level tiers cannot express. (2) Cycles are broken by *dropping*
non-identifier edges (`dependency_graph.py:90-100`), and the dropped edges are logged as a warning
(`infrahub_sync/__init__.py:141-144`) — a peer reachable only through a dropped edge is not ordered
before its referrer. (3) When a configuration declares an explicit `order:`, `compute_order_and_tiers`
returns `tiers = None` (`infrahub_sync/__init__.py:132-133`) and `Potenda` receives no tiers at all
(`potenda/__init__.py:33`, `46-48`) — so FR-002's per-operation tier has nothing to derive from. The
qualified config happens to avoid (1) and (2) (verified: 6 tiers, zero dropped edges, no
self-references) and omits `order:`, so the guarantee holds there by luck, not by construction.

**Minimum fix**: qualify FR-014 — tier ordering guarantees peer-before-referrer only for references
carried in the computed dependency graph — and state the required behavior for the three exceptions
(intra-kind self-reference ordering, edges dropped to break a cycle, configurations with an explicit
`order`), which is where FR-014's destination-query fallback must carry the load.

### CHK006 — the FR-024 warning's consequence is unstated (RECOMMENDED, `PRODUCT-AMBIGUITY`)

**Anchor**: spec.md:354-355 (FR-024); spec.md:248-252 (Edge case).

**Evidence**: FR-024 says only "the plan run MUST warn at plan time, naming the affected kind and
identifier". Neither FR-024 nor the edge case says whether the plan run still succeeds, nor whether
the warning is recorded in the artifact or merely emitted. Recording it in the manifest would draw it
into `plan_checksum` and SC-006's byte-identity requirement (spec.md:279-283, 411-414); leaving it out
makes it invisible to a reviewer reading the stored artifact later, which is the whole point of
DBR-012.

**Minimum fix**: state in FR-024 that the plan run still succeeds (or fails), and whether the warning
is a manifest field or emitted output only. If it becomes a manifest field, say whether it is inside
or outside the checksum.

### CHK007 — the FR-024 warning's output stream is unspecified (RECOMMENDED)

**Anchor**: spec.md:354-355 (FR-024); spec.md:292-297 (FR-008); spec.md:428-430 (SC-010).

**Evidence**: FR-008 requires review output on standard output "so it can be captured and scanned",
and SC-010 scans "summary output and per-object output" for canary credentials. FR-024's warning names
a kind and an identifier attribute — plausibly emitted to the same stream. Nothing states which
stream, so an implementer can either bury it in the log (unobservable in captured output, and the
project logs via `logger`, `potenda/__init__.py:12`) or emit it on stdout where it becomes part of
the scanned surface.

**Minimum fix**: one clause in FR-024 naming the stream, aligned with FR-008's stdout/log split.

### CHK009 — the "affects anything that renders it" claim contradicts AD004 (RECOMMENDED)

**Anchor**: spec.md:256-258 (Edge case, Recorded deletes change existing output); spec.md:324-330
(FR-015); AD004 at spec.md:67-75.

**Evidence**: the edge case says recording deletes "makes previously hidden operations appear in the
plan **and in anything that renders it**". AD004 says deletes "are never placed into the comparison
result that the write path consumes". The only existing renderer of change content is
`logger.info("\n%s", mydiff.str())` (`cli.py:153`), which renders the diffsync `Diff` — precisely the
object AD004 keeps deletes out of. So under AD004, existing rendered output does **not** change, and
the fixture/documentation obligation in FR-015 (spec.md:329-330) is scoped to a set that may be
empty. The wording is inherited from the brief, but AD004 is the spec's own decision and creates the
tension.

**Minimum fix**: narrow the edge case and FR-015 to "anything that renders the plan artifact",
and note that the diff console rendering is unchanged because deletes never enter the comparison
result.

### CHK011 — "no duplicate" has no stated notion of identity (BLOCKING)

**Anchor**: spec.md:393-395 (SC-002, "the same object, the same identity, no duplicate");
spec.md:471-473 (Assumptions, "Destination identifier attributes are unique-constrained").

**Evidence**: SC-002 measures "identities" without saying whose. Two candidate notions exist and the
code shows they are genuinely different. The plan's *destination identity* (FR-002, spec.md:268-269)
would be the diffsync identifiers configured per kind (`schema_mapping[].identifiers`, e.g.
`DcimDevice: ["location", "name"]`). The *actual convergence key* is Infrahub's HFID: `save(allow_upsert=True)`
issues `<Kind>Upsert` with `data["hfid"]` (`.venv/.../infrahub_sdk/node/node.py:1838-1846`,
`295-298`), computed from the destination schema's `human_friendly_id` and returning `None` when the
schema declares none or a component is missing (`node.py:127-138`). If a kind's HFID does not
correspond to the configured identifiers, an upsert can converge onto a *different* object than the
plan's identity names, or not converge at all — and SC-002 would be measuring the wrong thing. The
Assumptions line names "unique-constrained identifier attributes", which is neither the HFID nor the
plan identity, so the precondition as stated does not entail convergence.

**Minimum fix**: define "the same identity" in SC-002 as the plan's destination-identity values, and
restate the assumption as the required correspondence: for every kind in the plan, the destination
kind's convergence key (Infrahub human-friendly ID, or a uniqueness constraint) must cover the plan's
destination identity. That restated precondition is also the observable FR-024 should check (see
CHK026). **Brief-level gap**: the brief's *Assumptions* row and its *Dependencies* row ("The adapter's
identifier-keyed upsert converges on repeat") both assert the precondition in terms of "identifier
attributes" without naming the key the upsert actually uses.

### CHK012 — "with no loaded comparison store" is not externally checkable (RECOMMENDED)

**Anchor**: spec.md:318-319 (FR-014); spec.md:419-420 (SC-008).

**Evidence**: SC-008's condition is an internal process state. SC-001 (spec.md:390-392) shows the
spec knows how to express such a thing checkably — "a trace or inspection showing no comparison-engine
diff/sync call on the apply path" — but SC-008 does not reuse it. The distinction matters because
`apply_cmd` constructs both adapters through `get_potenda_from_instance` (`cli.py:313-318`), and the
Infrahub adapter's `__init__` fetches the whole destination schema and a `CoreAccount`
(`infrahub.py:311-345`) — so "no loaded comparison store" is not the same as "no destination network
access", and an ambiguous reading could be satisfied vacuously.

**Minimum fix**: express SC-008's precondition as SC-001 does — no source or destination extraction
call on the apply path and an empty destination adapter store — rather than as an internal state.

### CHK013 — memoization lifetime and negative caching unstated (RECOMMENDED)

**Anchor**: spec.md:319-321 (FR-014, "Resolution MUST be memoized within one apply"); AD003 at
spec.md:62-65.

**Evidence**: AD003 says the cache is "populated as each planned create or update **completes**",
which implies success only but does not say so, and then says the fallback query's result is
memoized — including, on the plain reading, a negative result. A cached negative would make one
transient miss poison every later reference to that peer within the apply, and a cached resolution
from a write that then failed would hand later operations a peer id for an object that was not
written. Neither case is addressed, and both interact with FR-025's partial-apply behavior
(spec.md:356-357).

**Minimum fix**: state that only successful writes and successful destination lookups populate the
cache, and that a failed resolution is not memoized.

### CHK015 / CHK018 — the action vocabulary is not closed; SC-003's "write classes" are not expressible (both BLOCKING, one fix)

**Anchor**: spec.md:264-265 (FR-001); spec.md:267-272 (FR-002); spec.md:371-373 (Key Entities,
Planned operation); spec.md:396-400 (SC-003); spec.md:212-229 (User Story 5).

**Evidence**: the spec carries two incompatible models of a relationship change.
Key Entities makes it an **action value**: "the action (create, update, delete, or relationship
change)" (spec.md:372). FR-002 makes relationship references a **field of every operation**: per
operation "the action, the destination kind, destination identity, … relationship references"
(spec.md:268-269). FR-014 treats peers as resolved *for* operations, never as operations
(spec.md:318-323). SC-003 then enumerates "the create, update, and relationship write classes"
(spec.md:396) — a third framing. The choice is load-bearing in three places: FR-006's "counts by
action" summary (spec.md:288-289), FR-021's uniqueness assertion (spec.md:346-348) combined with
AD002's identifier over `(action, kind, identity)` (spec.md:50-51) — under the action reading, one
object can carry both an `update` and a `relationship` operation with distinct identifiers, under the
field reading it cannot — and DBA-003's per-class conformance matrix, which cannot be built without
knowing whether "relationship" is a class of operation or a facet of one.

**Minimum fix**: one edit resolves both items. Fix a single closed action vocabulary in FR-002 and
Key Entities, state how a relationship-only change is represented (a distinct `relationship` action,
or an `update` whose payload carries relationship references), and restate SC-003's "write classes"
in exactly those terms.

### CHK017 — FR-015 presumes a complete destination load (RECOMMENDED)

**Anchor**: spec.md:324-330 (FR-015, "derived from the destination-only identities in the loaded
destination state"); AD004 at spec.md:67-70.

**Evidence**: the derivation is a set difference that requires a complete destination enumeration.
The engine has a warm path that does not provide one: `load_one_side` hydrates a side from the
previous run's Parquet snapshot and then applies only changed-since rows
(`potenda/__init__.py:202-228`), and the adapter documents that "timestamp-filtered queries miss
DELETEs" (`infrahub.py:381-383`). On a warm run, an object deleted at the destination since the last
run persists in the hydrated state and would be emitted as a *phantom* delete operation — which,
under FR-017 and SC-007, forces every apply of that plan into a failed state. The CLI currently
defaults to full extraction (`cli.py:103-110`), so the qualified path is safe, but the spec is silent
on the precondition.

**Minimum fix**: add a clause to FR-015 stating that delete derivation requires a complete
destination extraction, and what the plan run does when the destination side was loaded incrementally
(omit deletes and record why, or refuse).

### CHK019 — silence on `sync` mode inheriting the fail-on-delete obligation (RECOMMENDED)

**Anchor**: spec.md:324-333 (FR-015, FR-016); spec.md:334-336 (FR-017, "reported **at apply time**").

**Evidence**: `sync` plans and writes in one run — `sync_in_tiers` computes the diff, calls
`write_plan(diff)`, then `sync(diff=diff)` (`potenda/__init__.py:461-468`; also `cli.py:271`). Under
FR-015 the plan that run writes now contains deletes, while the same run's write path must not see
them. FR-017's obligation is scoped "at apply time", so a `sync` run's recorded delete is *not*
executed and the run still succeeds — a silent divergence between the recorded plan and what was
written, which is exactly the outcome FR-017 exists to prevent. Whether that is intended is unstated.

**Minimum fix**: state that FR-015's plan-content change applies to plans written by any mode, and
that FR-017's fail-the-run obligation binds the saved-plan apply path only, leaving `sync` behavior
unchanged — or state the alternative explicitly.

### CHK020 — SC-003 measures relationship convergence that no requirement obliges (RECOMMENDED)

**Anchor**: spec.md:315-317 (FR-013, scoped to "a planned create or update"); spec.md:396-400
(SC-003); spec.md:160-184 (User Story 3).

**Evidence**: FR-013 is the only convergence requirement and it names create and update. SC-003 and
User Story 3 scenario 2 (spec.md:178-181) extend the convergence obligation to the relationship write
class across apply-twice and both crash variants. Nothing requires the relationship write to be
idempotent, so the criterion measures unrequired behavior.

**Minimum fix**: extend FR-013, or add a clause to FR-014, requiring that applying a planned
relationship write is convergent, so SC-003's relationship class has a requirement behind it.

### CHK022 — "clean-single-run counts" is undefined (RECOMMENDED)

**Anchor**: spec.md:396-400 (SC-003).

**Evidence**: the term is used three times in one sentence's worth of criterion with no baseline
definition anywhere in the spec, while the neighbouring SC-002 does define its own comparison
("apply-once and apply-twice object counts and identities", spec.md:394-395).

**Minimum fix**: define it once in SC-003 — the per-kind destination object counts and identities
observed after one uninterrupted apply of the same plan against the same starting state.
**Brief-level gap**: the brief's DBA-003 row uses the same undefined term and defers the method to a
validation source that is off limits to this session; the *Acceptance criteria* table's "Verification
evidence expected" column should have carried the definition inline.

### CHK023 — the crash-window criterion names a record the spec excludes, and FR-025 contradicts the ledger exclusion (BLOCKING)

**Anchor**: spec.md:396-399 (SC-003, "a crash injected after a write commits but before it is
recorded"); spec.md:344-345 (FR-020); spec.md:356-357 (FR-025); spec.md:460-461 (Out of Scope, "A
durable per-operation apply ledger surviving a crash. This outcome records applied operation
identifiers on the run result only").

**Evidence**: the only record the spec provides is the run result, and the run result is not written
per operation. `apply_cmd` saves `run.json` once with `status: running` before `apply_plan()` and once
after it returns (`cli.py:322-323`, `345-351`); `RunFile.save` writes the whole payload atomically in
one shot (`cache/sidecars.py:88-90`). So after a hard crash mid-apply, `run.json` reads
`status: running` with no applied identifiers **regardless of which side of a destination write the
crash landed on** — the boundary SC-003 uses to distinguish its two windows is unobservable. Two
consequences: (a) SC-003's windows are still *injectable* and its assertion still *evaluable*, but
only because the measurement is destination-side (counts and identities), so the phrase "before it is
recorded" should not appear in the criterion; and (b) FR-025's "If an apply stops partway … the run
MUST record the last operation it reported as applied" cannot hold across a crash without the durable
per-operation write that Out of Scope forbids. Those two rules cannot both hold. The brief's DBA-003
demands both crash windows in the same words (brief *Acceptance criteria*, DBA-003), so the flaw is
inherited, but the spec restates it without repairing it.

**Minimum fix**: (1) restate SC-003's windows in destination terms — a crash injected after the
destination write commits and before the apply advances to the next operation, versus one injected
before the destination write is issued — dropping "before it is recorded"; and (2) restate FR-025 as
an in-process obligation: the run result written when the apply terminates records the last operation
reported as applied, and a hard crash may leave no run result at all, because durable per-operation
progress is out of scope.

### CHK024 — SC-008's comparison is not specified (RECOMMENDED)

**Anchor**: spec.md:419-422 (SC-008); AD003 at spec.md:60-62.

**Evidence**: SC-008 says destination relationships are "read back and compared against the plan's
relationship references" without naming which relationships are in scope or how peer ordering is
treated. AD003's canonical ordering fixes the *plan's* serialization for SC-006, not the comparison.
Infrahub peer sets are unordered in practice — the adapter sorts on read (`infrahub.py:581`) — so a
list-equality comparison would be arbitrary.

**Minimum fix**: state that SC-008 compares, for each relationship declared in the schema mapping for
the kind under test, the destination's peer set against the plan's reference list as an unordered set
of (peer kind, peer identity) pairs. This edit and CHK003's are the same sentence.

### CHK025 — FR-013 has no adapter-surface criterion (RECOMMENDED)

**Anchor**: spec.md:315-317 (FR-013); spec.md:520 (Traceability, "DBR-013 | FR-013; User Story 3");
spec.md:393-395 (SC-002).

**Evidence**: FR-013 is an obligation on the adapter surface, and every criterion that touches it
measures the end-to-end apply path. The surface is new — `apply_cached_row` has zero implementations
today (`potenda/__init__.py:354-360`; grep shows only the engine, one test double, and
`tasks/bench.py:413`) — and the project constitution makes the adapter contract the primary extension
point (`dev/constitution.md`, Principle III), so a criterion exercising the surface directly is what
lets a later adapter be held to the same bar.

**Minimum fix**: add one criterion asserting that the Infrahub adapter's planned-write surface,
invoked twice with the same planned operation, yields one destination object — or state explicitly
that FR-013 is verified through SC-002 only.

### CHK026 — FR-024 has no criterion, no traceability row, and names an unreadable observable (BLOCKING)

**Anchor**: spec.md:354-355 (FR-024); spec.md:248-252 (Edge case); spec.md:502-540 (Requirements
Traceability); spec.md:565-567 (Open Design Decisions).

**Evidence**: three findings, all confirmed.
(1) **No criterion.** SC-001 through SC-013 each trace to a DBA and none covers FR-024. The
Requirements Traceability table maps DBR/DBA rows only and does not list FR-024 at all (nor FR-021
through FR-026), so the requirement is unverifiable and untraced in a spec whose own table is the
completeness check.
(2) **Nothing in the tree can detect it.** No occurrence of `uniqueness_constraints` or
`human_friendly_id` exists anywhere in `infrahub_sync/`; the only `hfid` usage is a `client.get`
filter (`infrahub.py:326`, `337`). Detection *is* feasible — the fields exist on the SDK schema model
(`.venv/.../infrahub_sdk/schema/main.py:272,274`) and the adapter already caches the whole
destination schema (`infrahub.py:345`) — but no code path reads them today, so the requirement is
entirely greenfield and the spec offers no observable to build against.
(3) **The observable is the wrong one.** FR-024 warns when "a destination identifier attribute is not
unique-constrained". What convergence actually rides on is that the `<Kind>Upsert` mutation has a key:
`data["hfid"]` from the destination schema's `human_friendly_id`
(`.venv/.../infrahub_sdk/node/node.py:1838-1846`, `295-298`), which is `None` when the schema declares
no HFID or a component is missing (`node.py:127-138`). A kind can have a unique-constrained attribute
and still upsert unkeyed, and a kind can converge on an HFID that does not match the plan's
destination identity. So even a faithful implementation of FR-024 as worded would not detect the
condition that endangers DBA-002.

**Minimum fix**: (a) restate FR-024's condition in terms of the destination's convergence key — warn
when a kind's destination convergence key (human-friendly ID or uniqueness constraint) does not cover
the plan's destination identity for that kind; and (b) add one success criterion: a plan run against a
kind whose destination convergence key does not cover its plan identity emits a warning naming that
kind and identity, and the plan run's outcome is *(the outcome CHK006 asks the spec to fix)*.
**Brief-level gap**: the brief raises this from documentation to detection in its *Edge cases and
failure behavior* section ("Detect and report it … Documenting it as a precondition is not
sufficient") but adds no DBA row for it, so the brief's *Acceptance criteria* table is where the
criterion should have originated.

### CHK027 — SC-007's counts are unscoped (RECOMMENDED)

**Anchor**: spec.md:415-418 (SC-007).

**Evidence**: "destination object counts before and after" names no kind set, and unchanged totals do
not by themselves prove the object named by the delete operation survived.

**Minimum fix**: scope the counts to the kinds appearing in the applied plan, and add the direct
assertion that the object named by each delete operation is still present at the destination.

### CHK028 — SC-002's identity comparison is unscoped (RECOMMENDED)

**Anchor**: spec.md:393-395 (SC-002).

**Evidence**: "object counts and identities recorded against a live destination" is not scoped to an
enumerable set of kinds, and (per CHK011) "identity" has no definition.

**Minimum fix**: scope to every kind for which the applied plan contains an operation, and reference
the identity definition CHK011 asks for.

### CHK031 — no crash expectation for relationship writes specifically (RECOMMENDED)

**Anchor**: spec.md:396-400 (SC-003); spec.md:318-323 (FR-014).

**Evidence**: SC-003 measures the relationship class as "counts", but relationships are not objects
one counts — the failure mode is an object created with its peers unlinked, which leaves object counts
correct and relationships wrong. SC-008 compares relationships but involves no crash, so the
intermediate state falls between the two criteria and no requirement says a re-apply must repair it.

**Minimum fix**: state that after either crash variant a re-apply restores the relationships the plan
specified, and define the relationship class's measurement in SC-003 by reference to SC-008's peer-set
comparison rather than as counts. (Depends on the CHK018 fix.)

### CHK032 — a peer identity matching more than one destination object is unspecified (BLOCKING, `PRODUCT-AMBIGUITY`)

**Anchor**: spec.md:318-323 (FR-014); spec.md:354-355 (FR-024); spec.md:248-252 (Edge case).

**Evidence**: the non-unique-identifier condition is addressed only at plan time, as a warning. FR-014
introduces a destination query at apply time, which is exactly where the condition resurfaces with
teeth — and no behavior is stated for a multi-match. Today the destination query raises
`IndexError("More than 1 node returned")` (`.venv/.../infrahub_sdk/client.py:561-566`), i.e. an
opaque crash with no adapter, kind, or identity context, in direct tension with the constitution's
"never crash opaquely" (`dev/constitution.md`, Principle II) and with FR-023's standard for
actionable errors (spec.md:352-353).

**Minimum fix**: same clause as CHK004 — add the multi-match arm to FR-014, requiring a refusal that
names the kind, the identity, and the number of matches, and stating whether the run fails before any
further write. **Brief-level gap**: as CHK004 — the brief's *Edge cases and failure behavior* section
has no apply-time peer-resolution case.

### CHK033 — a planned update whose target vanished is unspecified (RECOMMENDED)

**Anchor**: spec.md:312-317 (FR-012, FR-013); spec.md:466 (Out of Scope, "Destination freshness
checks, plan expiration, and conflict policies").

**Evidence**: this is the sharpest code-level consequence in the checklist. The existing update path
resolves its target by `client.get(id=self.local_id, ...)` (`infrahub.py:622`), and `local_id` is a
destination-assigned node id captured only during a destination load (`infrahub.py:510`,
`infrahub_sync/__init__.py:232`). A saved-plan apply performs no destination load, and the plan
deliberately carries no destination-assigned identifier (spec.md:270-271, AD003 at spec.md:58-60). So
a planned update **must** execute through the identifier-keyed upsert path — and that path creates the
object when no match exists (`<Kind>Upsert`, `.venv/.../infrahub_sdk/node/node.py:1838-1846`). A
reviewed "update" therefore silently becomes a create when the target has been removed since planning,
making the applied set differ in kind from the reviewed set — the exact property FR-017 and the
outcome statement exist to protect. Nothing in the spec says this is intended.

**Minimum fix**: state in FR-013 what a planned update does when no destination object matches its
identity — converge by creating (and say so, so a reviewer knows an `update` may create), or fail the
operation — and note that detecting the drift is excluded by the freshness/conflict exclusion.
**Brief-level gap**: the brief's *Edge cases and failure behavior* section should have carried the
vanished-target case alongside "Missing destination write surface" and "Partial apply".

### CHK034 — a planned create against a drifted destination object is unspecified (RECOMMENDED, `PRODUCT-AMBIGUITY`)

**Anchor**: spec.md:315-317 (FR-013); spec.md:393-395 (SC-002); spec.md:466 (Out of Scope).

**Evidence**: FR-013 covers *repeating* an operation; it does not cover a create planned against a
destination that gained the object, with different values, between plan and apply. The upsert path
would overwrite the mapped attributes on the matched node (`infrahub.py:611-612` →
`.venv/.../infrahub_sdk/node/node.py:1838-1846`). Whether that is convergence, an implicit update, or
a conflict that should refuse is not stated, and "conflict policies" being out of scope arguably makes
overwrite the inherited default — but that is an inference, not a requirement.

**Minimum fix**: state in FR-013 that a planned create whose destination identity already exists
converges onto the existing object, and that detecting the divergent payload is excluded by the
freshness/conflict exclusion — or record the refusal reading. Do not decide it in review.

### CHK035 — an unknown peer kind is unspecified (NIT)

**Anchor**: spec.md:318-323 (FR-014); spec.md:267-272 (FR-002).

**Evidence**: references derive from `SchemaMappingField.reference` entries, so a peer kind absent
from the configuration is reachable only through a mis-typed `reference:` — and such a value still
becomes a graph node in `build_dependency_graph` (`dependency_graph.py:30-35`) while
`load_one_side` skips resources with no model class (`potenda/__init__.py:216-217`), so it fails
quietly today. Low consequence, and mostly subsumed once CHK004's zero-match rule exists.

**Minimum fix**: one sentence in FR-014 stating that a reference to a kind absent from the
configuration is a plan-time error.

### CHK036 — the convergent-write-path assumption is factually wrong for updates (BLOCKING)

**Anchor**: spec.md:483-484 (Assumptions: "The Infrahub destination adapter already has an
identifier-keyed write path that converges on repeat; FR-013 routes planned creates and updates
through it rather than inventing a new one"); spec.md:315-317 (FR-013).

**Evidence**: there is no single identifier-keyed convergent path. The **create** path is
identifier-keyed and convergent: `client.create(...)` + `save(allow_upsert=True)`
(`infrahub.py:611-612`) issues `<Kind>Upsert` keyed on the node's HFID
(`.venv/.../infrahub_sdk/node/node.py:1838-1846`, `295-298`). The **update** path is *not*: it opens
with `client.get(id=self.local_id, ...)` (`infrahub.py:622`), keyed on a destination-assigned node id
that exists only because a destination load populated it (`infrahub.py:510`) — unavailable at apply
time by construction. Its relationship handling compounds this: `update_node` resolves peers from
`node._client.store` with `fallback=False` (`infrahub.py:133-140`, `155-162`), and that store is
populated by `model_loader` during a destination load (`infrahub.py:454`), so with no load every peer
resolves to `None` and is skipped with a warning (`infrahub.py:141-143`). FR-013's stated approach —
route creates *and updates* through the existing path "rather than inventing a new one" — is therefore
not executable for updates. Separately, this assumption carries neither the evidence it rests on nor
an impact-if-wrong clause, unlike the unique-constraint assumption immediately above it
(spec.md:471-473).

**Minimum fix**: restate the assumption: the adapter's create path is identifier-keyed and convergent
via the destination kind's human-friendly ID / uniqueness constraint, while the existing update path
is keyed on a destination-assigned id captured during a destination load and is therefore unusable
from a saved plan; FR-013's planned update must execute through the upsert path. Add the
impact-if-wrong clause. **Brief-level gap**: the brief's *Dependencies and shared contracts* row
"Infrahub destination adapter convergent write path — External, already present — Satisfied — The
adapter's identifier-keyed upsert converges on repeat" asserts the satisfied state for the whole write
surface; it should have scoped the claim to create/upsert and flagged the update path.

### CHK037 — the dependency on existing tiers is presumed, not recorded (RECOMMENDED)

**Anchor**: spec.md:267-272 (FR-002's per-operation dependency tier); spec.md:322 (FR-014);
spec.md:469-489 (Assumptions); spec.md:491-500 (Dependencies).

**Evidence**: FR-002 requires each operation to carry a dependency tier and FR-014 leans on tier
ordering, but neither the Assumptions nor the Dependencies section records that a tier computation
already exists, what shape it has, or when it is absent. It is kind-level, not per-operation
(`dependency_graph.py:64-107`), and it is `None` outright for configurations declaring an explicit
`order:` (`infrahub_sync/__init__.py:132-133`). The Assumptions section does record the analogous
engine/artifact-layout dependency (spec.md:479-482), so the omission is inconsistent with the spec's
own practice.

**Minimum fix**: add an assumption recording that the engine computes kind-level tiers from
`schema_mapping[].reference`, that no tiers exist when a configuration declares an explicit `order`,
and that this outcome derives its per-operation tier from them.

### CHK039 — the unique-constraint assumption's scope is ambiguous (RECOMMENDED)

**Anchor**: spec.md:471-473 (Assumptions); spec.md:354-355 (FR-024).

**Evidence**: "on the qualified path" reads either as every kind in
`examples/netbox_to_infrahub/config.yml` (21 mapping entries) or as only the kinds a given plan
touches. FR-024 evaluates per kind ("naming the affected kind and identifier"), which implies the
narrower scope, but the assumption that underwrites SC-002 does not say. The scope decides whether one
non-conforming kind invalidates SC-002 or merely produces a warning.

**Minimum fix**: scope the assumption to every kind for which the plan under test contains an
operation.

### CHK040 — no impact-if-not-ratified statement for AD003/AD004 (NIT)

**Anchor**: spec.md:31-34 (Clarifications preamble); spec.md:271-272, 323, 330 (per-FR `per AD00N`
annotations); spec.md:555-557 (Open Design Decisions).

**Evidence**: the dependency *is* stated — FR-002, FR-014 and FR-015 each name their AD, so the
revisit set is mechanically derivable — but nothing says what changes if AD003 or AD004 is rejected.

**Minimum fix**: one sentence in Open Design Decisions naming the requirements that would be reopened
if AD003 or AD004 is not ratified (FR-002 and FR-014 for AD003; FR-015 and FR-016's boundary for
AD004).

### CHK041 — the relationship-bearing-kind precondition for SC-008 is unrecorded (NIT)

**Anchor**: spec.md:477 (Assumptions, qualified path); spec.md:419-422 (SC-008).

**Evidence**: SC-008 requires "a relationship-bearing kind from the qualified configuration". The
named config satisfies this abundantly — 18 of 21 mapping entries carry `reference:` fields, including
cardinality-many ones such as `LocationSite.tags → BuiltinTag`
(`examples/netbox_to_infrahub/config.yml:74-76`) and `InterfacePhysical.ip_addresses → IpamIPAddress`
— so the assumption holds in fact but rests on a reader inspecting the config.

**Minimum fix**: extend the qualified-path assumption with a half-sentence noting that the
configuration contains relationship-bearing kinds of both cardinalities, which is what SC-008 needs.

## Items marked SATISFIED, with the text that satisfies them

- **CHK001** — spec.md:352-353 (FR-023: "Applying a plan against an adapter with no planned-write
  surface MUST fail with a clear, actionable error naming the adapter, before any write is attempted")
  plus spec.md:477 ("The qualified path is NetBox → Infrahub") bound FR-013's Infrahub-only obligation
  and give every other adapter a defined loud failure. Matches the engine's present behavior
  (`potenda/__init__.py:354-360`).
- **CHK008** — spec.md:344-345 (FR-020) states what apply records; combined with FR-002's per-operation
  action (spec.md:268) and AD002's identifier derived from `(action, kind, identity)` (spec.md:50-51),
  the applied identifier set joins back to the plan, which is what SC-005's comparison and SC-003's
  per-class matrix need.
- **CHK010** — spec.md:316-317 ("convergently, so that repeating an operation does not create a second
  object") and spec.md:393-394 ("the same object, the same identity, no duplicate") are
  postconditions, not mechanisms.
- **CHK014** — spec.md:320 ("MUST take an operation's own result as the resolution for later operations
  referring to it") denotes the destination object the operation wrote, which exists for a create and
  for an update alike. (The prior question — how an update finds its target at all — is CHK033.)
- **CHK016** — AD004 at spec.md:71-73: "a delete is structurally incapable of reaching the destination
  rather than merely being suppressed by configuration", carried into FR-015 at spec.md:326-329. This
  is exactly the configuration-independence the item asks for, and it replaces today's
  configuration-dependent suppression (`potenda/__init__.py:86-93`, defaulting to
  `DiffSyncFlags.SKIP_UNMATCHED_DST`).
- **CHK021** — spec.md:462-463 excludes building batched writes; spec.md:358-359 (FR-026) only forbids
  a plan contract that would preclude them. No conflict.
- **CHK029** — determined by FR-014 + AD003 as written: the cache is populated only by planned creates
  and updates (spec.md:63-64), so a delete never contributes a resolution, and the destination-query
  fallback (spec.md:321) resolves the peer, which still exists because FR-016 forbids applying the
  delete.
- **CHK030** — FR-017 (spec.md:334-336) fails the run and applies the supported operations, of which a
  delete-only plan has none; FR-016 (spec.md:331) forbids the write; FR-022 (spec.md:349-351) keeps
  the empty case distinct. The behavior is fully determined.
- **CHK038** — spec.md:462-463 ("Load-path reference-scan replacement … Only apply-path peer
  resolution is here"), FR-014's "at apply time" (spec.md:318), and FR-016's "The existing write
  path's behavior … is unchanged" (spec.md:331-332) together tell an implementer which resolution path
  is out of bounds.
- **CHK042** — spec.md:565-567: "The requirement and the warning's content are fixed; the detection
  mechanism is a planning-phase choice with no cross-outcome contract attached." Exactly the
  fixed-versus-open split the item asks for. (That the *content* names the wrong observable is
  CHK026's finding, not this item's.)
