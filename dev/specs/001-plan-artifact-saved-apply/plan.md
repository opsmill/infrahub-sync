# Implementation Plan: Saved plan artifact and apply-exactly-what-was-reviewed

**Branch**: `001-plan-artifact-saved-apply-infp-653` | **Date**: 2026-07-26 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `dev/specs/001-plan-artifact-saved-apply/spec.md`, itself
derived from delivery brief DB-001 (brief_version 5, batch-v3, primary card INFP-653).

## Summary

A plan run writes a durable, canonical, checksummed plan artifact — `<run_dir>/plan/manifest.json`
plus `<run_dir>/plan/operations.jsonl` — carrying every proposed create, update and delete with a
derived operation identifier, a full mapped payload, relationship references named by peer kind and
peer identity, and a dependency tier. That artifact is reviewable at summary and per-object depth
from a stored run in a process that never touches source or destination, verifiable against five
pre-apply checks, and applicable by run ID through a new convergent write surface on the Infrahub
adapter with apply-time peer resolution.

The engine is already most of the way there: `Potenda.apply_plan` already reads a stored plan and
dispatches per row without calling the comparison engine
(`infrahub_sync/potenda/__init__.py:341-370`), and per-side snapshots plus run sidecars are already
written behind fsspec (`infrahub_sync/potenda/__init__.py:123-161`, `infrahub_sync/cache/`). What is
missing, and what this plan builds: a lossless artifact format (today's rows drop `dest_id` and
`attribute` entirely — `infrahub_sync/potenda/__init__.py:317-330`), the destination write surface
(`apply_cached_row` has zero adapter implementations anywhere in the tree), delete derivation,
apply-time peer resolution without a comparison store, the five-check pre-apply gate, and a
read-from-artifact review mode on the existing `diff` command.

AD001–AD064 in the spec's Clarifications are treated as settled input throughout. Where this plan had
to close a detail those decisions left open, or where two of them interact in a way neither
anticipated, the resolution is recorded in [research.md](./research.md) as **PD-nnn** and flagged,
not buried. Five of those PDs were ratified into the spec as AD037–AD041; AD042–AD048 came from a
later cross-artifact analysis and are settled input here in the same way.

**AD054–AD064 were ratified after the first three-lens critique round and land in this plan as five kinds of
change**, listed here so a reader knows what moved:

| Ratified decision | What changes in this plan |
|---|---|
| **AD054** | Code fact **V12 is corrected** — the existing `update_node` "replace-set" adds without removing, and V12a/V12b are added. Phase E's replace-set enforcement re-reads the destination peer set; Phase E's conformance harness is rebuilt against a committed schema fixture and asserts the **rendered mutation input**. Its third clause, correcting the pre-existing ordering on the live update path, is **withdrawn by AD070** |
| **AD055** | Phase E's apply loop ends a delete-bearing plan in **`applied`** with a recorded skipped-delete count and an operator-visible warning, not `failed`. FR-016, FR-017, FR-020 and SC-007 all move with it |
| **AD056** | Phase C's review surface carries the delete-computation record and the delete-count annotation; Phase F's renderer shows both; SC-009's pass condition grows |
| **AD057** | Phase F's review mode is `--from-plan <run-id>`; `--run-id` keeps one meaning |
| **AD058, AD059, AD061, AD062** | Contract-level: the resolver's declared entry point, the verifier's adapter-name argument, the next-action obligation across the taxonomy, specified help text, and `summary["applied_operations"]` as FR-020's named home |
| **AD060** | The SC-012 baseline is the committed fixture from T002; the quickstart's checksum snippet takes its run directory as an argument |
| **AD063** | Code fact **V22 is corrected** — the schema-subhash abort is unreachable. **T060 and its test case are dropped**; the plan does not repair dead code |
| **AD064** | DBA-006 is reported as **conditionally** carried, with the pinned-extraction-mode condition named |

**AD065–AD074 were ratified after the second critique round.** All ten correct this plan's own delivery
and **AD070 removes scope from it**; none adds any.

| Ratified decision | What changes in this plan |
|---|---|
| **AD065** | "Fetch first" is not a re-read: the destination library's peer load self-guards on an initialized flag that a locally built node already sets, so Phase E names the forcing mechanism — discard the locally held peer set before loading, or issue a scoped destination read — and the test observable becomes **a destination read was issued** rather than a load was attempted |
| **AD066** | The flat "an unkeyed write is never issued" guarantee is **struck**. Phase E's pre-write gate gains a rendered-mutation check that refuses an unkeyed render for an all-direct convergence key and warns once per kind for a relationship-crossing one; the per-component check over `data` stays as the diagnostic |
| **AD067** | The conformance harness's keyedness assertion **splits**: all-direct kinds must render keyed; the relationship-crossing kind carries the same assertion as a **strict expected failure** citing the recorded Material risk, so it self-invalidates when the risk closes |
| **AD068** | "Two applies produce one create" becomes **byte-identical rendered mutation inputs**, and every downstream restatement moves with it |
| **AD069** | `apply_plan` **returns** an apply record and writes no run file; the CLI **merges** it into the run file's summary before saving. A mid-apply rejection carries its partial record on the raised error |
| **AD070** | Phase E's replace-set enforcement is **new code on the planned-write path only**. `update_node` is left exactly as it is and its additive ordering is recorded as a pre-existing defect for a later outcome. The "one deliberate exception" to the untouched-live-path non-goal is removed |
| **AD071** | Two named error classes are added for the derivation failures that had none, and the derivation test asserts the **next action** |
| **AD072** | The quickstart's negative walkthrough is reordered so every review case runs before the destructive steps, each case is labelled with the branch it exercises, and the declared-but-empty case names a kind the qualified configuration declares |
| **AD073** | The run-identifier enumeration is bounded to the most recent twenty with the total when truncated, and the no-runs case gets a stated message and a test |
| **AD074** | AD055's authority is recorded correctly, with a second and narrower ground; two brief passages are named for a planner revision |

## Technical Context

**Language/Version**: Python 3.10–3.13 (`pyproject.toml` `requires-python`), `from __future__ import
annotations` throughout, modern `str | None` unions per Constitution IV.

**Primary Dependencies**: `typer` (CLI, `infrahub_sync/cli.py:31`), `diffsync` 2.x (comparison
engine), `infrahub-sdk` (`InfrahubClientSync`), `pyarrow` + `fsspec` (existing snapshot/plan I/O,
`infrahub_sync/cache/parquet_io.py`), `filelock` (`infrahub_sync/cache/locks.py`), `pydantic` v2.
**No new runtime dependency is introduced** — the artifact uses the standard library `json` and
`hashlib` only.

**Storage**: The existing per-run cache directory, `cache_root_for(<sync name>)/<run_id>/`
(`infrahub_sync/cache/paths.py:26-59`). The new artifact is a `plan/` subdirectory inside it.
`plan.parquet`, `A/`, `B/`, `run.json`, `cursors.json` and `schema-sub-hash.txt` keep their present
meaning.

**Testing**: `pytest` with the existing opt-in `integration` marker (`pyproject.toml:133-135` —
"tests that require a running Infrahub instance. Skipped by default; opt in with `-m integration` and
`INFRAHUB_ADDRESS` + `INFRAHUB_API_TOKEN` set"), following
`tests/integration/test_infrahub_node_to_diffsync_integration.py` as the pattern. Per AD007 no live
Infrahub is reachable in this environment, so every criterion needing a live destination lands there
and every other criterion runs locally against fakes and fixtures. Those live criteria are therefore
**deferred, not produced** (AD045); a local **rendered-mutation** conformance harness against a real node
built from a committed schema fixture (AD054) partially compensates, and both facts are recorded in the evidence map rather than left implicit.

**Target Platform**: CLI on Linux/macOS; the artifact is filesystem-local through `pathlib` (the
fsspec indirection is preserved for the Parquet files it already owns).

**Project Type**: Single Python package + Typer CLI.

**Performance Goals**: None asserted — the spec's Out of Scope explicitly declines plan-volume and
review-latency targets (AD030). The line-oriented `operations.jsonl` encoding is chosen so a large
plan can be summarized without materializing all of it, but no threshold is set or tested.

**Constraints**: no new CLI command group (FR-008, SC-012); no delete reaches the destination and not
executing one is a designed limitation rather than a run failure (FR-016, FR-017, AD055); the shared execution core refactor is not a prerequisite; the qualified path is
NetBox → Infrahub via `examples/netbox_to_infrahub/config.yml`; the artifact format is a shared
contract nine later outcomes consume, so any post-ship change is breaking.

**Scale/Scope**: ~11 new modules under `infrahub_sync/plan/`, ~250 lines added to
`infrahub_sync/adapters/infrahub.py`, ~120 lines to `infrahub_sync/potenda/__init__.py`, ~150 lines to
`infrahub_sync/cli.py`, three docs pages, and roughly 51 new tests of which 7 carry the `integration`
marker (six evidence tests plus their shared fixture). The count moved from 48 by the ratified critique
round: five tasks were added (AD056's disclosure implementation and test, AD059's next-action taxonomy and
its test, AD061's help-text assertion) and one was dropped (AD063's repair of unreachable code).

## Verified facts about the existing code

Every claim this plan relies on was read in the tree at the cited line. Nothing below is inferred.

| # | Claim the plan depends on | Verified at |
|---|---|---|
| V1 | `Potenda.apply_plan` reads `plan.parquet` and dispatches per row to `destination.apply_cached_row(resource, action, source_id, attribute, new_value)`; it never calls `diff_from`/`sync_from`, and it guards a missing surface with `NotImplementedError` naming the adapter class | `infrahub_sync/potenda/__init__.py:341-370` (guard `:354-360`, dispatch `:363-370`) |
| V2 | `_diff_to_rows` emits `dest_id: ""` and `attribute: ""` as literal empty strings and carries only the changed attributes (`get_attrs_diffs()`), never the full payload and never the identity | `infrahub_sync/potenda/__init__.py:297-331` (empty fields `:322-323`, attrs `:314-316`) |
| V3 | `apply_cached_row` has **no adapter implementation anywhere**: the only occurrences in the repository are the engine's own dispatch, one `MagicMock` test double, and a bench print | `infrahub_sync/potenda/__init__.py:344,354,357,361,364`; `tests/cache/test_apply_plan.py:43-44`; `tasks/bench.py:413` |
| V4 | `DiffElement` carries `keys` (the identifiers mapping — the destination identity), `type` (the kind), and `source_attrs` (the full source **attribute** set, not just the delta). The two are disjoint: `source_attrs` is populated from `src_obj.get_attrs()`, whose contract is "Does not include the fields in `_identifiers`", so `source_attrs` carries **no identity field at all** and `keys` is the only source of identity on the element | `.venv/…/diffsync/diff.py:189-196`; `keys` documented "as in DiffSyncModel.get_identifiers()" at `:178`; populated `.venv/…/diffsync/helpers.py:212-219,223`; exclusion contract `.venv/…/diffsync/__init__.py:340-347` |
| V5 | `DiffElement.action` returns `create` / `update` / `delete` / `None` | `.venv/…/diffsync/diff.py:237-254` |
| V6 | Deletes are absent from today's plan because `Potenda` defaults its flags to `SKIP_UNMATCHED_DST`, and diffsync drops destination-only objects from the diff under that flag | `infrahub_sync/potenda/__init__.py:92-93`; `.venv/…/diffsync/helpers.py:191-192` |
| V7 | `write_resource_side` writes `<run_dir>/<side>/<resource>.parquet` and injects `_extract_ts`, `_source_id`, `_tombstone` into every row; `_extract_ts` is `datetime.now(timezone.utc)` allocated per side per run | `infrahub_sync/cache/parquet_io.py:92-142` (injection `:126-128`); `infrahub_sync/potenda/__init__.py:130` |
| V8 | Sidecars are written atomically with tmp+`Path.replace` — the discipline AD014 asks the manifest write to match | `infrahub_sync/cache/sidecars.py:13-24` |
| V9 | Run state vocabulary is `pending \| running \| dry-run \| applied \| failed`, and `previous_successful_run_dir` treats only `applied`/`dry-run` as successful | `infrahub_sync/cache/sidecars.py:71`; `infrahub_sync/cache/incremental.py:24,44` |
| V10 | The Infrahub create path is the convergent upsert — `client.create(...)` then `save(allow_upsert=True)` | `infrahub_sync/adapters/infrahub.py:611-612` |
| V11 | `InfrahubModel.update` opens with `client.get(id=self.local_id, ...)`, and `local_id` is populated only by a destination load, so it is unusable from a saved plan | `infrahub_sync/adapters/infrahub.py:622`; populated `:510`; declared `infrahub_sync/__init__.py:232` |
| V12 | **CORRECTED (AD054).** The only replace-set-*shaped* code is `update_node`'s `compare_lists(existing_peer_ids, new_peer_ids)` then remove `existing_only` / add `new_only` — but it is **not a replace-set**. It reads `attr_manager.peer_ids` at `:151` and only calls `fetch()` at `:168-169`, so on an uninitialized manager it compares the desired set against an **empty** one: `existing_only` comes back empty and it adds without ever removing. The earlier wording ("cardinality-many replace-set exists today in `update_node`") overstated it, and PD-005 / AD038's "the only *verified* replace-set in the tree" inherited the overstatement. Phase E's enforcement therefore issues its own destination read before comparing. **Narrowed by AD070**: that enforcement is new code on the planned-write path, and `update_node` keeps its present ordering — the defect is real and is recorded for a later outcome, because its only caller is the live `sync` write path (`:625`) and correcting it here would make `sync` start removing destination peers | `infrahub_sync/adapters/infrahub.py:149-175` (read `:151`, compare `:166`, fetch `:168-169`, remove `:171-172`, add `:174-175`); sole caller `:625` |
| V12a | **New (AD054).** A relationship manager reports itself initialized from whatever data constructed it — `self.initialized = data is not None` — and `fetch()` returns immediately when it is. So a node built locally from a write payload reports the **desired** peer set as its **existing** one, and a `compare_lists` against it is a guaranteed no-op that can pass only against a mock. This is why the reconciliation must re-read the destination's peer set before comparing. **AD065 draws the consequence the earlier wording missed**: because `fetch()` opens with `if not self.initialized:` (`:286-288`), calling it *first* performs no read either — ordering is not the mechanism. The enforcement must set `initialized` false before calling `fetch()`, or issue its own `client.get(id=node.id, kind=…, include=[<rel>])` and read the manager off that node; and its evidence must be that a destination read was **issued** | `.venv/…/infrahub_sdk/node/relationship.py:264`, `:286-299` (the guard at `:286-288`, the `client.get` inside it at `:290-296`) |
| V12b | **New (AD054).** The **rendered mutation input** is where keyedness is observable, and it is rendered client-side: `_generate_input_data` sets `data["id"] = self.id` if set, else `data["hfid"] = self.hfid` when `exclude_hfid` is false, and the upsert path renders with `exclude_hfid=False`. So an offline harness can assert keyedness without a server — which the assembled `data` cannot show, because a relationship-crossing HFID component is a resolved node-id string by then | `.venv/…/infrahub_sdk/node/node.py:295-298`; upsert render `:1843-1846`; `save(allow_upsert=True)` dispatch `:1533-1535` |
| V13 | Peer resolution today reads the **loaded** SDK node store (`store.get(key=…, kind=…)`), populated during `model_loader` — exactly the dependency a saved-plan apply cannot satisfy | `infrahub_sync/adapters/infrahub.py:57-94` (store reads `:78,81`); store populated `:454`, `:501`, `:613` |
| V14 | A zero-match peer today is dropped with a `logger.warning` and `continue` — the behavior AD016 replaces | `infrahub_sync/adapters/infrahub.py:141-143`, `:212-214`, `:229-231` |
| V15 | The SDK upsert mutation is keyed on `data["id"]` if set, else `data["hfid"]`; `get_human_friendly_id()` returns `None` when the schema declares no `human_friendly_id` or any component path resolves to `None` | `.venv/…/infrahub_sdk/node/node.py:295-298`, `:128-138` |
| V16 | `human_friendly_id: list[str] \| None` is a field on the schema object the adapter already caches wholesale | `.venv/…/infrahub_sdk/schema/main.py:272`; adapter cache `infrahub_sync/adapters/infrahub.py:345` |
| V17 | A multi-match from `client.get` surfaces today as a bare `IndexError("More than 1 node returned")` | `.venv/…/infrahub_sdk/client.py:566` |
| V18 | Tiers come from `compute_tiers`, which excludes self-edges and drops optional (non-identity-bearing) edges to break cycles; a configuration with an explicit `order:` yields `tiers = None` | `infrahub_sync/dependency_graph.py:25-36` (self-edge `:33-34`), `:39-53`, `:81-100`; `infrahub_sync/__init__.py:132-133` |
| V19 | The CLI is a single flat `typer.Typer()` with **no** `add_typer` anywhere, exposing exactly five commands: `list`, `diff`, `sync`, `apply`, `generate` | `infrahub_sync/cli.py:31`, `:77`, `:86`, `:166`, `:295`, `:355` |
| V20 | `diff` already has `--run-id` meaning "re-use a specific cache run id"; its plan output goes through the logger; its whole body is wrapped in `pipeline_lock` with a 60-second default timeout | `infrahub_sync/cli.py:98`, `:153`, `:129`; `infrahub_sync/cache/locks.py:21-33` |
| V21 | `get_potenda_from_instance` creates the run directory unconditionally (`mkdir(parents=True, exist_ok=True)`) and writes `schema-sub-hash.txt` into it **before** any check — which is what would turn a typo'd run id into a valid-looking empty run | `infrahub_sync/utils.py:244-246`, `:256-263` |
| V22 | **CORRECTED — the earlier claim was wrong (AD063).** The `apply` command does write `run.json` with `status: running` at `:322-323`, but the schema-subhash abort it was said to leave `running` behind **cannot execute**. The block imports `infrahub_sync.utils._resolve_infrahub_schema`, which is defined **nowhere in the package** — the only three occurrences in the tree are the comment at `:325` ("Plan 2 will provide _resolve_infrahub_schema"), the import at `:330`, and the call at `:332` — so the import raises `ImportError` and the `except ImportError: pass` at `:341-342` swallows the whole block. The `print_error_and_abort` at `:336-340` is dead code. Consequence: AD010's incidental repair of this path is **dropped** (T060 and its test case go with it), because a test for it could pass only against an injected stub. AD010's run-state rule stands for the **new** refusal paths, which is what DBA-004 measures | `infrahub_sync/cli.py:322-323`, `:325`, `:330`, `:332`, `:336-340`, `:341-342`; `print_error_and_abort` `:72-74`; `grep -rn "_resolve_infrahub_schema" --include='*.py' .` returns only those three lines |
| V23 | `write_plan` is called unconditionally on both the `diff` and `sync` paths and in both `sync_in_tiers` branches | `infrahub_sync/cli.py:152`, `:271`; `infrahub_sync/potenda/__init__.py:462`, `:496-499` |
| V24 | In the tier branch of `sync_in_tiers`, per-tier diffs and per-tier writes are **interleaved**, and the aggregated plan is written only after every write has happened | `infrahub_sync/potenda/__init__.py:480-499` (diff `:484`, accumulate `:485`, sync `:487`, write `:496-499`) |
| V25 | `load_one_side` OR-accumulates `_did_full_extract` across both sides deliberately, so it cannot answer "did the destination run a full extract" | `infrahub_sync/potenda/__init__.py:189-200` (accumulate `:200`, comment `:197-199`), consumed `:430` |
| V26 | The incremental path replays the prior run's snapshot skipping tombstones and layers `list_changed_since` on top — so the destination store is not a live full enumeration | `infrahub_sync/cache/incremental.py:135-170` (tombstone skip `:164-165`); `infrahub_sync/potenda/__init__.py:221-228` |
| V27 | A stored run is located only as `cache_root_for(<sync name>)/<run_id>`, so review is adapter-free but stays configuration-bound | `infrahub_sync/cache/paths.py:26-59` |
| V28 | The `integration` marker exists and is documented as opt-in with `INFRAHUB_ADDRESS` + `INFRAHUB_API_TOKEN` | `pyproject.toml:133-135` |
| V29 | Generated DiffSync models are flat and carry relationships as plain fields alongside attributes, with `_identifiers` / `_attributes` tuples | `infrahub_sync/generator/templates/diffsync_models.j2:29-48`; non-component relationships folded into `_attributes` at `infrahub_sync/generator/__init__.py:78-88` |
| V30 | On the qualified configuration, **ten schema-mapping entries** across nine kinds carry a **relationship inside their identity** (`LocationRack.site`, `DcimDeviceType.manufacturer`, `DcimDevice.location` **twice**, `Interface{Physical,Virtual,Lag}.device`, `IpamVLAN.vlan_group`, `IpamPrefix.vrf`, `IpamIPAddress.vrf`) — so an identity value is sometimes a peer's DiffSync unique-id, not a scalar | `examples/netbox_to_infrahub/config.yml`, `identifiers:` cross-referenced against `fields[].reference`; enumerated in [research.md](./research.md) PD-004 |
| V31 | `DcimDevice` is declared by **two** schema-mapping entries with the same `identifiers: ["location", "name"]` but **different** `location` references — `mapping: rack` / `reference: LocationRack` and `mapping: site` / `reference: LocationSite`, split by complementary `rack` filters — so `SchemaMappingField.reference` does not uniquely determine a peer's kind for that field | `examples/netbox_to_infrahub/config.yml:212`, `:254` |
| V32 | `human_friendly_id` and `uniqueness_constraints` are **both** fields on the same schema object the adapter already caches wholesale, so FR-024's two conditions cost one read each and no new fetch | `.venv/…/infrahub_sdk/schema/main.py:272`, `:274`; adapter cache `infrahub_sync/adapters/infrahub.py:345` |
| V33 | `top_level` is read **only** by the comparison differ, inside `calc_diff`; the synchronizer never reads it and walks the children of whatever `Diff` it is handed. In the tier branch the assignment at `:483` precedes `self.diff()` at `:484`, so the narrowing is a **comparison-time** narrowing, not an execution-time one | `.venv/…/diffsync/helpers.py:79-88`; `.venv/…/diffsync/__init__.py:577-607` (`sync_from` skips `diff_from` when a diff is supplied); `infrahub_sync/potenda/__init__.py:483-487` |
| V34 | `--continue-on-error` is declared on the `sync` command only and is passed to nothing on the `diff` path, so it is unavailable to plan derivation running under `diff` | `infrahub_sync/cli.py:190`, consumed `:234`; `diff_cmd` `:87-165` declares no such option |
| V35 | `update_node`, `resolve_peer_node` and `diffsync_to_infrahub` are **module-level functions**, not methods on `InfrahubAdapter`; the adapter class begins at `:277` | `infrahub_sync/adapters/infrahub.py:57`, `:97`, `:180`, `:277` |
| V36 | Today's convergent create passes identifiers **and** attributes into the payload builder — `diffsync_to_infrahub(ids=ids, attrs=attrs, …)` — which is why its HFID resolves and the upsert is keyed | `infrahub_sync/adapters/infrahub.py:602-604`, upsert at `:611-612` |
| V37 | The comparison store has **no kind-free lookup by unique-id**: `get`, `get_all` and `get_by_uids` all take `model` and select the per-model bucket before the identifier is used, and the only kind-free call, `get_all_model_names()`, enumerates loaded kinds rather than answering for one unique-id. So AD046's "the entry knows its own kind" is not reachable and AD050's bounded probe replaces it | `.venv/…/diffsync/store/__init__.py:40-52`, `:55-63`, `:66-77`; `.venv/…/diffsync/store/local.py:22-28`, `:30-49` |
| V38 | `self.schema` (the wholesale destination schema cache FR-024 reads) exists on the **Infrahub adapter only** — no other adapter in `infrahub_sync/adapters/` defines it — and the repository already reaches for it defensively where it needs it | `infrahub_sync/adapters/infrahub.py:345`; defensive read `infrahub_sync/utils.py:260` (`getattr(dst, "schema", None)`) |
| V39 | The SDK cannot compute a **relationship-crossing** HFID component from a peer supplied as a bare id: `get_path_value` resolves the peer through the SDK client store and returns `None` on a miss (with an in-code comment naming the batch-create case), one `None` component nulls the whole HFID, and a value rendered as `{"id": …}` carries no `__typename`, so the store read is never even attempted. The client store is populated on `save()` and on `get`/`filters` with `populate_store=True` | `.venv/…/infrahub_sdk/node/node.py:100-107`, `:135-139`, `:295-298`, `:744`, `:1549`; `.venv/…/infrahub_sdk/node/related_node.py:54-55`, `:64-68`, `:298-304`; `.venv/…/infrahub_sdk/schema/__init__.py:172-181`; `.venv/…/infrahub_sdk/client.py:911-918`, `:2271-2278` |

Two things this plan deliberately does **not** claim to have verified, because AD007 records that no
live Infrahub is reachable here, are called out at their tasks and routed to `integration`-marked
tests: whether `save(allow_upsert=True)` replaces or merges a cardinality-many relationship set
(neutralized by PD-005 so the answer does not decide correctness), and the exact GraphQL filter
spelling for a nested `<rel>__<attr>__value` lookup (PD-004).

## Constitution Check

*GATE: evaluated before Phase 0 and re-evaluated after Phase 1 design. Result: **PASS**, with one
recorded and justified tension (Principle III).*

| Principle | Verdict | How this plan satisfies it |
|---|---|---|
| **I. Read-only / dry-run by default** | PASS, **with the "safe to run at any time" clause read explicitly** | The new `diff --from-plan` mode is strictly read-only: no adapter constructed, nothing extracted, no lock taken, no run directory created or modified, run state never mutated (FR-008, AD021, AD031). The only new mutating behavior sits inside the already-mutating `apply`, gated behind five checks that must all pass before any write. No new implicit default writes anything. **The reading this plan commits to, stated so it is reviewable**: FR-030 puts new hard failures on `diff` — an unformable identity, an unresolvable source-side peer, an unencodable payload value, a duplicate identifier — and `--continue-on-error` is a `sync`-only option (V34), so there is no tolerance switch on that path. Principle I's "`list`, `diff` and `generate` … MUST stay safe to run at any time" is read as **the command performs no destination mutation**, not as **the command never exits non-zero**. That reading holds here: derivation runs *after* a read-only comparison and writes only inside the run directory. The alternative — degrade to warn-and-skip on `diff` — is rejected, because a silently incomplete plan is precisely the divergence between the reviewed set and the applied set that DBR-016 exists to prevent, and it is worst in the one feature whose product is a plan an operator is asked to trust (AD047). |
| **II. Sync idempotency & safety** | PASS | Convergence is the point: creates and updates both route through the HFID-keyed upsert (FR-013, V10); SC-002/SC-003 measure re-apply and both crash windows; every divergence between the reviewed set and the applied set is **recorded rather than inferred**: an unresolved peer (FR-014) and a destination rejection (AD027) refuse and fail the run, and a recorded delete — which this release does not execute *by design* — is recorded as a count and a set of identifiers with an operator-visible warning naming the count, so the run completes `applied` while the difference stays provably knowable (FR-017, AD055). Partial failure is surfaced with the failing operation identifier, never left ambiguous. |
| **III. Adapter symmetry & pattern consistency** | PASS **with a recorded tension** | The planned-write surface is implemented on the Infrahub adapter only. That is asymmetric across the nine adapters. It is nonetheless the brief's explicit scope ("a destination write surface on the Infrahub adapter"), the asymmetry is already the status quo (V3: zero adapters implement the existing surface), and the engine already fails with a clear, actionable error naming the adapter when the surface is absent (V1, preserved as FR-023). Recorded in [Complexity Tracking](#complexity-tracking). `list`/`diff` pathways remain available on every adapter and everything still flows through `potenda`. |
| **IV. Type safety & explicit contracts** | PASS | Every new module is fully typed with modern unions; the record types are Pydantic models in the existing `SyncConfig` style; a specific exception hierarchy (`PlanArtifactError` + its named subclasses, see [contracts/plan-reader-api.md](./contracts/plan-reader-api.md)) replaces any broad catch, and the base class declares `next_action: str` so a subclass cannot be added without one (AD059). No `[[tool.ty.overrides]]` block is added; `uv run ty check .` must exit 0. Where the SDK's dynamic surface forces it, a targeted `# ty: ignore[<rule>]` with a TODO is used at the call site, matching `infrahub_sync/potenda/__init__.py:69-70`. |
| **V. Test discipline** | PASS **with a recorded evidence gap** | ~48 tests written alongside the change, parametrized for the verification matrix and the negative cases, atomic and single-purpose. Live-destination evidence is opt-in behind the existing `integration` marker (V28) rather than a new mechanism. The gap, stated rather than implied (AD045): five of the brief's own criteria — DBA-001, DBA-002, DBA-003 and DBA-008 in full, plus the live half of DBA-007 — together with the live half of this specification's derived SC-016, have **no passing evidence at merge time**, so the brief's completion condition — inspectable passing evidence for every criterion — is not met. Two things narrow it. First, a local **rendered-mutation** conformance harness — built against a real node from a committed `NodeSchemaAPI` fixture, not a mock (AD054) — asserts that the mutation input the SDK renders carries `id` or `hfid` — for an all-direct HFID as a requirement, for a relationship-crossing HFID as a **strict expected failure** against the recorded risk (AD067) — that the replace-set enforcement **issues a destination read** for the relationship before reading the peer set it compares against (AD065), and that two applies of one operation render **byte-identical** mutation inputs (AD068). That is exactly the class of defect AD042 was, and the class those deferred criteria are the only other check on. Its earlier form asserted the assembled `data` against a wholly mocked SDK, where two of the three assertions cannot fail for the right reason — so it must not be counted as narrowing anything unless it is built in the AD054 shape as sharpened by AD065, AD067 and AD068. Second, the deferral is recorded here, in [the evidence map](#success-criteria-evidence-map) and in `tasks.md`, so it cannot read as covered. |
| **VI. Security, secrets & input boundaries** | PASS | FR-018/SC-010: the artifact carries mapped source field values only; `settings` credentials never enter the artifact or review output; the canary scan asserts it over the artifact files, captured stdout, and the reader's returned data. Refusal messages name expected/found values only where neither is secret (FR-009). `_require_safe_segment` already guards `--run-id` traversal (`infrahub_sync/cache/paths.py:11-23`) and the review path reuses it. |
| **VII. Simplicity & maintainability** | PASS | No new runtime dependency (stdlib `json` + `hashlib`). One reader entry point, not a plan API (FR-029, AD029). No second apply path — the v1 dispatch is replaced, not paralleled (FR-019). Generated code untouched. The `infrahub_sync/plan/` package is split by responsibility rather than speculatively: each module has a real caller in this change, and the format itself is consumed by nine later outcomes. |
| **Workflow: logging** | PASS | Module loggers everywhere except the one place FR-008 mandates stdout, which uses `typer.echo` (AD032) — the builtin `print` is never used, which is what the repository's rule bans, and the CLI already echoes help this way (`infrahub_sync/cli.py:69`). |
| **Workflow: documentation** | PASS | New CLI flags and a new artifact layout are user-visible, so `docs/docs/reference/cache-layout.mdx`, `docs/docs/reference/cli.mdx` (regenerated via `uv run invoke docs.generate`) and `docs/docs/running-a-sync.mdx` are updated in the same change (Phase G); AD036 records the documentation obligation explicitly. |

**Post-design re-evaluation**: no gate moved. Phase 1 design added no dependency, no command group, no
configuration surface, and no broad exception handler. The single Principle III tension is unchanged
and remains justified below.

## Project Structure

### Documentation (this feature)

```text
dev/specs/001-plan-artifact-saved-apply/
├── spec.md                                  # input (AD001–AD053 settled)
├── plan.md                                  # this file
├── research.md                              # Phase 0 — PD-001..PD-010, the details AD001–AD036 left open
├── data-model.md                            # Phase 1 — entities, fields, validation, state
├── contracts/
│   ├── plan-artifact-format.md              # the shared contract nine outcomes consume
│   ├── plan-reader-api.md                   # FR-029's in-process entry point + error taxonomy
│   ├── cli-review-mode.md                   # `diff --from-plan` surface, exit codes, messages
│   └── destination-write-surface.md         # the adapter method + peer-resolution contract
├── quickstart.md                            # Phase 1 — runnable validation, local and integration
├── checklists/                              # pre-existing evaluation artifacts
└── tasks.md                                 # Phase 2 output — NOT created here
```

### Source Code (repository root)

```text
infrahub_sync/
├── plan/                                    # NEW package — the artifact, its reader, its review
│   ├── __init__.py                          # re-exports the reader entry point + public types
│   ├── canonical.py                         # canonical JSON bytes, sha256 helpers, value normalization
│   ├── models.py                            # PlanManifest, PlannedOperation, RelationshipReference, SourceSnapshotRecord
│   ├── identity.py                          # canonical destination identity + operation-id derivation
│   ├── checksum.py                          # plan_checksum over manifest ⧺ operations
│   ├── config_version.py                    # the default configuration-version rule (FR-011)
│   ├── derive.py                            # Diff → operations, delete derivation, tiers, HFID warning
│   ├── writer.py                            # ops-first / manifest-last atomic artifact write
│   ├── reader.py                            # artifact load, v1 / torn / version detection, error taxonomy
│   ├── verify.py                            # the five ordered pre-apply checks (FR-009)
│   ├── review.py                            # FR-029 single entry point: summary + per-object detail
│   └── errors.py                            # PlanArtifactError hierarchy
├── potenda/__init__.py                      # MODIFIED — per-side extract flag, artifact write, saved-plan apply, tier reordering
├── adapters/infrahub.py                     # MODIFIED — apply_planned_operation, PeerResolver, replace-set helper
├── cli.py                                   # MODIFIED — diff --from-plan/--detail/--kind, apply rewiring, run-state fix
└── cache/                                   # UNCHANGED — paths, sidecars, parquet_io, locks, incremental

tests/
├── plan/                                    # NEW — unit coverage for every module above
│   ├── test_canonical.py, test_identity.py, test_checksum.py, test_config_version.py
│   ├── test_writer.py, test_reader.py, test_verify.py, test_review.py
│   ├── test_derive.py
│   └── test_apply_conformance.py            # NEW — rendered-mutation conformance, real node over
│                                            #       a committed schema fixture (AD045, AD054)
├── cache/test_apply_plan.py                 # REWRITTEN — the v1 dispatch it asserts is removed
├── test_cli_plan_review.py                  # NEW — CLI review mode, no-group assertion, error cases
├── test_potenda_plan_artifact.py            # NEW — engine wiring, tier ordering, delete derivation
├── adapters/test_infrahub_planned_write.py  # NEW — write surface + peer resolution against a mocked SDK
└── integration/
    └── test_saved_plan_apply_integration.py # NEW — every criterion needing a live destination

docs/docs/
├── reference/cache-layout.mdx               # MODIFIED — the plan/ directory and its two files
├── reference/cli.mdx                        # REGENERATED — new diff flags
└── running-a-sync.mdx                       # MODIFIED — review-then-apply workflow
```

**Structure Decision**: a single new package, `infrahub_sync/plan/`, sitting beside
`infrahub_sync/cache/` and owned by the engine rather than by any adapter. This mirrors the existing
split — `cache/` owns run-directory I/O, `potenda/` orchestrates, `adapters/` talk to systems — and
keeps the format (a contract nine outcomes consume) out of both the CLI and the adapters, so a future
consumer imports `infrahub_sync.plan` and nothing else. No new top-level directory, no `src/` layout
change, no new test root beyond `tests/plan/`.

## Implementation phases

Seven phases, ordered so each is independently reviewable. A–C are pure additions with no behavior
change and each ends fully green; D changes what a plan run writes; E adds the write surface; F
changes the CLI; G closes documentation and fixtures.

**Which phases end green, stated precisely so the two documents agree.** S, A, B and C end fully
green — nothing existing has changed. **D does not**: it is the first phase that changes what a plan
run writes, and the existing fixtures it makes stale are swept in G (T067), so at Checkpoint D the tree
is green only for the suites named in T035's Done-when. **E ends green** — the one test the dispatch
removal invalidates is rewritten inside E, immediately after the removal, rather than deferred to G.
F and G each end green. G is not optional: the brief requires the stale fixtures and documentation to
be updated in the same change as the delete-recording behavior.

### Phase A — Canonical encoding, identity, checksum, configuration version

**Delivers**: FR-003, FR-005, FR-011, FR-028.3, and the checksum half of FR-004.

New modules `infrahub_sync/plan/canonical.py`, `identity.py`, `checksum.py`, `config_version.py`,
`models.py`, `errors.py`.

- `canonical_json_bytes(value) -> bytes` — `json.dumps` with `sort_keys=True`,
  `separators=(",", ":")`, `ensure_ascii=False`, UTF-8 encoded. One newline-free encoding for the
  manifest and one per operations line; the file uses LF only (AD001). Non-JSON-native scalars are
  normalized beforehand by `canonical_value` per PD-002, rather than by a `default=` hook, so the
  normalization is inspectable and testable on its own — today's code reaches for
  `json.dumps(..., default=str)` at `infrahub_sync/potenda/__init__.py:324-325`, which is not
  determinism-safe for an artifact.
- `canonical_identity(mapping) -> dict[str, Any]` — key-sorted mapping of identity attribute name to
  value, the single representation FR-028.3 fixes; used by the identifier hash, by
  relationship-reference ordering, and by review output.
- `operation_id(action, kind, identity) -> str` — `"op_" + sha256(canonical_json_bytes([action, kind,
  canonical_identity(identity)])).hexdigest()[:16]`. The triple is encoded as a JSON **array**, fixed
  by PD-001. Payload deliberately excluded (AD002).
- `compute_plan_checksum(manifest_without_excluded, operations_bytes) -> str` — sha256 over
  `canonical_json_bytes(manifest_minus_three_fields) + operations_bytes`, **no separator**, the three
  fields *removed* rather than blanked (AD035). Lowercase hex, no prefix.
- `default_config_version(config) -> str` — sha256 over the canonical JSON of the parsed configuration
  excluding `directory`, per PD-003. Caller-supplied values are validated as non-empty printable ASCII
  and stored verbatim.
- Pydantic record types in `models.py` mirroring [data-model.md](./data-model.md), with
  `PLAN_FORMAT_VERSION = 2` and `SUPPORTED_FORMAT_VERSIONS = frozenset({2})`.

**Tests (all local)**: identifier stability across re-derivation and insensitivity to payload;
identifier changes when any of action/kind/identity changes; canonical encoding byte-stability under
dict reordering; list-valued payload attributes preserved in source order and **not** re-sorted
(FR-005); the checksum excludes exactly three fields; configuration-version determinism across two
loads of the same file, opacity (an arbitrary printable-ASCII string round-trips), and rejection of an
empty or non-printable supplied value.

### Phase B — Artifact writer

**Delivers**: FR-004, FR-010's count field, FR-019's write-order clause, FR-021, FR-022, FR-026,
FR-027.

New module `infrahub_sync/plan/writer.py`:

```python
def write_plan_artifact(
    *, run_dir: Path, run_id: str, config_version: str,
    source_snapshot: list[SourceSnapshotRecord], deletes_computed: bool,
    operations: Sequence[PlannedOperation],
) -> PlanManifest
```

- Sorts operations by `(tier, operation_id)` (AD001) and asserts identifier uniqueness, raising
  `DuplicateOperationIdError` and failing the plan run (FR-021).
- Writes `<run_dir>/plan/operations.jsonl` **first**, then `<run_dir>/plan/manifest.json` **last**,
  each via tmp+`replace`, reusing the discipline at `infrahub_sync/cache/sidecars.py:13-24` (V8). The
  manifest's presence is the commit point (AD014), which is what makes the v1 and torn verdicts
  disjoint by construction rather than by heuristic.
- Zero operations produce a present, empty `operations.jsonl` and `operations_count: 0` (FR-022).

**Tests (all local)**: write order observable via a monkeypatched writer; manifest-last atomicity;
ordering by tier then identifier; a duplicate identifier raises and leaves no manifest; an empty plan
writes a zero-length operations file and a count of 0; the manifest carries exactly the eight FR-027
fields and nothing that groups operations (FR-026); re-writing identical content yields byte-identical
files.

### Phase C — Reader, verifier, review

**Delivers**: FR-006, FR-007, FR-009, FR-010, FR-019's detection rule, FR-027's version refusal and
unknown-field tolerance, FR-029.

New modules `infrahub_sync/plan/reader.py`, `verify.py`, `review.py`.

- `reader.load_plan_artifact(run_dir) -> LoadedPlan` classifies **before** parsing: `plan/` absent →
  `PlanFormatV1Error` (the re-plan message, FR-019); `plan/` present without a complete manifest →
  `PlanArtifactTornError`; an unreadable path → `PlanArtifactUnreadableError` naming the path
  (AD036); `format_version` not in `SUPPORTED_FORMAT_VERSIONS` → `PlanFormatVersionError` naming the
  version found and the versions supported, textually distinct from the v1 message (FR-027, SC-018).
  Unknown additional manifest fields are preserved verbatim so they stay inside the checksum
  (FR-027) — `model_config = ConfigDict(extra="allow")`.
- `verify.verify_plan(...) -> list[VerificationFailure]` runs the five FR-009 checks in the stated
  order: format version → run-identifier equality → plan checksum → source-snapshot binding →
  configuration version. **All** are evaluated and **every** failure named, except that an
  unrecognized format version short-circuits the rest, since an artifact whose revision the reader
  does not understand cannot have its remaining fields meaningfully interpreted (PD-006). The
  operations-file integrity test (absent, or a line count disagreeing with `operations_count`) is
  evaluated with the checksum check and reported as torn rather than as a checksum mismatch, because a
  checksum cannot be computed over bytes that are not there. Each failure carries the run identifier
  it refused, the check name, the expected and found values where neither is secret, and the
  operator's next action (AD036).
- `review.read_saved_plan(*, sync_name, run_id, config=None) -> SavedPlan` is FR-029's single entry
  point. It returns **data**: `.manifest`, `.summary()`, `.operations(kind=None)`, `.checksum_ok`,
  `.verification_notes`. It never writes to a stream, never
  mutates run state, and renders a plan that would fail verification rather than refusing (AD031).
  `.summary()` carries a count per action and a count per kind **plus two disclosure fields (AD056)**:
  `delete_operations_computed`, read up from the manifest, and `deletes_not_executed`, the plan's delete
  count. Both are derived on read, so the artifact format and `plan_checksum` are untouched. They are
  mandatory rather than convenient: without the first, a plan whose whole delete class was omitted is
  indistinguishable from a plan that has no deletes, and FR-015's "explicit and reviewable" claim is
  carried by nothing. **`operations(kind=…)` returns `[]`** for a kind the configuration declares and the
  plan has no operation for, and raises `UnknownPlanKindError` **only** for a kind the configuration does
  not declare (AD058) — the never-empty rule is FR-006's presentation obligation and is discharged by the
  Phase F renderer, because forcing a programmatic caller to catch an exception to learn a count is the
  presentation rule leaking into the interface FR-029 requires callers to consume as data.
- Every error the reader and verifier raise carries a **next action** (AD059), and where the raising site
  already holds an enumeration the message lists it: `SUPPORTED_FORMAT_VERSIONS` for a version refusal,
  `ACTIONS` for an unrecognized action, the plan's kinds for an unknown kind, the existing run identifiers
  for an unknown run. `PlanArtifactError` declares `next_action: str` on the base class, so a subclass
  cannot be added without one. `UnsupportedOperationActionError` joins the taxonomy: an operation record
  whose `action` is outside `ACTIONS` is refused **here**, while the artifact is being read and before any
  destination write, which is where FR-017 needs the genuinely-unsupported case caught (AD055). Because the
  same reader serves review, **review refuses such a plan too** — stated and tested rather than discovered,
  and reconciled with AD031's "review renders rather than refusing", which is scoped to *verification*
  failures: a plan whose operation vocabulary this release cannot interpret cannot be honestly summarized
  either.
  Two further classes join the taxonomy for FR-030's derivation failures that had none (AD071):
  `UnformableDestinationIdentityError`, for an operation whose destination identity cannot be formed, whose
  next action points at the schema mapping's `identifiers` for that kind; and `SourcePeerUnresolvedError`,
  for a relationship peer absent from the **loaded source store**, whose next action is to load the peer's
  kind or drop the relationship from the mapping. The second exists because `PeerNotFoundError` is defined
  as a **destination** miss with a destination remedy — "create the peer at the destination" fixes nothing
  when the destination is not what is missing — and reusing it would attach the wrong route to the
  condition. Both are needed structurally as well as editorially: the taxonomy sweep walks declared entries,
  so a condition with no entry is never swept, and an unnamed condition invites a bare exception that
  bypasses the base class's `next_action` guarantee altogether.
  The unknown-run enumeration is bounded and guarded per AD073, described under Phase F.

**Tests (all local)**: SC-004's six negative cases plus SC-011, SC-015 and SC-018 as fixture-driven
parametrized cases; a v1 fixture (a run directory with `plan.parquet` and no `plan/`) rejects with the
re-plan message; torn fixtures (missing operations file; count mismatch; absent snapshot; truncated
snapshot) reject on the torn path; unknown-field tolerance; the version message differs textually from
the v1 message; summary and detail read from a stored artifact in a subprocess (FR-007, SC-009's
in-process cases); a kind-filter miss raises.

### Phase D — Plan derivation and engine wiring

**Delivers**: FR-001, FR-002, the plan side of FR-012, FR-015, FR-024, FR-028's obligation levels.

New module `infrahub_sync/plan/derive.py`, plus edits to `infrahub_sync/potenda/__init__.py`:

- `operations_from_diff(diff, *, config, tier_of, source_adapter)` walks `diff.children` exactly as
  `_diff_to_rows` does (V2) but keeps what it discards: `element.keys` becomes the destination
  identity, `element.type` the kind, `element.action` the action (V5), and the payload is
  **`element.keys ∪ element.source_attrs`** (AD042). The union is not a convenience: `source_attrs`
  comes from `get_attrs()`, which by its own contract excludes `_identifiers` (V4), and the generator
  strips identifiers out of `_attributes` (`infrahub_sync/generator/__init__.py:95`), so
  `source_attrs` alone carries **no identity field**, the destination HFID cannot be formed, the
  upsert is unkeyed and every re-apply duplicates — SC-002 and SC-003 unachievable. Today's create
  path converges only because it passes both (V36). Elements whose action is `delete` are **skipped
  here** so deletes come from one source only (FR-015) — which also keeps the derivation correct for a
  project that has cleared `SKIP_UNMATCHED_DST` (V6).
- Payload/relationship split: a payload key whose `SchemaMappingField.reference` is set becomes a
  relationship reference, not a payload field — **including when that key is an identity component**,
  in which case it appears in `identity` and in `relationships` but not in `payload`. The reference
  records the peer's identity mapping, obtained by looking the peer up in the loaded **source** store
  by its DiffSync unique-id and calling `get_identifiers()` — never by splitting the unique-id on
  `__`, which is the v1 flaw the brief names — and the peer's kind **probed** from the store rather
  than read from the mapping's `reference` value, because `DcimDevice` is declared twice with
  different `location` references (V31, AD046) and a wrong pick fails the whole apply run on the
  qualified path. The probe is bounded (AD050, V37): "the entry knows its own kind" is not reachable,
  since the store requires a model on every read and offers no kind-free lookup by unique-id, so the
  candidates are the kinds the mapping declares as that field's `reference` across every entry for the
  owning kind, each probed with `store.get(model=candidate, identifier=uid)`. One hit answers; **zero
  and more than one both fail the command** naming the owning kind, the field, the unique-id and the
  candidates tried (FR-030), with **no fallback to the mapping-declared kind even for a one-candidate
  set** — an unprobed single candidate is the mapping-derived answer AD046 forbids. A peer identity
  component that is itself a reference recurses into a nested
  `{peer_kind, identity}` pair rather than a unique-id string (AD043); ten mapping entries across nine
  kinds on the qualified path hit that case (V30). Cardinality-many references are a list ordered canonically by
  peer identity (AD003). Absent versus empty is honored strictly (FR-028.2). Any derivation failure
  here — unresolvable peer, unformable identity, unencodable value — **fails the command**, on `diff`
  as on `sync`; there is no tolerance option on the `diff` path (V34, FR-030, AD047).
- `tier_of(kind)`: the index of the tier set containing the kind (`Potenda.tiers`, V18); when tiers are
  absent because the configuration declares `order:`, the tier is the kind's index in `top_level`
  (PD-007) — the field stays required and deterministic, and AD022's qualification of what a tier
  *guarantees* is unaffected.
- `derive_deletes(...)`: for each kind, destination-store identities minus source-store identities,
  enumerated through `adapter.get_all(kind)` and `get_identifiers()` — the same enumeration
  `_write_side_snapshot` already performs (V7). Derived **only** when the destination side ran a full
  extract. Because `_did_full_extract` is OR-accumulated across both sides deliberately (V25), a new
  per-side `self._side_full_extract: dict[str, bool]` is recorded in `load_one_side` alongside it; the
  existing flag is untouched so `persist_baseline_counts` (`:430`) is unchanged. When the destination
  side was incremental, no delete is derived and the manifest records
  `delete_operations_computed: false` (FR-015, SC-017).
- Deletes carry no payload (FR-028.1) and never enter the diff the write path consumes — which is what
  makes FR-016 structural rather than configuration-dependent. Their **identities**, however, go through
  the same canonicalisation as every other operation's, recursive nested `{peer_kind, identity}` pairs
  included, with the AD050 probe run against the **destination** store instead of the source store
  (AD049). Deletes are not exempt from the recursive rule: a delete's peers are destination-only by
  construction — that is what makes it a delete — so the source-side wording of AD046 has nothing to
  resolve against, which is a reason to change the store, not to change the rule. Nine kinds on the
  qualified path carry a reference inside their identifiers (V30), so this is the ordinary delete case
  there.
- `warn_missing_convergence_key(...)`: for each destination kind with an operation, read the cached
  destination schema and warn on the log stream in **either** of two conditions (FR-024, AD044) —
  `human_friendly_id` absent or not fully supplied by the plan's identity (V16), **or**
  `uniqueness_constraints` declaring no constraint covering the plan's identity attributes. Both are
  fields on the same cached object (V32), so the second costs one more read. The second condition is
  the brief's own and is checked in its own right: a kind can carry a complete HFID and still
  duplicate silently for want of a uniqueness constraint. Warning only — never a manifest field, so it
  stays outside the checksum and outside SC-006. **Guarded on the destination exposing a schema at all
  (AD052, V38)**: `self.schema` exists on the Infrahub adapter and on no other, and derivation now runs
  on the `diff` path for *every* destination with failures fatal (AD047), so an unguarded read would be
  an `AttributeError` on eight adapters that compare fine today. Where no schema is exposed the whole
  warning is skipped and that is never an error — `getattr(dst, "schema", None)` is how the repository
  already reaches for it (V38). A regression test asserts `diff` against a non-Infrahub destination
  still succeeds; the coupling itself is recorded in [Complexity Tracking](#complexity-tracking).
- `Potenda.write_plan_artifact(...)` composes the above, computes the source-snapshot binding per
  PD-008, and calls the Phase B writer. It is invoked from the `diff` path, the serial `sync` path, and
  — after the reordering below — the tier `sync` path.
- **Tier-mode reordering**: today the tier branch interleaves per-tier diff and per-tier sync and writes
  the plan only at the end (V24), which cannot satisfy FR-001's "before anything is written". The branch
  is restructured to compute every tier's diff first, write the artifact, then execute the stored diffs
  tier by tier through the existing `sync(diff=...)` signature
  (`infrahub_sync/potenda/__init__.py:292-295`). Recorded as PD-009 / AD039. **The `top_level`
  narrowing goes in the compute loop, not the execution loop**: it is read only by the comparison
  differ and never by the synchronizer, and today it is assigned immediately before the `diff()` call
  (V33). The compute loop therefore sets it around each `diff()` exactly as the interleaved loop does,
  and the execution loop replays the retained diffs with `top_level` restored, where it is irrelevant.
  Putting it in the execution loop instead would compute six identical full-destination diffs rather
  than six disjoint per-tier ones and record every operation once per tier — which is why the
  regression assertion is on per-tier diff **contents**, not only on call order.
- `plan.parquet` keeps being written unchanged everywhere it is written today (V23) and is never read by
  the new path (FR-019, AD014).

**Tests (all local, following the fake-diff pattern at `tests/cache/test_plan_serialization.py:49-72`)**:
identity and full payload survive into the record; relationship references carry peer kind and peer
identity and are canonically ordered; a delete in the diff is not double-recorded; deletes derived by
set difference appear with identifiers; no delete is derived and the manifest says so when the
destination side was incremental (SC-017); tier assignment with and without `order:`; the HFID warning
fires and the run still succeeds (SC-014); the tier path writes the artifact before the first
`sync_from` call (asserted on call order); SC-006 byte-identity across two derivations of identical
input.

### Phase E — Destination write surface and apply-time peer resolution

**Delivers**: FR-013, FR-014, the apply side of FR-012, FR-017, FR-020, FR-023, FR-025.

Edits to `infrahub_sync/adapters/infrahub.py`:

```python
def apply_planned_operation(self, *, operation: PlannedOperation, peers: PeerResolver) -> str
```

- `delete` raises `SkippedDeleteOperation`. The engine collects these rather than stopping,
  because SC-007 requires every non-delete operation to still be applied — and the run then ends
  **`applied`**, not `failed` (AD055). Not executing a delete is a designed limitation of this release, so
  a run that behaves as designed is not reported as broken. The engine records
  `summary["skipped_delete_count"]` and `summary["skipped_delete_operations"]` and emits an
  operator-visible warning naming the count. The class that *does* still fail the run is an operation whose
  action is outside `ACTIONS`, refused at load in Phase C before any write.
- `create` and `update` both build `data` from the payload plus resolved relationship peer ids, run it
  through the existing `client.schema.generate_payload_create(...)` for source/owner/protection parity
  (`infrahub_sync/adapters/infrahub.py:608-610`), then `client.create(...)` + `save(allow_upsert=True)`
  (V10) — never `InfrahubModel.update`, which is unusable without a destination load (V11). Because
  the payload carries the identity components (AD042), the HFID resolves and the upsert is keyed, the
  same way today's create path is keyed (V36). **Two checks sit between building `data` and issuing the
  write, and they are not the same check (AD066).** The first is the **diagnostic**: every component path of
  the destination kind's `human_friendly_id` must be *accounted for* in what the apply holds, or the write
  raises naming the kind and the missing component. It is the only form that can say *which* component is
  missing, and it is the apply-time counterpart of FR-024's plan-time warning. The second is the
  **keyedness gate**, and it exists because keyedness is a property of the **rendered mutation input**, not
  of `data` (V12b): read `node._generate_input_data(exclude_hfid=False)["data"]` — the same render the
  upsert path performs — and branch on the destination kind's HFID shape. Where every component is
  **direct**, a render carrying neither `id` nor `hfid` can only mean the payload lost its identity
  components, which is the AD042 defect class, so it **raises**. Where a component **crosses a
  relationship**, the render carries neither today for a reason this outcome does not control (V39), so it
  emits **one operator-visible warning per destination kind** naming the recorded risk and proceeds: the
  convergent write may still key server-side, and refusing would withdraw the ten identity-bearing-reference
  mapping entries of the qualified path (V30) from what this outcome delivers, which is the
  relationship-bearing capability DBR-013 and DBA-008 require. **The flat claim "an unkeyed write is never
  issued" is therefore struck** — what holds is narrower and is what these two checks deliver: no write is
  issued whose payload is missing an HFID component, and no render is issued unkeyed where being unkeyed can
  only be a defect.
  "Accounted for" is defined per component shape (AD051), because "resolves against `data`" is not
  implementable: by the time the assertion runs, a relationship key in `data` holds a resolved node-id
  string and no attribute can be read out of it. A **direct** component (`<attr>` / `<attr>__value`)
  must be present and non-null in `data`. A **relationship-crossing** component
  (`<rel>__<attr>__value`) requires the `<rel>` key present and non-null in `data` **and** the
  operation's nested `{peer_kind, identity}` for `<rel>` to supply `<attr>`. Both arms fail for the
  cases the assertion exists to catch. The dependency this leaves on the SDK client store for
  server-side HFID formation is verified (V39) and carried as a [risk](#risks) with this assertion as
  its detector, not assumed away — the contract records it in full.
- Cardinality-many relationships are then reconciled as an explicit **replace-set** against the saved
  node, by a **new** `_replace_relationship_set(node, rel_name, peer_ids)` on the planned-write path. This
  is PD-005: it
  makes the semantics deterministic regardless of whether the upsert mutation itself replaces or merges
  a relationship list, which cannot be verified without a live server (AD007). **It must re-read the
  destination's peer set before comparing (AD054).** The pre-existing code reads `attr_manager.peer_ids` at
  `:151` and only calls `fetch()` at
  `:168-169` (corrected V12), and a relationship manager reports itself initialized from whatever data
  built it, with `fetch()` a no-op once it is (V12a). So on a node built locally from the write payload,
  `peer_ids` *is* the desired set: the comparison finds nothing to remove and the reconciliation removes
  nothing — a guaranteed no-op that can pass only against a mock.
  **The re-read is a mechanism, not an ordering (AD065).** `fetch()` carries its own
  `if not self.initialized:` guard, so calling it first changes nothing on a locally built node. The helper
  must force the manager cold — set `initialized` false, then `fetch()`, then read `peer_ids` — or issue its
  own scoped `client.get(id=node.id, kind=…, include=[rel_name])` and read the manager off the returned node.
  Either is acceptable; "fetch first" is not, and the test asserts that a destination **read was issued** for
  that relationship before the peer set was read.
  **It does not touch `update_node` (AD070).** The pre-existing additive ordering there is a real defect and
  it stays: `update_node`'s only caller is `InfrahubModel.update` (`:625`), the live `sync` write path, so
  correcting it would make the existing `sync` command start removing destination relationship peers absent
  from the source on configurations that have never removed one — a data-removing change to an existing
  command with no requirement, criterion, edge case or documentation entry behind it, in an outcome whose
  out-of-scope list is explicit. It is recorded as a **pre-existing defect for a later outcome to own**. The
  cost is that the compare-and-reconcile shape appears twice, roughly eight lines; that duplication is
  deliberate and is what makes "the live write path is unchanged" literally true. PD-005's earlier rejection
  of exactly this option, on the grounds that a shared helper cannot be a replace-set on one caller and
  additive on the other, is overruled here on scope: the engineering preference was sound and the authority
  to act on it is not this outcome's.
- `PeerResolver` (new, in the adapter): a memoized `(kind, canonical identity) -> destination node id`
  map. Populated from each completed create/update; on a miss it queries the destination by building
  filter kwargs from the destination kind's `human_friendly_id` component paths (V16), sourcing each
  path from the peer identity and — for a `rel__attr__value` path — from the **nested
  `{peer_kind, identity}` pair** the peer identity records under `rel` (AD043), recursively, so no data
  value is ever split on `__` (PD-004). Zero results → `PeerNotFoundError` naming peer kind, peer
  identity and the referring operation identifier; more than one → `PeerAmbiguousError` naming peer
  kind, peer identity and the match count (FR-014, SC-016). Failed lookups and failed writes are
  **never** memoized (FR-014, AD036). **Scope (AD048)**: these refusals apply to this new resolver
  only. The live `sync` write path's warn-and-continue (V14) and the SDK's bare `IndexError` (V17) are
  **left exactly as they are** — existing behavior on an existing path the brief does not authorize
  touching — and a test asserts they still hold.

Edits to `infrahub_sync/potenda/__init__.py`:

- `apply_plan()` is **replaced**, not paralleled: it loads the new artifact, checks the write surface
  before any write (FR-023, keeping today's `NotImplementedError` shape at `:354-360`, and passing the
  **adapter's name** to `verify_plan` rather than a boolean, since the message names the adapter — AD058),
  executes operations in stored order, and **returns** the apply outcome as a record carrying three named
  values (AD062, AD069): the ordered applied-operation identifiers (FR-020, whose final element is FR-025's
  last-applied pointer), the skipped-delete identifiers and their count (FR-017, AD055).
  **One writer owns `run.json`, and it is the CLI (AD069).** `apply_plan` writes no run file. The CLI, which
  already constructs a `RunFile` at `status: running` (`infrahub_sync/cli.py:322-323`) and saves it again
  after the apply returns (`:350-351`), **merges** the returned record into `run_file.summary` before that
  save. This is not a stylistic choice: `RunFile.save()` writes the whole payload from the in-memory instance
  with no merge (`infrahub_sync/cache/sidecars.py:87-89`) and the CLI's instance is constructed with an empty
  `summary` and never reloaded, so an engine that wrote the keys itself would have every one of them deleted
  by the CLI's later save — silently destroying FR-020's record, with the tests that would catch it two
  phases downstream. A mid-apply rejection carries its **partial** record on the raised error so the CLI can
  merge what was written before recording `failed`; without that, FR-025's last-applied pointer could not
  survive a partial apply at all. The three values land under
  `summary["applied_operations"]`, `summary["skipped_delete_operations"]` and
  `summary["skipped_delete_count"]`; `summary`
  is already `dict[str, Any]` inside the closed `RunFile.KEYS` tuple
  (`infrahub_sync/cache/sidecars.py:73`, `:76`), so nothing in the persisted schema changes and the
  `cache/` layer stays unchanged as this plan declares. Deletes are collected rather than stopping the
  loop, and the run ends **`applied`** with a `logging.WARNING` naming the skipped count and a completion
  line that names it too — **not
  `failed`** (AD055). A destination rejection or transport failure stops
  at that operation, keeps what was written, hands back the partial record, and fails naming the
  operation identifier, the
  underlying error and the next action (AD027, AD059). The v1 `apply_cached_row` dispatch is removed — leaving it wired would be
  exactly the second apply path FR-019 forbids, and it has zero implementations to break (V3). Recorded
  as PD-010 / AD040. **The one test double that asserts the removed dispatch
  (`tests/cache/test_apply_plan.py:43-44`) is rewritten inside this phase, immediately after the
  removal**, not deferred: the removal task's done-condition is a passing suite, and a rewrite two
  phases later would leave that condition unsatisfiable in between.

**Tests**: local unit tests against a mocked `InfrahubClientSync` covering payload construction, upsert
invocation, replace-set reconciliation **and its re-read**, the memo's population and its refusal to cache
negatives, both peer-resolution refusals with their next actions, a delete collected and skipped with the
run ending `applied`, an unrecognized action refused at load with the run ending `failed`, the
missing-surface error, the ordered applied set and the skipped-delete record, and
fail-fast on rejection. Plus the **rendered-mutation conformance harness** (AD045a, rebuilt by AD054) in
`tests/plan/test_apply_conformance.py`: it constructs a real `InfrahubNodeSync` from a **committed
`NodeSchemaAPI` fixture** and asserts the **rendered mutation input** carries `id` or `hfid`
(`.venv/…/infrahub_sdk/node/node.py:295-298`, `:1843-1846`) — required for a kind whose HFID is all-direct,
and a **strict expected failure** for a kind whose HFID crosses a relationship, which cannot render keyed
today (V39, AD067) — that the reconciliation **issued a destination read** for the relationship before
reading the peer set it compares against (AD065), and that two applies of one operation render
**byte-identical** mutation inputs (AD068) — the
offline check for the class of defect AD042 was. Its earlier form asserted the assembled `data` against a
wholly mocked SDK, which makes two of the three assertions unfalsifiable: `data` cannot show keyedness
(a relationship-crossing component is a resolved id string by then), and a mock holds no destination state
against which "no second create" or "the peer set was replaced" could fail. Live evidence (SC-001, SC-002,
SC-003, SC-007, SC-008, SC-016) goes to
`tests/integration/test_saved_plan_apply_integration.py` behind the `integration` marker and is
**deferred, not produced** (AD045b).

### Phase F — CLI review mode and apply rewiring

**Delivers**: FR-008, FR-009's run-state obligations, SC-009's CLI cases, SC-012.

Edits to `infrahub_sync/cli.py`:

- `diff` gains **`--from-plan <run-id>`** (str, AD057), `--detail` (bool) and `--kind` (str). The review
  option **takes the run identifier as its value** rather than being a bare flag paired with the existing
  `--run-id`: that pairing gives one option two inverse meanings — a write target whose unknown value is
  silently created and whose stored plan is overwritten (V21) without the flag, a read source that errors
  with it — behind a discriminator the operator can omit, so a single omission turns a read into a
  destructive write against the artifact being read. Folding the identifier in removes the overload, and
  the "`--from-plan` with no `--run-id`" error case ceases to exist. In review mode the
  command branches **before** `pipeline_lock` (V20) and before `get_potenda_from_instance` (V21), so no
  lock is taken, no adapter is constructed, and no run directory is created. It resolves the run via
  `cache_root_for(name)/<run_id>` (V27), calls `read_saved_plan`, and renders through `typer.echo`
  (AD032). An unknown run or a run with no
  artifact errors naming the run identifier, the expected artifact path, **the run identifiers that do
  exist**, and the next action; an unreadable path errors naming the path and the next action (AD059).
  **That enumeration is bounded and guarded (AD073)**: the most recent twenty identifiers, with the total
  stated when it truncates — they sort by time by construction (`infrahub_sync/cache/paths.py:46-52`) and
  nothing in the repository ever prunes a run directory, so an hourly pipeline would otherwise turn the
  commonest typo in this feature into thousands of lines. And because `cache_root_for` computes a path
  without creating or checking it (`:26-43`), the absent-or-empty case is handled explicitly rather than
  raising: the message says plainly that this sync has no stored runs and its next action is to produce a
  plan first.
  The live meaning of `--run-id` (V20) is untouched — passing it **alongside** `--from-plan` is ignored,
  and, because it is the only other option that names a run, that is warned about rather than left silent,
  following the precedent already in the file for an ignored option (`infrahub_sync/cli.py:249-252`). Its
  **help text** is corrected to cross-reference
  `--from-plan`, and every new option's help string is fixed in
  [contracts/cli-review-mode.md](./contracts/cli-review-mode.md) before `docs.generate` renders it
  (AD061). The live path keeps emitting through the logger (`:153`, AD023).
- The renderer carries two obligations the reader deliberately does not (AD056, AD058): it states the
  plan's delete-computation record at both depths and annotates a non-zero delete count inline to say no
  delete will be executed by this release; and it turns an empty `operations(kind=…)` result for a
  *declared* kind into FR-006's error, listing the kinds the plan does hold.
- `apply` refuses before constructing anything when the named run has no artifact (AD026), then runs the
  five checks before any write and records `failed` on refusal with `summary["applied_operations"]` as an
  empty list (AD062). **It is the single writer of `run.json` (AD069)**: it merges the record `apply_plan`
  returns — or the partial record a mid-apply rejection carries on its error — into `run_file.summary`
  before the save at `:350-351`, because that save writes the whole payload from an instance whose `summary`
  is otherwise the empty one built at `:322-323`. A delete-bearing plan **exits 0 and records `applied`**
  with a non-zero
  `summary["skipped_delete_count"]`, a `logging.WARNING` naming it, and a completion line naming it too, so
  the last thing an operator reads is not the bare `Applied run <id>` of today's `:352` (AD055). The
  schema-subhash abort at `:336-340` is **left alone**: corrected V22 shows it is unreachable, so
  AD010's incidental repair of it is dropped along with T060 (AD063).
- No `add_typer` is added; the command count stays at five (V19).

**Tests (all local)**: `CliRunner` cases for summary, detail, `--kind`, each error path with its next
action and its enumeration, the delete-computation and delete-count disclosure at both depths, and the
SC-012 comparison against the **committed** T002 baseline fixture — not a `git stash` round trip, which
no-ops on a committed tree and diffs the post-change listing against itself (AD060). Plus an assertion
that the review path constructs no adapter (the adapter import is patched to raise if called) and creates
no directory; an assertion that a held pipeline lock does not block review; and an assertion that each new
option's help string matches the contract.

### Phase G — Documentation, fixtures, and the changed-content fallout

**Delivers**: FR-015's "test fixtures and documentation affected by the change in plan content MUST be
updated in the same change", FR-008's documentation obligation, SC-010's canary scan.

- `docs/docs/reference/cache-layout.mdx` — the `plan/` directory, both files, the manifest field set,
  and the note that `plan.parquet` is retained and no longer read by `apply`.
- `docs/docs/running-a-sync.mdx` — the review-then-apply workflow and the new flags.
- `docs/docs/reference/cli.mdx` — regenerated with `uv run invoke docs.generate`.
- The fixture sweep for everything Phase D's delete recording and artifact write made stale.
  (`tests/cache/test_apply_plan.py` is **not** here: it is rewritten in Phase E, immediately after the
  dispatch removal that invalidates it.)
- SC-010's canary scan lands as a test that injects a credential into `settings`, runs a plan, and greps
  the artifact files, the captured CLI stdout, and the reader's returned data.
- Full gate: `uv run invoke format`, `uv run invoke lint`, `uv run ty check .`, `uv run pytest -q`, and
  the three CLI sanity commands from `AGENTS.md`.

## Requirements coverage

Every functional requirement has a named home. No FR is unhomed.

| FR | Implemented in |
|---|---|
| FR-001 | Phase D — `Potenda.write_plan_artifact` on the `diff`, serial `sync` and (reordered) tier `sync` paths |
| FR-002 | Phase A `models.PlannedOperation` + Phase D `derive.operations_from_diff`; [contracts/plan-artifact-format.md](./contracts/plan-artifact-format.md) |
| FR-003 | Phase A `identity.operation_id` |
| FR-004 | Phase A `checksum.compute_plan_checksum` + Phase B manifest assembly (snapshot binding, delete-computation field) |
| FR-005 | Phase A `canonical.canonical_json_bytes` + Phase B ordering |
| FR-006 | Phase C `review.SavedPlan.summary()` / `.operations(kind=…)`, including the delete-computation and delete-count disclosure fields (AD056) and the empty-for-a-declared-kind rule (AD058); Phase F renderer for the never-empty error and the inline annotations |
| FR-007 | Phase C `reader.load_plan_artifact` (pure filesystem read); proven by the subprocess test |
| FR-008 | Phase F `diff --from-plan <run-id> --detail --kind` (AD057), branching before lock and adapter construction; help text specified in the CLI contract (AD061) |
| FR-009 | Phase C `verify.verify_plan` (five ordered checks) + Phase F apply gating and run-state recording |
| FR-010 | Phase B `operations_count` + Phase C torn classification and snapshot digest/row-count recheck |
| FR-011 | Phase A `config_version.default_config_version` + opaque equality comparison in Phase C `verify` |
| FR-012 | Phase E `Potenda.apply_plan` replacement — reads the artifact, no extraction, no comparison |
| FR-013 | Phase E `InfrahubAdapter.apply_planned_operation` (upsert for create **and** update, replace-set) |
| FR-014 | Phase E `PeerResolver` (memo, destination query, zero-match and multi-match refusals) |
| FR-015 | Phase D `derive.derive_deletes` + the per-side extract flag + manifest `delete_operations_computed` |
| FR-016 | Phase D (structural: deletes never enter the diff) + Phase E (`delete` raises `SkippedDeleteOperation`, never writes; declining to execute it is a designed limitation, not a fault — AD055) |
| FR-017 | Phase E apply loop — deletes collected and skipped, every non-delete applied, run ends **`applied`** with `summary["skipped_delete_count"]`, the skipped identifiers, and a warning naming the count (AD055); Phase C reader for the one class that still fails the run, an action outside `ACTIONS`, refused before any write |
| FR-018 | Phase D payload construction (mapped fields only; `settings` never read into a record) + Phase G canary test |
| FR-019 | Phase C `reader` classification (absent `plan/` → v1 message) + Phase B write order + Phase E removal of the v1 dispatch |
| FR-020 | Phase E — ordered applied-identifier sequence under `summary["applied_operations"]` on the run file, its one named home (AD062) |
| FR-021 | Phase B — uniqueness assertion at write time, failing the plan run |
| FR-022 | Phase B — present, empty operations file with count 0; Phase E — successful no-op apply |
| FR-023 | Phase E — write-surface check inside the same pre-write gate, error names the adapter |
| FR-024 | Phase D `derive.warn_missing_convergence_key` — both the HFID condition and the uniqueness-constraint condition; log stream only, non-manifest |
| FR-025 | Phase E — best-effort last-applied pointer as the final element of FR-020's sequence |
| FR-026 | Phase B — the manifest and operation schemas carry no grouping field; asserted by a test |
| FR-027 | Phase A `models.PlanManifest` (eight fields, `extra="allow"`) + Phase C version refusal |
| FR-028 | Phase A `models` obligation levels and `identity.canonical_identity`; Phase D absent-versus-empty handling |
| FR-029 | Phase C `review.read_saved_plan`, returning `[]` rather than raising for a declared kind with no operations (AD058); the Phase F command is a thin renderer over it and owns the presentation rules |
| FR-030 | Phase D `derive` — every derivation failure raises a named error and fails the command, on the `diff` path as on `sync`; no tolerance option is added (V34) |

## Success-criteria evidence map

**Six** criteria and half-criteria need a live Infrahub and land behind the `integration` marker (V28);
the rest run locally.

**These six are deferred evidence, not produced evidence (AD045b).** No Infrahub is reachable in this
environment (AD007), so SC-001, SC-002, SC-003 and SC-008, and the live halves of SC-007 and SC-016,
have **no passing evidence at merge time**. **Five** of those six are the brief's own criteria —
DBA-001, DBA-002, DBA-003 and DBA-008 in full, plus the live half of DBA-007. The sixth, SC-016's live
half, is a criterion this specification derived from DBR-007; the brief states none for it, so it must
not be counted against the brief's tally. The brief's completion condition, "every requirement and
acceptance criterion has inspectable passing evidence", is therefore **not met at merge**. This is
stated rather than left to be inferred from a marker. The rendered-mutation conformance row below
narrows the exposure — it catches an AD042-class defect offline, which is exactly the class those six
criteria were the only other check on — but it does not substitute for them.

| SC | Evidence the plan proposes | Live destination? |
|---|---|---|
| SC-001 | Apply a stored plan against live Infrahub with `diff_from`/`sync_from` patched to fail if called; assert the apply completes and neither was invoked | **integration** |
| SC-002 | Apply once, record per-kind counts and HFID identities, apply again, compare | **integration** |
| SC-003 | Per-class matrix (create / update / relationship-bearing) across apply-once, apply-twice, crash-after-commit and crash-before-write; the crash is injected by raising inside the apply loop at the two points; the relationship class is measured by SC-008's peer-set comparison | **integration** |
| SC-004 | Six parametrized negative fixtures (checksum mismatch, config-version mismatch, snapshot-binding mismatch, absent operations, truncated snapshot, absent snapshot) asserting refusal, zero writes and `failed` in `run.json` | local (fixtures + a write-recording fake destination) |
| SC-005 | Review a stored plan, capture the identifier set from per-object output, apply against a fake destination, compare against the FR-020 record | local |
| SC-006 | Two derivations over identical inputs at the same extraction mode; byte-compare `operations.jsonl` and the manifest with `run_id` and `created_at` removed from both sides. **DBA-006 is therefore carried *conditionally*** — the pinned-extraction-mode precondition is part of the criterion, not a caveat, because `delete_operations_computed` is inside the checksum and outside the two masked fields (AD064) | local |
| SC-007 | A plan fixture containing a delete; apply; assert every non-delete operation landed, the delete target is untouched, run state **`applied`**, `summary["skipped_delete_count"]` equals the plan's delete count, `summary["skipped_delete_operations"]` holds their identifiers, a captured warning names the count, and `applied ∪ skipped` covers the plan's whole identifier set. A run state of `failed` **fails** this criterion (AD055) | local (fake destination) **and** integration (counts before/after live) |
| SC-008 | Apply a relationship-bearing kind from the qualified config with no store loaded; read peer sets back; compare as unordered `(peer kind, peer identity)` sets. **At least one referenced peer pre-exists at the destination and is absent from the plan**, so the destination-query path actually runs — with every peer created by the same plan, tier ordering fills the memo and the query path is never exercised, and the test would pass while the requirement is broken | **integration** |
| SC-009 | Four cases — summary and detail, in-process and via CLI — all against a stored artifact read in a **new process**, with source and destination unreachable. Each case also asserts the delete-computation record is stated and a non-zero delete count is annotated; two run against an incrementally-loaded plan so the not-computed wording is asserted reachable (AD056) | local |
| SC-010 | Canary credential in `settings`; scan the artifact files, captured stdout, and the reader's returned data | local |
| SC-011 | v1 fixture (`plan.parquet`, no `plan/`); assert the re-plan message and zero writes | local |
| SC-012 | `--help` captured after the change and diffed as text against the **committed** T002 baseline fixture — not a `git stash` round trip, which no-ops on a committed tree and diffs the post-change listing against itself (AD060); plus the SC-009 CLI cases | local |
| SC-013 | An opaque printable-ASCII value supplied verbatim in-process, round-tripped through write and apply comparison; plus SC-004's mismatch case | local |
| SC-014 | Four plan runs — three against fake schemas (a kind with no `human_friendly_id`, a kind whose plan identity misses an HFID component, and a kind with a complete HFID but **no uniqueness constraint** covering the plan's identity attributes), asserting each warning's content and a successful run; plus one against a destination exposing **no schema at all**, asserting the warning is skipped, no error is raised and `diff` still succeeds (AD052) | local (fake schema) |
| SC-015 | Copy a `plan/` directory between two run directories; assert refusal on the run-identifier check, zero writes, `failed` | local |
| SC-016 | Zero-match and multi-match peer fixtures; assert both message shapes and that neither is skipped, **and** that the live `sync` write path's warn-and-continue is unchanged (AD048) | local (mocked SDK) **and** integration (real ambiguity) |
| SC-017 | Two plan runs, one full-extract and one incremental on the destination side; compare delete presence and the manifest field; assert the incremental run's apply records a `skipped_delete_count` of **zero** so no phantom delete inflates it, and that both review depths state deletes were not computed (AD055, AD056) | local |
| SC-018 | A fixture manifest with `format_version: 99`; assert refusal, message content, zero writes, `failed`, and textual difference from the SC-011 message | local |
| *(no SC — offline conformance, AD045a as rebuilt by AD054 and sharpened by AD065/AD067/AD068)* | **Rendered-mutation** conformance against a real `InfrahubNodeSync` built from a **committed `NodeSchemaAPI` fixture**: the rendered mutation input carries `id` or `hfid` (`.venv/…/infrahub_sdk/node/node.py:295-298`, `:1843-1846`) — asserted outright for an all-direct HFID, and as a `xfail(strict=True)` for a relationship-crossing HFID, which cannot render keyed today (AD067); the replace-set reconciliation **issued a destination read** for the relationship before reading the peer set it compares against (AD065); two applies of one operation render **byte-identical** inputs (AD068). Not a criterion of its own — it is the offline half of the assurance SC-002, SC-003 and SC-008 carry, so an AD042-class defect is caught without waiting for the deferred live run. Its earlier form asserted the assembled `data` against a wholly mocked SDK, where two of the three assertions cannot fail for the right reason | local (real node, committed schema fixture) |

## Complexity Tracking

| Violation | Why needed | Simpler alternative rejected because |
|---|---|---|
| **Principle III** — the planned-write surface exists on one adapter of nine | The brief scopes the write surface to "the Infrahub destination adapter"; the qualified path is NetBox → Infrahub, and NetBox is the source side, which needs no write surface | Implementing the surface on all nine adapters is scope this outcome does not carry and would need a convergence-key story per system. The asymmetry is already today's state (V3: zero implementations) and is already handled by a clear, actionable error naming the adapter (V1), which FR-023 preserves |
| A new multi-module package rather than extending `cache/` | The plan artifact is a shared contract nine later outcomes consume; keeping it inside `cache/` would entangle a public format with run-directory plumbing, and every consumer would import a module named for storage | A single `infrahub_sync/plan.py` was considered. It would exceed 900 lines and mix canonical encoding, derivation, verification and rendering in one namespace — worse for the reviewability Principle VII asks for. Every module here has a real caller in this change |
| Replacing `Potenda.apply_plan` rather than adding a sibling | FR-019 forbids "a second apply path with weaker guarantees"; leaving the v1 row dispatch wired would *be* that second path | Adding `apply_saved_plan` alongside was considered and rejected on the requirement's plain text. Removal is safe: `apply_cached_row` has zero implementations (V3), so only one test double is affected |
| **Engine-level plan derivation reads one adapter's schema surface** — `warn_missing_convergence_key` runs in `infrahub_sync/plan/derive.py`, which the engine calls for every destination, but the `human_friendly_id` / `uniqueness_constraints` it reads live only on the Infrahub adapter (V38) | FR-024 is the brief's own non-unique-destination-identifier edge case and it is stated as a plan-time warning, so it has to run where the plan is derived. The two fields it needs exist on one adapter because Infrahub is the only destination with a planned-write surface (the Principle III row above), so the warning is only ever *meaningful* there | Two alternatives were rejected. Pushing the check behind an adapter method would add a method to the adapter contract that eight adapters implement as a no-op — a wider change than the guard. Skipping the check entirely rather than guarding it would drop the brief's own edge case. The accepted shape is AD052: read the schema defensively, skip the warning where none is exposed, and never make its absence an error — with a regression assertion that `diff` against a non-Infrahub destination still succeeds. The coupling is real and is recorded here rather than hidden inside a `getattr` |

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| `save(allow_upsert=True)` may merge rather than replace a cardinality-many relationship set; unverifiable without a live server (AD007) | SC-008 fails; relationships drift | PD-005 makes the replace-set explicit after the upsert, **re-reading the destination's peer set before comparing** (AD054), so the server's own semantics do not decide the outcome. The re-read is the load-bearing part: the pre-existing code reads the peer set before loading it (corrected V12) and a locally built node reports the desired set as its existing set (V12a), so without the re-read the reconciliation is a guaranteed no-op that passes against a mock and removes nothing against a server. **And the re-read must be a mechanism, not an ordering (AD065)**: the SDK's `fetch()` self-guards on `initialized`, so an enforcement that merely calls it first still reads nothing — the manager is forced cold or a scoped destination read is issued, and the test asserts a read was issued. The enforcement is new code on the planned-write path; `update_node` is untouched (AD070) |
| The nested `<rel>__<attr>__value` filter spelling for peer lookup is unverified offline | Peer resolution fails on the ten identity-bearing-reference mapping entries (V30) | PD-004 fixes the construction rule from the schema's own HFID paths, and AD043's recursive `{peer_kind, identity}` shape is what makes the nested arm constructible without splitting a unique-id; the spelling is asserted by an `integration`-marked test, and a zero match is a loud refusal (FR-014), never a silent drop |
| Five brief acceptance criteria, plus one criterion this specification derived, have no passing evidence at merge (AD007, AD045b) | The brief's completion condition is not met; a convergence defect could ship unseen — AD042 is exactly that class | Stated explicitly rather than left to a marker, in the evidence map above, Constitution Principle V, and `tasks.md`. Narrowed by the AD045a conformance harness **in the AD054 shape** — a real node from a committed schema fixture, asserting the rendered mutation input — and by the apply-time HFID-component assertion, neither of which needs a server. In its earlier form the harness narrowed nothing: an assertion over the assembled `data` cannot see keyedness, and a mock holds no destination state for a second-create or replace-set assertion to fail against. **How much it narrows is now stated rather than implied (AD067)**: for a destination kind whose HFID crosses a relationship the harness can only record that the render is unkeyed today, as a strict expected failure, so that class of convergence rests entirely on the deferred live evidence. **Material — reported to root** |
| **Nested HFID resolution depends on the SDK client store being populated** (V39, AD051) | For a destination kind whose `human_friendly_id` crosses a relationship, the SDK cannot form the mutation's `hfid` from a peer supplied as a resolved id: `get_path_value` needs the peer out of the client store, a bare-id relationship value carries no `__typename` so the store is never even consulted, one `None` component nulls the whole HFID, and the mutation then goes out with neither `id` nor `hfid`. That is an unkeyed write — the AD042 failure mode by another route. This resolver is specified to return ids and never to touch that store | Not mitigated away, and not claimed to be solved. Step 3b's per-component assertion (AD051) is the detector: an operation whose HFID components cannot be accounted for raises *before* the create instead of duplicating silently, so the failure is loud and local rather than a duplicate discovered later at the destination. What step 3b cannot establish offline is whether an accounted-for nested component actually keys the server-side upsert — that needs a live Infrahub (AD007) and is carried by the `integration`-marked SC-002 and SC-003. **Two additions make the residual visible rather than filed (AD066, AD067)**: the keyedness gate emits one operator-visible warning per destination kind whose render comes back unkeyed for this reason, so an operator meets it rather than inferring it; and the conformance harness carries the keyedness assertion for such a kind as a `xfail(strict=True)` naming this row, so the day the hole closes the suite says so instead of the risk table going quietly stale. Refusing the write instead was considered and rejected — it would withdraw the ten identity-bearing-reference mapping entries of the qualified path from what this outcome delivers. **Material — reported to root** |
| `--continue-on-error` does not exist on `diff` (V34), so new plan-derivation failures there are hard failures | An operator's `diff` starts exiting non-zero on data that used to render | Deliberate (FR-030, AD047): warn-and-skip would emit a silently incomplete plan, the divergence DBR-016 exists to prevent. The Principle I reading that permits it is stated in the Constitution Check above so it is reviewable. **Material — reported to root** |
| A source snapshot's raw bytes vary every run because `_extract_ts` is per-run (V7), so a byte-level binding digest would make SC-006 unachievable | DBA-006 unachievable | PD-008 defines the snapshot digest over the logical rows excluding `_extract_ts`. **Material — reported to root** |
| Restructuring the tier branch changes an existing execution path | Regression in `sync --parallel` | PD-009; the change is a reordering only, guarded by the existing `tests/test_potenda_parallel.py` and `tests/cache/test_sync_cache_flow.py` plus a new call-order assertion |
| Deletes now appear in plans, changing what operators see | User-visible change | Sanctioned by the brief and FR-015; fixtures and docs updated in Phase G. Both review depths disclose the delete count and say no delete will be executed (AD056), and the apply records the skipped count rather than failing (AD055), so the change is legible rather than alarming |
| An apply that skips deletes records `applied`, which the incremental path's success set already contains (`infrahub_sync/cache/incremental.py:24`) | A delete-bearing apply counts as a successful prior run for a later warm start | Accepted and recorded rather than mitigated (AD055): the apply did succeed at everything this release executes, and adding a distinct state to say otherwise is the compatibility change AD010 declines. The recorded skip count is what a later reader uses to tell the two cases apart |
