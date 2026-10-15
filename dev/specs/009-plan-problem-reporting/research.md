# Research: Plan problem reporting

Each section states a decision, why it was chosen from the code at commit `4646b716`, and the
alternatives that were rejected. File references are to that commit. Sizes and timings come
from `infrahub-sync-process/product/metrics.md`; no new measurement was run (product owner
constraint, 2026-10-15).

This is the second plan. The product owner rejected two parts of the first one on 2026-10-15:

- It changed what DiffSync compares: it replaced `self.add(...)` with an `admit_record` call at
  each adapter call site, emptied relationship fields at load time for kept records, and removed
  left-out records from the source store, so that DiffSync computed a diff that was then
  corrected after derivation (first plan R2, R4).
- It masked credential values of any length by search and replace in messages, plan files and
  logs (first plan R11).

R2 to R8 replace the first design. R13 and R14 replace the masking.

## R1. Where each of the six problem types is found today

| Problem type (stable name) | Where it stops a plan today | Where this plan finds it |
| --- | --- | --- |
| `dropdown_value_not_in_choices` | Not found. Infrahub rejects the write during apply (#370, #371). A Dropdown is typed `str` (`runtime_schema/models.py:46`), so the model accepts any text | Problem pass, against the choices in the destination schema record (R6) |
| `duplicate_source_identity` | `self.add(item)` raises DiffSync's `ObjectAlreadyExists` (`adapters/netbox.py:224`, `nautobot.py:254`, `aci.py:287`, `genericrestapi.py:145`, `prometheus.py:511`, `slurpitsync.py:281`; raised by `diffsync/__init__.py:806-815`) | Load: the record admission step of the base class (R3) |
| `destination_identity_collision` | `refuse_destination_identity_collisions` raises (`plan/keying.py:523`), called from `warn_missing_convergence_key` (`plan/derive.py:908`) | Problem pass, through the same check changed to return the groups (R8) |
| `wrong_value_type` | Pydantic refuses the model at `model(**data)`, for example a rack position of `2.5` for an integer (`adapters/netbox.py:223`, #374) | Load: the record admission step (R3) |
| `required_peer_excluded_by_filter` | `ValueError("Configured filter excluded all peers for required relationship …")` (`adapters/netbox.py:270-275`, `306-312`) | Load: the record admission step, through a typed exception of the base class (R4) |
| `identities_merge_in_destination` | A warning only (`_warn_identity_finer_than_destination_key`, `plan/derive.py:791`; #166) and `refuse_source_identity_collisions` (`plan/keying.py:67`, called at `plan/derive.py:931`) | Problem pass, through the same checks changed to return the groups (R8) |

Today every load failure ends the whole load: `_load_side` wraps it into one `ValueError`
(`potenda/__init__.py:488-501`).

## R2. Plan steps, and how DiffSync's view stays the source data as loaded

Decision: a plan runs these steps in this order. Only step 1 builds the stores; no later step
changes them.

1. **Load.** Each adapter loads its side. A record that cannot be built (R3) is not added to the
   store, because there is no valid model to add. It is recorded in the adapter's load report.
   Every other record is added as the source holds it, with every reference it holds (R4).
2. **Compare.** `self.destination.diff_from(self.source, …)` (`potenda/__init__.py:553`),
   unchanged. It compares the stores as loaded.
3. **Derive.** `operations_from_diff` and `derive_deletes` (`potenda/__init__.py:655-680`) derive
   one operation per difference, for every record, including records with problems. The only
   change: a reference to a record that the load report names resolves to a placeholder instead
   of raising `SourcePeerUnresolvedError` (R7).
4. **Find problems.** A new problem pass (`infrahub_sync/plan/problems.py`) reads the load
   reports, the source store, the destination schema record and the derived operations. It runs
   the Dropdown check, the identity checks and the dependency walk, and returns a plan report:
   problems, left-out records and kept records. It reads; it changes nothing.
5. **Select operations.** A separate, pure function `select_operations(operations, report)`
   applies the report to the derived operations: it removes the operations of left-out records,
   removes the deletes that name a record the load could not build, and removes from a kept
   record's operation the relationship to a left-out peer. Every placeholder of step 3 is gone
   after this step; the plan writer refuses an operation that still holds one.
6. **Check and write.** `warn_missing_convergence_key` (`plan/derive.py:888`) runs on the
   selected operations, then the plan writer writes the operations and the report (R9).

What keeps DiffSync's view intact:

- The stores are not changed after step 1. A test takes a canonical dump of both stores before
  step 2 and after step 5 and asserts that they are equal.
- The diff that step 2 returns is not edited. Left-out and kept decisions are applied in step 5,
  to the plan's operations, and step 5 is the only code that applies them.
- No relationship field is emptied at load time to obtain a decision. A kept record is loaded
  with its reference to the left-out record, so DiffSync compares the real value.
- A record that the load could not build is absent from the source store, so DiffSync reports
  its Infrahub node as a delete. That is correct for the data as loaded. Step 5 records it as
  left out instead of as a delete not executed (ADR 0004: deletes are recorded, never executed).
- Models declare no `_children`. The runtime model builder emits none
  (`runtime_schema/models.py:11`), and derivation refuses a diff element with children
  (`_refuse_child_elements`, `plan/derive.py:387-415`). V3 calls only `diff_from`
  (`potenda/__init__.py:553`), never `sync_from` or `sync_to`. A test asserts both: no
  runtime model has a `_children` mapping, and `Potenda` has no call to a DiffSync sync method.

Rationale: the problem types need data from three moments: the load (a record that cannot be
built), the stores (Dropdown values, references) and the derived operations (identity
collisions are defined over creates, `plan/keying.py:523`). Running the checks after derivation
lets each use the data where it exists, without changing what DiffSync compares.

Alternatives considered:

- Remove left-out records from the stores before the diff (first plan). Rejected by the product
  owner: DiffSync then proposes deletes and clears relationships that are not true of the source.
- Run the whole problem pass before the diff. Rejected: the identity checks work on derived
  creates and would have to repeat derivation.

## R3. One admission step for records that cannot be loaded

Which hook point catches which error, verified against the code:

- `DiffSync.Adapter.add` (`diffsync/__init__.py:806-815`, diffsync 2.2.3) calls
  `self.store.add(obj=obj)`, which raises `ObjectAlreadyExists` with `existing_object`
  (`diffsync/exceptions.py:45-51`). Overriding `add` in `DiffSyncMixin`
  (`infrahub_sync/__init__.py:300`) would catch duplicates for every adapter.
- `add` never sees a pydantic `ValidationError`. The adapter raises it at `model(**data)`, one
  line before `self.add(item)` (`adapters/netbox.py:223-224`), so the exception leaves the
  adapter's loop before `add` is reached.
- Model construction cannot absorb it either. A wrap model validator on the model base that
  returns a substitute instance was tried with pydantic 2.12.5: pydantic warns "A custom
  validator is returning a value other than `self`" and discards the returned object, leaving an
  empty instance. There is no supported way for `model(**data)` to return a "not loaded" result.
- `DiffSyncMixin.load()` (`infrahub_sync/__init__.py:305-313`) sees the error only after the
  adapter's `model_loader` loop has ended, so the rest of that kind would be lost.

Decision: the base class owns the per-record loop. `DiffSyncMixin.model_loader` gets a default
implementation that every adapter inherits:

1. find the mapping entry for the kind;
2. call the adapter's `list_records(model_name, element)` to fetch raw records;
3. on the source side, apply `model.filter_records` and record the IDs a filter removed (the
   NetBox bookkeeping of `adapters/netbox.py:29-59, 226-227` moves here), then
   `model.transform_records`;
4. for each record, call `load_record(model, element, record)`.

`load_record` is the one admission step. It calls the adapter's
`record_to_data(record, element, model)`, builds `model(**data)` and calls `self.add(item)`, and
on the source side catches exactly these exceptions per record:

| Exception | Recorded as |
| --- | --- |
| `pydantic.ValidationError` whose errors are all type errors | `wrong_value_type`, with the field, the error type and the value (from `errors(include_url=False)`) |
| `diffsync.exceptions.ObjectAlreadyExists` | `duplicate_source_identity`, naming the record already in the store (`existing_object`); the store keeps that first record unchanged |
| `ExcludedRequiredPeerError` (new, base class) | `required_peer_excluded_by_filter`, with the field and the excluded peer ID |
| `PeerNotLoadedError` (new, base class) | a dependent left-out record, naming the peer (R4) |

Any other exception, including a `ValidationError` that holds a non-type error such as a
missing required value, is raised again as `RecordLoadError` with the kind, the record ID and
the field, and fails the plan (FR-018). On the destination side nothing is caught: a bad
Infrahub record is not a source problem.

The load report holds, for each record not loaded: the kind, the source record ID
(`local_id`), the identity when it can be computed (`model.create_unique_id(**identifiers)`
when every identifier field is in `data`), and the structured fields above. It holds no error
text.

Adapter changes, which remove each adapter's own loop rather than adding a call to it:

| Adapter | `list_records` from | `record_to_data` from |
| --- | --- | --- |
| NetBox | `model_loader`, `adapters/netbox.py:182-227` | `netbox_obj_to_diffsync`, `:229` |
| Nautobot | `model_loader`, `adapters/nautobot.py:208-254` | `nautobot_obj_to_diffsync`, `:256` |
| ACI | `model_loader`, `adapters/aci.py:240-287` | `obj_to_diffsync`, `:289` |
| Generic REST (and Peering Manager, its subclass) | `model_loader`, `adapters/genericrestapi.py:100-145` | `obj_to_diffsync`, `:179` |
| Prometheus | `model_loader`, `adapters/prometheus.py:466-511`; the `lookup` injection stays in `list_records` | `obj_to_diffsync`, `:513` |
| Slurp'it | `model_loader`, `adapters/slurpitsync.py:231-281`; its `load()` override that closes the client (`:104-109`) stays | `slurpit_obj_to_diffsync`, `:288` |
| IP Fabric | `model_loader`, `adapters/ipfabricsync.py:76-105` | `ipfabric_dict_to_diffsync`, `:107` |

IP Fabric today calls `update_or_add_model_instance` (`adapters/ipfabricsync.py:105`), so a
duplicate overwrites the first record silently. Through `load_record` it calls `add`, as the
documented contract says (`docs/docs/development/knowledge/adapter-anatomy.md:52`), and a
duplicate becomes a problem.

The Infrahub adapter keeps its own `model_loader` (`adapters/infrahub.py:825-854`), because it
also fills the SDK store with each node and hydrates peers. It calls `load_record` for each
record, with a hook `after_add(item, record)` for the SDK store. It is the one adapter that
calls `load_record` itself. Its peer hydration (`:956-957`) stays on `update_or_add_model_instance`,
because the same node can be added by a peer first; duplicates from an Infrahub source are
therefore found only when the same record appears twice in one kind's records.

The engine builds models in two other places, both for incremental loads: the snapshot rows
(`potenda/__init__.py:442-444`) and the changed rows (`cache/incremental.py:219-220`). Both call
`load_record` with an identity conversion. Spec 010 owns incremental loads; R17 passes it the
rule that a plan after one that left out records reads the source in full.

Custom adapters (INFP-654, 3.0) implement `list_records` and `record_to_data` and get the same
admission with no code of their own. An adapter that overrides `model_loader` and calls
`self.add(model(**data))` itself opts out of it; the adapter anatomy page and the
writing-an-adapter guideline say so.

Rationale: every bundled loader already has the same shape (mapping lookup, fetch, filter,
transform, convert, build, add), so the base class can own it. One loop in one class is the
only place where a per-record catch can continue with the next record.

Alternatives considered:

- `admit_record` at each adapter call site (first plan). Rejected by the product owner: the
  behaviour depends on each adapter calling it.
- Override `add` only. Rejected: it does not see `ValidationError` (above).
- A model validator that turns a failure into a flagged instance. Rejected: pydantic discards
  the substitute (above).

## R4. References to records that were not loaded or were excluded by a filter

Decision: the base class resolves source references for the adapters that resolve references
while loading (NetBox `adapters/netbox.py:250-312`, Nautobot `nautobot.py:276-300`, ACI
`aci.py:312-367`, generic REST `genericrestapi.py:209-234`, Slurp'it `slurpitsync.py:308`). Each
replaces its scan of `self.store.get_all(...)` by `local_id` with one call to
`self.peer_unique_id(peer_kind, peer_source_id, owner=…, field=…, required=…)`:

| The peer is | Result |
| --- | --- |
| in the store | its unique ID, from an index by kind and `local_id` |
| in the load report with a known identity | that identity. The referring record loads with the real reference, and the problem pass decides whether it is left out or kept (R7) |
| in the load report without a known identity | `PeerNotLoadedError`: the referring record is left out as a dependent, whether the relationship is required or optional, because its reference cannot be stated |
| removed by a configured filter, relationship required | `ExcludedRequiredPeerError` (the required field is `str` with no default, `runtime_schema/models.py:77-81`, so the record cannot be built) |
| removed by a configured filter, relationship optional | no value, as today (`adapters/netbox.py:258-271`): a filter decides what the source holds, so this is not a left-out record |
| none of these | `PeerNotFoundError`, which fails the plan with the kind, record ID, field and peer ID |

Kinds load in dependency order (`SyncConfig.compute_order_and_tiers`,
`infrahub_sync/__init__.py:187`), so a peer's load result is known before its referrers load.

Behaviour change: Nautobot today logs "Unable to locate the node" and drops the reference
(`adapters/nautobot.py:286`, `:300`), so DiffSync compares a relationship the source does not
have. Under the shared resolution it fails the plan, as NetBox, ACI and generic REST do today
(`netbox.py:276-277`, `aci.py:332-333`, `genericrestapi.py:217-218`).

## R5. One mechanism instead of `continue_on_error`

Decision: the `continue_on_error` parameter is removed from the engine and the adapters. The
admission step of R3 replaces its record-skipping branches, and every record it leaves out is
counted in the plan report.

Today:

- `execution.py:108`, `:1133` and `:1232` and `utils.py:208`, `:313` pass the flag to `Potenda`,
  which sets it on both adapters (`potenda/__init__.py:256-262`, `309-312`).
- Nautobot (`adapters/nautobot.py:236-253`) and Infrahub (`adapters/infrahub.py:839-850`) skip a
  record whose model fails validation, with a warning and no count.
- Infrahub skips a peer relationship whose peer is missing identifier values
  (`adapters/infrahub.py:930-951`, `PeerIdentifierError`).
- V3 never sets it: the CLI option is retired (`tests/cli/test_parity_and_closure.py:16-19`), and
  remote runs pass `False` (`tests/test_execution_surface.py:1479`).

After this change, the two record-skipping branches are deleted; their case becomes
`wrong_value_type` through admission. The Infrahub peer-identifier branch keeps raising
`PeerIdentifierError`, which fails the plan, as every V3 run does today. The tests that set the
flag (`tests/adapters/test_infrahub_peer_identifier.py`, `test_infrahub_loader_fields.py`,
`test_infrahub_incremental.py`) are changed to the admission behaviour.

Rationale: two ways of skipping records, one silent and one counted, would let a plan leave out
records that its report does not list.

## R6. The Dropdown check needs the choices in the destination schema record

Decision: the schema read keeps the names of a Dropdown attribute's choices.

- `_attribute_shape` (`configuration/capabilities.py:344`) adds `"choices": [names]` for an
  attribute of kind `Dropdown`.
- `NormalizedAttribute` (`runtime_schema/domain.py:29`) adds `choices: tuple[str, ...] | None`.
- `_member_semantics` (`runtime_schema/projection.py:38`) adds `choices` for a mapped Dropdown
  attribute, which changes the consumed-schema fingerprint of every configuration that maps one.
- The problem pass checks every mapped Dropdown value in the source store against the choices.
  A `None` value on an optional attribute is not checked.

Rationale: the spec accepts that plans saved by an earlier release must be planned again (#371).
`PlanSchemaChangedError` already refuses an apply whose recorded fingerprint differs from the
live one, so a change to the choices in Infrahub also invalidates a reviewed plan.

Alternative considered: read the choices only during the check. Rejected: a change to the
choices would then not invalidate a reviewed plan.

## R7. The problem pass, the dependency walk and the selection step

Decision, in `infrahub_sync/plan/problems.py`:

1. Problems: from the load reports (R3, R4), the Dropdown check (R6) and the identity checks
   (R8).
2. Dependency walk: starting from every record with a problem and every record the load could
   not build, the pass walks the source store's reference fields (the mapping's
   `field.reference` entries) to find the records that refer to them, and repeats until no new
   record is found. A reference through a relationship that the destination schema marks
   `optional: false`, or that is a component of the destination's human-friendly ID, leaves the
   referring record out, naming the record it depends on. A reference through an optional
   relationship keeps the record and lists it as kept, with the relationship and the left-out
   record. A record with a problem and a left-out peer is listed once, with its problem.
3. Selection (`select_operations`):
   - removes every create and update of a left-out record;
   - removes every delete whose identity is a record of the load report or a left-out record,
     so a left-out record that exists in Infrahub stays as it is (FR-008);
   - for a kept record, removes the relationship to the left-out peer from its operation. On a
     create, the relationship is absent (ADR 0001: an absent `relationships` entry carries no
     value); for a relationship of cardinality many, the other peers stay. On an update, the
     relationship is removed from the operation, so the destination keeps its current value,
     as `_warn_dropped_cardinality_one_clear` already does for a null peer
     (`plan/derive.py:558-587`). For cardinality many on an update, the whole relationship is
     removed, because a replace-set write (ADR 0012) of the other peers would remove the
     left-out peer from Infrahub (spec 010, edge case on relationships with several peers). An
     update whose only change was that relationship is removed.

In step 3 of R2, a reference to a record of the load report resolves to a placeholder pair
marked as left out, instead of raising `SourcePeerUnresolvedError` (`plan/derive.py:272-338`
already has `allow_missing` for a peer absent from the store). Step 5 removes every placeholder.

Rationale: the product owner decided on 2026-10-15 that an optional reference keeps the record
(FR-005). Applying the decisions in one pure function after derivation keeps `operations_from_diff`
and `derive_deletes` unchanged except for the placeholder, so ADR 0001's operation identifiers
and checksum rules are unchanged.

## R8. Duplicates and identity collisions leave out every record involved

Decision: for `duplicate_source_identity`, the record kept in the store and every refused
record are left out. `refuse_destination_identity_collisions` (`plan/keying.py:523-557`) and
`refuse_source_identity_collisions` (`plan/keying.py:67-122`) are split into a function that
returns the groups and a function that raises when the groups are not empty. The problem pass
calls the first on the derived operations; `warn_missing_convergence_key` keeps calling the
second on the selected operations, where it finds nothing, because every record of a group is
left out. `_warn_identity_finer_than_destination_key` (`plan/derive.py:791-837`) returns its
merge groups to the problem pass instead of only logging a warning.

Rationale: the spec assumption says the plan cannot tell which record is correct.

## R9. The plan report and its size

Decision: the problems, left-out records and kept records are written to a third file in the
plan directory, `plan/report.jsonl`, in the canonical encoding of ADR 0001, one entry per line,
in a stable order. The manifest records its SHA-256 digest, its size and the counts, so the plan
checksum covers it (R10).

Size, estimated from `metrics.md`, not measured: the tier L first-sync plan is 69,757,845 bytes
for 87,815 objects ("A v3 sync of tier L cannot complete"), about 794 bytes per object, so a
tier M plan (10,207 objects) is about 8.1 MB, below the 16 MiB client limit. A report entry is
at most about 1 KB (reason limited to 512 characters), so a report where every tier M record has
a problem is about 10 MB. The report is returned in pages (contracts/sync-api.md), so it does
not reach the client limit.

Planning time: the added steps run in memory over data already loaded. The tier M first-sync
plan step takes 4 min 32 s to 4 min 33 s (`metrics.md`, "Where the v3 time goes"). Whether the
new steps add measurable time is not known; they run inside the part that spec 010 FR-009 names
"checking the plan before it is saved", which spec 010 times.

## R10. Decision record 0022 (draft): saved plan format 4

Name: `dev/adr/0022-saved-plan-format-4-records-left-out-records.md`.

> **Status**: Proposed. **Date**: 2026-10-15. **Source**: `dev/specs/009-plan-problem-reporting`.
>
> **Context.** A plan can now leave out records (ADR 0020). Apply must then require the person
> to confirm the left-out count. ADR 0001 lets a later outcome add a manifest key without a
> format bump, because unknown keys are tolerated on read. For this key that is the wrong
> posture: a reader that does not know the key would apply the plan without the confirmation.
>
> **Decision.** The plan format becomes 4.
>
> - The plan directory holds a third file, `report.jsonl`, written after `operations.jsonl` and
>   before `manifest.json`, in the same canonical encoding. `manifest.json` stays the commit
>   point.
> - The manifest gains a `report` object: `digest`, `size`, `problems`, `left_out`,
>   `left_out_with_problem`, `left_out_dependent`, `kept`. The checksum rule of ADR 0001 is
>   unchanged and covers the report through the digest.
> - `PlannedOperation` is unchanged, so operation identifiers and their bytes are unchanged.
> - The report holds source data, schema names and fixed reason templates. No entry is built
>   from connection settings or from the text of an error (ADR 0021).
> - Reading and review accept 2, 3 and 4. Apply accepts 4 alone, and refuses 3 with
>   `PlanFormatApplyUnsupportedError` and the instruction to plan again
>   (`potenda/__init__.py:148-163`).
> - Apply compares the confirmed count with `report.left_out` before the first ownership proof,
>   so a refusal writes nothing.
>
> **Consequences.** Every plan saved before this release must be planned again before it is
> applied. An older Sync release refuses a format-4 plan instead of applying it without the
> confirmation.
>
> **Alternatives considered.** An additive manifest key with no bump: an older reader would
> ignore the confirmation. The report inside `operations.jsonl`: it would change the bytes and
> the meaning of the file every consumer reads as operations.

## R11. Decision record 0020 (draft): records with problems are left out at the plan stage

Name: `dev/adr/0020-records-with-problems-are-left-out-of-a-plan.md`. It records the
engineering side of the AD-12 amendment (FR-029). The product amendment is a change to
`infrahub-sync-process/product/decisions.md`, outside this repository; its text is drafted
below for the product owner.

> **Status**: Proposed, needs product sign-off (spec 009 FR-029). **Date**: 2026-10-15.
>
> **Context.** A plan stops at the first bad source record (#369). AD-12 says failed
> dependencies are visible and block dependent work, and leaves the per-object policy open.
> `plan/derive.py:36-39` (AD047) states that a derivation failure fails the command and that
> warn-and-drop would emit a silently incomplete plan. Two adapters already skip bad records
> without a count when `continue_on_error` is set.
>
> **Decision.**
>
> - At the plan stage, a record with one of six named problems, and every record that depends
>   on it through a required relationship, is left out. A record that refers to it only through
>   an optional relationship is kept without that relationship.
> - DiffSync compares the stores as loaded. A record that cannot be built is not loaded; every
>   other record is loaded as the source holds it, references included. The adapter base class
>   owns the per-record loop and is the one place that catches a record that cannot be built.
> - Problems are found after derivation, by a pass that reads the stores and the operations.
>   The left-out and kept decisions are applied by one selection step to the plan's operations,
>   never to the stores or to the diff.
> - The plan lists each problem, left-out record and kept record, counts them, and its checksum
>   covers the list (ADR 0022). Apply needs the confirmed left-out count. A `sync` run whose plan
>   leaves out records stops after the plan.
> - Every other plan failure still fails the plan. During apply, an Infrahub rejection still
>   stops the apply. `continue_on_error` is removed.
>
> **Consequences.** AD047's rule changes for these six problem types only. A left-out record
> that exists in Infrahub is not updated and not deleted. An adapter that overrides
> `model_loader` opts out of the admission step. The policy for runs that nobody watches stays
> open for 3.0.
>
> **Alternatives considered.** `fail-fast` (today): one plan per problem. Removing left-out
> records from the stores before the diff: DiffSync would compute a diff that is not true of
> the source. `block` (leave out a whole kind): leaves out valid records the team could import.

Draft text for the AD-12 row in `decisions.md`:

> AD-12: Failed dependencies are visible and block dependent work. At the plan stage, records
> with problems and the records that depend on them through a required relationship are left
> out and counted; apply needs the person to confirm the left-out count, and a `sync` run whose
> plan leaves out records stops after the plan. The policy for runs that nobody watches is open
> for 3.0.

## R12. Decision record 0021 (draft): a run's failure record holds a structured cause

Name: `dev/adr/0021-a-run-failure-record-holds-a-structured-cause.md`. It replaces the rule at
`infrahub_sync/service/flow.py:722-725` ("A class name, never a message"). It keeps ADR 0005:
the cause is built at the remote boundary only.

> **Status**: Proposed, needs product sign-off (spec 009 FR-030). **Date**: 2026-10-15.
>
> **Context.** A failed run records only an error class name, because a destination's text "can
> carry anything" (`service/flow.py:722-725`). A team cannot tell why a run failed without the
> worker log (#369), and an Infrahub rejection is reported as "a destination GraphQL rejection"
> without Infrahub's code (`potenda/__init__.py:231-232`, #370). Masking credentials in free
> text was tried and rejected: short values break unrelated text (SYNC-82) and masking can make
> a published plan invalid JSON (SYNC-262).
>
> **Decision.** A run's failure record holds a structured cause built only from these fields:
> stage, failure type (a fixed list), system role and adapter name, Infrahub's error code, node
> kind and attribute from Infrahub's `extensions`, Infrahub's own error message for a rejected
> write, the kind, record identity and field named by a typed Sync exception, and apply
> progress. One module builds it (`infrahub_sync/failure_cause.py`), called only from the remote
> boundary. It reads typed attributes and exception classes; it never formats an exception's
> text, an address, a header or a traceback. Each field is limited in length.
>
> **Consequences.** Infrahub's message reaches the run record; it describes the write Infrahub
> received, which is source data. A failure of an unknown type records its class name and a
> fixed message. The secret-redaction guideline and the comment at `service/flow.py:722-725`
> are rewritten to match. Existing value replacement is unchanged; it no longer protects the
> run record, which holds no free text.
>
> **Alternatives considered.** The message with secrets masked (first plan of spec 009):
> rejected for SYNC-82 and SYNC-262. Class names only (today): gives no cause. A fixed message
> per error code: an unknown code says nothing.

## R13. The failure cause builder is the one layer that handles secrets

Decision: a new module `infrahub_sync/failure_cause.py` with `build_failure_cause(stage, exc,
record) -> RunFailureCause`. `_failure_evidence` (`service/flow.py:697-736`) calls it and stores
the result; the CLI and the Sync API return that stored record. No other code turns an
exception into text that leaves the worker.

Classification, by exception type in the cause chain, never by text:

| Failure type | Recognised by | Fields |
| --- | --- | --- |
| `destination_rejected_write` | `GraphQLError` during apply | `error_code`, `node_kind`, `attribute` from the first error's `extensions` when they are strings (at most 128 characters each); `message` from the first error's `message` (at most 1,024 characters, control characters removed) |
| `system_unreachable` | `requests.ConnectionError`, `httpx.ConnectError`, Infrahub SDK `ServerNotReachableError` | `system` |
| `system_timeout` | `requests.Timeout`, `httpx.TimeoutException`, `ServerNotResponsiveError` | `system` |
| `token_rejected` | HTTP 401 or 403 from `requests`, `httpx` or `pynetbox.RequestError`; Infrahub `AuthenticationError` | `system` |
| `tls_certificate_rejected` | `ssl.SSLCertVerificationError` (agreed with spec 013) | `system` |
| `record_load_failed` | `RecordLoadError`, `PeerNotFoundError` (R3, R4) | `node_kind`, `record`, `field` |
| `transform_failed` | a new `TransformError(ValueError)` that `apply_transform` raises instead of a bare `ValueError` (`infrahub_sync/__init__.py:436-441`), with the field and the record's `local_id` as attributes; its message text, which repeats the transform expression and the error, is not used | `node_kind`, `record`, `field` |
| `verification_failed`, `apply_refused`, `credential_missing`, `left_out_review_required` | the existing typed errors | fixed text |
| `internal_error` | anything else | class name, fixed text |

The system role and adapter name come from a new `SideLoadError(ValueError)` that `_load_side`
(`potenda/__init__.py:488-501`) raises instead of a bare `ValueError`, with `side` and `adapter`
attributes. It subclasses `ValueError`, so ADR 0005's handlers still match.

Where credential values are: they are resolved only into adapter and SDK connection settings
(`configuration/runtime.py:52`, `configuration/capabilities.py:208`). The plan's destination
binding records the destination without the token (`plan/models.py:342-361`). Problem reasons
are rendered from fixed templates over typed fields (kind, identity, field, value, choices,
peer). None of these paths reads a connection setting or an error's text, so no credential can
enter a cause, a problem reason or a plan file, whatever its length or characters.

Rationale: the product owner required prevention in one layer. The remote boundary of ADR 0005
is already the one place where a failure becomes a run record.

## R14. Existing redaction, and what happens to SYNC-82 and SYNC-262

Decision: this plan adds no value replacement and changes no existing one.

What stays as it is in the testing release:

- `collect_secret_values` with its six-character floor (`execution.py:126`, `:411`), `redact`
  (`:493`) and `sanitize_exception_chain` (`:509`), used at the worker boundary
  (`service/flow.py:922`), the service (`service/service.py:133`, `:568`, `:657`), the apply
  guard (`service/apply_guard.py:303`, `:380`) and the direct Prefect flow
  (`orchestration/flow.py:175`, `:190`).
- The JSON and byte passes of `publish_artifact` (`product_store/store.py:2925`, `:3561`,
  `:3597`) and of configuration content (`product_store/configs.py:371`).

SYNC-82 (short credentials appear in errors and plans): resolved for causes, problem reasons,
left-out and kept entries, plan files and Sync API responses, because none of them is built from
a credential or from error text (R13). What remains: the worker container log and the
orchestration log keep exception text and tracebacks, protected only by the six-character
replacement. A credential shorter than six characters can appear there. These logs are read by
OpsMill engineers and are not returned by the Sync API or the CLI. Follow-up after the testing
release: stop logging the text of adapter and HTTP library errors in the worker, and log the
structured cause instead.

SYNC-262 (masking corrupts a published plan): the byte pass of `publish_artifact` can still make
a published plan invalid JSON when a collected value of six or more characters spans a number
and the punctuation after it (`docs/docs/development/guidelines/secret-redaction.md`, "Drop
values below a length floor"). Under R13 a plan holds no credential by construction, so the pass
protects nothing on a plan. It can be removed safely for the plan review artifact
(`service/flow.py:178-187`) once a test asserts that the review document is built only from the
saved plan's fields (`service/flow.py:164-175`). Following the product owner's rule, that removal
is follow-up work, not MVP work. Until then, the existing risk stays.

Follow-ups to file (not filed by this plan): remove value replacement from the plan review
artifact (SYNC-262); log the structured cause instead of error text in the worker (rest of
SYNC-82).

## R15. `configs validate` reaches the destination schema and the worker credentials

Decision for FR-032: `POST /configs/{config_id}/versions/{registry_version}/validate`
(`service/config_routes.py:368-369`) accepts an optional `branch` query parameter. With it, the
route calls `configs.validate(..., destination_schema=DestinationSchemaOptions(branch=…))`
(`product_store/configs.py:863-891`), which runs `collect_destination_schema_findings`
(`configuration/schema_validation.py:183`). Today the route never passes the option. The check
runs in `sync-api`, which holds `INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN` (agreed with spec
013). The Dropdown check uses the choices of R6 against every literal `static` value in the
mapping. CLI: `infrahub-sync configs validate --branch <name>`.

Decision for FR-033: the worker writes the names of the `INFRAHUB_SYNC_CREDENTIAL_*` variables
that are set and not empty into a new product-store record, `worker_environment`, when it starts
and on each poll (`service/worker.py`). Names only, never values. Compose passes unset
variables as empty strings, so an empty value counts as unset. The validate route compares the
package's references with the most recent record of a live worker and reports each missing name
as `credential-not-set-in-worker`. When no live worker has a record, it reports
`worker-environment-unknown` as a warning.

Alternative considered: run validation as a worker run. Rejected: it adds an operation that
FR-025's stage list does not have, and the route is synchronous.

Decision for FR-034 (SYNC-147): the plan's create check (`refuse_unkeyed_create`,
`plan/keying.py:397`) and validation's check (`writable_convergence_reason`,
`plan/keying.py:181`, used by `configuration/schema_validation.py`) call one shared function for
the part that depends on the schema only. A parametrized test runs both over the same kinds.

Decision for FR-035 (SYNC-107): the responses of `configs register` and `configs version`
return the checksum without `declared_content`.

## R16. The NetBox example on the pinned `demo` dataset (FR-031, #374)

- Device status outside the five choices: a `device_status` transform in both `DcimDevice`
  entries of `examples/netbox_to_infrahub/package.yml` translates `planned` and `staged` to
  `provisioning`, `offline` and `failed` to `maintenance`, `inventory` to `reserved`, and
  `decommissioning` to `deprecated`, as the local copy did on 2026-10-05 (#374).
- Rack position `2.5`: reported as `wrong_value_type`. Rounding would write a position the
  device does not have.
- Two devices with the same name at two sites: reported as `destination_identity_collision`.

## R17. A stopped apply and the next plan (FR-020 to FR-022)

- FR-020: `ApplyRecord` (`plan/models.py:497-583`) records the applied operation IDs, the
  failing operation and whether it wrote. The cause adds the failing operation's kind and
  identity, read from the plan by its ID.
- FR-021: updates are keyed by recorded destination IDs and creates converge by upsert
  (ADR 0012, ADR 0013), so a new plan contains only what the destination does not match.
- FR-022: the product store refuses any stage on a run that admitted a write
  (`_reservation_refused`, `product_store/store.py:1827`). The refusal gets the error code
  `new-plan-required`.
- Rules passed to spec 010, which owns incremental reads: a plan that follows a stopped apply of
  the same configuration reads the destination in full, and a plan that follows one that left
  out records reads the source in full, so that left-out records are checked again (FR-009).
