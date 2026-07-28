# Engineering lens — round 1

**Feature**: `001-plan-artifact-saved-apply-infp-653` | **Head**: `016802e` | **Date**: 2026-07-26

**Remit**: correctness, feasibility, testability, error handling, typing, repository standards.
Reviewed through the `wwpd` persona (invoked; `references/testing-and-standards.md` loaded), which is
why the mock-versus-real-seam findings below carry the weight they do.

**Method**: every code fact this review turns on was read in the tree or in `.venv`. Nothing is
accepted from the artifacts' description of the codebase. Spot-checked facts and their verdicts are
listed at the end.

## Findings

| ID | Severity | Summary | Anchor |
|---|---|---|---|
| E1 | **Must-Address** | The PD-005/AD038 replace-set is a guaranteed no-op against the real SDK; it can only pass against a mock | `infrahub_sync/adapters/infrahub.py:149-175`; `.venv/…/infrahub_sdk/node/relationship.py:262,285-288`; tasks.md:244,253,265 |
| E2 | **Must-Address** | Step 3b asserts against `data`, but keyedness is a property of the generated mutation; "an unkeyed write is never issued" is false for the qualified path's own example | plan.md:456; contracts/destination-write-surface.md:56,94; `.venv/…/infrahub_sdk/node/node.py:100-107,135-139,295-298` |
| E3 | **Must-Address** | T081 assertion 3 ("the same operation twice produces exactly one `client.create`") is vacuous or false; it measures nothing offline | tasks.md:271 |
| E4 | **Must-Address** | FR-020's applied-operation set has no home: `RunFile.KEYS` is closed, plan.md declares `cache/` UNCHANGED, T065 requires reading it from `run.json` | `infrahub_sync/cache/sidecars.py:68-88`; plan.md:191; tasks.md:296 |
| E5 | **Must-Address** | V22 is wrong: the schema-subhash abort is unreachable, twice over. T060's fix and its test exercise dead code | `infrahub_sync/cli.py:329-341`; `infrahub_sync/utils.py:256-263`; plan.md:105; tasks.md:289-291 |
| E6 | Recommended | Derivation walks only top-level `diff.children`; a nested child element is silently dropped — the one outcome AD047 exists to prevent | tasks.md:199; `.venv/…/diffsync/diff.py:196` |
| E7 | Recommended | `DuplicateOperationIdError` conflates a real duplicate triple with a 64-bit hash collision; the message misdirects the operator | tasks.md:160; spec.md:1199-1204 |
| E8 | Recommended | SC-012's evidence is a raw `--help` text diff — width-, colour- and runner-dependent | tasks.md:111,295 |
| E9 | Recommended | `derive_deletes` "per kind" is unpinned; iterating `schema_mapping` entries double-derives `DcimDevice` deletes and fails the plan run on the qualified path | tasks.md:210; `examples/netbox_to_infrahub/config.yml:212,254` |
| E10 | Recommended | Phase E's entire local evidence is a mocked `InfrahubClientSync`; E1 and E2 are both invisible to a mock and both visible offline to a real `InfrahubNodeSync` | tasks.md:264-271; contracts/destination-write-surface.md:267-268 |
| E11 | Nit | T033 carries two contradictory `Done when` lines; the stale one says "three cases" for a four-case T039 | tasks.md:213-214 |
| E12 | Nit | The AD050 probe mutates `LocalStore._data` (a `defaultdict`), permanently changing `get_all_model_names()` | `.venv/…/diffsync/store/local.py:20,47` |

---

## E1 — Must-Address: the replace-set mitigation is a no-op against the real SDK

**Claim under review.** PD-005/AD038 is the mitigation for the plan's own highest-rated relationship
risk: "it makes FR-013's replace-set clause true by construction instead of by assumption about
server behavior nobody here can test" (research.md:242). Step 7 of the write surface is
`_replace_relationship_set(node, ref.field, resolved_peer_ids)` against the **saved node**
(contracts/destination-write-surface.md:63).

**What the code does.** Trace the node that reaches step 7:

1. `create_data = client.schema.generate_payload_create(...)` renders a cardinality-many relationship
   as `[{"id": "<uuid>", …}, …]` — `.venv/…/infrahub_sdk/schema/__init__.py:178-181`.
2. `node = client.create(kind=…, data=create_data)` constructs `InfrahubNodeSync(client, schema,
   data=create_data)` — `.venv/…/infrahub_sdk/client.py:1828`.
3. `_init_relationships` builds `RelationshipManagerSync(…, data=rel_data)` for every many-cardinality
   relationship — `.venv/…/infrahub_sdk/node/node.py:1379-1385`.
4. `RelationshipManagerSync.__init__` sets `self.initialized = data is not None` and populates
   `self.peers` from that list — `.venv/…/infrahub_sdk/node/relationship.py:262,269-278`.
5. `save(allow_upsert=True)` → `create(allow_upsert=True)` → `_process_mutation_result`, which
   refreshes `self.id` and attribute values but touches relationships **only** for resource pools —
   `.venv/…/infrahub_sdk/node/node.py:1533-1534,1825-1836`.

So at step 7, `attr_manager.peer_ids` is already exactly the set we are about to write.
`compare_lists(existing, new)` returns `existing_only == []` and `new_only == []`, and the
`if not attr_manager.initialized: attr_manager.fetch()` guard at
`infrahub_sync/adapters/infrahub.py:168-169` does not fire because `initialized` is `True`. The
reconciliation removes nothing and adds nothing, unconditionally.

**Second problem, in the code being reused.** `update_node` reads `existing_peer_ids =
attr_manager.peer_ids` at `infrahub_sync/adapters/infrahub.py:151` — **before** the `fetch()` at
`:168-169`. When the manager arrives uninitialised (the common case for a node from `client.get`
without `include=[rel]`), `peer_ids` is `[]`, `existing_only` is `[]`, and the loop degrades to
pure addition. So V12's "Cardinality-many replace-set exists today … via `compare_lists`" is an
overstatement: what exists is an intended replace-set whose statement order makes it additive.
T042's "behaviour-preserving extraction" therefore preserves that.

**Why it matters.** Step 7 is the only thing standing between the feature and stale peers on
re-apply, and SC-008 — the criterion that would catch it — is deferred (AD045b). T051 asserts
"after the upsert, existing-only peers are removed and new-only peers added"; against a
`MagicMock` client the test author hand-builds a manager reporting different existing peers, the
assertion passes, and production merges. This is the exact category the review brief names: a
capability with no real implementation, only a test double.

**Minimum fix.**

1. In `_replace_relationship_set`, obtain the *destination's* current peer set before comparing —
   either `rm = getattr(node, rel_name); rm.initialized = False; rm.fetch()`, or a scoped
   `client.get(id=node.id, kind=…, include=[rel_name])` — and reorder so `existing_peer_ids` is
   read after that. Fix the same ordering in `update_node` (it is a one-line move and it is the
   caller the extraction is supposed to preserve).
2. Re-word V12 and research.md:230-238 so they do not describe the existing code as a verified
   replace-set.
3. Make T051 construct a real `InfrahubNodeSync` (see E10) so the no-op cannot pass.

---

## E2 — Must-Address: step 3b asserts against the wrong artifact, and "no unkeyed write is ever issued" is false

**Claim under review.** plan.md:456 and contracts/destination-write-surface.md:56 state flatly that
step 3b means "An unkeyed write is never issued", and tasks.md:251 repeats it. AD051 defines
"accounted for" against the `data` mapping: a relationship-crossing HFID component
`<rel>__<attr>__value` passes when `data[<rel>]` is present and non-null **and** the operation's
nested `{peer_kind, identity}` for `<rel>` supplies `<attr>`.

**What the code does.** The plan's own V39 already established the mechanism; the consequence was not
carried into the claim. Take the artifact format's own second worked example
(contracts/plan-artifact-format.md:70): a `LocationRack` whose identity is `{name, site}` with `site`
travelling as a relationship reference. At apply, `data = {"name": …, "site": "<uuid>", "tags": […]}`.
Step 3b passes on both arms. Then:

- `generate_payload_create` renders `site` as `{"id": "<uuid>", "source": …}` with **no**
  `__typename` — `.venv/…/infrahub_sdk/schema/__init__.py:175-177`.
- `RelatedNodeSync.__init__` sets `_typename = node_data.get("__typename")` → `None` —
  `.venv/…/infrahub_sdk/node/related_node.py:64-68`.
- `get_path_value("site__name__value")` calls `related_node.get()`, which needs `id` **and**
  `typename`, falls through to `hfid_str` (also `None`) and raises `ValueError`; the caller catches
  it and returns `None` — `.venv/…/infrahub_sdk/node/node.py:100-107`,
  `.venv/…/infrahub_sdk/node/related_node.py:298-304`.
- One `None` component nulls the whole HFID — `.venv/…/infrahub_sdk/node/node.py:135-139`.
- `_generate_input_data` then emits neither `id` nor `hfid` —
  `.venv/…/infrahub_sdk/node/node.py:295-298`. (Note the sync path uses `exclude_hfid=False` at
  `:1844`, unlike the async path at `:1038`, so the HFID *would* be sent if it existed.)

The mutation goes out unkeyed, and step 3b said it was fine. The assertion passes precisely in the
case the risk row (plan.md:644) says it cannot see. The risk row is honest; the three places that
state the flat guarantee are not, and an implementer will write a test named after the flat
guarantee that cannot be true.

**What makes this fixable rather than merely deferrable.** Keyedness *is* checkable offline, on the
real SDK object, one line before `save()`:

```python
input_data = node._generate_input_data(exclude_hfid=False)["data"]
if "id" not in input_data and "hfid" not in input_data:
    raise UnkeyedPlannedWriteError(kind=..., operation_id=..., components=...)
```

That check fails for the `LocationRack` case above and passes for a kind whose HFID is all-direct.
It is the difference between asserting the plan carried the components and asserting the mutation
can use them.

**Minimum fix.**

1. Move the step-3b gate onto the generated mutation input as above, keeping the AD051 per-component
   rule as the *diagnostic* that names which component is missing.
2. Delete the flat "an unkeyed write is never issued" claim from plan.md:456,
   contracts/destination-write-surface.md:56,121 and tasks.md:251, or requalify it as "no write is
   issued whose data is missing an HFID component" — which is what `data`-level checking supports.
3. Add the corresponding case to T081 (a fixture kind whose HFID crosses a relationship must be
   asserted to produce a keyed mutation, not merely accounted-for `data`).

---

## E3 — Must-Address: T081 assertion 3 measures nothing

**Text.** tasks.md:271, assertion (3): "applying the same operation twice produces exactly **one**
`client.create` invocation with `allow_upsert=True` on save and **no second create**; convergence
measured at the mutation, not at the destination."

**Why it cannot hold.** FR-021 forbids two operations sharing an identifier in one plan
(spec.md:1199), so "twice" must mean two apply runs. Two apply runs issue two `client.create` calls —
there is no operation-level dedup in the design, `PeerResolver`'s memo is keyed on peers not on
operations (contracts/destination-write-surface.md:157-161), and dedup would be wrong anyway. So
either the assertion is false as written, or it silently means "one create per apply", which is
trivially true of any loop and demonstrates nothing.

Against a mocked client there is no destination state, so no mock-based assertion can establish
that the second upsert converged. The Done-when at tasks.md:272-273 requires "each is demonstrated
to fail when its behavior is reverted" — there is no behaviour to revert for (3).

**Minimum fix.** Replace assertion 3 with something offline-decidable and load-bearing: the two
applies must issue **byte-identical, keyed** mutation payloads for the same operation (same
`hfid`/`id`, same `data`), asserted on `node._generate_input_data()`. That is a genuine convergence
*precondition* and it fails if E2's defect regresses. Stop describing it as "the offline half" of
SC-002/SC-003 — see the harness verdict below.

---

## E4 — Must-Address: FR-020's applied-operation set has no home, and two rules forbid the obvious one

**The three statements.**

- `RunFile` is a dataclass with a closed `KEYS: ClassVar = ("status", "mode", "summary",
  "finished_at")`, and `save()` serialises exactly those keys —
  `infrahub_sync/cache/sidecars.py:68-88`.
- plan.md:191 lists `cache/ # UNCHANGED — paths, sidecars, parquet_io, locks, incremental`.
- T065's Done-when requires "`failed` with an **empty** applied-operation set **read back from
  `run.json`**" — tasks.md:296. T059 requires "an empty applied-operation set rather than an absent
  field" — tasks.md:287.

`run.json` is written only by `RunFile.save()`. So the applied-operation set cannot reach `run.json`
without changing `infrahub_sync/cache/sidecars.py`, which plan.md says is untouched and which no
task authorises. The one escape — putting it inside the free-form `summary: dict[str, Any]` — is
nowhere stated, and `summary` currently carries `{"resources": n}` (`infrahub_sync/cli.py:155`).

**Why it matters beyond tidiness.** FR-020 is a shared contract: the brief names DB-012 (the apply
ledger) as a consumer of these identifiers, and DB-003's public API reads the run result. An
implementer picking a location arbitrarily fixes a cross-outcome key name by accident. Separately,
T056 (SC-005's evidence) compares "the FR-020 record on the run result" against review output — a
comparison with no defined subject.

**Minimum fix.** One new task, before T047, that pins the field: either extend `RunFile` with
`applied_operations: list[str] = field(default_factory=list)` and add it to `KEYS` (and correct
plan.md:191), or state in data-model.md that it lives at `summary["applied_operations"]`. Reference
that decision from T047, T056, T059 and T065.

---

## E5 — Must-Address: V22 is wrong; the schema-subhash abort is unreachable, twice over

**The claim.** V22 (plan.md:105): "The `apply` command writes `run.json` with `status: running` and
then, on a schema-subhash mismatch, aborts through `print_error_and_abort`, permanently leaving
`running` on disk." T060 fixes it; T065 asserts the fix; FR-009's owning-task list includes T060.

**What the code does.**

1. The whole check sits in a `try` whose third import is
   `from infrahub_sync.utils import _resolve_infrahub_schema` — `infrahub_sync/cli.py:330`, carried
   with `# ty: ignore[unresolved-import]` and a comment saying "Plan 2 will provide" it.
   `grep -rn "_resolve_infrahub_schema" infrahub_sync/` returns **only** the three lines in
   `cli.py` — the symbol does not exist. The `except ImportError: pass` at `:341` swallows it, so
   `:336-340` is never reached.
2. Even if the symbol existed, `get_potenda_from_instance` writes
   `SchemaHashFile(path=rdir / "schema-sub-hash.txt", value=subhash).save()` with the **current**
   subhash into the same run directory before returning — `infrahub_sync/utils.py:256-263`. The
   apply then reads that file back at `infrahub_sync/cli.py:338` and compares it with `current`.
   They are equal by construction, so the mismatch branch could never fire.

**Consequence.** T060 changes a line that cannot execute, and T065's schema-subhash case can only
pass by monkeypatching a symbol into `infrahub_sync.utils` that the product does not define — a test
asserting against a stub the reviewer supplied. It reads as coverage of FR-009 and is not.

**Minimum fix.** Correct V22 to state that the branch is currently unreachable and why. Then choose
explicitly: either drop T060 and its T065 case with a one-line note (the run-state bug does not
occur), or add the two-line change that makes the check live (do not rewrite
`schema-sub-hash.txt` when `run_id` was supplied and the file already exists) and keep the fix — but
that is scope this brief does not carry, so the first option is the honest one.

---

## E6 — Recommended: nested diff children are silently dropped

`operations_from_diff` is specified to "walk `diff.children` as `_diff_to_rows` does" (tasks.md:199),
which is a single level. Every `DiffElement` carries a `child_diff` (`.venv/…/diffsync/diff.py:196`),
populated for models that declare `_children`. Generated models never do (V29 verified: the template
at `infrahub_sync/generator/templates/diffsync_models.j2:29-48` sets `_identifiers` and `_attributes`
only), but the engine loads third-party adapters through `infrahub_sync/plugin_loader.py` and nothing
constrains their models.

Under FR-030/AD047 every derivation failure is fatal precisely so a plan is never silently
incomplete. A dropped child element is a silently incomplete plan reached by a different route.

**Minimum fix.** In `derive.py`, either recurse into `element.child_diff.children`, or — cheaper and
sufficient — raise a named derivation error when `element.child_diff` is non-empty, naming the kind
and the child kind. Add the case to T083.

## E7 — Recommended: the duplicate-identifier error cannot tell a duplicate from a collision

`operation_id` truncates SHA-256 to 16 hex digits (64 bits). FR-021 (spec.md:1199-1204) reasons that
"exactly one operation exists per (action, kind, destination identity), so a collision is always
pathological", and T016 raises `DuplicateOperationIdError` "naming both operations' kind, action and
identity". Those are two different events. A genuine duplicate means two operations share a triple
(a derivation bug — see E9). A 64-bit collision means two *different* triples hashed the same; the
operator remedy is entirely different, and the message as specified would send them hunting a
duplicate that does not exist. Birthday odds are small (~2.7e-6 at 10^7 operations) but the brief
lists identifier collision as an edge case in its own right.

**Minimum fix.** On a clash, compare the two `(action, kind, canonical_identity)` triples: equal →
duplicate (a derivation defect); unequal → collision, with its own error type and message. One extra
branch in `writer.py`, one extra case in T017.

## E8 — Recommended: SC-012's evidence is a fragile text diff

T002 captures `uv run infrahub-sync --help` from a real terminal into a committed fixture; T064
captures `--help` "after the change and compare[s] as text" (tasks.md:111,295). Typer renders help
through rich: the output depends on terminal width, colour support and `NO_COLOR`, and `CliRunner`'s
default width differs from an interactive shell. This test will fail for reasons unrelated to the
command set, on someone else's machine or in CI.

**Minimum fix.** Assert on the structured command list — `app.registered_commands` / the click group's
`list_commands()` — plus the existing `no add_typer` grep. Keep the text file as documentation, not
as the assertion.

## E9 — Recommended: `derive_deletes` "per kind" is unpinned and can fail the qualified path

tasks.md:210 says "per kind, destination-store identities minus source-store identities". On the
qualified configuration `DcimDevice` is declared by **two** `schema_mapping` entries
(`examples/netbox_to_infrahub/config.yml:212`, `:254`). If the implementation iterates
`config.schema_mapping`, every `DcimDevice` delete is derived twice, both copies carry the same
`(delete, DcimDevice, identity)` triple and therefore the same `operation_id`, and T016's uniqueness
assertion raises `DuplicateOperationIdError` — failing the plan run on the brief's own qualified
path. Iterating `adapter.top_level` (unique kinds, as `_write_side_snapshot` does at
`infrahub_sync/potenda/__init__.py:136`) is correct.

**Minimum fix.** Say "per destination kind in `top_level`" in T032 and data-model.md, and add a
duplicate-mapping-entry delete case to T084.

## E10 — Recommended: the one surface with zero implementations is evidenced entirely by mocks

Every local test for Phase E is "against a mocked `InfrahubClientSync`" — T050, T051, T052, T053,
T081 (tasks.md:264-271; contracts/destination-write-surface.md:267-268). Both E1 and E2 are
invisible to a mocked client and both are visible offline to a **real** `InfrahubNodeSync`: node
construction, relationship-manager initialisation, `get_human_friendly_id()` and
`_generate_input_data()` are all pure client-side computation over a `NodeSchemaAPI`. The only thing
needing a server is the HTTP round trip, and the SDK's transport is `httpx`.

This is the repository's own standard talking: a component that can only be checked through a mock
is telling you the seam is in the wrong place. The seam here is `client.execute_graphql`, not
`client`.

**Minimum fix.** For T050, T051 and T081, build a real `InfrahubNodeSync` from a committed
`NodeSchemaAPI` fixture (one all-direct-HFID kind, one relationship-crossing kind) with the HTTP
layer faked, and assert on the rendered mutation. Reserve `MagicMock` for the engine-level apply-loop
tests (T055), where the adapter really is the boundary.

## E11 — Nit: T033 has two contradictory `Done when` lines

tasks.md:213-214 reads `**Done when**: T039 and T085 pass.` immediately followed by
`**Done when**: T039 passes all three of its cases.` T039 now carries four cases (tasks.md:224).
Delete the second line.

## E12 — Nit: the AD050 probe mutates the store it probes

`LocalStore._data` is a `defaultdict(dict)` (`.venv/…/diffsync/store/local.py:20`) and `get()` does
`if uid not in self._data[modelname]` (`:47`), which inserts an empty bucket for any modelname
probed. Probing candidate kinds therefore permanently adds them to `get_all_model_names()`. Nothing
in this repository reads that today, so the impact is nil — but the probe is specified as read-only
and is not. Prefiltering candidates against `store.get_all_model_names()` before probing costs
nothing and removes the side effect.

---

## Things checked and found sound

Recorded so they are not re-litigated.

- **Canonical JSON and the checksum.** `sort_keys=True, separators=(",", ":"), ensure_ascii=False`,
  LF-only, three fields *removed* not blanked, no separator between manifest and operations bytes —
  internally consistent, and PD-008's logical-row snapshot digest is the only reading under which
  AD008 and SC-006 can both hold. `_extract_ts` really is stamped per row per run
  (`infrahub_sync/cache/parquet_io.py:126`; `infrahub_sync/potenda/__init__.py:130`), so the raw-bytes
  alternative would indeed have made SC-006 unachievable. Correct call.
- **AD050's bounded probe against the real store API.** V37 is right: `BaseStore.get` requires
  `model` and `_get_object_class_and_model` returns `(None, modelname)` for a model the adapter does
  not define (`.venv/…/diffsync/store/__init__.py:241-254`), and `_get_uid` accepts a bare string
  identifier (`:256-272`), so `store.get(model=candidate, identifier=uid)` raises `ObjectNotFound`
  cleanly for a miss. The probe is constructible exactly as specified. The no-single-candidate-fallback
  rule is the right call.
- **Deriving deletes by set difference, and the full-extract precondition.** V6 verified
  (`infrahub_sync/potenda/__init__.py:92-93`; `.venv/…/diffsync/helpers.py:191-192`) — deletes are
  dropped before an element exists, so set difference is the only route. V25's OR-accumulation is
  real (`:200`), and T031's separate per-side dict is the minimal correct fix.
- **The tier restructure (PD-009/AD039).** V33 verified: `top_level` is read only inside
  `DiffSyncDiffer.calc_diff` (`.venv/…/diffsync/helpers.py:79-88`) and `sync_from` skips
  `diff_from` when a diff is supplied (`.venv/…/diffsync/__init__.py:605-608`). The corrected
  placement of the narrowing in the compute loop is right, and T040's per-tier *content* assertion is
  the assertion that catches getting it wrong. The equivalence argument holds because tiers partition
  kinds and a tier's sync only adds models of its own kinds to the destination store.
- **Routing creates and updates through `client.create` + `save(allow_upsert=True)`.** V10, V11, V36
  verified (`infrahub_sync/adapters/infrahub.py:602-604,611-613,622`). AD042's
  `element.keys ∪ element.source_attrs` is genuinely load-bearing: `get_attrs()`'s contract
  (`.venv/…/diffsync/__init__.py:340-352`) excludes `_identifiers` and the generator strips them from
  `_attributes` (`infrahub_sync/generator/__init__.py:95`). Catching this was the single most valuable
  thing the prior passes did. The residual is E2, not AD042.
- **Review is genuinely adapter-free.** V21 verified: `get_potenda_from_instance` mkdirs the run
  directory and writes `schema-sub-hash.txt` before any check (`infrahub_sync/utils.py:244-263`), so
  branching above it in `diff_cmd` is required and is what T058 specifies. `_require_safe_segment`
  (`infrahub_sync/cache/paths.py:11-23`) really is the traversal guard and is reused.
- **No new CLI group, no new dependency, no `ty` override.** V19 verified — five `@app.command`, no
  `add_typer` (`infrahub_sync/cli.py:31,77,86,166,295,355`). `pyproject.toml:315-321` has
  `[tool.ty]`, `[tool.ty.src]` and `[tool.ty.environment]` and **no** `[[tool.ty.overrides]]`; nothing
  in the plan adds one. `hashlib`/`json`/`pathlib` cover the artifact.
- **Task ordering.** The S→A→B→C→D→E→F→G chain is genuinely dependency-ordered, the D-is-not-green
  boundary is stated identically in plan.md:225-231 and tasks.md:232-236, and moving T066 into Phase E
  beside T048 is the correct resolution of what would otherwise have been an unsatisfiable Done-when.
  T029's back-dependency on T072 was correctly removed (tasks.md:455-457). The three named test-design
  traps are real traps and each is encoded rather than advised — trap 3 in particular (a plan whose
  every peer it creates itself never exercises the query path) is exactly the kind of self-passing test
  this lens exists to catch, and it was caught.
- **Error handling.** A named eight-member exception hierarchy, no broad `except`, every refusal
  naming check + run id + next action. The one broad-swallow pattern in the tree
  (`except ImportError: pass` at `infrahub_sync/cli.py:341`) is pre-existing and is the subject of E5,
  not something this plan introduces.

---

## Verdict on the offline conformance harness (T081)

**It does not prove convergence. As specified, two of its three assertions prove only that the mock
was called.**

- **Assertion 1** (every HFID component accounted for in each `client.create` call's `data`) is real
  and worth having — it is a genuine regression test for the AD042 defect class, and it would have
  failed against a `source_attrs`-only payload. But it proves the *plan carried the components*, not
  that the *mutation is keyed*. Those diverge on exactly the qualified path's relationship-crossing
  kinds (E2). Calling it the offline half of SC-002/SC-003 overstates it.
- **Assertion 2** (replace-set reconciliation issued for every cardinality-many relationship) can only
  pass against a mock, because against the real SDK the reconciliation is a guaranteed no-op (E1). It
  asserts that a call was made; the call does nothing.
- **Assertion 3** (a repeated operation produces no second create) is vacuous or false (E3), and no
  mock-based test can observe convergence, which is destination state.

**It can be made to prove something real, offline, with one change**: build a real
`InfrahubNodeSync` from a committed `NodeSchemaAPI` fixture and assert on
`node._generate_input_data(exclude_hfid=False)["data"]` — that it contains `id` or `hfid`, and that
two applies of the same operation produce byte-identical keyed payloads. That is a true convergence
*precondition*, it is decidable without a server, and it fails today's design on the
relationship-crossing case. Until then, the harness narrows the AD042 class only, and the plan's
statement that it "catches the class of defect those criteria were the only other check on"
(tasks.md:32-33) should be narrowed to "catches a payload-shape regression of the AD042 class".

The deferral itself (AD045b) is handled with unusual honesty — stated in plan.md, the evidence map,
the contract and tasks.md, and explicitly not counted as covered. That is the right treatment. The
problem is not the deferral; it is that the compensating control is weaker than claimed.

---

## Code facts spot-checked

Verified in the tree or `.venv` at head `016802e`.

| Fact | Verdict |
|---|---|
| V1, V2, V3 — `apply_plan` dispatch, lossy `_diff_to_rows`, zero `apply_cached_row` implementations | **Correct** (`infrahub_sync/potenda/__init__.py:297-370`; grep confirms only the engine, `tests/cache/test_apply_plan.py:43-44`, `tasks/bench.py:413`) |
| V4, V5 — `DiffElement.keys` / `source_attrs` disjoint; `get_attrs()` excludes `_identifiers`; action vocabulary | **Correct** (`.venv/…/diffsync/diff.py:189-196,237-254`; `helpers.py:212-223`; `__init__.py:340-352`) |
| V6 — deletes dropped under `SKIP_UNMATCHED_DST` before an element exists | **Correct** (`potenda/__init__.py:92-93`; `.venv/…/diffsync/helpers.py:191-192`) |
| V7, V8 — `_extract_ts` per side per run; sidecar tmp+`replace` | **Correct** (`cache/parquet_io.py:126`; `cache/sidecars.py:13-24`) |
| V10, V11, V36 — upsert create path; `update` keyed on `local_id`; ids+attrs both passed | **Correct** (`adapters/infrahub.py:602-604,611-613,622`) |
| V12 — "cardinality-many replace-set exists today" | **Overstated — see E1.** `existing_peer_ids` is read at `:151`, before `fetch()` at `:168-169`, so the loop is additive whenever the manager arrives uninitialised |
| V15, V39 — upsert keyed on `id` else `hfid`; nested HFID unreachable from a bare id | **Correct** (`.venv/…/infrahub_sdk/node/node.py:100-107,135-139,295-298`; `related_node.py:54-68,298-304`). Note the sync `create()` uses `exclude_hfid=False` (`:1844`) while the async one uses `True` (`:1038`) — the plan relies on the sync path, correctly |
| V18 — tiers, self-edge exclusion, `tiers is None` under `order:` | **Correct** (`dependency_graph.py:25-53,81-100`; `__init__.py:132-133`) |
| V19, V20, V21, V27 — flat CLI, `diff --run-id`, unconditional mkdir, run location | **Correct** (`cli.py:31,86,98,129,153`; `utils.py:244-263`; `cache/paths.py:11-59`) |
| V22 — schema-subhash abort leaves `running` on disk | **Wrong — see E5.** The branch is unreachable (missing `_resolve_infrahub_schema`, and the hash file is rewritten with the current value before it is read) |
| V23, V24, V25 — `write_plan` call sites; interleaved tier diff/sync; OR-accumulated extract flag | **Correct** (`potenda/__init__.py:462,480-499,197-200`) |
| V29, V30, V31 — flat generated models; ten identity-bearing references; `DcimDevice` declared twice | **Correct** (`generator/templates/diffsync_models.j2:29-48`; `examples/netbox_to_infrahub/config.yml:212,254`) |
| V33 — `top_level` read only by the differ | **Correct** (grep over `.venv/…/diffsync/` returns `helpers.py:79-88` and `__init__.py:441-549` only; the syncer never reads it) |
| V34 — `--continue-on-error` on `sync` only | **Correct** (`cli.py:190`; `diff_cmd` at `:87-165` declares none) |
| V37 — no kind-free store lookup | **Correct** (`.venv/…/diffsync/store/__init__.py:40-77,241-272`; `store/local.py:22-49`) |
| V38 — `self.schema` on the Infrahub adapter only; defensive read already in the tree | **Correct** (`adapters/infrahub.py:345`; `utils.py:260`) |
| `pyproject.toml` has no `[[tool.ty.overrides]]` | **Correct** (`pyproject.toml:315-321`) |

Two facts corrected, thirty-plus confirmed. Neither correction was catchable from the artifacts
alone — both needed the file open.
