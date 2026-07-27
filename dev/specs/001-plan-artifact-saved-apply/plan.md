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

AD001–AD036 in the spec's Clarifications are treated as settled input throughout. Where this plan had
to close a detail those decisions left open, or where two of them interact in a way neither
anticipated, the resolution is recorded in [research.md](./research.md) as **PD-nnn** and flagged,
not buried.

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
and every other criterion runs locally against fakes and fixtures.

**Target Platform**: CLI on Linux/macOS; the artifact is filesystem-local through `pathlib` (the
fsspec indirection is preserved for the Parquet files it already owns).

**Project Type**: Single Python package + Typer CLI.

**Performance Goals**: None asserted — the spec's Out of Scope explicitly declines plan-volume and
review-latency targets (AD030). The line-oriented `operations.jsonl` encoding is chosen so a large
plan can be summarized without materializing all of it, but no threshold is set or tested.

**Constraints**: no new CLI command group (FR-008, SC-012); no delete reaches the destination
(FR-016); the shared execution core refactor is not a prerequisite; the qualified path is
NetBox → Infrahub via `examples/netbox_to_infrahub/config.yml`; the artifact format is a shared
contract nine later outcomes consume, so any post-ship change is breaking.

**Scale/Scope**: ~11 new modules under `infrahub_sync/plan/`, ~250 lines added to
`infrahub_sync/adapters/infrahub.py`, ~120 lines to `infrahub_sync/potenda/__init__.py`, ~150 lines to
`infrahub_sync/cli.py`, three docs pages, and roughly 45 new tests of which 9 carry the `integration`
marker.

## Verified facts about the existing code

Every claim this plan relies on was read in the tree at the cited line. Nothing below is inferred.

| # | Claim the plan depends on | Verified at |
|---|---|---|
| V1 | `Potenda.apply_plan` reads `plan.parquet` and dispatches per row to `destination.apply_cached_row(resource, action, source_id, attribute, new_value)`; it never calls `diff_from`/`sync_from`, and it guards a missing surface with `NotImplementedError` naming the adapter class | `infrahub_sync/potenda/__init__.py:341-370` (guard `:354-360`, dispatch `:363-370`) |
| V2 | `_diff_to_rows` emits `dest_id: ""` and `attribute: ""` as literal empty strings and carries only the changed attributes (`get_attrs_diffs()`), never the full payload and never the identity | `infrahub_sync/potenda/__init__.py:297-331` (empty fields `:322-323`, attrs `:314-316`) |
| V3 | `apply_cached_row` has **no adapter implementation anywhere**: the only occurrences in the repository are the engine's own dispatch, one `MagicMock` test double, and a bench print | `infrahub_sync/potenda/__init__.py:344,354,357,361,364`; `tests/cache/test_apply_plan.py:43-44`; `tasks/bench.py:413` |
| V4 | `DiffElement` carries `keys` (the identifiers mapping — the destination identity), `type` (the kind), and `source_attrs` (the **full** source attribute set, not just the delta) | `.venv/…/diffsync/diff.py:189-196`; `keys` documented "as in DiffSyncModel.get_identifiers()" at `:180` |
| V5 | `DiffElement.action` returns `create` / `update` / `delete` / `None` | `.venv/…/diffsync/diff.py:237-254` |
| V6 | Deletes are absent from today's plan because `Potenda` defaults its flags to `SKIP_UNMATCHED_DST`, and diffsync drops destination-only objects from the diff under that flag | `infrahub_sync/potenda/__init__.py:92-93`; `.venv/…/diffsync/helpers.py:191-192` |
| V7 | `write_resource_side` writes `<run_dir>/<side>/<resource>.parquet` and injects `_extract_ts`, `_source_id`, `_tombstone` into every row; `_extract_ts` is `datetime.now(timezone.utc)` allocated per side per run | `infrahub_sync/cache/parquet_io.py:92-142` (injection `:126-128`); `infrahub_sync/potenda/__init__.py:130` |
| V8 | Sidecars are written atomically with tmp+`Path.replace` — the discipline AD014 asks the manifest write to match | `infrahub_sync/cache/sidecars.py:13-24` |
| V9 | Run state vocabulary is `pending \| running \| dry-run \| applied \| failed`, and `previous_successful_run_dir` treats only `applied`/`dry-run` as successful | `infrahub_sync/cache/sidecars.py:71`; `infrahub_sync/cache/incremental.py:24,44` |
| V10 | The Infrahub create path is the convergent upsert — `client.create(...)` then `save(allow_upsert=True)` | `infrahub_sync/adapters/infrahub.py:611-612` |
| V11 | `InfrahubModel.update` opens with `client.get(id=self.local_id, ...)`, and `local_id` is populated only by a destination load, so it is unusable from a saved plan | `infrahub_sync/adapters/infrahub.py:622`; populated `:510`; declared `infrahub_sync/__init__.py:232` |
| V12 | Cardinality-many replace-set exists today only in `update_node`, via `compare_lists(existing_peer_ids, new_peer_ids)` then remove `existing_only` / add `new_only` | `infrahub_sync/adapters/infrahub.py:149-175` (compare `:166`, remove `:171-172`, add `:174-175`) |
| V13 | Peer resolution today reads the **loaded** SDK node store (`store.get(key=…, kind=…)`), populated during `model_loader` — exactly the dependency a saved-plan apply cannot satisfy | `infrahub_sync/adapters/infrahub.py:57-94` (store reads `:78,81`); store populated `:454`, `:501`, `:613` |
| V14 | A zero-match peer today is dropped with a `logger.warning` and `continue` — the behavior AD016 replaces | `infrahub_sync/adapters/infrahub.py:141-143`, `:212-214`, `:229-231` |
| V15 | The SDK upsert mutation is keyed on `data["id"]` if set, else `data["hfid"]`; `get_human_friendly_id()` returns `None` when the schema declares no `human_friendly_id` or any component path resolves to `None` | `.venv/…/infrahub_sdk/node/node.py:295-298`, `:128-138` |
| V16 | `human_friendly_id: list[str] \| None` is a field on the schema object the adapter already caches wholesale | `.venv/…/infrahub_sdk/schema/main.py:272`; adapter cache `infrahub_sync/adapters/infrahub.py:345` |
| V17 | A multi-match from `client.get` surfaces today as a bare `IndexError("More than 1 node returned")` | `.venv/…/infrahub_sdk/client.py:566` |
| V18 | Tiers come from `compute_tiers`, which excludes self-edges and drops optional (non-identity-bearing) edges to break cycles; a configuration with an explicit `order:` yields `tiers = None` | `infrahub_sync/dependency_graph.py:25-36` (self-edge `:33-34`), `:39-53`, `:81-100`; `infrahub_sync/__init__.py:132-133` |
| V19 | The CLI is a single flat `typer.Typer()` with **no** `add_typer` anywhere, exposing exactly five commands: `list`, `diff`, `sync`, `apply`, `generate` | `infrahub_sync/cli.py:31`, `:77`, `:86`, `:166`, `:295`, `:355` |
| V20 | `diff` already has `--run-id` meaning "re-use a specific cache run id"; its plan output goes through the logger; its whole body is wrapped in `pipeline_lock` with a 60-second default timeout | `infrahub_sync/cli.py:98`, `:153`, `:129`; `infrahub_sync/cache/locks.py:21-33` |
| V21 | `get_potenda_from_instance` creates the run directory unconditionally (`mkdir(parents=True, exist_ok=True)`) and writes `schema-sub-hash.txt` into it **before** any check — which is what would turn a typo'd run id into a valid-looking empty run | `infrahub_sync/utils.py:244-246`, `:256-263` |
| V22 | The `apply` command writes `run.json` with `status: running` and then, on a schema-subhash mismatch, aborts through `print_error_and_abort`, permanently leaving `running` on disk | `infrahub_sync/cli.py:322-323`, `:336-340`; `print_error_and_abort` `:72-74` |
| V23 | `write_plan` is called unconditionally on both the `diff` and `sync` paths and in both `sync_in_tiers` branches | `infrahub_sync/cli.py:152`, `:271`; `infrahub_sync/potenda/__init__.py:462`, `:496-499` |
| V24 | In the tier branch of `sync_in_tiers`, per-tier diffs and per-tier writes are **interleaved**, and the aggregated plan is written only after every write has happened | `infrahub_sync/potenda/__init__.py:480-499` (diff `:484`, accumulate `:485`, sync `:487`, write `:496-499`) |
| V25 | `load_one_side` OR-accumulates `_did_full_extract` across both sides deliberately, so it cannot answer "did the destination run a full extract" | `infrahub_sync/potenda/__init__.py:189-200` (accumulate `:200`, comment `:197-199`), consumed `:430` |
| V26 | The incremental path replays the prior run's snapshot skipping tombstones and layers `list_changed_since` on top — so the destination store is not a live full enumeration | `infrahub_sync/cache/incremental.py:135-170` (tombstone skip `:164-165`); `infrahub_sync/potenda/__init__.py:221-228` |
| V27 | A stored run is located only as `cache_root_for(<sync name>)/<run_id>`, so review is adapter-free but stays configuration-bound | `infrahub_sync/cache/paths.py:26-59` |
| V28 | The `integration` marker exists and is documented as opt-in with `INFRAHUB_ADDRESS` + `INFRAHUB_API_TOKEN` | `pyproject.toml:133-135` |
| V29 | Generated DiffSync models are flat and carry relationships as plain fields alongside attributes, with `_identifiers` / `_attributes` tuples | `infrahub_sync/generator/templates/diffsync_models.j2:29-48`; non-component relationships folded into `_attributes` at `infrahub_sync/generator/__init__.py:78-88` |
| V30 | On the qualified configuration, seven kinds carry a **relationship inside their identity** (`LocationRack.site`, `DcimDeviceType.manufacturer`, `DcimDevice.location`, `Interface{Physical,Virtual,Lag}.device`, `IpamVLAN.vlan_group`, `IpamPrefix.vrf`, `IpamIPAddress.vrf`) — so an identity value is sometimes a peer's DiffSync unique-id, not a scalar | `examples/netbox_to_infrahub/config.yml`, `identifiers:` cross-referenced against `fields[].reference`; enumerated in [research.md](./research.md) PD-004 |

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
| **I. Read-only / dry-run by default** | PASS | The new `diff --from-plan` mode is strictly read-only: no adapter constructed, nothing extracted, no lock taken, no run directory created or modified, run state never mutated (FR-008, AD021, AD031). The only new mutating behavior sits inside the already-mutating `apply`, gated behind five checks that must all pass before any write. No new implicit default writes anything. |
| **II. Sync idempotency & safety** | PASS | Convergence is the point: creates and updates both route through the HFID-keyed upsert (FR-013, V10); SC-002/SC-003 measure re-apply and both crash windows; the apply refuses rather than silently skipping on every divergence path — unsupported operation (FR-017), unresolved peer (FR-014), destination rejection (AD027). Partial failure is surfaced with the failing operation identifier, never left ambiguous. |
| **III. Adapter symmetry & pattern consistency** | PASS **with a recorded tension** | The planned-write surface is implemented on the Infrahub adapter only. That is asymmetric across the nine adapters. It is nonetheless the brief's explicit scope ("a destination write surface on the Infrahub adapter"), the asymmetry is already the status quo (V3: zero adapters implement the existing surface), and the engine already fails with a clear, actionable error naming the adapter when the surface is absent (V1, preserved as FR-023). Recorded in [Complexity Tracking](#complexity-tracking). `list`/`diff` pathways remain available on every adapter and everything still flows through `potenda`. |
| **IV. Type safety & explicit contracts** | PASS | Every new module is fully typed with modern unions; the record types are Pydantic models in the existing `SyncConfig` style; a specific exception hierarchy (`PlanArtifactError` + eight named subclasses, see [contracts/plan-reader-api.md](./contracts/plan-reader-api.md)) replaces any broad catch. No `[[tool.ty.overrides]]` block is added; `uv run ty check .` must exit 0. Where the SDK's dynamic surface forces it, a targeted `# ty: ignore[<rule>]` with a TODO is used at the call site, matching `infrahub_sync/potenda/__init__.py:69-70`. |
| **V. Test discipline** | PASS | ~45 tests written alongside the change, parametrized for the verification matrix and the negative cases, atomic and single-purpose. Live-destination evidence is opt-in behind the existing `integration` marker (V28) rather than a new mechanism. |
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
├── spec.md                                  # input (1367 lines, AD001–AD036 settled)
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
│   └── test_derive.py
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

Seven phases, ordered so each is independently reviewable and every phase before F leaves the tree
green. A–C are pure additions with no behavior change; D changes what a plan run writes; E adds the
write surface; F changes the CLI; G closes documentation and fixtures.

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
  point. It returns **data**: `.manifest`, `.summary()` (count per action and count per kind),
  `.operations(kind=None)`, `.checksum_ok`, `.verification_notes`. It never writes to a stream, never
  mutates run state, and renders a plan that would fail verification rather than refusing (AD031). A
  `kind` filter matching no operation, or naming a kind the configuration does not declare, raises
  `UnknownPlanKindError` naming the kind rather than returning empty (FR-006, AD036).

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
  identity, `element.source_attrs` the full payload (V4), `element.type` the kind, `element.action` the
  action (V5). Elements whose action is `delete` are **skipped here** so deletes come from one source
  only (FR-015) — which also keeps the derivation correct for a project that has cleared
  `SKIP_UNMATCHED_DST` (V6).
- Payload/relationship split: a payload key whose `SchemaMappingField.reference` is set becomes a
  relationship reference, not a payload field. The reference records the peer's kind (the `reference`
  value) and the peer's identity mapping, obtained by looking the peer up in the loaded **source**
  store by its DiffSync unique-id and calling `get_identifiers()` — never by splitting the unique-id
  on `__`, which is the v1 flaw the brief names. Cardinality-many references are a list ordered
  canonically by peer identity (AD003). Absent versus empty is honored strictly (FR-028.2).
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
  makes FR-016 structural rather than configuration-dependent.
- `warn_missing_convergence_key(...)`: for each destination kind with an operation, read
  `adapter.schema[kind].human_friendly_id` (V16) and warn on the log stream when it is absent or when
  the plan's identity does not supply every component path (FR-024). Warning only — never a manifest
  field, so it stays outside the checksum and outside SC-006.
- `Potenda.write_plan_artifact(...)` composes the above, computes the source-snapshot binding per
  PD-008, and calls the Phase B writer. It is invoked from the `diff` path, the serial `sync` path, and
  — after the reordering below — the tier `sync` path.
- **Tier-mode reordering**: today the tier branch interleaves per-tier diff and per-tier sync and writes
  the plan only at the end (V24), which cannot satisfy FR-001's "before anything is written". The branch
  is restructured to compute every tier's diff first, write the artifact, then execute the stored diffs
  tier by tier through the existing `sync(diff=...)` signature
  (`infrahub_sync/potenda/__init__.py:292-295`). Recorded as PD-009.
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

- `delete` raises `UnsupportedPlannedOperationError`. The engine collects these rather than stopping,
  because SC-007 requires every non-delete operation to still be applied.
- `create` and `update` both build `data` from the payload plus resolved relationship peer ids, run it
  through the existing `client.schema.generate_payload_create(...)` for source/owner/protection parity
  (`infrahub_sync/adapters/infrahub.py:608-610`), then `client.create(...)` + `save(allow_upsert=True)`
  (V10) — never `InfrahubModel.update`, which is unusable without a destination load (V11).
- Cardinality-many relationships are then reconciled as an explicit **replace-set** against the saved
  node, reusing the `compare_lists` remove/add logic that is today the only verified replace-set in the
  tree (V12), extracted into `_replace_relationship_set(node, rel_name, peer_ids)`. This is PD-005: it
  makes the semantics deterministic regardless of whether the upsert mutation itself replaces or merges
  a relationship list, which cannot be verified without a live server (AD007).
- `PeerResolver` (new, in the adapter): a memoized `(kind, canonical identity) -> destination node id`
  map. Populated from each completed create/update; on a miss it queries the destination by building
  filter kwargs from the destination kind's `human_friendly_id` component paths (V16), sourcing each
  path from the operation's identity and — for a `rel__attr__value` path — from the identity recorded
  on that field's relationship reference, so no data value is ever split on `__` (PD-004). Zero results
  → `PeerNotFoundError` naming peer kind, peer identity and the referring operation identifier; more
  than one → `PeerAmbiguousError` naming peer kind, peer identity and the match count (FR-014, SC-016)
  — replacing today's silent drop (V14) and the SDK's bare `IndexError` (V17). Failed lookups and
  failed writes are **never** memoized (FR-014, AD036).

Edits to `infrahub_sync/potenda/__init__.py`:

- `apply_plan()` is **replaced**, not paralleled: it loads the new artifact, checks the write surface
  before any write (FR-023, keeping today's `NotImplementedError` shape at `:354-360`), executes
  operations in stored order, records applied identifiers as an ordered sequence on the run result
  (FR-020, whose final element is FR-025's last-applied pointer), collects unsupported operations, and
  fails the run naming them if any (FR-017, SC-007). A destination rejection or transport failure stops
  at that operation, keeps what was written, and fails naming the operation identifier and the
  underlying error (AD027). The v1 `apply_cached_row` dispatch is removed — leaving it wired would be
  exactly the second apply path FR-019 forbids, and it has zero implementations to break (V3). Recorded
  as PD-010.

**Tests**: local unit tests against a mocked `InfrahubClientSync` covering payload construction, upsert
invocation, replace-set reconciliation, the memo's population and its refusal to cache negatives, both
peer-resolution refusals, delete → unsupported, the missing-surface error, the ordered applied set, and
fail-fast on rejection. Live evidence (SC-001, SC-002, SC-003, SC-007, SC-008, SC-016) goes to
`tests/integration/test_saved_plan_apply_integration.py` behind the `integration` marker.

### Phase F — CLI review mode and apply rewiring

**Delivers**: FR-008, FR-009's run-state obligations, SC-009's CLI cases, SC-012.

Edits to `infrahub_sync/cli.py`:

- `diff` gains `--from-plan` (bool), `--detail` (bool) and `--kind` (str). In `--from-plan` mode the
  command branches **before** `pipeline_lock` (V20) and before `get_potenda_from_instance` (V21), so no
  lock is taken, no adapter is constructed, and no run directory is created. It resolves the run via
  `cache_root_for(name)/<run_id>` (V27), calls `read_saved_plan`, and renders through `typer.echo`
  (AD032). `--from-plan` without `--run-id` errors naming the option; an unknown run or a run with no
  artifact errors naming the run identifier and the expected artifact path; an unreadable path errors
  naming the path. The live meaning of `--run-id` (V20) is untouched, and the live path keeps emitting
  through the logger (`:153`, AD023).
- `apply` refuses before constructing anything when the named run has no artifact (AD026), then runs the
  five checks before any write, records `failed` on refusal with an empty applied-operation set, and —
  the pre-existing bug AD010 folds in — the schema-subhash abort at `:336-340` now records `failed`
  instead of leaving `running` on disk (V22).
- No `add_typer` is added; the command count stays at five (V19).

**Tests (all local)**: `CliRunner` cases for summary, detail, `--kind`, each error path, and the SC-012
before/after `--help` capture compared as text; an assertion that the review path constructs no adapter
(the adapter import is patched to raise if called) and creates no directory; an assertion that a held
pipeline lock does not block review.

### Phase G — Documentation, fixtures, and the changed-content fallout

**Delivers**: FR-015's "test fixtures and documentation affected by the change in plan content MUST be
updated in the same change", FR-008's documentation obligation, SC-010's canary scan.

- `docs/docs/reference/cache-layout.mdx` — the `plan/` directory, both files, the manifest field set,
  and the note that `plan.parquet` is retained and no longer read by `apply`.
- `docs/docs/running-a-sync.mdx` — the review-then-apply workflow and the new flags.
- `docs/docs/reference/cli.mdx` — regenerated with `uv run invoke docs.generate`.
- `tests/cache/test_apply_plan.py` — rewritten against the new artifact and the new surface.
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
| FR-006 | Phase C `review.SavedPlan.summary()` / `.operations(kind=…)` and the kind-miss error |
| FR-007 | Phase C `reader.load_plan_artifact` (pure filesystem read); proven by the subprocess test |
| FR-008 | Phase F `diff --from-plan --detail --kind`, branching before lock and adapter construction |
| FR-009 | Phase C `verify.verify_plan` (five ordered checks) + Phase F apply gating and run-state recording |
| FR-010 | Phase B `operations_count` + Phase C torn classification and snapshot digest/row-count recheck |
| FR-011 | Phase A `config_version.default_config_version` + opaque equality comparison in Phase C `verify` |
| FR-012 | Phase E `Potenda.apply_plan` replacement — reads the artifact, no extraction, no comparison |
| FR-013 | Phase E `InfrahubAdapter.apply_planned_operation` (upsert for create **and** update, replace-set) |
| FR-014 | Phase E `PeerResolver` (memo, destination query, zero-match and multi-match refusals) |
| FR-015 | Phase D `derive.derive_deletes` + the per-side extract flag + manifest `delete_operations_computed` |
| FR-016 | Phase D (structural: deletes never enter the diff) + Phase E (`delete` raises, never writes) |
| FR-017 | Phase E apply loop — unsupported operations collected, non-deletes applied, run fails naming them |
| FR-018 | Phase D payload construction (mapped fields only; `settings` never read into a record) + Phase G canary test |
| FR-019 | Phase C `reader` classification (absent `plan/` → v1 message) + Phase B write order + Phase E removal of the v1 dispatch |
| FR-020 | Phase E — ordered applied-identifier sequence on the run result |
| FR-021 | Phase B — uniqueness assertion at write time, failing the plan run |
| FR-022 | Phase B — present, empty operations file with count 0; Phase E — successful no-op apply |
| FR-023 | Phase E — write-surface check inside the same pre-write gate, error names the adapter |
| FR-024 | Phase D `derive.warn_missing_convergence_key`, log stream only, non-manifest |
| FR-025 | Phase E — best-effort last-applied pointer as the final element of FR-020's sequence |
| FR-026 | Phase B — the manifest and operation schemas carry no grouping field; asserted by a test |
| FR-027 | Phase A `models.PlanManifest` (eight fields, `extra="allow"`) + Phase C version refusal |
| FR-028 | Phase A `models` obligation levels and `identity.canonical_identity`; Phase D absent-versus-empty handling |
| FR-029 | Phase C `review.read_saved_plan`; the Phase F command is a thin renderer over it |

## Success-criteria evidence map

Nine criteria need a live Infrahub and land behind the `integration` marker (V28); the rest run locally.

| SC | Evidence the plan proposes | Live destination? |
|---|---|---|
| SC-001 | Apply a stored plan against live Infrahub with `diff_from`/`sync_from` patched to fail if called; assert the apply completes and neither was invoked | **integration** |
| SC-002 | Apply once, record per-kind counts and HFID identities, apply again, compare | **integration** |
| SC-003 | Per-class matrix (create / update / relationship-bearing) across apply-once, apply-twice, crash-after-commit and crash-before-write; the crash is injected by raising inside the apply loop at the two points; the relationship class is measured by SC-008's peer-set comparison | **integration** |
| SC-004 | Six parametrized negative fixtures (checksum mismatch, config-version mismatch, snapshot-binding mismatch, absent operations, truncated snapshot, absent snapshot) asserting refusal, zero writes and `failed` in `run.json` | local (fixtures + a write-recording fake destination) |
| SC-005 | Review a stored plan, capture the identifier set from per-object output, apply against a fake destination, compare against the FR-020 record | local |
| SC-006 | Two derivations over identical inputs at the same extraction mode; byte-compare `operations.jsonl` and the manifest with `run_id` and `created_at` removed from both sides | local |
| SC-007 | A plan fixture containing a delete; apply; assert the non-delete operations landed, the delete target is untouched, run state `failed`, message names the identifier and action | local (fake destination) **and** integration (counts before/after live) |
| SC-008 | Apply a relationship-bearing kind from the qualified config with no store loaded; read peer sets back; compare as unordered `(peer kind, peer identity)` sets | **integration** |
| SC-009 | Four cases — summary and detail, in-process and via CLI — all against a stored artifact read in a **new process**, with source and destination unreachable | local |
| SC-010 | Canary credential in `settings`; scan the artifact files, captured stdout, and the reader's returned data | local |
| SC-011 | v1 fixture (`plan.parquet`, no `plan/`); assert the re-plan message and zero writes | local |
| SC-012 | `--help` captured before and after and diffed as text; plus the SC-009 CLI cases | local |
| SC-013 | An opaque printable-ASCII value supplied verbatim in-process, round-tripped through write and apply comparison; plus SC-004's mismatch case | local |
| SC-014 | A plan run against a kind with no `human_friendly_id` and one with an incomplete identity; assert warning content and a successful run | local (fake schema) |
| SC-015 | Copy a `plan/` directory between two run directories; assert refusal on the run-identifier check, zero writes, `failed` | local |
| SC-016 | Zero-match and multi-match peer fixtures; assert both message shapes and that neither is skipped | local (mocked SDK) **and** integration (real ambiguity) |
| SC-017 | Two plan runs, one full-extract and one incremental on the destination side; compare delete presence and the manifest field; assert the incremental run's apply is not driven to `failed` | local |
| SC-018 | A fixture manifest with `format_version: 99`; assert refusal, message content, zero writes, `failed`, and textual difference from the SC-011 message | local |

## Complexity Tracking

| Violation | Why needed | Simpler alternative rejected because |
|---|---|---|
| **Principle III** — the planned-write surface exists on one adapter of nine | The brief scopes the write surface to "the Infrahub destination adapter"; the qualified path is NetBox → Infrahub, and NetBox is the source side, which needs no write surface | Implementing the surface on all nine adapters is scope this outcome does not carry and would need a convergence-key story per system. The asymmetry is already today's state (V3: zero implementations) and is already handled by a clear, actionable error naming the adapter (V1), which FR-023 preserves |
| A new multi-module package rather than extending `cache/` | The plan artifact is a shared contract nine later outcomes consume; keeping it inside `cache/` would entangle a public format with run-directory plumbing, and every consumer would import a module named for storage | A single `infrahub_sync/plan.py` was considered. It would exceed 900 lines and mix canonical encoding, derivation, verification and rendering in one namespace — worse for the reviewability Principle VII asks for. Every module here has a real caller in this change |
| Replacing `Potenda.apply_plan` rather than adding a sibling | FR-019 forbids "a second apply path with weaker guarantees"; leaving the v1 row dispatch wired would *be* that second path | Adding `apply_saved_plan` alongside was considered and rejected on the requirement's plain text. Removal is safe: `apply_cached_row` has zero implementations (V3), so only one test double is affected |

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| `save(allow_upsert=True)` may merge rather than replace a cardinality-many relationship set; unverifiable without a live server (AD007) | SC-008 fails; relationships drift | PD-005 makes the replace-set explicit after the upsert using the only verified implementation in the tree (V12), so the server's own semantics do not decide the outcome |
| The nested `<rel>__<attr>__value` filter spelling for peer lookup is unverified offline | Peer resolution fails on the seven identity-bearing-reference kinds (V30) | PD-004 fixes the construction rule from the schema's own HFID paths; the spelling is asserted by an `integration`-marked test, and a zero match is a loud refusal (FR-014), never a silent drop |
| A source snapshot's raw bytes vary every run because `_extract_ts` is per-run (V7), so a byte-level binding digest would make SC-006 unachievable | DBA-006 unachievable | PD-008 defines the snapshot digest over the logical rows excluding `_extract_ts`. **Material — reported to root** |
| Restructuring the tier branch changes an existing execution path | Regression in `sync --parallel` | PD-009; the change is a reordering only, guarded by the existing `tests/test_potenda_parallel.py` and `tests/cache/test_sync_cache_flow.py` plus a new call-order assertion |
| Deletes now appear in plans, changing what operators see | User-visible change | Sanctioned by the brief and FR-015; fixtures and docs updated in Phase G |
