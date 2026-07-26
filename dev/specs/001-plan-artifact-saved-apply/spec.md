# Feature Specification: Saved plan artifact and apply-exactly-what-was-reviewed

**Feature Branch**: `001-plan-artifact-saved-apply-infp-653`

**Created**: 2026-07-26

**Status**: Draft

**Input**: Delivery brief DB-001 (`db-001-plan-artifact-saved-apply.md`, brief_version 5, batch-v3),
primary JPD card **INFP-653**. The brief is the sole scope authority for this specification.

## Overview

A sync run in `plan` mode produces a durable plan artifact containing every proposed create,
update, delete, and relationship operation, each carrying a stable operation identifier and a
full payload. That artifact can be inspected at summary and per-object depth at any time after
the run, verified for safety, and applied — by run ID — so that the operations written to the
destination are provably the operations that were reviewed, with no recomputation of the
comparison.

Today `infrahub-sync diff` prints the plan once and discards it. There is no way to review a
plan later, prove what was applied, or apply the exact set of operations that was reviewed —
the run recomputes everything at apply time. Teams in change-managed environments must review
and approve every set of changes before they are written, and must be able to show that what
was applied is what was approved.

## Clarifications

### Session 2026-07-26

Five underspecified areas were resolved. All five are implementation decisions the brief either
delegates explicitly or does not reach; none changes scope. Each is **provisional** pending
ratification — the `[PROVISIONAL AD0NN]` markers below are the ratification handles and are
removed once the decision is confirmed.

- Q: What is the plan artifact's concrete on-disk encoding and file layout, how is its checksum
  computed, and how is a pre-existing v1 plan detected? → A: A `plan/` directory inside the
  existing per-run directory holds `manifest.json` and `operations.jsonl`. Both are canonical
  JSON — UTF-8, keys sorted, no insignificant whitespace, LF line endings — with
  `operations.jsonl` carrying exactly one operation object per line in dependency-tier order and,
  within a tier, ascending operation-identifier order. A single manifest field `plan_checksum`
  holds a SHA-256 over the canonical manifest with `plan_checksum`, the run identifier, and the
  creation timestamp excluded, concatenated with the bytes of `operations.jsonl`; the excluded set
  is exactly the set SC-006 masks, which is what makes the manifest byte-identical across
  re-plans. A manifest field `operations_count` distinguishes an empty plan (file present, zero
  lines, count 0) from a torn one (file absent or line count disagreeing with the manifest). The
  pre-existing `plan.parquet` is left in place untouched and is not read by the new apply path; a
  run directory with `plan.parquet` but no `plan/manifest.json` is what identifies a v1 plan.
  `[PROVISIONAL AD001]`
- Q: How is an operation identifier derived? → A: `op_` followed by the first 16 hex characters of
  a SHA-256 over the canonical JSON of the triple (action, destination kind, destination identity).
  The payload is deliberately excluded, so the identifier names the logical operation and stays
  stable across re-plans; exactness of the payload is already guaranteed by `plan_checksum`. The
  identifier is derived, never random or positional, so re-planning identical input reproduces it
  byte-for-byte. Uniqueness within a plan is asserted at write time rather than assumed.
  `[PROVISIONAL AD002]`
- Q: How does a planned operation reference its relationship peers so they can be resolved at apply
  time with no comparison store loaded? → A: Each reference records the peer's kind and the peer's
  identity values — never a destination-assigned identifier, which does not exist yet at plan time
  for peers this same plan creates and which would not survive being moved between environments.
  A cardinality-one reference is a single object; a cardinality-many reference is a list ordered
  canonically by the peer identity, so it is stable for SC-006. At apply time peers are resolved
  through a per-apply cache keyed by (kind, identity), populated as each planned create or update
  completes and, on a miss, by querying the destination for that identity and memoizing the
  result. Dependency-tier ordering guarantees a peer is written before anything referring to it.
  `[PROVISIONAL AD003]`
- Q: How are delete operations recorded in the plan without changing what the write path writes?
  → A: Delete operations are derived from the loaded destination state — the destination-only
  identities remaining after the source identities are removed — and are materialized only into
  plan records. They are never placed into the comparison result that the write path consumes, so
  a delete is structurally incapable of reaching the destination rather than merely being
  suppressed by configuration. The comparison flags configured for a project keep their present
  meaning for the write path and are not loosened. Deletes are recorded from this one source only,
  so an operation cannot be recorded twice and collide on its identifier.
  `[PROVISIONAL AD004]`
- Q: Which existing commands carry plan review, and what is the exact spelling? → A: The existing
  non-mutating `diff` command gains a read-from-artifact mode: `--run-id <id> --from-plan` prints
  the summary, `--detail` expands to per-object records, and `--kind <kind>` narrows the detail to
  one kind. In that mode no adapter is constructed and neither side is extracted, which is what
  lets review run in a process that did not produce the plan. No command is added and no command
  group is added. Review output is written to standard output rather than the log stream, since it
  is the command's product and must be capturable for the credential scan. The in-process reader is
  the single implementation; the command is a thin renderer over it, so both paths in SC-009
  exercise the same code. `[PROVISIONAL AD005]`

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Review a saved plan, then apply it by run ID (Priority: P1)

An operator runs a plan against a configuration and a destination that already holds data. The
run saves a plan artifact. The operator inspects the plan's summary (counts by action and kind),
expands one kind to per-object detail, and — after review — applies that run by its run ID. The
destination receives exactly the operations that were listed, in dependency order, with no
re-extraction of either side and no recomputation of the comparison. Every operation reported by
the apply carries the same identifier the operator saw at review time.

**Why this priority**: This is the outcome. Without a durable, reviewable, replayable plan there
is no review-before-write contract and nothing for the later outcomes to link through. Review
depth and identifier stability are what make the applied set provably the reviewed set.

**Independent Test**: Produce a plan run, read its summary and per-object detail from the stored
artifact in a process that did not create it, apply by run ID, and compare the destination
contents and the reported operation identifiers against the reviewed plan.

**Acceptance Scenarios**:

1. **Given** a `from-netbox` configuration and a destination with existing data, **When** an
   operator produces a plan, inspects its summary and then its per-object detail for one kind,
   and finally applies the run by ID, **Then** the destination receives exactly the operations
   listed in the reviewed plan, in dependency order, with no re-extraction and no recomputation,
   and the applied operations carry the same identifiers shown at review time.
2. **Given** a saved plan whose originating process has exited, **When** the plan is summarized
   and then expanded to per-object detail, **Then** both depths are produced from the stored
   artifact, both in-process and from the command line.
3. **Given** the command set before this feature, **When** it is compared against the command set
   after, **Then** no new command group has been added and both review depths are reachable
   through commands that already exist.
4. **Given** a plan artifact and both review outputs, **When** they are scanned for credential
   values, **Then** no secret value appears in any of them.

---

### User Story 2 - Refuse a plan that is no longer safe to apply (Priority: P2)

An operator requests an apply for a run whose saved plan can no longer be trusted: the stored
checksum no longer matches the artifact, the configuration the run used has changed, the source
snapshot the plan was computed against has been removed, or the artifact is torn — a manifest
exists but the operations or the snapshot are absent or truncated. The apply is refused before
any destination write, naming which verification failed, and the run does not enter an applied
state.

**Why this priority**: Applying an unverified plan silently breaks the guarantee the whole
outcome exists to provide. Refusal must land with the apply path, not after it.

**Independent Test**: Corrupt or remove each bound element of a saved plan in turn, request an
apply, and assert refusal, the named failed check, zero destination writes, and the resulting
run state.

**Acceptance Scenarios**:

1. **Given** a saved plan whose source snapshot has been removed, or whose stored checksum no
   longer matches the artifact, **When** an apply is requested for that run, **Then** the apply
   is refused before any destination write, naming which verification failed, and the run does
   not enter an applied state.
2. **Given** a saved plan whose recorded configuration-version value differs from the current
   one, **When** an apply is requested, **Then** the apply is refused without that value being
   parsed or interpreted.
3. **Given** a saved plan whose manifest exists but whose operations or source snapshot are
   absent or truncated, **When** an apply is requested, **Then** the apply is refused the same
   way and nothing is partially applied.
4. **Given** a plan artifact in the pre-existing v1 row format, **When** an apply is attempted,
   **Then** it is rejected with a message directing the operator to re-plan, and no destination
   write occurs.
5. **Given** an unchanged source and destination, **When** the plan is produced twice in
   succession, **Then** the operations section and the manifest are byte-identical, excluding
   only the fields that necessarily vary per run.

---

### User Story 3 - Re-apply the same plan without duplicating (Priority: P3)

An operator applies a saved plan, then applies the same saved plan again — after an interrupted
run, a retry, or a re-run of an automated pipeline. The destination ends at the same object
counts and the same object identities as after the first apply.

**Why this priority**: A plan that cannot be safely re-applied is not usable in the retry-heavy
environments this feature targets, and convergence is what makes an interrupted apply recoverable
by simply applying again.

**Independent Test**: Apply a plan once, record destination object counts and identities, apply
the identical plan again, and compare.

**Acceptance Scenarios**:

1. **Given** a run whose saved plan has already been applied successfully, **When** the same
   saved plan is applied a second time, **Then** the destination ends at the same object counts
   and the same object identities as after the first apply — no duplicates are created.
2. **Given** a plan covering create, update, and relationship operations, **When** it is applied
   once, applied twice, and applied with a crash injected after a write commits but before it is
   recorded, and again with a crash injected before the write, **Then** every one of those write
   classes ends at clean-single-run counts.
3. **Given** a plan with zero operations, **When** it is applied, **Then** the apply succeeds as
   a no-op.

---

### User Story 4 - A recorded delete is never silently skipped (Priority: P4)

An object present in the destination has been removed from the source. The plan run records a
delete operation for it, with a stable operation identifier, so a reviewer can see it. Applying
that plan executes every non-delete operation, does not delete the object, and ends in a failed
state naming the unsupported operation.

**Why this priority**: Recording deletes is a deliberate change to what the plan shows, and the
gap between "recorded" and "not applied" must be loud. A silent skip would make the applied set
differ from the reviewed set, which contradicts the outcome.

**Independent Test**: Plan against a source from which a destination-present object has been
removed, confirm the delete operation appears in the artifact with an identifier, apply, and
assert destination object counts before and after plus the recorded run state and message.

**Acceptance Scenarios**:

1. **Given** a source dataset from which an object present in the destination has been removed,
   **When** a plan run completes and that plan is applied, **Then** the plan artifact contains a
   delete operation for that object with a stable operation identifier; the apply executes every
   non-delete operation, does not delete the object, and completes in a **failed** state naming
   the unsupported operation.

---

### User Story 5 - Relationship operations apply without a comparison store (Priority: P5)

A relationship-bearing kind is planned and applied. At apply time there is no loaded comparison
store to resolve relationship peers from, so peers are resolved from what the plan itself
carries. The relationships that land on the destination are the ones the plan specified.

**Why this priority**: Relationship-bearing kinds are the majority of a real configuration;
without apply-time peer resolution the saved-plan path only covers flat objects.

**Independent Test**: Apply a plan covering a relationship-bearing kind with no comparison store
loaded, read the destination relationships back, and compare them against the plan's relationship
references.

**Acceptance Scenarios**:

1. **Given** a relationship-bearing kind from the qualified configuration, **When** its plan is
   applied with no loaded comparison store, **Then** the resulting relationships on the
   destination match those the plan specified.

---

### Edge Cases

- **Missing destination write surface.** Applying a plan against an adapter that cannot execute
  planned operations fails with a clear, actionable error naming the adapter — the behavior the
  engine already has today.
- **Empty plan.** A plan with zero operations is a valid artifact; applying it is a successful
  no-op. It is recorded as a present-but-empty operations section with a count of zero, which is
  what keeps it distinguishable from the torn case below.
- **Torn artifact.** A plan whose manifest exists but whose operations or source snapshot are
  absent or truncated is refused, not partially applied. An operations section whose length
  disagrees with the count the manifest records is torn.
- **Identifier collision.** Two operations must never share an operation identifier within one
  plan. Since the identifier is derived from the action, kind, and destination identity, a
  collision means two operations target the same object with the same action; the plan run fails
  rather than emitting a plan whose identifiers do not address one operation each.
- **Non-unique destination identifier.** Convergence rides on the destination unique-constraining
  the identifier attribute; without that constraint an upsert produces duplicates. This is
  detected and reported at plan time, as a warning naming the affected kind and identifier.
  Documenting it as a precondition is not sufficient, because the failure is silent data
  duplication.
- **Partial apply.** If apply stops partway, the operations already written stay written and the
  run records the last operation it reported as applied. Durable crash-surviving progress and
  resumption are out of scope.
- **Recorded deletes change existing output.** Because deletes are suppressed from the plan
  today, recording them makes previously hidden operations appear in the plan and in anything
  that renders it. Affected test fixtures and documentation are updated in the same change.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: A plan run MUST produce and save a plan artifact containing every proposed create,
  update, delete, and relationship change before anything is written to the destination.
  *(DBR-001)*
- **FR-002**: The plan artifact format MUST be defined, and MUST carry, per operation: a stable
  operation identifier, the action, the destination kind, destination identity, the required
  source values as a full payload, relationship references, and a dependency tier. Each
  relationship reference MUST identify its peer by kind and identity values, never by a
  destination-assigned identifier. *(DBR-008, DBR-011; encoding and layout per AD001, reference
  shape per AD003)*
- **FR-003**: Every operation MUST carry a stable identifier that links review, application,
  audit, and recovery records for that operation. The identifier MUST be derived from the
  operation's action, destination kind, and destination identity — never randomly generated and
  never derived from the operation's position in the plan — so that re-planning identical input
  reproduces it and a plan that gains or loses an operation does not renumber the others.
  *(DBR-005; derivation per AD002)*
- **FR-004**: The artifact MUST include a plan manifest binding it to its run, its configuration
  version, and the source snapshot it was planned against, with a single deterministic checksum
  over the manifest and the ordered operations. The checksum MUST exclude only the checksum field
  itself, the run identifier, and the creation timestamp, so that the fields SC-006 masks are
  exactly the fields the checksum does not cover. *(DBR-006, DBR-008; computation per AD001)*
- **FR-005**: The manifest and the ordered operations MUST be serialized deterministically — a
  fixed key order, no insignificant whitespace, a fixed ordering of the operations, and a fixed
  ordering of every ordered collection inside them — so the checksum is stable across
  re-serialization of identical content. *(DBR-014; encoding per AD001)*
- **FR-006**: A saved plan MUST be reviewable at two depths: a summary giving counts by action and
  by kind, and per-object detail for the operations it contains. *(DBR-002)*
- **FR-007**: The plan MUST be readable from the stored artifact at any time after the run,
  including after the process that produced it has exited. *(DBR-012)*
- **FR-008**: Both review depths MUST be reachable in-process and by extending CLI commands that
  already exist. No new CLI command is added and no new CLI command group is introduced. Review
  MUST be carried by the existing non-mutating command, MUST NOT construct an adapter or extract
  either side, and MUST write its output to standard output so it can be captured and scanned. The
  in-process reader MUST be the single implementation, with the command a thin renderer over it.
  *(DBR-020; command and flag spelling per AD005)*
- **FR-009**: Before any destination write, an apply MUST verify that the plan checksum, the
  configuration version, and the source-snapshot binding still match. A plan that fails any of
  these MUST be refused, naming the failed check, and the run MUST NOT reach an applied state.
  *(DBR-003, DBR-006)*
- **FR-010**: The plan and its source snapshot MUST be bound so the pair cannot tear. A plan whose
  manifest exists but whose operations or source snapshot are absent or truncated MUST be refused
  on the same path as a mismatch. The manifest MUST carry an operation count so that a plan with
  no operations is distinguishable from a plan whose operations are missing, rather than the two
  presenting identically. *(DBR-015; count field per AD001)*
- **FR-011**: The manifest's configuration-version field MUST hold a deterministic content
  checksum computed over the configuration the run used, unless the caller supplies a version
  identifier explicitly, in which case that value MUST be stored verbatim. Either way the value
  MUST be treated as opaque at apply: compared for equality and never parsed. No new user-facing
  input is introduced. *(DBR-018)*
- **FR-012**: A saved plan MUST be applicable by run ID, executing exactly the stored operations
  in dependency order, without re-extracting either side and without recomputing the comparison.
  *(DBR-004)*
- **FR-013**: The Infrahub destination adapter MUST be able to execute a planned create or update
  convergently, so that repeating an operation does not create a second object. *(DBR-013,
  DBR-011)*
- **FR-014**: Relationship peers MUST be resolvable at apply time without a loaded comparison
  store, from the peer kind and identity the plan itself carries. Resolution MUST be memoized
  within one apply, MUST take an operation's own result as the resolution for later operations
  referring to it, and MUST fall back to querying the destination for that identity on a miss.
  Dependency-tier ordering MUST guarantee a peer is written before anything referring to it.
  *(DBR-007, DBR-011; resolution shape per AD003)*
- **FR-015**: Delete operations MUST be recorded in the plan, changing today's default of
  suppressing them. They MUST be derived from the destination-only identities in the loaded
  destination state and materialized only into plan records, never into the comparison result the
  write path consumes. The comparison flags a project configures MUST keep their present meaning
  for the write path and MUST NOT be loosened to make deletes visible. Deletes MUST come from that
  one source only, so no operation is recorded twice. Test fixtures and documentation affected by
  the change in plan content MUST be updated in the same change. *(DBR-009; mechanism per AD004)*
- **FR-016**: A delete MUST NOT be applied to the destination by the saved-plan apply path. The
  existing write path's behavior under a project's configured comparison flags is unchanged by this
  feature. *(DBR-010)*
- **FR-017**: An unsupported operation in a plan MUST be reported at apply time and MUST fail the
  run; it MUST NOT be silently skipped. Supported operations in the same plan are still applied.
  *(DBR-016)*
- **FR-018**: No secret value MUST appear in the plan artifact or in any review output. *(DBR-017)*
- **FR-019**: A plan artifact in the pre-existing v1 row format MUST be detected and rejected with
  a message directing the operator to re-plan. The reader MUST NOT accept v1 rows, v1 plans MUST
  NOT be migrated, and no second apply path with weaker guarantees may be built. Detection MUST NOT
  depend on parsing a v1 artifact: a run holding only the pre-existing plan file and no new-format
  manifest is a v1 plan. The pre-existing file MUST be left in place rather than deleted or
  rewritten. *(DBR-019; detection rule per AD001)*
- **FR-020**: The identifiers of operations reported as applied MUST be recorded on the run
  result. *(scope boundary: run result only, not a durable ledger)*
- **FR-021**: Two operations within one plan MUST NOT share an operation identifier. Because the
  identifier is derived rather than allocated, uniqueness MUST be asserted when the plan is written
  and MUST fail the plan run if it does not hold, rather than being assumed.
- **FR-022**: A plan with zero operations MUST be a valid artifact, and applying it MUST be a
  successful no-op. It MUST be represented as a present-but-empty operations section with a
  recorded count of zero, not as an absent one.
- **FR-023**: Applying a plan against an adapter with no planned-write surface MUST fail with a
  clear, actionable error naming the adapter, before any write is attempted.
- **FR-024**: When a destination identifier attribute is not unique-constrained, the plan run MUST
  warn at plan time, naming the affected kind and identifier.
- **FR-025**: If an apply stops partway, the operations already written MUST stay written and the
  run MUST record the last operation it reported as applied.
- **FR-026**: The plan contract MUST order operations without prescribing write granularity, so
  batched destination writes remain possible later without a plan-format change.

### Key Entities

- **Plan artifact**: The durable output of a plan run — a manifest plus an ordered set of planned
  operations, held together in the run's own directory, readable independently of the process that
  wrote it and readable without loading all of it at once, and versioned so a pre-existing v1 plan
  is recognizable and refusable. *(concrete layout per AD001)*
- **Plan manifest**: The artifact's header. Binds the artifact to its run identifier, the
  configuration version it ran with, and the source snapshot it was planned against; records the
  format version and the operation count; and carries the deterministic checksum over itself and
  the ordered operations. *(fields and checksum rule per AD001)*
- **Planned operation**: One proposed change. Carries a stable operation identifier, the action
  (create, update, delete, or relationship change), the destination kind, destination identity,
  the required source values as a full payload, relationship references, and a dependency tier.
- **Relationship reference**: A peer named by kind and identity values rather than by any
  destination-assigned identifier, so it is resolvable at apply time and does not depend on which
  destination instance the plan is applied to. *(per AD003)*
- **Source snapshot**: The extracted source-side state the plan was computed against, bound to
  the plan so the pair cannot tear.
- **Run**: The unit a plan belongs to and the handle an apply is requested by. Carries the run
  state, including whether the plan reached an applied state and which operations were reported
  as applied.
- **Configuration-version value**: An opaque, equality-compared string in the manifest —
  by default a deterministic content checksum over the configuration the run used, or a
  caller-supplied identifier stored verbatim. Never parsed or interpreted.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A persisted plan is applied without re-running extraction and without a fork-wide
  rewrite of the comparison engine — evidenced by an apply run against a live destination plus a
  trace or inspection showing no comparison-engine diff/sync call on the apply path. *(DBA-001)*
- **SC-002**: Re-applying an identical saved plan converges: the same object, the same identity,
  no duplicate — evidenced by apply-once and apply-twice object counts and identities recorded
  against a live destination. *(DBA-002)*
- **SC-003**: The create, update, and relationship write classes end at clean-single-run counts
  across apply-once, apply-twice, and both crash-window variants (a crash injected after a write
  commits but before it is recorded, and one injected before the write) — evidenced by a per-class
  conformance matrix. Delete is excluded, because applying deletes is out of scope. *(DBA-003, as
  narrowed by the brief)*
- **SC-004**: A plan whose checksum, configuration version, or source-snapshot binding no longer
  matches is refused before any destination write, naming the failed check, and the run does not
  reach an applied state; a plan whose manifest exists but whose operations or source snapshot are
  absent or truncated is refused the same way — evidenced by five negative cases (checksum
  mismatch, configuration-version mismatch, snapshot-binding mismatch, absent operations,
  truncated snapshot), each asserting refusal, zero destination writes, and the resulting run
  state. *(DBA-004)*
- **SC-005**: The operation identifiers shown at review are the identifiers reported against each
  operation in the apply result — evidenced by a review-then-apply trace comparing both identifier
  sets. *(DBA-005)*
- **SC-006**: Re-planning an unchanged source and destination produces a byte-identical operations
  section and a byte-identical manifest, excluding the fields that necessarily vary per run (the
  run identifier and the creation timestamp) — evidenced by two consecutive plan runs and a byte
  comparison with the varying fields masked. *(DBA-006)*
- **SC-007**: A plan containing a delete operation applies its non-delete operations, does not
  delete from the destination, and ends in a failed state naming the unsupported operation —
  evidenced by destination object counts before and after plus the recorded run state and message.
  *(DBA-007)*
- **SC-008**: A relationship-bearing kind from the qualified configuration applies with no loaded
  comparison store, and the resulting relationships on the destination match those the plan
  specified — evidenced by destination relationships read back and compared against the plan's
  relationship references. *(DBA-008)*
- **SC-009**: A saved plan can be summarized by action and kind, and expanded to per-object
  detail, at any time after the run and after the originating process has exited — reachable both
  in-process and from the CLI. Evidenced by four cases: summary and per-object detail, each
  produced in-process and from the CLI, all against a stored artifact read in a new process.
  *(DBA-009)*
- **SC-010**: No secret value appears in the plan artifact, in summary output, or in per-object
  output — evidenced by a canary-credential scan over the artifact and both review outputs.
  *(DBA-010)*
- **SC-011**: A v1-format plan is rejected with a message directing the operator to re-plan, and
  no destination write occurs — evidenced by an apply attempted against a v1 fixture plan,
  asserting refusal, the message, and zero writes. *(DBA-011)*
- **SC-012**: The CLI command set gains no new command group, and review is reachable through
  commands that already exist — evidenced by the top-level command list compared before and after
  showing no group added, plus the SC-009 CLI cases demonstrating that both review depths are
  reachable from existing commands. *(DBA-012)*
- **SC-013**: Applying a plan whose configuration-version value differs from the one recorded at
  plan time is refused without the value being parsed or interpreted, and an arbitrary opaque
  string round-trips unchanged through manifest write and apply comparison — evidenced by a
  round-trip test using a deliberately opaque value, plus the mismatch refusal from SC-004.
  *(DBA-013)*

## Out of Scope

Carried verbatim from the brief. None of the following is delivered here.

- **Applying a delete to the destination.** The plan records delete operations; executing them is
  explicitly excluded, and doing so safely requires an ownership grammar this outcome does not
  define. Until that lands, a plan containing a delete behaves as FR-017 and SC-007 specify: the
  delete is reported and the run fails, never silently skipped.
- The shared execution core refactor — it is not a prerequisite here, and this outcome must not
  require it to land first.
- Durable run/artifact storage behind provider interfaces; this outcome uses the per-run directory
  layout the engine already writes.
- **Creating, validating, or managing configuration versions** — the version registry, the
  append-only history, and the validation model. This outcome computes a content checksum over the
  configuration it ran with, stores it in the manifest, and compares it at apply. It does not
  version, validate, or interpret configurations.
- A durable per-operation apply ledger surviving a crash. This outcome records applied operation
  identifiers on the run result only.
- Load-path reference-scan replacement and batched destination writes. Only apply-path peer
  resolution is here.
- **Any new CLI command group.** Review is delivered by extending existing commands only;
  introducing `plan`, `runs`, or `configs` command groups belongs elsewhere.
- Destination freshness checks, plan expiration, and conflict policies.
- Branch review mode.

## Assumptions

- Destination identifier attributes are unique-constrained on the qualified path. If they are not,
  create and update converge into duplicates instead of converging, which invalidates SC-002 —
  hence the plan-time warning in FR-024.
- Review is reachable by extending existing CLI commands, without any new command group. If
  extending existing commands proves impossible, that is a scope change requiring a new decision,
  not an implementer's call.
- The qualified path is NetBox → Infrahub using `examples/netbox_to_infrahub/config.yml`.
- The run-mode vocabulary (`plan`, `sync`, `apply`) is fixed naming, not a build dependency.
- The existing engine and per-run artifact layout are present: a saved plan is already read and
  dispatched per row to the destination's planned-write method, and per-side snapshots and run
  sidecars are already written. The planned-write surface currently has no implementation on any
  adapter, and today's plan rows are lossy.
- The Infrahub destination adapter already has an identifier-keyed write path that converges on
  repeat; FR-013 routes planned creates and updates through it rather than inventing a new one.
- The configuration-version value is consumed as an opaque input. Before a version registry
  exists, a checksum over the configuration's declared content satisfies the binding.
- Which existing commands carry review, and their exact flag spelling, is an implementation choice
  within one fixed constraint: no new top-level command group. That choice is now recorded as
  AD005 rather than left open.

## Dependencies

- No in-batch dependencies. This outcome can be completed independently.
- **This specification owns a shared contract.** The plan artifact format — the manifest fields,
  the per-operation record, the deterministic serialization, and the checksum rule — is owned here
  and consumed by nine later outcomes: the public API's apply, the configuration-version binding,
  a schema-fingerprint manifest field, branch review, the apply ledger's operation identifiers,
  scoped plans, per-operation dependency tiers, plan summaries in the UI, and byte-for-byte
  comparison against this format. Any change to the format after this ships is a breaking change
  for all nine.

## Requirements Traceability

Brief requirements (DBR) and acceptance criteria (DBA) to the sections that carry them.

| Brief item | Carried by |
|---|---|
| DBR-001 | FR-001; User Story 1 |
| DBR-002 | FR-006; User Story 1 scenario 2 |
| DBR-003 | FR-009; User Story 2 |
| DBR-004 | FR-012; User Story 1 scenario 1 |
| DBR-005 | FR-003, FR-021; SC-005 |
| DBR-006 | FR-004, FR-009; User Story 2 scenario 1 |
| DBR-007 | FR-014; User Story 5 |
| DBR-008 | FR-002, FR-004; Key Entities |
| DBR-009 | FR-015; User Story 4 |
| DBR-010 | FR-016; User Story 4 |
| DBR-011 | FR-002, FR-013, FR-014 |
| DBR-012 | FR-007; User Story 1 scenario 2 |
| DBR-013 | FR-013; User Story 3 |
| DBR-014 | FR-005; SC-006 |
| DBR-015 | FR-010; User Story 2 scenario 3 |
| DBR-016 | FR-017; User Story 4 |
| DBR-017 | FR-018; SC-010 |
| DBR-018 | FR-011; Key Entities (configuration-version value); SC-013 |
| DBR-019 | FR-019; User Story 2 scenario 4 |
| DBR-020 | FR-008; User Story 1 scenario 3 |
| DBA-001 | SC-001 |
| DBA-002 | SC-002; User Story 3 scenario 1 |
| DBA-003 | SC-003; User Story 3 scenario 2 |
| DBA-004 | SC-004; User Story 2 scenarios 1 and 3 |
| DBA-005 | SC-005; User Story 1 scenario 1 |
| DBA-006 | SC-006; User Story 2 scenario 5 |
| DBA-007 | SC-007; User Story 4 scenario 1 |
| DBA-008 | SC-008; User Story 5 scenario 1 |
| DBA-009 | SC-009; User Story 1 scenario 2 |
| DBA-010 | SC-010; User Story 1 scenario 4 |
| DBA-011 | SC-011; User Story 2 scenario 4 |
| DBA-012 | SC-012; User Story 1 scenario 3 |
| DBA-013 | SC-013; User Story 2 scenario 2 |

## Open Design Decisions

Both items previously deferred here are now answered in [Clarifications](#clarifications) and
carried into the requirements above. They are recorded as **provisional** decisions rather than
silent implementation choices, because they are design commitments other outcomes consume:

- **The plan artifact's concrete on-disk encoding** — decided as AD001. Nine later outcomes consume
  this format and any later change to it is a breaking change for all of them, so it is recorded
  explicitly and marked provisional until ratified.
- **Which existing commands carry review, and the exact flag spelling** — decided as AD005. It is
  user-visible and will later be folded into a `plan` group without changing behavior, so it is
  named rather than left implicit.

Three further design commitments were surfaced during clarification and recorded the same way:
AD002 (operation-identifier derivation), AD003 (relationship-reference shape and apply-time peer
resolution), and AD004 (how deletes are recorded without changing what the write path writes).

Nothing here remains open. What remains genuinely deferred is not a design commitment:

- **Plan size and review performance.** The brief sets no volume or latency target, so none is
  invented here. The encoding chosen in AD001 is line-oriented specifically so a large plan can be
  summarized and detailed without loading all of it, which is the property a later target would
  need; no threshold is asserted.
- **How a missing destination unique constraint is detected** for the FR-024 warning. The
  requirement and the warning's content are fixed; the detection mechanism is a planning-phase
  choice with no cross-outcome contract attached.
