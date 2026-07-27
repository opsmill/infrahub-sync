---
description: "Dependency-ordered task list for the saved plan artifact and apply-exactly-what-was-reviewed"
---

# Tasks: Saved plan artifact and apply-exactly-what-was-reviewed

**Feature**: `001-plan-artifact-saved-apply-infp-653` | **Date**: 2026-07-26

**Input**: [spec.md](./spec.md) (FR-001…FR-030, SC-001…SC-018, AD001–AD053),
[plan.md](./plan.md) (phases A–G, V1–V39), [research.md](./research.md) (PD-001…PD-010),
[data-model.md](./data-model.md), [quickstart.md](./quickstart.md), [contracts/](./contracts/).

**Tests**: required. The specification states eighteen measurable success criteria, each of which
names its evidence, so every criterion below has a task that produces it. Twelve criteria and two
half-criteria run locally; SC-001, SC-002, SC-003, SC-008 and the live halves of SC-007 and SC-016
need a running Infrahub and carry the repository's existing `integration` marker
(`pyproject.toml:133-135` — skipped by default, opted into with `-m integration` plus
`INFRAHUB_ADDRESS` and `INFRAHUB_API_TOKEN`).

## Evidence deferral, stated up front (AD045b)

Per AD007 no Infrahub is reachable in this environment. **Phase H produces no evidence here.** **Five**
of the brief's criteria — **DBA-001, DBA-002, DBA-003 and DBA-008 in full, plus the live half of
DBA-007** — remain deferred to a run against a live Infrahub, as does the live half of **SC-016**,
which this specification derived rather than took from the brief and which therefore does not count
against the brief's tally. So the brief's completion condition — "every requirement and
acceptance criterion above has inspectable passing evidence" — **is not met at merge time**. Phase H's
tasks author the tests; they do not close the criteria.

Two things narrow the exposure and neither substitutes for the deferred run:

- **T081**, a local mutation-payload conformance harness against a mocked SDK, catches the class of
  defect those criteria were the only other check on — AD042 was exactly that class.
- **T045's HFID-component assertion** (per component shape, AD051) refuses to issue an unkeyed write at
  all, so the same class of defect fails loudly rather than duplicating silently.

Do not record Phase H as satisfied on the strength of either.

**Organization**: grouped by the implementation phases plan.md fixes (A–G), because those phases are
what keeps the tree green at every boundary before F — A, B and C are pure additions, D changes what
a plan run writes, E adds the write surface, F changes the CLI, G closes the fallout. A Setup group
precedes A and a live-evidence group follows G. Each task also carries the user story it serves, so
story-level traceability is preserved; the mapping is in
[User-story coverage](#user-story-coverage).

## Format

```text
- [ ] [TaskID] [P?] [Story?] Description with file path — requirements it serves
```

- **[P]**: parallelizable — different file, no dependency on an incomplete task.
- **[Story]**: `[US1]`…`[US5]` from spec.md; omitted on infrastructure and cross-cutting tasks.
- Each task states, under **Done when**, the observable that proves it complete. No task is complete
  by opinion.

## Twelve decisions settled after spec.md's first draft

All twelve are now recorded in spec.md's Clarifications and override any earlier reading. Each has an
owning task.

| Decision | Rule | Owning tasks |
|---|---|---|
| **AD037 / PD-008** | The source-snapshot digest covers **logical rows with `_extract_ts` excluded**, not raw file bytes. `_tombstone` and `_source_id` stay in the digest | T007, T013 |
| **AD038 / PD-005** | Replace-set for cardinality-many relationships is **enforced explicitly after the upsert**, not assumed of it | T042, T046, T051 |
| **AD039 / PD-009** | The tiered sync branch computes **all** tier diffs and writes the artifact **before** the first destination write. The `top_level` narrowing goes in the **compute** loop — it governs diff computation, not execution (V33) | T035, T040 |
| **AD040 / PD-010** | The pre-existing `apply_cached_row` dispatch is **removed**, not left wired — and the test asserting it is rewritten in the **same phase**, immediately after | T048, T066 |
| **AD041 / PD-003** | The default configuration-version checksum covers the parsed configuration with `directory` excluded and `settings` included. Consequence: rotating a credential invalidates every saved plan | T008, T014 |
| **AD042** | An operation's payload is `element.keys ∪ element.source_attrs` — **the identity components are in the payload**. `source_attrs` alone carries none, so the upsert would be unkeyed and every re-apply would duplicate | T028, T036, T045, T050, T081 |
| **AD043** | A peer identity component that is itself a reference records a nested `{peer_kind, identity}` pair, **recursively**, never a raw unique-id string | T029, T036, T044, T079 |
| **AD044** | FR-024 warns on **both** conditions: HFID absent or incomplete, **and** no uniqueness constraint covering the plan's identity attributes | T033, T039 |
| **AD045** | (a) A local mutation-payload conformance harness catches an AD042-class defect offline; (b) the live criteria are **deferred**, stated explicitly, not covered | T081; the deferral note above |
| **AD046** | A peer's kind is **never** the mapping's `reference` — `DcimDevice` is declared twice with different `location` references (V31). *How* it is obtained is fixed by AD050 | T029, T082 |
| **AD047** | A plan-derivation failure **fails the command**, on `diff` as on `sync`. No `--continue-on-error` on `diff`, no warn-and-skip | T029, T032, T083 |
| **AD048** | The zero-match and multi-match refusals are scoped to the **new apply-path resolver only**; the live `sync` write path's warn-and-continue is unchanged | T044, T053 |
| **AD049** | A derived **delete**'s identity is canonicalised by the same recursive rule as any other operation's, with nested peer kinds probed against the **destination** store. Deletes are not carved out of FR-002 | T032, T037, T084 |
| **AD050** | AD046's peer-kind lookup is a **bounded probe**: candidates are the kinds the mapping declares as `reference` for that field across every entry for the owning kind; each is probed in the store; **zero and >1 both fail the command**; **no fallback** to the mapping-declared kind, not even for a one-candidate set (V37) | T029, T032, T082 |
| **AD051** | The HFID-component assertion is defined **per component shape** — direct: present and non-null in `data`; relationship-crossing: `<rel>` present and non-null in `data` **and** the operation's nested `{peer_kind, identity}` supplies `<attr>`. The SDK client-store dependency for nested HFID formation is a recorded risk (V39) | T045, T081 |
| **AD052** | FR-024's warning is scoped to destinations that **expose a schema**; where none is exposed it is skipped and is never an error (V38). Regression assertion: `diff` against a non-Infrahub destination still succeeds | T033, T039, T085 |
| **AD053** | FR-009 now **admits** the format-version short-circuit: the gate reports its one failure and says the remaining four were not evaluated; once it passes, all four are evaluated and every failure named | T021, T025 |

## Three test-design traps

Each makes a criterion silently unsound if missed. Each is encoded in a task rather than left as
advice.

1. **SC-006 (T041)** compares two consecutive plan runs byte-for-byte. The engine may switch to
   incremental extraction on the second run, and `delete_operations_computed` is inside the
   checksum and is **not** one of the two masked fields. The test MUST pin the same extraction mode
   on both runs and both sides, and MUST assert the pinning held before comparing.
2. **SC-002 / SC-003 / SC-008 (T074)** ride on the destination kind's human-friendly ID being
   declared in the destination schema **and** fully supplied by the plan's identity. A fixture
   lacking it produces duplicates that read as a product bug. The integration fixture MUST assert
   the precondition and fail as a fixture error when it does not hold.
3. **SC-008 (T079)** exercises apply-time peer resolution — but if every referenced peer is created by
   the same plan, dependency-tier ordering fills the resolver's memo and the destination-query path
   **never runs**, so the test passes while the requirement it measures is broken. The fixture MUST
   include at least one referenced peer that **pre-exists at the destination and is absent from the
   plan**, and MUST assert that the query path was taken for it.

---

## Phase S: Setup

**Purpose**: the package skeleton and the one baseline that must be captured *before* the CLI
changes, because SC-012's evidence is a before/after comparison.

- [ ] T001 Create the `infrahub_sync/plan/` package with an empty-but-importable `infrahub_sync/plan/__init__.py`, and the test package `tests/plan/__init__.py` — plan.md Project Structure.
  **Done when**: `uv run python -c "import infrahub_sync.plan"` exits 0 and `uv run pytest -q` is
  unchanged from the pre-task run.
- [ ] T002 [P] Create the `tests/data/` directory (it does not exist in the tree today) and capture the pre-change top-level command list into it as a committed fixture at `tests/data/cli_help_baseline.txt`, produced by `uv run infrahub-sync --help` on the current tree — SC-012.
  **Done when**: `tests/data/` exists and the file inside it lists exactly the five commands `list`,
  `diff`, `sync`, `apply`, `generate` (V19), and contains no Typer group heading. T064 diffs against
  it.

**Checkpoint S**: tree green, no behavior change.

---

## Phase A: Canonical encoding, identity, checksum, configuration version

**Delivers**: FR-003, FR-005, FR-011, FR-028.3, the checksum half of FR-004, the record types for
FR-002/FR-027/FR-028, and the error taxonomy. Pure addition — nothing existing changes.

- [ ] T003 [P] Implement the `PlanArtifactError` hierarchy in `infrahub_sync/plan/errors.py`: `PlanFormatV1Error`, `PlanArtifactTornError`, `PlanFormatVersionError`, `PlanArtifactUnreadableError`, `UnknownPlanKindError`, `DuplicateOperationIdError`, `UnserializablePayloadValueError`, `PlanVerificationError` — [contracts/plan-reader-api.md](./contracts/plan-reader-api.md), Constitution IV.
  **Done when**: all eight import, each derives from `PlanArtifactError`, and `uv run ty check .`
  exits 0.
- [ ] T004 [P] Implement `canonical_json_bytes(value) -> bytes` and `canonical_value(v)` in `infrahub_sync/plan/canonical.py` — `json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False)`, UTF-8, LF only; the PD-002 normalization table (`str|int|float|bool|None` pass through; `datetime`/`date` → ISO-8601; `Decimal` → `str`; `list`/`tuple` recurse in source order; `dict` recurses key-sorted; anything else raises `UnserializablePayloadValueError` naming kind, field and Python type). No `default=` hook — FR-005, PD-002.
  **Done when**: T010 passes.
- [ ] T005 Implement `canonical_identity(mapping)` and `operation_id(action, kind, identity)` in `infrahub_sync/plan/identity.py` — identifier is `"op_" + sha256(canonical_json_bytes([action, kind, canonical_identity(identity)])).hexdigest()[:16]`, the hash input a JSON **array** (PD-001), payload deliberately excluded (AD002) — FR-003, FR-028.3.
  **Done when**: T011 passes, including the worked test vector in
  [contracts/plan-artifact-format.md](./contracts/plan-artifact-format.md).
- [ ] T006 Implement `compute_plan_checksum(manifest_mapping, operations_bytes)` in `infrahub_sync/plan/checksum.py` — sha256 over `canonical_json_bytes(manifest minus plan_checksum, run_id, created_at)` concatenated with the raw operations bytes, **no separator**, the three fields **removed** not blanked (AD035); lowercase hex, no prefix — FR-004, FR-027.8.
  **Done when**: T012 passes.
- [ ] T007 **[AD037 / PD-008]** Implement `source_snapshot_digest(path)` and `source_snapshot_records(run_dir)` in `infrahub_sync/plan/checksum.py` — the digest covers the snapshot's **logical rows**: the Parquet table with the `_extract_ts` column dropped, rows in file order, each row canonically encoded and LF-joined; `_source_id` and `_tombstone` stay in; the record is `{path (run-relative POSIX), digest, row_count}`, list ordered by `path`, source side (`A/`) only — FR-004, FR-010, AD037.
  **Done when**: T013 passes. A raw-bytes digest would make SC-006 unachievable because
  `_extract_ts` is stamped per run into every row (V7); the test asserts the exclusion directly.
- [ ] T008 **[AD041 / PD-003]** Implement `default_config_version(config)` and the caller-supplied path in `infrahub_sync/plan/config_version.py` — default is `sha256(canonical_json_bytes(config.model_dump(mode="json", exclude={"directory"})))`, `settings` **included**, `directory` **excluded** (it is an absolute path); a caller-supplied value is validated against `^[\x20-\x7e]+$` and stored verbatim — FR-011, AD041.
  **Done when**: T014 passes.
- [ ] T009 Define the Pydantic v2 record types in `infrahub_sync/plan/models.py` — `RelationshipReference`, `PlannedOperation`, `SourceSnapshotRecord`, `PlanManifest`, `PlanSummary`, `VerificationFailure`, and the constants `PLAN_FORMAT_VERSION = 2`, `SUPPORTED_FORMAT_VERSIONS = frozenset({2})`, `ACTIONS`, `CHECKSUM_EXCLUDED_FIELDS`, `SC006_MASKED_FIELDS` — exactly as [data-model.md](./data-model.md) specifies, with `PlanManifest` on `ConfigDict(extra="allow")` and `PlannedOperation` on a closed field set — FR-002, FR-027, FR-028.
  **Done when**: T015 passes and `uv run ty check .` exits 0.
- [ ] T010 [P] Write `tests/plan/test_canonical.py` — byte stability under dict key reordering; a payload's list-valued attribute preserved in source order and not re-sorted; each row of the PD-002 type table; an out-of-table type raises `UnserializablePayloadValueError` naming kind, field and type — FR-005.
  **Done when**: the file passes and every PD-002 table row is a parametrized case.
- [ ] T011 [P] Write `tests/plan/test_identity.py` — the contract's worked test vector for the exact hash input bytes; identifier stable across re-derivation; identifier unchanged when only the payload changes; identifier changes when any of action, kind or identity changes; identifier matches `^op_[0-9a-f]{16}$`; identity key order does not affect the result — FR-003.
- [ ] T012 [P] Write `tests/plan/test_checksum.py` (checksum cases) — exactly `plan_checksum`, `run_id` and `created_at` are excluded and are **removed** rather than blanked (a manifest with those keys set to `null` hashes differently from one with them absent); an unknown extra manifest field changes the checksum, proving it is inside the checksummed bytes; the two byte sequences are joined with no separator — FR-004, FR-027.
- [ ] T013 [P] **[AD037]** Write `tests/plan/test_checksum.py` (snapshot-digest cases) — the digest is **invariant** to `_extract_ts` (two tables identical but for that column digest equal) and **sensitive** to `_source_id`, to `_tombstone`, to row order, and to any data value; `row_count` is the table's row count; records are ordered by `path`; only the `A/` side is recorded — FR-004, FR-010, AD037.
- [ ] T014 [P] **[AD041]** Write `tests/plan/test_config_version.py` — determinism across two loads of the same file; two configurations differing **only** in `directory` produce the **same** value; two differing in `settings` produce **different** values; an arbitrary printable-ASCII caller value round-trips verbatim and is never parsed; empty and non-printable supplied values are rejected — FR-011, SC-013 (plan-side half), AD041.
- [ ] T015 [P] Write `tests/plan/test_models.py` — `payload` is `None` if and only if `action == "delete"`; `operation_id` is recomputed on construction and a mismatched stored value is rejected; **the model-level identity-in-payload guard holds: on a `create` and on an `update`, every key of `identity` must appear either in `payload` or as the `field` of a `RelationshipReference`, and a record where a key appears in neither is rejected at construction** — this is the AD042 guard at the type level and it has to be asserted here rather than only at T036's derivation level, because without it a payload built from `source_attrs` alone validates cleanly and produces unkeyed writes at apply; `cardinality == "one"` requires exactly one peer; an absent `relationships` and an empty list are distinct and neither is emitted for the other case; **no field at either level expresses a grouping of operations into write units**, asserted over the model field sets so a future addition trips it; `PlanManifest` tolerates unknown fields, `PlannedOperation` does not — FR-002, FR-026, FR-027, FR-028.

**Checkpoint A**: `uv run pytest -q`, `uv run invoke lint` and `uv run ty check .` all pass; no
existing behavior changed.

---

## Phase B: Artifact writer

**Delivers**: FR-004's assembly, FR-010's count field, FR-019's write-order clause, FR-021, FR-022,
FR-026, FR-027. Still a pure addition — nothing calls the writer yet.

- [ ] T016 Implement `write_plan_artifact(*, run_dir, run_id, config_version, source_snapshot, deletes_computed, operations) -> PlanManifest` in `infrahub_sync/plan/writer.py` — sorts operations by `(tier, operation_id)`; asserts identifier uniqueness and raises `DuplicateOperationIdError` naming both operations' kind, action and identity; writes `<run_dir>/plan/operations.jsonl` **first** and `<run_dir>/plan/manifest.json` **last**, each tmp+`Path.replace` in the discipline at `infrahub_sync/cache/sidecars.py:13-24` (V8); a zero-operation plan yields a present, zero-byte operations file and `operations_count: 0` — FR-004, FR-010, FR-019, FR-021, FR-022, FR-026, FR-027.
  **Done when**: T017, T018 and T019 pass.
- [ ] T017 [P] Write `tests/plan/test_writer.py` (core cases) — write order observable via a monkeypatched atomic writer; a failure during the operations write leaves **no** manifest; ordering is by tier then identifier; a duplicate identifier raises and leaves no manifest, failing the plan run; an empty plan writes a zero-byte operations file with count 0; the manifest carries exactly the eight FR-027 fields plus tolerated unknowns — FR-010, FR-019, FR-021, FR-022, FR-027.
- [ ] T018 [P] Write `tests/plan/test_writer.py` (writer-level determinism) — writing identical content twice produces byte-identical `operations.jsonl` and a `manifest.json` byte-identical after removing `run_id` and `created_at` from both sides; masking is key removal applied symmetrically — FR-005, supporting SC-006 (the end-to-end criterion is T041).
- [ ] T019 [P] Write `tests/plan/test_writer.py` (FR-026 at the byte level) — parse every written operation line and the manifest and assert no key at either level groups operations into write units; the assertion enumerates the permitted key sets so an added key fails — FR-026.

**Checkpoint B**: tree green; the artifact can be written by a caller but nothing calls it.

---

## Phase C: Reader, verifier, review

**Delivers**: FR-006, FR-007, FR-009, FR-010, FR-019's detection rule, FR-027's version refusal and
unknown-field tolerance, FR-029. Still a pure addition.

- [ ] T020 Implement `load_plan_artifact(run_dir) -> LoadedPlan` in `infrahub_sync/plan/reader.py` — classify **before** parsing: `plan/` absent entirely → `PlanFormatV1Error` naming the run identifier, the expected artifact path and the re-plan instruction; `plan/` present without a complete manifest, or manifest present with `operations.jsonl` absent, or line count ≠ `operations_count` → `PlanArtifactTornError` naming which part is torn; unreadable path → `PlanArtifactUnreadableError` naming the path; `format_version` outside `SUPPORTED_FORMAT_VERSIONS` → `PlanFormatVersionError` naming the version found and the versions supported, textually distinct from the v1 message; unknown manifest fields preserved verbatim — FR-007, FR-010, FR-019, FR-027.
  **Done when**: T024 passes.
- [ ] T021 **[AD053]** Implement `verify_plan(*, run_dir, run_id, config_version, write_surface_available) -> list[VerificationFailure]` in `infrahub_sync/plan/verify.py` — the five FR-009 checks in order (format version → run-identifier equality → plan checksum → source-snapshot binding → configuration version) plus the write-surface check in the same pre-write gate; check 1 is a **gate** that short-circuits 2–5 with a message saying they were not evaluated and why (PD-006), which FR-009 now states explicitly rather than merely tolerating (AD053), and checks 2–5 are all evaluated with every failure named once the gate passes; an absent or line-count-disagreeing operations file is reported as **torn**, not as a checksum mismatch; each failure carries the check name, the refused run identifier, expected/found where neither is secret, and the operator's next action. The function writes nothing and records no run state — FR-009, FR-010, FR-011, FR-023.
  **Done when**: T025 passes.
- [ ] T022 Implement `read_saved_plan(*, sync_name, run_id, config=None) -> SavedPlan` in `infrahub_sync/plan/review.py` — locates the run as `cache_root_for(sync_name)/run_id` reusing `_require_safe_segment`'s traversal guard (`infrahub_sync/cache/paths.py:11-23`); returns **data** (`.manifest`, `.checksum_ok`, `.verification_notes`, `.summary()`, `.operations(kind=None)`); renders a plan that would fail verification rather than refusing (AD031); never writes a stream, never creates or modifies anything in the run directory, never mutates run state; a `kind` matching no operation or naming a kind the configuration does not declare raises `UnknownPlanKindError` naming the kind — FR-006, FR-007, FR-029.
  **Done when**: T026 and T027 pass.
- [ ] T023 Re-export exactly `read_saved_plan` and the public record types from `infrahub_sync/plan/__init__.py`, and add a test asserting `__all__` names no other reading surface — FR-029 ("exactly one supported entry point"), AD029.
  **Done when**: a test enumerates `__all__` and fails if a second read path is exported.
- [ ] T024 [P] Write `tests/plan/test_reader.py` — a pure unit test of the reader's classification, with **no apply and no destination in scope**: this phase adds no apply path, so no test here may assert a destination write count or a run-sidecar state. **SC-011 (reader half)**: a v1 fixture (a run directory holding `plan.parquet` and no `plan/`) raises `PlanFormatV1Error` with the re-plan message; torn fixtures (missing operations file, count mismatch, unparseable manifest) reject on the torn path with distinct messages; an unreadable path names the path; unknown manifest fields survive a read/write round trip; **SC-018 (reader half)**: `format_version: 99` raises `PlanFormatVersionError` naming the version found and the versions supported, with the message text asserted **different** from the SC-011 message. The zero-writes and `failed`-run-state halves of SC-011 and SC-018 are produced by **T065** on the Phase F CLI apply path, where an apply actually exists — FR-010, FR-019, FR-027, SC-011, SC-018.
- [ ] T025 [P] Write `tests/plan/test_verify.py` — a pure unit test of `verify_plan`'s return value, with **no apply and no destination in scope**, for the same reason as T024; `verify_plan` writes nothing and records no run state by construction, so asserting either here would be asserting against a stub. **SC-004 (verifier half)** as six parametrized cases (checksum mismatch, configuration-version mismatch, snapshot-binding mismatch, absent operations, truncated snapshot, **absent snapshot**), each asserting the returned `VerificationFailure` list is non-empty and names the expected check, with the expected/found values populated; **SC-015 (verifier half)**: a `plan/` directory copied from one run directory into another yields a `run_binding` failure; the PD-006 / AD053 gate short-circuits 2–5, returns that one failure and no other, and its message says the remaining checks were not evaluated; when the gate passes, two simultaneous failures are **both** named — assert both halves, since the gate and the evaluate-all rule are each other's exception. The zero-writes and `failed`-run-state halves of SC-004 and SC-015 are produced by **T065** — FR-009, FR-010, SC-004, SC-015.
- [ ] T026 [P] Write `tests/plan/test_review.py` — summary reports a count per action and a count per kind; per-object detail carries at least operation identifier, action, destination kind and destination identity; `kind` narrows; a `kind` miss and a kind the configuration does not declare each raise `UnknownPlanKindError` naming the kind rather than returning empty; a zero-operation plan produces a summary that **states** the plan has no operations; a plan whose checksum fails is still rendered with `checksum_ok` false and a note; no `run.json` is written by any review call — FR-006, FR-022, FR-029, AD031.
- [ ] T027 [P] Write `tests/plan/test_review.py` (stored-artifact, new-process cases) — **SC-009's in-process half**: summary and per-object detail both produced in a **subprocess** that reads a stored artifact, with source and destination unreachable (adapter env vars unset and the adapter import patched to raise), proving the producing process need not be alive and no adapter is constructed — FR-007, FR-029, SC-009.

**Checkpoint C**: tree green; the artifact can be written, read, verified and reviewed, and nothing
in the engine or CLI has changed yet.

---

## Phase D: Plan derivation and engine wiring

**Delivers**: FR-001, FR-002's population, the plan side of FR-012, FR-015, FR-018's write-side
rule, FR-024, FR-028's obligation levels. This is the first phase that changes what a plan run
writes.

- [ ] T028 **[AD042]** [US1] Implement `operations_from_diff(diff, *, config, tier_of, source_adapter)` in `infrahub_sync/plan/derive.py` — walk `diff.children` as `_diff_to_rows` does (V2) but keep what it discards: `element.keys` → destination identity, `element.type` → kind, `element.action` → action (V5), and **`payload = element.keys ∪ element.source_attrs`** (minus keys that become relationship references in T029). The union is load-bearing, not tidiness: `source_attrs` is built from `get_attrs()`, whose contract is "does not include the fields in `_identifiers`" (`.venv/…/diffsync/__init__.py:340-347`, called at `.venv/…/diffsync/helpers.py:223`), and the generator strips identifiers out of `_attributes` (`infrahub_sync/generator/__init__.py:95`), so a payload taken from `source_attrs` alone carries **no identity field at all** — `get_human_friendly_id()` then returns `None`, the mutation carries neither `id` nor `hfid`, the upsert is unkeyed, and every re-apply duplicates (SC-002 and SC-003 unachievable). Today's create path converges only because it passes both (`infrahub_sync/adapters/infrahub.py:602-604`, V36). Elements whose action is `delete` are **skipped here** so deletes come from one source only (FR-015) — FR-002, FR-028.4, AD042.
  **Done when**: T036 passes, including its assertion that every identity key of a create or update
  appears in `payload` or as a relationship-reference `field`.
- [ ] T029 **[AD043 / AD046 / AD047]** [US5] Implement the payload/relationship split in `infrahub_sync/plan/derive.py` — a payload key whose `SchemaMappingField.reference` is set becomes a `RelationshipReference` instead of a payload field, **including when that key is an identity component**, in which case it appears in `identity` and in `relationships` but not in `payload`. The reference carries the peer's **identity mapping**, obtained by looking the peer up in the loaded source store by its DiffSync unique-id and calling `get_identifiers()`, **never** by splitting the unique-id on `__`; and the peer's **kind established by a bounded store probe (AD050)**, not from the mapping's `reference` value (AD046) — `DcimDevice` is declared by two schema-mapping entries with different `location` references (`examples/netbox_to_infrahub/config.yml:212` → `LocationRack`, `:254` → `LocationSite`, V31), so the mapping alone is ambiguous and a wrong pick fails the whole apply run on the qualified path. **The probe, exactly (AD050, V37)**: AD046's "the store entry knows its own kind" is not implementable — `BaseStore.get(*, model, identifier)` and `LocalStore.get` both require `model` and select the per-model bucket before touching the identifier (`.venv/…/diffsync/store/__init__.py:40-52`, `.venv/…/diffsync/store/local.py:30-49`), and the only kind-free store call, `get_all_model_names()` (`.venv/…/diffsync/store/local.py:22-28`), enumerates kinds rather than answering for a unique-id. So build the candidate set as the kinds declared as `reference` for that field across **every** `schema_mapping` entry whose `name` is the owning destination kind (`{LocationRack, LocationSite}` for `DcimDevice.location`, `examples/netbox_to_infrahub/config.yml:239`, `:281`), probe each with `store.get(model=candidate, identifier=uid)` treating `ObjectNotFound` as "not this kind", and take the single hit. **Zero hits and more than one hit both fail the command**, naming the owning kind, the field, the unique-id and the candidates tried (FR-030). **No fallback to the mapping-declared kind in either arm, and a one-candidate set is probed like any other** — returning an unprobed sole candidate *is* the mapping-derived answer AD046 forbids. A peer identity component that is **itself** a reference recurses into a nested `{"peer_kind": …, "identity": …}` pair rather than a unique-id string, to whatever depth the configuration nests (AD043); ten mapping entries on the qualified path hit this (V30). Cardinality derived from scalar versus list; cardinality-many peer lists ordered canonically by peer identity; absent versus empty honored strictly. **An unresolvable or ambiguous peer fails the command**, naming owning kind, field, candidate kinds tried and unique-id — there is **no** `--continue-on-error` escape here: the option is declared on `sync` only (`infrahub_sync/cli.py:190`, V34) and derivation also runs under `diff`, where degrading to warn-and-drop would emit a silently incomplete plan (FR-030, AD047). The adapter's own load-path `continue_on_error` handling (`:491-493`) is a different path and is untouched. Only mapped source values enter the record — `settings` is never read into a payload — FR-002, FR-005, FR-018, FR-028.2, FR-030, AD043, AD046, AD047, AD050.
  **Done when**: T036 and T082 pass. (T072's canary scan is Phase G evidence for FR-018 and does not
  gate this task.)
- [ ] T030 [US1] Implement `tier_of(kind)` in `infrahub_sync/plan/derive.py` — the index of the tier set containing the kind (`Potenda.tiers`, V18); when tiers are absent because the configuration declares `order:`, the kind's index in `top_level` (PD-007). The field stays required and deterministic — FR-028.1, PD-007.
  **Done when**: T038 passes.
- [ ] T031 Record a per-side extraction flag `self._side_full_extract: dict[str, bool]` in `Potenda.load_one_side` (`infrahub_sync/potenda/__init__.py:189-200`), leaving the existing OR-accumulated `_did_full_extract` untouched so `persist_baseline_counts` (`:430`) is unchanged — FR-015 (V25).
  **Done when**: a test asserts the new dict answers per side while `_did_full_extract` keeps its
  current value on a mixed run, and `tests/cache/test_incremental_engine.py` still passes.
- [ ] T032 **[AD047 / AD049 / AD050]** [US4] Implement `derive_deletes(...)` in `infrahub_sync/plan/derive.py` — per kind, destination-store identities minus source-store identities via `adapter.get_all(kind)` and `get_identifiers()`; derived **only** when the destination side ran a full extract (T031's flag), otherwise none are derived and the manifest records `delete_operations_computed: false`; deletes carry **no** payload and never enter the diff the write path consumes, which is what makes FR-016 structural rather than configuration-dependent; a delete operation for which no destination identity can be formed **fails the command** naming the kind and the identity attribute, on the `diff` path as on `sync` (FR-030, AD047). **A delete's identity is canonicalised by the same recursive rule as any other operation's (AD049)** — a component whose `SchemaMappingField.reference` is set is recorded as a nested `{peer_kind, identity}` pair, recursively, never as the peer's unique-id string. Reuse T029's canonicaliser; the **only** difference is that the AD050 candidate probe runs against the **destination** adapter's store, not the source's, because a delete exists precisely when the object is at the destination and absent from the source, so its peers are destination-only by construction and the source store has nothing to resolve. Do **not** special-case deletes out of the rule: nine kinds on the qualified path carry a reference inside their identifiers (V30), so exempting deletes would leave the artifact with one place where a consumer must split a unique-id on `__`, and would derive a delete's `operation_id` from an identity no reviewer is ever shown. Zero-hit and multi-hit probes fail the command on the same terms as T029 — FR-002, FR-015, FR-016, FR-028.1, FR-030, AD049, AD050.
  **Done when**: T036, T037 and T084 pass.
- [ ] T033 **[AD044 / AD052]** Implement `warn_missing_convergence_key(...)` in `infrahub_sync/plan/derive.py` — **first, guard on the destination exposing a schema at all (AD052)**: read it as `getattr(destination, "schema", None)`, which is how `infrahub_sync/utils.py:260` already reaches for it, and when there is none **skip the whole warning and return** — that is never an error and never fails the plan run. This guard is load-bearing, not defensive habit: `self.schema` is defined on the Infrahub adapter only (`infrahub_sync/adapters/infrahub.py:345`, V38) while derivation now runs on the `diff` path for **every** destination with derivation failures fatal (AD047), so an unguarded attribute access would be a hard regression on the eight adapters that `diff` fine today. Then, for each destination kind with an operation, warn on the **log stream** in **either** of two conditions, naming the kind and what is missing. **(1)** `human_friendly_id` (V16) is absent, or the plan's identity does not supply every component path. **(2)** `uniqueness_constraints` (`.venv/…/infrahub_sdk/schema/main.py:274`, V32) declares no constraint covering the plan's identity attributes. Condition 2 is the brief's own stated condition and must be checked in its own right: AD017 had substituted condition 1 for it, but a kind with a complete HFID and no uniqueness constraint still duplicates silently. Both fields sit on the same cached object, so condition 2 costs one extra read and no extra fetch. Warning only in both cases, never a manifest field, so it stays outside the checksum and outside SC-006; the plan run still succeeds — FR-024, AD044, AD052.
  **Done when**: T039 and T085 pass.
  **Done when**: T039 passes all three of its cases.
- [ ] T034 [US1] Add `Potenda.write_plan_artifact(...)` in `infrahub_sync/potenda/__init__.py` composing T028–T033 plus `source_snapshot_records` (T007) and `default_config_version` (T008), and call it on the `diff` path and the serial `sync` path before any destination write; `plan.parquet` keeps being written unchanged everywhere it is written today (V23) and is never read by the new path — FR-001, FR-004, FR-019.
  **Done when**: T036 passes and a `diff` run over a fixture leaves `plan/manifest.json` and
  `plan/operations.jsonl` beside the existing `plan.parquet`.
- [ ] T035 **[AD039 / PD-009]** [US1] Restructure the tier branch of `Potenda.sync_in_tiers` (`infrahub_sync/potenda/__init__.py:480-499`) into two loops — compute and retain **every** tier's `Diff`, write the plan artifact, then execute the retained diffs tier by tier through the existing `sync(diff=...)` entry point (`:292-295`). Today diff and write are interleaved and the plan is written last (V24), so under the default `sync --parallel` (`infrahub_sync/cli.py:182-185`) no artifact can exist before the first write. **The `self.destination.top_level` narrowing belongs in the COMPUTE loop, around each `self.diff()` call, restored afterwards** — it governs **diff computation**, not execution: today it is assigned at `:483` immediately before `self.diff()` at `:484`, and `top_level` is read only by the comparison differ (`.venv/…/diffsync/helpers.py:79-88`) and never by the synchronizer, which walks the children of whatever `Diff` it is handed (V33). The execution loop replays the retained per-tier diffs with `top_level` restored to `saved_top`. Leaving the narrowing in the execution loop instead would compute every tier's diff against the whole destination — six identical full diffs rather than six disjoint per-tier ones — and the artifact would record each operation once per tier — FR-001, FR-015, AD039.
  **Done when**: T040 passes, including its per-tier **content** assertions, and
  `tests/test_potenda_parallel.py` plus `tests/cache/test_sync_cache_flow.py` still pass unchanged.
- [ ] T036 [P] **[AD042 / AD043]** [US1] Write `tests/test_potenda_plan_artifact.py` (derivation cases), following the fake-diff pattern at `tests/cache/test_plan_serialization.py:49-72` — destination identity and the **full** payload survive into the record (not just `get_attrs_diffs`); **every identity key of a create or update appears in `payload` or as a relationship-reference `field`, and never in neither** — assert this against a fake element whose `source_attrs` deliberately omits the identifiers, which is what the real comparison engine produces, so a regression to `source_attrs`-only fails here rather than at a live destination; relationship references carry peer kind and peer identity and cardinality-many lists are canonically ordered; **a peer identity component that is itself a reference is recorded as a nested `{peer_kind, identity}` pair and not as a unique-id string, at two levels of nesting**; a `delete` element present in the diff is **not** double-recorded alongside the derived delete; derived deletes appear with identifiers and no payload — FR-002, FR-015, FR-028, AD042, AD043.
- [ ] T037 [P] [US4] Write `tests/test_potenda_plan_artifact.py` (**SC-017**) — two plan runs against the same source and destination, one with a full destination extract and one incremental: the first records delete operations and `delete_operations_computed: true`, the second records none and `false`; assert the incremental run's plan does **not** drive its apply into `failed` through a phantom delete — FR-015, SC-017.
- [ ] T038 [P] [US1] Write `tests/test_potenda_plan_artifact.py` (tier assignment) — with computed tiers, the tier is the index of the containing tier set; with an explicit `order:` in the configuration (tiers `None`), the tier is the kind's index in `top_level`; the field is present on every operation in both cases — FR-028.1, PD-007.
- [ ] T039 [P] **[AD044]** Write `tests/test_potenda_plan_artifact.py` (**SC-014**) — **three** cases against fake schemas, one per FR-024 condition: (1) a kind that declares **no** `human_friendly_id`; (2) a kind whose plan identity misses an HFID component; (3) a kind with a **complete** `human_friendly_id` but **no `uniqueness_constraints` entry covering the plan's identity attributes**. Each emits a warning on the log stream naming the kind and what is missing, the warning is absent from the manifest, and the plan run still **succeeds**. Case 3 must fail if only the HFID condition is implemented — that is the whole point of adding it, since AD017 had substituted condition 1 for the brief's own condition. **A fourth case (AD052)**: a destination object exposing **no** `schema` attribute at all — assert the warning is skipped, that no `AttributeError` or any other error escapes, and that the plan run still succeeds; the case must fail if the schema read is unguarded — FR-024, SC-014, AD044, AD052.
- [ ] T040 [P] **[AD039]** [US1] Write `tests/test_potenda_plan_artifact.py` (tier ordering) — assert on recorded call order that in the tier branch the artifact write happens **before** the first `sync_from`, and that every tier's diff is computed before it. **Assert on per-tier diff *contents*, not only call order**: for each tier, the `Diff` retained by the compute loop contains exactly the kinds of that tier and no others, which is what proves the `top_level` narrowing was applied around the `diff()` call rather than moved to the execution loop (V33) — a call-order-only assertion passes just as happily against six identical full-destination diffs. Also assert the executed set and per-tier execution ordering are unchanged from the interleaved implementation — FR-001, AD039.
- [ ] T041 [P] **[Trap 1]** [US1] Write `tests/test_potenda_plan_artifact.py` (**SC-006**) — two consecutive plan runs over identical source and destination input **with the extraction mode pinned identically on both runs and both sides**; the test asserts the pinning held (both manifests carry the same `delete_operations_computed`) **before** comparing, then byte-compares `operations.jsonl` and compares `manifest.json` after removing `run_id` and `created_at` from both sides; include a negative control asserting two runs at **different** extraction modes are expected to differ, so the pinning is proven load-bearing rather than incidental — FR-005, FR-015, SC-006.
- [ ] T082 [P] **[AD046 / AD050]** [US5] Write `tests/test_potenda_plan_artifact.py` (duplicate schema-mapping entries) — derive a plan from a configuration in which **one destination kind is declared by two schema-mapping entries whose `reference` for the same identity field differs**, mirroring `DcimDevice` on the qualified path (`examples/netbox_to_infrahub/config.yml:212` → `LocationRack`, `:254` → `LocationSite`, V31). Assert that each operation's relationship reference carries the `peer_kind` of the store entry that **actually holds** that peer, so a rack-located device gets `LocationRack` and a site-located one gets `LocationSite`; assert that a derivation reading `peer_kind` from the mapping's `reference` produces at least one wrong kind and therefore fails this test. **Assert the AD050 probe's three arms explicitly**, because the rule is only safe if its failure arms exist: (a) with the peer present under exactly one candidate kind, that kind is recorded and the store was probed — assert the probe happened rather than inferring it from the answer, since a one-candidate set would produce the same answer by an unprobed mapping read; (b) with the peer present under **no** candidate kind, the command fails naming the owning kind, the field, the unique-id and both candidates tried, and **no** plan artifact is left behind; (c) with the peer present under **both** candidate kinds, the command fails the same way naming the match — assert it does **not** silently pick the first, the mapping-declared one, or any other. Add a **one-candidate** fixture whose sole candidate does not hold the peer and assert it fails too, which is what proves there is no single-candidate fallback. Also assert the two entries' operations are distinguishable and carry distinct identifiers — FR-002, FR-030, AD046, AD050.
- [ ] T083 [P] **[AD047]** Write `tests/test_potenda_plan_artifact.py` (derivation-failure cases) — on the **non-mutating `diff` path**, each of four derivation failures fails the command with a non-zero exit and an error naming the destination kind and the cause: an operation whose destination identity cannot be formed; a relationship peer absent from the loaded source store; a payload value outside the canonical-value table; and a duplicate operation identifier. Assert that **no** `--continue-on-error`-style tolerance exists on `diff` (the option is declared on `sync` only, `infrahub_sync/cli.py:190`, V34) and that none of the four degrades to a warning with a partial artifact written; assert no `plan/manifest.json` is left behind in any of the four. Assert the failures are equally hard on the `sync` path — FR-030, AD047.
- [ ] T084 [P] **[AD049 / AD050]** [US4] Write `tests/test_potenda_plan_artifact.py` (delete-identity canonicalisation) — derive deletes for a kind whose `identifiers` include a **reference** (mirroring `LocationRack.site` on the qualified path, V30), with the peer present in the **destination** store and absent from the source store, which is the only state a delete can be derived from. Assert the delete's `identity` records that component as a nested `{"peer_kind": …, "identity": …}` pair and **not** as the peer's DiffSync unique-id string; assert it nests recursively at two levels; assert its `operation_id` equals the identifier derived from that canonical identity, so the identity a reviewer reads is the one the identifier came from. Assert the AD050 probe ran against the **destination** store — a probe wired to the source store finds nothing there, so this test must fail rather than the derivation silently emitting a unique-id string. Assert the zero-hit and multi-hit probe arms fail the command destination-side exactly as T082 asserts them source-side. Assert the delete still carries **no** payload and no `relationships`: the recursive rule reaches its identity only — FR-002, FR-015, FR-028.1, FR-030, AD049, AD050.
- [ ] T085 [P] **[AD052]** Write `tests/test_potenda_plan_artifact.py` (non-Infrahub destination regression) — run the **non-mutating `diff` path** end to end against a destination adapter that exposes **no** `schema` attribute, which is every adapter in the repository except Infrahub (V38). Assert the command exits zero, the plan artifact is written, and no `AttributeError` or other error escapes from `warn_missing_convergence_key`; assert the convergence-key warning is simply absent rather than emitted empty. This is a **regression** assertion, not a feature one: derivation is newly wired onto `diff` for every destination and AD047 makes its failures fatal, so an unguarded schema read here would break `diff` on the eight adapters that work today. The test must fail if the guard is removed — FR-024, FR-030, AD047, AD052.

**Checkpoint D**: plan runs now write the artifact and record deletes. **This checkpoint is not fully
green, by design**: existing suites and fixtures made stale by the delete change and by the artifact
write are swept in Phase G (T067), so until then the boundary is green only for the suites listed in
T035's Done-when. S, A, B and C end fully green; E, F and G each end green. plan.md's
[Implementation phases](./plan.md#implementation-phases) states the same thing in the same terms.

---

## Phase E: Destination write surface and apply-time peer resolution

**Delivers**: FR-013, FR-014, the apply side of FR-012, FR-017, FR-020, FR-023, FR-025.

- [ ] T042 **[AD038 groundwork]** [US5] Extract the existing cardinality-many replace-set from the **module-level** `update_node` function (`infrahub_sync/adapters/infrahub.py:97`, replace-set body `:149-175`) — it is **not** a method on `InfrahubAdapter`, whose class body only begins at `:277` (V35) into `_replace_relationship_set(node, rel_name, peer_ids)` — `compare_lists(existing_peer_ids, new_peer_ids)` then remove `existing_only` and add `new_only`; the extraction is behavior-preserving for the existing caller — FR-013, AD038 (V12).
  **Done when**: `tests/adapters/` passes unchanged and `update_node` calls the helper.
- [ ] T043 [US5] Implement `PeerResolver` in `infrahub_sync/adapters/infrahub.py` — a memo keyed on `(kind, canonical_identity(identity))`, populated from each completed create/update so an operation's own result resolves later operations referring to it; lifetime is one apply and it is discarded with it; it **never** reads `client.store` or the DiffSync store; failed lookups and failed writes are **never** memoized — FR-014.
  **Done when**: T052 passes.
- [ ] T044 **[AD043 / AD048]** [US5] Implement `PeerResolver`'s destination query (PD-004) — build filter kwargs from the destination kind's `human_friendly_id` component paths (V16, cached at `infrahub_sync/adapters/infrahub.py:345`): an `<attr>__value` path takes its value from the peer identity's scalar under `<attr>`; a `<rel>__<attr>__value` path takes its value from the **nested `{peer_kind, identity}` pair** the peer identity records under `<rel>` (AD043), read at `identity[<attr>]`, recursing for deeper nesting. A **schema path** is split, a **data value** never is — and AD043's nested shape is what makes the nested arm constructible at all: on a memo miss the resolver holds nothing but the peer's identity mapping, so a raw unique-id string there would force exactly the `__` split the brief names as the v1 flaw. `client.filters(kind=..., **kwargs)` result count drives three arms — 1 → the node id and memoize; 0 → `PeerNotFoundError` naming peer kind, peer identity and the referring operation identifier; >1 → `PeerAmbiguousError` naming peer kind, peer identity and the match count. Neither is ever a silent skip. **Scope (AD048)**: both refusals live in this new resolver only. Do **not** change the live `sync` write path's warn-and-continue (`infrahub_sync/adapters/infrahub.py:141-143`, `:212-214`, `:229-231`, V14) or the SDK's bare `IndexError` (V17) — that is existing behavior on an existing path this brief does not authorize touching. Document the `<rel>__ids` fallback for a kind whose HFID does not cover its plan identity — FR-014, SC-016, AD043, AD048.
  **Done when**: T053 passes, including its assertion that the live-path behavior is unchanged; the
  nested filter spelling itself is asserted by T079/T080.
- [ ] T045 **[AD042]** [US1] Implement `InfrahubAdapter.apply_planned_operation(*, operation, peers) -> str` in `infrahub_sync/adapters/infrahub.py` — `delete` raises `UnsupportedPlannedOperationError` naming the operation identifier, action and kind and never touches the destination; `create` and `update` both build `data` from the payload — which carries the identity components (AD042) — plus resolved peer ids, run it through `client.schema.generate_payload_create(...)` for source/owner/protection parity (`:608-610`), then `client.create(...)` + `save(allow_upsert=True)` (V10); neither routes through `InfrahubModel.update`, whose `local_id` keying needs the destination load FR-012 forbids (V11); the payload is authoritative for the mapped fields it carries and touches no unmapped destination field. **Between building `data` and issuing the create, assert that every component path of the destination kind's `human_friendly_id` is *accounted for***; an unaccounted-for component raises, naming the kind and the missing component, and **no unkeyed write is ever issued**. That assertion is what makes an AD042-class regression fail loudly instead of duplicating silently: the SDK keys the upsert on `data["id"]` if set, else `data["hfid"]`, and `get_human_friendly_id()` returns `None` when any component resolves to `None` (`.venv/…/infrahub_sdk/node/node.py:295-298`, `:135-139`, V15). **"Accounted for" is per component shape (AD051)** — "resolves against `data`" is not implementable, because by this point step 3 has replaced each relationship value with a resolved node-id **string** and no attribute can be read out of a node id. For a **direct** component (`<attr>` or `<attr>__value`): the key is present in `data` and its value is not `None`. For a **relationship-crossing** component (`<rel>__<attr>__value`): the `<rel>` key is present in `data` and not `None`, **and** the operation's nested `{peer_kind, identity}` for `<rel>` (AD043) supplies `<attr>` in its `identity`; deeper nesting recurses through the nested `identity` by the same rule. Both arms still fail for every case the assertion exists to catch. Do **not** attempt to make the SDK read the attribute back out: for a relationship-crossing HFID component the SDK resolves the peer through its client store and returns `None` on a miss, a bare-id relationship value renders as `{"id": …}` with no `__typename` so that store read is never even attempted, and one `None` component nulls the whole HFID (V39) — that dependency is a recorded risk in plan.md with this assertion as its detector, not something this task solves — FR-013, FR-016, FR-028.4, AD042, AD051.
  **Done when**: T050, T054 and T081 pass.
- [ ] T046 **[AD038]** [US5] After the upsert in `apply_planned_operation`, reconcile every cardinality-many relationship the operation carries as an explicit **replace-set** against the saved node via `_replace_relationship_set` — enforced, not assumed of the upsert, because whether the upsert mutation replaces or merges a relationship list cannot be verified offline (AD007); `peers: []` under `cardinality: "many"` means empty the set and the replace-set acts on it — FR-013, FR-028.2, AD038.
  **Done when**: T051 passes.
- [ ] T047 [US1] Replace the body of `Potenda.apply_plan` in `infrahub_sync/potenda/__init__.py` — load the new artifact, run the five verification checks plus the write-surface check **before any write**, execute operations in stored order with no re-sorting and no recomputation, record applied identifiers as an **ordered** sequence on the run result (FR-020, whose final element is FR-025's last-applied pointer), collect `delete` operations as unsupported rather than stopping at the first, and fail the run naming each unsupported operation's identifier and action while every non-delete still applies; a destination rejection or transport failure stops at that operation, keeps what was written, and fails naming the operation identifier and the underlying error (AD027); an empty plan is a successful no-op but verification still runs first — FR-012, FR-017, FR-020, FR-022, FR-023, FR-025.
  **Done when**: T054 and T055 pass.
- [ ] T048 **[AD040 / PD-010]** Remove the v1 `apply_cached_row` dispatch and its `hasattr` guard from `infrahub_sync/potenda/__init__.py:341-370`, and update the stale reference at `tasks/bench.py:413` — the guard's *shape* is preserved for the new surface (a `NotImplementedError` naming the adapter class and directing the operator to `sync`) so FR-023 keeps the behavior the engine already has. Leaving the dispatch wired would be exactly the second apply path FR-019 forbids; it has zero adapter implementations (V3) — FR-019, FR-023, AD040.
  **Done when**: T066 has landed, `grep -rn "apply_cached_row" --include='*.py' .` returns no hit
  anywhere in the tree, and `uv run pytest -q` passes.
- [ ] T066 **[AD040 fallout]** Rewrite `tests/cache/test_apply_plan.py` against the new artifact and the new surface — the `MagicMock` asserting the `apply_cached_row` dispatch shape (`:43-44`) is gone with the dispatch; the rewritten file asserts the new artifact-driven apply. **This task belongs in Phase E, immediately after T048, and must land in the same change as it.** It cannot be deferred: T048's done-condition is a passing suite, and this file asserts the behavior T048 removes, so any gap between them leaves that condition unsatisfiable — FR-019, AD040.
  **Done when**: the file passes and references no removed symbol.
- [ ] T049 [US2] Wire the configuration-version comparison value into the apply path — recomputed by the default rule (T008) on the CLI path, or taken verbatim when an in-process caller supplies one (AD013); the value is compared for equality and **never** parsed — FR-011.
  **Done when**: T057 passes.
- [ ] T050 [P] [US1] Write `tests/adapters/test_infrahub_planned_write.py` (payload cases) against a mocked `InfrahubClientSync` — `data` is built from the payload plus resolved peer ids; `generate_payload_create` is invoked with the source/owner/protection parity arguments; `client.create` + `save(allow_upsert=True)` is the write; `InfrahubModel.update` is never called; no unmapped destination field appears in the payload — FR-013, FR-028.4.
- [ ] T051 [P] **[AD038]** [US5] Write `tests/adapters/test_infrahub_planned_write.py` (replace-set cases) — after the upsert, existing-only peers are removed and new-only peers added; an empty `peers` list under `cardinality: "many"` empties the set; when the upsert already replaced, the reconciliation is a no-op with empty `existing_only`/`new_only` — FR-013, FR-028.2, AD038.
- [ ] T052 [P] [US5] Write `tests/adapters/test_infrahub_planned_write.py` (memo cases) — a completed operation's result resolves a later operation referring to it with **no** destination query; a failed lookup is not cached and the next reference re-attempts; a failed write is not cached; the resolver never reads `client.store` (asserted by making the store attribute raise) — FR-014.
- [ ] T053 [P] **[AD048]** [US5] Write `tests/adapters/test_infrahub_planned_write.py` (**SC-016 local half**) — a zero-match peer refuses the operation and fails the run with a message naming the peer kind, the peer identity **and** the referring operation identifier; a multi-match refuses with a message naming the peer kind, the peer identity **and** the match count; neither is silently skipped and neither operation is dispatched. **Plus the scope assertion (AD048)**: a zero-match on the **live `sync` write path** still warns and continues exactly as today (`infrahub_sync/adapters/infrahub.py:141-143`, `:212-214`, `:229-231`), so the refusal has not leaked out of the new apply-path resolver into behavior this brief does not authorize changing — FR-014, SC-016, AD048.
- [ ] T054 [P] [US4] Write `tests/adapters/test_infrahub_planned_write.py` (**SC-007 local half**) — a plan fixture containing at least one delete: every non-delete operation is applied against a recording fake destination, the delete is never dispatched, the run ends `failed`, and the message names the unsupported operation's identifier and action — FR-016, FR-017, SC-007.
- [ ] T055 [P] [US1] Write `tests/adapters/test_infrahub_planned_write.py` (apply-loop cases) — an adapter without `apply_planned_operation` fails **before any write** with an error naming the adapter class (FR-023); the applied-identifier record is an **ordered** sequence and FR-025's last-applied pointer is its final element; a rejection mid-plan stops there, keeps prior writes, and names the failing operation identifier (AD027); an empty plan applies as a successful no-op after verification has run (FR-022, AD033); stored order is executed exactly — FR-012, FR-020, FR-022, FR-023, FR-025.
- [ ] T056 [P] [US1] Write the **SC-005** evidence test spanning `tests/plan/test_review.py` and `tests/adapters/test_infrahub_planned_write.py` — capture the operation-identifier set from per-object review output of a stored plan, apply the same plan against a recording fake destination, and assert the review-side set equals the FR-020 record on the run result, per operation and in the same order. **The fixture plan MUST contain no delete operation**, and the test asserts that precondition before comparing: a delete is reviewed but never applied (FR-016, FR-017), so its identifier appears on the review side and not in the FR-020 record, and an order-sensitive equality over a delete-bearing plan fails for a reason that has nothing to do with SC-005. T054 is where a delete-bearing plan is exercised — FR-003, FR-020, SC-005.
- [ ] T081 [P] **[AD045a]** [US1] Write `tests/plan/test_apply_conformance.py` — a local mutation-payload conformance harness against a mocked `InfrahubClientSync`, asserting the **payloads the apply hands to the SDK** rather than any destination state, so the class of defect AD042 was is caught offline instead of only by the deferred live criteria. Three assertions, over a plan covering create, update and a relationship-bearing kind from a fixture mirroring the qualified configuration: **(1)** for every `client.create` call, every component path of the destination kind's `human_friendly_id` is *accounted for* in the `data` argument by the AD051 per-component rule — a direct component present and non-null in `data`, a relationship-crossing component requiring `<rel>` present and non-null in `data` **and** the operation's nested `{peer_kind, identity}` supplying `<attr>` — so no create is ever issued whose data cannot form the convergence key; the fixture must include at least one kind whose HFID crosses a relationship, so the second arm is exercised and not merely declared; **(2)** for every cardinality-many relationship an operation carries, the replace-set reconciliation is issued against the saved node after the upsert (AD038), including for `peers: []`; **(3)** applying the same operation twice produces exactly **one** `client.create` invocation with `allow_upsert=True` on save and **no second create** — convergence measured at the mutation, not at the destination. Each assertion must fail if the corresponding behavior is removed; the harness is not a substitute for SC-002, SC-003 or SC-008, which stay deferred, and it does not close the recorded risk that the SDK cannot form a nested `hfid` client-side from a resolved id (V39, AD051) — FR-013, FR-028.4, AD038, AD042, AD045, AD051.
  **Done when**: all three assertions pass, and each is demonstrated to fail when its behavior is
  reverted (a payload built from `source_attrs` alone must fail assertion 1).
- [ ] T057 [P] [US2] Write the **SC-013** apply-side test in `tests/plan/test_config_version.py` — a deliberately opaque printable-ASCII value supplied verbatim by an in-process caller round-trips through the manifest write and is compared verbatim at apply without being parsed; combined with SC-004's configuration-version mismatch case (T025), the criterion is complete — FR-011, SC-013.

**Checkpoint E**: a stored plan can be applied end to end in-process; the CLI still has its old
surface. **This checkpoint is green**: T066 landed with T048, so no test asserts the removed dispatch.

---

## Phase F: CLI review mode and apply rewiring

**Delivers**: FR-008, FR-009's run-state obligations, SC-009's CLI cases, SC-012.

- [ ] T058 [US1] Add `--from-plan`, `--detail` and `--kind` to the existing `diff` command in `infrahub_sync/cli.py` — in `--from-plan` mode the command branches **above** `pipeline_lock` (`:129`, V20) and **above** `get_potenda_from_instance` (V21), so no lock is taken, no adapter is constructed, nothing is extracted and no run directory is created or modified; it resolves the run as `cache_root_for(name)/<run_id>` (V27), calls `read_saved_plan`, and renders through `typer.echo` (AD032) as a **thin renderer** that re-implements no reading, filtering or summarizing; the live path's `--run-id` meaning and logger output channel are untouched (AD023). No `add_typer` is added — FR-006, FR-008, FR-029, SC-012.
  **Done when**: T061, T063 and T064 pass.
- [ ] T059 [US2] Rewire the existing `apply` command in `infrahub_sync/cli.py` — refuse **before constructing anything** when the named run does not exist or holds no plan artifact, naming the run identifier and the expected artifact path and creating no run directory (AD026); run the five checks plus the write-surface check before any destination write; on refusal record `failed` with an **empty** applied-operation set rather than an absent field; a run already at `applied` may be applied again and verification still runs (AD033) — FR-009, FR-020, FR-023.
  **Done when**: T065 passes.
- [ ] T060 **[Run-state correction]** [US2] Fix the pre-existing schema-subhash refusal path in `infrahub_sync/cli.py:336-340` to record `failed` — today it writes `run.json` with `status: running` at `:322-323` and then aborts through `print_error_and_abort` (`:72-74`), permanently leaving `running` on disk (V22). No new state is introduced; the existing vocabulary is reused (AD010) — FR-009.
  **Done when**: T065's schema-subhash case reads `failed` from `run.json` after the abort, and
  `tests/cache/test_schema_subhash_persist.py` still passes.
- [ ] T061 [P] [US1] Write `tests/test_cli_plan_review.py` (**SC-009 CLI half**) — `CliRunner` cases for the summary and for per-object detail with and without `--kind`, both against a stored artifact written by an earlier, exited process, with source and destination unreachable; the summary presents a count per action and a count per kind; the detail presents one record per operation carrying at least identifier, action, kind and identity — FR-006, FR-008, SC-009.
- [ ] T062 [P] [US1] Write `tests/test_cli_plan_review.py` (error paths) — `--from-plan` with no `--run-id` errors naming the required option; an unknown run identifier errors naming the identifier and the expected artifact path and is **never** presented as a zero-operation plan; a run with no `plan/` errors with the re-plan message; a torn artifact names which part is torn; an unrecognized `format_version` errors with the version-found/versions-supported message; an unreadable path names the path; `--kind` matching nothing errors naming the kind. Each exits non-zero and creates no run directory — FR-006, FR-008, FR-010, FR-019, FR-027.
- [ ] T063 [P] [US1] Write `tests/test_cli_plan_review.py` (isolation) — the review path constructs **no** adapter (the adapter import is patched to raise if called), creates **no** directory under the cache root, writes no `run.json`, and is not blocked by a held pipeline lock (assert it returns well inside the 60-second lock timeout while the lock is held) — FR-008, AD021, AD031.
- [ ] T064 [P] **[SC-012]** Write `tests/test_cli_plan_review.py` (command-set assertion) — capture `--help` after the change and compare as text against the T002 baseline, asserting the command list is unchanged at exactly five commands with no group added; assert no `add_typer` call exists in `infrahub_sync/cli.py`; assert the three new flags appear only under `diff --help` — FR-008, SC-012.
- [ ] T065 [P] [US2] Write `tests/test_cli_plan_review.py` (apply refusal cases) — this task carries the **zero-destination-writes and `failed`-run-state halves of SC-004, SC-011, SC-015 and SC-018**, which T024 and T025 cannot: those are Phase C unit tests and no apply exists in that phase. Against a write-recording fake destination on the CLI apply path, cover **all six** of SC-004's enumerated refusal cases individually — checksum mismatch, configuration-version mismatch, snapshot-binding mismatch, absent operations, truncated snapshot, absent snapshot — rather than one representative per family; plus the v1 plan (SC-011), the run-identifier mismatch (SC-015) and the unrecognized `format_version` (SC-018). Each of the nine asserts refusal, the named failed check, **zero destination writes** observed as an unchanged recorded-write count, and `failed` with an **empty** applied-operation set read back from `run.json`. Also: a missing artifact refuses without creating a run directory; and the schema-subhash abort of T060 records `failed` instead of leaving `running` — FR-009, FR-020, SC-004, SC-011, SC-015, SC-018.

**Checkpoint F**: full local behavior is in place; the review-then-apply walkthrough in
[quickstart.md](./quickstart.md) runs against a fixture.

---

## Phase G: Documentation, fixtures, and the changed-content fallout

**Delivers**: FR-015's "test fixtures and documentation affected by the change in plan content MUST
be updated in the same change", FR-008's documentation obligation, SC-010's canary scan, and the
project gate.

- [ ] T067 Sweep every test fixture and expectation made stale by delete recording and by the artifact write, and update them in this same change — start from `tests/cache/test_plan_serialization.py`, `tests/cache/test_sync_cache_flow.py`, `tests/test_potenda_parallel.py`, `tests/test_potenda_tiers.py` and `tests/cache/test_guardrails.py`, then run the whole suite and fix each remaining stale expectation — FR-015 (the brief mandates this in the same change).
  **Done when**: `uv run pytest -q` passes with no skips added, and the task records the list of
  files changed and why each was stale.
- [ ] T068 [P] Update `docs/docs/reference/cache-layout.mdx` — the `plan/` directory, both files, the manifest field set, the write order and commit point, and the note that `plan.parquet` is retained and is no longer read by `apply` — FR-008, FR-019, AD036.
- [ ] T069 [P] Update `docs/docs/running-a-sync.mdx` — the review-then-apply workflow, the three new `diff` flags, applying by run ID, and the refusal behaviors an operator will meet — FR-008.
- [ ] T070 Regenerate `docs/docs/reference/cli.mdx` with `uv run invoke docs.generate` so the new `diff` flags appear — FR-008.
  **Done when**: the regenerated file lists `--from-plan`, `--detail` and `--kind` under `diff` and
  the diff is confined to that command.
- [ ] T071 [P] Sweep the docs for statements made stale by delete recording and correct them — grep `docs/docs/` for delete-related claims (at minimum `creating-a-sync-project.mdx`, `reference/schema-mapping.mdx`, `reference/incremental-extraction.mdx`, `reference/cache-layout.mdx`) and state the new behavior: plans record deletes, the apply never executes one, and deletes are not computed when the destination side was loaded incrementally — FR-015, FR-016.
  **Done when**: no docs page still asserts that plans omit deletes, and the task records each page
  changed.
- [ ] T072 Write the **SC-010** canary test — inject a canary credential into the configuration's `settings`, run a plan, and scan the artifact files directly, the captured CLI stdout of both review depths, and the in-process reader's returned value **as data**; the canary appears in none of them — FR-018, SC-010.
  **Done when**: the test fails if the canary is planted into a payload, proving the scan has teeth.
- [ ] T073 Run the full project gate and the CLI sanity commands from `AGENTS.md` — `uv run invoke format`, `uv run invoke lint`, `uv run ty check .`, `uv run pytest -q`, `uv run rumdl check .`, then `uv run infrahub-sync --help`, `uv run infrahub-sync list --directory examples/`, `uv run infrahub-sync generate --name from-netbox --directory examples/`.
  **Done when**: every command exits 0, `ty` reports no errors and no `[[tool.ty.overrides]]` block
  was added to `pyproject.toml`.

**Checkpoint G**: the local track of [quickstart.md](./quickstart.md) passes end to end.

---

## Phase H: Live-destination evidence (`integration` marker)

**Purpose**: the six criteria and half-criteria that need a running Infrahub. These tests are
authored alongside Phases E and F but are grouped here because they are skipped by default and never
gate a phase boundary. Per AD007 no live Infrahub is reachable in the development environment, so
they are expected to run in CI or on a maintainer's machine.

**This phase is authored, not satisfied (AD045b).** Completing every task below leaves DBA-001,
DBA-002, DBA-003 and DBA-008, and the live halves of DBA-007 and SC-016, **without passing evidence**,
and the brief's completion condition unmet at merge. T081's offline conformance harness and T045's
HFID-component assertion narrow the exposure; neither closes it. Record the deferral in the merge
notes rather than marking these criteria covered.

- [ ] T074 **[Trap 2]** Create `tests/integration/test_saved_plan_apply_integration.py` with the shared fixture — `@pytest.mark.integration` on every test and the env-var skip pattern of `tests/integration/test_infrahub_node_to_diffsync_integration.py`; the fixture seeds the qualified path (`examples/netbox_to_infrahub/config.yml`, NetBox → Infrahub) and **asserts as a fixture precondition** that every destination kind under test declares a `human_friendly_id` in the destination schema **and** that the plan's identity supplies every one of its component paths. A fixture failing that precondition raises a fixture error naming the kind and the missing component, never a test failure — without it the upsert is unkeyed and produces duplicates that read as a product bug (FR-024, AD017, V15).
  **Done when**: running with `-m integration` and no `INFRAHUB_ADDRESS` skips cleanly, and the
  precondition assertion fails loudly when pointed at a kind with no HFID.
- [ ] T075 [P] [US1] **SC-001** in `tests/integration/test_saved_plan_apply_integration.py` — patch `Adapter.diff_from` and `Adapter.sync_from` to fail if called, apply a stored plan against live Infrahub, assert the apply completed and neither was invoked, and assert no source or destination extraction ran — FR-012, SC-001. **Marker**: `integration`.
- [ ] T076 [P] [US3] **SC-002** — apply once, record per-kind object counts and HFID identities, apply the identical plan again, and compare: the same object, the same identity, no duplicate, for every kind the plan carries an operation for — FR-013, SC-002. **Marker**: `integration`.
- [ ] T077 [P] [US3] **SC-003** — a per-class conformance matrix over create, update and the relationship-bearing class, across apply-once, apply-twice, a crash injected **after** the destination write commits and before the loop advances, and one injected **before** the write is issued (both injected by raising inside the apply loop at those two points); every class ends at clean-single-run counts, with the relationship class measured by SC-008's peer-set comparison rather than object counts. Delete is excluded, because applying deletes is out of scope — FR-013, SC-003. **Marker**: `integration`.
- [ ] T078 [P] [US4] **SC-007 live half** — apply a plan containing a delete against live Infrahub; record destination object counts before and after scoped to the kinds in the plan, assert the object named by each delete operation is still present, and assert the run state is `failed` with a message naming the unsupported operation — FR-016, FR-017, SC-007. **Marker**: `integration`.
- [ ] T079 [P] **[Trap 3 / AD043]** [US5] **SC-008** — apply a relationship-bearing kind from the qualified configuration with no comparison store loaded; read the destination peer sets back and compare against the plan's reference list as an **unordered set of (peer kind, peer identity) pairs**, for each relationship the schema mapping declares for the kind under test; evidence the no-store precondition as SC-001 does. **The fixture MUST include at least one referenced peer that pre-exists at the destination and is absent from the plan**, and the test MUST assert the destination-query path was taken for it (for example by counting `client.filters` calls, or by seeding a peer no operation in the plan creates). Without that, dependency-tier ordering fills the resolver's memo from the plan's own creates and the destination-query path never runs — so the test would pass while apply-time peer resolution, the requirement it exists to measure, is broken. That pre-existing peer must be one whose own identity contains a reference, so the nested `{peer_kind, identity}` resolution of AD043 is exercised too. This is also what asserts PD-004's nested `<rel>__<attr>__value` filter spelling, which cannot be verified offline — FR-013, FR-014, SC-008, AD043. **Marker**: `integration`.
- [ ] T080 [P] [US5] **SC-016 live half** — seed a genuinely ambiguous peer in the destination and assert the multi-match refusal names the peer kind, the peer identity and the **real** match count, and that the operation is refused rather than skipped — FR-014, SC-016. **Marker**: `integration`.

**Checkpoint H**: the live track of [quickstart.md](./quickstart.md) passes where an Infrahub is
reachable.

---

## Functional-requirement coverage

Every FR-001…FR-030 has at least one owning task. No FR is unhomed.

| FR | Owning tasks |
|---|---|
| FR-001 | T034, T035, T040 |
| FR-002 | T009, T028, T029, T036, T082, T084 |
| FR-003 | T005, T011, T056 |
| FR-004 | T006, T007, T016, T012, T013 |
| FR-005 | T004, T010, T016, T018 |
| FR-006 | T022, T026, T061, T062 |
| FR-007 | T020, T022, T027 |
| FR-008 | T058, T061, T062, T063, T068, T069, T070 |
| FR-009 | T021, T025, T059, T060, T065 |
| FR-010 | T016, T020, T021, T024, T025 |
| FR-011 | T008, T014, T049, T057 |
| FR-012 | T047, T055, T075 |
| FR-013 | T045, T046, T050, T051, T076, T081 |
| FR-014 | T043, T044, T052, T053, T079, T080 |
| FR-015 | T031, T032, T037, T041, T067, T071, T084 |
| FR-016 | T032, T045, T054, T078 |
| FR-017 | T047, T054, T078 |
| FR-018 | T029, T072 |
| FR-019 | T016, T020, T024, T048, T066 |
| FR-020 | T047, T055, T056, T059 |
| FR-021 | T016, T017 |
| FR-022 | T016, T017, T026, T055 |
| FR-023 | T021, T047, T048, T055 |
| FR-024 | T033, T039, T074, T085 |
| FR-025 | T047, T055 |
| FR-026 | T015, T019 |
| FR-027 | T009, T012, T016, T020, T024 |
| FR-028 | T009, T015, T028, T029, T030, T032, T036, T050, T081 |
| FR-029 | T022, T023, T027, T058 |
| FR-030 | T029, T032, T082, T083, T084, T085 |

## Success-criteria evidence

Each criterion names the task that produces its evidence and whether it runs locally or behind the
`integration` marker.

| SC | Evidence task | Track |
|---|---|---|
| SC-001 | T075 | **integration — deferred, not produced** |
| SC-002 | T076; partially anticipated offline by T081 assertion 3 | **integration — deferred, not produced** |
| SC-003 | T077; partially anticipated offline by T081 | **integration — deferred, not produced** |
| SC-004 | T025 (verifier half, six cases), T065 (all six on the CLI apply path: zero writes + `failed`) | local |
| SC-005 | T056 (delete-free fixture) | local |
| SC-006 | T041 (extraction mode pinned, trap 1); T018 supports at writer level | local |
| SC-007 | T054 (local half), T078 (live half) | local + **integration (live half deferred)** |
| SC-008 | T079 (trap 3: a pre-existing peer absent from the plan) | **integration — deferred, not produced** |
| SC-009 | T027 (in-process), T061 (CLI) | local |
| SC-010 | T072 | local |
| SC-011 | T024 (reader half: message), T065 (zero writes + `failed`) | local |
| SC-012 | T002 (baseline), T064 (comparison) | local |
| SC-013 | T014 (plan side), T057 (apply side) | local |
| SC-014 | T039 (four cases: the two HFID cases, the uniqueness-constraint case, and the no-schema-exposed skip); T085 for the `diff` regression it protects | local |
| SC-015 | T025 (verifier half), T065 (zero writes + `failed`) | local |
| SC-016 | T053 (local half, plus the AD048 scope assertion), T080 (live half) | local + **integration (live half deferred)** |
| SC-017 | T037 | local |
| SC-018 | T024 (reader half: message), T065 (zero writes + `failed`) | local |
| *(no SC)* | T081 — offline mutation-payload conformance (AD045a); T082 — duplicate-mapping-entry derivation and the AD050 probe arms (AD046, AD050); T083 — derivation-failure behavior on `diff` (AD047, FR-030); T084 — delete-identity canonicalisation against the destination store (AD049, AD050); T085 — `diff` regression on a destination exposing no schema (AD052) | local |

## User-story coverage

| Story | Priority | Tasks that deliver it | Independent test |
|---|---|---|---|
| US1 — Review a saved plan, then apply it by run ID | P1 | T028, T030, T034, T035, T036, T038, T040, T041, T045, T047, T050, T055, T056, T058, T061, T062, T063, T075, T081 | Produce a plan, review its summary and one kind's detail from the stored artifact with both systems unreachable, then apply by run ID and compare the applied identifiers with the reviewed ones (T056, T061) |
| US2 — Refuse a plan that is no longer safe | P2 | T049, T057, T059, T060, T065 | Corrupt each of the five bindings in turn and assert refusal before any write with `failed` recorded (T025 for the verifier's verdict, T065 for the zero writes and the run state) |
| US3 — Re-apply without duplicating | P3 | T076, T077, T081 | Apply the same stored plan twice against a live destination and compare per-kind counts and identities (T076 — **deferred**); offline, assert a repeated operation issues no second create (T081) |
| US4 — A recorded delete is never silently skipped | P4 | T032, T037, T054, T078 | Apply a plan containing a delete: non-deletes land, the delete target survives, the run ends `failed` naming it (T054, T078) |
| US5 — Relationship operations apply without a comparison store | P5 | T029, T042, T043, T044, T046, T051, T052, T053, T079, T080, T082 | Apply a relationship-bearing kind with no store loaded and compare the destination peer sets against the plan's references, with at least one peer pre-existing and absent from the plan (T079) |

## Dependencies and execution order

### Phase dependencies

```text
S ──▶ A ──▶ B ──▶ C ──▶ D ──▶ E ──▶ F ──▶ G
                              └──────────▶ H (authored with E/F, gated by nothing)
```

- **S** blocks everything. T002 in particular must run **before** any Phase F task, because SC-012's
  evidence is a before/after comparison and the "before" cannot be recovered afterwards.
- **A** blocks B, C, D and E — every later module imports the canonical encoding, the identity
  derivation, the checksum helpers and the record types.
- **B** blocks D (the derivation writes through the writer) and C's torn/checksum tests, which need
  real artifacts to corrupt.
- **C** blocks E (the apply loop verifies through `verify_plan`) and F (the CLI renders through
  `read_saved_plan`).
- **D** blocks E in practice, because the apply-path tests consume artifacts the derivation produces.
- **E** blocks F's apply rewiring (T059, T065) but not F's review tasks.
- **G** depends on D, E and F — the fallout it sweeps is created by those phases. It no longer carries
  T066: that rewrite moved into E, beside the removal that causes it.
- **H** depends on E and F for the code under test; it never gates a boundary because the marker is
  skipped by default, and per AD045b it produces no evidence in this environment.
- **No Phase-D task depends on a Phase-G task.** T029 previously named T072's canary scan in its
  Done-when, which is a backwards dependency across four phases; T072 remains FR-018's evidence in G,
  but T029 completes on T036 and T082.

### Green-tree boundaries

S, A, B and C are pure additions: the tree is green at each of their checkpoints with no existing
behavior changed. **D does not end green** — it is the first phase that changes what a plan run
writes, and the fixtures it makes stale are swept in G (T067), so at Checkpoint D the tree is green
only for the suites named in T035's Done-when. **E ends green**: T066, the one test the T048 dispatch
removal invalidates, is rewritten inside E immediately after T048 rather than deferred to G, so T048's
"`uv run pytest -q` passes" done-condition is satisfiable at the point it is claimed. F and G each end
green. This is the same statement plan.md's
[Implementation phases](./plan.md#implementation-phases) makes; the two documents must not diverge on
it.

### Parallel opportunities

- Phase A: T003 and T004 in parallel; then T005–T009 sequentially by import order; then T010–T015
  all in parallel.
- Phase B: T017, T018, T019 in parallel after T016.
- Phase C: T020, T021, T022 sequentially (T021 and T022 both consume the reader); T024–T027 in
  parallel.
- Phase D: T028–T033 touch one module and are sequential; T036–T041 and T082–T085 in parallel after
  T034 and T035.
- Phase E: T042 first (it is a behavior-preserving extraction), then T043–T048 sequentially by file;
  **T066 immediately after T048, in the same change**; then T049; T050–T057 and T081 in parallel.
- Phase F: T058, T059, T060 sequentially in `cli.py`; T061–T065 in parallel.
- Phase G: T068, T069, T071 in parallel; T070 after T058; T073 last.
- Phase H: T075–T080 in parallel after T074.

## Implementation strategy

1. **S + A + B + C** — the artifact exists, can be written, read, verified and reviewed, and nothing
   in the product has changed. Reviewable as one pure-addition increment.
2. **D** — plan runs start writing the artifact and recording deletes. This is the first
   user-visible change and the one the brief sanctions explicitly.
3. **E** — a stored plan becomes applicable in-process. US1, US3, US4 and US5 all become
   demonstrable here, locally against fakes. The v1 dispatch goes and its test double is rewritten in
   the same step, so the phase ends green.
4. **F** — the operator-facing surface: review from the stored artifact and apply by run ID.
5. **G** — the fallout the earlier phases created, and the project gate.
6. **H** — live evidence, run wherever an Infrahub is reachable. Authored here, **not produced here**
   (AD045b): six brief criteria stay without passing evidence at merge and the brief's completion
   condition is unmet. Say so in the merge notes.

The narrowest useful increment is **S + A + B + C + D + E + F**, which makes the headline scenario in
[quickstart.md](./quickstart.md) work end to end. G is not optional: the brief requires the stale
fixtures and documentation to be updated in the same change as the delete-recording behavior.

## Scope guardrails

Carried from the brief's Out of Scope and the spec's. No task below the line, and any task that
starts to grow one is wrong:

- No delete is ever executed against a destination.
- No new CLI command **group**, and under AD005 no new command either.
- No durable per-operation apply ledger — applied identifiers live on the run result only.
- No configuration-version registry, validation or interpretation — the value is opaque.
- No load-path reference-scan replacement and no batched destination writes.
- No destination freshness check, plan expiration or conflict policy.
- No v1 migration and no second apply path.
- **No change to the live `sync` write path's peer behavior.** The zero-match and multi-match refusals
  belong to the new apply-path resolver only; the existing warn-and-continue stays exactly as it is
  (AD048).
- **No error-tolerance option added to `diff`.** Derivation failures there are hard failures; adding a
  `--continue-on-error` to the non-mutating command would be new user-facing surface the brief does
  not carry (AD047).
