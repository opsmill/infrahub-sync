# Review: apply-safety checklist (CHK001–CHK042)

**Checklist**: `../apply-safety.md` · **Spec under review**: `../../spec.md` (567 lines)
**Brief**: `db-001-plan-artifact-saved-apply.md` (brief_version 5, batch-v3)
**Reviewer**: clean-context requirements evaluator (read-only) · **Date**: 2026-07-26

Verdicts are about the requirements documents. Every claim about current engine behavior was
checked against the tree and is cited `file:line`.

## Reference: run-state vocabulary that exists today

The spec's "applied state" / "resulting run state" language has an existing implementation
vocabulary it never references:

- `infrahub_sync/cache/sidecars.py:71` — `status: str = "pending"  # pending | running | dry-run | applied | failed`
  (the `RunFile` sidecar, written to `<run_dir>/run.json`).
- Writers: `infrahub_sync/cli.py:146` (`running`, mode `diff`), `:154` (`dry-run`), `:157` (`failed`),
  `:244` (`running`, mode `sync`), `:284` (`applied`), `:286` (`failed`), `:322` (`running`, mode
  `apply`), `:345` (`applied`), `:347` (`failed`).
- Consumer: `infrahub_sync/cache/incremental.py:24` — `_SUCCESS_STATUSES = frozenset({"applied", "dry-run"})`;
  `previous_successful_run_dir` (`incremental.py:27-48`) selects the warm-run baseline from those two
  statuses, so the state chosen for a refused apply has a functional consequence beyond reporting.
- Today's only pre-apply refusal (the schema-sub-hash check, `cli.py:335-340`) calls
  `print_error_and_abort` **outside** the `try/except` at `cli.py:343-349`, so it leaves
  `run.json` at `status: "running"` permanently. There is no `refused`, `rejected`, or `aborted`
  state in the vocabulary.

## Verdict table

| Item | Verdict | Severity | One-line reason |
|---|---|---|---|
| CHK001 | DEFECT | BLOCKING | The run state resulting from a refusal is never named; SC-004 requires asserting it. |
| CHK002 | DEFECT | RECOMMENDED | Verification set is not closed: the manifest's format version is recorded but never verified. |
| CHK003 | DEFECT | RECOMMENDED | No ordering of checks, and "naming the failed check" (singular) never says all-vs-first. |
| CHK004 | DEFECT | RECOMMENDED | No requirement permits re-applying a run already in an applied state; only SC-002 implies it. |
| CHK005 | DEFECT | RECOMMENDED | "Before any destination write" is not an obligation over adapter construction/connection. |
| CHK006 | DEFECT | RECOMMENDED | No refusal-message content bar; FR-018 binds artifact and review output, not error output. |
| CHK007 | DEFECT | RECOMMENDED | No stated rule separating pre-write refusals from post-application failures. |
| CHK008 | DEFECT | RECOMMENDED | What the run result records on refusal (empty identifier set vs nothing) is unstated. |
| CHK009 | DEFECT | RECOMMENDED | The configuration inputs the checksum rule covers are undefined. |
| CHK010 | SATISFIED | — | SC-013's opaque round-trip is a definite pass condition for "never parsed". |
| CHK011 | DEFECT | BLOCKING | "Truncated"/"mismatch" is undefined for the source snapshot; no snapshot digest exists. |
| CHK012 | DEFECT | BLOCKING | "Applied state" is not a named state with transitions, so the MUST-NOT is uncheckable. |
| CHK013 | DEFECT | NIT | FR-023 gives no content bar; the existing-behavior anchor sits only in Edge Cases. |
| CHK014 | DEFECT | RECOMMENDED | "The last operation it reported as applied" names no record and no reporting sequence. |
| CHK015 | DEFECT | RECOMMENDED | SC-004's five cases omit the absent snapshot that User Story 2 scenario 1 names. |
| CHK016 | DEFECT | RECOMMENDED | v1 detection is presence-based and cannot separate a crashed new-format plan write. |
| CHK017 | SATISFIED | — | FR-017 states supported operations are still applied; FR-009's check list is closed. |
| CHK018 | DEFECT | RECOMMENDED | FR-020 and FR-025 describe records whose relationship is never stated. |
| CHK019 | DEFECT | RECOMMENDED | FR-023's write-surface check is not placed relative to FR-009's verifications. |
| CHK020 | DEFECT | BLOCKING | FR-025 and the ledger exclusion cannot both hold under the crash reading of "stops partway". |
| CHK021 | SATISFIED | — | SC-001 defines recomputation as a comparison-engine diff/sync call; verification is neither. |
| CHK022 | DEFECT | RECOMMENDED | SC-004 asserts zero destination writes without stating how they are observed. |
| CHK023 | SATISFIED | — | SC-001 says "comparison-engine diff/sync call", not the brief's literal method names. |
| CHK024 | DEFECT | RECOMMENDED | SC-005 says "apply result", FR-020 says "run result"; neither links them or fixes granularity. |
| CHK025 | SATISFIED | — | FR-019 fixes the message content ("directing the operator to re-plan"). |
| CHK026 | DEFECT | RECOMMENDED | FR-025 has no success criterion and no stated observability after abnormal termination. |
| CHK027 | DEFECT | RECOMMENDED | FR-020 has no criterion of its own and is absent from the traceability table. |
| CHK028 | DEFECT | RECOMMENDED | "Naming the unsupported operation" is not resolved to identifier, action, or object. |
| CHK029 | DEFECT | RECOMMENDED | A non-existent run ID is unaddressed and today silently creates the run directory. |
| CHK030 | DEFECT | RECOMMENDED | Concurrency is unmentioned though an existing pipeline lock both covers and constrains it. |
| CHK031 | DEFECT | RECOMMENDED (PRODUCT-AMBIGUITY) | Destination unreachable mid-apply is classified nowhere. |
| CHK032 | DEFECT | RECOMMENDED (PRODUCT-AMBIGUITY) | A destination-rejected operation: stop or continue is unspecified. |
| CHK033 | DEFECT | RECOMMENDED | FR-009's trigger is phrased on a write, leaving empty-plan verification ambiguous against FR-022. |
| CHK034 | SATISFIED | — | "Length disagrees with the count the manifest records" covers both directions. |
| CHK035 | DEFECT | BLOCKING | Nothing verifies the manifest's run identifier against the run being applied, and the checksum excludes it. |
| CHK036 | DEFECT | NIT | Retry-after-refusal is unstated; consequence of the missing run-state model (CHK012). |
| CHK037 | SATISFIED | — | The Assumptions block records per-side snapshots and run sidecars as already present. |
| CHK038 | DEFECT | BLOCKING | The apply-side supplier of the configuration-version comparison value is unspecified, so SC-013's round-trip cannot close. |
| CHK039 | DEFECT | RECOMMENDED | FR-020 carries the scope-boundary marker; FR-025, which needs it more, does not. |
| CHK040 | DEFECT | RECOMMENDED | No assumption that the value recomputes identically, and no stated consequence of a benign reformat. |
| CHK041 | SATISFIED | — | Each AD001-dependent requirement carries a trailing `per AD001` tag; the marker is the ratification handle. |
| CHK042 | DEFECT | RECOMMENDED | The pre-adapter-construction assumption is unrecorded and contradicted by the current call order. |

**Counts** — SATISFIED 8 · DEFECT BLOCKING 6 · DEFECT RECOMMENDED 26 · DEFECT NIT 2 ·
NOT-APPLICABLE 0 · PRODUCT-AMBIGUITY labels 2 (CHK031, CHK032).

---

## BLOCKING defects

### CHK001 / CHK012 — the run state on a refused apply is never named (one fix)

**Anchor**: `spec.md:298-300` (FR-009), `spec.md:379-381` (Key Entities / Run), `spec.md:401-407` (SC-004),
`spec.md:415-418` (SC-007).

**Evidence.** FR-009 (`spec.md:300`): "A plan that fails any of these MUST be refused, naming the
failed check, and the run MUST NOT reach an applied state." SC-004 (`spec.md:406-407`) requires each
of five negative cases to assert "refusal, zero destination writes, and **the resulting run state**".
No section of the spec names a single run state. Key Entities/Run (`spec.md:379-381`) says the run
"Carries the run state, including whether the plan reached an applied state" — a property, not a
vocabulary, and no transitions are given. So SC-004's third assertion has no target value and
"MUST NOT reach an applied state" has no enumerated complement.

The vocabulary already exists in the tree and the spec does not reference it:
`sidecars.py:71` — `pending | running | dry-run | applied | failed`; `applied` is written at
`cli.py:284` and `cli.py:345`; `failed` at `cli.py:157`, `:286`, `:347`. The choice is not free:
`incremental.py:24` treats `applied` and `dry-run` as success statuses for warm-run baseline
selection, so labelling a refusal `dry-run` would poison the incremental path. Today's only
pre-apply refusal leaks `running` because `print_error_and_abort` at `cli.py:336` fires before the
`try/except` at `cli.py:343-349` — i.e. the existing code does not answer the question either.

**Brief vs spec.** Brief-gap **and** spec defect. The brief's DBA-004 evidence column
(`brief §Acceptance criteria`, DBA-004) demands "the resulting run state" while no brief section
defines run states; the brief's Requirements table has no DBR fixing the vocabulary. The spec could
still have closed it, since the vocabulary is an existing implementation fact rather than new scope.

**Minimum fix.** Add to FR-009: "A refused apply MUST record the run in the `failed` state
(`run.json` status vocabulary `pending | running | dry-run | applied | failed`); 'applied state'
means `status: applied`. A refusal MUST NOT leave the run in `running`." Then SC-004 and SC-007 can
name `failed` explicitly.

### CHK011 — the source snapshot has no defined binding value and no truncation-detection rule

**Anchor**: `spec.md:302-306` (FR-010), `spec.md:281-283` (FR-004), `spec.md:36-49` (AD001),
`spec.md:368-370` (Key Entities / Plan manifest), `spec.md:401-407` (SC-004).

**Evidence.** AD001 (`spec.md:42-46`) defines `plan_checksum` as "a SHA-256 over the canonical
manifest with `plan_checksum`, the run identifier, and the creation timestamp excluded, concatenated
with the bytes of `operations.jsonl`". The source snapshot is not in the covered bytes. AD001's
`operations_count` (`spec.md:46-48`) is explicitly a discriminator for the **operations** file only.
FR-010 (`spec.md:302-306`) nonetheless requires refusal when "the source snapshot [is] absent or
truncated", and SC-004 (`spec.md:404-407`) enumerates "snapshot-binding mismatch" and
"truncated snapshot" as two of its five mandatory negative cases. Nothing in the spec says what the
snapshot binding *is* — Key Entities/Plan manifest (`spec.md:368-370`) says only that the manifest
"Binds the artifact to … the source snapshot it was planned against", with no field content.

Verified against the tree: the source snapshot is not one file. `_write_side_snapshot`
(`potenda/__init__.py:123-160`) writes one Parquet file per kind via `write_resource_side`
(`cache/parquet_io.py:92-142`) to `<run_dir>/A/<resource>.parquet`, injecting `_extract_ts`,
`_source_id`, `_tombstone`. Per-run row counts are not persisted — `RowcountsFile` is written to the
**cache root**, not the run directory (`potenda/__init__.py:420-427`). So no existing artifact carries
a per-run snapshot digest or row count that a "truncated snapshot" check could read.

Consequence: two of DBA-004's five required negative tests (snapshot-binding mismatch, truncated
snapshot) have no detection mechanism defined anywhere in the spec, making DBA-004 unachievable as
written.

**Brief vs spec.** Spec defect. The brief carries the requirement (DBR-015, "Bind the plan and its
source snapshot so the pair cannot tear") and the criterion (DBA-004) and correctly delegates the
mechanism; AD001 is where the mechanism should have been named and is the one binding element it
omits.

**Minimum fix.** Extend AD001 (and FR-004/FR-010 by reference) with a snapshot-binding field: the
manifest records, per snapshot file the plan was computed against, its relative path plus a digest
or byte length; refusal on any absent file, digest mismatch, or length mismatch. State that
`plan_checksum` covers the manifest — so tampering with those recorded digests fails the checksum —
which is what makes the snapshot un-tearable without adding the snapshot bytes to the checksum.

### CHK020 — FR-025 and the durable-ledger exclusion cannot both hold under the crash reading

**Anchor**: `spec.md:356-357` (FR-025), `spec.md:460-461` (Out of Scope), `spec.md:253-255`
(Edge Cases / Partial apply), `spec.md:396-400` (SC-003).

**Evidence.** FR-025 (`spec.md:356-357`): "If an apply stops partway, the operations already written
MUST stay written and the run MUST record the last operation it reported as applied." Out of Scope
(`spec.md:460-461`): "A durable per-operation apply ledger surviving a crash. This outcome records
applied operation identifiers on the run result only." The spec never says whether "stops partway"
covers abnormal termination. It elsewhere assumes it does: SC-003 (`spec.md:397-399`) mandates
"a crash injected after a write commits but before it is recorded". Under that reading, recording
the last applied operation *is* durable crash-surviving progress — precisely the excluded artifact.
Under the exception-only reading both rules hold trivially: `cli.py:346-349` already persists the
run sidecar in an `except Exception` handler via `RunFile.save()` (`sidecars.py:88-90`, atomic
tmp+rename at `sidecars.py:13-24`), which survives an exception but not a SIGKILL.

This is load-bearing rather than academic: the brief's Completion conditions state "no durable
apply ledger is built", so an implementer who reads FR-025 the crash way violates a completion
condition, and one who reads it the exception way arguably under-delivers FR-025.

**Brief vs spec.** Brief-gap carried verbatim. The brief's `§Edge cases and failure behavior`
(Partial apply) and `§Out of scope` (ledger) hold the same unqualified pair; the brief should have
written "If apply stops partway **with a reported error**". The spec inherited the ambiguity without
resolving it and can resolve it without changing scope.

**Minimum fix.** Add one clause to FR-025: "'Stops partway' means an apply that terminates with a
reported error. Recording is best-effort at that point and is explicitly not required to survive
abnormal process termination — durable crash-surviving progress is out of scope."

### CHK035 — nothing verifies the manifest's run identifier against the run being applied

**Anchor**: `spec.md:281-283` (FR-004), `spec.md:36-49` (AD001), `spec.md:298-300` (FR-009),
`spec.md:312-314` (FR-012).

**Evidence.** FR-004 requires "a plan manifest binding it to its run", and the same sentence requires
the checksum to "exclude only the checksum field itself, the run identifier, and the creation
timestamp" (`spec.md:281-283`; AD001 repeats it at `spec.md:42-46`, correctly, because SC-006
[`spec.md:411-414`] needs the manifest byte-identical across re-plans). FR-009's verification set is
closed at three checks and does not include the run binding. So a `plan/` directory copied from run
X into run Y's directory verifies **clean**: the checksum recomputes over the manifest minus the run
identifier plus the same `operations.jsonl` bytes and matches. FR-012 (`spec.md:312-314`) then applies
"exactly the stored operations" under run Y's identity, and FR-020 records those identifiers on run
Y's result. The feature's own headline — "provably the operations that were reviewed" for *this* run
(`spec.md:16-19`) — is not enforced by any stated check.

The run directory is trivially reachable: `run_dir()` is `cache_root/<sync_name>/<run_id>`
(`cache/paths.py:56-59`), and `apply --run-id` takes an arbitrary segment (`cli.py:301`).

**Brief vs spec.** Spec defect. The brief owns the manifest contract (`§Dependencies and shared
contracts`) and DBR-006 lists the three bindings; the spec introduced the checksum-exclusion rule
(via AD001) that makes the run binding unprotected, so the compensating equality check belongs here.

**Minimum fix.** Add to FR-009: "The apply MUST also verify that the manifest's recorded run
identifier equals the run being applied, and refuse on mismatch. This is a separate equality check
rather than a checksum input, because SC-006 requires the run identifier to be excluded from the
checksum."

### CHK038 — the apply-side supplier of the configuration-version value is unspecified

**Anchor**: `spec.md:308-311` (FR-011), `spec.md:145-147` (User Story 2 scenario 2),
`spec.md:438-442` (SC-013), `spec.md:382-384` (Key Entities), `spec.md:485-486` (Assumptions).

**Evidence.** FR-011 (`spec.md:308-311`): the manifest field holds "a deterministic content checksum
computed over the configuration the run used, unless the caller supplies a version identifier
explicitly, in which case that value MUST be stored verbatim. Either way the value MUST be treated
as opaque at apply: compared for equality and never parsed." Compared **against what** is never
stated. User Story 2 scenario 2 (`spec.md:145-147`) says "differs from the current one", implying
recomputation at apply — but a plan whose manifest stored a *caller-supplied verbatim* value has
nothing to recompute, so under recomputation-at-apply every such plan is refused unconditionally.
SC-013 (`spec.md:438-442`) requires that "an arbitrary opaque string round-trips unchanged through
manifest write **and apply comparison**"; with no apply-side supplier for a caller-supplied value,
that round-trip cannot be closed, so DBA-013 is unachievable as specified. FR-011's closing
sentence, "No new user-facing input is introduced", forecloses a CLI flag, and the CLI's `apply`
command today takes only `name`/`config-file`/`directory`/`run-id`/`branch` (`cli.py:296-302`).

**Brief vs spec.** Brief-gap plus spec defect. The brief's DBR-018 row and its
`§Dependencies and shared contracts` "Configuration-version value" row describe only the plan-time
side ("the manifest stores and compares whatever stable identifier the caller supplies"); neither
says who supplies the comparison value at apply. The spec repeated the asymmetry.

**Minimum fix.** Add to FR-011: "At apply, the comparison value is recomputed by the same default
rule unless the in-process caller supplies one explicitly, in which case the supplied value is
compared verbatim. The CLI apply path uses the default rule only."

---

## RECOMMENDED and NIT defects

### CHK002 — verification set is not exhaustive (format version unverified)

**Anchor** `spec.md:298-300` (FR-009), `spec.md:302-306` (FR-010), `spec.md:338-343` (FR-019),
`spec.md:368-370` (Key Entities). The manifest "records the format version" (`spec.md:369`) but no
requirement verifies it: FR-009's set is checksum, configuration version, snapshot binding; FR-019's
v1 detection is presence-based and explicitly "MUST NOT depend on parsing a v1 artifact"
(`spec.md:340-341`). A future v3 manifest would be read as v2. **Fix**: add to FR-009 that the apply
verifies the manifest's format version is one this reader supports and refuses otherwise.

### CHK003 — no verification ordering, and all-vs-first reporting unstated

**Anchor** `spec.md:300`, `spec.md:401-407`. FR-009 says "naming the failed check" (singular) and
SC-004 asserts against it, but nothing fixes evaluation order or whether all failures are named when
several hold. **Fix**: state a fixed order (cheapest/structural first) and whether the message names
one or all.

### CHK004 — re-apply of an already-applied run is permitted by no requirement

**Anchor** `spec.md:160-184` (User Story 3), `spec.md:312-314` (FR-012), `spec.md:393-395` (SC-002).
US3 and SC-002 require the second apply to succeed; no FR states that a run in the applied state
remains appliable, so a defensive "already applied" guard would satisfy the FRs and break SC-002.
**Fix**: one clause in FR-012 — a run in the applied state MAY be applied again; convergence, not
state, is what protects the destination.

### CHK005 — "before any destination write" is not an ordering obligation over adapter construction

**Anchor** `spec.md:298-300` (FR-009), `spec.md:293-297` (FR-008). FR-008 shows the spec knows how to
state this when it wants to: review "MUST NOT construct an adapter or extract either side"
(`spec.md:295`). FR-009 has no analogue. Verified: the current apply path constructs **both**
adapters before any verification (`utils.py:183-235`, invoked from `cli.py:313-318`), and
`InfrahubAdapter.__init__` makes live destination calls at construction
(`adapters/infrahub.py:311`, `:317` — `InfrahubClientSync(...)` then `self.client.get(kind="CoreAccount", …)`).
`utils.py:244-246` also creates the run directory and `utils.py:256-263` writes
`schema-sub-hash.txt` before any check runs. **Fix**: state in FR-009 that verification precedes
adapter construction and any destination connection, or state explicitly that it does not and only
writes are ordered.

### CHK006 — no refusal-message content bar

**Anchor** `spec.md:300`, `spec.md:337` (FR-018). FR-018 binds "the plan artifact or any review
output" — an error message is neither, so a refusal quoting a payload is not covered, while
`dev/constitution.md` §VI requires that "error messages MUST NOT leak internal details". **Fix**:
extend FR-018 to refusal and failure messages, and require the message to identify the run.

### CHK007 — no rule separating pre-write refusal from post-application failure

**Anchor** `spec.md:298-300` (FR-009), `spec.md:334-336` (FR-017), `spec.md:338-343` (FR-019),
`spec.md:352-353` (FR-023). Deletes are visible in the artifact before any write, so an implementer
could pre-scan and refuse — satisfying FR-009's phrasing while contradicting FR-017/SC-007's
"applies its non-delete operations". FR-017 answers the delete case but no general classification
rule exists. **Fix**: state that FR-009's verification set is closed and that operation
supportability is evaluated during execution, never as a pre-apply refusal.

### CHK008 — what the run result records on refusal is unstated

**Anchor** `spec.md:298-300`, `spec.md:344-345` (FR-020). SC-004 asserts a resulting run state but
nothing says whether an empty applied-identifier set is written or the field is absent — which
matters to any consumer reading `run.json` (`sidecars.py:69-90`). **Fix**: one sentence in FR-020.

### CHK009 — the configuration inputs the checksum rule covers are undefined

**Anchor** `spec.md:308-311` (FR-011), `spec.md:485-486`. "Computed over the configuration the run
used" and "the configuration's declared content" do not say file bytes vs parsed model, nor whether
included/templated content or environment substitution participates. **Fix**: name the input set
(e.g. the canonicalized parsed configuration for this sync project) in FR-011.

### CHK013 (NIT) — FR-023 has no content bar

**Anchor** `spec.md:352-353`, `spec.md:235-237`. The Edge Cases entry anchors to "the behavior the
engine already has today" — which is a real bar: `potenda/__init__.py:354-360` names the adapter
class and directs the operator to `infrahub-sync sync`. FR-023 omits that anchor. **Fix**: carry the
"as today" anchor into FR-023.

### CHK014 — "the last operation it reported as applied" is under-specified

**Anchor** `spec.md:356-357` (FR-025), `spec.md:344-345` (FR-020), `spec.md:312-314` (FR-012).
Sequence is inferable from FR-012's dependency order, but the record is not named, and SC-003
(`spec.md:397-399`) establishes that "written" and "recorded" differ. **Fix**: state that "last" means
last in the dependency order actually executed, recorded in the FR-020 field.

### CHK015 — SC-004's case list omits the absent snapshot

**Anchor** `spec.md:141-144` (US2 sc1), `spec.md:148-150` (US2 sc3), `spec.md:302-306` (FR-010),
`spec.md:401-407` (SC-004). The story and FR-010 both cover a **removed/absent** snapshot; SC-004's
five cases are checksum mismatch, configuration-version mismatch, snapshot-binding mismatch, absent
operations, truncated snapshot. Origin is the **brief**: DBA-004's evidence column enumerates the
same five while the brief's Scenario 3 and its Edge-case "Torn artifact" bullet cover absence.
**Fix**: add a sixth case (absent snapshot), or restate as "each bound element absent, truncated, or
mismatched".

### CHK016 — v1 detection cannot separate a crashed new-format plan write (CONFIRMED)

**Anchor** `spec.md:46-49` (AD001), `spec.md:338-343` (FR-019), `spec.md:302-306` (FR-010).
AD001: "a run directory with `plan.parquet` but no `plan/manifest.json` is what identifies a v1
plan"; FR-019 repeats it as a MUST and adds that the pre-existing file "MUST be left in place".
Verified in the tree: every non-apply run writes `plan.parquet` today —
`cli.py:152` (`diff`) and `cli.py:271` (`sync`) call `Potenda.write_plan`
(`potenda/__init__.py:333-339`), plus `potenda/__init__.py:462` and `:496-499` in `sync_in_tiers`.
`write_plan` → `parquet_io.py:81-84` → `write_table` (`parquet_io.py:56-71`) is atomic tmp+rename, so
the parquet file itself is all-or-nothing — but the *ordering* window between it and the new
`plan/manifest.json` is not closed by anything the spec says. A new-format plan run that dies in that
window presents byte-for-byte as AD001's v1 signature, so it is diagnosed "v1 — re-plan" instead of
"incomplete/torn". FR-010's torn rule cannot catch it either, because FR-010 is conditioned on the
manifest existing. Not rated BLOCKING because both classifications refuse and write nothing, so no
DBA fails; the harm is a misleading diagnosis. The spec is also silent on whether a new-format plan
run continues to write `plan.parquet` at all, which is what makes the collision permanent rather
than transitional. **Fix (minimum)**: make v1 detection depend on a recorded marker rather than file
presence — e.g. the plan run records its plan-format version in the run sidecar before writing any
plan bytes, and a run with that marker but no `plan/manifest.json` is torn, not v1. Alternatively
state that a new-format plan run does not write `plan.parquet`, making its presence unambiguous.

### CHK018 — FR-020 and FR-025 records are not related to each other

**Anchor** `spec.md:344-345`, `spec.md:356-357`. One is a set of applied identifiers, the other a
"last operation" pointer; whether the pointer is derived from the set or stored separately is
unstated. **Fix**: state that the last-applied pointer is the final element of the FR-020 record.

### CHK019 — FR-023 is not placed relative to FR-009

**Anchor** `spec.md:352-353`, `spec.md:298-300`. FR-023 says "before any write is attempted", FR-009
"Before any destination write" over a closed three-check set; the relative order is unstated. Today
the surface check runs first inside `apply_plan` (`potenda/__init__.py:354-360`) before `read_plan`
(`:362`). **Fix**: name the order explicitly.

### CHK022 — SC-004 does not say how zero writes are evidenced

**Anchor** `spec.md:401-407`; compare SC-002 (`spec.md:393-395`), which does name its observation
("object counts and identities recorded against a live destination"). **Fix**: state the observation
(destination object counts unchanged, or no invocation of the planned-write surface).

### CHK024 — SC-005 does not say where the apply-side identifiers are read from

**Anchor** `spec.md:408-410` ("the apply result"), `spec.md:344-345` (FR-020, "the run result").
Two terms, no stated identity, and "both identifier sets" leaves per-operation pairing optional.
**Fix**: point SC-005 at the FR-020 record and require per-operation correspondence.

### CHK026 — FR-025 has no criterion and no post-crash observability rule

**Anchor** `spec.md:356-357`, `spec.md:460-461`, and the traceability table (`spec.md:506-540`), which
lists no row for FR-025. Same root as CHK020. **Fix**: after scoping FR-025 to reported errors, add a
criterion asserting the recorded pointer after a mid-apply error, or state explicitly that FR-025 is
verified only through SC-003's convergence evidence.

### CHK027 — FR-020 has no criterion of its own

**Anchor** `spec.md:344-345`, `spec.md:408-410`, `spec.md:506-540`. FR-020 appears in no traceability
row and is exercised only incidentally through SC-005. **Fix**: extend SC-005's evidence to assert
the FR-020 record on the run result.

### CHK028 — SC-007's message content is not resolvable

**Anchor** `spec.md:415-418`, `spec.md:334-336`. "Naming the unsupported operation" could mean the
identifier, the action, or the object. Since FR-003 exists precisely so identifiers link review to
application, the identifier is the natural answer but is not stated. **Fix**: require the message to
name the operation identifier and the action.

### CHK029 — an apply naming a non-existent run is unaddressed

**Anchor** `spec.md:312-314` (FR-012). Verified failure mode: `get_potenda_from_instance` creates the
directory unconditionally (`utils.py:244-246`, `rdir.mkdir(parents=True, exist_ok=True)`) and may
write `schema-sub-hash.txt` into it (`utils.py:256-263`), so an unknown `--run-id` produces a
spurious run directory. Such a directory has neither `plan.parquet` nor `plan/manifest.json`, so it
matches neither FR-019's v1 rule nor FR-010's torn rule — behavior is undefined by the spec.
**Fix**: add a requirement that an apply for a run with no plan artifact is refused with a message
naming the run, and that no run directory is created for an unknown run identifier.

### CHK030 — concurrency is unmentioned though an existing lock both covers and constrains it

**Anchor** `spec.md:312-314` (FR-012), `spec.md:290-291` (FR-007), `spec.md:469-489` (Assumptions).
Verified: every CLI command body runs inside `pipeline_lock(sync_instance.name)`
(`cli.py:129`, `:238`, `:312`; implementation `cache/locks.py:20-33`, `timeout=60.0`), so two
concurrent applies of one sync are already serialized — but so is *review*, since AD005 puts review
inside `diff`. A review requested during a long sync would block up to 60 s and then raise
`filelock.Timeout`, which sits awkwardly against SC-009's "at any time after the run"
(`spec.md:423-427`). **Fix**: record the pipeline lock in Assumptions and state that the read-only
review path does not require it.

### CHK031 — destination unreachable mid-apply is classified nowhere *(PRODUCT-AMBIGUITY)*

**Anchor** `spec.md:356-357` (FR-025), `spec.md:334-336` (FR-017). Neither covers transport failure;
whether it is the partial-apply path or a distinct failure class is a product decision the brief's
`§Edge cases and failure behavior` does not carry. No reading is picked here. **Fix (brief-side)**:
the brief's Edge-cases section should state whether a transport failure is a partial apply.

### CHK032 — a destination-rejected operation: stop or continue is unspecified *(PRODUCT-AMBIGUITY)*

**Anchor** `spec.md:334-336` (FR-017), `spec.md:356-357` (FR-025). FR-017 covers *unsupported*
actions; nothing covers an operation the destination refuses. The engine already has a
continue-on-error concept for other paths — `cli.py:190` flag, propagated at
`potenda/__init__.py:74-75`, honored at `adapters/infrahub.py:443` and `:491` — and the `apply`
command deliberately exposes no such flag (`cli.py:296-302`), so the spec's silence leaves a
user-visible policy open. Fail-fast versus continue-and-report is a product decision the brief does
not carry. **Fix (brief-side)**: the brief's `§Edge cases and failure behavior` should state the
apply-time write-error policy.

### CHK033 — empty-plan verification is ambiguous against FR-022

**Anchor** `spec.md:349-351` (FR-022), `spec.md:298-300` (FR-009). FR-009's obligation is phrased as
a precondition of a write ("Before any destination write, an apply MUST verify…"); an empty plan
performs none, so verification is arguably skippable, while FR-022 says applying it "MUST be a
successful no-op" — leaving an empty plan with a broken checksum unresolved. **Fix**: state that
verification is unconditional and independent of the operation count.

### CHK036 (NIT) — retry after a corrected refusal is unstated

**Anchor** `spec.md:298-300`, `spec.md:338-343`. Consequence of the missing run-state model; closed by
the CHK001/CHK012 fix plus a clause that a refusal is not terminal for the run identifier.

### CHK039 — the ledger boundary is stated for FR-020 but not FR-025

**Anchor** `spec.md:344-345` (carries "*(scope boundary: run result only, not a durable ledger)*"),
`spec.md:356-357` (carries no marker), `spec.md:460-461`. **Fix**: give FR-025 the same parenthetical.

### CHK040 — no assumption that the configuration-version value recomputes identically

**Anchor** `spec.md:485-486`, `spec.md:308-311`. The Assumptions block says only that "a checksum over
the configuration's declared content satisfies the binding". If the rule is byte-sensitive, adding a
comment or reindenting `config.yml` invalidates every saved plan — an operator-visible consequence
recorded nowhere. **Fix**: record the assumption and the consequence (a benign reformat requires a
re-plan), or state that the checksum is over canonicalized parsed content.

### CHK042 — the pre-adapter-construction assumption is unrecorded and currently false

**Anchor** `spec.md:298-300`, `spec.md:469-489`, `dev/constitution.md` §I. The constitution makes the
non-mutating path the always-safe one, which would suggest a refusal touches nothing; the current
order does the opposite (`utils.py:183-235` → `adapters/infrahub.py:311-341` live calls, before
`cli.py:322-340`). **Fix**: record the assumption explicitly, in the same breath as the CHK005 fix.

---

## SATISFIED items — supporting quotes

- **CHK010** `spec.md:438-442` (SC-013): "an arbitrary opaque string round-trips unchanged through
  manifest write and apply comparison — evidenced by a round-trip test using a deliberately opaque
  value". That is the definite pass condition "never parsed" needs.
- **CHK017** `spec.md:334-336` (FR-017): "An unsupported operation … MUST be reported at apply time
  and MUST fail the run; it MUST NOT be silently skipped. **Supported operations in the same plan are
  still applied.**" With FR-009's closed three-check set (`spec.md:298-300`) and SC-007
  (`spec.md:415-418`), an unsupported operation is consistently not a pre-apply refusal.
- **CHK021** `spec.md:390-392` (SC-001): "showing no comparison-engine diff/sync call on the apply
  path" defines recomputation as a comparison-engine call; FR-009's checksum and binding checks are
  not one. Confirmed against the tree: `apply_plan` (`potenda/__init__.py:341-370`) never calls
  `diff_from`/`sync_from` (`potenda/__init__.py:290`, `:295`).
- **CHK023** `spec.md:392` says "comparison-engine diff/sync call", generalizing the brief's literal
  `diff_from`/`sync_from`, so the criterion does not depend on internal names.
- **CHK025** `spec.md:338-339` (FR-019): "MUST be detected and rejected with a message directing the
  operator to re-plan" — a stated content requirement SC-011 (`spec.md:431-433`) can assert against.
- **CHK034** `spec.md:243`: "An operations section whose length disagrees with the count the manifest
  records is torn" — "disagrees" is direction-neutral; FR-010 (`spec.md:302-306`) carries the count
  field.
- **CHK037** `spec.md:480-482`: "The existing engine and per-run artifact layout are present: a saved
  plan is already read and dispatched per row to the destination's planned-write method, and per-side
  snapshots and run sidecars are already written." Verified: `potenda/__init__.py:123-160`
  (`_write_side_snapshot`), `cache/parquet_io.py:92-142` (`write_resource_side`),
  `cache/sidecars.py:69-90` (`RunFile`), `potenda/__init__.py:341-370` (`apply_plan`).
- **CHK041** AD001-dependent requirements each carry the tag: `spec.md:271-272` (FR-002),
  `spec.md:283` (FR-004), `spec.md:287` (FR-005), `spec.md:306` (FR-010), `spec.md:343` (FR-019),
  `spec.md:366`/`:370` (Key Entities); the ratification mechanism is stated at `spec.md:31-34` and
  the cross-outcome stakes at `spec.md:548-551`.

## Brief-level gaps (for planner feedback)

| Item(s) | Brief section that should have carried it | What it should have said |
|---|---|---|
| CHK001, CHK012 | `§Acceptance criteria` (DBA-004 evidence) and `§Requirements` | Name the run-state vocabulary, or state that the resulting state is the existing `failed` status. |
| CHK015 | `§Acceptance criteria` (DBA-004) | Enumerate the absent-snapshot case, or phrase as "each bound element absent, truncated, or mismatched". |
| CHK020, CHK026 | `§Edge cases and failure behavior` (Partial apply) vs `§Out of scope` (ledger) | Qualify "stops partway" as "terminates with a reported error". |
| CHK031, CHK032 | `§Edge cases and failure behavior` | State the apply-time policy for a destination write error and for a transport failure (fail-fast vs continue-and-report). |
| CHK038 | `§Requirements` (DBR-018) and `§Dependencies and shared contracts` (Configuration-version value) | Say who supplies the comparison value at apply time when the plan-time value was caller-supplied. |

## Product ambiguities (no reading picked)

- **CHK031** — is a mid-apply transport failure the partial-apply path or a distinct failure class?
- **CHK032** — when the destination rejects one operation, does the apply stop or continue?
