# Research: A second plan shows only real changes, and the plan step is measured by part

Paths are relative to the repository root. Line numbers were read on branch
`019-second-plan-real-changes` at `cbacabd3`.

## R1. Where deletes come from

**Finding**: Deletes are derived only in `derive_deletes` (`infrahub_sync/plan/derive.py:707-769`),
called from `infrahub_sync/potenda/__init__.py:969-978`. A destination record whose unique id
the source does not hold becomes a delete (`infrahub_sync/plan/derive.py:746-750`).
`select_operations` (`infrahub_sync/plan/problems.py:781-827`) then removes the deletes of
records left out and of kinds whose deletes are withheld. Apply never executes a delete
(`infrahub_sync/adapters/infrahub.py:1783-1789`, ADR 0004).

The destination read keeps only `local_id` and the mapped fields
(`infrahub_sync/adapters/infrahub.py:1496-1574`). Nothing on the record marks a node that
Infrahub creates itself. The only existing rule for nodes that exist only in the destination
applies to peers (`infrahub_sync/plan/destination_only_peer.py:1-163`), and the `default` IP
namespace is the reason that rule exists.

## R2. How to recognize a node that Infrahub creates itself

**Decision**: The Infrahub destination adapter keeps, beside the models it loads, the set of
destination node ids that Infrahub creates itself, as it already keeps `last_changes`
(`infrahub_sync/adapters/infrahub.py:1009-1021`, `:1268-1270`). It exposes the set through an
optional protocol in `infrahub_sync/plan/write_surface.py`, the pattern of
`MetadataReadingDestination`. `derive_deletes` skips a node of that set in its unmatched branch
only (`infrahub_sync/plan/derive.py:748-750`), so a node that a source record matches is
compared like any other (FR-003). The skip adds no entry to the plan report and logs one
`info` line with the count. A destination adapter without the protocol keeps today's behavior.

Which mark fills the set is decided by a probe of a fresh Infrahub 1.11.9 branch, the first
task of the implementation:

1. A node-level mark that Infrahub sets on every node it creates itself, for example the
   creator in the node metadata (`infrahub_sdk` `node/metadata.py`, `node.get_node_metadata()`).
   It covers every kind, so it meets FR-002 directly.
2. If no such mark exists: the read-only `default` Boolean that the `BuiltinIPNamespace`
   generic declares (schema snapshot
   `tests/data/generator_schema_snapshots/netbox_example_schema.json`; SDK protocol
   `IpamNamespace(BuiltinIPNamespace): default`). It is read only for kinds whose schema
   declares that attribute as read-only. It does not depend on a configuration or a list, and
   it covers IP namespaces only. If the probe finds built-in nodes of other mappable kinds,
   the implementation stops and the spec is amended.

The probe result is committed as a fixture and recorded in the decision record. The mark is
read only for the kinds that can carry it, and the destination read part is measured on tier S
before and after the change.

**Probe result (T001, T002, 2026-11-16)**: The node-level mark is chosen. A node that Infrahub
creates itself carries the creator `__system__` (display label `[Infrahub System]`) in its node
metadata, and the adapter reads `node_metadata.created_by.id == "__system__"`.

- The probe ran on a newly initialised Infrahub 1.11.9 of the development stack, with the core
  schema only. It read two node kinds outside `Core*` and `Internal*`: `BuiltinTag` and
  `IpamNamespace`. Fixture: `tests/data/infrahub_builtin_nodes/infrahub-1.11.9.json`, written
  by `tests/integration/test_infrahub_builtin_nodes_integration.py`.
- It found one built-in node: the `IpamNamespace` named `default`. Its creator is `__system__`
  and its `default` attribute is `true`, so both marks agree on it. No built-in node of another
  mappable kind exists, so the spec is not amended.
- A control `BuiltinTag` that the API token created on the same branch carries the creator
  `Admin`, the account of the token.
- A separate check, run after the fixture was written: a `BuiltinTag` created on a branch and
  merged into `main` keeps the creator `Admin` on `main`. A node that an import writes on a
  branch therefore does not take the mark when the branch is merged.
- Not known: whether Infrahub records `__system__` as the creator of other nodes it writes, for
  example during a migration, a generator run or a resource pool allocation. The probe covered
  only a new instance.

**Rationale**: The rule must not depend on a name or on a list kept in the package (FR-002).
The mark must come from Infrahub, the only system that knows which nodes it created.

**Alternatives considered**:

- A list of built-in nodes by kind and name: forbidden by FR-002.
- A "no deletes" flag on a mapping entry: depends on the configuration (FR-002).
- Leaving out the deletes of nodes that no import wrote, using lineage: it would also leave
  out a node that an operator created by hand, which the edge cases of the spec keep as a
  delete.
- Filtering in `select_operations` or recording a kept record: changes the plan report.

## R3. Removing the acceptances by name

**Decision**: Remove the name-based acceptance from:

- `tasks/bench.py:480-492` (`builtin_deletes`), `development/bench/records.py:245`, and
  `tests/test_benchmark_runner.py:173, 418, 483, 614`;
- `tests/data/v2_gate/expected_deletes.yml`: remove the only entry, keep the file and its
  loader in `.github/scripts/nightly_e2e.py:403-420, 582-590`;
- `examples/netbox_to_infrahub/README.md:124` and
  `docs/docs/development/guides/netbox-benchmark-runner.md:153-170`.

ADR 0041 and ADR 0042 describe the delete as a fact of their time. The new decision record
supersedes those statements; the two records get a one-line note that points to it.

## R4. How a plan records an emptied one-peer relationship

**Decision**: `RelationshipReference(cardinality="one", peers=[])` on an update means "this
relationship becomes empty". No new field.

- `infrahub_sync/plan/models.py:104` allows 0 or 1 peers for cardinality one, from plan format
  5 on. Under a reader context with a format below 5, the "exactly one peer" rule stays.
- An operation-level rule refuses an empty one-peer reference on a create, and on a
  reference that is an identity component (`infrahub_sync/plan/models.py:284`,
  `infrahub_sync/plan/derive.py:117-121`, `:463-468`) (FR-007).

**Rationale**: A reference with an empty peer list already means "replace the set with nothing"
for cardinality many (`infrahub_sync/plan/models.py:97-103`). The same meaning for cardinality
one changes the fewest readers. Verify already compares it correctly: `_planned_peer_ids`
(`infrahub_sync/adapters/infrahub.py:2170-2195`) and `_destination_node` (`:2341-2358`) both
give an empty set for it, and `infrahub_sync/plan/destination_check.py:116-134` compares the
two sets (FR-013). The web interface already shows a one-peer reference without peers as "no
peers" (`frontend/src/features/runs/plan-values.ts:128-150`).

**Alternatives considered**:

- A `None` for the relationship in the payload: the payload is reviewed as attributes
  (`infrahub_sync/adapters/infrahub.py:1686-1707`), and the SDK renders it as `{"id": "None"}`.
- A new `cleared` list on the operation: more schema, more readers.

## R5. Plan format 5

**Decision**: `PLAN_FORMAT_VERSION = 5` and `SUPPORTED_FORMAT_VERSIONS = {2, 3, 4, 5}`
(`infrahub_sync/plan/models.py:36-40`). The writer always writes the current version
(`infrahub_sync/plan/writer.py:317`, ADR 0022). Apply keeps its floor at format 4
(`infrahub_sync/potenda/__init__.py:222-245`), so a plan in format 4 is still applied (FR-012).
The checksum already covers `operations.jsonl` in full (`infrahub_sync/plan/checksum.py:3-10`).

Every reader checks the version in one of two places: `infrahub_sync/plan/reader.py:498-521`
and gate check 1 of `infrahub_sync/plan/verify.py:119-160`. Both refuse an unknown version
with a message that names the version found and the supported versions
(`infrahub_sync/plan/reader.py:283`). The service maps the refusal to the recovery action
`compatible_version` (`infrahub_sync/service/flow.py:383-398`). An earlier release therefore
refuses a plan in format 5 without new code; a test proves it (FR-011).

**Alternatives considered**: Writing format 4 when the plan holds no emptied relationship. It
would let an earlier worker apply most plans during an upgrade, but gives two rules. The
deployment runs one release of the API and the worker at a time, so it is not needed.

## R6. Deriving an emptied relationship

**Decision**: In `infrahub_sync/plan/derive.py:662-704`, after `_resolve_references`, an update
gains an empty one-peer reference for each field that meets all of these:

- it is a one-peer reference field of the mapping (`candidates`);
- the source value is `None`;
- it is in `element.get_attrs_diffs()["+"]`, so the destination holds a peer;
- it is not an identifier;
- the destination schema marks it optional;
- the source adapter did not leave the peer out through a configured filter (R7).

`_warn_dropped_cardinality_one_clear` (`infrahub_sync/plan/derive.py:575-604`) is removed
(FR-008). Its condition is not a difference: it warns for any `None` field of an update, even
when the destination is empty too.

**Rationale**: DiffSync puts every attribute in `source_attrs` and only the changed ones in
`get_attrs_diffs()["+"]` (`diffsync/diff.py:281-300`). The destination read omits an empty
one-peer relationship (`infrahub_sync/adapters/infrahub.py:1529-1530`), and the model default is
`None` (`infrahub_sync/runtime_schema/models.py:77-81`). So "the field is in the diff and the
source value is `None`" is exactly "the destination holds a peer that the source does not".

A required one-peer relationship that the source cannot fill already leaves the record out
through the load report (`infrahub_sync/adapters/references.py:56-122`), and apply refuses a
null required relationship (`infrahub_sync/adapters/infrahub.py:1832-1847`). The rule above
never records an emptied required relationship.

**Required relationship emptied in the source (spec FR-013a, clarification of 2026-11-16).** When
the source record holds no peer for a one-peer relationship that the mapping model requires
(`reference_is_required`, `infrahub_sync/adapters/references.py:44-53`), the plan reports a
problem of the new type `required_relationship_empty_in_source` and plans no operation for the
record. The precedent is `required_peer_excluded_by_filter`: a source-side problem raised while
the source is read, which leaves the record out and keeps its destination node, so no delete is
derived for it and its dependents follow the existing rules for records left out
(`infrahub_sync/plan/problems.py:375-430`). The implementation first writes a test of what the
current code does for a source `None` on a required one-peer field (model validation may already
refuse it as `wrong_value_type`), then routes that case to the new type. The new type is added to
`ProblemType` (`infrahub_sync/plan/models.py:803`), which allows additions.

A peer that was left out of the plan already removes the reference from the update
(`infrahub_sync/plan/problems.py:749-778`), so a kept record does not record an emptied
relationship.

## R7. Peers that a configured filter removes

**Finding**: An optional peer that a configured filter removes gives `None` with only a log
warning (`infrahub_sync/adapters/references.py:95-107`), and
`tests/adapters/test_netbox_filtered_references.py:297-341` asserts that the plan leaves that
relationship alone. Without a signal, R6 would record it as emptied.

**Decision**: The shared reference helper records `(owner kind, owner source id, field)` in the
source adapter's read state when it removes a peer through a filter. Derivation reads it with
`getattr(source_adapter, ..., ())` and records no emptied relationship for it. The plan report
does not change. NetBox, Nautobot, ACI and generic REST use the shared helper; Infrahub as a
source does not.

## R8. Writing an emptied relationship

**Decision**: In `apply_planned_operation` (`infrahub_sync/adapters/infrahub.py:1852-1861`):

- An empty one-peer reference adds no entry to the create data (today `peer_ids[0]` would
  raise `IndexError`).
- For an operation that holds an emptied one-peer reference, and only for it, the destination
  id is put in the create data before `client.create`, which marks the node as existing
  through the public SDK interface; then the field is set to `None`. Every other operation
  keeps today's path, and a conformance test proves that the rendered input of an update
  without an emptied relationship does not change. With SDK
  1.23.2 this renders `owner: null` in the Upsert input. Without the assignment, the SDK does
  not render the relationship for an existing node (`infrahub_sdk/node/node.py:474-481`).
- The rendered-input guard (`infrahub_sync/adapters/infrahub.py:1924-1937`) is extended: the
  write is refused unless each emptied field renders as `null` and no relationship outside the
  operation's fields renders. This catches an SDK version in the allowed range
  (`infrahub-sdk>=1.18`) that renders `null` for every uninitialized one-peer relationship
  (`tests/plan/test_apply_conformance.py:71-83`).
- A live integration test, modelled on
  `tests/integration/test_infrahub_replace_set_shrink_integration.py`, sets a one-peer
  relationship, applies the emptied relationship and reads it back.

**Rationale**: The Upsert stays the only destination write of an operation (ADR 0010, ADR 0012).
A second SDK `update()` call would add a write and move the resume position.

**Live result (T003, 2026-11-16)**: Infrahub 1.11.9 removes the edge on `rel: null` in an
Upsert. `tests/integration/test_infrahub_one_peer_clear_integration.py` renders exactly
`{"id": <destination id>, "name": {"value": ...}, "lead": null}` with SDK 1.23.2, saves it with
`allow_upsert=True` and reads the node back with no `lead` peer.

## R9. The second-plan tests

**Decision**:

- `tests/plan/test_second_plan.py` builds on `plan_fixture` of `tests/plan/problem_plan.py:356`
  (the real `Potenda` plan path, a fixture source, an `InfrahubAdapter` with a stand-in
  client) and a new `ApplyingDestination` stand-in. The stand-in implements
  `new_peer_resolver` and `apply_planned_operation`, keeps model data per kind, applies the
  payload and the references (`peers: []` gives `None`), and returns the destination data for
  the next `plan_fixture`. The second plan therefore depends on what apply wrote, not on the
  source records (`tests/plan/test_replan_after_stopped_apply.py:91` builds it from the source
  records, which would hide an apply defect).
- New fixture data under `tests/data/second_plan/`: `InterfaceLag` with an
  `InterfacePhysical.bundle` one-peer relationship, prefixes and IP addresses with no VRF (the
  `default` namespace as a node that only the destination holds, see
  `tests/plan/test_destination_only_peer.py:485-584`), and a many relationship for tagged VLANs.
  The variant with records left out and kept records reuses `Variant.ALL` of
  `tests/data/plan_problems/`.
- The fixture destination overrides `infrahub_node_to_diffsync`, so it cannot catch #167 (a
  `PeerIdentifierError` for `InterfacePhysical.bundle` when a shallow `InterfaceLag` peer misses
  `device`). One more test runs the real converter on shallow LAG peers, with the fakes of
  `tests/adapters/test_infrahub_peer_identifier.py`. The V3 path now hydrates a peer by id when
  identifiers are missing (`infrahub_sync/adapters/infrahub.py:1390-1424`), so the defect is
  probably fixed; the test decides.
- The "same data planned three times" test compares the operations digest (R16) and the plan
  report, not the plan bytes, because the bytes hold the run identity.
- FR-023: NetBox, Nautobot and generic REST all sort peers through `peer_unique_ids`
  (`infrahub_sync/adapters/references.py:184`), and derivation sorts again
  (`infrahub_sync/plan/derive.py:328`). One test per adapter feeds reversed peer lists through
  its `record_to_data` and compares the operations.

## R9a. What "zero operations" means

**Finding**: `SavedPlan.summary` gives `total = len(operations)`
(`infrahub_sync/plan/review.py:113-140`); the counts of the plan report come from
`PlanReport.summary_counts()`. The CLI prints `operations: <total>` (`infrahub_sync/cli.py:1208`)
and then the report counts (`infrahub_sync/cli.py:1147-1161`). FR-025 already holds in the code.

**Decision**: No code change for FR-025. FR-027 is a paragraph in `docs/docs/running-a-sync.mdx`,
sections "Review the plan" and "Review the problems and the left-out records", and one
sentence in the `runs plan` help text, from which `docs/docs/reference/cli.mdx` is generated.

## R10. Where each part of the plan step begins and ends

**Decision**: The seven parts of FR-014 have these bounds:

| Part | Starts | Ends |
| --- | --- | --- |
| Waiting for a worker | `submitted_at` of the execution link (`infrahub_sync/service/service.py:1548-1554`) | `claimed_at`, written by `claim_execution` (`infrahub_sync/product_store/store.py:1583-1610`), called from `infrahub_sync/service/flow.py:1483` |
| Preparing | `claimed_at` | The earlier `started_at` of the two reads |
| Reading the source, reading the destination | `_load_side` (`infrahub_sync/potenda/__init__.py:731`) | `:748` on success, `:740` on failure |
| Comparing | The later `ended_at` of the two reads | The start of `run_problem_pass` (`infrahub_sync/potenda/__init__.py:984`) |
| Checking the plan | The start of `run_problem_pass` | The end of `run_problem_pass` (`:992`) |
| Saving | `write_artifact` (`infrahub_sync/potenda/__init__.py:1001`) | The end of `_publish_plan` (`infrahub_sync/service/flow.py:840` for `plan`, `:999` for `sync`) |

- The two reads run at the same time: `execute_run(concurrent_load=True)`
  (`infrahub_sync/execution.py:1147`) and a two-thread pool in `Potenda.load_both_sides`
  (`infrahub_sync/potenda/__init__.py:826-833`). Their durations overlap.
- Preparing holds the live destination-schema read (`infrahub_sync/service/flow.py:619-622`),
  the construction of the adapters (`infrahub_sync/execution.py:1246-1268`) and the lineage
  and metadata reads (`infrahub_sync/potenda/__init__.py:817-818`). For a `sync`, it also holds
  the wait for the write guard (`infrahub_sync/service/flow.py:971`).
- Comparing holds `ptd.diff()`, the legacy `plan.parquet` write and the derivation of the
  operations and of the deletes (`infrahub_sync/potenda/__init__.py:842-980`).
- Saving holds the plan artifact write, the read back, the `run.json` save, the internal
  checkpoint bundle and the public plan review (`infrahub_sync/service/flow.py:319-334`,
  `:833`). `GET /runs/{id}/plan` serves that review (`infrahub_sync/service/service.py:1440-1443`),
  so its publication is the moment "the run shows its plan".

**Rationale**: `run_problem_pass` (`infrahub_sync/plan/problems.py:839`) is the only
substantial check on the derived plan before it is saved. The writer's own refusals
(`infrahub_sync/plan/writer.py:291-292`, `:348`) take microseconds, so a "checking" part made
of them alone would always read zero.

**Alternatives considered**: Checking as the writer's refusals only, rejected for the reason
above. Ending saving at the terminal commit, rejected because a `sync` commits after apply.

## R11. How the timings are stored

**Decision**: One nullable TEXT column `plan_step` on `prefect_executions`, holding one JSON
document with the parts `preparing`, `comparing`, `checking` and `saving`, each with
`started_at` and `ended_at`, and a `running` key that names the part that runs. The column is
added at start-up by `_migrate_prefect_execution_columns` (`infrahub_sync/product_store/store.py:1007-1016`),
the mechanism that added `source_read` and `destination_read`. Old rows read `NULL`.

- The document is checked against a fixed list of keys and part names before the write, as
  `_check_side_read_document` does (`infrahub_sync/product_store/store.py:4663-4709`). It
  holds no error message and no data value (FR-021).
- The write uses `WHERE ... terminal_at IS NULL`, as `record_side_reads` does
  (`infrahub_sync/product_store/store.py:1612-1630`). A worker that writes after the verdict
  is refused.
- The reads are not stored a second time (FR-015). Waiting is not stored: it is derived from
  `submitted_at` and `claimed_at`. The start of preparing is `claimed_at`, and its end is the
  earlier read start, so preparing stores only what the reads do not give: nothing. The
  worker writes the three other parts and the end of saving.

**Rationale**: The four parts run one after the other on one thread, after the reads. One
document rewritten on each change is enough. The reads keep their own columns because they run
on two threads (archived spec 013, research R3).

**Alternatives considered**: A separate table, rejected because a few fields on the existing
row are enough. Run events per part, rejected because no event marks a part boundary
(`infrahub_sync/product_store/models.py:730-759`) and an event is saved in the transaction of
an API decision (ADR 0033). Deriving the parts from artifact `created_at` and `terminal_at`,
as the web interface estimates "compare" today (`frontend/src/domain/plan-reads.ts:46-52`),
rejected because it cannot split comparing, checking and saving, and is wrong for a `sync`.

## R12. How the engine reports a part

**Decision**: A second optional observer protocol in `infrahub_sync/potenda/`, with
`part_started(part)` and `part_ended(part)`, set through a private `execute_run` keyword
`_plan_step_observer`, after the engine factory, as `_read_observer` is
(`infrahub_sync/execution.py:1268`, ADR 0035). Potenda calls it around the comparison, the
problem pass and the artifact write. The worker's observer closes saving after
`_publish_plan`. An observer error is logged by its class name and never changes the plan.

**Rationale**: It follows the read observer that ADR 0035 chose, so adapters stay unchanged.

**Alternatives considered**: Timing calls in each adapter, rejected as ADR 0035 rejected them
for reads.

## R13. Failure, cancellation and worker loss

**Decision**: The response returns, for the part that was running, a derived `stopped_at`:

- The worker writes the end of the running part before its failure commit
  (`infrahub_sync/service/flow.py:1546-1559`).
- When the store has set a verdict (failure, cancellation, liveness `interrupted` or
  `abandoned`: `infrahub_sync/product_store/store.py:1720`, `:1735`, `:2155`), `stopped_at` is
  `terminal_at`.
- When Prefect reports that the run ended and no verdict exists yet, the part is returned as
  stopped with no stop time.
- A completed execution whose document still names a running part returns no entry for that
  part, as `stage_reads` does for a read (`infrahub_sync/service/read_progress.py:143-162`).

**Rationale**: FR-016 requires a stop time for the part that was running. The read state
returns an interrupted read with `ended_at=None` (`infrahub_sync/service/read_progress.py:140`);
the parts follow the same derivation and add `stopped_at`.

## R14. The run response and `runs show`

**Decision**:

- `OrchestrationSummary` gains `plan_step: PlanStepTimings | None = None`, with `running` and
  a mapping of part names to `PlanStepPart(started_at, ended_at, stopped_at)`. Part names are
  plain strings, as `SideRead.state` is, so a later part does not fail an older client
  (`infrahub_sync/client/models.py:111-112`, `extra="allow"`).
- Waiting is returned in `plan_step` only when the document exists, and only when
  `claimed_at` is not before `submitted_at`. Old rows fall back to `last_observed_at` for
  `submitted_at` (`infrahub_sync/product_store/store.py:4838`), so deriving waiting for them
  would invent a value.
- `runs show` prints one line per part after the two read lines, because a test pins the read
  lines right after `prefect_flow_run_url` (`tests/cli/test_client_commands.py:876-890`).
- FR-020: no field-level stability marker exists today; the API is marked `v3-unstable` as a
  whole (`docs/docs/reference/sync-http-api.mdx:225-229`). Each reference gets one sentence that
  marks the timings as subject to change: the hand-written `sync-http-api.mdx`, and the
  `runs_show` docstring, from which `uv run invoke docs.generate` builds `cli.mdx`.
- The web interface is out of scope. New optional fields leave its generated types valid.

## R15. Benchmark records

**Decision**:

- The v3 benchmark measures a confirmed `sync` (`tasks/bench.py:437-502`). New fields on
  `ResultRecord` (`development/bench/records.py:224-253`):
  - `plan_parts`: seconds per part, read from the latest `sync` entry's `plan_step` and
    `reads`. A part without times is left out, never 0.
  - `source_read_mode` and `destination_read_mode`, from `reads`. The service path reads in
    full today (`infrahub_sync/execution.py:1148`, `infrahub_sync/service/flow.py:445-452`).
  - `plan_bytes`: the `size` of the public `saved-plan-review` artifact
    (`infrahub_sync/client/models.py:235`), already in `run.artifact_refs`. The results page
    states that it measures the review document, not the internal bundle.
  - `operations_digest` (R16).
- `plan_seconds` keeps its definition: from the start of the run to the publication of the
  plan.
- The `builtin_deletes` field and the exclusion by name (`tasks/bench.py:480-494`) are removed
  (FR-004).
- The `warm` check requires `plan.summary.total == 0` (FR-030). Today a `warm` plan with a
  delete that is not built in passes, because deletes are cleared before the check
  (`tasks/bench.py:493`).
- A `sync` whose plan leaves records out stops at `planned` with a completed verdict
  (`infrahub_sync/service/flow.py:1061-1098`), and its results summary is the plan summary.
  The runner compares it with `{create, update, delete}` and fails (`tasks/bench.py:475`).
  The runner handles this case: it reports the counts of records left out and kept, and
  compares the apply summary only when an apply ran (FR-029).
- The runner does not read the plan format: the service serves every format it supports through
  the same `PlanResource` (`infrahub_sync/client/models.py:754-772`).

**Alternatives considered**: The size of the internal bundle, rejected because it is never
served and would need a new public field for a developer-only need.

## R16. Operations digest

**Decision**: A shared helper `infrahub_sync/plan/digest.py::operations_digest(operations)`:
for each operation, `canonical_json_bytes` (`infrahub_sync/plan/canonical.py:81`) of
`{action, kind, identity, payload, relationships}`, without `destination_id`; sort the lines;
take the SHA-256. The benchmark computes it from `PlanResource.operations`, and the tests of
FR-022 call the same helper.

**Rationale**: No existing value fits:

- The ADR 0007 fingerprint (`infrahub_sync/cache/fingerprint.py:35-61`) hashes the legacy
  `plan.parquet` in the worker's temporary directory, which is gone after the stage
  (`infrahub_sync/service/flow.py:1495-1499`).
- `plan_checksum` covers the destination binding, the lineage, the source snapshot digests and
  `destination_id` (`infrahub_sync/plan/checksum.py:59-67`, `infrahub_sync/plan/models.py:124-130`),
  which differ on each rebuilt Infrahub.

**Risk**: If a payload holds an Infrahub ID, the digest differs between repetitions. The
"same data planned three times" test of FR-022 proves it on stand-in data; the benchmark
proves it on tier data.

## R17. Scenarios and results page

**Decision**:

- "About 100 changes" is the `changed` scenario at tier M: 1% of the tier, 102 changes, split
  70/20/10 between updates, creates and deletes (`development/netbox/datasets/change_netbox.py:171-228`,
  `docs/docs/development/guides/netbox-benchmark-tiers.md:36`). Tier S makes 6 changes. A VRF
  move of a prefix becomes a delete and a create, so the number of operations can be above
  102.
- The results page is `docs/docs/development/guides/netbox-benchmark-results.md`, linked from
  the runner guide, the tiers guide and `docs/docs/development/guides/index.md`, and added to the
  docs sidebar and to the page list of `tests/test_developer_pages_sidebar.py:68-73`.
- `tests/test_benchmark_runner.py` runs in the unit tier and gains tests for the new fields,
  the digest, the `warm` check and the left-out path.

## Facts not known at the time of writing

- Whether the benchmark runner passes against the current stack. It needs a live run.
- Whether the plan of tier M fits the 16 MiB response limit and the 64 MiB bundle limit.
- Whether tier data produces records left out.
- The clock skew between the API container and the worker container.
