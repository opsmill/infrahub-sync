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

### Session 2026-07-26 — specification clarification

Five underspecified areas were resolved. All five are implementation decisions the brief either
delegates explicitly or does not reach; none changes scope. Each is **provisional** pending
ratification — the `[PROVISIONAL AD0NN]` markers below are the ratification handles and are
removed once the decision is confirmed.

If a decision is not ratified, every requirement citing it must be revisited: AD001 → FR-002,
FR-004, FR-005, FR-010, FR-019; AD002 → FR-003, FR-021; AD003 → FR-002, FR-014; AD004 → FR-015,
FR-016; AD005 → FR-008. The revisit sets for AD008–AD036 are the requirements carrying each
marker.

- Q: What is the plan artifact's concrete on-disk encoding and file layout, how is its checksum
  computed, and how is a pre-existing v1 plan detected? → A: A `plan/` directory inside the
  existing per-run directory holds `manifest.json` and `operations.jsonl`. Both are canonical
  JSON — UTF-8, keys sorted, no insignificant whitespace, LF line endings — with
  `operations.jsonl` carrying exactly one operation object per line in dependency-tier order and,
  within a tier, ascending operation-identifier order. A single manifest field `plan_checksum`
  holds a SHA-256 over the canonical manifest with `plan_checksum`, the run identifier, and the
  creation timestamp excluded, concatenated with the bytes of `operations.jsonl`. The checksum
  therefore excludes three fields while SC-006 masks two: SC-006 needs no mask for `plan_checksum`
  because `plan_checksum` is a function of the checksummed bytes alone and is already byte-identical
  across re-plans of identical content. Excluding the run identifier and the creation timestamp is
  what makes the manifest byte-identical across re-plans. A manifest field `operations_count`
  distinguishes an empty plan (file present, zero lines, count 0) from a torn one (file absent or
  line count disagreeing with the manifest). The pre-existing `plan.parquet` is left in place
  untouched and is not read by the new apply path; the absence of the whole `plan/` directory is
  what identifies a v1 plan, per AD014. `[PROVISIONAL AD001]`
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

### Session 2026-07-26 — checklist evaluation

Sixteen further areas were resolved after four independent evaluations worked 166 checklist items
against this specification and the repository. Each resolution takes the reading that does **not**
expand scope; where the brief's own text settles the matter it is followed rather than re-decided.
All sixteen are **provisional** on the same basis as AD001–AD005, and the `[PROVISIONAL ADnnn]`
markers are the ratification handles.

- Q: What value binds the plan to its source snapshot, and how is a truncated snapshot detected? →
  A: A manifest field `source_snapshot` records, per source-snapshot file the plan was computed
  against, that file's run-relative path, a SHA-256 digest of its content, and its row count. "Match"
  is recomputed equality of all three; an absent recorded file, or a disagreeing digest or row count,
  is a refusal. One field yields both a binding-mismatch signal and a truncation signal, and the row
  count is already computed on the load path. The source snapshot is one Parquet file per resource
  rather than a single file — `write_resource_side` writes `<run_dir>/A/<resource>.parquet`
  (`cache/parquet_io.py:92-142`, called from `_write_side_snapshot`, `potenda/__init__.py:123`) —
  which is why the field is per file. Because `plan_checksum` covers the canonical manifest, tampering
  with the recorded digests fails the checksum, so the pair cannot tear without the snapshot bytes
  themselves entering the checksum. `[PROVISIONAL AD008]`
- Q: Is "relationship change" a fourth action, or a field carried on a create or update? → A: The
  action vocabulary is closed to `create | update | delete`. A relationship change travels as
  relationship references on the owning object's create or update operation, never as a separate
  action, which matches the brief's In-scope wording ("relationship references" as a per-operation
  field) and matches the generated models, which are flat and carry relationships as fields
  (`generator/templates/diffsync_models.j2:29-48`). SC-003's third write class is therefore
  "operations whose payload carries relationship references". AD002's identifier triple is unchanged:
  under this model exactly one operation exists per (action, kind, identity), so a collision is always
  pathological and FR-021's assertion is correct as written. `[PROVISIONAL AD009]`
- Q: What run state does a refused apply record, and what is "an applied state"? → A: A refused apply
  records run state `failed`, and "an applied state" means `status: applied`, in the existing run
  sidecar vocabulary `pending | running | dry-run | applied | failed` (`cache/sidecars.py:71`). No new
  state is introduced, because `previous_successful_run_dir` consumes that vocabulary through
  `_SUCCESS_STATUSES = frozenset({"applied", "dry-run"})` (`cache/incremental.py:24`) and adding one
  would be a compatibility change this outcome does not authorize; `failed` is already outside that
  set, which is the behavior that matters. The pre-existing schema-subhash refusal path, which today
  aborts via `print_error_and_abort` (`cli.py:336-340`) after the run sidecar was written with
  `status: running` (`cli.py:322`) and therefore leaves `running` on disk permanently, must record
  `failed` too. `[PROVISIONAL AD010]`
- Q: How can FR-025's partial-apply record hold while a durable crash-surviving ledger is out of
  scope? → A: "Stops partway" means an apply that terminates in-process with a reported error. The
  record is best-effort at that point and is explicitly **not** required to survive abnormal process
  termination. SC-003's crash windows stay evaluable without a durable ledger because their
  measurement is destination-side — object counts and identities — so the windows remain meaningful as
  injection points even though no persisted record distinguishes them afterwards.
  `[PROVISIONAL AD011]`
- Q: What stops a `plan/` directory copied into a different run directory from verifying clean? →
  A: An additional pre-apply check — one of the five FR-009 now enumerates, once AD028 added the
  format-version check ahead of it: the manifest's recorded run identifier must equal the run being
  applied. This is a separate equality comparison rather than a checksum input, because AD001
  deliberately excludes the run identifier from `plan_checksum` so the manifest can be byte-identical
  across re-plans (SC-006) — which is exactly what leaves the copied-plan hole. DBA-004 names three
  checks but does not forbid additional ones, and refusing a mis-filed plan is inside DBR-003's "safe
  to apply". `[PROVISIONAL AD012]`
- Q: At apply, the stored configuration-version value is compared for equality — against what? →
  A: The apply recomputes the value by the same default rule (a deterministic content checksum over
  the configuration it is applying with) and compares for equality, unless an in-process caller
  supplies one verbatim, in which case the supplied value is compared verbatim. The value is never
  parsed either way, and no new user-facing input is added: the CLI apply path uses the default rule
  only. `[PROVISIONAL AD013]`
- Q: A crashed new-format plan write leaves `plan.parquet` with no `plan/manifest.json` — is that a v1
  plan or a torn one, and do new plan runs keep writing `plan.parquet`? → A: `plan/operations.jsonl`
  is written first and `plan/manifest.json` **last**, so the manifest's presence is the commit point,
  matching the atomicity discipline the existing sidecars already use (`cache/sidecars.py:13-24`,
  tmp+replace). A v1 verdict then requires the absence of the whole `plan/` directory; a `plan/`
  directory present without a complete manifest is TORN, not v1, which makes the two cases disjoint by
  construction rather than by heuristic. New plan runs keep writing `plan.parquet` unchanged — it is
  written unconditionally today (`cli.py:152`, `cli.py:271`, `potenda/__init__.py:462`, `:496-499`)
  and this outcome authorizes no removal — and the new reader never reads it, so DBR-019's "no second
  apply path" still holds structurally. `[PROVISIONAL AD014]`
- Q: How does the adapter execute a planned update, given the existing update path cannot run without
  a destination load? → A: Planned creates **and** updates both route through the HFID-keyed
  convergent upsert — `client.create(...)` followed by `save(allow_upsert=True)`
  (`adapters/infrahub.py:611-612`) — and never through `InfrahubModel.update`, which opens with
  `client.get(id=self.local_id, ...)` (`adapters/infrahub.py:622`) on a `local_id` populated only by a
  destination load (`adapters/infrahub.py:510`, `infrahub_sync/__init__.py:232`) that DBR-004 forbids.
  Cardinality-many relationships are **replace-set**, which is the existing behavior: `update_node`
  computes `compare_lists(existing_peer_ids, new_peer_ids)` and then removes `existing_only` and adds
  `new_only` (`adapters/infrahub.py:166-175`). An update payload is authoritative for the mapped
  fields it carries and does not touch unmapped destination fields. `[PROVISIONAL AD015]`
- Q: What happens when a planned relationship peer matches no destination object, or more than one? →
  A: A **zero** match refuses the operation and fails the run, naming the peer kind, the peer identity,
  and the referring operation identifier. A **multiple** match refuses, naming the peer kind, the peer
  identity, and the match count. Never a silent skip: "a silent skip would make the applied set differ
  from the reviewed set" is the brief's own reasoning for DBR-016 and it governs a dropped relationship
  exactly as it governs a dropped operation. This replaces today's behavior, which drops a zero match
  with a log warning (`adapters/infrahub.py:141-143`, `:212-214`, `:229-231`) and surfaces a
  multi-match as a bare `IndexError("More than 1 node returned")` from the SDK
  (`infrahub_sdk/client.py:566`). `[PROVISIONAL AD016]`
- Q: FR-024 warns when a destination identifier attribute is not unique-constrained — is that the
  observable convergence actually rides on? → A: No. Convergence rides on Infrahub's
  `human_friendly_id`: the upsert mutation is keyed on `data["id"]` if set, else `data["hfid"]`
  (`infrahub_sdk/node/node.py:295-298`), and `get_human_friendly_id` returns `None` when the
  destination schema declares no `human_friendly_id` or any component is missing
  (`infrahub_sdk/node/node.py:128-138`) — in which case the upsert is unkeyed and duplicates. FR-024
  is therefore restated in those terms: warn at plan time when the destination kind declares no
  `human_friendly_id`, or when the plan's destination identity does not supply every HFID component.
  It stays a **warning**, not a failure, because the brief says "Detect and report it — at plan time,
  as a warning". Detection is feasible: the adapter already caches the whole destination schema
  (`adapters/infrahub.py:345`) and `human_friendly_id` is a field on it
  (`infrahub_sdk/schema/main.py:272`), though nothing in `infrahub_sync/` reads it today.
  `[PROVISIONAL AD017]`
- Q: What marks a value secret, and what does redaction do? → A: The artifact carries mapped source
  field values only. Credentials live in the configuration's `settings` and are never written to the
  artifact or to review output. No field-level secret-classification model with omit, mask, or refuse
  behavior is built — that would be new user-visible behavior, a new configuration surface, and a new
  failure mode, none of which the brief's In-scope list authorizes. DBA-010's canary is injected as a
  credential in `settings`, which is where secrets actually enter this system. `[PROVISIONAL AD018]`
- Q: FR-008 forbids a new CLI command; the brief forbids only a new command group. Which binds? →
  A: The brief's bar: no new command **group**. DBR-020, DBA-012, D026 and D002 all forbid a group,
  and the brief's Constraints leave the carrier free. A specification must not assert a constraint
  stricter than the brief, even a conservative one, because it would make a future in-scope choice
  look like a violation. AD005 nonetheless extends `diff` by choice, so in fact no command is added
  either. The distinction is live because the CLI has no groups at all today (`cli.py:31`, a single
  flat `typer.Typer()` with no `add_typer` anywhere in the package), so the group bar is trivially met
  and the command bar is the only one doing work. `[PROVISIONAL AD019]`
- Q: What must per-object review output show? → A: Per operation, at least the operation identifier,
  the action, the destination kind, and the destination identity. Without this, SC-005's comparison of
  "the identifiers shown at review" against the apply result has no review-side source.
  `[PROVISIONAL AD020]`
- Q: `diff` already has a `--run-id` meaning "re-use a specific cache run id", an unknown run id
  silently creates the directory, and `diff` takes an exclusive lock. How does review mode behave? →
  A: In `--from-plan` mode, `--run-id` selects the stored run to read, MUST NOT create a run
  directory, and an unknown or plan-less run id is an error naming the run identifier and the expected
  artifact path. The mode constructs no adapter, extracts nothing, and takes no pipeline lock. The
  existing live-path meaning of `--run-id` (`cli.py:98`) is unchanged. Without this, reviewing a
  typo'd run id renders as a valid zero-operation plan — the most dangerous possible output for a
  review-before-write feature, since an empty plan is also a legitimate artifact — because
  `get_potenda_from_instance` creates the run directory with `mkdir(parents=True, exist_ok=True)`
  before any check (`utils.py:244-246`) and then writes `schema-sub-hash.txt` into it
  (`utils.py:256-263`). Not taking the lock is what makes review usable while a sync is running
  (`cli.py:129`, `cache/locks.py:21-33`, 60-second timeout) and is safe because the mode only reads.
  `[PROVISIONAL AD021]`
- Q: Is FR-014's tier guarantee true unconditionally? → A: No — it is qualified to references present
  in the computed dependency graph. Self-references are excluded from write-order edges
  (`dependency_graph.py:33-34`), optional edges are dropped to break cycles
  (`dependency_graph.py:81-98`), and a configuration supplying an explicit `order:` yields no tiers at
  all (`infrahub_sync/__init__.py:132-133`). In those three cases the peer may be unresolved at apply,
  which AD016's zero-match arm then refuses rather than silently skipping. The qualification is safe
  precisely because the miss is loud; an absolute guarantee the graph machinery falsifies would remove
  the reason to implement AD016's refusal at all. `[PROVISIONAL AD022]`
- Q: AD005 sends review output to stdout. Does the existing live `diff` output move too? → A: No.
  Only the new `--from-plan` mode writes to stdout; the existing live `diff` output keeps using the
  logger, unchanged (`cli.py:153`). Moving existing output is a user-visible change to an existing
  path that the brief does not authorize, and DBA-010's scan only needs the new outputs capturable.
  `[PROVISIONAL AD023]`

### Session 2026-07-26 — remediation follow-up

One residue surfaced while the decisions above were being applied and was resolved separately.

- Q: FR-015 derives deletes from "the destination-only identities in the loaded destination state",
  which presumes a complete destination enumeration. The engine has a warm path that does not provide
  one. What happens then? → A: Deletes are derived only when the **destination side** ran a full
  extract. When it did not, no delete operations are derived, and the manifest records that deletes
  were not computed for this plan, so the omission is explicit and reviewable rather than silent.
  The problem is real: `load_one_side` takes the incremental path whenever `should_use_incremental`
  allows (`potenda/__init__.py:189-200`, `cache/incremental.py:51`), in which case
  `hydrate_from_parquet` replays the **prior** run's snapshot, skipping tombstoned rows
  (`cache/incremental.py:135-170`, tombstone skip at `:164-165`), and only rows from
  `list_changed_since(resource, cursor)` are added on top (`potenda/__init__.py:227-228`). So the
  destination store is prior snapshot plus changed-since, not a live full read, and an object deleted
  at the destination out-of-band since the prior run remains in it — making a destination-minus-source
  difference over-inclusive. That phantom delete matters more than ordinary staleness because FR-017
  and SC-007 turn any delete in a plan into a **failed** apply, so a stale cache would become a
  spurious operator-facing run failure in the one feature whose purpose is trustworthy
  review-before-write. Recording the omission is what keeps FR-017's "never silently skipped"
  contract true: nothing is dropped quietly, because the manifest says deletes were not computed.
  Implementation note: the signal exists but is not per-side today —
  `self._did_full_extract = self._did_full_extract or (not use_inc)` OR-accumulates across both sides
  (`potenda/__init__.py:200`, consumed at `:430`), deliberately so per the comment at `:197-199`, so
  it cannot answer "did the destination run a full extract". The requirement is stated in terms of the
  destination side's extraction completeness rather than in terms of that flag.
  `[PROVISIONAL AD024]`

### Session 2026-07-26 — checklist evaluation, round two

Twelve further areas were resolved after an independent verification pass worked 168 checklist items
against this specification and the repository. As before, each resolution takes the reading that does
**not** expand scope; where an item asked for an obligation the brief does not carry, the resolution
records the exclusion explicitly rather than building the capability. All twelve are **provisional**
on the same basis as AD001–AD024.

- Q: What happens to a reviewed `update` whose destination object was deleted out-of-band between
  plan and apply, given destination freshness checks are out of scope? → A: It materializes as a
  create. This is a direct consequence of the write path FR-013 mandates: the human-friendly-ID-keyed
  convergent upsert creates when no destination object matches the key. No conflict detection, no
  destination freshness check, and no refusal path is built, because the brief places destination
  freshness checks and conflict policies out of scope. The operation is still reported under its
  original operation identifier and its original action, so the review-to-apply identifier link
  (SC-005) is unaffected. `[PROVISIONAL AD025]`
- Q: AD021 forbade creating a run directory for an unknown run identifier on the review path. What
  does an **apply** naming a run identifier with no plan artifact do? → A: The same thing. An apply
  naming a run identifier that does not exist, or whose run holds no plan artifact, is an error
  naming the run identifier and the expected artifact path, and MUST NOT create a run directory.
  Today the run directory is created unconditionally before any check and a schema sub-hash file is
  written into it, which would turn a typo'd apply into a fresh, empty, plan-less run directory.
  `[PROVISIONAL AD026]`
- Q: What happens when the destination rejects an individual operation, or the connection to it
  fails partway through an apply? → A: Fail fast. The apply stops at the first operation the
  destination rejects or that fails in transport; the operations already reported applied stay
  recorded per FR-020; the run is recorded `failed`; and the failure names the failing operation
  identifier and the underlying error. The apply does not continue past a failure and does not roll
  back. This is FR-025's partial-apply path with an explicit trigger, and it follows DBR-016's rule
  that a divergence between the reviewed set and the applied set is never silent. `[PROVISIONAL AD027]`
- Q: Is a manifest format version required, and what does a reader do with a version it does not
  recognize or with fields it does not know? → A: A `format_version` field is required. A manifest
  whose `format_version` is unrecognized is refused with a message naming the version found and the
  versions supported — a message distinct from the v1 rejection message, because the two conditions
  have different operator remedies. Unknown *additional* manifest fields are tolerated on read and
  preserved for checksum purposes, because a later outcome adds a schema-fingerprint field to this
  same manifest and a strict reader would break the moment it does. The complete field set is
  carried in one normative requirement rather than assembled from prose, because it is the contract
  nine later outcomes consume. `[PROVISIONAL AD028]`
- Q: DBR-020 requires review "through the in-process API", but which surface is that? → A: One
  supported entry point reads a stored plan and produces both review depths. It returns data rather
  than writing to a stream, and the command-line renderer is a thin layer over it, so both of
  SC-009's reachability paths exercise one code path. No broader public API is designed here; a
  single reader entry point is named and nothing else. `[PROVISIONAL AD029]`
- Q: Several checklist items ask for retention, pagination, performance targets, an output-stability
  contract, and a format-change governance policy. Are any of them in scope? → A: None. Each is
  recorded as an explicit Out-of-scope line so the requirements are complete about the exclusion
  rather than silent on it. The brief's Out-of-scope and Constraints text supports each exclusion,
  and each is cited at the exclusion. `[PROVISIONAL AD030]`
- Q: May a plan that would fail apply verification be reviewed? → A: Yes. Review verifies the plan
  checksum and reports the result prominently in its output, but does not refuse to render. Refusing
  to *show* an operator a suspect plan is worse than showing it with a clear warning, and review
  performs no writes, so nothing rides on the verdict. Review never mutates the run state.
  `[PROVISIONAL AD031]`
- Q: The constitution forbids `print`. FR-008 requires review output on standard output. How? → A:
  `typer.echo`. The repository's no-`print` rule bans the builtin `print` specifically — its
  enforcement test matches a call whose function name is `print` — and the CLI already uses
  `typer.echo` for help output. Naming the mechanism is what makes the logging rule and FR-008's
  stdout requirement visibly reconciled rather than apparently in conflict. `[PROVISIONAL AD032]`
- Q: May a run already at `status: applied` be applied again, and is verification skipped for an
  empty plan? → A: Yes to the first; no to the second. SC-002 and User Story 3 both presume a second
  apply of an already-applied run, so it is permitted. Pre-apply verification runs unconditionally on
  every apply attempt regardless of the operation count, so an empty plan with a broken checksum is
  still refused. A refusal is not terminal for the run identifier: the same run may be applied again
  once the cause is corrected. `[PROVISIONAL AD033]`
- Q: Does "before any destination write" also order verification before adapter construction? → A:
  No. All pre-apply verification completes before any destination **write**. Constructing an adapter
  or opening a destination connection is permitted before verification, which is what the code does
  today: adapters are built while the engine handle is assembled, before the apply command reaches
  its own checks. No refactor to verify before adapter construction is required. `[PROVISIONAL AD034]`
- Q: Which reproducibility details are still unstated? → A: Eight, closed together: the three
  checksum-excluded manifest fields are **removed** before canonicalization rather than blanked; the
  canonical manifest bytes and the operations bytes are concatenated with no separator; SC-006's
  masking is key removal, applied to the same two fields on both sides of the comparison before the
  byte comparison; "destination identity" has one canonical representation everywhere it appears — an
  ordered mapping of identity attribute name to value, key-sorted, which is what AD002 hashes and
  AD003 orders by; each per-operation field carries a stated obligation level and an absent-versus-
  empty rule; "the required source values as a full payload" is authoritative for the mapped fields
  it carries and silent about the rest, consistent with AD015; the configuration-version value's
  character domain is a non-empty printable-ASCII string; and the default checksum rule covers the
  declared content of the configuration the run used, as parsed, not the file's bytes.
  `[PROVISIONAL AD035]`
- Q: Twelve smaller items remain — ordering, message content, negative caching, `sync`-mode deletes,
  filter misses, and similar. → A: Each is closed at its own requirement rather than through a new
  cross-cutting rule, because each is a local statement the surrounding requirement already almost
  makes. The set is: the pre-apply checks are evaluated in a stated order and **all** failures are
  named; a refusal message names the failed check, the expected and found values where they are not
  secret, and the operator's next action; a refusal records an empty applied-operation set; FR-025's
  last-applied pointer is the final element of FR-020's ordered set rather than a separate field;
  SC-004 enumerates "absent, truncated, or mismatched" so the *absent* snapshot User Story 2 names is
  covered; the FR-024 warning is emitted only and is not a manifest field, so it stays outside
  `plan_checksum` and SC-006; a failed lookup or failed write is never cached by the peer-resolution
  memo; a `sync`-mode run records deletes in its plan exactly as a `plan`-mode run does, and its
  write path still cannot delete because AD004 makes that structural; FR-013 is verified through
  SC-002 and SC-008; a planned create whose destination identity already exists converges through
  the upsert, with conflict policy out of scope; a `--kind` filter matching no operation or naming a
  kind absent from the configuration is an error naming it rather than empty output; `--from-plan`
  without a run identifier is an error naming the required option; FR-018's "any review output" and
  SC-010's enumeration name the same surfaces; an unreadable run directory is an error naming the
  path; SC-012's before-and-after command listing is captured as `--help` output to a file; and the
  new review flags carry a documentation obligation. `[PROVISIONAL AD036]`

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
   is refused before any destination write, naming which verification failed, and the run is
   recorded `failed` rather than reaching `status: applied`. `[PROVISIONAL AD010]`
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
6. **Given** a plan directory copied out of one run and into another run's directory, **When** an
   apply is requested for the receiving run, **Then** the apply is refused because the manifest's
   recorded run identifier does not equal the run being applied, and no destination write occurs.
   `[PROVISIONAL AD012]`

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
  disagrees with the count the manifest records is torn. A source snapshot file the manifest
  records but which is absent, or whose recomputed digest or row count disagrees with the recorded
  value, is likewise torn. A `plan/` directory present without a complete manifest is torn, not a
  v1 plan. `[PROVISIONAL AD008]` `[PROVISIONAL AD014]`
- **Identifier collision.** Two operations must never share an operation identifier within one
  plan. Because the action vocabulary is closed to `create | update | delete` and relationship
  changes travel as references on the owning create or update, exactly one operation exists per
  (action, kind, destination identity), so a collision genuinely means two operations address the
  same object with the same action — a pathological plan. The plan run fails rather than emitting a
  plan whose identifiers do not address one operation each. `[PROVISIONAL AD009]`
- **Destination kind with no usable convergence key.** Convergence rides on the destination kind's
  human-friendly ID: the upsert is keyed on it, and an absent or incomplete human-friendly ID makes
  the upsert unkeyed, which produces duplicates. This is detected and reported at plan time, as a
  warning naming the affected kind and the missing component. Documenting it as a precondition is
  not sufficient, because the failure is silent data duplication. `[PROVISIONAL AD017]`
- **Relationship peer that does not resolve at apply.** A planned relationship reference whose peer
  matches no destination object refuses that operation and fails the run, naming the peer kind, the
  peer identity, and the referring operation identifier. A reference whose peer identity matches
  more than one destination object refuses, naming the peer kind, the peer identity, and the match
  count. Neither case is ever a silent skip. `[PROVISIONAL AD016]`
- **Partial apply.** If apply stops partway — meaning it terminates in-process with a reported
  error — the operations already written stay written and the run records, best effort, the last
  operation it reported as applied. The record is explicitly not required to survive abnormal
  process termination. Durable crash-surviving progress and resumption are out of scope.
  `[PROVISIONAL AD011]`
- **Destination side loaded incrementally.** The delete derivation is a set difference needing a
  complete destination enumeration, which the engine's incremental path does not provide: it replays
  the prior run's snapshot plus changed-since rows, so an object deleted at the destination
  out-of-band since that run is still present and would yield a phantom delete — which FR-017 and
  SC-007 would turn into a spurious failed apply. When the destination side did not run a full
  extract, no delete operations are derived and the manifest records that deletes were not computed,
  so the omission is disclosed rather than silent. `[PROVISIONAL AD024]`
- **Recorded deletes change the plan artifact's content.** Because deletes are suppressed from the
  plan today, recording them makes previously hidden operations appear in the plan artifact and in
  anything that renders that artifact. The existing live comparison rendering is unchanged, because
  deletes never enter the comparison result. Affected test fixtures and documentation are updated
  in the same change. `[PROVISIONAL AD004]` `[PROVISIONAL AD023]`
- **A reviewed update whose target has vanished.** The destination object a planned `update` names may
  have been deleted out-of-band between plan and apply. Because planned creates and updates both route
  through the convergent upsert, which creates when no destination object matches the convergence key,
  the operation materializes as a create. This is a consequence of the mandated convergent write path,
  not a separate behavior: no conflict detection is built, because destination freshness checks and
  conflict policies are out of scope. The operation is reported under its original operation identifier
  and its original action. `[PROVISIONAL AD025]`
- **A planned create whose destination identity already exists.** It converges onto the existing
  object through the same upsert rather than producing a duplicate or a second object. Whether the
  existing object's payload differs is not examined and no conflict is raised, because conflict
  policies are out of scope. `[PROVISIONAL AD025]`
- **An apply naming a run identifier with no plan artifact.** An apply naming a run identifier that
  does not exist, or whose run holds no plan artifact, is an error naming the run identifier and the
  expected artifact path, and creates no run directory. It is never presented as a plan with zero
  operations. `[PROVISIONAL AD026]`
- **A manifest declaring an unrecognized format version.** Refused, with a message naming the version
  found and the versions supported. That message is distinct from the v1 rejection message, because
  the operator's remedy differs: a v1 plan is re-planned, while an unrecognized newer version means
  the artifact was written by a different version of the tool. `[PROVISIONAL AD028]`
- **The destination rejects an operation, or the connection to it fails.** The apply stops at the
  first operation the destination rejects or that fails in transport. Operations already reported
  applied stay recorded, the run is recorded `failed`, and the failure names the failing operation
  identifier and the underlying error. The apply does not continue past the failure and does not roll
  back. `[PROVISIONAL AD027]`
- **Re-applying a run already at `status: applied`.** Permitted. Verification runs unconditionally on
  every apply attempt, whatever the operation count, so an empty plan with a broken checksum is still
  refused. A refusal is not terminal for the run identifier: the same run may be applied again once
  the cause is corrected. `[PROVISIONAL AD033]`
- **A plan that would fail apply verification, reviewed rather than applied.** Review verifies the
  plan checksum and reports the result prominently, but renders the plan regardless. Refusing to show
  an operator a suspect plan is worse than showing it with a clear warning, and review performs no
  writes. Review never mutates the run state. `[PROVISIONAL AD031]`
- **An operation whose destination identity is absent or empty.** The identity is derived from the
  configuration's identity attribute mapping; an operation for which no identity value can be formed
  fails the plan run, naming the kind and the identity attribute that had no value, rather than
  emitting an operation whose derived identifier does not address a destination object.
  `[PROVISIONAL AD035]`
- **A run directory that cannot be read.** A review or apply that cannot read the run directory or a
  file inside it — permission denied or an I/O failure — is an error naming the path that could not be
  read. It is never presented as an absent plan, a v1 plan, or a plan with zero operations.
  `[PROVISIONAL AD036]`
- **A kind filter that matches nothing.** A per-object review narrowed to a destination kind for
  which the plan holds no operation, or to a kind the configuration does not declare, is an error
  naming that kind. It is never empty output, for the same reason a mistyped run identifier is not an
  empty plan. `[PROVISIONAL AD036]`

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: A plan run MUST produce and save a plan artifact containing every proposed create,
  update, delete, and relationship change before anything is written to the destination.
  *(DBR-001)*
- **FR-002**: The plan artifact format MUST be defined, and MUST carry, per operation: a stable
  operation identifier, the action, the destination kind, destination identity, the required
  source values as a full payload, relationship references, and a dependency tier. The action MUST
  be exactly one of `create`, `update`, or `delete`; the vocabulary is closed. A relationship change
  MUST be carried as relationship references on the create or update operation for the owning
  object, never as a separate operation or a fourth action value. Each relationship reference MUST
  identify its peer by kind and identity values, never by a destination-assigned identifier. Each of
  those fields carries the obligation level and the absent-versus-empty rule FR-028 states, and
  "destination identity" and "the required source values as a full payload" carry the single
  representation and the single authority FR-028 fixes.
  *(DBR-008, DBR-011; encoding and layout per AD001, reference shape per AD003, closed action
  vocabulary per AD009, field obligations per AD035)* `[PROVISIONAL AD009]` `[PROVISIONAL AD035]`
- **FR-003**: Every operation MUST carry a stable identifier that links review, application,
  audit, and recovery records for that operation. The identifier MUST be derived from the
  operation's action, destination kind, and destination identity — never randomly generated and
  never derived from the operation's position in the plan — so that re-planning identical input
  reproduces it and a plan that gains or loses an operation does not renumber the others.
  *(DBR-005; derivation per AD002)*
- **FR-004**: The artifact MUST include a plan manifest binding it to its run, its configuration
  version, and the source snapshot it was planned against, with a single deterministic checksum
  over the manifest and the ordered operations. The checksum MUST exclude only the checksum field
  itself, the run identifier, and the creation timestamp. Those three exclusions and SC-006's two
  masked fields are deliberately not the same set: SC-006 needs no mask for the checksum field,
  because the checksum is a function of the checksummed bytes alone and is therefore already
  byte-identical whenever those bytes are. The source-snapshot binding MUST be a manifest field
  recording, per source-snapshot file the plan was computed against, that file's run-relative path,
  a SHA-256 digest of its content, and its row count; the binding matches when every recorded path
  exists and its recomputed digest and row count equal the recorded values. The manifest MUST also
  carry a field recording whether delete operations were computed for this plan, per FR-015.
  *(DBR-006, DBR-008, DBR-015; computation per AD001, snapshot binding per AD008, delete-computation
  disclosure per AD024)* `[PROVISIONAL AD008]` `[PROVISIONAL AD024]`
- **FR-005**: The manifest and the ordered operations MUST be serialized deterministically — a
  fixed key order, no insignificant whitespace, and a fixed ordering of the operations sequence — so
  the checksum is stable across re-serialization of identical content. Canonical ordering applies to
  the operations sequence and to relationship-reference lists only. Collections whose order is part
  of the value — a payload's list-valued attributes — MUST be serialized in source order and MUST NOT
  be re-sorted, because sorting them would make the applied value differ from the reviewed source
  value. The remaining canonicalization details are fixed elsewhere: how the checksum-excluded fields
  are removed and how the two byte sequences are joined by FR-027, and the single canonical
  representation of destination identity by FR-028. *(DBR-014; encoding per AD001, reference-list
  ordering per AD003, remaining determinism details per AD035)* `[PROVISIONAL AD035]`
- **FR-006**: A saved plan MUST be reviewable at two depths: a summary giving a count per action
  and a count per kind, and per-object detail for the operations it contains. Per-object detail MUST
  present, per operation, at least its operation identifier, its action, its destination kind, and
  its destination identity, and MUST be narrowable to a single destination kind. A narrowing that
  names a destination kind for which the plan holds no operation, or a kind the configuration does not
  declare, MUST be an error naming that kind and MUST NOT be presented as empty detail. *(DBR-002,
  DBR-005; minimum field set per AD020, filter-miss behavior per AD036)* `[PROVISIONAL AD020]`
  `[PROVISIONAL AD036]`
- **FR-007**: The plan MUST be readable from the stored artifact at any time after the run,
  including after the process that produced it has exited. *(DBR-012)*
- **FR-008**: Both review depths MUST be reachable in-process and by extending CLI commands that
  already exist. No new CLI command **group** is introduced — that is the brief's bar, and this
  specification MUST NOT assert a stricter one. AD005 additionally chooses to extend the existing
  non-mutating command rather than add a sibling command, so in fact no command is added either.
  Review MUST be carried by that existing non-mutating command, MUST NOT construct an adapter or
  extract either side, MUST NOT take the pipeline lock, and MUST NOT create or modify anything in the
  run directory — including the run state, which review MUST NOT mutate. In the read-from-artifact
  mode, review output MUST be written to standard output so it can be captured and scanned, emitted
  through the command framework's echo facility rather than the language's built-in print, which is
  what reconciles this requirement with the project's structured-logging standard; the existing live
  comparison path's output channel is unchanged. Review remains configuration-bound, because a stored
  run is located only by sync name and run identifier under the cache root. The run identifier selects
  the stored run to read; an unknown run identifier, or one whose run holds no plan artifact, MUST be
  an error naming the run identifier and the expected artifact path, and MUST NOT be presented as a
  plan with zero operations. Requesting the read-from-artifact mode with **no** run identifier MUST be
  an error naming the required option. A run directory or artifact file that cannot be read — a
  permission or I/O failure — MUST be an error naming the path that could not be read. Because these
  review flags are a user-visible CLI change, the same change MUST update the user documentation for
  the command they extend. The in-process reader MUST be the single implementation, with the command a
  thin renderer over it; FR-029 fixes that reader's contract. *(DBR-020; command and flag spelling per
  AD005, group-only bar per AD019, run-identifier and lock behavior per AD021, output channel per
  AD023, echo mechanism per AD032, missing-option, unreadable-path and documentation obligations per
  AD036)* `[PROVISIONAL AD019]` `[PROVISIONAL AD021]` `[PROVISIONAL AD023]` `[PROVISIONAL AD032]`
  `[PROVISIONAL AD036]`
- **FR-009**: Before any destination write, an apply MUST verify five things, in this order: that the
  manifest's declared format version is recognized (FR-027), that the manifest's recorded run
  identifier equals the run being applied, that the plan checksum still matches, that the source-
  snapshot binding still matches, and that the configuration version still matches. The order runs
  from the cheapest and most structural check to the most contingent, so an operator is told the
  artifact is the wrong artifact before being told its contents disagree. **All** checks MUST be
  evaluated and **every** failure MUST be named, not only the first, so one apply attempt tells the
  operator everything that is wrong. The run-identifier check MUST be a separate equality comparison
  rather than a checksum input, because SC-006 requires the run identifier to be excluded from the
  checksum — which is what would otherwise let a plan directory copied into a different run verify
  clean. A plan that fails any of these MUST be refused. Each refusal message MUST name the run
  identifier it refused, the failed check, the expected and the found value where neither is secret,
  and the operator's next action — the run identifier because a refusal that names only the check
  leaves an operator applying several runs unable to tell which one was refused.
  Verification MUST complete before any destination **write**; it does not order verification before
  adapter construction or before a destination connection is opened, which are permitted beforehand.
  FR-023's write-surface check is part of the same pre-write gate: it is evaluated with these five and
  refuses before any write is attempted, rather than surfacing as a later per-operation failure. A
  refused apply MUST record the run as `failed` in the existing run-state vocabulary
  `pending | running | dry-run | applied | failed`; "an applied state" means `status: applied`, and a
  refusal MUST NOT leave the run in `running`. A refused apply MUST record an **empty** applied-
  operation set on the run result under FR-020, rather than recording nothing, so a refusal and an
  apply that wrote nothing are not distinguishable only by an absent field. The pre-existing
  schema-subhash refusal path, which today aborts leaving `status: running` on disk after the run
  sidecar was already written `running`, MUST record `failed` too. *(DBR-003, DBR-006; run-identifier
  check per AD012, run state per AD010, format-version check per AD028, order and full-failure
  reporting, message content and empty applied set per AD036, write-ordering scope per AD034)*
  `[PROVISIONAL AD010]` `[PROVISIONAL AD012]` `[PROVISIONAL AD028]` `[PROVISIONAL AD034]`
  `[PROVISIONAL AD036]`
- **FR-010**: The plan and its source snapshot MUST be bound so the pair cannot tear. A plan whose
  manifest exists but whose operations or source snapshot are absent or truncated MUST be refused
  on the same path as a mismatch. The manifest MUST carry an operation count so that a plan with
  no operations is distinguishable from a plan whose operations are missing, rather than the two
  presenting identically. A source snapshot file the manifest records but which is absent, or whose
  recomputed digest or row count disagrees with the recorded value, is truncated or tampered with and
  MUST be refused on that same path. *(DBR-015; count field per AD001, snapshot digest and row count
  per AD008)* `[PROVISIONAL AD008]`
- **FR-011**: The manifest's configuration-version field MUST hold a deterministic content
  checksum computed over the configuration the run used, unless the caller supplies a version
  identifier explicitly, in which case that value MUST be stored verbatim. Either way the value
  MUST be treated as opaque at apply: compared for equality and never parsed. At apply, the
  comparison value MUST be recomputed by the same default rule, unless an in-process caller supplies
  one verbatim, in which case the supplied value is compared verbatim; the CLI apply path uses the
  default rule only. No new user-facing input is introduced. *(DBR-018; apply-side supplier per
  AD013)* `[PROVISIONAL AD013]`
- **FR-012**: A saved plan MUST be applicable by run ID, executing exactly the stored operations
  in dependency order, without re-extracting either side and without recomputing the comparison.
  *(DBR-004)*
- **FR-013**: The Infrahub destination adapter MUST be able to execute a planned create or update
  convergently, so that repeating an operation does not create a second object. Planned creates and
  planned updates MUST both route through the adapter's convergent upsert, keyed on the destination
  kind's human-friendly ID, and MUST NOT route through the adapter's existing update path, which is
  keyed on a destination-assigned node identifier populated only by a destination load that FR-012
  forbids. Cardinality-many relationships MUST be written as a replace-set, which is the existing
  behavior. An update payload is authoritative for the mapped fields it carries and MUST NOT touch
  unmapped destination fields. Two consequences of that mandated write path are recorded here rather
  than left to inference. First, a planned `create` whose destination identity already exists converges
  onto the existing object rather than producing a duplicate; whether that object's payload differs is
  not examined, because conflict policies are out of scope. Second, a planned `update` whose
  destination object was deleted out-of-band between plan and apply materializes as a create, because
  the upsert creates when no destination object matches the key; no conflict detection, destination
  freshness check, or refusal path is built, because destination freshness checks are out of scope. In
  both cases the operation is reported under its original operation identifier and its original action.
  This requirement is verified through SC-002 and SC-008 rather than by a criterion of its own: SC-002
  measures convergence and SC-008 measures the relationship semantics, and between them they exercise
  every clause here. *(DBR-013, DBR-011; write path per AD015, create-on-no-match consequences per
  AD025, verification route per AD036)* `[PROVISIONAL AD015]` `[PROVISIONAL AD025]`
  `[PROVISIONAL AD036]`
- **FR-014**: Relationship peers MUST be resolvable at apply time without a loaded comparison
  store, from the peer kind and identity the plan itself carries. Resolution MUST be memoized
  within one apply, MUST take an operation's own result as the resolution for later operations
  referring to it, and MUST fall back to querying the destination for that identity on a miss. The
  memo MUST hold successful resolutions only: a failed destination lookup and a failed destination
  write MUST NOT be cached, so a later operation referring to the same peer re-attempts resolution
  rather than inheriting a negative result. The memo's lifetime is one apply and it is discarded with
  it. Dependency-tier ordering MUST guarantee a peer is written before anything referring to it, for
  references carried in the computed dependency graph. That guarantee does not extend to three cases
  the existing tier machinery cannot express: a self-reference, which is excluded from write-order
  edges; a reference reachable only through an optional edge dropped to break a cycle; and any
  reference in a configuration that supplies an explicit `order:`, which yields no tiers at all. In
  those cases the peer may be unresolved at apply, and the resolution-failure behavior below governs.
  A peer identity that matches **no** destination object MUST refuse that operation and fail the run,
  naming the peer kind, the peer identity, and the referring operation identifier. A peer identity
  that matches **more than one** destination object MUST refuse, naming the peer kind, the peer
  identity, and the match count. Neither case may be a silent skip. *(DBR-007, DBR-011; resolution
  shape per AD003, resolution failures per AD016, tier qualification per AD022, memo negative-caching
  rule per AD036)* `[PROVISIONAL AD016]` `[PROVISIONAL AD022]` `[PROVISIONAL AD036]`
- **FR-015**: Delete operations MUST be recorded in the plan, changing today's default of
  suppressing them. They MUST be derived from the destination-only identities in the loaded
  destination state and materialized only into plan records, never into the comparison result the
  write path consumes. The comparison flags a project configures MUST keep their present meaning
  for the write path and MUST NOT be loosened to make deletes visible. Deletes MUST come from that
  one source only, so no operation is recorded twice. Test fixtures and documentation affected by
  the change in plan content MUST be updated in the same change. Deletes MUST be derived only when
  the **destination side** ran a full extract, because the derivation is a set difference that
  requires a complete destination enumeration and the engine's incremental path does not provide one.
  When the destination side was loaded incrementally, no delete operation MUST be derived, and the
  manifest MUST record that deletes were not computed for this plan, so the omission is explicit and
  reviewable rather than silent — which is what keeps FR-017's "never silently skipped" contract true.
  That field is part of the canonical manifest and is therefore covered by the FR-004 checksum and not
  masked by SC-006, so comparing two plans for byte-identity requires both runs to have used the same
  extraction mode on both sides. This requirement is stated in terms of the destination side's
  extraction completeness; the engine's existing full-extract flag is accumulated across both sides
  and cannot answer it per side. No input is added for requesting that deletes be computed, and
  extraction behavior is unchanged. A run in `sync` mode — which plans and writes within one run — MUST
  record deletes in its plan exactly as a `plan`-mode run does, so the artifact a `sync` run leaves
  behind is reviewable on the same terms. Its write path still cannot delete, and not because of a
  configuration setting: deletes never enter the comparison result the write path consumes, so the
  recorded-but-not-written divergence is structural and intended in both modes. *(DBR-009; mechanism
  per AD004, extraction precondition and disclosure per AD024, `sync`-mode parity per AD036)*
  `[PROVISIONAL AD024]` `[PROVISIONAL AD036]`
- **FR-016**: A delete MUST NOT be applied to the destination by the saved-plan apply path. The
  existing write path's behavior under a project's configured comparison flags is unchanged by this
  feature. *(DBR-010)*
- **FR-017**: An unsupported operation in a plan MUST be reported at apply time and MUST fail the
  run; it MUST NOT be silently skipped. Supported operations in the same plan are still applied.
  *(DBR-016)*
- **FR-018**: No secret value MUST appear in the plan artifact or in any review output. The artifact
  carries mapped source field values only. Credentials live in the configuration's `settings` and MUST
  never be written to the artifact or to review output. No field-level secret-classification model —
  omit, mask, or refuse behavior over mapped data fields — is built here; that is scope this outcome
  does not carry. *(DBR-017; scope and mechanism per AD018)* `[PROVISIONAL AD018]`
- **FR-019**: A plan artifact in the pre-existing v1 row format MUST be detected and rejected with
  a message directing the operator to re-plan. The plan reader — review and apply alike — MUST NOT
  accept v1 rows, v1 plans MUST NOT be migrated, and no second apply path with weaker guarantees may
  be built. Detection MUST NOT depend on parsing a v1 artifact: a v1 verdict requires the absence of
  the whole new-format plan directory. A plan directory present without a complete manifest is a torn
  new-format artifact under FR-010, not a v1 plan, and MUST be refused with a message distinct from
  the v1 message. To make those cases disjoint by construction, the operations section MUST be written
  first and the manifest MUST be written last and atomically, so the manifest's presence is the commit
  point. A new plan run MAY continue to write the pre-existing plan file unchanged; the pre-existing
  file MUST be left in place rather than deleted or rewritten, and it is never read by the new reader
  and is not part of the plan artifact for FR-004, FR-018, SC-006 or SC-010. *(DBR-019; detection rule
  per AD001, write order and v1/torn disjointness per AD014)* `[PROVISIONAL AD014]`
- **FR-020**: The identifiers of operations reported as applied MUST be recorded on the run result
  as an **ordered** sequence, in the order the operations were reported applied. The ordering is what
  makes "the last operation reported as applied" well defined: FR-025's last-applied pointer is the
  final element of this sequence rather than a separate recorded field. *(scope boundary: run result
  only, not a durable ledger. Verified through SC-005, whose evidence reads the apply-side identifier
  set from this record. Ordering and the last-applied pointer per AD036.)* `[PROVISIONAL AD036]`
- **FR-021**: Two operations within one plan MUST NOT share an operation identifier. Because the
  identifier is derived rather than allocated, uniqueness MUST be asserted when the plan is written
  and MUST fail the plan run if it does not hold, rather than being assumed. Under FR-002's closed
  action vocabulary exactly one operation exists per (action, kind, destination identity), so a
  collision is always pathological. *(Carries the brief's "Identifier collision" edge case; no
  separate acceptance criterion, because the brief states no criterion for it and the obligation is
  a write-time assertion rather than an observable outcome.)* `[PROVISIONAL AD009]`
- **FR-022**: A plan with zero operations MUST be a valid artifact, and applying it MUST be a
  successful no-op. It MUST be represented as a present-but-empty operations section with a
  recorded count of zero, not as an absent one. A summary of a plan with zero operations MUST state
  that the plan contains no operations rather than producing empty output. *(Carries the brief's
  "Empty plan" edge case; its apply behavior is exercised by User Story 3 scenario 3, with no
  separate acceptance criterion because the brief states none.)*
- **FR-023**: Applying a plan against an adapter with no planned-write surface MUST fail with a
  clear, actionable error naming the adapter, before any write is attempted — the behavior the engine
  already has today. *(Carries the brief's "Missing destination write
  surface" edge case; no separate acceptance criterion, because the brief states none and the
  behavior is pre-existing.)*
- **FR-024**: When a destination kind declares no human-friendly ID, or when the plan's destination
  identity for that kind does not supply every human-friendly-ID component, the plan run MUST warn at
  plan time, naming the affected kind and the missing component. This is the observable convergence
  actually rides on: the upsert is keyed on the human-friendly ID and is unkeyed — and therefore
  duplicates — when it is absent or incomplete. The plan run MUST still succeed; this is a warning,
  not a failure, per the brief. The warning MUST be emitted on the run's **log stream**, which is
  where the plan path already emits its operational output, and not on the standard-output channel
  FR-008 reserves for read-from-artifact review output. It is emitted only: it MUST NOT be recorded as
  a manifest field, so it stays outside the FR-004 checksum and outside SC-006's byte comparison.
  *(Carries the brief's non-unique-destination-identifier edge case, restated on the real convergence
  key per AD017; emitted-only, non-manifest status and output channel per AD036; criterion SC-014.)*
  `[PROVISIONAL AD017]` `[PROVISIONAL AD036]`
- **FR-025**: If an apply stops partway — meaning it terminates in-process with a reported error —
  the operations already written MUST stay written and the run MUST record, best effort, the last
  operation it reported as applied, where "last" means last in the dependency order actually
  executed. The record is explicitly NOT required to survive abnormal process termination.
  *(scope boundary: run result only, not a durable ledger. Carries the brief's "Partial apply" edge
  case; no separate acceptance criterion, because SC-003's crash windows are measured
  destination-side and a durable per-operation record is out of scope.)* `[PROVISIONAL AD011]`
- **FR-026**: The plan format MUST express ordering only as the operation sequence and each
  operation's dependency tier, and MUST NOT record any grouping of operations into write units. This
  is a constraint on the format, carried from the brief; it is not a testable requirement about later
  work. Whether destination writes are later batched is out of scope here, and the format does not
  prescribe write granularity either way. *(Carries the brief's Constraint "The plan contract orders
  operations; it does not prescribe write granularity"; inspectable as the absence of any grouping
  field in the artifact, with no separate acceptance criterion.)*
- **FR-027**: The plan manifest MUST carry the following field set, stated here in one place because
  it is the format contract nine later outcomes consume and must be readable without being assembled
  from the requirements that each touch one part of it:
    1. A **format version**, required on every manifest, declaring which revision of this artifact
       format the manifest was written to.
    2. The **run identifier** the plan was produced under.
    3. The **creation timestamp** of the plan.
    4. The **configuration-version value** the run planned with, per FR-011.
    5. The **source-snapshot binding**, per FR-004: one record per source-snapshot file the plan was
       computed against, holding that file's run-relative path, a content digest, and its row count.
    6. The **operation count**, per FR-010, which is what keeps a plan with no operations
       distinguishable from a plan whose operations are missing.
    7. The **delete-computation record**, per FR-015, stating whether delete operations were computed
       for this plan.
    8. The **plan checksum**, per FR-004: one deterministic value over the manifest and the ordered
       operations, excluding only itself, the run identifier, and the creation timestamp. Those three
       are **removed** before the manifest is canonicalized rather than blanked, and the canonical
       manifest bytes and the operations bytes are joined with no separator between them.

  A manifest whose declared format version is not one the reader recognizes MUST be refused, with a
  message naming the version found and the versions supported. That message MUST be distinct from the
  message FR-019 requires for a plan in the pre-existing format, because the two conditions have
  different operator remedies: a pre-existing-format plan is re-planned, while an unrecognized version
  means the artifact was written by a different version of the tool. Unknown **additional** manifest
  fields MUST be tolerated on read rather than refused, and MUST be included in the bytes the checksum
  is computed over — a later outcome adds a schema-fingerprint field to this same manifest, and a
  reader that rejected fields it did not know would refuse that artifact on arrival. This requirement
  consolidates the manifest obligations FR-004, FR-010 and FR-015 state field by field; where they and
  this requirement describe the same field they are one obligation, not two. *(DBR-006, DBR-008;
  consolidated field set, format-version field and unknown-field tolerance per AD028; the individual
  field rules per AD001, AD008 and AD024; canonicalization details per AD035; criterion SC-018)*
  `[PROVISIONAL AD028]`
- **FR-028**: The per-operation record MUST fix, for each field FR-002 names, the rules that make two
  readers of the same plan agree about it:
    1. **Obligation level.** The operation identifier, the action, the destination kind, the
       destination identity, and the dependency tier are required on every operation. The payload is
       required on a create and on an update, and is omitted on a delete, which proposes no source
       values. Relationship references are optional: present when the operation carries any, absent
       when it carries none. FR-014's qualification of the tier ordering guarantee concerns what the
       tier guarantees, not whether the field is present.
    2. **Absent versus empty.** An absent field means the operation carries no value of that kind at
       all; an empty collection means the operation carries that kind of value and the value is empty
       — for a cardinality-many relationship reference, that the peer set is deliberately empty, which
       the replace-set write FR-013 mandates then acts on. The two MUST NOT be used interchangeably: a
       writer MUST NOT emit an empty collection where the field is absent, nor omit a field whose
       value is an empty collection.
    3. **One representation of destination identity.** "Destination identity" has a single canonical
       representation everywhere it appears — an ordered mapping of identity attribute name to value,
       sorted by attribute name. That one representation is what FR-003's identifier derivation
       hashes, what FR-005's canonical ordering of relationship-reference lists sorts by, and what
       per-object review presents under FR-006, so the identity an operator reads is the identity the
       operation identifier was derived from.
    4. **The authority of the payload.** "The required source values as a full payload" is
       authoritative for the mapped fields it carries and silent about every other destination field:
       applying it MUST set the fields it carries and MUST NOT touch unmapped destination fields,
       which is the same authority FR-013 states for a planned update. "Full" means complete with
       respect to the configuration's field mapping, not complete with respect to the destination
       schema.

  *(DBR-008, DBR-011, DBR-014; obligation levels, the absent-versus-empty rule, the single identity
  representation and the payload's authority per AD035; payload authority consistent with AD015;
  verified through SC-002, SC-005, SC-006 and SC-008 rather than by a criterion of its own)*
  `[PROVISIONAL AD035]`
- **FR-029**: Reading a stored plan MUST have exactly one supported entry point: an in-process plan
  reader that takes the sync name and the run identifier locating a stored run, reads that run's plan
  artifact, and produces both review depths FR-006 defines — the summary and the per-object detail.
  It MUST return that content to its caller as data rather than writing it to any output stream, so a
  caller consumes it without parsing rendered text and so SC-010's credential scan can scan the
  returned value as data. The command-line review mode MUST be a thin renderer over that same entry
  point and MUST NOT re-implement reading, filtering, or summarizing, so both of SC-009's
  reachability cases — in-process and from the command line — exercise one code path. Nothing beyond
  this single reader is specified as a supported surface: no broader programmatic interface for
  plans, runs, or applies is designed here. *(DBR-002, DBR-012, DBR-020; single reader entry point per
  AD029; verified through SC-009 and SC-010 rather than by a criterion of its own)*
  `[PROVISIONAL AD029]`

### Key Entities

- **Plan artifact**: The durable output of a plan run — a manifest plus an ordered set of planned
  operations, held together in the run's own directory, readable independently of the process that
  wrote it and readable without loading all of it at once, and versioned so a pre-existing v1 plan
  is recognizable and refusable. *(concrete layout per AD001)*
- **Plan manifest**: The artifact's header. Binds the artifact to its run identifier, the
  configuration version it ran with, and the source snapshot it was planned against; records the
  format version and the operation count; and carries the deterministic checksum over itself and
  the ordered operations. The source-snapshot binding is a per-file record of run-relative path,
  content digest, and row count. Also records whether delete operations were computed for this plan,
  which is false when the destination side was loaded incrementally. *(fields and checksum rule per
  AD001, snapshot binding per AD008, delete-computation disclosure per AD024)* `[PROVISIONAL AD008]`
  `[PROVISIONAL AD024]`
- **Planned operation**: One proposed change. Carries a stable operation identifier, the action —
  exactly one of `create`, `update`, or `delete` — the destination kind, destination identity, the
  required source values as a full payload, relationship references, and a dependency tier. A
  relationship change is not an action; it is carried as relationship references on the owning
  object's create or update operation. *(closed action vocabulary per AD009)* `[PROVISIONAL AD009]`
- **Relationship reference**: A peer named by kind and identity values rather than by any
  destination-assigned identifier, so it is resolvable at apply time and does not depend on which
  destination instance the plan is applied to. *(per AD003)*
- **Source snapshot**: The extracted source-side state the plan was computed against — one file per
  extracted resource under the run's source side — bound to the plan by the manifest's per-file path,
  digest, and row count so the pair cannot tear. *(per AD008)*
- **Run**: The unit a plan belongs to and the handle an apply is requested by. Carries the run
  state, drawn from the existing vocabulary `pending | running | dry-run | applied | failed`,
  including whether the plan reached `status: applied` and which operations were reported as applied.
  A refused apply is recorded `failed`. *(per AD010)* `[PROVISIONAL AD010]`
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
  against a live destination, for every kind for which the applied plan contains an operation.
  "The same identity" means the plan's destination-identity values for the operation, which the
  Assumptions section binds to the destination's convergence key. *(DBA-002)*
- **SC-003**: The create and update write classes, and the class of operations whose payload carries
  relationship references, all end at clean-single-run counts across apply-once, apply-twice, and both
  crash-window variants — a crash injected after the destination write commits and before the apply
  advances to the next operation, and one injected before the destination write is issued — evidenced
  by a per-class conformance matrix. "Clean-single-run counts" means the per-kind destination object
  counts and identities observed after one uninterrupted apply of the same plan against the same
  starting state. The crash windows are injection points measured destination-side; no durable
  per-operation record distinguishes them, and none is required. For the relationship class the
  measurement is SC-008's peer-set comparison rather than object counts, since an object created with
  its peers unlinked leaves counts correct and relationships wrong. Delete is excluded, because
  applying deletes is out of scope. *(DBA-003, as narrowed by the brief; third class named per AD009,
  crash-window measurement per AD011)* `[PROVISIONAL AD009]` `[PROVISIONAL AD011]`
- **SC-004**: A plan whose checksum, configuration version, or source-snapshot binding is **absent,
  truncated, or mismatched** is refused before any destination write, naming the failed check, and the
  run is recorded `failed` rather than reaching `status: applied`; a plan whose manifest exists but
  whose operations or source snapshot are absent or truncated is refused the same way — evidenced by
  six negative cases: the five the brief names (checksum mismatch, configuration-version mismatch,
  snapshot-binding mismatch, absent operations, truncated snapshot) plus an **absent source
  snapshot**, the case User Story 2 scenario 1 names and which "no longer matches" alone did not
  reach. Each case asserts refusal, zero destination writes observed as unchanged destination object
  counts, and the resulting run state read from the run sidecar. *(DBA-004; run state per AD010;
  absent-truncated-or-mismatched enumeration and the sixth case per AD036)* `[PROVISIONAL AD010]`
  `[PROVISIONAL AD036]`
- **SC-005**: The operation identifiers shown at review are the identifiers reported against each
  operation in the apply result — evidenced by a review-then-apply trace comparing both identifier
  sets per operation, with the review-side set read from per-object review output (FR-006) and the
  apply-side set read from the FR-020 record on the run result. *(DBA-005; review-side field set per
  AD020)* `[PROVISIONAL AD020]`
- **SC-006**: Re-planning an unchanged source and destination produces a byte-identical operations
  section and a byte-identical manifest, excluding the fields that necessarily vary per run (the
  run identifier and the creation timestamp) — evidenced by two consecutive plan runs **that both
  used the same extraction mode on each side** and a byte comparison with the varying fields masked.
  Fixing the extraction mode is part of the evidence procedure rather than an incidental detail: the
  manifest records whether deletes were computed, that field is inside the checksum and is not one of
  the two masked fields, and the engine may legitimately take the incremental path on a second run, so
  FR-015 makes byte-identity conditional on both runs having extracted the same way. Two runs at
  different extraction modes are expected to differ, and comparing them would make this criterion's own
  test unsound. *(DBA-006; same-extraction-mode precondition per AD024)* `[PROVISIONAL AD024]`
- **SC-007**: A plan containing a delete operation applies its non-delete operations, does not
  delete from the destination, and ends in run state `failed` naming the unsupported operation's
  identifier and action — evidenced by destination object counts before and after, scoped to the kinds
  appearing in the applied plan, the direct assertion that the object named by each delete operation is
  still present, plus the recorded run state and message. *(DBA-007; run state per AD010)*
  `[PROVISIONAL AD010]`
- **SC-008**: A relationship-bearing kind from the qualified configuration applies with no loaded
  comparison store, and the resulting relationships on the destination match those the plan
  specified — evidenced by, for each relationship the schema mapping declares for the kind under test,
  the destination's peer set read back and compared against the plan's reference list as an **unordered
  set of (peer kind, peer identity) pairs**. The no-comparison-store precondition is evidenced as
  SC-001 evidences its own: no source or destination extraction call on the apply path. *(DBA-008)*
- **SC-009**: A saved plan can be summarized by action and kind, and expanded to per-object
  detail, at any time after the run and after the originating process has exited — reachable both
  in-process and from the CLI. Evidenced by four cases: summary and per-object detail, each
  produced in-process and from the CLI, all against a stored artifact read in a new process. Each
  case passes when the summary presents a count per action and a count per kind, and the detail
  presents one record per operation carrying at least its operation identifier, action, destination
  kind, and destination identity. Every case is produced with neither source nor destination
  reachable, which evidences that no adapter is constructed. *(DBA-009; field set per AD020)*
  `[PROVISIONAL AD020]`
- **SC-010**: No secret value appears in the plan artifact, in summary output, or in per-object
  output — evidenced by a canary-credential scan over the artifact and both review outputs, with the
  canary injected as a credential in the configuration's `settings`, which is where credentials enter
  this system. The artifact files are scanned directly; the CLI outputs are scanned from captured
  standard output; the in-process reader's return value is scanned as data. *(DBA-010; injection point
  per AD018)* `[PROVISIONAL AD018]`
- **SC-011**: A v1-format plan is rejected with a message directing the operator to re-plan, and
  no destination write occurs — evidenced by an apply attempted against a v1 fixture plan,
  asserting refusal, the message, and zero writes. *(DBA-011)*
- **SC-012**: The CLI command set gains no new command group, and review is reachable through
  commands that already exist — evidenced by the top-level command listing captured before and after
  and compared as text, showing no group added, plus the SC-009 CLI cases demonstrating that both
  review depths are reachable from existing commands. *(DBA-012; group-only bar per AD019)*
  `[PROVISIONAL AD019]`
- **SC-013**: Applying a plan whose configuration-version value differs from the one recorded at
  plan time is refused without the value being parsed or interpreted, and an arbitrary opaque
  string round-trips unchanged through manifest write and apply comparison — evidenced by a
  round-trip test using a deliberately opaque value supplied verbatim by an in-process caller and
  compared verbatim at apply, plus the mismatch refusal from SC-004. *(DBA-013; apply-side supplier
  per AD013)* `[PROVISIONAL AD013]`
- **SC-014**: A plan run against a destination kind that declares no human-friendly ID, or whose
  plan identity does not supply every human-friendly-ID component, emits a warning naming that kind
  and the missing component, and the plan run still succeeds — evidenced by a plan run against such a
  kind, asserting the warning's content and the run's successful outcome. *(FR-024, per AD017; the
  brief raises this edge case from documentation to detection but states no criterion for it)*
  `[PROVISIONAL AD017]`
- **SC-015**: An apply whose manifest records a run identifier other than the run being applied is
  refused before any destination write, naming that check, and the run is recorded `failed` —
  evidenced by copying a plan directory from one run into another and asserting refusal, zero
  destination writes, and the resulting run state. *(FR-009, per AD012; beyond DBA-004's five cases,
  which do not include the run binding)* `[PROVISIONAL AD012]`
- **SC-016**: A planned relationship reference whose peer matches no destination object, and one
  whose peer identity matches more than one destination object, each refuse the operation and fail the
  run with a message naming the peer kind and the peer identity — the zero-match message also naming
  the referring operation identifier, the multi-match message also naming the match count — and
  neither is silently skipped. *(FR-014, per AD016)* `[PROVISIONAL AD016]`
- **SC-017**: A plan run whose destination side ran a full extract records delete operations and
  records in the manifest that deletes were computed; a plan run whose destination side was loaded
  incrementally records no delete operations and records in the manifest that deletes were not
  computed — evidenced by two plan runs against the same source and destination, one with a full
  destination extract and one incremental, comparing in each the presence of delete operations and
  the manifest's delete-computation field, and asserting that the incremental run's plan does not
  drive its apply into a failed state through a phantom delete. *(FR-015, per AD024)*
  `[PROVISIONAL AD024]`
- **SC-018**: An apply whose manifest declares a format version the reader does not recognize is
  refused before any destination write, with a message naming the version found and the versions
  supported, and the run is recorded `failed`; that message differs from the message a plan in the
  pre-existing format is rejected with — evidenced by an apply attempted against a fixture manifest
  carrying an unrecognized format version, asserting refusal, the message content, zero destination
  writes, and the resulting run state, and by comparing that message text against the pre-existing-
  format rejection message SC-011 asserts. *(FR-009, FR-027, per AD028; FR-009's first check had no
  criterion — SC-004 covers three of the five and SC-015 the run identifier)* `[PROVISIONAL AD028]`

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

Seven further boundaries are recorded here rather than carried from the brief, because checklist
evaluations raised each as a candidate for expansion and each was declined. Recording the exclusion
is the whole of what is done: none of the capabilities below is built here.

- **A field-level secret-classification model** over mapped data fields — a declared secret-field
  list, a name-pattern rule, or omit / mask / refuse-to-plan behavior. FR-018 is satisfied by never
  writing the configuration's `settings` credentials into the artifact or review output; classifying
  mapped data values would be new user-visible behavior, a new configuration surface, and a new
  failure mode, none of which the brief's In-scope list carries. *(per AD018)* `[PROVISIONAL AD018]`
- **Artifact retention, lifecycle, and pruning.** No expiry, age-out, quota, retention policy, or
  prune-old-plans behavior is defined for saved plans. The brief's Out-of-scope list puts durable
  run and artifact storage behind provider interfaces elsewhere — this outcome uses the per-run
  directory layout the engine already writes — and puts plan expiration out of scope alongside
  destination freshness checks and conflict policies. A stored plan therefore lives exactly as long as
  its run directory does, by whatever means the operator already manages that directory.
  *(per AD030)* `[PROVISIONAL AD030]`
- **Pagination or truncation of per-object review output.** Per-object detail is narrowable to a
  single destination kind under FR-006 and by nothing else: no page size, no record limit, no elision
  of a large result, and no continuation handle. The brief's In-scope list names summary review and
  per-object review with no volume qualifier and states no output-size obligation, so none is
  invented. *(per AD030)* `[PROVISIONAL AD030]`
- **Plan-volume and review-latency targets.** No maximum operation count, artifact size, review
  response time, or apply throughput is asserted here, and none is tested. The brief sets no volume or
  latency target. The line-oriented encoding AD001 chose is what a later target would build on,
  because it allows a large plan to be summarized and detailed without loading all of it, but no
  threshold is set. *(per AD030)* `[PROVISIONAL AD030]`
- **Rendered review output as a stability or compatibility contract.** The summary and per-object
  renderings are operator-facing text, not a format other software may depend on: their wording,
  field order, and layout may change without that being a breaking change, and nothing here obliges a
  later outcome to preserve them. What this outcome owns as a contract is the plan artifact format —
  the manifest fields, the per-operation record, the deterministic serialization, and the checksum
  rule — which is the shared contract the brief names and which nine later outcomes consume.
  Presenting plan summaries in a user interface is one of those later outcomes and owns its own
  presentation. *(per AD030)* `[PROVISIONAL AD030]`
- **A cross-outcome policy governing changes to the plan artifact format.** No versioning process,
  deprecation window, migration procedure, or change-negotiation protocol between this outcome and
  its nine consumers is defined. The brief states the consequence — any change to the format after
  this ships is a breaking change for all nine — and states no process for managing it. FR-027's
  format-version field and its tolerance of unknown fields are the two mechanisms this outcome
  provides; the governance around them is not this outcome's to write. *(per AD030)*
  `[PROVISIONAL AD030]`
- **Folding the review flags into a command group.** This outcome adds no command group (FR-008) and
  asserts nothing about a later outcome folding whichever review spelling is chosen into one. The
  brief's Constraints assign that rework to a later outcome; whether such a fold preserves behavior is
  that outcome's obligation to establish, not a property this specification requires, tests, or
  guarantees. Recording the exclusion is preferred over stating a preservation requirement, because a
  requirement here would bind work the brief has already assigned elsewhere. *(per AD019)*
  `[PROVISIONAL AD019]`

## Assumptions

- For every kind for which the plan under test contains an operation, the destination kind's
  convergence key — its human-friendly ID — covers the plan's destination identity for that kind. This
  is the correspondence SC-002's "the same identity" rides on: the upsert is keyed on the
  human-friendly ID, so if that key does not correspond to the plan's destination identity an upsert
  can converge onto a different object than the plan named, or not converge at all. If the
  correspondence does not hold, create and update produce duplicates instead of converging, which
  invalidates SC-002 — hence the plan-time warning in FR-024. *(restated on the real convergence key
  per AD017)* `[PROVISIONAL AD017]`
- Review is reachable by extending existing CLI commands, without any new command group. If
  extending existing commands proves impossible, that is a scope change requiring a new decision,
  not an implementer's call.
- The qualified path is NetBox → Infrahub using `examples/netbox_to_infrahub/config.yml`.
- The run-mode vocabulary (`plan`, `sync`, `apply`) is fixed naming, not a build dependency.
- The existing engine and per-run artifact layout are present: a saved plan is already read and
  dispatched per row to the destination's planned-write method, and per-side snapshots and run
  sidecars are already written. The planned-write surface currently has no implementation on any
  adapter, and today's plan rows are lossy.
- The Infrahub destination adapter's **create** path is identifier-keyed and convergent, via the
  destination kind's human-friendly ID (`client.create(...)` then `save(allow_upsert=True)`,
  `adapters/infrahub.py:611-612`). Its existing **update** path is not: it is keyed on a
  destination-assigned node id captured only during a destination load
  (`adapters/infrahub.py:622`, `:510`; `infrahub_sync/__init__.py:232`) and is therefore unusable from
  a saved plan, which performs no destination load. FR-013 accordingly routes planned creates **and**
  planned updates through the upsert path rather than inventing a new one. If that path turns out not
  to converge for a planned update, SC-002 and SC-003 fail and the write surface needs a decision this
  specification does not carry. *(corrected per AD015)* `[PROVISIONAL AD015]`
- The engine computes kind-level dependency tiers from `schema_mapping[].fields[].reference`
  (`dependency_graph.py:25-36`), and this outcome derives each operation's tier from them. Tiers are
  absent entirely when a configuration declares an explicit `order:`
  (`infrahub_sync/__init__.py:132-133`), and the graph excludes self-edges and drops optional edges to
  break cycles — which is why FR-014's tier guarantee is qualified. On the qualified configuration the
  computation yields six tiers with no dropped edges and no active self-references, and the
  configuration contains relationship-bearing kinds of both cardinalities, which is what SC-008 needs.
  *(per AD022)* `[PROVISIONAL AD022]`
- Secrets enter this system as credentials in the configuration's `settings`, not as mapped source
  data. FR-018 is defended by never writing `settings` values into the artifact or review output, and
  SC-010's canary is injected as a credential in `settings` accordingly. Source record data on the
  qualified path is assumed to carry no credential values; if that assumption fails, handling it would
  require the field-level classification model AD018 places out of scope. *(per AD018)*
  `[PROVISIONAL AD018]`
- The configuration-version value is consumed as an opaque input. Before a version registry
  exists, a checksum over the configuration's declared content satisfies the binding. At apply the
  comparison value is recomputed by the same rule, so the rule must be stable for an unchanged
  configuration; a benign reformat of the configuration that the rule is sensitive to invalidates
  saved plans and requires a re-plan. *(per AD013)* `[PROVISIONAL AD013]`
- Which existing commands carry review, and their exact flag spelling, is an implementation choice
  within one fixed constraint: no new top-level command group. That choice is now recorded as
  AD005 rather than left open.
- SC-010's scan is performed over the artifact files and over the CLI's captured standard output.
  The in-process reader returns data rather than writing to a stream, and is scanned as data.

## Dependencies

- No in-batch dependencies. This outcome can be completed independently.
- **The existing non-mutating command this outcome extends.** Four properties of it are load-bearing
  for FR-008 and are recorded here rather than presumed:
    1. `--run-id` already exists on that command, meaning "Re-use a specific cache run id" for a live
       comparison (`cli.py:98`). The read-from-artifact mode gives the same option a select-the-stored-run
       meaning, mode-switched by the new flag; the live-path meaning is unchanged.
    2. Its plan output is emitted through the logger today (`cli.py:153`), which is why FR-008's stdout
       clause is scoped to the read-from-artifact mode only and the live path's channel is unchanged.
    3. It requires a sync name or configuration file, and review still needs one, because a stored run
       is located only as `cache_root_for(<sync name>)/<run_id>` (`cache/paths.py:56-59`) — so review is
       adapter-free but remains configuration-bound.
    4. It wraps its whole body in an exclusive per-pipeline file lock with a 60-second timeout
       (`cli.py:129`, `cache/locks.py:21-33`). FR-008 exempts the read-only review path from that lock,
       so review of a stored plan neither blocks nor is blocked by a running sync.

  Today the run directory is also created before any check
  (`utils.py:244-246`, `mkdir(parents=True, exist_ok=True)`, followed by `schema-sub-hash.txt` at
  `utils.py:256-263`); FR-008 forbids that on the review path. *(per AD021)* `[PROVISIONAL AD021]`
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
| DBR-002 | FR-006, FR-029; User Story 1 scenario 2 |
| DBR-003 | FR-009; User Story 2 |
| DBR-004 | FR-012; User Story 1 scenario 1 |
| DBR-005 | FR-003, FR-006, FR-021; SC-005 |
| DBR-006 | FR-004, FR-009, FR-027; User Story 2 scenario 1 |
| DBR-007 | FR-014; User Story 5 |
| DBR-008 | FR-002, FR-004, FR-027, FR-028; Key Entities |
| DBR-009 | FR-015; SC-017; User Story 4 |
| DBR-010 | FR-016; User Story 4 |
| DBR-011 | FR-002, FR-013, FR-014, FR-028 |
| DBR-012 | FR-007, FR-029; User Story 1 scenario 2 |
| DBR-013 | FR-013; User Story 3 |
| DBR-014 | FR-005, FR-028; SC-006 |
| DBR-015 | FR-004, FR-010; User Story 2 scenario 3; Edge Cases (Torn artifact) |
| DBR-016 | FR-017; User Story 4 |
| DBR-017 | FR-018; SC-010 |
| DBR-018 | FR-011; Key Entities (configuration-version value); SC-013 |
| DBR-019 | FR-019; User Story 2 scenario 4 |
| DBR-020 | FR-008, FR-029; User Story 1 scenario 3 |
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

The brief's edge cases and constraints do not carry DBR or DBA identifiers, so they are traced
separately. Every requirement in this specification appears in one of the two tables.

| Brief edge case or constraint | Carried by |
|---|---|
| Edge case: Missing destination write surface | FR-023; Edge Cases (Missing destination write surface) |
| Edge case: Empty plan | FR-022; User Story 3 scenario 3; Edge Cases (Empty plan) |
| Edge case: Torn artifact | FR-004, FR-010, FR-019; SC-004; Edge Cases (Torn artifact) |
| Edge case: Identifier collision | FR-002, FR-021; Edge Cases (Identifier collision) |
| Edge case: Non-unique destination identifier | FR-024; SC-014; Edge Cases (Destination kind with no usable convergence key) |
| Edge case: Partial apply | FR-020, FR-025; SC-003; Edge Cases (Partial apply) |
| Constraint: the plan contract orders operations without prescribing write granularity | FR-026 |
| Constraint: no v1 compatibility; detect and reject | FR-019; SC-011 |
| Constraint: recording deletes changes existing plan content | FR-015; Edge Cases (Recorded deletes change the plan artifact's content) |
| Derived from DBR-009 and DBR-016: the delete derivation's extraction precondition | FR-004, FR-015; SC-017; Edge Cases (Destination side loaded incrementally) |
| Constraint: which existing commands carry review is an implementation choice, within no new command group | FR-008; SC-012; Assumptions; Dependencies |
| Constraint: the qualified path is NetBox → Infrahub | Assumptions |
| Derived from DBR-007 at apply time: peer resolution failures | FR-014; SC-016 |
| Derived from DBR-003/DBR-006: the plan's run binding | FR-009; SC-015; User Story 2 scenario 6 |
| Derived from DBR-003/DBR-006 and DBR-019: the manifest's format-version check | FR-009, FR-027; SC-018; Edge Cases (A manifest declaring an unrecognized format version) |

## Open Design Decisions

Both items previously deferred here are now answered in [Clarifications](#clarifications) and
carried into the requirements above. They are recorded as **provisional** decisions rather than
silent implementation choices, because they are design commitments other outcomes consume:

- **The plan artifact's concrete on-disk encoding** — decided as AD001. Nine later outcomes consume
  this format and any later change to it is a breaking change for all of them, so it is recorded
  explicitly and marked provisional until ratified.
- **Which existing commands carry review, and the exact flag spelling** — decided as AD005. It is
  user-visible, so it is named rather than left implicit. Whether a later outcome can fold that
  spelling into a command group without changing behavior is **not** asserted here: it is recorded as
  an explicit exclusion in [Out of Scope](#out-of-scope), because the brief assigns the command-group
  rework to a later outcome and a requirement here would bind work this specification does not own.
  *(per AD019)* `[PROVISIONAL AD019]`

Three further design commitments were surfaced during clarification and recorded the same way:
AD002 (operation-identifier derivation), AD003 (relationship-reference shape and apply-time peer
resolution), and AD004 (how deletes are recorded without changing what the write path writes).

If AD003 is not ratified, FR-002's reference shape and FR-014's resolution mechanism reopen. If
AD004 is not ratified, FR-015's derivation source and FR-016's structural boundary reopen.

A further sixteen commitments — AD008 through AD023 — were recorded after the checklist evaluation
and are carried in [Clarifications](#session-2026-07-26--checklist-evaluation). Each is marked at
every requirement that encodes it, so the revisit set for any one of them is the set of requirements
carrying its marker. None of them expands scope: each either names a representation the brief
delegates, corrects a statement this specification made about existing code, or picks the reading
that keeps the brief's own text true.

Nothing here remains open. What remains genuinely deferred is not a design commitment:

- **Plan size and review performance.** The brief sets no volume or latency target, so none is
  invented here. The encoding chosen in AD001 is line-oriented specifically so a large plan can be
  summarized and detailed without loading all of it, which is the property a later target would
  need; no threshold is asserted. The exclusion itself is recorded in
  [Out of Scope](#out-of-scope). *(per AD030)* `[PROVISIONAL AD030]`
- **How a missing destination unique constraint is detected** for the FR-024 warning. The
  requirement and the warning's content are fixed; the detection mechanism is a planning-phase
  choice with no cross-outcome contract attached.
