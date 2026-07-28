# Review: `checklists/plan-artifact.md` (CHK001–CHK040)

**Reviewed**: 2026-07-26 · **Reviewer**: clean-context requirements evaluator (read-only)
**Spec under evaluation**: `dev/specs/001-plan-artifact-saved-apply/spec.md` (567 lines)
**Brief**: `db-001-plan-artifact-saved-apply.md` (brief_version 5, batch-v3)
**Repository verified at**: branch `001-plan-artifact-saved-apply-infp-653`, working tree clean

Verdicts are against the requirements documents. Every claim about existing repository behavior
below was checked by reading the code and is cited `file:line`.

## Verdict table

| Item | Verdict | Severity | One-line reason |
|---|---|---|---|
| CHK001 | DEFECT | RECOMMENDED | Manifest fields are spread across FR-004, FR-010, Key Entities and AD001; no requirement enumerates the complete set. |
| CHK002 | DEFECT | RECOMMENDED | The format-version field appears only at spec.md:369 (Key Entities); no FR requires it. |
| CHK003 | DEFECT | RECOMMENDED | FR-019 covers only v1; a newer or unrecognized declared format version has no reader behavior. |
| CHK004 | DEFECT | RECOMMENDED | FR-002 enumerates seven per-operation fields with no obligation level; absent-vs-empty is undefined. |
| CHK005 | DEFECT | RECOMMENDED | The action set is enumerated only in FR-001's coverage clause and Key Entities, never closed normatively. |
| CHK006 | SATISFIED | — | FR-014 states the property a tier assignment must satisfy (spec.md:322). |
| CHK007 | DEFECT | **BLOCKING** | The source-snapshot binding has no stated representation and no definition of "match"; SC-004's snapshot-binding case is unbuildable. |
| CHK008 | DEFECT | **BLOCKING** | Nothing recorded in the artifact can detect a truncated snapshot; AD001's `plan_checksum` covers only manifest + operations. |
| CHK009 | DEFECT | RECOMMENDED | Whether a new-format plan run still writes `plan.parquet` is unstated, though the code writes it unconditionally today. |
| CHK010 | DEFECT | RECOMMENDED | No write-order/atomicity requirement for the plan write; a crash between files can be misdiagnosed as a v1 plan. |
| CHK011 | DEFECT | RECOMMENDED | FR-002's "full payload" vs FR-018's absolute no-secret rule is unresolved for credential-bearing mapped fields (`PRODUCT-AMBIGUITY`). |
| CHK012 | DEFECT | RECOMMENDED | "the required source values as a full payload" is never defined — required by which authority is unstated. |
| CHK013 | DEFECT | **BLOCKING** | "Relationship change" is an action in Key Entities/SC-003 and a field of an operation in FR-002; the two models are incompatible. |
| CHK014 | DEFECT | RECOMMENDED | "destination identity" is undefined; FR-002/AD003 say "identity values", FR-003/AD002 say "identity". |
| CHK015 | DEFECT | RECOMMENDED | AD001 does not say whether excluded fields are removed or blanked, nor that concatenation uses no separator. |
| CHK016 | DEFECT | RECOMMENDED | FR-005's "fixed ordering of every ordered collection" does not distinguish meaning-bearing order from re-sortable order. |
| CHK017 | SATISFIED | — | FR-003 plus AD002 state what must change the identifier and what may change without it. |
| CHK018 | DEFECT | RECOMMENDED | FR-011 permits a caller-supplied version while forbidding new user-facing input, and never names the supplying surface. |
| CHK019 | DEFECT | RECOMMENDED | FR-004 and AD001 both assert the excluded set is *exactly* SC-006's masked set; it is not (three fields vs two). |
| CHK020 | SATISFIED | — | FR-010's operation count and FR-022's recorded count are one field with one meaning (`operations_count`, AD001). |
| CHK021 | DEFECT | **BLOCKING** | Under the Key Entities reading of CHK013, two legitimate relationship-change operations on one object collide and FR-021 fails the plan run. |
| CHK022 | DEFECT | RECOMMENDED | FR-019, Key Entities and AD001 agree on *read* and *delete* but none states whether the new path *writes* the pre-existing file. |
| CHK023 | SATISFIED | — | FR-002, Key Entities and AD003 state the reference shape consistently; AD003 only adds the cardinality rule. |
| CHK024 | DEFECT | RECOMMENDED | FR-026 asserts a property of an undesigned future change and is not falsifiable as worded. |
| CHK025 | DEFECT | RECOMMENDED | FR-022 and FR-026 appear in neither the SC set nor the traceability table; FR-021's uniqueness obligation has no criterion. |
| CHK026 | DEFECT | NIT | SC-006 names the masked fields but not *how* masking is applied. |
| CHK027 | SATISFIED | — | FR-019:341–342 plus AD001 define a v1 fixture precisely: `plan.parquet` present, `plan/manifest.json` absent. |
| CHK028 | DEFECT | NIT | No stated encoding/length/character domain for the configuration-version value beyond what AD001's canonical JSON implies. |
| CHK029 | SATISFIED | — | FR-005 states the single-process re-serialization property directly (spec.md:286–287). |
| CHK030 | DEFECT | RECOMMENDED | A run directory holding neither a manifest nor the pre-existing plan file has no specified behavior. |
| CHK032 | DEFECT | RECOMMENDED | Nothing states what a failed FR-021 uniqueness assertion leaves on disk; the residue collides with FR-019's v1 rule. |
| CHK031 | SATISFIED | — | AD001 gives the reader rule separating empty from torn; the checksum over a zero-line file follows from the byte rule. |
| CHK033 | DEFECT | RECOMMENDED | Cycles and self-references are unaddressed; FR-014's absolute tier guarantee is false for edges the existing tier computation drops. |
| CHK034 | DEFECT | RECOMMENDED | Plan size lives only in a deferral note plus a Key Entities clause; no FR and no Out-of-scope line. |
| CHK035 | DEFECT | RECOMMENDED | An absent or empty destination identity has no specified behavior, yet the identifier is derived from it. |
| CHK036 | DEFECT | RECOMMENDED | The obligation to the nine consumers is narrative only — no version-field requirement and no format-change policy. |
| CHK037 | DEFECT | RECOMMENDED | No rule for how a reader treats unknown manifest or operation fields, though DB-010 will add one. |
| CHK038 | SATISFIED | — | Assumptions:480–482 and Out of Scope:455–456 record the per-run-layout precondition; verified present in the tree. |
| CHK039 | DEFECT | NIT | The per-FR "per AD00N" pointers make the mapping traceable, but no sentence states the revisit obligation. |
| CHK040 | SATISFIED | — | Assumptions:482 records the lossiness; FR-019 + SC-011 + US2 scenario 4 record the consequence. |

**Counts** — SATISFIED 9 · DEFECT(BLOCKING) 4 · DEFECT(RECOMMENDED) 24 · DEFECT(NIT) 3 ·
NOT-APPLICABLE 0 · of which `PRODUCT-AMBIGUITY` 1 (CHK011).

## Repository facts this review relies on

Established by reading the tree, not assumed:

- The current plan artifact is a single flat Parquet file at `<run_dir>/plan.parquet`
  (`infrahub_sync/cache/parquet_io.py:81-89`), schema `PLAN_SCHEMA`
  (`infrahub_sync/cache/parquet_io.py:25-38`).
- It is lossy exactly as the brief states: `dest_id` and `attribute` are written as empty strings
  and there is no operation identifier or checksum column
  (`infrahub_sync/potenda/__init__.py:317-330`).
- `Potenda.apply_plan` reads that Parquet and dispatches per row to `destination.apply_cached_row`,
  guarding its absence with `NotImplementedError`, and never calls `diff_from`/`sync_from`
  (`infrahub_sync/potenda/__init__.py:341-370`). No adapter implements `apply_cached_row` (grep over
  `infrahub_sync/adapters/` returns only the potenda dispatch site and tests).
- `write_plan` is called unconditionally on the `diff` path (`infrahub_sync/cli.py:152`), the `sync`
  path (`infrahub_sync/cli.py:271`), and both branches of `sync_in_tiers`
  (`infrahub_sync/potenda/__init__.py:462`, `:499`). Nothing gates it.
- `diff` already accepts `--run-id` (`infrahub_sync/cli.py:96`, "Re-use a specific cache run id"),
  and `apply` already takes `--run-id` as required (`infrahub_sync/cli.py:298`). AD005's spelling
  extends surfaces that exist.
- Deletes are suppressed by the default flag: `Potenda.__init__` falls back to
  `DiffSyncFlags.SKIP_UNMATCHED_DST` when the project configures none
  (`infrahub_sync/potenda/__init__.py:88-90`), and the differ drops destination-only objects on that
  flag (`.venv/lib/python3.12/site-packages/diffsync/helpers.py:191-192`). AD004's "derive deletes
  from loaded destination state" is consistent with this.
- Per-run layout: `<cache_root>/<sync_name>/<run_id>/` (`infrahub_sync/cache/paths.py:56-59`) holding
  `run.json`, `plan.parquet`, `A/`, `B/`, `cursors.json`, `schema-sub-hash.txt`. Nothing iterates the
  run directory's children except `previous_successful_run_dir`, which only reads `run.json`
  (`infrahub_sync/cache/incremental.py:34-48`) — so a new `plan/` subdirectory does not disturb the
  existing layout.
- Parquet writes are already atomic (tmp + rename, `infrahub_sync/cache/parquet_io.py:56-71`) and so
  are the JSON sidecars (`infrahub_sync/cache/sidecars.py:13-24`). The new artifact has no such
  requirement stated.
- Tiers are per *kind*, computed from `schema_mapping[].fields[].reference`
  (`infrahub_sync/dependency_graph.py:25-36`). Self-edges are excluded from write-order edges
  (`:33-34`), and cycles through non-identity edges are broken by *dropping* those edges
  (`:81-100`).
- On the qualified path (`examples/netbox_to_infrahub/config.yml`) `compute_tiers` returns six tiers
  with **no** dropped edges and **no** active self-references (executed via
  `uv run python` against `compute_tiers`). This is why CHK033 is RECOMMENDED, not BLOCKING.
- Generated models are flat: the models template emits no `_children`
  (`infrahub_sync/generator/templates/diffsync_models.j2:29-48`), so no kind appears in two tier
  diffs. This rules out a second, engine-level collision vector for CHK021.
- Infrahub `List` attributes map to `list[Any]` (`infrahub_sync/generator/__init__.py:28`), i.e.
  order-bearing payload collections genuinely exist. This is the evidence behind CHK016.
- `examples/netbox_to_infrahub/config.yml:293` filters `name is_not_empty` with the comment
  "/!\ Netbox allows empty name for devices" — empty identity values are a real, already-known
  condition. This is the evidence behind CHK035.

---

## BLOCKING defects

### CHK007 — The source-snapshot binding has no stated representation

**Anchors**: spec.md:279–281 (FR-004), spec.md:298–300 (FR-009), spec.md:302–306 (FR-010),
spec.md:377–378 (Key Entities / Source snapshot), spec.md:401–407 (SC-004), spec.md:36–49 (AD001).

**Evidence**. FR-004 requires the manifest bind the artifact to "the source snapshot it was planned
against" (spec.md:280). FR-009 requires apply to verify "the source-snapshot binding still
match[es]" (spec.md:299). AD001 — the decision that fixes the concrete manifest fields — never
mentions the snapshot: its enumerated fields are `plan_checksum` and `operations_count` plus the
excluded run identifier and creation timestamp (spec.md:41–46). A grep for "snapshot" across
spec.md returns only prose uses; no field, no value, no equality rule. Key Entities offers only
"bound to the plan so the pair cannot tear" (spec.md:377–378).

SC-004 requires a **snapshot-binding mismatch** negative case as one of five (spec.md:405). A
mismatch cannot be constructed, and a match cannot be asserted, without knowing what value is
stored and what "match" compares. DBA-004 (brief:185) demands the same five tests. The requirement
is therefore untestable as written, which is the BLOCKING bar.

**Not a brief gap.** The brief carries DBR-006 ("checksum, configuration version, and
source-snapshot binding still match", brief:162) and DBR-015 ("Bind the plan and its source snapshot
so the pair cannot tear", brief:171) and delegates the format definition to DBR-008 ("Define the
plan artifact's format", brief:164). The representation is inside the spec's own mandate.

**Minimum fix**. Add to FR-004 a named manifest field for the binding and its match rule — e.g.
"a `source_snapshot` field recording, for each per-resource source snapshot file the run wrote, its
relative path, a content digest, and its row count; the binding matches when every recorded path
exists and its recomputed digest and row count equal the recorded values." One clause; it also
closes CHK008.

### CHK008 — A truncated snapshot is not detectable from anything the artifact records

**Anchors**: spec.md:41–46 (AD001, `plan_checksum` scope), spec.md:302–306 (FR-010),
spec.md:401–407 (SC-004, "truncated snapshot").

**Evidence**. AD001 defines `plan_checksum` as "a SHA-256 over the canonical manifest with
`plan_checksum`, the run identifier, and the creation timestamp excluded, concatenated with the
bytes of `operations.jsonl`" (spec.md:41–44). The source snapshot's bytes are outside that input.
FR-010's only positive detection mechanism is an operation count — "The manifest MUST carry an
operation count so that a plan with no operations is distinguishable from a plan whose operations
are missing" (spec.md:304–306) — which says nothing about the snapshot. AD001's torn rule is
likewise operations-only: "a torn one (file absent or line count disagreeing with the manifest)"
(spec.md:46).

So of SC-004's two tear cases, "absent operations" is detectable (count disagreement, or absent
file) and "truncated snapshot" is not. Absence of a snapshot file is detectable by a stat; a
*truncated* one is not distinguishable from a small one. SC-004 and DBA-004 both name it as a
required negative case, so the criterion is unachievable as specified.

Note the tree makes this worse rather than better: snapshots are written atomically via tmp+rename
(`infrahub_sync/cache/parquet_io.py:56-71`), so a truncated snapshot has to be produced
deliberately by the test — which means the test can only pass if the artifact records something the
check can compare against.

**Minimum fix**. The same clause as CHK007: require a per-snapshot-file digest and row count in the
manifest, and add to FR-010 "a source snapshot whose recomputed digest or row count disagrees with
the manifest is truncated and MUST be refused on the same path."

### CHK013 — The action vocabulary is internally inconsistent

**Anchors**: spec.md:264–265 (FR-001), spec.md:267–272 (FR-002), spec.md:372–373 (Key Entities /
Planned operation), spec.md:396–400 (SC-003).

**Evidence**. Three mutually exclusive positions coexist:

1. Key Entities makes relationship change a *value of the action field*: "the action (create,
   update, delete, or relationship change)" (spec.md:372–373).
2. FR-002 makes relationship references *fields of an operation*: every operation carries "…
   relationship references, and a dependency tier" (spec.md:268–269), i.e. a create or update
   record carries its relationships inline.
3. SC-003 treats it as a *write class*: "The create, update, and relationship write classes end at
   clean-single-run counts" (spec.md:396–397), inheriting DBA-003's per-class matrix (brief:184).

If (1) holds, an operation exists whose action is "relationship change" and whose payload semantics
FR-002 never describes — and CHK021's collision follows. If (2) holds, "relationship change" is not
an action value and SC-003's third class is not a class of operation, so the conformance matrix
DBA-003 requires has no third row to fill. The two readings cannot both be implemented, and SC-003
cannot be evidenced until one is chosen. FR-001's "every proposed create, update, delete, and
relationship change" (spec.md:264–265) is neutral between them.

**Origin note.** The brief seeds the phrasing — DBR-001 (brief:157) lists "relationship change"
alongside create/update/delete while the In-scope bullet lists "relationship references" as a
per-operation field (brief:72). But resolving it is a format-definition decision the brief
explicitly delegates via DBR-008 (brief:164), so this is a spec defect, not a brief gap. It is not
a product decision: both readings deliver the same user-visible outcome.

**Minimum fix** (either, not both):

- (a) Close the enum in FR-002 — "the action is exactly one of `create`, `update`, `delete`" — and
  add "a relationship change is carried as relationship references on the create or update
  operation for the owning object, never as a separate operation." Then restate SC-003's third
  class as "operations carrying relationship references." *(Recommended: it is the reading FR-002,
  FR-014 and AD003 already assume.)*
- (b) Make `relationship_change` a fourth action value, and extend FR-003/AD002's identifier triple
  to include the relationship name — without which CHK021 stays blocking.

### CHK021 — Two legitimate relationship-change operations on one object collide

**Anchors**: spec.md:273–277 (FR-003), spec.md:50–55 (AD002), spec.md:346–348 (FR-021),
spec.md:244–247 (Edge Cases / Identifier collision).

**Verdict: CONFIRMED**, conditional on and inseparable from CHK013.

**Evidence**. AD002 derives the identifier as `op_` + the first 16 hex characters of a SHA-256 over
"the canonical JSON of the triple (action, destination kind, destination identity)" (spec.md:50–52).
The relationship name is not in the triple. Under the Key Entities reading where "relationship
change" is an action value (spec.md:372–373), an object with two changed relationships yields two
operations sharing all three components — for example a `DcimDevice` whose `tags` and
`primary_address` both change (`examples/netbox_to_infrahub/config.yml:242`, `:245`). FR-021 then
mandates that this "MUST fail the plan run" (spec.md:346–348), so a routine, correct plan is
rejected — DBR-001 and DBA-008 both become unachievable on the qualified path.

The Edge Cases text compounds it by asserting the collision is always pathological: "Since the
identifier is derived from the action, kind, and destination identity, a collision means two
operations target the same object with the same action; the plan run fails rather than emitting a
plan whose identifiers do not address one operation each" (spec.md:245–247). Under reading (1) that
inference is simply wrong — two operations *legitimately* target the same object with the same
action.

Two other collision vectors were checked and **refuted**: (i) a kind appearing in two tier diffs is
impossible because generated models are flat, with no `_children` emitted
(`infrahub_sync/generator/templates/diffsync_models.j2:29-48`); (ii) a delete recorded twice is
excluded by AD004's single-source rule (spec.md:73–74). So CHK013 is the whole of the exposure.

**Minimum fix**. Resolve CHK013 with option (a) — then a create and an update on the same object
differ by action and no legitimate collision exists — or, if option (b) is chosen, extend AD002's
triple to a quadruple including the relationship name and amend the Edge Cases inference at
spec.md:245–247 accordingly.

---

## RECOMMENDED defects

### CHK001 — No requirement enumerates the complete manifest field set

Anchor spec.md:279–283 (FR-004), :304–306 (FR-010), :367–370 (Key Entities), :41–46 (AD001). The
fields are assembled from four places: FR-004 gives run / configuration version / source snapshot /
checksum; FR-010 adds the operation count; Key Entities alone adds the format version; AD001 alone
gives the concrete names `plan_checksum` and `operations_count`. This specification declares itself
the owner of a contract nine later outcomes consume (spec.md:494–500), so the field list should be
normative in one place. **Minimum fix**: make FR-004 enumerate the field set as a list, with FR-010
and Key Entities referring to it rather than adding to it.

### CHK002 — The format-version field is required by nothing

Anchor spec.md:369 ("records the format version and the operation count"), spec.md:366 ("versioned
so a pre-existing v1 plan is recognizable and refusable"). A grep for "format version" over spec.md
returns only line 369; no FR mentions it, and FR-019 deliberately does not use it — detection is by
file presence: "a run holding only the pre-existing plan file and no new-format manifest is a v1
plan" (spec.md:341–342). So the only normative consumer of a version field is absent, leaving the
field optional in practice. **Minimum fix**: add to FR-004 "the manifest MUST carry an explicit
format-version field identifying this format," and state its relationship to FR-019's
presence-based detection (the field identifies the format for future readers; presence-based
detection exists because a v1 artifact has no manifest to read a field from).

### CHK003 — No reader behavior for an unrecognized format version

Anchor spec.md:338–343 (FR-019). FR-019 handles exactly one case, v1. Nothing says what happens
when a manifest declares a format version the reader does not support — the forward case that
arises the moment DB-010 or DB-017 revises the format. **Minimum fix**: one clause in FR-019 — "a
manifest whose declared format version is not one the reader supports MUST be refused on the same
path, naming the declared version, and MUST NOT be parsed further."

### CHK004 — Per-operation fields carry no obligation level

Anchor spec.md:267–272 (FR-002). Seven fields are listed with a flat MUST. Nothing says whether
relationship references are absent, `null`, or `[]` on a flat create, nor whether a dependency tier
is always present. For a format nine outcomes read, absent-versus-empty is a compatibility
decision. **Minimum fix**: annotate FR-002's list — which fields are always present, and that a
field with no content is written as an empty collection rather than omitted (or vice versa; pick
one).

### CHK005 — The action set is not closed normatively

Anchor spec.md:264–265 (FR-001), :267–268 (FR-002), :372–373 (Key Entities). FR-001 enumerates the
four in a coverage clause ("containing every proposed create, update, delete, and relationship
change"), which constrains what the plan must *include*, not what the action field may *hold*.
FR-002 requires "the action" without enumerating. Only Key Entities enumerates, and CHK013 shows
that enumeration conflicts with FR-002. **Minimum fix**: fold into the CHK013 fix — state the
closed set of permissible action values in FR-002.

### CHK009 / CHK022 — Whether the new path writes `plan.parquet` is unstated

Anchors spec.md:47–48 (AD001), :342–343 (FR-019), :363–366 (Key Entities / Plan artifact).

AD001 says the pre-existing `plan.parquet` "is left in place untouched and is not read by the new
apply path" (spec.md:47–48); FR-019 says "The pre-existing file MUST be left in place rather than
deleted or rewritten" (spec.md:342–343). Both speak about a file that already exists. Neither says
whether a *new* plan run continues to emit one. Today it does, unconditionally, on every path:
`infrahub_sync/cli.py:152`, `infrahub_sync/cli.py:271`, `infrahub_sync/potenda/__init__.py:462`
and `:499`.

Two consequences, both real:

- **FR-018 / SC-010 scope.** If `plan.parquet` keeps being written it sits in the same run
  directory and carries `new_value` as a JSON dump of the changed source attributes
  (`infrahub_sync/potenda/__init__.py:324-325`). Whether it counts as "the plan artifact" for the
  canary scan (spec.md:337, :428–430) is undefined.
- **FR-019's own detection rule.** `diff --run-id` re-uses an existing run directory
  (`infrahub_sync/cli.py:96`), so a second plan into the same directory would overwrite a
  `plan.parquet` that FR-019 says must not be rewritten.

**Minimum fix**: one clause — state whether a plan run in this format continues to write
`plan.parquet`; if it does, state that the file is not part of the plan artifact for FR-018, SC-006
and SC-010, and narrow FR-019's "not rewritten" to a v1 artifact from a prior run.

### CHK010 / CHK032 — No write-ordering rule; a failed or crashed plan write is misdiagnosed

Anchors spec.md:264–265 (FR-001), :302–306 (FR-010), :346–348 (FR-021), :341–342 (FR-019).

FR-010 gives the *reader* a torn-detection rule but nothing constrains the *writer*. Combined with
FR-019's presence-based v1 rule, a specific misdiagnosis follows: if a plan run writes
`operations.jsonl` and then fails — either by crashing, or by FR-021's uniqueness assertion firing
— the run directory holds `plan.parquet` (written earlier on the same path, see CHK009) and no
`plan/manifest.json`. FR-019:341–342 classifies exactly that shape as a **v1 plan**, so the
operator is told to re-plan because their plan is in the old format, which is false. The remedy the
message gives is right by accident; the diagnosis is wrong, and a torn new-format artifact is
indistinguishable from a v1 one.

The tree shows the surrounding code already solves this class of problem — tmp+rename for Parquet
(`infrahub_sync/cache/parquet_io.py:56-71`) and for sidecars
(`infrahub_sync/cache/sidecars.py:13-24`) — so the requirement is not exotic.

**Minimum fix**: (i) add to FR-001 or FR-010 "the artifact MUST be written so that no interruption
leaves a manifest whose checksum validates over an incomplete operations file — the manifest is
written last and atomically"; (ii) add to FR-019 "a run directory containing a plan directory
without a manifest is an incomplete plan, refused with a message distinct from the v1 message." (ii)
also answers CHK032's "what does a failed uniqueness assertion leave behind."

### CHK011 — "Full payload" versus "no secret value" is unresolved · `PRODUCT-AMBIGUITY`

Anchors spec.md:267–269 (FR-002), :337 (FR-018), :428–430 (SC-010).

FR-002 requires "the required source values as a full payload"; FR-018 states, without exception,
"No secret value MUST appear in the plan artifact or in any review output." If a project's schema
mapping maps a credential-bearing source field, the two cannot both hold: recording the payload
violates FR-018, and redacting it violates FR-002 and breaks FR-013's convergent write (the applied
value would differ from the reviewed one).

This is a product decision the brief does not carry: whether the answer is "refuse to plan such a
field", "redact and mark the operation unappliable", or "out of scope — no supported mapping carries
a secret." **Do not pick a reading.** The brief's Requirements table (DBR-017, brief:173) or its
"Edge cases and failure behavior" section should have carried it; DBR-017 states only the batch-wide
absolute rule.

Note DBA-010 remains achievable as written: adapter connection credentials come from `settings`, not
from mapped fields, so a canary-credential scan passes regardless. That is why this is RECOMMENDED
rather than BLOCKING.

### CHK012 — "the required source values as a full payload" is undefined

Anchors spec.md:269 (FR-002), :373 (Key Entities). "Required" by which authority — the schema
mapping's declared field list, the destination schema's mandatory attributes, or whatever the source
record happens to hold? The three differ, and the choice determines what FR-013's convergent write
sends. The brief uses the same undefined phrase (DBR-011, brief:167), so the wording is inherited,
but the spec owns the format. **Minimum fix**: define it once — "the values of exactly the fields
the run's schema mapping declares for that kind, i.e. the values the write path would send."

### CHK014 — "destination identity" is never given one representation

Anchors spec.md:268 and :270–271 (FR-002), :275 (FR-003), :51–52 (AD002), :58–59 (AD003).

FR-003 and AD002 say "destination identity"; FR-002's last sentence and AD003 say "identity
values". Nothing states whether that is an ordered list, a mapping from identifier field name to
value, or a joined string. It matters twice: AD002 hashes "the canonical JSON of the triple", whose
digest differs across those three shapes, and AD003 orders cardinality-many references "canonically
by the peer identity" (spec.md:61–62), which is a different sort depending on the shape. For a
contract nine outcomes must reproduce byte-for-byte, one shape must be named. Note the tree's
existing convention is a `__`-joined diffsync unique-id (brief:63), which is *not* what AD002
describes. **Minimum fix**: define "destination identity" once in Key Entities as the mapping from
the kind's declared identifier field names to their values, and state that the operation record,
the identifier derivation, and every relationship reference use that same representation.

### CHK015 — The checksum input is under-scoped in two small ways

Anchor spec.md:41–45 (AD001). Two things are unstated: whether the three excluded fields are
*removed* from the object before canonicalization or *set to a fixed placeholder* (both are
deterministic, and they produce different digests), and whether the manifest bytes and the
operations bytes are joined with any separator ("concatenated with" implies none but does not say
so). Order is stated (manifest first). **Minimum fix**: "the excluded fields are removed from the
manifest object before canonicalization; the canonical manifest bytes and the bytes of
`operations.jsonl` are concatenated in that order with no separator."

### CHK016 — FR-005 does not distinguish order-bearing from re-sortable collections

Anchor spec.md:284–287 (FR-005), :61–62 (AD003). FR-005 requires "a fixed ordering of every ordered
collection inside them", which reads either as "record the order deterministically" or as "sort it".
AD003 chooses *sort* for cardinality-many relationship references. Applying the same choice to
payload collections would corrupt data: Infrahub `List` attributes map to `list[Any]`
(`infrahub_sync/generator/__init__.py:28`), and their order is the value. A sorted payload would
make the applied value differ from the source value — breaking the review-equals-applied
guarantee. **Minimum fix**: split the clause — "collections whose order is part of the value (list
attributes in a payload) MUST be serialized in source order; collections whose order carries no
meaning (relationship reference lists) MUST be canonically sorted, per AD003."

### CHK018 — FR-011 is self-tensioned and names no supplying surface

Anchor spec.md:307–311. "…unless the caller supplies a version identifier explicitly, in which case
that value MUST be stored verbatim… No new user-facing input is introduced." Either "caller" means
the in-process API (consistent, but unnamed) or it means an operator (contradictory). An implementer
cannot tell whether to add a parameter, a CLI option, or nothing. **Origin**: the brief's DBR-018
row (brief:174) carries this wording verbatim, so the tension is inherited — the brief's
Requirements table should have named the supplying surface. The spec could still close it.
**Minimum fix**: "the caller here is the in-process plan API; the value is supplied as a parameter
to it and no CLI option is added."

### CHK019 — FR-004's "exactly the set SC-006 masks" is false

Anchors spec.md:281–283 (FR-004), :43–44 (AD001), :411–414 (SC-006). FR-004: "The checksum MUST
exclude only the checksum field itself, the run identifier, and the creation timestamp, so that the
fields SC-006 masks are exactly the fields the checksum does not cover." AD001 repeats it: "the
excluded set is exactly the set SC-006 masks." SC-006 masks two fields — "the run identifier and
the creation timestamp" (spec.md:413) — not three. The sets legitimately differ, because
`plan_checksum` is a function of byte-identical inputs and is therefore itself byte-identical
without masking. The behavior is fine; the stated equivalence is wrong, in a contract nine outcomes
will quote. **Minimum fix**: reword FR-004 and AD001 — "the checksum excludes `plan_checksum`, the
run identifier and the creation timestamp; SC-006 masks the run identifier and the creation
timestamp, and needs no mask for `plan_checksum` because it is a function of the checksummed bytes
alone."

### CHK024 — FR-026 is not verifiable as worded

Anchor spec.md:358–359. "The plan contract MUST order operations without prescribing write
granularity, so batched destination writes remain possible later without a plan-format change." The
consequent asserts a property of a change that does not exist yet and is explicitly out of scope
(spec.md:462–463, brief:110), so no test can falsify it. Note the brief carries this as a
*Constraint* (brief:218–219), not as a requirement; the spec promoted it to a MUST. **Minimum fix**:
either move it to a Constraints/design-principle note, or restate it as an inspectable property of
the artifact — "the artifact MUST NOT record any grouping of operations into write units; ordering
is expressed only as the operation sequence and each operation's dependency tier."

### CHK025 — FR-021, FR-022 and FR-026 lack acceptance criteria

Anchors spec.md:346–348, :349–351, :358–359; traceability table spec.md:506–540.

Verified by grep across spec.md: `FR-021` appears twice (its definition and the DBR-005 row at
spec.md:512, whose criterion SC-005 is about review-to-apply identifier linkage, not uniqueness);
`FR-022` and `FR-026` appear once each, in their own definitions only. So FR-022's artifact shape
has no SC (User Story 3 scenario 3 at spec.md:182–183 covers the no-op apply but not the
"present-but-empty with a recorded count of zero" representation), FR-021's uniqueness assertion has
no SC, and FR-026 has neither. All three trace to brief *edge cases and constraints*
(brief:203–206, brief:218–219) rather than to a DBA, which is why this is RECOMMENDED.
**Minimum fix**: add an SC asserting the empty-plan representation and no-op apply; add an SC
asserting that a duplicate-identifier plan fails the plan run and leaves no manifest (pairs with
CHK032); and give FR-026 the inspectable form from CHK024 with a one-line SC, or demote it.

### CHK030 — A run directory with neither artifact has no behavior

Anchor spec.md:338–343 (FR-019). FR-019 defines one shape (pre-existing plan file, no manifest → v1)
and the CHK010 fix defines a second (plan directory, no manifest → incomplete). The third — nothing
at all, e.g. a mistyped `--run-id` — is unspecified. Today `apply_plan` would surface a raw fsspec
`FileNotFoundError` from `read_plan` (`infrahub_sync/potenda/__init__.py:362`,
`infrahub_sync/cache/parquet_io.py:87-89`), not a clear message. **Minimum fix**: one clause —
"an apply requested for a run holding neither a new-format manifest nor the pre-existing plan file
MUST be refused with a message stating no plan exists for that run, distinct from the v1 message."

### CHK033 — Cycles and self-references are unaddressed, and FR-014's guarantee is absolute

Anchor spec.md:318–323 (FR-014), :268 (FR-002). FR-014 ends "Dependency-tier ordering MUST guarantee
a peer is written before anything referring to it" — with no qualification. The existing tier
machinery cannot provide that in general: self-edges are excluded from write-order edges
(`infrahub_sync/dependency_graph.py:33-34`), and cycles through non-identity edges are resolved by
*dropping* the edge (`:81-100`). For a dropped or self edge, a peer created by the same plan may be
written after the operation referring to it. FR-014's fallback — "MUST fall back to querying the
destination for that identity on a miss" (spec.md:321) — also misses in that case, and FR-014 does
not say what happens when both the cache and the destination miss.

**Not blocking**, because the qualified path is clean: `compute_tiers` on
`examples/netbox_to_infrahub/config.yml` returns six tiers with an empty dropped-edge list and no
active self-references (verified by execution). So DBA-008 is achievable as scoped.

**Minimum fix**: qualify FR-014 — "the tier guarantee holds for dependency edges the tier
computation retains; for a reference on a self-edge or an edge dropped to break a cycle, resolution
falls back to the destination query, and an unresolvable peer MUST fail the operation with a message
naming the kind, the identity and the relationship." Add a sentence to Assumptions recording that
the qualified configuration has no dropped or self edges.

### CHK034 — Plan size is neither required nor excluded

Anchor spec.md:560–564 (Open Design Decisions), :363–366 (Key Entities). The streamability property
that later outcomes depend on — "readable without loading all of it at once" (spec.md:365) — sits in
Key Entities prose, and the volume/latency question sits in a deferral note. Neither is a
requirement and neither is in Out of Scope. The brief legitimately sets no target (nothing in its
Constraints or Acceptance criteria), and the spec is right not to invent one. **Minimum fix**:
promote the streamability property into FR-007 ("…and MUST be readable without loading the whole
artifact into memory"), and add one Out-of-scope line for plan-volume and review-latency targets.

### CHK035 — An absent or empty destination identity has no behavior

Anchors spec.md:273–277 (FR-003), :267–269 (FR-002). The identifier is derived from the identity, so
an empty identity produces a well-formed identifier over empty input — and two objects with empty
identities produce the *same* identifier, tripping FR-021 and failing the whole plan run with a
collision message that names the wrong problem. This is not hypothetical: the qualified
configuration filters on `name is_not_empty` with the comment "/!\ Netbox allows empty name for
devices" (`examples/netbox_to_infrahub/config.yml:293`), i.e. empty identity values are a known
condition handled today only by a hand-written filter. **Minimum fix**: add to FR-003 — "an
operation whose destination identity has an absent or empty required identifier value MUST fail the
plan run before identifier derivation, naming the kind and the field."

### CHK036 — The nine-consumer obligation is narrative only

Anchor spec.md:494–500 (Dependencies). "This specification owns a shared contract… Any change to the
format after this ships is a breaking change for all nine" is stated in a narrative section with no
corresponding requirement: no FR requires a version field (CHK002) and no FR states a change policy.
**Minimum fix**: one FR — "the manifest MUST carry an explicit format-version field; a change to the
format that a reader of the previous version cannot process MUST increment it."

### CHK037 — No rule for unknown manifest or operation fields

Anchor spec.md:496–499 (Dependencies), which names DB-010's schema-fingerprint manifest field as a
known future addition. Nothing says whether a reader ignores or refuses fields it does not
recognize. Since the checksum covers the canonical manifest as written, ignore-unknown is
compatible — but it must be stated, or the first additive change becomes a breaking one by
accident. **Minimum fix**: one clause in FR-004 — "a reader MUST ignore manifest and operation
fields it does not recognize; unrecognized fields remain covered by the checksum, and adding one
MUST NOT change the format version."

---

## NIT defects

### CHK026 — SC-006 does not say *how* fields are masked

Anchor spec.md:411–414. The masked fields are named and AD001 identifies the two files
(`manifest.json`, `operations.jsonl`), but "with the varying fields masked" does not say whether the
value is replaced with a constant or the key removed. Immaterial to the outcome — both runs mask
identically either way — hence NIT. **Minimum fix**: "…masked by replacing each value with a fixed
placeholder before comparison."

### CHK028 — SC-013's opaque-string domain is only implicit

Anchors spec.md:438–442 (SC-013), :307–311 (FR-011), :382–384 (Key Entities). AD001's canonical
UTF-8 JSON manifest implies the domain is "any string representable in JSON", which is enough to
build a round-trip test, but no requirement says so. **Minimum fix**: one clause in FR-011 — "the
value is an arbitrary UTF-8 string; no length, encoding or character constraint is imposed beyond
JSON representability."

### CHK039 — The revisit obligation for unratified decisions is traceable but unstated

Anchors spec.md:31–34 (Clarifications preamble), :544–557 (Open Design Decisions). Each affected
requirement carries a "per AD00N" pointer (spec.md:272, :283, :287, :343, :278, :323), so the
mapping is recoverable, but no sentence states that non-ratification obliges revisiting them.
**Minimum fix**: one sentence in the Clarifications preamble — "if a decision is not ratified, every
requirement citing it must be revisited: AD001 → FR-002, FR-004, FR-005, FR-010, FR-019; AD002 →
FR-003, FR-021; AD003 → FR-002, FR-014; AD004 → FR-015; AD005 → FR-008."

---

## Verdicts on the five flagged items

| Item | Verdict | Deciding evidence |
|---|---|---|
| CHK021 | **CONFIRMED** (BLOCKING) | AD002's triple (spec.md:51–52) omits the relationship name; under Key Entities' action enum (spec.md:372–373) an object with two changed relationships yields two operations sharing action+kind+identity, which FR-021 (spec.md:346–348) makes fatal. Two rival collision vectors refuted: flat generated models (`diffsync_models.j2:29-48`) and AD004's single-source delete rule. |
| CHK013 | **CONFIRMED** (BLOCKING) | Three incompatible positions: action value (spec.md:372–373), field of an operation (spec.md:268–269), write class (spec.md:396–397). SC-003's third conformance row cannot be defined until one is chosen. |
| CHK007 | **CONFIRMED** (BLOCKING) | FR-004 requires the binding (spec.md:280); AD001's field list (spec.md:41–46) never mentions the snapshot; grep for "snapshot" over spec.md yields prose only. SC-004's snapshot-binding-mismatch case (spec.md:405) is unbuildable. |
| CHK008 | **CONFIRMED** (BLOCKING) | `plan_checksum`'s input is manifest + `operations.jsonl` bytes only (spec.md:41–44); FR-010's only mechanism is an operation count (spec.md:304–306); AD001's torn rule is operations-only (spec.md:46). SC-004's truncated-snapshot case has nothing to compare against. |
| CHK024 | **CONFIRMED** (RECOMMENDED) | FR-026 (spec.md:358–359) asserts a property of an out-of-scope future change (spec.md:462–463); the brief carries it as a Constraint (brief:218–219), not a requirement. |
| CHK025 | **CONFIRMED, partially** (RECOMMENDED) | Grep: `FR-022` and `FR-026` appear only in their own definitions; `FR-021` appears in the DBR-005 traceability row (spec.md:512) but its criterion SC-005 covers identifier linkage, not uniqueness. All three trace to brief edge cases/constraints, not to a DBA. |

## Brief-level gaps to route to planner feedback

| Item | What the brief never said | Brief section that should have carried it |
|---|---|---|
| CHK011 (`PRODUCT-AMBIGUITY`) | What happens when a mapped source field is itself credential-bearing — refuse, redact, or declare out of scope. DBR-017 states only the absolute no-secret rule. | Requirements (DBR-017) or "Edge cases and failure behavior" |
| CHK018 | Which surface supplies an explicit configuration-version identifier. DBR-018 pairs "unless the caller supplies" with "no new user-facing input" without naming the caller. | Requirements (DBR-018) |
| CHK012 | What "full payloads" means — required by the mapping, the destination schema, or the source record. | Requirements (DBR-011) |
| CHK013 (contributing) | DBR-001 lists "relationship change" as a peer of create/update/delete while the In-scope bullet lists relationship references as a per-operation field. Resolvable inside DBR-008, so recorded as a spec defect, but the phrasing originates here. | Requirements (DBR-001) vs In scope |
| CHK034 (handled correctly) | No plan-volume or review-latency target. The spec rightly declines to invent one; noted only so the absence is visible to the planner. | Constraints / Acceptance criteria |

No item was NOT-APPLICABLE: all forty interrogate the plan-artifact format, which is squarely inside
the brief's In-scope list (brief:70–75) and its owned shared contract (brief:245–253).
