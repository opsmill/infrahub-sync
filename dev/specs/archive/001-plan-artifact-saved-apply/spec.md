# Feature Specification: Saved plan artifact and apply-exactly-what-was-reviewed

**Feature**: `saved-plan-artifact-and-apply`

**Created**: 2026-07-26

**Status**: Extracted

**Input**: Repository-local saved-plan requirements. This specification is the scope authority for
the archived implementation record.

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
delegates explicitly or does not reach; none changes scope. All five are **ratified**, as is every
other decision recorded in this section: the ratifying event is the delivery gate that closed this
run's critique loop, at which the decisions were confirmed and the run released to implementation.

Ratification does not discard each decision's **revisit set** — the requirements that must be
re-examined if the decision is ever reopened — because that set is what makes a reopening tractable:
AD001 → FR-002, FR-004, FR-005, FR-010, FR-019; AD002 → FR-003, FR-021; AD003 → FR-002, FR-014;
AD004 → FR-015, FR-016; AD005 → FR-008. The revisit set for each of AD008–AD088 is the requirements
whose `[ADnnn]` reference cites it. Two of the later decisions carry a wider set and are called out:
reopening **AD055** reopens FR-016, FR-017, FR-020, SC-007 and User Story 4 to the failed-run reading
and returns DBR-016 and DBA-007 to the brief's own derivation; reopening **AD056** reopens FR-006's
disclosure clause and FR-015's "explicit and reviewable" claim, and leaves AD024's justification for
omitting the delete class standing on nothing.

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
  what identifies a v1 plan, per AD014. `[AD001]`
- Q: How is an operation identifier derived? → A: `op_` followed by the first 16 hex characters of
  a SHA-256 over the canonical JSON of the triple (action, destination kind, destination identity).
  The payload is deliberately excluded, so the identifier names the logical operation and stays
  stable across re-plans; exactness of the payload is already guaranteed by `plan_checksum`. The
  identifier is derived, never random or positional, so re-planning identical input reproduces it
  byte-for-byte. Uniqueness within a plan is asserted at write time rather than assumed.
  `[AD002]`
- Q: How does a planned operation reference its relationship peers so they can be resolved at apply
  time with no comparison store loaded? → A: Each reference records the peer's kind and the peer's
  identity values — **recursively, per AD043, where an identity value is itself a reference** — never
  a destination-assigned identifier, which does not exist yet at plan time
  for peers this same plan creates and which would not survive being moved between environments.
  A cardinality-one reference is a single object; a cardinality-many reference is a list ordered
  canonically by the peer identity, so it is stable for SC-006. At apply time peers are resolved
  through a per-apply cache keyed by (kind, identity), populated as each planned create or update
  completes and, on a miss, by querying the destination for that identity and memoizing the
  result. Dependency-tier ordering guarantees a peer is written before anything referring to it.
  `[AD003]`
- Q: How are delete operations recorded in the plan without changing what the write path writes?
  → A: Delete operations are derived from the loaded destination state — the destination-only
  identities remaining after the source identities are removed — and are materialized only into
  plan records. They are never placed into the comparison result that the write path consumes, so
  a delete is structurally incapable of reaching the destination rather than merely being
  suppressed by configuration. The comparison flags configured for a project keep their present
  meaning for the write path and are not loosened. Deletes are recorded from this one source only,
  so an operation cannot be recorded twice and collide on its identifier.
  `[AD004]`
- Q: Which existing commands carry plan review, and what is the exact spelling? → A: The existing
  non-mutating `diff` command gains a read-from-artifact mode: `--run-id <id> --from-plan` prints
  the summary, `--detail` expands to per-object records, and `--kind <kind>` narrows the detail to
  one kind. **The two-option spelling is superseded by AD057**, which folds the run identifier into the
  review option's own value — `--from-plan <run-id>` — because `--run-id` otherwise carries two inverse
  meanings behind an omissible flag. `--detail` and `--kind` are unchanged, as is everything else this
  decision fixes. In that mode no adapter is constructed and neither side is extracted, which is what
  lets review run in a process that did not produce the plan. No command is added and no command
  group is added. Review output is written to standard output rather than the log stream, since it
  is the command's product and must be capturable for the credential scan. The in-process reader is
  the single implementation; the command is a thin renderer over it, so both paths in SC-009
  exercise the same code. `[AD005]`

### Session 2026-07-26 — environment fact

One environment fact is recorded as a decision handle because five artifacts cite it and it decides
which evidence can be produced at all.

- Q: Is a live Infrahub reachable in the environment this feature is planned and built in? → A: **No.**
  Every criterion whose evidence needs a live destination therefore lands behind the repository's
  existing opt-in `integration` marker (`pyproject.toml:133-135`) and is **authored rather than
  produced** here; every other criterion runs locally against fakes and fixtures. Two consequences are
  carried elsewhere rather than restated: the deferral this creates against the brief's completion
  condition is AD045, and the two facts about the destination write path that cannot be verified
  offline — whether the convergent write replaces or merges a cardinality-many peer set, and the exact
  nested relationship filter spelling — are neutralized by AD038 and by making a resolution miss a loud
  refusal (AD016), so neither answer decides correctness. `[AD007]`

### Session 2026-07-26 — checklist evaluation

Sixteen further areas were resolved after four independent evaluations worked 166 checklist items
against this specification and the repository. Each resolution takes the reading that does **not**
expand scope; where the brief's own text settles the matter it is followed rather than re-decided.
All sixteen are **ratified** on the same basis as AD001–AD005 — the delivery gate that closed this
run's critique loop — and each keeps its revisit set.

- Q: What value binds the plan to its source snapshot, and how is a truncated snapshot detected? →
  A: A manifest field `source_snapshot` records, per source-snapshot file the plan was computed
  against, that file's run-relative path, a SHA-256 digest of its content, and its row count —
  **"content" fixed by AD037 as the file's logical rows with the per-run `_extract_ts` excluded, not
  its raw bytes, without which this decision and SC-006 cannot both hold**. "Match"
  is recomputed equality of all three; an absent recorded file, or a disagreeing digest or row count,
  is a refusal. One field yields both a binding-mismatch signal and a truncation signal, and the row
  count is already computed on the load path. The source snapshot is one Parquet file per resource
  rather than a single file — `write_resource_side` writes `<run_dir>/A/<resource>.parquet`
  (`cache/parquet_io.py:92-142`, called from `_write_side_snapshot`, `potenda/__init__.py:123`) —
  which is why the field is per file. Because `plan_checksum` covers the canonical manifest, tampering
  with the recorded digests fails the checksum, so the pair cannot tear without the snapshot bytes
  themselves entering the checksum. `[AD008]`
- Q: Is "relationship change" a fourth action, or a field carried on a create or update? → A: The
  action vocabulary is closed to `create | update | delete`. A relationship change travels as
  relationship references on the owning object's create or update operation, never as a separate
  action, which matches the brief's In-scope wording ("relationship references" as a per-operation
  field) and matches the generated models, which are flat and carry relationships as fields
  (`generator/templates/diffsync_models.j2:29-48`). SC-003's third write class is therefore
  "operations whose payload carries relationship references". AD002's identifier triple is unchanged:
  under this model exactly one operation exists per (action, kind, identity), so a collision is always
  pathological and FR-021's assertion is correct as written. `[AD009]`
- Q: What run state does a refused apply record, and what is "an applied state"? → A: A refused apply
  records run state `failed`, and "an applied state" means `status: applied`, in the existing run
  sidecar vocabulary `pending | running | dry-run | applied | failed` (`cache/sidecars.py:71`). No new
  state is introduced, because `previous_successful_run_dir` consumes that vocabulary through
  `_SUCCESS_STATUSES = frozenset({"applied", "dry-run"})` (`cache/incremental.py:24`) and adding one
  would be a compatibility change this outcome does not authorize; `failed` is already outside that
  set, which is the behavior that matters. **Superseded in one clause by AD063**: this decision also
  folded in a repair of the pre-existing schema-subhash refusal path, on the reading that it aborts via
  `print_error_and_abort` (`cli.py:336-340`) leaving `running` on disk permanently. That path is
  **unreachable** — the block imports a resolver the package does not define (`cli.py:330`) and the
  `except ImportError: pass` at `:341-342` swallows it — so the repair is dropped and the record
  corrected. The run-state decision itself stands unchanged for the **new** refusal paths, which is what
  DBA-004 needs. `[AD010]` `[AD063]`
- Q: How can FR-025's partial-apply record hold while a durable crash-surviving ledger is out of
  scope? → A: "Stops partway" means an apply that terminates in-process with a reported error. The
  record is best-effort at that point and is explicitly **not** required to survive abnormal process
  termination. SC-003's crash windows stay evaluable without a durable ledger because their
  measurement is destination-side — object counts and identities — so the windows remain meaningful as
  injection points even though no persisted record distinguishes them afterwards.
  `[AD011]`
- Q: What stops a `plan/` directory copied into a different run directory from verifying clean? →
  A: An additional pre-apply check — one of the five FR-009 now enumerates, once AD028 added the
  format-version check ahead of it: the manifest's recorded run identifier must equal the run being
  applied. This is a separate equality comparison rather than a checksum input, because AD001
  deliberately excludes the run identifier from `plan_checksum` so the manifest can be byte-identical
  across re-plans (SC-006) — which is exactly what leaves the copied-plan hole. DBA-004 names three
  checks but does not forbid additional ones, and refusing a mis-filed plan is inside DBR-003's "safe
  to apply". `[AD012]`
- Q: At apply, the stored configuration-version value is compared for equality — against what? →
  A: The apply recomputes the value by the same default rule (a deterministic content checksum over
  the configuration it is applying with) and compares for equality, unless an in-process caller
  supplies one verbatim, in which case the supplied value is compared verbatim. The value is never
  parsed either way, and no new user-facing input is added: the CLI apply path uses the default rule
  only. `[AD013]`
- Q: A crashed new-format plan write leaves `plan.parquet` with no `plan/manifest.json` — is that a v1
  plan or a torn one, and do new plan runs keep writing `plan.parquet`? → A: `plan/operations.jsonl`
  is written first and `plan/manifest.json` **last**, so the manifest's presence is the commit point,
  matching the atomicity discipline the existing sidecars already use (`cache/sidecars.py:13-24`,
  tmp+replace). A v1 verdict then requires the absence of the whole `plan/` directory; a `plan/`
  directory present without a complete manifest is TORN, not v1, which makes the two cases disjoint by
  construction rather than by heuristic. New plan runs keep writing `plan.parquet` unchanged — it is
  written unconditionally today (`cli.py:152`, `cli.py:271`, `potenda/__init__.py:462`, `:496-499`)
  and this outcome authorizes no removal — and the new reader never reads it, so DBR-019's "no second
  apply path" still holds structurally. `[AD014]`
- Q: How does the adapter execute a planned update, given the existing update path cannot run without
  a destination load? → A: Planned creates **and** updates both route through the HFID-keyed
  convergent upsert — `client.create(...)` followed by `save(allow_upsert=True)`
  (`adapters/infrahub.py:611-612`) — and never through `InfrahubModel.update`, which opens with
  `client.get(id=self.local_id, ...)` (`adapters/infrahub.py:622`) on a `local_id` populated only by a
  destination load (`adapters/infrahub.py:510`, `infrahub_sync/__init__.py:232`) that DBR-004 forbids.
  Cardinality-many relationships are **replace-set**, which is the existing behavior: `update_node`
  computes `compare_lists(existing_peer_ids, new_peer_ids)` and then removes `existing_only` and adds
  `new_only` (`adapters/infrahub.py:166-175`). An update payload is authoritative for the mapped
  fields it carries and does not touch unmapped destination fields. **The replace-set clause is
  superseded by AD038**: `update_node` is on the path this decision forbids, so its behavior is not
  evidence about the path this decision mandates; the replace-set is enforced explicitly after the
  upsert instead of being assumed of it. `[AD015]`
- Q: What happens when a planned relationship peer matches no destination object, or more than one? →
  A: A **zero** match refuses the operation and fails the run, naming the peer kind, the peer identity,
  and the referring operation identifier. A **multiple** match refuses, naming the peer kind, the peer
  identity, and the match count. Never a silent skip: "a silent skip would make the applied set differ
  from the reviewed set" is the brief's own reasoning for DBR-016 and it governs a dropped relationship
  exactly as it governs a dropped operation. This replaces today's behavior, which drops a zero match
  with a log warning (`adapters/infrahub.py:141-143`, `:212-214`, `:229-231`) and surfaces a
  multi-match as a bare `IndexError("More than 1 node returned")` from the SDK
  (`infrahub_sdk/client.py:566`). `[AD016]`
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
  **Partly superseded by AD044**: the human-friendly-ID condition stands and is added, but it does not
  *replace* the brief's uniqueness-constraint condition, which AD044 restores alongside it.
  `[AD017]`
- Q: What marks a value secret, and what does redaction do? → A: The artifact carries mapped source
  field values only. Credentials live in the configuration's `settings` and are never written to the
  artifact or to review output. No field-level secret-classification model with omit, mask, or refuse
  behavior is built — that would be new user-visible behavior, a new configuration surface, and a new
  failure mode, none of which the brief's In-scope list authorizes. DBA-010's canary is injected as a
  credential in `settings`, which is where secrets actually enter this system. `[AD018]`
- Q: FR-008 forbids a new CLI command; the brief forbids only a new command group. Which binds? →
  A: The brief's bar: no new command **group**. DBR-020, DBA-012, D026 and D002 all forbid a group,
  and the brief's Constraints leave the carrier free. A specification must not assert a constraint
  stricter than the brief, even a conservative one, because it would make a future in-scope choice
  look like a violation. AD005 nonetheless extends `diff` by choice, so in fact no command is added
  either. The distinction is live because the CLI has no groups at all today (`cli.py:31`, a single
  flat `typer.Typer()` with no `add_typer` anywhere in the package), so the group bar is trivially met
  and the command bar is the only one doing work. `[AD019]`
- Q: What must per-object review output show? → A: Per operation, at least the operation identifier,
  the action, the destination kind, and the destination identity. Without this, SC-005's comparison of
  "the identifiers shown at review" against the apply result has no review-side source.
  `[AD020]`
- Q: `diff` already has a `--run-id` meaning "re-use a specific cache run id", an unknown run id
  silently creates the directory, and `diff` takes an exclusive lock. How does review mode behave? →
  A: In review mode the run identifier selects the stored run to read, MUST NOT create a run
  directory, and an unknown or plan-less run id is an error naming the run identifier and the expected
  artifact path. The mode constructs no adapter, extracts nothing, and takes no pipeline lock. The
  existing live-path meaning of `--run-id` (`cli.py:98`) is unchanged. **AD057 supersedes how the
  stored run is named**: the run identifier is the review option's own value rather than a separate
  `--run-id`, which is what removes the two-inverse-meanings overload this decision worked around by
  scoping behavior to a mode. Everything this decision fixes about that mode's behavior is unchanged.
  Without this, reviewing a
  typo'd run id renders as a valid zero-operation plan — the most dangerous possible output for a
  review-before-write feature, since an empty plan is also a legitimate artifact — because
  `get_potenda_from_instance` creates the run directory with `mkdir(parents=True, exist_ok=True)`
  before any check (`utils.py:244-246`) and then writes `schema-sub-hash.txt` into it
  (`utils.py:256-263`). Not taking the lock is what makes review usable while a sync is running
  (`cli.py:129`, `cache/locks.py:21-33`, 60-second timeout) and is safe because the mode only reads.
  `[AD021]`
- Q: Is FR-014's tier guarantee true unconditionally? → A: No — it is qualified to references present
  in the computed dependency graph. Self-references are excluded from write-order edges
  (`dependency_graph.py:33-34`), optional edges are dropped to break cycles
  (`dependency_graph.py:81-98`), and a configuration supplying an explicit `order:` yields no tiers at
  all (`infrahub_sync/__init__.py:132-133`). In those three cases the peer may be unresolved at apply,
  which AD016's zero-match arm then refuses rather than silently skipping. The qualification is safe
  precisely because the miss is loud; an absolute guarantee the graph machinery falsifies would remove
  the reason to implement AD016's refusal at all. `[AD022]`
- Q: AD005 sends review output to stdout. Does the existing live `diff` output move too? → A: No.
  Only the new `--from-plan` mode writes to stdout; the existing live `diff` output keeps using the
  logger, unchanged (`cli.py:153`). Moving existing output is a user-visible change to an existing
  path that the brief does not authorize, and DBA-010's scan only needs the new outputs capturable.
  `[AD023]`

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
  difference over-inclusive. That phantom delete matters more than ordinary staleness because it puts a
  delete in front of a reviewer for an object that no longer exists and inflates the skipped-delete count
  FR-017 records, in the one feature whose purpose is trustworthy review-before-write. **Under AD055 the
  consequence is a misleading plan and an overstated count rather than a spurious run failure**, which is
  a smaller harm than the earlier reading assumed but is still a reason to derive no deletes at all.
  Recording the omission is what keeps FR-017's "never silently skipped" contract true: nothing is
  dropped quietly, because the manifest says deletes were not computed — and, under AD056, both review
  depths say so too.
  Implementation note: the signal exists but is not per-side today —
  `self._did_full_extract = self._did_full_extract or (not use_inc)` OR-accumulates across both sides
  (`potenda/__init__.py:200`, consumed at `:430`), deliberately so per the comment at `:197-199`, so
  it cannot answer "did the destination run a full extract". The requirement is stated in terms of the
  destination side's extraction completeness rather than in terms of that flag.
  `[AD024]`

### Session 2026-07-26 — checklist evaluation, round two

Twelve further areas were resolved after an independent verification pass worked 168 checklist items
against this specification and the repository. As before, each resolution takes the reading that does
**not** expand scope; where an item asked for an obligation the brief does not carry, the resolution
records the exclusion explicitly rather than building the capability. All twelve are **ratified**
on the same basis as AD001–AD024.

- Q: What happens to a reviewed `update` whose destination object was deleted out-of-band between
  plan and apply, given destination freshness checks are out of scope? → A: It materializes as a
  create. This is a direct consequence of the write path FR-013 mandates: the human-friendly-ID-keyed
  convergent upsert creates when no destination object matches the key. No conflict detection, no
  destination freshness check, and no refusal path is built, because the brief places destination
  freshness checks and conflict policies out of scope. The operation is still reported under its
  original operation identifier and its original action, so the review-to-apply identifier link
  (SC-005) is unaffected. `[AD025]`
- Q: AD021 forbade creating a run directory for an unknown run identifier on the review path. What
  does an **apply** naming a run identifier with no plan artifact do? → A: The same thing. An apply
  naming a run identifier that does not exist, or whose run holds no plan artifact, is an error
  naming the run identifier and the expected artifact path, and MUST NOT create a run directory.
  Today the run directory is created unconditionally before any check and a schema sub-hash file is
  written into it, which would turn a typo'd apply into a fresh, empty, plan-less run directory.
  `[AD026]`
- Q: What happens when the destination rejects an individual operation, or the connection to it
  fails partway through an apply? → A: Fail fast. The apply stops at the first operation the
  destination rejects or that fails in transport; the operations already reported applied stay
  recorded per FR-020; the run is recorded `failed`; and the failure names the failing operation
  identifier and the underlying error. The apply does not continue past a failure and does not roll
  back. This is FR-025's partial-apply path with an explicit trigger, and it follows DBR-016's rule
  that a divergence between the reviewed set and the applied set is never silent. `[AD027]`
- Q: Is a manifest format version required, and what does a reader do with a version it does not
  recognize or with fields it does not know? → A: A `format_version` field is required. A manifest
  whose `format_version` is unrecognized is refused with a message naming the version found and the
  versions supported — a message distinct from the v1 rejection message, because the two conditions
  have different operator remedies. Unknown *additional* manifest fields are tolerated on read and
  preserved for checksum purposes, because a later outcome adds a schema-fingerprint field to this
  same manifest and a strict reader would break the moment it does. The complete field set is
  carried in one normative requirement rather than assembled from prose, because it is the contract
  nine later outcomes consume. `[AD028]`
- Q: DBR-020 requires review "through the in-process API", but which surface is that? → A: One
  supported entry point reads a stored plan and produces both review depths. It returns data rather
  than writing to a stream, and the command-line renderer is a thin layer over it, so both of
  SC-009's reachability paths exercise one code path. No broader public API is designed here; a
  single reader entry point is named and nothing else. `[AD029]`
- Q: Several checklist items ask for retention, pagination, performance targets, an output-stability
  contract, and a format-change governance policy. Are any of them in scope? → A: None. Each is
  recorded as an explicit Out-of-scope line so the requirements are complete about the exclusion
  rather than silent on it. The brief's Out-of-scope and Constraints text supports each exclusion,
  and each is cited at the exclusion. `[AD030]`
- Q: May a plan that would fail apply verification be reviewed? → A: Yes. Review verifies the plan
  checksum and reports the result prominently in its output, but does not refuse to render. Refusing
  to *show* an operator a suspect plan is worse than showing it with a clear warning, and review
  performs no writes, so nothing rides on the verdict. Review never mutates the run state.
  `[AD031]`
- Q: The constitution forbids `print`. FR-008 requires review output on standard output. How? → A:
  `typer.echo`. The repository's no-`print` rule bans the builtin `print` specifically — its
  enforcement test matches a call whose function name is `print` — and the CLI already uses
  `typer.echo` for help output. Naming the mechanism is what makes the logging rule and FR-008's
  stdout requirement visibly reconciled rather than apparently in conflict. `[AD032]`
- Q: May a run already at `status: applied` be applied again, and is verification skipped for an
  empty plan? → A: Yes to the first; no to the second. SC-002 and User Story 3 both presume a second
  apply of an already-applied run, so it is permitted. Pre-apply verification runs unconditionally on
  every apply attempt regardless of the operation count, so an empty plan with a broken checksum is
  still refused. A refusal is not terminal for the run identifier: the same run may be applied again
  once the cause is corrected. `[AD033]`
- Q: Does "before any destination write" also order verification before adapter construction? → A:
  No. All pre-apply verification completes before any destination **write**. Constructing an adapter
  or opening a destination connection is permitted before verification, which is what the code does
  today: adapters are built while the engine handle is assembled, before the apply command reaches
  its own checks. No refactor to verify before adapter construction is required. `[AD034]`
- Q: Which reproducibility details are still unstated? → A: Eight, closed together: the three
  checksum-excluded manifest fields are **removed** before canonicalization rather than blanked; the
  canonical manifest bytes and the operations bytes are concatenated with no separator; SC-006's
  masking is key removal, applied to the same two fields on both sides of the comparison before the
  byte comparison; "destination identity" has one canonical representation everywhere it appears — an
  ordered mapping of identity attribute name to value, key-sorted, which is what AD002 hashes and
  AD003 orders by; each per-operation field carries a stated obligation level and an absent-versus-
  empty rule; "the required source values as a full payload" is authoritative for the mapped fields
  it carries and silent about the rest, consistent with AD015 — **its extent closed by AD042, which
  puts the identity components inside it**; the configuration-version value's character domain is a
  non-empty printable-ASCII string; and the default checksum rule covers the declared content of the
  configuration the run used, as parsed, not the file's bytes — **that field set closed by AD041:
  every parsed field except the load directory, with `settings` included**. `[AD035]`
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
  kind absent from the configuration is an error naming it rather than empty output — **the raise/empty
  split refined by AD058 and the enumeration added by AD059**; FR-018's "any review output" and
  SC-010's enumeration name the same surfaces; an unreadable run directory is an error naming the
  path; SC-012's before-and-after command listing is captured as `--help` output to a file — **captured
  from the committed baseline fixture per AD060, not from a stash**; and the
  new review options carry a documentation obligation — **their help text specified per AD061**. Two
  items in this set are **retired**: the "`--from-plan` without a run identifier" error case, which
  AD057 makes unreachable by folding the run identifier into the review option's value; and the
  next-action obligation's confinement to refusals, which AD059 extends to the whole error taxonomy.
  `[AD036]`

### Session 2026-07-26 — planning-phase decisions

Five decisions were settled while planning worked AD001–AD036 down to the code. Each closes a
detail an earlier decision left one level of abstraction above the tree, or reconciles two of them
that turned out to conflict. They are recorded here, in the specification, because four of the five
change what a requirement above says rather than merely implementing it. All five are **ratified**
on the same basis as AD001–AD036.

- Q: AD008 records "a SHA-256 digest of its content" for each source-snapshot file. Over what bytes?
  → A: over the file's **logical rows**, not its raw bytes. The engine stamps a per-run extraction
  timestamp into every row of every snapshot (`cache/parquet_io.py:126`, allocated once per side per
  run at `potenda/__init__.py:130`), so two consecutive plan runs over an unchanged source produce
  snapshot files whose bytes differ by construction. A raw-bytes digest would therefore differ on
  every re-plan, the manifest would differ, and SC-006 — which fixes its mask at exactly two fields —
  would be unachievable. The digest is computed over the table with that timestamp column dropped,
  rows in file order, each row canonically encoded and LF-joined. The row source identifier and the
  tombstone flag stay **inside** the digest: both are deterministic for identical input and both are
  semantically part of what the plan was computed against. AD008's three recorded parts — run-relative
  path, digest, row count — and its "match is recomputed equality of all three" rule are unchanged.
  What the digest stops detecting is a change confined to the extraction timestamp, which is not a
  change to the data the plan was computed against. `[AD037]`
- Q: AD015 says cardinality-many relationships are replace-set, "which is the existing behavior". Is
  that true of the write path AD015 mandates? → A: **No.** The evidence AD015 cites is `update_node`
  (`adapters/infrahub.py:149-175`), which is on the path AD015 itself forbids the planned write from
  using. The path AD015 mandates — `client.create(...)` then `save(allow_upsert=True)` — has no
  verified replace-set semantics in this repository, and whether the server's upsert mutation replaces
  or merges a relationship list cannot be determined without a live Infrahub (AD007). So replace-set
  is **enforced explicitly after the upsert** rather than assumed of it: the planned write performs
  the upsert and then reconciles each cardinality-many peer set against the saved node using the only
  replace-set implementation in the tree. If the upsert already replaces, the reconciliation
  is a no-op. FR-013's "which is the existing behavior" is corrected accordingly. **Corrected further by
  AD054**: "the only *verified* replace-set implementation" was itself too generous. That implementation
  reads the peer set at `adapters/infrahub.py:151` and only fetches it at `:168-169`, so it compares the
  desired set against an unloaded one and adds without removing. The reconciliation this decision
  mandates therefore has to **re-read the destination peer set before comparing**. **Narrowed by AD070**:
  the enforcement is new code on the planned-write path and the pre-existing update path is left exactly
  as it is — correcting it there would change what the existing mutating command does to destination
  relationships, which this outcome does not authorize. **Sharpened by AD065**: re-reading is a matter of
  a destination read actually being issued, not of call order, so the mechanism is named rather than left
  as "fetch first". `[AD038]` `[AD054]` `[AD065]`
  `[AD070]`
- Q: FR-001 requires the artifact to exist before anything is written to the destination. Does the
  tiered write path allow that? → A: Not as written. The tier branch interleaves per-tier comparison
  and per-tier write and writes the aggregated plan only after every write has completed
  (`potenda/__init__.py:480-499`), and tiered execution is the default on the mutating path
  (`cli.py:182-185`), so no artifact can exist before the first write. The branch is restructured into
  two loops: compute and retain **every** tier's comparison result, write the artifact, then execute
  the retained results tier by tier. The destination-side narrowing that scopes each tier is a
  **comparison-time** narrowing — the comparison engine reads it when it computes a difference and the
  synchronizer does not read it at all (`.venv/…/diffsync/helpers.py:79-88`) — so it belongs around
  each comparison call in the compute loop, and the execution loop replays the retained results with
  the narrowing restored to its original value. `[AD039]`
- Q: Is the pre-existing per-row apply dispatch kept alongside the new one? → A: No — it is
  **removed**. FR-019 forbids a second apply path with weaker guarantees, and a wired row dispatch is
  that path. Removal is safe in a way it rarely is: the surface it dispatches to has zero adapter
  implementations anywhere in the repository, so nothing can be calling it successfully today. The
  guard's *shape* — an error naming the adapter class and directing the operator to the mutating
  command — is preserved for the new surface, so FR-023 keeps the behavior the engine already has.
  The one test double that asserts the removed dispatch is rewritten in the same phase as the
  removal, not later. `[AD040]`
- Q: AD035 fixes the default configuration-version rule as "the declared content of the configuration
  the run used, as parsed, not the file's bytes", which still leaves the field set open. Which fields?
  → A: everything the parsed configuration declares **except** the directory it was loaded from, which
  is an absolute filesystem path assigned from wherever the file was found and would make the value
  machine-dependent — a plan produced in one checkout could never be applied from another. Connection
  `settings` **are** included: a changed destination address is a changed configuration, and only a
  one-way digest is written, so including credentials in the hash input discloses none of them
  (FR-018 governs what is written). This closes AD035's open field set. It has one operator-visible
  consequence, recorded rather than left to be discovered: **rotating a credential in `settings`
  changes the configuration-version value and therefore invalidates every saved plan for that
  configuration**, which is refused at apply under FR-009 and requires a re-plan.
  `[AD041]`

### Session 2026-07-26 — cross-artifact remediation

Seven further decisions were settled after a cross-artifact analysis of this specification against
the plan, the tasks and the tree. Two close defects that would have made a brief acceptance criterion
unachievable; the rest correct a statement this specification or its planning artifacts made about
existing code, or scope a commitment that had leaked wider than the brief authorizes. As before, none
expands scope. All seven are **ratified** on the same basis as AD001–AD041.

- Q: What exactly does an operation's payload contain? → A: **The identity components as well as the
  non-identity mapped values.** The comparison engine's attribute accessor deliberately excludes the
  identity fields — its own contract states it "does not include the fields in `_identifiers`"
  (`.venv/…/diffsync/__init__.py:340-347`) — and the comparison element's attribute set is built from
  exactly that call (`.venv/…/diffsync/helpers.py:223`), while the generated models strip identifiers
  out of the attribute tuple (`generator/__init__.py:95`). A payload derived from that attribute set
  alone therefore carries **no identity fields at all**, which means the destination's convergence key
  cannot be formed, the write mutation carries neither a node identifier nor a human-friendly ID, the
  upsert is unkeyed, and every re-apply duplicates — making DBA-002 and DBA-003 unachievable. Today's
  create path avoids this precisely by passing identifiers and attributes together
  (`adapters/infrahub.py:602-604`). The payload written into an operation is therefore the union of
  the comparison element's identity mapping and its full source attribute set. An identity component
  whose schema mapping declares a reference is carried as a relationship reference rather than left in
  the payload as a raw unique-id string, on the same rule as any other reference-bearing field.
  `[AD042]`
- Q: A peer's identity may itself contain a reference. What does the plan record for it? → A: A peer
  identity component that is itself a reference records the nested pair (peer kind, peer identity)
  rather than a raw unique-id string — **recursively**, to whatever depth the configuration nests.
  This is not hypothetical on the qualified path: ten schema-mapping entries there carry a reference
  inside `identifiers`, and a reference field's value in a comparison model is the peer's unique-id
  string. (That figure is **configuration-side**, which is the right one here — the nesting follows
  `SchemaMappingField.reference`. It is *not* the count of kinds whose destination convergence key
  crosses a relationship, which is five; see AD091.) On a resolution miss at apply the resolver holds only the peer's identity mapping, so
  without the nested pair it could not build a nested destination filter without splitting a unique-id
  on its separator — the v1 flaw the brief names by name. This changes the artifact format that nine
  later outcomes consume, so it is settled before any of that format is written.
  `[AD043]`
- Q: FR-024 warns when the destination kind has no usable convergence key. AD017 restated the brief's
  "not unique-constrained" condition as "declares no human-friendly ID". Is that the same condition?
  → A: **No — and the brief's condition is the one that must be covered.** A kind with a complete
  human-friendly ID but no uniqueness constraint over the plan's identity attributes still duplicates
  silently, which is exactly the failure the brief asks to be detected. FR-024 therefore warns on
  **both**: the human-friendly ID absent or incomplete, **and** the destination kind declaring no
  uniqueness constraint covering the plan's identity attributes. Both are readable from the same
  cached destination schema object — the uniqueness constraints sit beside the human-friendly ID on it
  (`.venv/…/infrahub_sdk/schema/main.py:272,274`). It stays a warning, not a failure, per the brief.
  `[AD044]`
- Q: Six criteria and half-criteria sit behind the opt-in integration marker with no reachable
  destination (AD007) — **five** of them the brief's own (DBA-001, DBA-002, DBA-003 and DBA-008 in
  full, plus the live half of DBA-007) and one, SC-016's live half, derived by this specification
  rather than stated by the brief. That is against a brief completion condition demanding inspectable
  passing evidence for every criterion. What is done about it? → A: Both halves of a two-part answer, and neither is a
  substitute for the other. First, a **local conformance harness** asserts the *mutation the destination
  library renders* rather than the destination state: that the rendered mutation input carries either a
  destination-assigned identifier or a human-friendly identifier, so an unkeyed write is visible; that
  the replace-set reconciliation issues a destination read for the relationship before reading the peer set
  it compares against; and that two applies of the same operation render byte-identical mutation inputs.
  That catches
  a whole class of defect offline — AD042 is exactly such a defect, and it is the class those deferred
  criteria exist to catch. **Rebuilt by AD054**: as first written the harness asserted against the
  *assembled* data and against a wholly mocked destination library, which makes two of its three
  assertions unfalsifiable — the assembled data cannot show keyedness, and a mock holds no destination
  state against which "no second create" or "the peer set was replaced" could fail. The harness is built
  against a real destination node constructed from a **committed schema fixture** instead, and asserts
  the rendered mutation input. **Sharpened again by AD065, AD067 and AD068**: the replace-set observable is
  that a destination read was *issued*, because a mechanism expressed as call order performs no read at all;
  the keyedness assertion is split, holding for kinds whose key is all-direct and standing as a strict
  expected failure for a kind whose key crosses a relationship, which cannot render keyed today; and the
  repeat assertion is byte-identity of the rendered inputs, because "two applies produce one create" cannot
  fail against a double at all. Second, the deferral is **stated, not implied**: the brief's DBA-001,
  DBA-002, DBA-003 and DBA-008 and the live half of its DBA-007, together with the live half of this
  specification's own SC-016, remain deferred to a run against a live Infrahub, and the brief's
  completion condition is therefore not met at merge time. `[AD045]`
- Q: A peer's kind is derived from the referring field's schema-mapping `reference` value. Is that
  unambiguous? → A: Not on the qualified path. Two schema-mapping entries there declare the same
  destination kind with different references for the same identity field
  (`examples/netbox_to_infrahub/config.yml:212` and `:254`, both `DcimDevice` with
  `identifiers: ["location", "name"]`, one referencing `LocationRack` and one `LocationSite`), so the
  mapping alone cannot say which kind a given peer is, and a wrong pick fails the whole apply run on
  the brief's own qualified path. A peer's kind is therefore established by finding the referenced
  object in the loaded store rather than by reading the mapping's `reference` field. **AD050 supersedes
  how**: the store has no kind-free lookup, so "the entry knows its own kind" is not reachable as
  written, and the rule is carried out as a bounded probe over the candidate kinds the mapping declares
  for that field. AD046's *what* — never the mapping's `reference` as the answer — is unchanged.
  `[AD046]`
- Q: Several plan-derivation failures now land on the non-mutating command, whose `--continue-on-error`
  escape hatch does not exist there — the option is declared on the mutating command only
  (`cli.py:190`). What happens? → A: A plan-derivation failure **fails the command**, with a clear,
  actionable error naming the kind and the cause. No `--continue-on-error` is added to the
  non-mutating command, and derivation does not degrade to warn-and-skip: a silently incomplete plan
  is the failure DBR-016 exists to prevent, and it is worse here than anywhere else because the whole
  feature's product is a plan an operator trusts. The constitution's "`list`, `diff` and `generate`
  MUST stay safe to run at any time" is read as *the command performs no destination mutation* rather
  than *the command never errors* — which still holds, because derivation happens after a read-only
  comparison and writes only into the run directory. That reading is stated explicitly here and in the
  plan so it is reviewable rather than assumed. `[AD047]`
- Q: FR-014 replaces a zero-match peer's warn-and-continue with a refusal. Where does that replacement
  apply? → A: **Only to the new apply-path peer resolver.** The existing warn-and-continue in the live
  write path (`adapters/infrahub.py:141-143`, `:212-214`, `:229-231`) is unchanged: it is existing
  behavior on an existing path, and this brief does not authorize touching it. The refusal is a
  property of resolving a peer from a saved plan with no comparison store loaded, which is a path that
  does not exist today. Scoping it this way is what keeps FR-014 inside the brief's In-scope line
  "apply-time relationship peer resolution" rather than reaching into the live sync write path.
  `[AD048]`

### Session 2026-07-26 — remediation of the cross-artifact repairs

Five further decisions were settled after a second read of AD042–AD048 against the tree. Three of them
close defects the previous round's repairs introduced: a rule stated unconditionally that a later rule
made inapplicable, a resolution rule that turned out to be circular against the library it names, and an
assertion that cannot be implemented as worded. The other two reconcile a requirement with a design the
planning artifacts had already committed to. None expands scope. All five are **ratified** on the
same basis as AD001–AD048.

- Q: FR-002 and the recursive identity rule are unconditional, but a delete is derived from the
  destination store, where a reference field's value **is** the peer's unique-id string and AD046's
  "resolve the kind from the loaded source store" has nothing to resolve against — a delete's peer is
  destination-only by construction. Are deletes carved out? → A: **No.** A derived delete's identity is
  canonicalised by the **same** recursive rule, with the nested peer kinds resolved from the **loaded
  destination store** instead of the loaded source store. That is the only part of the rule that
  changes, and it changes because of what a delete is, not to make an exception for it. Carving deletes
  out would leave the artifact with exactly one place where a consumer must split a unique-id on `__` —
  the flaw the brief names — and would mean a delete's operation identifier was derived from an identity
  no reviewer ever sees. Nine kinds on the qualified path carry a reference inside their identifiers
  (ten mapping entries) — the **configuration-side** figure, which is the one this clause needs; the
  destination-side keying figure is different and smaller (AD091) — so this is the common case for
  deletes there, not a corner.
  `[AD049]`
- Q: AD046 says a peer's kind comes from "the loaded source store entry for the referenced unique-id —
  the entry knows its own kind". Is that lookup constructible? → A: **Not as written — it is circular.**
  The comparison library's store is keyed by (model, unique-id) and every read requires the model:
  `BaseStore.get(*, model, identifier)` and its local implementation both take `model` and use it to
  select the per-model bucket before the identifier is looked up at all
  (`.venv/…/diffsync/store/__init__.py:40-52`, `.venv/…/diffsync/store/local.py:30-49`). There is no
  kind-free lookup by unique-id, so "the entry knows its own kind" cannot be reached without already
  knowing the kind. The rule is therefore restated as a **bounded probe**: the candidate set is the
  kinds the configuration declares as `reference` for that field across **every** schema-mapping entry
  whose `name` is the owning destination kind — for `DcimDevice.location` on the qualified path,
  `{LocationRack, LocationSite}` (`examples/netbox_to_infrahub/config.yml:239`, `:281`). Each candidate
  is probed in the store with the referenced unique-id, `ObjectNotFound` meaning "not this kind".
  Exactly one hit gives the peer's kind. **Zero** hits and **more than one** hit both fail the plan run
  under FR-030, naming the owning kind, the field, the unique-id and the candidates tried. Neither
  arm may fall back to the mapping-derived kind, including the single-candidate case, which is probed
  like any other: a fallback is precisely the arbitrary pick AD046 exists to forbid, and it would make
  the wrong pick silent on the path where it fails the whole apply run. Under AD049 the same probe runs
  against the destination store for a derived delete. `[AD050]`
- Q: The write-surface step-3b assertion and the offline conformance harness both require "every
  component path of the destination kind's human-friendly ID resolves against the create data". For a
  relationship-crossing path the data holds a resolved node-id string, from which the attribute cannot
  be read. How is the check defined? → A: **Per component, by component shape.** For a **direct**
  component the check is that the key is present and non-null in the data handed to the create call.
  For a **relationship-crossing** component the check is twofold: the relationship key is present and
  non-null in that data, **and** the plan's nested `{peer_kind, identity}` for that relationship
  supplies the attribute the component names. That is implementable from what the apply actually holds,
  and it still fails loudly for every case the assertion exists to catch. A dependency is recorded with
  it rather than left to be discovered. Server-side, the convergence key is formed from the mutation's
  `hfid`, which the SDK computes with `get_human_friendly_id()`; for a relationship-crossing component
  that calls `get_path_value()`, which resolves the peer through the SDK **client store** and returns
  `None` when the store does not hold it — the code says so at the point of failure ("this can happen
  while batch creating nodes, the lookup won't work as the store is not populated",
  `.venv/…/infrahub_sdk/node/node.py:100-107`), and one `None` component makes the whole HFID `None`
  (`:135-139`), leaving the mutation with neither `id` nor `hfid` (`:295-298`). The SDK populates that
  store on `save()` (`:744`, `:1549`) and on `get`/`filters` with `populate_store=True`
  (`.venv/…/infrahub_sdk/client.py:911-918`, `:2271-2278`), and this outcome's resolver is specified to
  return ids and to never touch it. Because `generate_payload_create` renders a resolved id as
  `{"id": "<id>"}` with no `__typename` (`.venv/…/infrahub_sdk/schema/__init__.py:172-181`), the
  related node it builds has no typename and cannot be fetched from the store at all
  (`.venv/…/infrahub_sdk/node/related_node.py:54-55`, `:64-68`, `:298-304`). This is carried as an
  explicit risk with the step-3b assertion as its detector, not as a settled mechanism.
  `[AD051]`
- Q: FR-024's warning reads `human_friendly_id` and `uniqueness_constraints`, which exist only on the
  Infrahub destination adapter — but plan derivation is now wired onto the non-mutating command
  unconditionally and AD047 makes derivation failures fatal there. What happens on the other eight
  adapters? → A: The warning is **scoped to destinations that expose a schema**, and where none is
  exposed it is skipped and is never an error. An unguarded read would turn FR-024 into a hard
  regression on non-Infrahub destinations that compare perfectly well today, which is the opposite of
  what a warning is for. The repository already treats the destination schema as optional in exactly
  this way where it reaches for one (`infrahub_sync/utils.py:260`). A regression assertion that the
  non-mutating command still succeeds against a non-Infrahub destination is required, and the coupling
  of engine-level derivation to one adapter's schema surface is recorded in the plan's Complexity
  Tracking rather than left implicit. `[AD052]`
- Q: FR-009 says without qualification that all checks are evaluated and every failure named, but the
  format-version check is designed as a short-circuiting gate and a task asserts exactly that. Which
  wins? → A: **The gate**, and FR-009 is amended to admit it rather than leaving a task asserting
  behavior a MUST forbids. When the declared format version is unrecognized, or the manifest cannot be
  parsed, that one failure is reported, the remaining four checks are not evaluated, and the message
  says so. Evaluating the other four against a manifest whose semantics are unknown would report
  failures that are artifacts of the reader's ignorance — which defeats the purpose of the evaluate-all
  rule instead of serving it. Once the gate passes, all four remaining checks are evaluated and every
  failure is named, unchanged. `[AD053]`

### Session 2026-07-27 — critique round one, ratified

Eleven decisions were ratified after three independent critique lenses worked this specification, the
plan and the tasks against the brief and the repository. Ten correct a defect in this specification's
own delivery — an assertion that proves nothing, a disclosure that never reaches a review surface, an
option with two inverse meanings, a contract that does not match its own API, a failure with no next
action, a walkthrough step that does not execute, help text left to the implementer, a record with no
home, a repair applied to unreachable code, or a criterion reported as carried when it is carried
conditionally. The eleventh, AD055, re-derives two derived requirements and is recorded in its own
session below. None expands scope. All eleven are **ratified** on the same basis as AD001–AD053.

- Q: The offline conformance harness compensates for five brief criteria that cannot be evidenced
  without a live destination. Two of its three assertions prove only that a mock was called. What
  replaces it? → A: The harness is **rebuilt to assert the rendered mutation input**, not the assembled
  data. It constructs a real destination node from a committed schema fixture and asserts that the
  rendered mutation input carries a node identifier or a human-friendly identifier — which is where
  keyedness is actually observable, because the destination library renders the mutation locally and
  keys it on `data["id"]` if set, else `data["hfid"]`
  (`.venv/…/infrahub_sdk/node/node.py:295-298`, rendered on the upsert path at `:1843-1846`). Asserting
  against the assembled `data` cannot see keyedness at all: a relationship-crossing component is a
  resolved identifier string by then. Two further corrections travel with it. **First**, the replace-set
  reconciliation MUST **re-read the destination's peer set before comparing**. A locally built node
  reports the desired set as its existing set — `self.initialized = data is not None`
  (`.venv/…/infrahub_sdk/node/relationship.py:264`), and `fetch()` returns immediately once initialized
  (`:286-299`) — so a comparison made without re-reading is a guaranteed no-op that can only pass
  against a mock. **Second**, code fact V12 overstates today's behavior as a replace-set and is corrected
  in the plan: the pre-existing update path reads the peer set at
  `infrahub_sync/adapters/infrahub.py:151` and only then fetches at `:168-169`, so its "replace-set" adds
  without removing. This decision originally corrected that pre-existing ordering too; **AD070 withdraws
  that third clause** and confines the correction to the new planned-write path. `[AD054]`
  `[AD070]`
- Q: AD024 records the delete-computation flag in the manifest, and DBR-009 makes recording deletes the
  default, but no requirement puts either on a review surface — so a plan missing its entire delete
  class is indistinguishable from a plan with no deletes. Is the omission reviewable? → A: **Not as
  written, and it must be.** Both review depths MUST surface the delete-computation record and say
  plainly when deletes were not computed for the plan, and a non-zero delete count MUST be annotated
  inline in both the summary and the per-object detail to say that no delete will be executed by this
  release. This is the disclosure that makes AD024 defensible; without it AD024's own justification —
  that the omission is "explicit and reviewable" — is delivered by no obligation at all. It is carried
  into FR-006, the plan-summary contract, SC-009's pass condition, and the review-rendering task.
  `[AD056]`
- Q: In read-from-artifact mode the run identifier is a read source that errors on an unknown value;
  on the live path the same option is a write target whose unknown value is silently created and whose
  existing plan is overwritten. The discriminator is an omissible flag. Is that a contract an operator
  can hold? → A: **No.** The review option **takes the run identifier as its value**, so there is one
  option with one meaning rather than two inverse meanings behind an omissible flag. Forgetting the mode
  flag can no longer turn a read into a destructive write against the artifact being read. The brief
  makes option spelling an implementation choice within one fixed constraint — no new command group — so
  this is fully inside scope, and it retires the separately specified "review mode requested with no run
  identifier" error case, which can no longer arise. The live path's own run-identifier option keeps its
  existing meaning, untouched. `[AD057]`
- Q: Three contracts call operations their own documents do not declare. Which side is authoritative?
  → A: **The declared interface.** Three concrete mismatches are corrected. The destination write
  surface MUST use the peer resolver's single declared entry point rather than two singular/plural
  variants declared nowhere. The pre-apply verifier MUST receive the **adapter's name** rather than a
  boolean, because the message it promises names the adapter and a boolean cannot supply it. And
  per-object detail narrowed to a kind the configuration **does** declare but for which the plan holds no
  operation MUST return an empty result from the reading interface and MUST NOT raise: the never-empty
  rule is a presentation obligation and belongs to the renderer, not to the reader that FR-029 requires
  callers to consume without parsing output. Raising is reserved for a kind the configuration does not
  declare at all. `[AD058]`
- Q: AD036 attached the next-action obligation to *refusals*. Nine other failures name a cause and
  stop there. Does the obligation extend to them? → A: **Yes — to the whole error taxonomy.** Every
  failure this feature introduces MUST name the operator's next action: a torn artifact, an unrecognized
  format version, an unreadable path, an unknown run identifier, a kind narrowing that matches nothing,
  a plan-derivation failure on the non-mutating path, a peer that matches no destination object, a peer
  that matches more than one, and a payload value the canonical encoding cannot represent. Where an
  enumeration is already in hand the message MUST list it: the kinds the plan actually holds for a kind
  narrowing, the run identifiers that actually exist for an unknown run identifier. Echoing the
  operator's own input back while withholding an enumeration the command already holds is a failure that
  reads as an answer. `[AD059]`
- Q: Two validation steps were reproduced as broken as written. Do they stay? → A: **No — the
  validation walkthrough must execute as written.** Two are corrected. A checksum-recomputation snippet
  that reads its target from an argument it is never passed resolves a path at the repository root and
  cannot succeed; it MUST be passed the run directory. A baseline capture that relies on stashing an
  uncommitted change is a no-op on a committed tree, which makes the command-set comparison diff a file
  against itself and pass with no baseline at all; the "before" listing MUST come from the committed
  baseline fixture captured before any command-line change, which is the only form of that evidence that
  cannot silently degrade. The third broken step was the apply walkthrough, which AD055 rewrites.
  `[AD060]`
- Q: No artifact specifies the new options' help text, and the existing run-identifier option's help
  string becomes incomplete once it also selects a stored run — yet a task regenerates the
  command-line reference documentation from whatever strings appear. Where is that text decided? → A:
  **In the command-line contract, before it is generated.** The help text for each new option, and the
  corrected text for the run-identifier option, are specified there so the generated reference is
  reviewed rather than discovered after the fact. `[AD061]`
- Q: FR-020 requires the applied-operation identifiers on the run result; the run record's persisted
  key set is closed, and this outcome declares the run-directory layer unchanged. Where do they live?
  → A: **In the run's recorded summary, under one named key.** The summary is already a free-form
  mapping inside the run record's existing key set (`infrahub_sync/cache/sidecars.py:73`, `:76`), so
  nothing in a persisted schema other code reads has to change, and the location is named once rather
  than left for each reader to infer. FR-020 is a contract a later outcome consumes, so it cannot stay
  implicit. The same rule governs AD055's skipped-delete record. `[AD062]`
- Q: AD010 folded a repair of the pre-existing schema-subhash refusal path into this outcome. Is that
  path reachable? → A: **No — and the record is corrected rather than the code.** The block imports a
  resolver that does not exist anywhere in the package (`infrahub_sync/cli.py:330`, called `:332`; the
  comment at `:325` says a later outcome will provide it), so the import raises and the
  `except ImportError: pass` at `:341-342` swallows the whole block — the abort at `:336-340` cannot
  execute. Code fact V22 is wrong and is corrected in the plan. The repair task and its test case are
  **dropped**: fixing unreachable code yields a test that can only pass against an injected stub and a
  changelog entry that misleads. AD010's run-state decision stands unchanged for the **new** refusal
  paths, which is what DBA-004 actually needs. Making the dead check live is unrelated scope and is not
  done here. `[AD063]`
- Q: The traceability table reports DBA-006 as plainly carried while SC-006 rescues it with a
  pinned-extraction-mode precondition, and a user-story scenario restates the brief's unconditional
  wording. Which is true? → A: **Conditionally carried, and it must be reported that way.** DBA-006
  holds on the condition that both plan runs used the same extraction mode on each side — the
  delete-computation record is inside the checksum and outside the brief's two-field mask, so two runs
  at different extraction modes are expected to differ. The engineering rescue is sound; only the
  reporting overstated it. The condition is named in the traceability table and in the scenario that
  restates the brief's wording. `[AD064]`

### Session 2026-07-27 — the delete-bearing apply, re-derived

One decision was ratified at the decision gate, overriding the recommendation put forward. It is
recorded in its own session because it re-derives two requirements rather than closing a detail, and
because the basis it now carries is the whole of its authority.

- Q: DBR-009 makes recording deletes the default and DBR-010 forbids applying them. DBR-016 and
  DBA-007 then make any plan containing a delete end the apply in a failed state. Under the comparison
  engine's fallback flag set (`infrahub_sync/potenda/__init__.py:92-93`) any destination holding mapped
  objects absent from the source now yields deletes, so the qualified path's default posture would be a
  failed apply. Which side gives? → A: **The derived side, because it is the derived side.** DBR-009 and
  DBR-010 are **quoted** brief requirements and are untouched. DBR-016 and DBA-007 are both **derived**,
  so re-deriving them, carrying their new basis, softens nothing quoted.
  **The authority, corrected (AD074).** This decision was first recorded as resting on approved decision
  D020. It does not, and the citation was wrong in a way worth naming, because a miscited authority is the
  kind of error that gets copied. D020 ratifies the **planner's** derivations as batch policy on a
  basis-disclosure proviso; it says nothing about whether a derived row inside an approved brief may be
  re-derived downstream, and read the way this decision first read it, D020 would license re-deriving 6 of
  the brief's 20 requirements and 10 of its 13 acceptance criteria — which is not what a `READY` brief with
  an approver set can mean. The authority is instead the **brief owner's override at the delivery gate**:
  the person who ratified this decision is the same person recorded as the brief's approver and as D020's,
  and an approver amending their own approved artifact is scope authority acting rather than scope drift.
  D020 is cited here for one narrower thing only — the proviso that a re-derivation carry its basis, which
  this specification meets at four places.
  **The "second ground" is withdrawn, and the override was necessary (AD077).** AD074 also recorded a
  second and supposedly independent ground: that DBR-016's own term, an **unsupported operation**, never
  reached a recorded delete, because a delete is a member of the closed action vocabulary and what the
  brief separately excludes is *executing* it. That ground does not survive the brief's own usage, and it
  is withdrawn. DBA-007 applies the phrase "the unsupported operation" to a recorded delete, and
  User-scenario 4 applies it to the same thing; on the withdrawn reading DBA-007's phrase would have no
  referent at all. The brief therefore settles the meaning of its own term against that ground. The
  distinction this specification draws — between an operation this release declines to execute by design
  and an operation whose action it does not recognize — is the **substance** of the re-derivation, and it
  is a good and necessary distinction; what it is not is an alternative authority that makes the override
  optional. The consequence is the useful part: **no reading of the brief's text avoided amending a
  derived brief item, so the brief owner's override at the delivery gate was strictly necessary rather
  than belt-and-braces, and it is the whole of the authority.** Nothing recorded here is precedent for
  re-deriving a derived brief requirement without one.
  **The brief now reads false in two places and needs a revision (AD074).** Brief v5's Out-of-scope delete
  bullet and its User-scenario 4 both restate the superseded "the run fails" outcome. Both are
  restatements of DBR-016 and DBA-007 rather than normative content of their own, so they move with the
  re-derived pair and nothing normative is contradicted — but they are on the record as read by other
  briefs in the batch, and a v6 revision owes them the ratified outcome. That repair belongs to the
  planner; nothing here edits the brief.
  **The re-derivation.** A plan containing a delete applies every non-delete operation, does not delete
  from the destination, and ends in run state `applied`, with a non-zero skipped-delete count recorded
  in the run's summary and an operator-visible warning naming that count. **An operation this release
  does not execute is a designed limitation, not a run failure.** The limitation is the brief's own —
  DBR-010 puts applying deletes out of scope and assigns the capability to a later outcome — so a run
  that behaves exactly as designed must not be reported as a failure.
  **What DBR-016 protects is preserved, and that is the constraint the re-derivation is built around.**
  DBR-016 exists so the applied set stays *provably knowable* against the reviewed set; the failed state
  was one way of achieving that, not the thing being protected. Knowability is preserved by recording, on
  the run, both the identifiers of the operations that were applied and the count and identifiers of the
  deletes that were skipped: the reviewed set minus the applied set is then a recorded value rather than
  an inference, which is exactly why this is not the silent skip DBR-016 forbids. A skip is silent when
  nothing records it; this one is recorded twice over and warned about.
  **New basis for DBR-016**: DBR-009 requires recording deletes while DBR-010 forbids applying them, so
  the applied set necessarily differs from the reviewed set on every delete-bearing plan; that difference
  must be provably knowable rather than inferred, which the applied-operation identifiers together with
  the recorded skipped-delete count and identifiers supply. A **genuinely** unsupported operation — one
  whose action this release does not recognize at all — still fails the run, because nothing about it is
  designed and its effect on the destination is unknown.
  **New basis for DBA-007**: DBR-009, DBR-010 and the re-derived DBR-016, measured as the non-delete
  operations landing, the delete targets surviving, run state `applied`, the recorded non-zero
  skipped-delete count, and the warning naming it.
  **No new run status.** The vocabulary stays `pending | running | dry-run | applied | failed`
  (`infrahub_sync/cache/sidecars.py:71`); the count goes in the run **summary**, consistent with AD062
  putting FR-020's applied-operation set there rather than extending a persisted schema. One consequence
  is recorded rather than left to be discovered: an apply that skipped deletes records `applied`, which
  the incremental path's success set already contains (`infrahub_sync/cache/incremental.py:24`), so such
  a run counts as a successful prior run for a later warm start. That is correct — the apply succeeded at
  everything it executes — and it is stated here so it is reviewable.
  **Successor note.** A later outcome, DB-005, replaces the run record with durable storage behind
  provider interfaces; it should promote the skipped-delete count from a summary key to a first-class
  run-record field. That is recorded here and at FR-017 so a future reader finds it.
  `[AD055]` `[AD074]`

### Session 2026-07-27 — critique round two ratified

Three lenses re-ran against these artifacts after the round-one remediation and found nine blocking
defects, deduped into ten decisions. All ten are ratified. Every one corrects this run's own delivery;
**AD070 removes scope this outcome had quietly taken on**, and none adds any. Two of the round-one
repairs turned out not to have landed as reported, which is why this session exists at all.

- Q: The reconciliation is required to re-read the destination's peer set, and the mechanism specified for
  doing so is "fetch the relationship manager first". Does that re-read? → A: **No — it is a provable
  no-op, and the artifacts recorded the fact that defeats it three times over.** `fetch()` opens with
  `if not self.initialized:` (`.venv/…/infrahub_sdk/node/relationship.py:286-288`) and a manager built
  from local data reports `initialized is True` (`:264`), so on the node this reconciliation holds,
  calling `fetch()` first performs no destination read at all. The defect in the earlier fix is
  instructive: it was expressed in terms of **call order** when the property at stake is **whether a read
  happens**, so the change and its test would both have passed while changing nothing. The mechanism is
  therefore named rather than implied — discard the locally constructed peer set before fetching, so the
  guarded read actually runs, or issue a scoped destination read for that relationship and compare against
  its result. Either was acceptable here, and leaving it as "fetch first" was not; AD075 later narrows it to
  the first route, because the flush that decision adds must save the node whose manager was reconciled. And the test observable moves from
  "the manager was fetched before the peer set was read" — which the no-op satisfies — to **"a destination
  read was issued for that relationship before the peer set was read"**. `[AD065]`
- Q: The keyedness gate is asserted over the **assembled data**, while the flat guarantee attached to it
  says an unkeyed write is never issued. Keyedness is a property of the rendered mutation. Which moves —
  the gate or the claim? → A: **The claim, and the gate's observation point moves as far as it can go
  without destroying scope.** Moving the gate wholesale onto the rendered mutation input and refusing
  every unkeyed render was considered and rejected: for a destination kind whose human-friendly ID crosses
  a relationship the rendered input carries neither identifier today — verified, and recorded as a
  Material risk — so a hard refusal there would make the apply decline the qualified configuration's
  relationship-crossing-key kinds, which is precisely the
  relationship-bearing capability DBR-013 and DBA-008 require. **(AD091 corrects the magnitude: that
  population is five kinds and five mapping entries — `Interface{Physical,Virtual,Lag}`, `IpamPrefix`,
  `IpamIPAddress` — not the ten identity-bearing-reference entries this answer cited, which is a
  configuration-side count standing in for a destination-schema question. The choice is unchanged: a
  refusal would still decline every interface kind on the qualified path.)** Refusing them would be scope destruction
  dressed as rigour. So: the pre-write gate gains a **rendered-mutation check** — the strongest form of the
  claim that is both observable and free — which **refuses** an unkeyed render for a kind whose
  human-friendly ID is **all-direct** (that condition can only mean the payload lost its identity
  components, the AD042 defect class) and, for a kind whose identifier crosses a relationship, emits one
  operator-visible warning per kind naming the recorded risk and proceeds, because the convergent write may
  still key server-side and only live evidence can settle it. The per-component check over the assembled
  data stays as the **diagnostic** that names which component is missing. And the flat guarantee is struck
  everywhere it appeared, replaced by what the gate actually delivers: **no write is issued whose payload is
  missing an identifier component, and an unkeyed render is refused wherever it can only be a defect**.
  `[AD066]`
- Q: The offline conformance harness is required to include a kind whose human-friendly ID crosses a
  relationship, and then to assert that every operation renders keyed. The artifacts' own verified facts
  say that operation cannot render keyed. → A: **Split the assertion so the known hole is a live fact
  rather than prose in a risk table.** For kinds whose identifier is all-direct, every operation MUST
  render keyed, and a payload built from the comparison engine's attribute set alone MUST fail that — the
  AD042 regression detector, unchanged. For the relationship-crossing kind, the same keyedness assertion is
  made and marked a **strict expected failure** citing the recorded Material risk. It fails today, which is
  honest; the day the write surface closes the hole it passes, the strict marker turns that into a suite
  failure, and the limitation retires itself instead of being rediscovered. Dropping the fixture was
  rejected: it is the only offline signal on the path that most needs one. `[AD067]`
- Q: "Two applies produce exactly one create" is asserted against a test double. Can it fail? → A: **No,
  and the task's own diagnosis says so four lines above the assertion.** A double holds no destination
  state; two applies simply issue two creates, and no operation-level deduplication exists or is wanted.
  The assertion becomes the strongest claim that is checkable offline and is the property convergence
  actually rests on: **two applies of the same operation render byte-identical mutation inputs, and keyed
  ones wherever keyedness is assertable at all**. That can fail — a payload that varies between renders, or
  one that renders unkeyed, breaks it — and it regresses if the keyedness split above regresses. Every
  downstream restatement of the old claim moves with it. `[AD068]`
- Q: Both the engine and the command layer write the run record, and the command layer writes last from an
  object whose summary is empty. Who owns it? → A: **The command layer owns the file; the engine owns the
  record.** The run file's save writes the whole payload with no merge
  (`infrahub_sync/cache/sidecars.py:87-89`) and the command layer's instance is constructed with an empty
  summary and saved after the apply returns (`infrahub_sync/cli.py:322-323`, `:350-351`), so as specified
  every key the engine recorded was destroyed — silently deleting FR-020's record, with the tests that would
  catch it two phases downstream. The engine therefore **returns** an apply record and writes no run file;
  the command layer **merges** that record into the run file's summary before saving. A mid-apply rejection
  carries its partial record on the raised error so the command layer can merge what was written before
  recording the failure — without that, FR-025's last-applied pointer could not survive a partial apply at
  all. One sentence pins this in the task that produces the record and in every task that reads it. The
  alternative — moving run-file ownership into the engine — was rejected: it moves a persisted-file
  responsibility across a layer boundary this outcome does not otherwise touch, and the refusal paths and
  AD010 both depend on the command layer keeping it. `[AD069]`
- Q: AD054's third clause corrects the pre-existing additive ordering on the existing update path. That
  path's only caller is the live mutating write path. Does the fix belong here? → A: **No. It is
  withdrawn.** The correction would make the existing `sync` command start **removing** destination
  relationship peers that the source does not carry, on configurations that have never removed one — a
  data-removing change to an existing command, with no requirement stating it, no criterion measuring it,
  no edge case, and no entry in the documentation sweep that does disclose the delete-recording change. An
  operator would meet it in production with its only trace in a unit test's expectation. That the fix is
  obvious and correct does not make it authorized; it is a decision for the brief's owner, and it is the
  same class of unauthorized existing-path scope that AD048 was written to prevent and AD063 had just
  retired. So the replace-set enforcement is **new code on the planned-write path only**, the pre-existing
  path is left byte-for-byte as it is, and its additive ordering is recorded as a **pre-existing defect for
  a future outcome to own**. The small duplication of the compare-and-reconcile logic that follows is
  deliberate and is the price of leaving the existing path untouched. The contradiction between the task
  that changed it and the two tasks that assert the existing path is unchanged goes with it.
  `[AD070]`
- Q: FR-030 names four derivation failures. Do all four have a named failure class carrying a next action?
  → A: **Two do not, and the instrumentation that would have caught that only walks classes that exist.**
  An operation with no formable destination identity has no class at all, and a relationship peer missing
  from the loaded source state has only a class defined as a **destination** miss whose remedy — create the
  peer at the destination — is wrong for the condition, since nothing is missing at the destination.
  Both get their own named class with a next action of their own: one directing the operator at the
  identity attributes of the schema mapping that resolved to nothing, one at the configuration that does not
  load the peer's kind, kept textually distinct from the destination-side miss. The derivation task's
  assertions must cover the **next action**, not only the kind and the cause — this is the most-run command,
  it has never failed on data, and AD047 deliberately gives it no tolerance switch. `[AD071]`
- Q: The negative walkthrough demonstrates an unknown-kind error. Does the command it runs produce one? →
  A: **No — it raises the pre-existing-format error instead, and because it errors, it looks like it
  passed.** The case reads a run whose plan directory two commands earlier removed, and the kind it names
  is not one the qualified configuration declares, so even repaired it would exercise the reader's
  undeclared-kind branch rather than the renderer's declared-but-empty one. Those two branches were split
  deliberately by AD058 and this is the only hand-run demonstration of either. The fix: every review case
  runs **before** the step that removes the plan directory, the destructive steps go last, each case is
  **labelled with the branch it exercises**, and the declared-but-empty case names a kind the configuration
  actually declares. This is the same false-pass shape AD060 was created to eliminate, reappearing in the
  file AD060 repaired, which is why it is recorded rather than quietly fixed. `[AD072]`
- Q: An unknown run identifier is answered by listing the run identifiers that exist. Is that listing
  bounded, and what does it do when there are none? → A: **Unbounded, and it raises.** The cache root is
  computed, never created or checked (`infrahub_sync/cache/paths.py:26-43`), so for a sync that has never
  run the "one directory listing" both contracts describe as safe raises instead — and the operator most
  likely to meet that is the first-time one, getting a traceback from a helpful-error path. At the other end
  nothing prunes a run directory anywhere in the repository and retention is explicitly out of scope, so an
  hourly pipeline turns the most common typo in the feature into thousands of lines. The enumeration
  therefore lists the **most recent twenty** identifiers, with the total count stated when it truncates
  (they sort by time by construction), and when the cache root is absent or holds no runs the message
  **says so plainly** and its next action **names the command** that produces one. The no-runs case is
  tested. `[AD073]`
- Q: On what authority was the derived pair re-derived? → A: **Not the one AD055 recorded.** The correction
  is carried at AD055 itself: the authority is the brief owner's override at the delivery gate, the batch's
  derived-requirement policy is cited only for the basis proviso, and the two brief passages that now read
  false are named for a planner revision. AD074's second, override-free ground is **withdrawn at AD077**:
  the override was necessary, not confirmatory. `[AD074]`

### Session 2026-07-27 — critique round three ratified

Ten decisions were ratified at the delivery gate after the third and final critique round. Two of the
three lenses returned **no** blocking finding and both answered the implement-or-not question with
"implement"; the third returned one, AD075, which had to close before implementation began. All ten
correct this run's own delivery and **none adds anything that ships**. AD075 is a defect in the repair of
a defect in a repair, on the one mechanism this outcome's headline relationship guarantee rests on, which
is why its closure was verified by a narrow check against the library rather than by a fourth round.

- Q: The replace-set enforcement discards the locally held peer set, re-reads the destination's, compares,
  removes the surplus and adds the shortfall — and then the sequence ends. Does the reconciled set reach
  the destination? → A: **No. It is computed correctly and thrown away.** The destination library's
  relationship manager has **no** save of its own: both editors only mutate the in-memory peer list and
  set an update flag (`.venv/…/infrahub_sdk/node/relationship.py`, `add` `:322-332`, `remove` `:339-357`,
  the flag read at `:57`). The reconciled set reaches the destination only on a **subsequent write of the
  node**, which is exactly how the pre-existing shape works — the module-level updater returns the node
  unsaved (`infrahub_sync/adapters/infrahub.py:177`) and its caller flushes it (`:626`). The specified
  sequence had no such step, so it reconciled in memory and returned. The enforcement therefore gains an
  explicit flush, specified as a **plain** node save rather than a repeat of the convergent upsert: the
  create step marks the node existing (`.venv/…/infrahub_sdk/node/node.py:1811`), so a plain save
  dispatches an update (`:1533-1534`), the update renders with unmodified fields stripped (`:1867-1870`),
  and the stripping **retains** the relationship precisely because its update flag is set (`:352`, the
  relationship arm at `:362`) — while the manager renders the **full** peer list
  (`relationship.py:68-69`), which is what makes the write a replace. A second upsert would re-render the
  create instead. **And the flush must save the node whose manager was reconciled**, which narrows AD065's
  two re-read routes to one now that the flush is a separate step in the caller: reconciling the manager on a
  separately fetched node leaves the saved node's own manager as the payload-built one, with no update flag,
  so the stripping arm cited above pops it and the update carries no relationship at all — this same defect,
  silently. Discarding the locally held peer set and fetching keeps the two on one object, because that fetch
  assigns the peers it reads back onto the manager it was called on (`relationship.py:290-299`).
  And the observable moves off manager state onto **the issued destination write carrying
  the reconciled peer list**, which is the same correction AD065 already made for the read side: every
  previously specified observable — a mocked unit assertion on removed peers, a conformance assertion that
  "the surplus is removed", a done-condition on manager state — was satisfied without any flush at all,
  and the only criterion that would have caught it is behind the opt-in integration marker and is not
  produced at merge. The failure is co-extensive with the risk the step exists for: where the convergent
  write already replaces the peer set there is nothing to flush and the omission is invisible; where it
  **merges** — the case this enforcement exists for — the surplus is computed and discarded.
  **Amended by AD085**: the conclusions above stand, but the flush is a **full** update rather than a plain
  node save, because the default suppression of unchanged fields drops a peer set reconciled to empty.
  **Amended again by AD088**: the conclusions above still stand, but neither form of a whole-node update is
  the flush. Both render the whole node, and that render nulls every unmapped optional cardinality-one
  relationship of a node the library considers existing — so the flush becomes a targeted write of the
  replaced relationship fields alone. **The defect was latent in this decision's own design, not introduced
  by AD085**: this decision specified the flush as a write *of the node*, and the null follows from that
  under either suppression setting. `[AD075]`
- Q: The keyedness gate is specified as a branch on the destination kind's convergence-key shape, read on
  the rendered mutation input. Are both halves right? → A: **Neither is, and both are corrected.** First,
  the gate is **stricter than the claim it serves** for a kind that declares no convergence key at all:
  under the natural reading of "every component is direct" over an empty component list, such a kind lands
  in the refusing arm and is refused with a diagnosis that is false, since no component went missing.
  FR-024 explicitly permits that kind and requires the plan run to survive it, and the narrowed guarantee's
  own words — unkeyed is refused only where it *can only be a defect* — exclude it, because for such a kind
  being unkeyed is a schema fact. So it becomes a third case: reported, not refused. Second, the
  **accessor named for the rendered mutation is one level short of the payload**: the render call returns a
  mapping whose `"data"` member is itself `{"data": …}` (`.venv/…/infrahub_sdk/node/node.py:300`,
  `:304-308`), so a check for the identifier keys against `…["data"]` tests a one-key mapping and is true
  for **every** operation, which would make the refusing arm fire on all of them. The correct expression
  reaches `…["data"]["data"]`, and it is corrected in all four places it is written as executable
  pseudocode. `[AD076]`
- Q: AD074 recorded a second ground for the delete re-derivation that would need no override. Is it sound?
  → A: **No, and it is withdrawn.** The reasoning and the consequence are recorded at AD055 and in
  [Derived brief items re-derived here](#derived-brief-items-re-derived-here): the brief's own acceptance
  criterion and user scenario both apply the phrase "the unsupported operation" to a recorded delete, so
  the brief settles its own term against the reading and the withdrawn ground would leave that phrase
  without a referent. The brief owner's override at the delivery gate was therefore **necessary**, not the
  better-cited of two available routes, and nothing in this run is precedent for re-deriving a derived
  brief requirement without one. `[AD077]`
- Q: AD066's per-kind report is the disclosure that justified not refusing the relationship-crossing case.
  Is it specified to the standard this run already applied to the skipped-delete warning? → A: **No, and it
  is brought to it.** One round earlier this run ruled "operator-visible" insufficient and pinned the
  delete warning to a level the quiet verbosity floor cannot suppress, with a test asserting the level and
  not only the text; the same phrase was then used for this report and nothing else was supplied. So: the
  level is pinned on the same reasoning; the content is specified as the destination kind, that the write
  was issued anyway, and what to watch for — a duplicate of that kind at the destination if it does not key
  on the components as sent — rather than "the recorded risk", which names an artifact the operator does not
  have; the per-kind deduplication state is given a home with a stated lifetime, since the write surface is
  entered once per operation and "once per kind" needs state across them; a task asserts the report's
  existence, its level and its per-kind cardinality across two operations of one kind; and the docs sweep
  gains a clause stating that convergence is not verified in this release for destination kinds whose
  convergence key crosses a relationship — the one part of this that reaches an operator, and the part the
  sibling delete limitation already had. `[AD078]`
- Q: The local track of the run guide is headed "local, no servers". Does every step under it run without
  one? → A: **No.** The third CLI-sanity command reaches the destination for its schema and exits non-zero
  with an unreachable-server error, verified by execution. The contradiction is inherited from the
  repository's own contributor guide, which lists that command under post-change sanity checks and
  separately records that it needs a running server; the run guide copied the first without the second, on
  a command this outcome does not touch. It is annotated as requiring a reachable destination and excluded
  from the local track. `[AD079]`
- Q: The live-track walkthrough's last step promises "apply again — converges, no duplicates". Is that
  what AD066 leaves? → A: **Not unconditionally.** That walkthrough runs the qualified configuration,
  whose relationship-crossing identities are exactly the population the narrowed gate excludes, so for
  those kinds convergence is what the deferred criteria **measure**, not what the guide may assert. The
  step and the two criteria rows above it carry the caveat, and it names the recorded limitation so a
  maintainer who sees a duplicate does not read it as a regression they introduced. The accurate statement
  already sits in the same file's preamble; this makes the two agree. `[AD080]`
- Q: The brief records the destination adapter's convergent write path as a **Satisfied** dependency. Does
  this delivery still support that? → A: **Only in part, and the repair is the planner's.** The claim holds
  where the destination kind's convergence key is composed of its own direct attributes; where a component
  crosses a relationship the client cannot form the key from a peer supplied as a resolved identifier, so
  the write goes out unkeyed — **five kinds, five mapping entries** of the qualified path (AD091 corrects
  the "ten entries across nine kinds" this answer first gave, which counted plan identities containing a
  reference rather than destination keys that cross a relationship). The dependency
  row should be scoped to the all-direct case and recorded as unverified for the relationship-crossing one,
  with an impact-if-wrong in the brief's assumptions, and three later outcomes consuming this apply path
  inherit the same partial satisfaction. This is recorded for planner feedback only: **nothing here edits
  the brief**, and the delivery's own disclosure of the limitation is unchanged and adequate. `[AD081]`
- Q: One derivation-failure class is raised for two conditions. Does its taxonomy row cover both? → A:
  **No.** The class is raised both when a relationship peer is absent from the loaded source store and
  when a bounded kind probe returns more than one candidate, but the row defines only the absent case and
  writes its next action for that case — so an operator holding an ambiguous peer is routed at a condition
  they do not have. This is the same defect AD071 was created to remove, one size smaller, inside the class
  AD071 created. The row gains the ambiguity arm with its own next action; splitting the class was the
  alternative and one row is cheaper than two classes. `[AD082]`
- Q: How does the author of an adapter other than the destination adapter learn that a planned-write
  surface now exists? → A: **Today, only from a refusal message, and that is not good enough.** The
  pre-write refusal is graceful and points at the existing mutating command, so nothing breaks — but the
  guide an adapter author actually reads would not mention the surface at all. A documentation task now
  points them at it, alongside the other documentation obligations of this outcome. Three rounds of
  critique raised this and none routed it. `[AD083]`
- Q: The decisions above were carried behind provisional markers pending ratification. The gate has now
  passed — what happens to the markers? → A: **They are removed, and the sessions read as ratified.** The
  ratifying event is this delivery gate, named as such at the head of the Clarifications section. The
  decision records themselves are untouched and each keeps its identifier and its revisit set, because the
  revisit set is what makes a later reopening tractable and is useful independently of ratification; what
  is removed is the provisional framing, the sentence that explained the marker convention, and the
  sentence that made the markers the ratification handles. `[AD084]`

Three housekeeping repairs were applied in the same pass and carry no decision of their own: two
cross-references to the offline harness still cited an assertion number the AD067 split had moved; the
apply record crossing the engine-to-command boundary had a key list but no named type; and the no-stored-
runs next action did not name the command its sibling taxonomy row names.

### Session 2026-07-27 — the empty peer set, amending AD075

One decision, raised from implementation and closed by the brief owner. It **amends AD075** and nothing
else: no requirement, criterion, scope or guarantee moves.

- Q: AD075 specified the replace-set flush as a **plain** node save, on the stated ground that the
  unmodified-field stripping retains a relationship whose update flag is set. Does that hold for a
  relationship reconciled to the **empty** set — the case the plan format defines as "empty the set"? → A:
  **No, and the flush changes to a full update.** What was wrong is only AD075's *stated mechanism*, and
  only for the empty-set case. The stripping runs in two loops. The **first** behaves as AD075 said: an
  emptied manager whose update flag is set is not popped there, and the guard that would skip it never
  fires, because the relationship manager defines neither `__bool__` nor `__len__` and is therefore always
  truthy. The **second** loop is the collision: it pops any key whose rendered value equals the create
  payload's, and the create payload writes an empty list for a cardinality-many relationship, so the
  comparison is `[] == []` and the pop fires — a relationship manager is not an attribute, so the guard that
  protects a mutated attribute does not protect it. (The differing-payload path is **not** where the key is
  lost; the dictionary-stripping helper it would go through is dispatched only for a mapping, and a
  cardinality-many relationship is written as a list, so the key survives there. An earlier account of this
  said otherwise and was wrong.) The consequence is that under a plain save an emptied peer set never
  reaches the destination, which voids the enforcement in exactly one of the two cases it exists for. The
  flush therefore becomes a **full update** — the destination library's update with full-update requested,
  which renders with the stripping switched off, so the stripping never runs at all, the emptied set
  survives, the node identifier is still rendered so the write targets the reconciled node, and the
  mutation is still an update rather than a second convergent upsert. **AD075's conclusions and its shipped
  implementation stand**: the manager still has no save of its own, the editors are still purely local, the
  flush is still issued once after the loop and still on the node whose manager was reconciled, and the
  observable is still the issued destination write. Non-empty replaces are unaffected either way. Two test
  obligations follow. The empty-set case must be asserted on the **rendered mutation** — a real node over a
  real schema — not on a mocked adapter call, which is what let the defect through the first time. And an
  **SDK-boundary tripwire** must fail loudly, naming this decision, if the library's stripping behaviour
  changes, because the dependency is pinned as a version range and the behaviour is undocumented internals.
  **Amended by AD088**: the reading of the stripping recorded here is correct and stands, but the remedy is
  withdrawn. The full form of the update suppresses nothing *and* re-renders the whole node, which nulls
  every unmapped optional cardinality-one relationship; the flush becomes a targeted write of the replaced
  relationship fields instead. **AD088 also corrects the attribution of that null: it did not arrive with
  this decision.** The null goes out under a plain save too, so it was present from AD075's original flush;
  what this decision changed is which suppression setting the whole-node render runs under, and the null is
  independent of that setting. `[AD085]`

### Session 2026-07-28 — the write surface as a type, and a shipped release note

Two decisions, both raised from implementation review and closed by the brief owner. Neither adds,
expands, removes or reassigns product scope; no requirement, criterion or guarantee moves.

- Q: The destination write surface is reached by attribute name — presence tested by name, the write
  dispatched by name, and the peer resolver built by narrowing the destination to the one adapter that
  implements it. Nothing about any of that is checked. What replaces it? → A: **A runtime-checkable
  structural type with two members — the write surface itself and a peer-resolver factory — and the
  pre-write check becomes a type check against it.** The factory joins the surface because the engine
  has to build the per-apply resolver without naming a concrete adapter, and that narrowing is what
  the removed cast was doing. The destination adapter gains the factory, so it satisfies the type. The
  refusal still receives the **adapter's name** rather than a boolean, so the message FR-023 requires
  is unchanged. **The honest limit, which this decision states rather than glosses: a type check
  against a runtime-checkable structural type verifies member *presence*, never signatures. Against a
  duck-typed destination it is exactly equivalent to the by-name presence test it replaced — no
  stronger. FR-023's refusal is still presence-checking, and nothing here hardens it.** What is
  genuinely fixed is the **static** boundary: the type checker now verifies every call site and the
  resolver's parameter, and the unchecked by-name dispatch is gone. Making FR-023's refusal real at
  runtime requires an explicit opt-in from the destination — inheritance from an abstract base, or a
  class-level marker — which is a **separate design decision this one does not take, does not absorb
  and does not imply is done**. It is reported to the planner as a brief gap instead. Overclaiming a
  safety property in a decision record is the same error class as AD087's; this decision refuses to
  commit it while fixing that one. Two test obligations follow: the destination missing either member
  is still refused **before any write** and still named; and the presence-only limit is **asserted**,
  by a destination whose members carry the right names and the wrong shapes passing the gate and
  failing later, so a reader cannot mistake the type for enforcement. `[AD086]`
- Q: A current-documentation page and a **shipped 2.0.0 release note** both claimed the apply path
  refuses on schema-sub-hash drift, which it does not; the current page was corrected and the
  sentence was also deleted from the release note. Does the release-note edit stand? → A: **No — it is
  reverted, and the sentence returns exactly as shipped.** A shipped release note records what that
  release claimed, at the time it claimed it; it is not current documentation and is not a place where
  a claim gets quietly corrected. The remedy for a false claim in a shipped note is an erratum or a
  fix in the code the note described, and choosing between those is not a plan-artifact delivery's
  call — it is out of scope for this brief either way. **Every current-documentation correction
  stands, the cache-layout reference above all: that is where the false claim actually misled a
  reader, and fixing it there was correct.** No other documentation file is reverted. The scope
  boundary this went past — documentation edits are limited to current documentation and never touch
  shipped release notes — was nowhere in the brief, and is reported to the planner as a brief gap.
  `[AD087]`

### Session 2026-07-28 — the flush as a targeted write, amending AD085 and correcting its attribution

One decision, raised by convergence assessment as a CRITICAL defect and closed by the brief owner. It
**amends AD085's flush form**, **corrects the attribution of the defect**, and adds nothing that ships:
no requirement, criterion, scope or guarantee moves, and no product scope is added, expanded, removed or
reassigned.

- Q: FR-013 carries two MUSTs that cannot both be satisfied. It requires the replace-set flush to "request
  the full form of the update, which suppresses nothing" (AD085), and it requires that "an update payload
  ... MUST NOT touch unmapped destination fields". The full form re-renders the whole node, and the
  destination library renders `<rel>: null` for every **unmapped optional cardinality-one** relationship of
  a node it considers existing — deliberately, to let a caller clear one — while the convergent write marks
  the node existing. So every applied operation carrying a relationship silently clears destination fields
  the plan never mapped. Which clause gives? → A: **AD085's does. The flush becomes a targeted write of the
  replaced relationship fields — the node's identifier plus the cardinality-many fields being replaced, and
  nothing else — and never a re-render of the node.** Both prior clauses are named in FR-013 as having
  contradicted each other, so the contradiction is visible in the history rather than overwritten: the
  withdrawn clause is AD085's "request the full form of the update, which suppresses nothing"; the clause it
  contradicted is FR-013's own "MUST NOT touch unmapped destination fields". A targeted write satisfies both
  at once — an emptied peer set is written explicitly rather than left to survive a comparison, so nothing is
  suppressed, and no unmapped field is in the payload to be nulled. **Pre-initialising or restoring the
  unmapped relationships before a whole-node update is rejected**: it treats the symptom, and it would make
  the guarantee depend on first reading every unmapped relationship of the destination object.
  **Everything AD065 and AD075 established is unchanged**: the destination peer set is still re-read cold
  before comparison, the write is still issued once after the loop, and it is still issued for the object
  whose manager was reconciled. The write is still an ordinary update of the already-written object rather
  than a repeat of the convergent write.
  **The attribution in the convergence finding was wrong and is corrected here. AD085 did not introduce
  this; it was latent in AD075's original design.** Both paths were verified independently. Under a plain
  save (suppression on) the null still goes out: the first stripping loop does not pop the field, because
  that pop needs a non-optional related-node or a relationship manager and an uninitialized optional
  cardinality-one relationship is neither; and the second loop never visits it, because an unmapped field is
  absent from the original data that loop compares against. Under the full update (suppression off) nothing
  is stripped at all. The null is therefore independent of the suppression setting, and what AD085 changed
  was only that setting. AD075 specified the flush as a write **of the node**, and the null follows from
  that alone.
  Two test obligations follow, and the first constrains a **fixture** as much as an assertion. The offline
  conformance check must assert that the issued flush names no destination field the operation did not map,
  and its committed schema fixture must therefore declare, on the kind under replace-set, at least one
  optional cardinality-one relationship no operation maps — the existing fixtures declared only a
  cardinality-many relationship, which is exactly why the harness could not see this. And the SDK-boundary
  tripwire AD085 asked for is **re-pointed** at the render behaviour this decision depends on, with a
  failure message that says plainly that the library's render behaviour has changed and names the decisions
  resting on it. That render has now produced two defects on this one path, so the tripwire is pinned there
  rather than at the suppression behaviour, which no shipped call now depends on. `[AD088]`

### Session 2026-07-28 — the live tests were non-functional, not merely unexecuted

One decision, raised by the first run of the Phase H tests against a live destination and closed by the
brief owner. It repairs a **test fixture** and corrects a claim this run made about its own evidence. No
requirement, criterion, scope or guarantee moves; no product scope is added, expanded, removed or
reassigned; no assertion in Phase H is weakened; the live `sync` write path is untouched (AD070).

- Q: Run against a live destination, all eight Phase H tests error identically in fixture setup with
  `ValueError: An error occurred while loading Netbox: 'NetboxAdapter' object has no attribute
  'BuiltinTag'`. What is missing? → A: **The generate step. The fixture wrote its bounded configuration
  into a temporary workspace and never generated adapter code there, so it is added: after
  `_write_bounded_config`, the in-process equivalent of `infrahub-sync generate` renders into the same
  workspace, and the existence of both `netbox/sync_adapter.py` and `infrahub/sync_adapter.py` is a
  `LivePlanPreconditionError` when it does not hold.** `import_adapter` (`infrahub_sync/utils.py:72-98`)
  resolves the per-kind model classes from **generated** code at `<config directory>/<adapter
  name>/sync_adapter.py`; when that file is absent it falls back to the plugin loader and returns a bare
  adapter with no per-kind attributes, so `DiffSyncMixin.load` fails on the first `getattr`. **This is not
  a missing kind and not a fixable pointer at `examples/`**: `examples/netbox_to_infrahub/netbox/sync_models.py`
  does define `BuiltinTag`, and the adapters checked in there are stale anyway — they declare `InfraDevice`,
  `InfraRack`, `LocationGeneric` and `OrganizationGeneric` while the same directory's `config.yml` maps
  `DcimDevice`, `DcimDeviceType`, `LocationRack`, `LocationSite` and `OrganizationManufacturer`. Generated
  in process rather than by shelling out: it is the same pair of calls `generate` makes once it holds a
  schema (`find_missing_schema_model` then `render_adapter`, `infrahub_sync/cli.py:781-787`), the fixture
  already holds the destination schema, and a subprocess would add a second schema round trip and replace a
  typed precondition error with an exit code to parse. **One render is shared by the seed configuration and
  the configuration under test, from the wider `KINDS_UNDER_TEST` slice, and that is not a convenience** —
  the generated adapter reaches its models through `from .sync_models import ...`, so the first import
  caches `<adapter>.sync_models` in `sys.modules` and a later re-render of the same package is never seen:
  rendering per configuration makes the widened import fail, `import_adapter` swallows it as a warning, and
  the bare-adapter fallback returns with the original defect restored **silently**. Both halves were
  verified offline against a stubbed destination schema: with no generated adapter the source class is
  `NetboxAdapter` with no `BuiltinTag`; after the render it is `NetboxSync` carrying all seven kinds from
  `<workspace>/netbox/sync_adapter.py`; and a per-configuration re-render regresses to `NetboxAdapter` with
  zero per-kind attributes. Sharing is sound because the retained schema-mapping entries are copied
  verbatim, so every kind the seed maps renders identically from either configuration, and model classes a
  configuration does not map are inert — `DiffSyncMixin.load` walks the configuration's own `top_level`.
  **The record this decision corrects: "authored, not satisfied" (AD045b) was too weak a claim.** The Phase
  H tests were not merely unexecuted — they were **non-functional**, and only execution could reveal that.
  A test that has never run is not evidence of anything, including of its own validity. Everything AD045b
  concluded about the *coverage* still stands and none of it is softened; what was wrong was the implied
  floor, that an authored test is a test whose only missing ingredient is a destination. The transferable
  lesson belongs in the record rather than in the fix: an unexecuted test's validity is itself unevidenced,
  so a run may not report authorship as though it bounded the risk. The module docstring is amended to say
  so at the point a reader meets the claim. `[AD090]`

### Session 2026-07-28 (second) — V30's magnitude figure was read off the wrong schema

One decision, raised by querying the live destination schema directly. It corrects a **code fact** this
run recorded and every claim that rested on it, and it widens a **test fixture** so its own precondition
can be met. No requirement, criterion, guarantee or scope moves; no product scope is added, expanded,
removed or reassigned; no assertion or precondition in Phase H is weakened; the live `sync` write path is
untouched (AD070).

- Q: V30 says ten schema-mapping entries across nine kinds carry a relationship-crossing convergence key,
  and AD066, AD067, AD076, the Principle II tension, the nested-HFID risk and the filter-spelling risk all
  quantify themselves with it. Is that the right figure for those claims? → A: **No. The figure was
  derived from the *configuration's* `identifiers` lists containing references; what governs keying and
  filtering is the *destination kind's* `human_friendly_id`, and that is a different and smaller set.**
  Both facts are real and V30 now states them separately. Configuration-side: ten entries across nine
  kinds, which is what AD043's nested `{peer_kind, identity}` shape turns on and where the figure stays.
  Destination-side, verified against the live destination: of the **20 kinds** the qualified configuration
  maps or references (21 entries), **five kinds — five entries — have an HFID that crosses a
  relationship**: `InterfacePhysical`, `InterfaceVirtual`, `InterfaceLag` (all
  `['device__name__value', 'name__value']`), `IpamPrefix` (`['ip_namespace__name__value',
  'prefix__value']`) and `IpamIPAddress` (`['ip_namespace__name__value', 'address__value']`). The other
  fifteen are all-direct, and they include **four of the nine** the old figure counted — `LocationRack`,
  `DcimDeviceType`, `DcimDevice` and `IpamVLAN` — which are keyed on `name__value` alone and converge
  normally. All 20 also declare a uniqueness constraint. A further split inside the five: only the three
  interface kinds' plan identities *supply* the crossing component (`identifiers: ["device", "name"]`);
  `IpamPrefix` and `IpamIPAddress` supply no `ip_namespace` at all, so for those two AD051's per-component
  diagnostic refuses **before** the write rather than AD066's gate warning and proceeding.
  **The query, exactly**: `client.schema.all(branch="main")` against the live destination, then
  `human_friendly_id` and `uniqueness_constraints` read off every kind named in `schema_mapping[].name` or
  `schema_mapping[].fields[].reference` of `examples/netbox_to_infrahub/config.yml`; a component is
  relationship-crossing when `"__" in component.removesuffix("__value")`.
  **The risk stays; only its reach shrinks.** It is real for the three interface kinds and it was not
  wished away — the live run confirms the render is unkeyed there, seventeen keyedness warnings naming
  `InterfacePhysical`. What was wrong was the overstatement, and the corrected records say which figure
  answers which question so the two cannot be conflated again.
  **Only live data exposed it.** The proxy was flagged as a proxy in the third fidelity critique (R3-N2)
  and carried anyway, because with no reachable destination there was nothing to check it against; a count
  read from the artifact under review looked like a verified fact for as long as the schema it described
  was unreachable. The transferable lesson: a magnitude quoted in a justification must name the artifact
  it was read from, because a figure read from the wrong one is indistinguishable from a figure read from
  the right one until something can be queried.
  **Consequently the Phase H slice widens.** Its own `_require_preexisting_peer` precondition refused
  every test, correctly: with `DcimDevice` as the widest kind, every referenced kind was all-direct, so
  SC-008's nested identity walk and PD-004's nested filter spelling would have passed vacuously.
  `InterfacePhysical` joins the slice as the relationship-bearing kind under test and `InterfaceLag`
  (reached through `bundle`, HFID `device__name__value`) becomes the pre-existing crossing peer, which
  moves `DcimDevice` and `InterfaceLag` into the seed. Two bounding filters are added and documented at
  `ADDED_FILTERS`, one for size and one because `LocationRack` is not convergent against this destination
  schema — a separate defect this run found and recorded in plan.md's Risks rather than fixed. `[AD091]`

### Session 2026-07-28 (third) — an unsatisfiable-by-schema precondition is a skip, not an error

One decision, decided by the brief owner. It changes how a **test fixture** reports a condition it cannot
establish. No requirement, criterion, guarantee or scope moves; no product scope is added, expanded,
removed or reassigned; no assertion in Phase H is weakened, deleted or mocked; the live `sync` write path
is untouched (AD070).

- Q: `test_an_ambiguous_peer_refuses_the_operation` (SC-016's live half) errors in fixture setup, and T080
  is left open as undelivered. The cause is settled: seeding a genuinely ambiguous peer needs a referenced
  kind whose uniqueness constraints do not cover the components the resolver filters on, and every one of
  the 20 kinds the qualified configuration touches declares one that does (V30, AD091), so the destination
  refuses the clone with `Violates uniqueness constraint 'device-name'`, HTTP 422. Is an erroring fixture
  the right report for that? → A: **No. That is a property of the schema, not a defect and not an absent
  environment, so it is stated as an explicit precondition that skips with the reason in the message.**
  The guard is `_ambiguous_peer_or_skip`, over the pure `covering_uniqueness_constraint`: for every peer
  the plan references and does not create — the module fixture's chosen pre-existing peer first — it asks
  the **destination schema** whether any declared uniqueness constraint is fully pinned by the filters the
  resolver queries that kind with. A component is pinned when the filters name it directly
  (`name__value`), or, for a relationship component named on its own (`device`), when the filters reach
  through it far enough to identify a single peer — decided by applying the same test to the peer kind,
  which is what stops `device__name__value` being read as pinning `device` on a destination where device
  names are not unique. The first candidate whose constraints leave a filtered component free is seeded
  and the test runs in full; only when none does is the run skipped, with the candidate kinds, their
  resolver filters and the covering constraint in the message. It follows the skip pattern the module
  already establishes for a condition outside its control (`_env_or_skip`), and it is deliberately *not*
  `LivePlanPreconditionError`, which stays for a fixture that should have been satisfiable and was not.
  **This is materially different from both a mock and a silent skip, and the difference is checkable.**
  A mock would supply the ambiguity the destination refuses and assert against a fiction; nothing here is
  faked — the check reads the destination's own schema and every assertion in the test body is untouched.
  A silent skip states no reason and can never become a run; this one carries the reason a reader can
  reproduce (query the schema, read `uniqueness_constraints` off the referenced kinds) and **turns back
  into a run** the moment a schema admits an ambiguity. A skip that cannot ever turn back into a run is a
  deletion, so the guard is covered offline in `tests/test_live_fixture_preconditions.py` — four cases over
  schema doubles, one of them the schema that *does* admit an ambiguity and therefore runs the test —
  which is the same standard AD045b's precondition cover set for `assert_convergence_key_is_supplied`:
  the check that decides run-versus-skip may not itself be carried only by the runs it guards.
  **What is delivered and what is not.** T080 is ticked for a test that is written, unweakened and
  guarded by a schema precondition, and its text says exactly that: on this destination it **skips**, with
  the reason. The **live passing evidence** for SC-016's live half is still not produced and cannot be on
  this destination; every record that says so keeps saying so, unsoftened. SC-016's offline half (T053)
  passes, so the requirement is not unevidenced — only its live half.
  **Scope of the completion condition.** SC-016 is a **spec-derived** criterion with no counterpart in the
  brief's own acceptance list, so neither this decision nor the state it records touches the brief's
  completion condition; it changes only how this repository's own criterion reports an unreachable
  environment fact. `[AD092]`

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
   recorded `failed` rather than reaching `status: applied`. `[AD010]`
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
   succession **with both runs having extracted the same way on each side**, **Then** the operations
   section and the manifest are byte-identical, excluding only the fields that necessarily vary per
   run. The same-extraction-mode condition is stated here rather than left implicit, because the
   manifest's delete-computation record sits inside the checksum and outside the two masked fields, so
   two runs at different extraction modes are expected to differ and comparing them would make this
   scenario's own evidence unsound. `[AD064]`
6. **Given** a plan directory copied out of one run and into another run's directory, **When** an
   apply is requested for the receiving run, **Then** the apply is refused because the manifest's
   recorded run identifier does not equal the run being applied, and no destination write occurs.
   `[AD012]`

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
that plan executes every non-delete operation, does not delete the object, and completes
successfully — with the number of deletes it did not execute recorded on the run and named in a
warning the operator sees.

**Why this priority**: Recording deletes is a deliberate change to what the plan shows, and the
gap between "recorded" and "not applied" must be loud. A silent skip would leave the applied set
merely inferable from the reviewed set instead of provably knowable against it, which contradicts the
outcome. Recording the count and the identifiers of what was skipped is what makes the difference a
value rather than a guess — and a limitation this release designed in is not the same thing as a run
that went wrong, so the apply completes rather than failing. `[AD055]`

**Independent Test**: Plan against a source from which a destination-present object has been
removed, confirm the delete operation appears in the artifact with an identifier, apply, and
assert destination object counts before and after, the recorded run state, the recorded
skipped-delete count and identifiers, and the warning naming that count.

**Acceptance Scenarios**:

1. **Given** a source dataset from which an object present in the destination has been removed,
   **When** a plan run completes and that plan is applied, **Then** the plan artifact contains a
   delete operation for that object with a stable operation identifier; the apply executes every
   non-delete operation, does not delete the object, and completes in an **applied** state, with a
   non-zero skipped-delete count recorded on the run alongside the skipped operations' identifiers,
   and a warning the operator sees naming that count. `[AD055]`
2. **Given** an applied plan that contained deletes, **When** the operations recorded as applied are
   compared against the operations that were reviewed, **Then** every operation the two sets differ by
   is accounted for by the recorded skipped-delete identifiers, so the difference is a recorded value
   rather than an inference. `[AD055]`
3. **Given** a plan carrying an operation whose action this release does not recognize at all,
   **When** an apply is requested, **Then** the apply is refused before any destination write, naming
   the operation identifier, the unrecognized action and the operator's next action, and the run is
   recorded `failed` — which is the case a designed limitation is not.
   `[AD055]` `[AD059]`

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
  engine already has today. The check tests that the destination **offers** the surface's members; it
  cannot tell a destination that offers them in the wrong shape from one that implements them, and it
  is not claimed to. Enforcing conformance at runtime would need an explicit opt-in from the
  destination and is a decision this outcome does not take. `[AD086]`
- **Empty plan.** A plan with zero operations is a valid artifact; applying it is a successful
  no-op. It is recorded as a present-but-empty operations section with a count of zero, which is
  what keeps it distinguishable from the torn case below.
- **Torn artifact.** A plan whose manifest exists but whose operations or source snapshot are
  absent or truncated is refused, not partially applied. An operations section whose length
  disagrees with the count the manifest records is torn. A source snapshot file the manifest
  records but which is absent, or whose recomputed logical-row digest or row count disagrees with the
  recorded value, is likewise torn. A `plan/` directory present without a complete manifest is torn,
  not a v1 plan. `[AD008]` `[AD014]` `[AD037]`
- **Identifier collision.** Two operations must never share an operation identifier within one
  plan. Because the action vocabulary is closed to `create | update | delete` and relationship
  changes travel as references on the owning create or update, exactly one operation exists per
  (action, kind, destination identity), so a collision genuinely means two operations address the
  same object with the same action — a pathological plan. The plan run fails rather than emitting a
  plan whose identifiers do not address one operation each. `[AD009]`
- **Destination kind with no usable convergence key.** Two distinct conditions, both warned about at
  plan time, naming the affected kind. Convergence rides on the destination kind's human-friendly ID:
  the convergent write is keyed on it, and an absent or incomplete human-friendly ID makes the write
  unkeyed, which produces duplicates. Separately, a destination kind may declare a complete
  human-friendly ID and still have **no uniqueness constraint** over the plan's identity attributes,
  in which case it also duplicates silently — that is the brief's own condition and it is checked in
  its own right, not treated as covered by the first. Documenting either as a precondition is not
  sufficient, because the failure is silent data duplication. `[AD017]`
  `[AD044]`
- **Relationship peer that does not resolve at apply.** A planned relationship reference whose peer
  matches no destination object refuses that operation and fails the run, naming the peer kind, the
  peer identity, and the referring operation identifier. A reference whose peer identity matches
  more than one destination object refuses, naming the peer kind, the peer identity, and the match
  count. Neither case is ever a silent skip. Both refusals belong to the saved-plan apply resolver
  only; the live write path's existing warn-and-continue on an unresolvable peer is unchanged.
  `[AD016]` `[AD048]`
- **A plan-derivation failure on the non-mutating path.** An operation with no formable destination
  identity, a peer that cannot be resolved in the loaded source state, an unrepresentable payload
  value, or a duplicate operation identifier fails the command with an error naming the destination
  kind and the cause — on the non-mutating command exactly as on the mutating one. The mutating
  command's error-tolerance option is not extended to the non-mutating one and derivation never
  degrades to warn-and-skip, because a silently incomplete plan is the divergence the whole feature
  exists to prevent. `[AD047]`
- **Partial apply.** If apply stops partway — meaning it terminates in-process with a reported
  error — the operations already written stay written and the run records, best effort, the last
  operation it reported as applied. The record is explicitly not required to survive abnormal
  process termination. Durable crash-surviving progress and resumption are out of scope.
  `[AD011]`
- **A recorded delete at apply time.** The apply executes every non-delete operation, executes no
  delete, and completes in an applied state. The number of deletes it did not execute is recorded on
  the run alongside their identifiers, and a warning the operator sees names that count. This is a
  designed limitation of this release rather than a run failure: applying deletes is explicitly out of
  scope and assigned to a later outcome, so a run that behaves exactly as designed is not reported as
  having gone wrong. What the recording preserves is that the applied set stays provably knowable
  against the reviewed set — the two differ by exactly the recorded skipped identifiers, which is a
  value rather than an inference. `[AD055]`
- **An operation whose action this release does not recognize.** Distinct from a recorded delete, and
  the case that does fail. An operation carrying an action outside the closed vocabulary is refused
  before any destination write, naming the operation identifier, the action found, the actions
  recognized, and the operator's next action; the run is recorded `failed`. Nothing about such an
  operation is designed, so its effect on the destination is unknown and the run cannot claim to have
  applied what was reviewed. `[AD055]` `[AD059]`
- **Destination side loaded incrementally.** The delete derivation is a set difference needing a
  complete destination enumeration, which the engine's incremental path does not provide: it replays
  the prior run's snapshot plus changed-since rows, so an object deleted at the destination
  out-of-band since that run is still present and would yield a phantom delete — which would inflate
  the skipped-delete count and put a delete in front of a reviewer for an object that no longer exists.
  When the destination side did not run a full extract, no delete operations are derived and the
  manifest records that deletes were not computed, and both review depths say so plainly, so the
  omission is disclosed rather than silent. `[AD024]` `[AD056]`
- **Recorded deletes change the plan artifact's content.** Because deletes are suppressed from the
  plan today, recording them makes previously hidden operations appear in the plan artifact and in
  anything that renders that artifact. The existing live comparison rendering is unchanged, because
  deletes never enter the comparison result. Affected test fixtures and documentation are updated
  in the same change. `[AD004]` `[AD023]`
- **A reviewed update whose target has vanished.** The destination object a planned `update` names may
  have been deleted out-of-band between plan and apply. Because planned creates and updates both route
  through the convergent upsert, which creates when no destination object matches the convergence key,
  the operation materializes as a create. This is a consequence of the mandated convergent write path,
  not a separate behavior: no conflict detection is built, because destination freshness checks and
  conflict policies are out of scope. The operation is reported under its original operation identifier
  and its original action. `[AD025]`
- **A planned create whose destination identity already exists.** It converges onto the existing
  object through the same upsert rather than producing a duplicate or a second object. Whether the
  existing object's payload differs is not examined and no conflict is raised, because conflict
  policies are out of scope. `[AD025]`
- **An apply naming a run identifier with no plan artifact.** An apply naming a run identifier that
  does not exist, or whose run holds no plan artifact, is an error naming the run identifier and the
  expected artifact path, and creates no run directory. It is never presented as a plan with zero
  operations. `[AD026]`
- **A manifest declaring an unrecognized format version.** Refused, with a message naming the version
  found and the versions supported. That message is distinct from the v1 rejection message, because
  the operator's remedy differs: a v1 plan is re-planned, while an unrecognized newer version means
  the artifact was written by a different version of the tool. `[AD028]`
- **The destination rejects an operation, or the connection to it fails.** The apply stops at the
  first operation the destination rejects or that fails in transport. Operations already reported
  applied stay recorded, the run is recorded `failed`, and the failure names the failing operation
  identifier and the underlying error. The apply does not continue past the failure and does not roll
  back. `[AD027]`
- **Re-applying a run already at `status: applied`.** Permitted. Verification runs unconditionally on
  every apply attempt, whatever the operation count, so an empty plan with a broken checksum is still
  refused. A refusal is not terminal for the run identifier: the same run may be applied again once
  the cause is corrected. `[AD033]`
- **A plan that would fail apply verification, reviewed rather than applied.** Review verifies the
  plan checksum and reports the result prominently, but renders the plan regardless. Refusing to show
  an operator a suspect plan is worse than showing it with a clear warning, and review performs no
  writes. Review never mutates the run state. `[AD031]`
- **An operation whose destination identity is absent or empty.** The identity is derived from the
  configuration's identity attribute mapping; an operation for which no identity value can be formed
  fails the plan run, naming the kind and the identity attribute that had no value, rather than
  emitting an operation whose derived identifier does not address a destination object.
  `[AD035]`
- **A run directory that cannot be read.** A review or apply that cannot read the run directory or a
  file inside it — permission denied or an I/O failure — is an error naming the path that could not be
  read. It is never presented as an absent plan, a v1 plan, or a plan with zero operations.
  `[AD036]`
- **A kind filter that matches nothing.** A per-object review narrowed to a destination kind for
  which the plan holds no operation, or to a kind the configuration does not declare, is an error
  naming that kind, listing the kinds the plan does hold, and naming the operator's next action. It is
  never empty output, for the same reason a mistyped run identifier is not an empty plan. The obligation
  belongs to the **rendering** of review, not to the reading interface underneath it: a kind the
  configuration declares but the plan has no operation for is an empty result from the reader and an
  error from the renderer, because FR-029 requires a caller to consume the reader without parsing
  output. `[AD036]` `[AD058]` `[AD059]`
- **A failure that names a cause but no remedy.** Every failure this feature introduces names the
  operator's next action, not only the refusals AD036 reached: a torn artifact, an unrecognized format
  version, an unreadable path, an unknown run identifier, a kind narrowing that matches nothing, a
  plan-derivation failure on the non-mutating path, a peer matching no destination object, a peer
  matching more than one, and a payload value the canonical encoding cannot represent. Where the command
  already holds an enumeration it lists it rather than echoing the operator's input back: the kinds the
  plan holds, and the run identifiers that exist. `[AD059]`

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
  identify its peer by kind and identity values, never by a destination-assigned identifier. A peer
  identity component that is itself a reference MUST likewise record its own peer kind and peer
  identity rather than a raw unique-identifier string, recursively, so no consumer ever has to recover
  an identity by splitting a unique identifier on its separator. A peer's kind MUST be established by
  finding the referenced object in the loaded state rather than by reading the configuration's declared
  reference for the field, because one destination kind may be declared by more than one schema-mapping
  entry with different references for the same identity field, so the configuration alone cannot say
  which kind a given peer is. The loaded state can only be searched one kind at a time, so the search
  MUST be over the kinds the configuration declares as that field's reference across every mapping
  entry for the owning destination kind, and the kind recorded MUST be the one whose loaded state
  actually holds the referenced object. Where exactly one of those kinds holds it, that is the peer's
  kind. Where **none** holds it, or where **more than one** does, the plan run MUST fail under FR-030,
  naming the owning kind, the field, the referenced object and the kinds that were tried. Neither case
  may fall back to the configuration's declared reference: a fallback would reinstate the arbitrary
  pick this rule exists to prevent, and would do it silently on the path where a wrong pick fails the
  whole apply. The payload MUST carry the operation's
  identity components as well as its non-identity mapped values, because the destination's convergence
  key is formed from the identity and a payload without it cannot be written convergently; an identity
  component that is itself a reference travels as a relationship reference on the same rule as any
  other reference-bearing field rather than staying in the payload as a raw unique-identifier string.
  Each of those fields carries the obligation level and the absent-versus-empty rule FR-028 states, and
  "destination identity" and "the required source values as a full payload" carry the single
  representation and the single authority FR-028 fixes.
  *(DBR-008, DBR-011; encoding and layout per AD001, reference shape per AD003, closed action
  vocabulary per AD009, field obligations per AD035, identity in the payload per AD042, recursive
  reference shape per AD043, peer-kind resolution per AD046 as made constructible by AD050)*
  `[AD009]`
  `[AD035]` `[AD042]` `[AD043]` `[AD046]`
  `[AD050]`
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
  exists and its recomputed digest and row count equal the recorded values. "Content" here MUST mean
  the file's **logical rows** — the extracted records with the engine's per-run extraction timestamp
  excluded, in file order, each canonically encoded — and MUST NOT mean the file's raw bytes: the
  engine stamps that timestamp into every row of every snapshot, so a raw-byte digest would differ on
  every re-plan of an unchanged source and would make SC-006 unachievable while SC-006 fixes its mask
  at two fields. The row source identifier and the tombstone flag stay inside the digest, because both
  are deterministic for identical input and both are part of what the plan was computed against. The
  manifest MUST also carry a field recording whether delete operations were computed for this plan,
  per FR-015. *(DBR-006, DBR-008, DBR-015; computation per AD001, snapshot binding per AD008,
  delete-computation disclosure per AD024, logical-row digest per AD037)* `[AD008]`
  `[AD024]` `[AD037]`
- **FR-005**: The manifest and the ordered operations MUST be serialized deterministically — a
  fixed key order, no insignificant whitespace, and a fixed ordering of the operations sequence — so
  the checksum is stable across re-serialization of identical content. Canonical ordering applies to
  the operations sequence and to relationship-reference lists only. Collections whose order is part
  of the value — a payload's list-valued attributes — MUST be serialized in source order and MUST NOT
  be re-sorted, because sorting them would make the applied value differ from the reviewed source
  value. The remaining canonicalization details are fixed elsewhere: how the checksum-excluded fields
  are removed and how the two byte sequences are joined by FR-027, and the single canonical
  representation of destination identity by FR-028. *(DBR-014; encoding per AD001, reference-list
  ordering per AD003, remaining determinism details per AD035)* `[AD035]`
- **FR-006**: A saved plan MUST be reviewable at two depths: a summary giving a count per action
  and a count per kind, and per-object detail for the operations it contains. Per-object detail MUST
  present, per operation, at least its operation identifier, its action, its destination kind, and
  its destination identity, and MUST be narrowable to a single destination kind. A narrowing that
  names a destination kind for which the plan holds no operation, or a kind the configuration does not
  declare, MUST be an error naming that kind, listing the kinds the plan does hold, and naming the
  operator's next action; it MUST NOT be presented as empty detail. That obligation is a property of
  the **rendering**: the reading interface FR-029 fixes MUST return an empty result for a kind the
  configuration declares and the plan has no operation for, and MUST raise only for a kind the
  configuration does not declare at all, because a caller consuming data rather than rendered text
  cannot be served by a presentation rule.
  **Both depths MUST also surface the delete-computation record FR-015 puts in the manifest**, stating
  plainly when delete operations were not computed for this plan — without which a plan missing its
  entire delete class is indistinguishable from a plan that genuinely has no deletes, and FR-015's
  claim that the omission is explicit and reviewable is carried by nothing. A **non-zero** delete count
  MUST additionally be annotated inline, in the summary and in the per-object detail alike, stating that
  no delete will be executed against the destination by this release, so a reviewer approving a plan sees
  what will and will not be written from the same output they approve.
  **Review renders a plan it would refuse to apply, with one exception that MUST be stated rather than
  discovered.** A plan that would fail the pre-apply verification is rendered anyway, annotated with why —
  review's job is to show. But an operation carrying an action this release does not recognize is refused
  while the artifact is read, which is what puts FR-017's refusal before any destination write, and review
  reads through that same interface: so review **also** refuses such a plan, with the same message. That is
  the intended behavior and not an accident of sharing a reader — a plan whose operation vocabulary this
  release cannot interpret cannot be honestly summarized either, and the count it would print would be a
  count of operations it does not understand. It is stated here so no reader takes "review never refuses to
  show" as absolute, and it is tested. *(DBR-002,
  DBR-005; minimum field set per AD020, filter-miss behavior per AD036, delete-computation and
  delete-count disclosure per AD056, the empty-versus-raise split per AD058, next action and the kind
  enumeration per AD059; the review-side refusal stated per the round-two remediation)*
  `[AD020]`
  `[AD036]` `[AD056]` `[AD058]` `[AD059]`
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
  run is located only by sync name and run identifier under the cache root.
  **The read-from-artifact mode MUST be requested by an option that takes the run identifier as its own
  value**, so one option carries one meaning. It MUST NOT be a bare mode flag sitting beside the existing
  live-path run-identifier option: that spelling gives one option two inverse meanings — a write target
  whose unknown value is silently created and whose stored plan is overwritten without the mode flag, and
  a read source that errors on an unknown value with it — discriminated by a flag the operator can omit,
  so a single omission turns a read into a destructive write against the very artifact being read. The
  existing live-path run-identifier option keeps its present meaning unchanged, and the "mode requested
  with no run identifier" error case ceases to exist, because the mode cannot be requested without one.
  An unknown run identifier, or one whose run holds no plan artifact, MUST be an error naming the run
  identifier, the expected artifact path, the run identifiers that do exist for that sync, and the
  operator's next action, and MUST NOT be presented as a plan with zero operations. A run directory or
  artifact file that cannot be read — a permission or I/O failure — MUST be an error naming the path that
  could not be read and the operator's next action. Because these
  review options are a user-visible CLI change, the same change MUST update the user documentation for
  the command they extend, and **each new option's help text MUST be specified before the reference
  documentation is generated from it** rather than discovered afterwards; the existing run-identifier
  option's help text MUST be corrected in the same change, because it no longer describes everything that
  option does. The in-process reader MUST be the single implementation, with the command a
  thin renderer over it; FR-029 fixes that reader's contract. *(DBR-020; command and option spelling per
  AD005 as corrected by AD057, group-only bar per AD019, run-identifier and lock behavior per AD021,
  output channel per AD023, echo mechanism per AD032, unreadable-path and documentation obligations per
  AD036, next actions and the run-identifier enumeration per AD059, specified help text per AD061)*
  `[AD019]` `[AD021]` `[AD023]` `[AD032]`
  `[AD036]` `[AD057]` `[AD059]` `[AD061]`
- **FR-009**: Before any destination write, an apply MUST verify five things, in this order: that the
  manifest's declared format version is recognized (FR-027), that the manifest's recorded run
  identifier equals the run being applied, that the plan checksum still matches, that the source-
  snapshot binding still matches, and that the configuration version still matches. The order runs
  from the cheapest and most structural check to the most contingent, so an operator is told the
  artifact is the wrong artifact before being told its contents disagree. **All** checks MUST be
  evaluated and **every** failure MUST be named, not only the first, so one apply attempt tells the
  operator everything that is wrong — with **one** stated exception, which is a property of the first
  check rather than a weakening of the rule. The format-version check is a **gate**: when the declared
  format version is not one the reader recognizes, or the manifest cannot be parsed at all, that single
  failure MUST be reported and the remaining four checks MUST NOT be evaluated, and the refusal message
  MUST say that they were not evaluated and why. A reader that does not understand the artifact's format
  does not know what its remaining fields mean, so evaluating a checksum or a snapshot-binding rule
  against them would report failures that are artifacts of the reader's own ignorance rather than facts
  about the artifact — which tells the operator things that are not true, the opposite of what the
  evaluate-all rule exists to achieve. Once the gate passes, the remaining four checks MUST all be
  evaluated and every failure MUST be named. The run-identifier check MUST be a separate equality comparison
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
  apply that wrote nothing are not distinguishable only by an absent field. This obligation is on the
  **new** refusal paths, which are the paths DBA-004 measures. It is deliberately **not** extended to the
  pre-existing schema-subhash abort: that block imports a resolver this package does not define, so the
  import raises and the surrounding handler swallows the whole check — the abort cannot execute, and
  repairing unreachable code would produce a test that can only pass against an injected stub. Making
  that check live is unrelated scope and is not done here. *(DBR-003, DBR-006; run-identifier
  check per AD012, run state per AD010, format-version check per AD028, order and full-failure
  reporting, message content and empty applied set per AD036, write-ordering scope per AD034,
  format-version gate per AD053, the unreachable pre-existing path per AD063)*
  `[AD010]` `[AD012]` `[AD028]` `[AD034]`
  `[AD036]` `[AD053]` `[AD063]`
- **FR-010**: The plan and its source snapshot MUST be bound so the pair cannot tear. A plan whose
  manifest exists but whose operations or source snapshot are absent or truncated MUST be refused
  on the same path as a mismatch. The manifest MUST carry an operation count so that a plan with
  no operations is distinguishable from a plan whose operations are missing, rather than the two
  presenting identically. A source snapshot file the manifest records but which is absent, or whose
  recomputed logical-row digest or row count disagrees with the recorded value, is truncated or
  tampered with and MUST be refused on that same path. Every torn-artifact refusal MUST name which part
  is torn, the expected and found values, and the operator's next action — on the review path as well as
  the apply path, since a torn artifact is reachable from both. *(DBR-015; count field per AD001, snapshot
  digest and row count per AD008, logical-row digest per AD037, next action per AD059)*
  `[AD008]`
  `[AD037]` `[AD059]`
- **FR-011**: The manifest's configuration-version field MUST hold a deterministic content
  checksum computed over the configuration the run used, unless the caller supplies a version
  identifier explicitly, in which case that value MUST be stored verbatim. The default rule MUST
  cover every field of the parsed configuration **except** the filesystem location it was loaded
  from, which is machine-dependent and would stop a plan produced in one checkout from being applied
  from another. The configuration's connection settings ARE covered, because a changed destination
  address is a changed configuration and only a one-way digest is written, so no credential is
  disclosed by covering them. A consequence of that coverage is recorded rather than left to be
  discovered: rotating a credential invalidates every saved plan for that configuration, which is
  refused at apply under FR-009 and requires a re-plan. Either way the value
  MUST be treated as opaque at apply: compared for equality and never parsed. At apply, the
  comparison value MUST be recomputed by the same default rule, unless an in-process caller supplies
  one verbatim, in which case the supplied value is compared verbatim; the CLI apply path uses the
  default rule only. No new user-facing input is introduced. *(DBR-018; apply-side supplier per
  AD013, covered field set and the credential-rotation consequence per AD041)* `[AD013]`
  `[AD041]`
- **FR-012**: A saved plan MUST be applicable by run ID, executing exactly the stored operations
  in dependency order, without re-extracting either side and without recomputing the comparison.
  *(DBR-004)*
- **FR-013**: The Infrahub destination adapter MUST be able to execute a planned create or update
  convergently, so that repeating an operation does not create a second object. Planned creates and
  planned updates MUST both route through the adapter's convergent upsert, keyed on the destination
  kind's human-friendly ID, and MUST NOT route through the adapter's existing update path, which is
  keyed on a destination-assigned node identifier populated only by a destination load that FR-012
  forbids. The data handed to that convergent write MUST carry every component of the destination
  kind's convergence key; a write issued without it is unkeyed and duplicates on re-apply, which would
  make SC-002 and SC-003 unachievable. Those components arrive by **two** routes, not one, and the
  distinction matters because assigning them all to the payload would leave the second route
  unimplemented: a component whose mapped value is a plain value is supplied by FR-002's
  identity-bearing payload, while a component that is itself a reference is **not** in the payload at
  all — FR-002 requires it to travel as a relationship reference, and it reaches the write as a
  destination identifier produced by the FR-014 peer resolution. The requirement is on the assembled
  data, which MUST carry every component however it arrived.
  **Where that requirement is checked, and what the check can promise.** The assembled data MUST be
  checked component by component before the write is issued, and a component that is unaccounted for MUST
  raise, naming the destination kind and the component — that is the diagnostic, and it is the only form of
  the check that can say *which* component is missing. Keyedness itself, however, is a property of the
  **rendered mutation** rather than of the assembled data, so the rendered mutation MUST also be checked
  before the write is issued. Where the destination kind's convergence key is composed entirely of its own
  direct attributes, a rendered mutation carrying neither identifier can only mean the payload lost its
  identity components, and it MUST be refused. Where the convergence key crosses a relationship, the
  rendered mutation may carry neither identifier for a reason this outcome does not control — the
  destination library cannot form the key from a peer supplied as a resolved identifier — and the write is
  still issued, because the convergent write may key it at the destination and declining to issue it would
  withdraw relationship-bearing kinds from what this outcome delivers. That case MUST instead be reported
  once per destination kind on the run's log stream, and it is carried as a recorded risk rather than as a
  settled mechanism.
  **A destination kind that declares no convergence key at all is a third case and MUST NOT be refused
  (AD076).** FR-024 explicitly contemplates that kind and requires the plan run to survive it, so a check
  that treated an empty component list as "every component is direct" would refuse a configuration class
  this specification tolerates — and refuse it with a false diagnosis, since no component went missing. It
  MUST be reported on the same terms as the relationship-crossing case, naming the kind and the fact that
  it declares no convergence key, and the write MUST still be issued. This follows from the promise below
  in its own terms: for such a kind, being unkeyed is a schema fact rather than a defect.
  **Both reports are one obligation and it is pinned (AD078).** Each MUST be emitted once per destination
  kind rather than once per operation, so a large apply is disclosed to without being drowned; each MUST be
  emitted at a level the command's own quiet verbosity setting does not suppress, for the same reason
  FR-017's skipped-delete warning is, because the scripted and automated invocations that run quiet are
  exactly the ones for which this report is the only signal; and each MUST name the destination kind, state
  that the write was issued anyway, and say what to watch for — a duplicate object of that kind at the
  destination if the destination does not key on the components as sent. Naming an internally recorded risk
  is not sufficient content: the operator does not have that record.
  What this requirement therefore promises is narrower than it once claimed and is
  stated here rather than overstated: **no write is issued whose payload is missing a convergence-key
  component, and no rendered mutation is issued unkeyed where being unkeyed can only be a defect.**
  *(per AD066, with the no-convergence-key case per AD076 and the report's cardinality, level and content
  per AD078)* `[AD066]` `[AD076]` `[AD078]`
  Cardinality-many relationships MUST be written as a replace-set, and that replace-set MUST be
  **enforced explicitly after the convergent write rather than assumed of it**. The earlier reading
  that replace-set "is the existing behavior" was false twice over: the only replace-set this repository
  implements sits on the update path this requirement forbids, and that implementation is not in fact a
  replace-set at all — it reads the destination peer set before loading it, so it adds without removing.
  Whether the convergent write's own
  mutation replaces or merges a peer list is not determinable without a live destination. The enforcement
  MUST therefore **re-read the destination's own peer set before comparing**: a node built locally from
  the write payload already reports the desired set as its existing set, so a comparison made without
  re-reading is a guaranteed no-op that can pass only against a test double. Enforcing it with the
  re-read makes the clause true by construction; where the convergent write already replaces, the
  enforcement is a no-op for the right reason rather than by accident.
  **What "re-read" requires is a destination read, not an ordering.** The destination library's own
  peer-loading call declines to do anything when the peer set it holds already reports itself loaded, which
  is exactly the state a locally built node is in — so an enforcement that merely calls it before reading the
  peer set still reads nothing. The enforcement MUST discard the locally held peer set before loading, or
  issue its own scoped destination read for that relationship, and the evidence for it MUST be that a
  destination read was **issued** rather than that a load was attempted. *(per AD065)*
  **And the reconciled peer set MUST then be issued to the destination (AD075).** Adding and removing
  peers on a node's relationship handle changes only what this process holds in memory — the destination
  library performs no write of its own for either edit, and the reconciled set reaches the destination
  only on a subsequent write of the node. The enforcement MUST therefore issue that write once the
  reconciliation is complete, and it MUST be an ordinary update of the node just written rather than a
  repeat of the convergent write: by that point the node is already known to exist, so an ordinary update
  is what carries the reconciled relationship.
  **That update MUST carry every reconciled field, including a peer set reconciled to empty — and it MUST
  carry nothing else (AD088, amending AD085).** By default the destination library suppresses fields it
  judges unchanged, and it judges an emptied peer set unchanged against the payload the convergent write
  was built from — so the default form of the update drops it, and a plan that says to empty a peer set
  would leave the destination's peers in place. **From that, AD085 concluded that "the enforcement MUST
  therefore request the full form of the update, which suppresses nothing", and that clause is withdrawn.**
  Read against this requirement's own second clause — "an update payload is authoritative for the mapped
  fields it carries and MUST NOT touch unmapped destination fields" — it was unsatisfiable, and the two
  MUSTs are named here rather than quietly reconciled. Requesting the full form of a node's update means
  re-rendering the whole node, and the destination library deliberately renders a **null** for every
  **unmapped optional cardinality-one** relationship of a node it considers existing, precisely so that a
  caller can clear one; the convergent write marks the node existing, so the full form writes a null over
  every such field the plan never mapped. Suppressing nothing and touching nothing unmapped cannot both be
  had from a whole-node render.
  The enforcement MUST therefore be a **targeted write of the relationship fields being replaced** — the
  node's own identifier and those fields, and no other field of the node — rather than any form of a
  whole-node update. That satisfies both clauses at once: nothing is suppressed, because an emptied peer set
  is written explicitly rather than left to survive a comparison against the convergent write's payload; and
  no unmapped field is touched, because no unmapped field is in the payload at all. Pre-initialising or
  restoring the unmapped fields before a whole-node update MUST NOT be used instead — that treats the
  symptom, and it makes the guarantee depend on having first read every unmapped relationship of the
  destination object. *(flush per AD075, its form per AD085 as amended by AD088)*
  **The write MUST be issued for the same object whose peer set was reconciled.** A write issued for any
  other representation of the destination object carries that representation's own unreconciled peer set —
  the desired set, never compared against the destination's — so the reconciliation is discarded exactly as
  if it had never been issued, and what reaches the destination depends once again on what the mutation does
  with a peer list on its own, which is the very question this enforcement exists so as not to depend on.
  This is why the two re-read routes above are not interchangeable once the write is a
  separate step: whichever route is taken, the reconciliation and the write MUST meet on one object.
  The evidence for the whole
  enforcement MUST accordingly be the **destination write carrying the reconciled peer list**, and MUST
  NOT be the state of the in-memory peer set. A reconciliation that is never issued is indistinguishable
  from a correct one wherever the convergent write already replaces the peer set, and **silently wrong
  wherever it merges** — which is the case this enforcement exists for, so an unissued reconciliation
  fails exactly where it is needed and nowhere else. *(per AD075)*
  **This enforcement is new behavior on the planned-write path only.** The pre-existing update path's
  additive ordering is a pre-existing defect and MUST be left exactly as it is: correcting it there would
  change what the existing mutating command does to destination relationships, which this outcome does not
  authorize and no requirement, criterion or documentation entry here describes. It is recorded as work for
  a later outcome. *(per AD070)* `[AD065]` `[AD070]` `[AD075]` `[AD085]` `[AD088]`
  An update payload is authoritative for the mapped fields it carries and MUST NOT touch
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
  every clause here. Because both of those criteria need a live destination and none is reachable, an
  offline conformance check is required alongside them, so a defect of this class is caught without
  waiting for the deferred evidence — and that check MUST assert the **rendered mutation the destination
  library produces**, not the payload assembled before it. Keyedness is a property of the rendered
  mutation: it carries either a destination-assigned identifier or a human-friendly identifier, and it
  carries neither when a convergence-key component is missing. The assembled payload cannot show that,
  because by then a relationship-crossing component is a resolved identifier string from which no
  attribute can be read. The check MUST therefore build the operation against a **committed destination
  schema fixture** and assert on the rendered mutation, assert that the replace-set enforcement **issues a
  destination read for the relationship before reading the peer set** it compares against, assert that the
  reconciled peer set is then **issued to the destination** — a write carrying the reconciled list, made for
  the same object the reconciliation acted on, and taking the form of an ordinary update of the object
  already written rather than a repeat of the convergent write — **including where the plan reconciles a
  peer set to empty**, which is the case the default suppression of unchanged fields would drop, assert that
  that write **names no destination field the operation did not map** (AD088), and assert that
  applying the same operation twice renders **byte-identical** mutation inputs. The flush assertion is the
  fourth of these for the same reason the read assertion is the second: without it, an enforcement that
  reconciles in memory and issues nothing satisfies every other assertion in the set. The unmapped-field
  assertion is the fifth for a reason of its own, and it constrains the **fixture** rather than only the
  assertion: it is vacuous unless the schema fixture declares, on the kind under replace-set, at least one
  **optional cardinality-one** relationship that no operation in the check maps. A fixture carrying only a
  cardinality-many relationship cannot see this defect class at all, which is how one reached the tree. Keyedness is asserted as two
  cases, not one: for a kind whose convergence key is composed of its own direct attributes it MUST hold,
  and for a kind whose key crosses a relationship the same assertion is made and marked a **strict expected
  failure** against the recorded risk, so it reports the limitation today and turns into a suite failure the
  day the limitation is gone. An assertion that merely observes the write surface being called proves that a
  test double was invoked and nothing about convergence; an assertion that two applies produce one object
  proves nothing against a test double, which holds no destination state. *(DBR-013, DBR-011; write path per
  AD015, create-on-no-match consequences per AD025, verification route per AD036, convergence-key payload
  per AD042, explicit replace-set enforcement per AD038, offline conformance check per AD045 as rebuilt by
  AD054, the read-not-order observable per AD065, the keyedness split per AD067, the repeat assertion per
  AD068, the flush observable per AD075, the empty-set case per AD085, the unmapped-field assertion and the
  fixture shape it needs per AD088)* `[AD015]`
  `[AD025]` `[AD036]` `[AD038]` `[AD042]`
  `[AD045]` `[AD054]` `[AD065]` `[AD067]`
  `[AD068]`
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
  naming the peer kind, the peer identity, the referring operation identifier, and the operator's next
  action. A peer identity that matches **more than one** destination object MUST refuse, naming the peer
  kind, the peer identity, the match count, and the operator's next
  action. Neither case may be a silent skip. Both refusals are scoped to
  **this saved-plan apply resolver only**. The existing live write path's warn-and-continue on an
  unresolvable peer is unchanged by this feature: it is existing behavior on an existing path that
  this outcome does not authorize touching, and the refusal is a property of resolving a peer from a
  saved plan with no comparison store loaded — a path that does not exist today. Where a peer's
  identity itself contains a reference, resolution MUST use the nested peer kind and peer identity the
  plan records under FR-002 and MUST NOT recover an identity by splitting a unique identifier on its
  separator. *(DBR-007, DBR-011; resolution shape per AD003, resolution failures per AD016, tier
  qualification per AD022, memo negative-caching rule per AD036, recursive peer identity per AD043,
  scope of the refusal per AD048, next-action obligation on both refusals per AD059)*
  `[AD016]` `[AD022]` `[AD036]`
  `[AD043]` `[AD048]` `[AD059]`
- **FR-015**: Delete operations MUST be recorded in the plan, changing today's default of
  suppressing them. They MUST be derived from the destination-only identities in the loaded
  destination state and materialized only into plan records, never into the comparison result the
  write path consumes. A derived delete's destination identity MUST be canonicalised by the **same**
  recursive rule FR-002 states for every other operation — an identity component that is itself a
  reference records its own peer kind and peer identity rather than a raw unique-identifier string, to
  whatever depth the configuration nests — with the difference that the nested peer kinds are resolved
  from the loaded **destination** state rather than the loaded source state. Deletes are not carved out
  of that rule and MUST NOT be. The difference is forced rather than chosen: a delete exists precisely
  because its object is present at the destination and absent from the source, so its peers are
  destination-only by construction and the source-side resolution FR-002 describes has nothing to
  resolve against. Without this, a delete's identity would be the only place in the artifact where a
  consumer had to recover an identity by splitting a unique identifier on its separator, which is the
  flaw the recursive rule exists to prevent, and the identity a delete's operation identifier is derived
  from would not be the identity a reviewer reads. The comparison flags a project configures MUST keep their present meaning
  for the write path and MUST NOT be loosened to make deletes visible. Deletes MUST come from that
  one source only, so no operation is recorded twice. Test fixtures and documentation affected by
  the change in plan content MUST be updated in the same change. Deletes MUST be derived only when
  the **destination side** ran a full extract, because the derivation is a set difference that
  requires a complete destination enumeration and the engine's incremental path does not provide one.
  When the destination side was loaded incrementally, no delete operation MUST be derived, and the
  manifest MUST record that deletes were not computed for this plan, so the omission is explicit and
  reviewable rather than silent — which is what keeps FR-017's "never silently skipped" contract true.
  "Reviewable" MUST be delivered rather than asserted: FR-006 requires both review depths to surface
  that record and to say plainly when deletes were not computed, without which a plan missing its whole
  delete class is indistinguishable from a plan that has no deletes.
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
  per AD004, extraction precondition and disclosure per AD024, `sync`-mode parity per AD036,
  destination-side identity canonicalisation per AD049, the review-surface disclosure per AD056)*
  `[AD024]` `[AD036]` `[AD049]` `[AD056]`
- **FR-016**: A delete MUST NOT be applied to the destination by the saved-plan apply path. Not
  executing it is a **designed limitation of this release**, not a fault condition: applying deletes is
  out of scope here and is assigned to a later outcome, so the apply path is correct precisely when it
  declines to execute one. What FR-017 requires of that decline is that it be recorded and reported, not
  that it end the run. The existing write path's behavior under a project's configured comparison flags
  is unchanged by this feature. *(DBR-010; the designed-limitation framing per AD055)*
  `[AD055]`
- **FR-017**: A plan operation the apply path does not execute MUST be reported and MUST be recorded;
  it MUST NOT be silently skipped. Two classes exist and they are treated differently, because one is
  designed and the other is not.
    1. **A recorded delete** is a designed limitation of this release under FR-016. The apply MUST
       execute every non-delete operation in the same plan, MUST NOT delete from the destination, and
       MUST end in the applied run state. It MUST record on the run, in the same place FR-020's
       applied-operation record lives, both the **count** of deletes it did not execute and their
       operation identifiers, and it MUST emit an operator-visible warning naming that count. That warning
       MUST be emitted at a level the command's own quiet verbosity setting does not suppress, because the
       scripted and automated invocations that run quiet are exactly the ones for which the warning and the
       run record are the only signals; and the command's completion line MUST name the skipped count when
       it is non-zero, so the last line an operator reads is not silent about it. A
       delete-bearing plan MUST NOT fail the run, and no new run state is introduced for it.
    2. **A genuinely unsupported operation** — one carrying an action this release does not recognize —
       MUST be refused before any destination write, MUST name the operation identifier, the action
       found, the actions recognized and the operator's next action, and MUST fail the run. Nothing
       about such an operation is designed, so what it would do to the destination is unknown and the
       run cannot claim to have applied what was reviewed.
  **The basis this re-derivation carries**, as the batch's derived-requirement policy requires: DBR-009
  requires recording deletes while DBR-010 forbids applying them, so on every delete-bearing plan the
  applied set necessarily differs from the reviewed set. What DBR-016 protects is that the difference be
  **provably knowable** rather than inferred — and the applied-operation identifiers together with the
  recorded skipped-delete count and identifiers make it a recorded value, which is what distinguishes
  this from the silent skip DBR-016 forbids. A skip is silent when nothing records it. The failed state
  was one way of forcing the difference into view, not the property being protected, and it is the wrong
  way here because it reports a designed limitation as a fault. A second and narrower ground was once
  recorded here and is **withdrawn**: it read DBR-016's term as never reaching a recorded delete at all, on
  the footing that a delete is a fully recognized member of the closed action vocabulary whose
  **execution** the brief excludes separately. The brief's own text settles that against the reading — the
  acceptance criterion and the user scenario standing behind DBR-016 both apply the phrase "the unsupported
  operation" to a recorded delete, which would leave that phrase without a referent. So the distinction
  between the two classes above is the **substance** of this re-derivation, not an alternative authority
  for it, and this re-derivation rests wholly on the brief owner's override at the delivery gate — an
  override that was **necessary**, because no reading of the brief's existing text reached this outcome.
  *(DBR-016, re-derived per AD055 on the authority corrected at AD074, whose second ground is withdrawn at
  AD077; DBR-009 and DBR-010 are quoted and
  unchanged. **Successor note**: a
  later outcome replaces the run record with durable storage behind provider interfaces and should
  promote the skipped-delete count from a summary key to a first-class run-record field.)*
  `[AD055]` `[AD059]` `[AD077]`
- **FR-018**: No secret value MUST appear in the plan artifact or in any review output. The artifact
  carries mapped source field values only. Credentials live in the configuration's `settings` and MUST
  never be written to the artifact or to review output. No field-level secret-classification model —
  omit, mask, or refuse behavior over mapped data fields — is built here; that is scope this outcome
  does not carry. *(DBR-017; scope and mechanism per AD018)* `[AD018]`
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
  per AD001, write order and v1/torn disjointness per AD014)* `[AD014]`
- **FR-020**: The identifiers of operations reported as applied MUST be recorded on the run result
  as an **ordered** sequence, in the order the operations were reported applied. The ordering is what
  makes "the last operation reported as applied" well defined: FR-025's last-applied pointer is the
  final element of this sequence rather than a separate recorded field. The record MUST have **one named
  home**, stated once here rather than left for each reader to infer: it lives under a named key of the
  run's recorded summary, which is already a free-form mapping inside the run record's existing key set,
  so no persisted schema other code reads is extended and the run-directory layer stays unchanged as this
  outcome's plan declares. FR-017's skipped-delete count and identifiers live under their own named keys
  in that same summary, so the reviewed set, the applied set and the difference between them are all
  readable from one place. *(scope boundary: run result
  only, not a durable ledger. Verified through SC-005, whose evidence reads the apply-side identifier
  set from this record. Ordering and the last-applied pointer per AD036; the named home per AD062;
  the skipped-delete companion keys per AD055.)* `[AD036]` `[AD055]`
  `[AD062]`
- **FR-021**: Two operations within one plan MUST NOT share an operation identifier. Because the
  identifier is derived rather than allocated, uniqueness MUST be asserted when the plan is written
  and MUST fail the plan run if it does not hold, rather than being assumed. Under FR-002's closed
  action vocabulary exactly one operation exists per (action, kind, destination identity), so a
  collision is always pathological. *(Carries the brief's "Identifier collision" edge case; no
  separate acceptance criterion, because the brief states no criterion for it and the obligation is
  a write-time assertion rather than an observable outcome.)* `[AD009]`
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
- **FR-024**: The plan run MUST warn at plan time in either of two conditions, naming the affected
  kind and what is missing. **First**, when a destination kind declares no human-friendly ID, or when
  the plan's destination identity for that kind does not supply every human-friendly-ID component:
  that is the observable convergence actually rides on, because the convergent write is keyed on the
  human-friendly ID and is unkeyed — and therefore duplicates — when it is absent or incomplete.
  **Second**, when the destination kind declares no uniqueness constraint covering the plan's identity
  attributes: that is the brief's own stated condition, and it is a different condition from the
  first, because a kind with a complete human-friendly ID but no uniqueness constraint still
  duplicates silently. Both conditions MUST be checked, and both are readable from the same cached
  destination schema. The whole warning is therefore **scoped to destinations that expose a schema**:
  where the destination exposes none, neither condition is readable, the warning MUST be skipped, and
  skipping it MUST NOT be an error or a plan-run failure. That scoping is not a convenience — plan
  derivation now runs on the non-mutating command for **every** destination, and FR-030 makes a
  derivation failure fatal there, so a convergence-key check that assumed a schema would turn this
  warning into a hard regression on destinations that plan and compare perfectly well today. The plan
  run MUST still succeed in either case; this is a warning,
  not a failure, per the brief. The warning MUST be emitted on the run's **log stream**, which is
  where the plan path already emits its operational output, and not on the standard-output channel
  FR-008 reserves for read-from-artifact review output. It is emitted only: it MUST NOT be recorded as
  a manifest field, so it stays outside the FR-004 checksum and outside SC-006's byte comparison.
  *(Carries the brief's non-unique-destination-identifier edge case. The convergence-key condition is
  AD017's restatement; the uniqueness-constraint condition is the brief's own, restored per AD044
  because AD017 had substituted a different condition for it. Emitted-only, non-manifest status and
  output channel per AD036; schema-exposing scope per AD052; criterion SC-014.)*
  `[AD017]` `[AD036]`
  `[AD044]` `[AD052]`
- **FR-025**: If an apply stops partway — meaning it terminates in-process with a reported error —
  the operations already written MUST stay written and the run MUST record, best effort, the last
  operation it reported as applied, where "last" means last in the dependency order actually
  executed. The record is explicitly NOT required to survive abnormal process termination.
  *(scope boundary: run result only, not a durable ledger. Carries the brief's "Partial apply" edge
  case; no separate acceptance criterion, because SC-003's crash windows are measured
  destination-side and a durable per-operation record is out of scope.)* `[AD011]`
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
       computed against, holding that file's run-relative path, a digest over its logical rows with
       the engine's per-run extraction timestamp excluded, and its row count.
    6. The **operation count**, per FR-010, which is what keeps a plan with no operations
       distinguishable from a plan whose operations are missing.
    7. The **delete-computation record**, per FR-015, stating whether delete operations were computed
       for this plan.
    8. The **plan checksum**, per FR-004: one deterministic value over the manifest and the ordered
       operations, excluding only itself, the run identifier, and the creation timestamp. Those three
       are **removed** before the manifest is canonicalized rather than blanked, and the canonical
       manifest bytes and the operations bytes are joined with no separator between them.

  A manifest whose declared format version is not one the reader recognizes MUST be refused, with a
  message naming the version found, the versions supported, and the operator's next action. That message
  MUST be distinct from the
  message FR-019 requires for a plan in the pre-existing format, because the two conditions have
  different operator remedies: a pre-existing-format plan is re-planned, while an unrecognized version
  means the artifact was written by a different version of the tool. Unknown **additional** manifest
  fields MUST be tolerated on read rather than refused, and MUST be included in the bytes the checksum
  is computed over — a later outcome adds a schema-fingerprint field to this same manifest, and a
  reader that rejected fields it did not know would refuse that artifact on arrival. This requirement
  consolidates the manifest obligations FR-004, FR-010 and FR-015 state field by field; where they and
  this requirement describe the same field they are one obligation, not two. *(DBR-006, DBR-008;
  consolidated field set, format-version field and unknown-field tolerance per AD028; the individual
  field rules per AD001, AD008 and AD024; canonicalization details per AD035; next action per AD059;
  criterion SC-018)* `[AD028]` `[AD059]`
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
    4. **The authority and the extent of the payload.** "The required source values as a full payload"
       is authoritative for the mapped fields it carries and silent about every other destination
       field: applying it MUST set the fields it carries and MUST NOT touch unmapped destination
       fields, which is the same authority FR-013 states for a planned update. "Full" means complete
       with respect to the configuration's field mapping, not complete with respect to the destination
       schema. Complete with respect to the field mapping MUST include the **identity components**:
       the payload is the union of the operation's destination identity and its non-identity mapped
       values, minus any component that travels as a relationship reference instead. The comparison
       engine's attribute accessor excludes identity fields by contract, so a payload taken from it
       alone would carry none of them, and a write issued from such a payload cannot form the
       destination's convergence key.

  *(DBR-008, DBR-011, DBR-014; obligation levels, the absent-versus-empty rule, the single identity
  representation and the payload's authority per AD035; payload authority consistent with AD015; the
  payload's identity components per AD042; verified through SC-002, SC-005, SC-006 and SC-008 rather
  than by a criterion of its own)* `[AD035]` `[AD042]`
- **FR-029**: Reading a stored plan MUST have exactly one supported entry point: an in-process plan
  reader that takes the sync name and the run identifier locating a stored run, reads that run's plan
  artifact, and produces both review depths FR-006 defines — the summary and the per-object detail.
  It MUST return that content to its caller as data rather than writing it to any output stream, so a
  caller consumes it without parsing rendered text and so SC-010's credential scan can scan the
  returned value as data. Because it returns data rather than rendered text, it MUST NOT carry
  presentation obligations: narrowing to a kind the configuration declares but the plan holds no
  operation for MUST return an empty result, and MUST NOT raise. Raising is reserved for a kind the
  configuration does not declare at all, which is a caller error rather than an empty result. FR-006's
  never-empty-output rule is discharged by the renderer above this interface, which is where an operator
  is the audience. The command-line review mode MUST be a thin renderer over that same entry
  point and MUST NOT re-implement reading, filtering, or summarizing, so both of SC-009's
  reachability cases — in-process and from the command line — exercise one code path. Nothing beyond
  this single reader is specified as a supported surface: no broader programmatic interface for
  plans, runs, or applies is designed here. *(DBR-002, DBR-012, DBR-020; single reader entry point per
  AD029, the empty-versus-raise split per AD058; verified through SC-009 and SC-010 rather than by a
  criterion of its own)*
  `[AD029]` `[AD058]`
- **FR-030**: A failure while deriving the plan — an operation for which no destination identity value
  can be formed, a relationship peer that cannot be located in the loaded state the operation is
  derived from, or whose kind cannot be established unambiguously there under FR-002's rule, a payload
  value the canonical encoding cannot represent, or a duplicate operation identifier — MUST fail the
  command that was run, with a clear, actionable error naming the destination kind, the cause, and the
  operator's next action. This
  holds on the **non-mutating** command as well as the mutating one: derivation MUST NOT degrade to
  warn-and-skip there, and no error-tolerance option is added to the non-mutating command, because a
  silently incomplete plan is exactly the divergence between the reviewed set and the applied set that
  FR-017 exists to prevent — and it is worse here than anywhere else, because the product of this
  feature is a plan an operator is asked to trust. This does not weaken the project standard that the
  non-mutating commands are safe to run at any time: that standard means the command performs no
  destination mutation, which still holds, since derivation runs after a read-only comparison and
  writes only inside the run directory. *(Carries FR-002's, FR-014's and FR-021's failure obligations
  onto the non-mutating path, where the mutating path's error-tolerance option does not exist. No
  separate acceptance criterion, because the brief states none and each failure is already asserted at
  the requirement that produces it. Per AD047; the next-action obligation per AD059, with the
  two-condition routing of the source-peer failure per AD082.)*
  `[AD047]` `[AD059]` `[AD082]`

### Key Entities

- **Plan artifact**: The durable output of a plan run — a manifest plus an ordered set of planned
  operations, held together in the run's own directory, readable independently of the process that
  wrote it and readable without loading all of it at once, and versioned so a pre-existing v1 plan
  is recognizable and refusable. *(concrete layout per AD001)*
- **Plan manifest**: The artifact's header. Binds the artifact to its run identifier, the
  configuration version it ran with, and the source snapshot it was planned against; records the
  format version and the operation count; and carries the deterministic checksum over itself and
  the ordered operations. The source-snapshot binding is a per-file record of run-relative path,
  logical-row digest, and row count. Also records whether delete operations were computed for this
  plan, which is false when the destination side was loaded incrementally. *(fields and checksum rule
  per AD001, snapshot binding per AD008, delete-computation disclosure per AD024, logical-row digest
  per AD037)* `[AD008]` `[AD024]` `[AD037]`
- **Planned operation**: One proposed change. Carries a stable operation identifier, the action —
  exactly one of `create`, `update`, or `delete` — the destination kind, destination identity, the
  required source values as a full payload including the identity components, relationship references,
  and a dependency tier. A relationship change is not an action; it is carried as relationship
  references on the owning object's create or update operation. A delete carries no payload and no
  relationship references, but its destination identity obeys the same recursive canonicalisation as
  every other operation's, resolved against the destination state rather than the source state.
  *(closed action vocabulary per AD009,
  identity in the payload per AD042, delete identity per AD049)* `[AD009]`
  `[AD042]` `[AD049]`
- **Relationship reference**: A peer named by kind and identity values rather than by any
  destination-assigned identifier, so it is resolvable at apply time and does not depend on which
  destination instance the plan is applied to. Where a peer's identity component is itself a
  reference, it carries the nested peer kind and peer identity in turn, recursively, so no consumer
  has to recover an identity by splitting a unique identifier. The peer's kind comes from the loaded
  source entry for the referenced object, not from the configuration's declared reference for the
  field, because one destination kind may be declared by several schema-mapping entries with different
  references. *(per AD003, recursive shape per AD043, peer-kind resolution per AD046)*
  `[AD043]` `[AD046]`
- **Source snapshot**: The extracted source-side state the plan was computed against — one file per
  extracted resource under the run's source side — bound to the plan by the manifest's per-file path,
  logical-row digest, and row count so the pair cannot tear. *(per AD008, digest scope per AD037)*
  `[AD037]`
- **Run**: The unit a plan belongs to and the handle an apply is requested by. Carries the run
  state, drawn from the existing vocabulary `pending | running | dry-run | applied | failed` — no member
  is added — including whether the plan reached `status: applied`. Its recorded summary is where this
  outcome's apply record lives, under named keys: the ordered identifiers of the operations reported as
  applied (FR-020), the count of deletes the apply did not execute, and those deletes' identifiers
  (FR-017). Those three together are what make the applied set knowable against the reviewed set. A
  refused apply is recorded `failed` with an empty applied set; an apply that skipped deletes is recorded
  `applied` with a non-zero skipped-delete count. *(per AD010; the summary as the record's home per
  AD062; the skipped-delete keys per AD055)* `[AD010]` `[AD055]`
  `[AD062]`
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
  crash-window measurement per AD011)* `[AD009]` `[AD011]`
- **SC-004**: A plan whose checksum, configuration version, or source-snapshot binding is **absent,
  truncated, or mismatched** is refused before any destination write, naming the failed check, and the
  run is recorded `failed` rather than reaching `status: applied`; a plan whose manifest exists but
  whose operations or source snapshot are absent or truncated is refused the same way — evidenced by
  six negative cases: the five the brief names (checksum mismatch, configuration-version mismatch,
  snapshot-binding mismatch, absent operations, truncated snapshot) plus an **absent source
  snapshot**, the case User Story 2 scenario 1 names and which "no longer matches" alone did not
  reach. Each case asserts refusal, zero destination writes observed as unchanged destination object
  counts, and the resulting run state read from the run sidecar. *(DBA-004; run state per AD010;
  absent-truncated-or-mismatched enumeration and the sixth case per AD036)* `[AD010]`
  `[AD036]`
- **SC-005**: The operation identifiers shown at review are the identifiers reported against each
  operation in the apply result — evidenced by a review-then-apply trace comparing both identifier
  sets per operation, with the review-side set read from per-object review output (FR-006) and the
  apply-side set read from the FR-020 record on the run result. *(DBA-005; review-side field set per
  AD020)* `[AD020]`
- **SC-006**: Re-planning an unchanged source and destination produces a byte-identical operations
  section and a byte-identical manifest, excluding the fields that necessarily vary per run (the
  run identifier and the creation timestamp) — evidenced by two consecutive plan runs **that both
  used the same extraction mode on each side** and a byte comparison with the varying fields masked.
  Fixing the extraction mode is part of the evidence procedure rather than an incidental detail: the
  manifest records whether deletes were computed, that field is inside the checksum and is not one of
  the two masked fields, and the engine may legitimately take the incremental path on a second run, so
  FR-015 makes byte-identity conditional on both runs having extracted the same way. Two runs at
  different extraction modes are expected to differ, and comparing them would make this criterion's own
  test unsound. *(DBA-006; same-extraction-mode precondition per AD024)* `[AD024]`
- **SC-007**: A plan containing a delete operation applies every non-delete operation, does not
  delete from the destination, and ends in run state `applied`, with a **non-zero skipped-delete count**
  recorded on the run and an **operator-visible warning naming that count** — evidenced by destination
  object counts before and after, scoped to the kinds appearing in the applied plan; the direct assertion
  that the object named by each delete operation is still present; the recorded run state read back as
  `applied`; the recorded skipped-delete count equal to the number of delete operations in the plan,
  alongside their operation identifiers; the captured warning naming that same count, **asserted at a level
  the command's quiet verbosity setting does not suppress** rather than by its text alone; the command's own
  completion line naming the skipped count; and the assertion
  that the identifiers recorded as applied plus the identifiers recorded as skipped account for **every**
  operation the plan contained, so the applied set is knowable against the reviewed set as a recorded
  value rather than an inference. That closure is asserted **of a completed apply**: an apply stopped by a
  destination rejection leaves the unattempted operations in neither set, which is how a partial apply stays
  distinguishable from a completed one, so the closure is checked after the operation sequence ends and not
  during it. A run state of `failed` **fails this criterion**: not executing a
  delete is a designed limitation of this release, and the criterion measures that the limitation is
  disclosed, not that the run is reported as broken. *(DBA-007, re-derived per AD055; run state per
  AD010; the recorded record's home per AD062; the warning's level, the completion line and the
  completed-apply scoping pinned in the round-two remediation)* `[AD010]` `[AD055]`
  `[AD062]`
- **SC-008**: A relationship-bearing kind from the qualified configuration applies with no loaded
  comparison store, and the resulting relationships on the destination match those the plan
  specified — evidenced by, for each relationship the schema mapping declares for the kind under test,
  the destination's peer set read back and compared against the plan's reference list as an **unordered
  set of (peer kind, peer identity) pairs**. The no-comparison-store precondition is evidenced as
  SC-001 evidences its own: no source or destination extraction call on the apply path. At least one
  referenced peer MUST **pre-exist at the destination and be absent from the plan**, so the
  destination-query resolution path is actually exercised: when every peer is created by the same
  plan, dependency-tier ordering fills the resolution memo and the query path never runs, which would
  let this criterion pass while the requirement it measures is broken. *(DBA-008; the pre-existing-peer
  case per AD043)* `[AD043]`
- **SC-009**: A saved plan can be summarized by action and kind, and expanded to per-object
  detail, at any time after the run and after the originating process has exited — reachable both
  in-process and from the CLI. Evidenced by four cases: summary and per-object detail, each
  produced in-process and from the CLI, all against a stored artifact read in a new process. Each
  case passes when the summary presents a count per action and a count per kind, and the detail
  presents one record per operation carrying at least its operation identifier, action, destination
  kind, and destination identity, **and** when both depths state the plan's delete-computation record —
  saying plainly that deletes were not computed where that is what the manifest records — and annotate a
  non-zero delete count inline to say no delete will be executed. Two of the four cases are produced
  against a plan whose destination side was loaded incrementally, so the not-computed wording is asserted
  rather than assumed reachable. Every case is produced with neither source nor destination
  reachable, which evidences that no adapter is constructed. *(DBA-009; field set per AD020;
  delete-computation and delete-count disclosure per AD056)*
  `[AD020]` `[AD056]`
- **SC-010**: No secret value appears in the plan artifact, in summary output, or in per-object
  output — evidenced by a canary-credential scan over the artifact and both review outputs, with the
  canary injected as a credential in the configuration's `settings`, which is where credentials enter
  this system. The artifact files are scanned directly; the CLI outputs are scanned from captured
  standard output; the in-process reader's return value is scanned as data. *(DBA-010; injection point
  per AD018)* `[AD018]`
- **SC-011**: A v1-format plan is rejected with a message directing the operator to re-plan, and
  no destination write occurs — evidenced by an apply attempted against a v1 fixture plan,
  asserting refusal, the message, and zero writes. *(DBA-011)*
- **SC-012**: The CLI command set gains no new command group, and review is reachable through
  commands that already exist — evidenced by the top-level command listing captured after the change and
  compared as text against a **committed baseline fixture** captured before any command-line change,
  showing no group added, plus the SC-009 CLI cases demonstrating that both
  review depths are reachable from existing commands. The baseline must be a committed fixture rather
  than one recovered at comparison time by reverting the working tree: a revert that no-ops on a
  committed tree makes the comparison diff the post-change listing against itself, which passes with no
  baseline at all. *(DBA-012; group-only bar per AD019; committed baseline per AD060)*
  `[AD019]` `[AD060]`
- **SC-013**: Applying a plan whose configuration-version value differs from the one recorded at
  plan time is refused without the value being parsed or interpreted, and an arbitrary opaque
  string round-trips unchanged through manifest write and apply comparison — evidenced by a
  round-trip test using a deliberately opaque value supplied verbatim by an in-process caller and
  compared verbatim at apply, plus the mismatch refusal from SC-004. *(DBA-013; apply-side supplier
  per AD013)* `[AD013]`
- **SC-014**: A plan run emits a convergence-key warning naming the affected kind and what is missing,
  and still succeeds, in each of three cases — evidenced by three plan runs, one per case, asserting
  the warning's content and the run's successful outcome. The three cases are: a destination kind that
  declares **no human-friendly ID**; a kind whose plan identity does **not supply every
  human-friendly-ID component**; and a kind that declares a **complete human-friendly ID but no
  uniqueness constraint** covering the plan's identity attributes. The third case is the brief's own
  stated condition and is asserted in its own right, because a kind can pass the first two and still
  duplicate silently. *(FR-024, per AD017 and AD044; the brief raises this edge case from
  documentation to detection but states no criterion for it)* `[AD017]`
  `[AD044]`
- **SC-015**: An apply whose manifest records a run identifier other than the run being applied is
  refused before any destination write, naming that check, and the run is recorded `failed` —
  evidenced by copying a plan directory from one run into another and asserting refusal, zero
  destination writes, and the resulting run state. *(FR-009, per AD012; beyond DBA-004's five cases,
  which do not include the run binding)* `[AD012]`
- **SC-016**: A planned relationship reference whose peer matches no destination object, and one
  whose peer identity matches more than one destination object, each refuse the operation and fail the
  run with a message naming the peer kind, the peer identity and the operator's next action — the
  zero-match message also naming the referring operation identifier, the multi-match message also naming
  the match count — and
  neither is silently skipped. The evidence is scoped to the saved-plan apply path; the same evidence
  asserts that the live write path's existing warn-and-continue behavior is unchanged. *(FR-014, per
  AD016; scope per AD048; next action per AD059)* `[AD016]` `[AD048]`
  `[AD059]`
- **SC-017**: A plan run whose destination side ran a full extract records delete operations and
  records in the manifest that deletes were computed; a plan run whose destination side was loaded
  incrementally records no delete operations and records in the manifest that deletes were not
  computed — evidenced by two plan runs against the same source and destination, one with a full
  destination extract and one incremental, comparing in each the presence of delete operations and
  the manifest's delete-computation field; asserting that the incremental run's plan records a
  skipped-delete count of zero at apply, so no phantom delete inflates it; and asserting that both review
  depths of the incremental run's plan state plainly that deletes were not computed. *(FR-015, per AD024;
  the zero-count assertion per AD055, the review-surface assertion per AD056)*
  `[AD024]` `[AD055]` `[AD056]`
- **SC-018**: An apply whose manifest declares a format version the reader does not recognize is
  refused before any destination write, with a message naming the version found and the versions
  supported, and the run is recorded `failed`; that message differs from the message a plan in the
  pre-existing format is rejected with — evidenced by an apply attempted against a fixture manifest
  carrying an unrecognized format version, asserting refusal, the message content, zero destination
  writes, and the resulting run state, and by comparing that message text against the pre-existing-
  format rejection message SC-011 asserts. *(FR-009, FR-027, per AD028; FR-009's first check had no
  criterion — SC-004 covers three of the five and SC-015 the run identifier)* `[AD028]`

## Out of Scope

Carried from the brief; every exclusion is carried verbatim. The delete bullet's **behavioral clause** is
restated per AD055, so this list and the brief's differ there on purpose — the exclusion itself is
identical. None of the following is delivered here.

- **Applying a delete to the destination.** The plan records delete operations; executing them is
  explicitly excluded, and doing so safely requires an ownership grammar this outcome does not
  define. Until that lands, a plan containing a delete behaves as FR-017 and SC-007 specify: every
  non-delete operation is applied, no delete is executed, the run ends in the applied state, and the
  number of skipped deletes is recorded on the run and named in a warning the operator sees. It is never
  silently skipped, and it is never reported as a run failure either — this is the exclusion working as
  designed, which is a different thing from a run that went wrong. `[AD055]`
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
  failure mode, none of which the brief's In-scope list carries. *(per AD018)* `[AD018]`
- **Artifact retention, lifecycle, and pruning.** No expiry, age-out, quota, retention policy, or
  prune-old-plans behavior is defined for saved plans. The brief's Out-of-scope list puts durable
  run and artifact storage behind provider interfaces elsewhere — this outcome uses the per-run
  directory layout the engine already writes — and puts plan expiration out of scope alongside
  destination freshness checks and conflict policies. A stored plan therefore lives exactly as long as
  its run directory does, by whatever means the operator already manages that directory.
  *(per AD030)* `[AD030]`
- **Pagination or truncation of per-object review output.** Per-object detail is narrowable to a
  single destination kind under FR-006 and by nothing else: no page size, no record limit, no elision
  of a large result, and no continuation handle. The brief's In-scope list names summary review and
  per-object review with no volume qualifier and states no output-size obligation, so none is
  invented. *(per AD030)* `[AD030]`
- **Plan-volume and review-latency targets.** No maximum operation count, artifact size, review
  response time, or apply throughput is asserted here, and none is tested. The brief sets no volume or
  latency target. The line-oriented encoding AD001 chose is what a later target would build on,
  because it allows a large plan to be summarized and detailed without loading all of it, but no
  threshold is set. *(per AD030)* `[AD030]`
- **Rendered review output as a stability or compatibility contract.** The summary and per-object
  renderings are operator-facing text, not a format other software may depend on: their wording,
  field order, and layout may change without that being a breaking change, and nothing here obliges a
  later outcome to preserve them. What this outcome owns as a contract is the plan artifact format —
  the manifest fields, the per-operation record, the deterministic serialization, and the checksum
  rule — which is the shared contract the brief names and which nine later outcomes consume.
  Presenting plan summaries in a user interface is one of those later outcomes and owns its own
  presentation. *(per AD030)* `[AD030]`
- **A cross-outcome policy governing changes to the plan artifact format.** No versioning process,
  deprecation window, migration procedure, or change-negotiation protocol between this outcome and
  its nine consumers is defined. The brief states the consequence — any change to the format after
  this ships is a breaking change for all nine — and states no process for managing it. FR-027's
  format-version field and its tolerance of unknown fields are the two mechanisms this outcome
  provides; the governance around them is not this outcome's to write. *(per AD030)*
  `[AD030]`
- **Folding the review flags into a command group.** This outcome adds no command group (FR-008) and
  asserts nothing about a later outcome folding whichever review spelling is chosen into one. The
  brief's Constraints assign that rework to a later outcome; whether such a fold preserves behavior is
  that outcome's obligation to establish, not a property this specification requires, tests, or
  guarantees. Recording the exclusion is preferred over stating a preservation requirement, because a
  requirement here would bind work the brief has already assigned elsewhere. *(per AD019)*
  `[AD019]`

## Assumptions

- For every kind for which the plan under test contains an operation, the destination kind's
  convergence key — its human-friendly ID — covers the plan's destination identity for that kind. This
  is the correspondence SC-002's "the same identity" rides on: the upsert is keyed on the
  human-friendly ID, so if that key does not correspond to the plan's destination identity an upsert
  can converge onto a different object than the plan named, or not converge at all. If the
  correspondence does not hold, create and update produce duplicates instead of converging, which
  invalidates SC-002 — hence the plan-time warning in FR-024. *(restated on the real convergence key
  per AD017)* `[AD017]`
- Review is reachable by extending existing CLI commands, without any new command group. If
  extending existing commands proves impossible, that is a scope change requiring a new decision,
  not an implementer's call.
- The qualified path is NetBox → Infrahub using `examples/netbox_to_infrahub/config.yml`.
- The run-mode vocabulary (`plan`, `sync`, `apply`) is fixed naming, not a build dependency.
- The existing engine and per-run artifact layout are present: per-side snapshots and run sidecars are
  already written, and the engine already reads a saved plan and dispatches it per row without calling
  the comparison engine, which is the property DBA-001 rides on. That per-row dispatch is **removed**
  by this outcome rather than kept beside the new one, because leaving it wired would be the second
  apply path FR-019 forbids; removal is safe because the surface it dispatches to has no
  implementation on any adapter. Today's plan rows are lossy. *(dispatch removal per AD040)*
  `[AD040]`
- The Infrahub destination adapter's **create** path is identifier-keyed and convergent, via the
  destination kind's human-friendly ID (`client.create(...)` then `save(allow_upsert=True)`,
  `adapters/infrahub.py:611-612`) — and it is convergent only because it passes identifiers and
  attributes together (`adapters/infrahub.py:602-604`). Its existing **update** path is not: it is
  keyed on a destination-assigned node id captured only during a destination load
  (`adapters/infrahub.py:622`, `:510`; `infrahub_sync/__init__.py:232`) and is therefore unusable from
  a saved plan, which performs no destination load. FR-013 accordingly routes planned creates **and**
  planned updates through the upsert path rather than inventing a new one. If that path turns out not
  to converge for a planned update, SC-002 and SC-003 fail and the write surface needs a decision this
  specification does not carry. Two properties of that path are **not** assumed and are handled
  instead: whether its mutation replaces or merges a cardinality-many peer set is unverifiable here, so
  FR-013 enforces the replace-set explicitly afterwards, issuing its own destination read of the peer set
  before comparing so the enforcement cannot be a silent no-op (AD038, AD054, AD065) — as new behavior on
  the planned-write path only, leaving the pre-existing update path's ordering alone (AD070); and the
  identity components the key is
  formed from are not present in the comparison engine's attribute set, so FR-002 puts them into the
  payload deliberately rather than inheriting them (AD042). *(corrected per AD015, AD038, AD042; scoped per
  AD065, AD070)*
  `[AD015]` `[AD038]` `[AD042]` `[AD065]`
  `[AD070]`
- On the qualified path, one destination kind is declared by **two** schema-mapping entries with
  different references for the same identity field (`examples/netbox_to_infrahub/config.yml:212` and
  `:254`), so the configuration's declared reference for a field does not uniquely determine a peer's
  kind. FR-002 therefore resolves a peer's kind from the loaded source entry instead. If a future
  configuration made even that ambiguous, peer resolution would need a decision this specification does
  not carry. *(per AD046)* `[AD046]`
- Rotating a credential in the configuration's `settings` changes the default configuration-version
  value and therefore invalidates every saved plan for that configuration, which is refused at apply
  and requires a re-plan. This is accepted rather than mitigated: excluding `settings` would mean a
  changed destination address did not invalidate a plan, which is the worse failure. *(per AD041)*
  `[AD041]`
- Five of the brief's acceptance criteria need a live destination — DBA-001, DBA-002, DBA-003 and
  DBA-008 in full, and the live half of DBA-007 — and so does one criterion this specification derived
  rather than took from the brief, SC-016's live half. None is reachable in the
  development environment, so their evidence is **deferred**, not produced: the brief's completion
  condition — inspectable passing evidence for every criterion — is not met at merge time. The offline
  conformance check FR-013 requires narrows what the deferral can hide, but does not close it — and it
  narrows it only in the form AD054 rebuilds, asserting the rendered mutation against a committed schema
  fixture; in its earlier form it narrowed nothing, because an assertion against the assembled payload and
  a wholly mocked destination cannot fail for the right reason. What it narrows is also **less than the
  whole**: for a destination kind whose convergence key crosses a relationship it can only record that the
  rendered mutation is unkeyed today, as a strict expected failure, so that class of convergence stays
  entirely on the deferred live evidence (AD067). *(per AD045, as rebuilt by AD054 and split by AD067)*
  `[AD045]` `[AD054]` `[AD067]`
- The engine computes kind-level dependency tiers from `schema_mapping[].fields[].reference`
  (`dependency_graph.py:25-36`), and this outcome derives each operation's tier from them. Tiers are
  absent entirely when a configuration declares an explicit `order:`
  (`infrahub_sync/__init__.py:132-133`), and the graph excludes self-edges and drops optional edges to
  break cycles — which is why FR-014's tier guarantee is qualified. On the qualified configuration the
  computation yields six tiers with no dropped edges and no active self-references, and the
  configuration contains relationship-bearing kinds of both cardinalities, which is what SC-008 needs.
  *(per AD022)* `[AD022]`
- Secrets enter this system as credentials in the configuration's `settings`, not as mapped source
  data. FR-018 is defended by never writing `settings` values into the artifact or review output, and
  SC-010's canary is injected as a credential in `settings` accordingly. Source record data on the
  qualified path is assumed to carry no credential values; if that assumption fails, handling it would
  require the field-level classification model AD018 places out of scope. *(per AD018)*
  `[AD018]`
- The configuration-version value is consumed as an opaque input. Before a version registry
  exists, a checksum over the configuration's declared content satisfies the binding. At apply the
  comparison value is recomputed by the same rule, so the rule must be stable for an unchanged
  configuration; a benign reformat of the configuration that the rule is sensitive to invalidates
  saved plans and requires a re-plan. *(per AD013)* `[AD013]`
- Which existing commands carry review, and their exact option spelling, is an implementation choice
  within one fixed constraint: no new top-level command group. That choice is now recorded as
  AD005, as corrected by AD057, rather than left open. `[AD057]`
- An apply that skipped deletes records the applied run state, which the incremental path's success set
  already contains, so such a run counts as a successful prior run for a later warm start. This is
  accepted rather than mitigated: the apply did succeed at everything this release executes, and
  introducing a distinct state to say otherwise would be the compatibility change AD010 declines.
  *(per AD055)* `[AD055]`
- SC-010's scan is performed over the artifact files and over the CLI's captured standard output.
  The in-process reader returns data rather than writing to a stream, and is scanned as data.

## Dependencies

- No in-batch dependencies. This outcome can be completed independently.
- **The existing non-mutating command this outcome extends.** Four properties of it are load-bearing
  for FR-008 and are recorded here rather than presumed:
    1. `--run-id` already exists on that command, meaning "Re-use a specific cache run id" for a live
       comparison (`cli.py:98`), and an unknown value is silently created rather than refused. The
       read-from-artifact mode therefore does **not** reuse it: per AD057 the review option takes the run
       identifier as its own value, so the existing option keeps exactly one meaning and a forgotten mode
       flag cannot turn a read into a write against the artifact being read. Its help string is corrected
       in the same change only insofar as the new option is documented beside it.
       `[AD057]`
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
  `utils.py:256-263`); FR-008 forbids that on the review path. *(per AD021)* `[AD021]`
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
| DBR-016 | FR-017; User Story 4 scenarios 1–3 — **re-derived** (AD055): the applied set stays provably knowable against the reviewed set through the recorded applied identifiers plus the recorded skipped-delete count and identifiers, rather than through a failed run state |
| DBR-017 | FR-018; SC-010 |
| DBR-018 | FR-011; Key Entities (configuration-version value); SC-013 |
| DBR-019 | FR-019; User Story 2 scenario 4 |
| DBR-020 | FR-008, FR-029; User Story 1 scenario 3 |
| DBA-001 | SC-001 |
| DBA-002 | SC-002; User Story 3 scenario 1 |
| DBA-003 | SC-003; User Story 3 scenario 2 |
| DBA-004 | SC-004; User Story 2 scenarios 1 and 3 |
| DBA-005 | SC-005; User Story 1 scenario 1 |
| DBA-006 | SC-006; User Story 2 scenario 5 — **carried conditionally**: it holds only when both plan runs extracted the same way on each side, because the manifest's delete-computation record is inside the checksum and outside the brief's two-field mask, so two runs at different extraction modes are expected to differ. The condition is named at both carriers (AD064) |
| DBA-007 | SC-007; User Story 4 scenarios 1 and 2 — **re-derived** (AD055): the run ends `applied` with a recorded non-zero skipped-delete count and a warning naming it, not `failed` |
| DBA-008 | SC-008; User Story 5 scenario 1 |
| DBA-009 | SC-009; User Story 1 scenario 2 |
| DBA-010 | SC-010; User Story 1 scenario 4 |
| DBA-011 | SC-011; User Story 2 scenario 4 |
| DBA-012 | SC-012; User Story 1 scenario 3 |
| DBA-013 | SC-013; User Story 2 scenario 2 |

### Derived brief items re-derived here

Two derived brief items are re-derived by this specification rather than carried as the brief states them.
Nothing **quoted** is touched: DBR-009 and DBR-010 stand exactly as the brief states them, and re-deriving
the two derived items is what makes that possible.

**The authority is the brief owner's override at the delivery gate (AD074).** The batch's approved
derived-requirement policy is *not* the authority and was miscited as such: it ratifies the planner's
derivations on a basis-disclosure proviso, and read as a licence for downstream re-derivation it would make
more than half of this brief advisory. It is cited here for the proviso alone — that a re-derivation carry
its basis, which each row below does. AD074 also offered a second and narrower ground — that DBR-016
governs an operation this release does not **support**, and a delete is a recognized action whose
**execution** the brief excludes separately, so DBR-016 never reached a recorded delete. **That ground is
withdrawn (AD077).** DBA-007 and the brief's User-scenario 4 both apply the phrase "the unsupported
operation" to a recorded delete, so the brief's own usage places a recorded delete inside DBR-016's term
and the withdrawn reading would leave DBA-007's phrase without a referent. The override was therefore
**necessary**, not merely the better-cited of two routes: no reading of the brief's existing text reached
the ratified outcome without amending a derived item. The distinction between a designed decline and an
unrecognized action remains the substance of the re-derivation below; it is not a second authority for it,
and this row is not precedent for re-deriving a derived brief requirement without an override.

**Two brief passages now read false and are named here so the planner has the exact repair target
(AD074).** Brief v5's §Out-of-scope delete bullet and its §User-scenarios Scenario 4 both restate the
superseded "the run fails" outcome. Both are restatements of the two items below rather than normative
content of their own, so they move with them and nothing normative is contradicted — but a v6 revision owes
them the ratified outcome, because other briefs in the batch read this one. Nothing in this delivery edits
the brief.

| Re-derived item | Brief's original basis | New basis carried here |
|---|---|---|
| **DBR-016** — an unsupported operation is reported at apply time and fails the run, never silently skipped | DBR-009 requires recording deletes while DBR-010 forbids applying them; a silent skip would make the applied set differ from the reviewed set | DBR-009 requires recording deletes while DBR-010 forbids applying them, so on every delete-bearing plan the applied set **necessarily** differs from the reviewed set. What must hold is that the difference be **provably knowable** rather than inferred — which the applied-operation identifiers together with the recorded skipped-delete count and identifiers supply. A skip is silent when nothing records it; this one is recorded twice over and warned about. An operation this release does not execute **by design** is a limitation, not a fault, so it does not fail the run; an operation whose action this release does not recognize **at all** still does, because nothing about it is designed. Carried by FR-016, FR-017 and FR-020 (AD055) |
| **DBA-007** — a delete-bearing plan applies its non-deletes, does not delete, and ends in a failed state naming the unsupported operation | DBR-009, DBR-010, DBR-016 | DBR-009, DBR-010 and the re-derived DBR-016, measured as: the non-delete operations landing, the delete targets surviving, run state `applied`, a recorded non-zero skipped-delete count with the skipped identifiers alongside it, and an operator-visible warning naming that count. Carried by SC-007 and User Story 4 (AD055) |

A successor note travels with both, recorded so a later reader finds it: the outcome that replaces the
run record with durable storage behind provider interfaces should promote the skipped-delete count from
a summary key to a first-class run-record field.

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
| Derived from DBR-009 and DBR-016: the delete-computation record and the delete count must reach both review surfaces | FR-006, FR-015; SC-009, SC-017 (AD056) |
| Derived from DBR-009, DBR-010 and DBR-016: a recorded delete at apply is a designed limitation, not a run failure | FR-016, FR-017, FR-020; SC-007; User Story 4; Edge Cases (A recorded delete at apply time) (AD055) |
| Derived from DBR-016: an operation whose action this release does not recognize fails the run | FR-017; User Story 4 scenario 3; Edge Cases (An operation whose action this release does not recognize) (AD055) |
| Constraint: which existing commands carry review is an implementation choice, within no new command group | FR-008; SC-012; Assumptions; Dependencies |
| Constraint: the qualified path is NetBox → Infrahub | Assumptions |
| Derived from DBR-007 at apply time: peer resolution failures | FR-014; SC-016 |
| Derived from DBR-003/DBR-006: the plan's run binding | FR-009; SC-015; User Story 2 scenario 6 |
| Derived from DBR-003/DBR-006 and DBR-019: the manifest's format-version check | FR-009, FR-027; SC-018; Edge Cases (A manifest declaring an unrecognized format version) |
| Derived from DBR-008 and DBR-016: plan-derivation failure behavior on the non-mutating path | FR-030; Edge Cases (A plan-derivation failure on the non-mutating path) |

## Open Design Decisions

Both items previously deferred here are now answered in [Clarifications](#clarifications) and
carried into the requirements above. **The decision set recorded in this section was ratified at the
delivery gate** (AD084), so what follows is a record of ratified commitments rather than of provisional
ones; they are written down rather than left as silent implementation choices because they are design
commitments other outcomes consume:

- **The plan artifact's concrete on-disk encoding** — decided as AD001 and ratified. Nine later outcomes
  consume this format and any later change to it is a breaking change for all of them, which is why it is
  recorded explicitly here.
- **Which existing commands carry review, and the exact flag spelling** — decided as AD005. It is
  user-visible, so it is named rather than left implicit. Whether a later outcome can fold that
  spelling into a command group without changing behavior is **not** asserted here: it is recorded as
  an explicit exclusion in [Out of Scope](#out-of-scope), because the brief assigns the command-group
  rework to a later outcome and a requirement here would bind work this specification does not own.
  *(per AD019)* `[AD019]`

Three further design commitments were surfaced during clarification and recorded the same way:
AD002 (operation-identifier derivation), AD003 (relationship-reference shape and apply-time peer
resolution), and AD004 (how deletes are recorded without changing what the write path writes).

Both are ratified. The blast radius recorded for them when they were open is kept as the revisit map: were
AD003 ever revisited, FR-002's reference shape and FR-014's resolution mechanism would reopen; were AD004
ever revisited, FR-015's derivation source and FR-016's structural boundary would reopen.

A further sixteen commitments — AD008 through AD023 — were recorded after the checklist evaluation
and are carried in [Clarifications](#session-2026-07-26--checklist-evaluation). Each is marked at
every requirement that encodes it, so the revisit set for any one of them is the set of requirements
carrying its marker. None of them expands scope: each either names a representation the brief
delegates, corrects a statement this specification made about existing code, or picks the reading
that keeps the brief's own text true.

Twelve more — AD037 through AD048 — were recorded after planning worked the specification down to
the code and after a cross-artifact analysis checked the result against the tree. They are carried in
the [planning-phase](#session-2026-07-26--planning-phase-decisions) and
[remediation](#session-2026-07-26--cross-artifact-remediation) sessions, and are marked the same way.
Seven of them correct a statement this specification made about existing code rather than adding
anything: AD037 (what the snapshot digest covers), AD038 (replace-set is not existing behavior on the
mandated write path), AD040 (the pre-existing dispatch is removed), AD042 (the comparison engine's
attribute set carries no identity), AD044 (the brief's condition is uniqueness, not the convergence
key), AD046 (one kind, two mapping entries) and AD048 (the refusal is scoped to the new resolver).
Two settle a format detail before the format is written — AD041 and AD043 — and three record how a
constraint is met rather than changing it: AD039, AD045 and AD047. All twelve are ratified, and their
revisit maps stand as the record: revisiting AD042 would reopen FR-002's
payload extent, FR-013's convergence clause and FR-028.4, and would make SC-002 and SC-003
unachievable; revisiting AD043 would reopen FR-002's reference shape and FR-014's resolution mechanism
for a second time.

A further eleven — **AD054 through AD064** — were ratified after three independent critique lenses
worked this specification, the plan and the tasks against the brief and the tree. They are carried in
the [critique](#session-2026-07-27--critique-round-one-ratified) and
[delete-bearing apply](#session-2026-07-27--the-delete-bearing-apply-re-derived) sessions and are marked
the same way. Ten correct this specification's own delivery rather than changing what ships: AD054 (the
offline harness asserted the wrong thing, and two code facts about the replace-set were wrong), AD056
(the delete-computation disclosure reached no review surface), AD057 (one option carried two inverse
meanings), AD058 (three contracts called operations their own documents did not declare), AD059 (nine
failures named a cause and no remedy), AD060 (two validation steps did not execute), AD061 (help text was
left to be discovered), AD062 (FR-020's record had no home), AD063 (a repair was applied to unreachable
code) and AD064 (a conditionally carried criterion was reported as plainly carried). One, **AD055**,
re-derives DBR-016 and DBA-007 — both **derived** brief items, so re-deriving them touches nothing
quoted, and each carries its new basis in
[Derived brief items re-derived here](#derived-brief-items-re-derived-here) as the batch's policy
requires. AD055 is ratified on that basis, and AD077 records that the brief owner's override at the
delivery gate was **necessary** rather than confirmatory; its revisit
map stands as the record — were it revisited, FR-016, FR-017, FR-020, SC-007 and User Story 4 would revert
to the failed-run reading and the tension between the quoted DBR-009/DBR-010 pair and the derived pair
would reopen as an unresolved brief-gap.

A further ten — **AD065 through AD074** — were ratified after the same three lenses re-ran against the
remediated artifacts. They are carried in the
[round two](#session-2026-07-27--critique-round-two-ratified) session and are marked the same way. All ten
correct this specification's own delivery and **none adds anything that ships**: AD065 (the prescribed
re-read mechanism performed no read), AD066 (a flat keyedness guarantee rested on a check that could not
deliver it), AD067 (a conformance assertion was written as a universal its own mandated fixture must fail),
AD068 (an idempotence assertion could not fail against a test double), AD069 (two writers of the run
record, and the loser held it), AD071 (two derivation failures had no named class and therefore no next
action), AD072 (a walkthrough case raised the wrong error and so appeared to pass), AD073 (an enumeration
that was unbounded when full and raised when empty), and AD074 (a miscited authority, corrected). One,
**AD070**, *removes* scope: it withdraws a correction to the pre-existing update path that would have made
the existing mutating command start removing destination relationship peers. It is ratified, and its revisit
map stands as the record: were it revisited, that change would return and would need a requirement, a
criterion and a documentation entry of its own, plus the brief owner's decision, because it changes what an
existing command does to destination data.

A final ten — **AD075 through AD084** — were ratified at the delivery gate after the third and last
critique round, in which two of the three lenses returned no blocking finding. They are carried in the
[round three](#session-2026-07-27--critique-round-three-ratified) session. All ten correct this run's own
delivery and **none adds anything that ships**: AD075 (the reconciled peer set was never issued to the
destination, and every specified observable was satisfied without issuing it), AD076 (the keyedness gate
was stricter than its own claim for a kind declaring no convergence key, and its accessor was one level
short of the payload), AD077 (a second ground for the delete re-derivation was unsound and is withdrawn,
which makes the brief owner's override necessary rather than confirmatory), AD078 (the disclosure that
justified not refusing had no pinned level, no specified content, no test and no docs clause), AD079 and
AD080 (two run-guide steps promised more than the environment or the narrowed guarantee delivers), AD082
(one error class was raised for two conditions and routed only one of them), AD083 (the new write surface
was discoverable only from a refusal message) and AD084 (the provisional framing is removed now that the
gate has passed). One, **AD081**, changes nothing here at all: it records for the planner that a brief
dependency row marked satisfied is satisfied only for kinds whose convergence key is all-direct. Nothing
in this delivery edits the brief.

One further decision, **AD085**, was ratified after implementation began and is carried in the
[empty peer set](#session-2026-07-27--the-empty-peer-set-amending-ad075) session. It **amends AD075** and
nothing else: AD075's conclusions and its implementation stand, but its stated mechanism was wrong for the
empty-set case, so the flush becomes a full update that suppresses nothing. It adds nothing that ships
beyond that one call, and it adds two test obligations — the empty-set case asserted on the rendered
mutation rather than on a test double, and a tripwire that fails loudly if the destination library's
suppression behavior changes under the version range the project pins.

Two final decisions, **AD086** and **AD087**, were ratified after implementation and are carried in the
[write surface as a type](#session-2026-07-28--the-write-surface-as-a-type-and-a-shipped-release-note)
session. Neither adds anything that ships to the operator. **AD086** replaces the by-name reach for the
destination write surface with a structural type, which fixes the **static** boundary and — as that
decision states in its own words — leaves FR-023's runtime refusal exactly as strong as it already was;
the runtime enforcement it declines to absorb is reported to the planner rather than implemented.
**AD087** reverts an edit to a shipped release note and keeps every current-documentation correction,
on the ground that a shipped note records what a release claimed and is not where a claim is quietly
amended. Both scope boundaries they went past — how a non-conforming destination is detected, and which
documentation a delivery may edit — were absent from the brief and are reported as brief gaps.

One last decision, **AD088**, was ratified after a convergence assessment found the delivery's one CRITICAL
defect, and is carried in the [targeted write](#session-2026-07-28--the-flush-as-a-targeted-write-amending-ad085-and-correcting-its-attribution)
session. It **amends AD085's flush form** and nothing else, and it adds nothing that ships beyond changing
what one write sends: the flush becomes a targeted write of the replaced relationship fields instead of a
whole-node update, because a whole-node render nulls every unmapped optional cardinality-one relationship
and FR-013 forbids touching an unmapped field. It resolves a contradiction **inside FR-013** — two MUSTs
that could not both hold — and names both of them rather than overwriting one. It also **corrects an
attribution**: the defect was latent in AD075's original design and did not arrive with AD085, because the
null goes out under both of the library's render modes. Its two test obligations are an assertion that the
issued flush names no unmapped field, the fixture shape that assertion needs, and an SDK-boundary tripwire
re-pointed from the suppression behaviour to the render behaviour.

Nothing here remains open. What remains genuinely deferred is not a design commitment:

- **Plan size and review performance.** The brief sets no volume or latency target, so none is
  invented here. The encoding chosen in AD001 is line-oriented specifically so a large plan can be
  summarized and detailed without loading all of it, which is the property a later target would
  need; no threshold is asserted. The exclusion itself is recorded in
  [Out of Scope](#out-of-scope). *(per AD030)* `[AD030]`
- **How a missing destination unique constraint is detected** for the FR-024 warning. The requirement,
  its two conditions and the warning's content are fixed (AD044), and both conditions are readable
  from the same cached destination schema; the exact traversal is a planning-phase choice with no
  cross-outcome contract attached. *(per AD044)* `[AD044]`
