# Spec 002 — Land the saved-plan apply feature (INFP-653)

**Branch:** `feature/plan-apply-infp653` (from `main`)
**Source of the work:** `001-plan-artifact-saved-apply-infp-653` — the pushed, immutable record
of what the SpecKit run produced on its own. That branch is never rewritten; this branch takes
its final tree (WI-000), fixes the collated review findings in one pass, and ships as a single
PR with logical delineation conveyed in the PR description (decided — no stacked PRs).

**Status:** WORK ORDER — review collection **closed 2026-07-29** after seven collated reviews
(R1–R7). OQ-1…9 answered 2026-07-29 (recorded inline at each item and struck through in
**Open questions**); OQ-10…12 answered below at their items.

## Reading this document

- **The code is not on this branch yet.** Until WI-000 lands, inspect any referenced file with
  `git show 001-plan-artifact-saved-apply-infp-653:<path>`. All `file:line` references are to
  the run branch's tree; expect small drift once fixes land — anchor on the named symbols, not
  the numbers. Abbreviated paths in the tables (`plan/…`, `adapters/…`, `cli.py`,
  `potenda/…`) live under `infrahub_sync/`.
- **ID vocabulary.** `FIX-`/`MIN-`/`DISC-`/`WI-`/`OQ-` are this spec's items. `AD###` (settled
  decisions), `FR-###` (requirements), `SC-###` (success criteria), `T###` (tasks), and
  `PD-###` (research details) come from the original feature spec, archived on the run branch
  under `dev/specs/archive/001-plan-artifact-saved-apply/` (`spec.md`, `tasks.md`,
  `research.md`). `DBR-`/`DBA-` are the delivery brief's acceptance IDs (planning repo).
  `R1`…`R7` are review sources — see the table in **Review collation**. `LOC-`/`SIM-`/`IHR-`/
  `RIG-` are finding ids within R4/R5/R6/R7 respectively.
- **Browsing the completed work:** a detached worktree of the run branch may exist at
  `~/repos/opsmill/infrahub-sync-run3-record` (recreate anytime with
  `git worktree add --detach ../infrahub-sync-run3-record 001-plan-artifact-saved-apply-infp-653`).
  `git show` works without it.
- **Dev setup:** `uv sync --extra dev` — plain `uv sync` does not install ruff/invoke/pytest
  plugins. The verification workflow is AGENTS.md's: format → lint → tests → CLI sanity.

## Execution order

1. **Decisions: all twelve OQs answered (2026-07-29)** — nothing is blocked on a pending
   decision. Note the DISC-003 investigation **gates opening the PR at all** — no draft PR
   before it concludes (OQ-9).
2. **Intake (WI-000).** Byte-exact restores, six commits. Then run the verification gates
   once to establish the baseline — they pass on the run branch today (format clean; lint
   exit 0; 715 passed / 11 skipped / 1 xfailed).
3. **The fix pass — one pass, one commit per item, spec ID in the commit message.**
   Recommended internal order, driven by shared code:
   - **FIX-008 + FIX-009 first**: FIX-008 restructures verify/load into load-once and
     FIX-009 gives apply its own assembly seam; FIX-005's manifest comparison, FIX-006's
     record rework and FIX-003's error classification all hang off those surfaces and land
     cheaper on top of them.
   - Pairs that share a surface: FIX-002 + MIN-012 (peer resolver), FIX-001 + MIN-010
     (relationship flush), FIX-006 + FIX-011 + MIN-016 (apply record, exception boundary,
     docs), FIX-010 + FIX-012 with FIX-004 (CLI review/apply surface), FIX-013 + MIN-015 +
     MIN-024 (artifact validation cluster in models/reader). FIX-014 (streaming snapshot
     hash) is independent — its only constraint is digest byte-identity.
   - Independent, any order: MIN-001…003, MIN-005…008, MIN-014, MIN-023, MIN-025; MIN-004 is
     a docstring caveat only; MIN-018 moved to the compaction block (LOC-19).
   - LOC-03…13 from R4 ride with the fix touching the same file (mapping in the
     **Compaction pass** section).
   - Live-environment work batched together: FIX-001's shrink test, DISC-003's investigation,
     DISC-002's plan-time warning, and the integration gate.
4. **The compaction block** (R4: prose sweeps + test consolidation) — after the fix pass,
   before the PR; no-behavior-change commits; details in the **Compaction pass** section.
5. **Gates, then the PR** — the checklist and PR-body requirements at the end of this spec.

**Live environment.** FIX-001, DISC-002, DISC-003, and the integration gate need the live
NetBox + Infrahub setup documented in
`/Users/blake/repos/opsmill/infrahub-sync-lab/.planning/live-test-environment-requirements.md`
(`pynetbox` required, in no declared extra; NetBox v1 token). Until DISC-003 is resolved the
live suite is one-shot against a warm destination — plan a destination reset between runs.

## Work packages (for orchestration)

Each package is one agent-sized task: self-contained item set, named files, gates to run
(`invoke format` → `invoke lint` → `pytest -q`) before its commits are accepted. Arrows are
hard dependencies; unmarked packages may run after WP-0 in any order, but packages sharing a
file must not run concurrently on one branch.

| WP | Contents | Files (primary) | Depends on |
|----|----------|-----------------|------------|
| WP-0 | WI-000 intake (all six slices, #143 check, tag) + baseline gates | whole tree | — |
| WP-1 | FIX-008 + FIX-009 + LOC-06, LOC-03/04/05 | `potenda/__init__.py`, `plan/verify.py`, `plan/reader.py`, `plan/review.py`, `cli.py` (apply assembly) | WP-0 |
| WP-2 | FIX-005 (+SIM-07 fold) + FIX-003 (+RIG-07 fold) + LOC-10 | `plan/config_version.py`, `plan/verify.py`, `plan/checksum.py`, `plan/models.py` (manifest field), `plan/writer.py`, `adapters/infrahub.py` (endpoint capture), `cli.py` (override flag) | WP-1 |
| WP-3 | FIX-006 (+SIM-06 fold) + FIX-011 + LOC-07 + MIN-016 | `potenda/__init__.py`, `cli.py` (except arms), `plan/models.py` (ApplyRecord), docs | WP-1 |
| WP-4 | FIX-010 + FIX-012 + FIX-004 + MIN-005/006 | `cli.py`, `plan/writer.py` (immutability refusal), `plan/review.py` (payload render), docs | WP-1 (checksum display reads the loaded bundle) |
| WP-5 | FIX-001 + FIX-002 + MIN-010/012 (+ MIN-009 notes) | `adapters/infrahub.py`, ADR-0003, live shrink test | WP-0 (live test runs in WP-8) |
| WP-6 | FIX-013 + MIN-015 + MIN-024 + MIN-001/002/003 + MIN-025 | `plan/models.py`, `plan/reader.py`, `plan/canonical.py`, `plan/verify.py`, `plan/writer.py` | WP-0; coordinate with WP-2 on `models.py`/`verify.py` |
| WP-7 | FIX-014 + MIN-007/008 + DISC-002 warning + MIN-013 doc + MIN-014 + MIN-023 | `plan/checksum.py`, `plan/derive.py`, `potenda/__init__.py` (imports), dev docs | WP-0 |
| WP-8 | Live batch: DISC-003 investigation → disposition; FIX-001 shrink test; integration gate | live env; `tests/integration/` | WP-1…7 complete; **gates the PR** |
| WP-9 | Compaction block (P3 → P4 → P5) + MIN-018/LOC-19 | tests + prose repo-wide | WP-1…7 complete |
| WP-10 | Follow-up issue filing (MIN-009/011/013/019…022, `<rel>__ids` fallback, DISC-002 product fix, DISC-003 if (b)) + PR body + gates checklist | GitHub, PR | WP-8, WP-9 |

---

## WI-000 — Pull in the work from the run branch

**First task; everything else stacks on it.** The run branch's *final tree* is what we want —
not its 58-commit history (later commits fix earlier ones; replaying would put known-bad states
in front of reviewers). Take the content in logical slices with

```bash
git restore --source=001-plan-artifact-saved-apply-infp-653 -- <paths>
```

and commit each slice **byte-exact — no edits inside intake commits**; every correction happens
in the fix pass, on the record. One guard on the byte-exactness: the run branch was cut from
an older `main`, so **before committing each slice, check whether `main` has since changed any
file in it** — a byte-exact restore would silently revert the interim change. Known live case:
**PR #143** (open as of 2026-07-29) touches `adapters/infrahub.py` (`update_node` relationship
attribution) and adds `tests/adapters/test_infrahub_update_node_attribution.py`; if it merges
before intake, re-apply its diff on top of the restored file in slice 4. The slices give a
short, bisectable history matching the review boundaries:

| # | Slice | Paths |
|---|-------|-------|
| 1 | Developer docs: knowledge, guidelines, guides, ADRs, agent-context pointers | `dev/knowledge/`, `dev/guidelines/`, `dev/guides/`, `dev/adr/`, `AGENTS.md` — **not `CLAUDE.md`**: the run branch's SPECKIT block points at `dev/specs/001-plan-artifact-saved-apply/…` paths that the extraction commit moved to `archive/`; keep main's version (R3, landing-plan construction step 3) |
| 2 | The plan package and its unit tests | `infrahub_sync/plan/`, `tests/plan/` |
| 3 | Engine wiring: derivation into `sync`/`diff`, the apply loop | `infrahub_sync/potenda/`, `tests/test_potenda_plan_artifact.py`, `tests/cache/` |
| 4 | Adapter planned-write surface | `infrahub_sync/adapters/infrahub.py`, `tests/adapters/test_infrahub_planned_write.py`, `tests/adapters/test_infrahub_empty_peer_set_flush.py` |
| 5 | CLI review/apply mode, user docs, example, fixtures | `infrahub_sync/cli.py`, `infrahub_sync/__init__.py`, `tests/test_cli_plan_review.py`, `tests/data/`, `tests/test_sc010_credential_canary.py`, `tests/test_live_fixture_preconditions.py`, `tests/integration/test_saved_plan_apply_integration.py`, `docs/`, `examples/netbox_to_infrahub/config.yml`, `.pre-commit-config.yaml`, `tasks/bench.py` |
| 6 | Spec archive — **design subset only** (OQ-1: decided) | The 15 design artifacts of `dev/specs/archive/001-plan-artifact-saved-apply/`: `spec.md`, `plan.md`, `tasks.md`, `research.md`, `data-model.md`, `quickstart.md`, `contracts/` (4 files), `checklists/` (5 files, **not** `checklists/reviews/`). **Excluded** (process exhaust, stays on the tagged record branch): `critiques/`, `checklists/reviews/`, `sessions/`, `retrospective.md`, `opsmill-implement-report.md`, `planner-feedback-additions.md`, `EXTRACTED.md` |

**OQ-1 decided (2026-07-29): design subset in, exhaust out.** This matches the company
convention in `~/repos/opsmill/infrahub`, whose `specs/` tree keeps completed specs' design
artifacts on main (spec/plan/tasks/research/data-model/quickstart/contracts/checklists) and
never the process exhaust (no critiques, sessions, reviews, retrospectives, or run reports
anywhere in it). Resulting PR: ~82 files — under the 100-file threshold where automated review
refuses to run. The full 36-file archive stays on the tagged record branch. Condensing the
archived `spec.md` (3,057 lines; 45% is the AD001–AD092 decision transcript) was considered
and **deferred** — a possible later pass, not this round. If it happens, AD/FR/SC IDs must
stay resolvable: code and tests cite them.

**Also in WI-000:** tag the record branch so the archive's home is stable and the PR body has
a permanent reference — the push is done; add e.g.
`git tag speckit-run/001-plan-artifact-saved-apply 001-plan-artifact-saved-apply-infp-653`
and push the tag.

---

## Fixes required before the PR (majors)

### FIX-001 — Replace-set flush cannot remove peers; ADR-0003's claim is unsound (OQ-4)

`infrahub_sync/adapters/infrahub.py:193-301`

The flush renders only the surviving peer list — `RelationshipManagerBase._generate_input_data`
emits `[{id: …}, …]` with no removal directive, so nothing about a *removal* ever reaches the
wire (verified against the vendored SDK). The fetch-and-reconcile step therefore adds nothing:
if the destination's `<kind>Update` **merges** relationship lists (the exact uncertainty
PD-005/AD038 declare unknowable), shrinking a peer set silently leaves surplus peers in place;
if it **replaces** (actual Infrahub behavior), the mechanism works but the docstring/ADR claim
"true by construction" is false — it is true by server semantics. The live integration suite
does not pin replace-vs-merge, and does not cover the shrink case (only emptied-set).

**Proposed fix:** pin the server semantics instead of hedging them —

1. Add a live integration test (`-m integration`) that shrinks a cardinality-many peer set
   (N → fewer, and N → 0) through the planned-write surface and asserts the surplus peers are
   gone.
2. Reword `_replace_relationship_set` / `_flush_replaced_relationship_sets` docstrings and
   ADR-0003 from "true by construction" to "relies on the destination Update mutation's replace
   semantics, pinned by `<test>`", and simplify or justify the now-redundant fetch/reconcile
   round-trips.

**OQ-4 decided 2026-07-29: pin-and-reword (option 1 above), including simplifying away the
now-redundant fetch/reconcile round-trips** — which also shrinks MIN-009's query cost and
mostly settles MIN-010. No runtime behavior change to the feature; peer removal on apply is
specified behavior (AD085), intentionally stronger than old `sync`'s known add-only defect
(AD070). Escalate to explicit per-peer removal mutations only if the live shrink test fails —
i.e. only if Infrahub is proven to merge rather than replace.

### FIX-002 — Peer resolver can silently bind the wrong node on a degraded filter set

`infrahub_sync/adapters/infrahub.py:488-545` *(R2 concurs: independently reported, and extends
it from the empty case to partial filters — collated here.)*

Two degradation levels, both accepted today if the query returns exactly one result:

- **Empty filter** — when a peer identity supplies no scalar values (every value
  reference-shaped), `_filter_kwargs` returns `{}` and `client.filters(kind=…)` lists
  **every** node of the kind. With exactly one node of that kind at the destination, it is
  returned, memoized (AD036), and reused for the rest of the apply.
- **Partial filter** — unresolvable HFID components are silently skipped
  (`infrahub.py:513-517`), so the query can run on a strict subset of the convergence key
  (e.g. `name` without `site`) and bind whichever single node happens to match the loose
  filter. The skipped-component case is the FR-024 plan-time-warning degraded mode, but
  nothing signals it *at apply time*.

Both are silent wrong-peer wiring — the quiet-corruption class this feature otherwise refuses
loudly. The docstring's defense ("a query that is too loose matches either nothing or more
than one") misses the exactly-one case.

**Fix (OQ-5 decided 2026-07-29):** refuse before querying when `filter_kwargs` is empty (a
`PeerNotFoundError`-family error stating no usable filter could be derived); for partial
filters, **warn** — a per-kind apply-time warning naming the dropped components. Refusal for
partials arrives later, together with the `<rel>__ids` fallback follow-up (see FIX-007), so no
working config is stranded without a workaround. Unit tests: reference-only identity with one
node of the kind at the destination → refusal; partial filter → warning emitted.

### FIX-003 — Byte-corrupt snapshot Parquet crashes verify/review with a raw `ArrowInvalid`

`infrahub_sync/plan/verify.py:244` via `infrahub_sync/plan/checksum.py:58` *(R2 concurs —
independently reported at `checksum.py:56`.)*

`snapshot_digest_and_row_count` → `read_table` → `pq.read_table` has no handling for
`pyarrow.lib.ArrowInvalid` (not an `OSError`, so `source_snapshot_failures` misses it —
reproduced with a garbage `.parquet` file). A byte-truncated snapshot makes `verify_plan` raise
an undocumented exception and `read_saved_plan` crash with a traceback, violating AD059 (every
failure carries a next action) and AD031 (review renders rather than refuses). The module's
"absent, truncated and mismatched all land on this one check" holds only for row-level
truncation.

**Proposed fix:** catch `ArrowInvalid` (and consider the same in `source_snapshot_records`,
`checksum.py:99`, at plan-write time) and classify it into the existing snapshot-failure arm.
Unit test: garbage bytes at the manifest-declared snapshot path → classified failure on the
verify path, a rendered note on the review path. **Also fold in R7 RIG-07:** a read-time
`OSError` (snapshot removed between stat and open; stat-allowed/read-denied permissions)
escapes raw for the same reason — catch it around snapshot digesting and raise
`PlanArtifactUnreadableError` naming the path. Test: digest read raises `PermissionError`
after a successful stat → taxonomy error with next action.

### FIX-004 — Traversal-shaped `--from-plan` / `--run-id` values escape as raw tracebacks

`infrahub_sync/cli.py:301` (`_review_saved_plan`), `infrahub_sync/cli.py:580`
(`_require_applicable_plan`)

Both guards catch only `PlanArtifactError`, but `require_stored_run` → `run_dir` →
`_require_safe_segment` (`infrahub_sync/cache/paths.py:22`) raises `ValueError` for a value
containing `/`, `..`, or an absolute path. Reproduced on both entry points: full rich traceback
instead of the designed one-line refusal. Safe (nothing written, exit 1) but violates the
feature's own AD059 error contract, and is reachable by pasting a run *path* where a run *id*
goes — which the docs' own `Cached run <id> at <dir>` line invites.

**Proposed fix:** map the `ValueError` into the existing `print_error_and_abort` path in both
guards (or raise a `PlanArtifactError` subclass from `require_stored_run`). Unit tests for both
commands with `../evil` and an absolute path.

### FIX-005 — A plan is not bound to its *effective* destination (R2, verified)

`infrahub_sync/plan/config_version.py:45`, `infrahub_sync/adapters/infrahub.py:689-694`

The config-version digest covers the **parsed YAML** (`settings` included, `directory`
excluded — PD-003/AD041), but the adapter resolves the effective endpoint as
`INFRAHUB_ADDRESS`/`INFRAHUB_URL` env var **before** `settings["url"]`, the token as
`INFRAHUB_API_TOKEN` before `settings["token"]`, and the branch as `settings["branch"] or
--branch`. The repo's own guidance tells users to keep credentials in env vars — precisely the
deployment where the digest is blind to the destination. Consequence: a plan reviewed against
one destination verifies cleanly and applies to a different endpoint or branch, with no signal.
The archived spec ratifies the digest rule but never addresses the env-var/`--branch`
override, so this is a gap, not an overturned decision.

**Fix (OQ-6 decided 2026-07-29): record and compare.** At plan time, write the effective
destination identity — normalized endpoint URL and branch, never the token — into the
manifest as an additive field. At apply/verify time, compare against the live values and
refuse on mismatch with a re-plan next action, overridable via an explicit
`--allow-destination-change`-style flag for deliberate cross-environment applies. Plans
without the field (older format) skip the check. Normalize URLs (trailing slash, case) so
equivalent addresses don't false-refuse. Tests: mismatch refuses; override applies; absent
field skips; normalization equivalences. **Also fold in R5 SIM-07:** define a
`VerificationCheck` Literal alias and type `VerificationFailure.check`, `GATED_CHECKS`, and
LOC-05's failure builder with it — the new `destination_binding` check is exactly the site
where a string-typed vocabulary would fail at runtime instead of type-check time.

### FIX-006 — A failed operation's write can be missing from the apply record (R2, verified)

`infrahub_sync/adapters/infrahub.py:1147-1149` (upsert precedes relationship flush),
`infrahub_sync/potenda/__init__.py:628-660` (success recorded only after the whole operation)

`apply_planned_operation` issues the base upsert, then reconciles and flushes cardinality-many
relationships. The engine appends to `applied_operations` only after the whole call returns, so
a failure during the flush leaves the destination **changed by the upsert** while the run
record and the error message ("The N operation(s) applied before it stay written") imply the
failing operation wrote nothing. Convergent re-apply (AD033) makes this operationally
recoverable, but for a feature whose contract is an accurate record of what an apply did, the
record undercounts writes.

**Proposed fix:** record the failing operation id on the `ApplyRecord` (e.g.
`failed_operation` plus a `may_have_partially_written: true` marker), correct the engine error
message and the "write was issued" phrasing, and update `running-a-sync.mdx` /
`planned-write-and-apply.md` to state that a failed operation may have partially written and
re-apply converges it. R2's "misleading documentation concerning partial-apply records"
item folds in here. **Also fold in R5 SIM-06:** make `skipped_delete_count` a computed
property instead of a stored field (serialized `as_summary_keys()` shape unchanged) — while
adding the new failure fields, a stored count is exactly the kind of contradictory state
whose validator could mask the destination error being handled.

### FIX-007 — Relationship-crossing convergence keys ship as warn-and-proceed (R2; decided OQ-7)

`infrahub_sync/adapters/infrahub.py:1015-1047`, `tests/plan/test_apply_conformance.py:434`
(strict xfail)

When a kind's convergence key crosses a relationship (or it has no HFID), the rendered mutation
carries neither `id` nor `hfid`; the adapter warns once per kind and issues the write anyway.
The strict-xfail conformance test documents that generalized safe re-application is *not*
established for these kinds — the live run converged only because the tested schema's
uniqueness constraint happened to cover it. This is a ratified scope limit (the FR-024
plan-time warning names it; the specified `<rel>__ids` fallback is explicitly deferred), so it
is collated as a decision to confirm, not a defect to silently fix.

**OQ-7 decided 2026-07-29: (a) keep warn-and-proceed, disclosed prominently in the PR body
and docs as a v1 limit, with (c) — the `<rel>__ids` fallback — filed as a named follow-up
issue.** That follow-up also carries partial-filter refusal (OQ-5) and revisits MIN-009's
scoped peer read; the strict-xfail conformance test stays as the honest marker until then.
(b) refuse-by-default was rejected: it breaks the tested live schema that converges today.

### FIX-008 — The bytes verified are not the bytes applied (R3; verified 2026-07-28)

`infrahub_sync/potenda/__init__.py:~600` (`verify_plan`) and `:~621` (`load_plan_artifact`);
`infrahub_sync/plan/reader.py:357-385`

`Potenda.apply_plan` verifies the artifact from disk, then **reads it again**, and applies the
second copy. `load_plan_artifact`'s validation is structural only — it never recomputes the
checksum — so anything that replaces the files between the two reads (a concurrent
`diff --run-id X` rewriting `plan/` while `apply --run-id X` runs is reachable through the
documented interface) applies operations that were never checksum-verified. The DBR-006/DBA-004
guarantee — a checksum-mismatched plan is refused before any destination write — holds only for
a copy that is then discarded. A gap against this feature's *own* acceptance criteria; cannot
ship as a known limitation.

**Proposed fix (small — the pieces already exist):** load once, then verify the loaded bundle —
`LoadedPlan` already carries `operations_bytes` and `manifest_mapping`, exactly what the
checksum comparison consumes. Change `verify_plan` to accept the loaded bundle instead of a
`run_dir`, and apply that same object. Constraints to preserve: `require_plan_directory` still
runs first (FR-019's verdict must not degrade), the reader's own refusals still fire before any
write, and FR-009's evaluate-all disclosure survives (T097's ordering rationale). Extend
T097's revert-asserting regression test rather than replacing it, plus a new test substituting
a structurally-valid different artifact between verify and apply.

*(Full analysis: `/Users/blake/repos/opsmill/infrahub-sync-lab/.planning/proposed-issues/bug-apply-verifies-and-loads-the-plan-in-two-separate-reads.md`.)*

### FIX-009 — Apply must not construct the source adapter or rewrite stored-run sidecars (R5 SIM-01; verified)

`infrahub_sync/cli.py:627-656`, `infrahub_sync/utils.py:183-263`

`apply_cmd` reuses the live diff/sync factory (`get_potenda_from_instance`), which
unconditionally imports and constructs **both** adapters and writes cache-identity sidecars.
Two consequences, both verified:

- **Apply requires the source it never reads.** A plan reviewed on one host cannot be applied
  from a host that has destination credentials but no source dependency/token (e.g. no
  `pynetbox`): the factory constructs the NetBox source and fails before verification. This
  directly undercuts the review-here-apply-there story FIX-005 exists to make safe. R7
  (RIG-02) independently confirmed and worsened it: `AciAdapter.__init__` performs a **live
  ACI GET** (`_build_device_mapping()`), so applying an ACI-sourced plan contacts the source
  network.
- **Apply overwrites its own evidence.** The factory recomputes `schema-sub-hash.txt` from the
  *live* destination and writes it into the **stored run's** directory before the CLI's
  comparison would read the cached value — so the stored provenance is clobbered and any
  comparison is current-vs-current. Today the comparison is dead anyway: the guard imports
  `_resolve_infrahub_schema`, which does not exist, and the broad `ImportError` arm skips the
  block silently (the AD063-documented dead code R1 flagged).

**Fix:** an apply-specific assembly seam (e.g. `PlanApplier.open_existing(...)`) that
constructs the destination only, allocates nothing, and treats the stored run's sidecars as
immutable extraction provenance; delete the dead "Plan 2" schema-guard block — FIX-005's
destination binding is the supported apply-time guard. Tests: a source adapter whose
import/constructor raises if touched while apply succeeds against a working destination;
`schema-sub-hash.txt` seeded `OLD` stays byte-identical through both refusal and successful
apply. **Sequencing:** land with/just after FIX-008 — this seam is where FIX-005, FIX-006 and
FIX-008 all hang, and doing it first makes them cleaner (R5's verdict, adopted).

### FIX-010 — **BLOCKER** — Review approval is not bound to an immutable plan generation (R6 IHR-01; reproduced by reviewer)

`infrahub_sync/plan/writer.py:144-161`, `infrahub_sync/cli.py:169-181,602-610`,
`docs/docs/running-a-sync.mdx:40,92-110`

A run id is mutable after review: `diff --run-id R` (a documented flag) rewrites
`R/plan/` with a new, internally valid plan, and `apply --run-id R` verifies **whichever plan
currently occupies the run id** — the checksum proves integrity of the current files, not
identity with the files a human approved. Reproduced by the reviewer: review showed
`role=router`, a second diff replaced it with `role=server`, verification returned zero
failures, and the operation id was unchanged (payload is deliberately excluded from it).
Distinct from FIX-008, which closes the race *inside* one apply invocation; nothing protects
the human review-to-approval interval.

**Fix, two parts:**

1. **Immutable generations (mandatory):** refuse `diff`/`sync --run-id R` once
   `R/plan/manifest.json` exists — a committed plan generation is never overwritten; re-plan
   means a new run id. Refusal message names the existing plan and the next action.
2. **Explicit approval binding (OQ-11 decided 2026-07-29: yes, both):** print the full plan
   checksum in review output and accept `apply --expected-checksum <value>`, refusing before
   destination construction when it differs — the operator's approval then names the exact
   bytes it approved.

Tests: review plan A, capture checksum; attempt same-run replacement → refused with A intact;
apply with A's expected checksum against a substituted valid B → refused before the first
destination call; corrupt-byte cases stay covered by the existing verify suite.

**Folds in R7 RIG-01** (a *failed* in-place replacement leaves the old plan applicable, and a
crash between the two file replacements disproves the manifest-last commit point *during
replacement*): part 1's immutability removes the replacement workflow outright, which is the
simpler resolution than RIG-01's proposed generation transaction. Residuals to preserve: the
refusal keys on `manifest.json` existence, so a first-write crash that never published a
manifest stays retryable under the same run id (operations-first order keeps that safe); add
RIG-01's failure-injection tests — failure before the writer and failure during manifest
publication both leave review/apply refusing the incomplete generation with zero destination
calls.

### FIX-011 — Unexpected code defects are reported as designed destination refusals (R6 IHR-03)

`infrahub_sync/potenda/__init__.py:628-660`, `infrahub_sync/cli.py:663-693`

`apply_plan` wraps **every** ordinary `Exception` from `apply_planned_operation` as
`OperationApplyFailedError`, so a programming or SDK-compatibility failure (`AttributeError`,
`TypeError`, `KeyError` after an SDK shape change) presents as an expected destination
rejection — the CLI advises destination repair/re-plan while the real problem is a code
defect, possibly after the base upsert already wrote.

**Fix (coordinate with FIX-006 — same arms):** define the operational exception boundary
explicitly — wrap the plan taxonomy plus known SDK transport/auth/GraphQL/destination
rejections; attach the partial `ApplyRecord` to unexpected exceptions and re-raise them
unchanged; the CLI's generic arm persists the carried record, then preserves the normal
traceback path. **Supersedes LOC-07's second bullet** (merging the `Exception` arm into
`BaseException` assumed the arms stay equivalent; this rework makes them deliberately
different). Tests: known destination failures wrapped with operation/run context; injected
`TypeError`/`AttributeError` escape unchanged with the run failed and partial record saved.
R7 (RIG-05) independently confirmed, adding a concrete in-tree defect example (the schema-type
guard at `adapters/infrahub.py:1123` raises `TypeError`) and a test shape: destination
succeeds once then raises `AssertionError` → the assertion escapes unchanged while `run.json`
retains the first operation id.

### FIX-012 — `--detail` hides the values and relationships apply will write (R6 IHR-02; reproduced by reviewer)

`infrahub_sync/cli.py:209-230`, `docs/docs/running-a-sync.mdx:124-138`

The deepest review renders operation id, action, kind, and identity only — no scalar payload,
no non-identity relationship peers. Reviewer reproduced two valid operations differing only in
`role=router` vs `role=server` rendering byte-identical detail rows. A human can approve an
object's *presence* in a plan without ever seeing the *change* being approved — which, with
FIX-010, is the whole point of review.

**Fix (OQ-12 decided 2026-07-29: enrich the existing `--detail`):** render the canonical
payload and each relationship field (peer kind + identity) beneath each detail record, labeled
as **desired destination state** (not a diff); define an explicit redaction policy rather than
suppressing all values.
The artifact already carries the data — no format change. Extend the SC-010 credential canary
to the new payload rendering. Tests: same-identity operations with different payloads render
differently and contain the desired values; ditto different peer sets.

### FIX-013 — The identity reviewed and hashed can disagree with the value written (R7 RIG-03)

`infrahub_sync/plan/models.py:121-149`, `infrahub_sync/adapters/infrahub.py:1125-1138,1164-1165`

`PlannedOperation._validate_record` checks that every identity field is *present* in payload
or relationships — never that the **values agree**. The model accepts
`identity={"name": "reviewed"}` with `payload={"name": "actually-written"}`, and a
relationship identity naming `site-a` beside a `RelationshipReference` naming `site-b`. Apply
builds the mutation from the payload/reference (writing the *other* object), then memoizes the
returned node under the disagreeing reviewed identity — so a later same-run reference can be
wired to the wrong node with no destination query. Review, the operation id, and the write can
each describe a different object.

**Fix (with MIN-015 — same validator):** after MIN-015 establishes one unambiguous source per
field, enforce value agreement: direct identity components equal their canonical payload
values; relationship-valued components equal the matching reference's `peer_kind` and peer
identity. A mismatch read from disk classifies as torn. Tests: scalar and cardinality-one
mismatch records refuse at model/read/apply before any destination call; matching scalar,
one-peer, and many-peer shapes stay accepted.

### FIX-014 — Snapshot hashing holds several copies of the dataset in memory (R7 RIG-06)

`infrahub_sync/plan/checksum.py:56-62,98-100`; called from `plan/verify.py:244`

Snapshot digesting reads the whole decompressed Parquet table, duplicates it as a Python list
of row dicts, materializes every canonical row encoding, then allocates the joined byte string
— SHA-256 is inherently streamable, and this stack makes peak memory a multiple of dataset
size. A kind that fits comfortably in compressed Parquet can exhaust a worker during plan
creation or pre-apply verification, making the plan impossible to create, review, or apply —
an operational ceiling that rises with exactly the scale this feature targets.

**Fix:** hash bounded record batches incrementally from one open of the file (excluding
`_extract_ts`, LF only between logical rows) so the digest stays **byte-identical**; row count
from Parquet metadata or accumulated while streaming. Tests: multi-row-group snapshot yields
identical digest/count across batch sizes including one; a spy fails the test if the path
falls back to whole-table `read_table().to_pylist()`.

---

## Disclosures and decisions

### DISC-001 — `--continue-on-error` behavior change

Derivation failures are now **fatal to `sync` and `diff` even under `--continue-on-error`**.
A dangling source reference that previously completed with `update_node`'s "Unable to find …
Ignored" warning (`adapters/infrahub.py:154-156`) now raises `SourcePeerUnresolvedError` inside
`write_plan` (`potenda/__init__.py:407-408`, `plan/derive.py:151-157`) before any destination
write, failing the whole run. This is AD047 by design, but it inverts the operational meaning
of `--continue-on-error` for source data that syncs fine today.

**Action:** a prominent paragraph in the PR body and an entry in the release notes /
`docs/docs/running-a-sync.mdx`. Not a code change unless the team overturns AD047.

### DISC-002 — Identity finer than the destination constraint silently merges objects (R3; pre-existing on main, decided OQ-8)

A `main` defect the apply path *exposes* rather than creates: when a mapping's `identifiers`
distinguish more than the destination's uniqueness constraint does (`identity ⊄ HFID`),
distinct source objects silently collapse — verified live on the qualified path:
`LocationRack` **13 → 1** (thirteen `Comms closet` racks, one per site), `DcimDevice` 3 → 1,
exit 0, no signal. FR-024 guards the *opposite* direction only (`HFID ⊄ identity` → unkeyed
write → duplication); its condition passes precisely in this dangerous case.

**OQ-8 decided 2026-07-29: (c) ship with a scoped, documented limitation plus a prioritized
follow-up issue — and land the plan-time warning this round.** The warning: when a kind's
sync `identifiers` are not fully covered by the destination's uniqueness constraint /
human-friendly ID, warn at plan time naming the kind, the uncovered fields, and the count of
source objects sharing a destination identity (e.g. `LocationRack: 13 source objects share 1
destination identity`). Both schemas are in hand at plan time, so this is derivation-side work
plus tests; the review surface shows it like other plan warnings. The underlying resolution
(tighten schema vs. loosen mapping vs. per-kind override) is the follow-up issue's product
decision. Release notes must not claim convergence; the limitation is stated in the PR body
and docs.

*(Full analysis: `/Users/blake/repos/opsmill/infrahub-sync-lab/.planning/proposed-issues/bug-identity-finer-than-destination-constraint-merges-objects.md`.)*

### DISC-003 — `diff` fails after the first successful interface sync (R3; pre-existing on main, decided OQ-9 — gates the PR)

On the qualified NetBox → Infrahub path, `diff` succeeds once; after its apply writes
`InterfacePhysical`, **every subsequent `diff` fails during extraction** until the interface
kinds are cleared at the destination. Reproduced twice by the delivery run; mechanism not yet
isolated; the extraction path is byte-identical to `main`, so this is a pre-existing defect —
but plan → review → apply → **re-plan** is this feature's core loop, and a reviewer who runs
the tutorial twice hits it. This is also the probable cause of the live suite's
second-run fixture errors (see gates).

**OQ-9 decided 2026-07-29: investigate first, and no PR — not even a draft — until the
investigation is done and the disposition is taken.** Steps: (1) investigate and capture the
actual extraction traceback (which side, which kinds, incremental-cache dependence — capture
list in the linked record); (2) disposition: **(b)** caveat the NetBox tutorial + prioritized
follow-up issue if the defect proves NetBox-specific, **escalating to (a) block** if it is
general to the extraction engine (a re-plan step that reliably crashes is not shippable
without a stated workaround); (3) state the outcome in the PR body.

*(Full analysis: `/Users/blake/repos/opsmill/infrahub-sync-lab/.planning/proposed-issues/bug-diff-fails-after-first-interface-sync.md`.)*

### DISC-004 — Smaller PR-body statements (R3)

- The stale generated example adapters under `examples/netbox_to_infrahub/{netbox,infrahub}/`
  are pre-existing on `main` and deferred to INFP-652 (which removes user-facing `generate`);
  only `config.yml` is in this diff.
- Live evidence requires `pynetbox`, which is **in no declared extra** — setup documented in
  `/Users/blake/repos/opsmill/infrahub-sync-lab/.planning/live-test-environment-requirements.md` (NetBox v1 token
  required).
- The AD066/AD067 convergence limitation is carried as a strict `xfail` (see FIX-007).
- The spec archive in this PR is the **design subset only** (infrahub convention); the full
  run record including critiques and session logs lives on the tagged record branch (OQ-1).

---

## Minor fixes

Dispositions: **fix now** = this round's fix pass · **follow-up** = post-merge issue, not this
PR · **discuss** = blocked on the named OQ · **conditional** = depends on the named OQ's
outcome.

| ID | Where | Problem | Disposition |
|----|-------|---------|-------------|
| MIN-001 | `plan/canonical.py:52,89` | `NaN`/`Infinity` floats pass the type table; `json.dumps` default `allow_nan=True` emits invalid JSON into a file the contract calls canonical. Fix: `allow_nan=False`, map `ValueError` → `UnserializablePayloadValueError`. | fix now |
| MIN-002 | `plan/verify.py:91`, `plan/reader.py:245` | Unhashable `format_version` (e.g. `[2]` in a hand-edited manifest) raises raw `TypeError` in the two components built to classify corrupt manifests. Guard `isinstance(…, int)`. | fix now |
| MIN-003 | `plan/verify.py:228-229`, `plan/models.py:158` | Manifest-supplied snapshot `path` joined to `run_dir` unvalidated — `../`, absolute paths, or symlinks escape the run dir; the model accepts any string. Validate run-relative at the model (`SourceSnapshotRecord`) **and** mirror `_require_safe_segment` at the read. Also: a record missing `path` renders as the literal string `"None"`. *(R2 concurs, rates it blocker; kept fix-now either way.)* | fix now |
| MIN-004 | `plan/writer.py:41-58` | `_atomic_write_bytes` does no fsync before/after `Path.replace`; "never observed half-written" holds for process crashes, not power loss. Matches existing sidecar discipline. | document as tradeoff (docstring caveat), no code change |
| MIN-005 | `docs/docs/running-a-sync.mdx:196` | Example shows the skipped-delete completion line at `INFO`; `cli.py:722` deliberately logs it at `WARNING` so `--quiet` still shows it (AD089/SC-007). One-word fix. | fix now |
| MIN-006 | `cli.py:139-148` vs `:216-230` | Delete-disclosure NOTE promises "(not executed)" markers "in the --detail listing" even when `--kind` narrows the listing to zero delete records. Wording tweak. | fix now |
| MIN-007 | `plan/derive.py:418-421` | Derivation never recurses into diff-element children; safe today (no generated model emits `_children`) but a custom adapter's child changes silently vanish from the plan — FR-001 violation with no error. Add a loud guard (raise or warn when an element carries children). | fix now (guard only) |
| MIN-008 | `plan/derive.py:270-282` | A deliberately empty many-peer set under a multi-candidate schema mapping raises `SourcePeerUnresolvedError.ambiguous` and kills the run. **OQ-2 decided 2026-07-29: treat an empty set as trivially resolved** — it references no peer, so no candidate kind needs choosing; AD046 stays intact for all non-empty sets. Guard clause + parametrized test (empty list under single- and multi-candidate mappings both derive). | fix now |
| MIN-009 | `adapters/infrahub.py:235-236` | Forcing the manager cold triggers the SDK's batch peer fetch with `populate_store=True` — 1 + O(peer kinds) extra queries per cardinality-many relationship per operation, loads full nodes nobody reads, and contradicts the resolver's "never reads client.store" hygiene story. A scoped id-only read would do. | follow-up (perf) |
| MIN-010 | `adapters/infrahub.py:285-287` | The flush renders peers as bare `{"id": …}`, dropping the `_relation__is_protected`/`source`/`owner` metadata the upsert set moments earlier — planned-apply-managed peers lose lineage metadata, only on kinds with cardinality-many relationships. **PR #143 raises the stakes**: it adds source/owner attribution to relationship updates on the live path, which would leave planned-apply the only write path without it — revisit during FIX-001's flush simplification. | follow-up (decide with FIX-001 rework) |
| MIN-011 | `adapters/infrahub.py:1144-1147` | Destination schema drift: a payload field no longer in the schema is silently dropped (`generate_payload_create` leaves `{}`; node construction ignores unknown keys) while the run ends `applied`. | follow-up |
| MIN-012 | `adapters/infrahub.py:510` | `PeerResolver` reads the schema via `self._adapter.schema.get()` which returns `None` for an unknown kind and silently degrades to the scalar fallback (feeding FIX-002), while the operation path raises loudly. Make it loud. | fix now (with FIX-002) |
| MIN-013 | `plan/models.py:71`, `plan/derive.py:303-307` | A planned update can never *clear* a cardinality-one peer: `RelationshipReference` cannot encode an emptied cardinality-one relationship and `_resolve_references` drops `None`-valued reference fields. **OQ-3 decided 2026-07-29: intended v1 scope limit — parity with live `sync`, which also skips `None` here.** Document in the model docstring + user docs; file a follow-up issue for "clear" support (format-version machinery covers the later extension). | fix now (document) + follow-up issue |
| MIN-014 | `potenda/__init__.py` (multiple) | ~20 new pylint `import-outside-toplevel` warnings in touched code. Lint exits 0, but AGENTS.md says fix actionable issues in touched code. Hoist or justify once. Optional companion (R3): the one-line pylint severity gate (`--fail-on=E`) so a reviewer running `invoke lint` locally isn't left asking why it's noisy — take or skip, not a crusade. | fix now |
| MIN-015 | `plan/models.py:121-151` | `PlannedOperation._validate_record` accepts duplicate `field` entries across relationship references and a field present in both `payload` and `relationships` — either makes the apply ambiguous (upsert value vs. flush value; double reconcile). Reject both at validation, with tests. *(R2, verified.)* | fix now |
| MIN-016 | docs sweep | Misleading statements to correct alongside FIX-006: the "write was issued" ordering phrasing, and the messaging around missing `plan/` directories. Sweep `running-a-sync.mdx`, `cache-layout.mdx`, and the `dev/knowledge/` pages once the FIX-006 semantics land. *(R2.)* **Plus R7 additions:** RIG-09 — `cache-layout.mdx:29-32` falsely calls `plan/` apply's *only* input (verification rereads the manifest-bound `A/*.parquet` snapshots; the CLI also needs config + destination access) — an operator archiving only `plan/` gets a refusal; RIG-10 — the delete-dispatch comments (`plan/errors.py:29-47`, `adapters/infrahub.py:1079-1118`, `adapter-anatomy.md:85-91`) promise plan-level skip/record behavior on the *direct adapter* path where only Potenda's loop performs it — scope those statements to the apply loop. | fix now (with FIX-006) |
| MIN-017 | `dev/specs/archive/…/sessions/session-2026-07-28-1136.md:57`, `dev/specs/archive/…/opsmill-implement-report.md:344` | The checked-in verification evidence is internally inconsistent: one session records INCOMPLETE with lint failing; the report addendum shows different test/type counts. **Moot under OQ-1's decision** — both files are process exhaust, excluded from the PR; they stay as-is on the record branch. *(R2.)* | resolved (moot) |
| MIN-018 | `.pre-commit-config.yaml` | The branch excludes all of `^tests/data/` from `trailing-whitespace` and `end-of-file-fixer` while its own comment justifies exactly one file. **Superseded by LOC-19**: the compaction pass deletes the byte-exact help baseline that motivated the excludes, so revert them entirely (with their comment) instead of narrowing. *(R3 → R4.)* | fix in compaction (LOC-19) |
| MIN-019 | `plan/write_surface.py:29-52`, adapter guides | The `PlannedWriteDestination` protocol — documented as the extension boundary for *any* destination adapter — types its methods with Infrahub's concrete `PeerResolver`, so a future non-Infrahub destination can't statically conform without importing the Infrahub adapter. Options: (a) make the resolver type generic/adapter-neutral now; (b) narrow the adapter-writing docs to "Infrahub-only in v1" and file the neutral boundary as a follow-up. **OQ-10 decided 2026-07-29: (b)** — narrow the docs to "Infrahub-only in v1"; file the neutral boundary as a follow-up. *(R5 SIM-02.)* | fix now (docs) + follow-up issue |
| MIN-020 | `adapters/infrahub.py` (~600 planned-write lines in a 1,247-line module) | Extract the planned-write component (`PeerResolver`, replace-set rendering, keyedness checks, operation application) into its own module with thin adapter delegates — keeps the two write protocols from drifting/conflicting (PR #143 pressure). *(R5 SIM-03.)* | follow-up (post-merge; moving 600 lines pre-PR would invalidate four reviews' line-level scrutiny) |
| MIN-021 | `plan/derive.py` (616 lines) | Split by responsibility: peer/reference resolution → `plan/references.py`, identity-coverage/convergence diagnostics → `plan/convergence.py`; keep assembly + `tier_of` in `derive.py`. *(R5 SIM-04.)* | follow-up (post-merge, same rationale as MIN-020) |
| MIN-022 | `cli.py` (787 lines) | Thin the root CLI: saved-plan rendering → `plan/cli_review.py` with injected output; apply persistence/error translation → the FIX-009 `PlanApplier` boundary (which partially delivers this). *(R5 SIM-05.)* | follow-up (FIX-009 takes the apply half now; rendering move post-merge) |
| MIN-023 | `dev/guides/adding-an-adapter.md:166`, `dev/knowledge/adapter-anatomy.md:102-104`, `plan/errors.py:3-7`, `plan/models.py:1-6` | Stale references to the pre-archive `dev/specs/001-plan-artifact-saved-apply/…` path — two broken doc links (rumdl `MD057`) plus module-docstring contract pointers. Fix to the archive path decided by OQ-1, and run a repo-wide sweep asserting no non-archive file retains the stale prefix. *(R6 IHR-04 + R7 RIG-11.)* | fix now |
| MIN-024 | `plan/reader.py:265-309`, `plan/writer.py:61-79` | Operation-id uniqueness is enforced only at write time; the reader validates lines independently, so a checksum-valid artifact with duplicate ids loads, reviews, and applies (last-write-wins by line order, ambiguous apply record). Reject a repeated id at `_load_operations` as torn, naming the id and both line numbers. Reaching this requires a hand-built artifact (the writer refuses; wholesale rewrite is FIX-010's territory), hence minor not major. *(R7 RIG-04.)* | fix now (with MIN-015/FIX-013 validation cluster) |
| MIN-025 | `plan/writer.py:49-58` | `_atomic_write_bytes` cleanup does an unguarded `unlink()` before the bare re-raise — a cleanup `PermissionError` can replace the original ENOSPC/replace failure that explains the torn artifact. Suppress (or separately log) cleanup-only `OSError`, preserving the original exception. Test: inject distinct exceptions from `replace` and `unlink`; the replace error propagates. *(R7 RIG-08.)* | fix now |

---

## Compaction pass (R4 — the LOC/reviewability review)

Source:
`/Users/blake/repos/opsmill/infrahub-sync-lab/.planning/reviews/run3-plan-apply-loc-review.md`.
Its findings are self-numbered `R3-01…R3-22` (the doc calls the run "Run 3"); they are cited
here as **LOC-01…LOC-22** to avoid colliding with review-source R3. Its verdict concurs with
R1–R3: no dead code, no unused helpers — the diff's problem is packaging, and the cure is
curation, not rework.

**Superseded / already handled:**

- LOC-01 (exclude the spec archive) — superseded by OQ-1: the design subset ships, the
  exhaust is excluded (~8k of the projected ~17k saving is realized).
- LOC-02 (stale `CLAUDE.md` spec pointers) — already handled: WI-000 slice 1 keeps main's
  `CLAUDE.md`.

**Folded into the fix pass** (same functions as FIX items; separate commits per item, but
sequenced together to avoid double churn):

- LOC-06 (hoist the duplicated `verify_plan` gate call; collapse the two-helper refusal
  split) → with FIX-008's load-once restructure.
- LOC-03/04/05 (route review's checksum check through verify; share the byte-read and
  line-split helpers — the copies have already diverged on UTF-8 handling; a
  `VerificationFailure` builder) → with FIX-003/FIX-008, which rework the same functions.
- LOC-10 (duplicate config-version regex with an incorrect defending comment;
  orchestrator-confirmed) → with FIX-005's `config_version.py` changes.
- LOC-07 (duplicate/subsumed CLI except arms; first bullet orchestrator-confirmed) → with
  FIX-006's record/message rework. **Second bullet superseded by FIX-011**, which makes the
  `Exception` and `BaseException` arms deliberately different instead of merging them.
- LOC-08/09/11/12/13 (small code cleanups: checksum wrapper stack, redundant sort,
  unreachable defensive arms, misc nitpicks) → batched with whichever fix touches the file;
  leftovers join the compaction block.

**The compaction block** — runs after the fix pass, before the PR; no-behavior-change
commits, one per sub-pass; the full test suite is the guard:

1. **Source prose sweep (P3, ~600–700 lines)** per the review's three rules: strip
   design-history rationale (keep the rule, cite the ADR), remove all cross-file line-number
   citations, deduplicate rationale stated 2–3 times across files. FIX-001 already rewrites
   the two flush docstrings and FIX-006/MIN-016 the apply-record prose — sweep what remains.
2. **Test consolidation (P4 = LOC-14…22, ~1,900 lines):** one shared recording-destination
   double, one SDK client harness, one apply-loop matrix layer, thin the 6-layer
   unrecognized-action coverage to 2–3, cut the process-artifact test files, standardize on
   the committed schema-fixture JSON. **Guards:** preserve the AD088 SDK-boundary tripwire
   when folding `test_infrahub_empty_peer_set_flush.py` into planned_write (LOC-17); keep
   the "options only under diff" CLI test when cutting the help-baseline block (LOC-19,
   which also supersedes MIN-018); be most conservative here — consolidation is where
   compaction can silently drop coverage.
3. **Test prose sweep (P5, ~1,800–2,200 lines):** module docstrings to ~5 lines, per-test
   docstrings to one line (names are already sentence-length), drop T/FR/SC/AD banners and
   citations (currently 857 references; 26% of test lines are prose).

Projected PR after fixes + compaction: ≈ 15k lines of code/tests/docs plus the ~9k
design-subset archive, ~82 files.

---

## Review collation

**Collection closed 2026-07-29** — seven reviews collated; the fix pass runs once against
this final set. (If a late review does arrive: record it in the table, dedupe against
existing IDs, give genuinely new findings the next free ID, and route judgment calls to
**Open questions** before acting on them.)

### Incoming reviews

| # | Source | Date | Scope | Collated |
|---|--------|------|-------|----------|
| R1 | Claude Code four-agent review (plan pkg / derive+engine / adapter / CLI+docs), 2026-07-28 | 2026-07-28 | full branch | yes — seeded this spec |
| R2 | External review (pasted by Blake) | 2026-07-28 | full branch, incl. archive evidence | yes — new: FIX-005/006/007, MIN-015/016/017; extended FIX-002 (partial filters); concurs: FIX-002, FIX-003, MIN-003 |
| R3 | `/Users/blake/repos/opsmill/infrahub-sync-lab/.planning/db-001-landing-plan.md` + `proposed-issues/` sweep (abandoned issue/PR sequencing; salvaged findings and intake facts) | 2026-07-28 | landing mechanics, live evidence, pre-existing bugs | yes — new: FIX-008, DISC-002/003/004, MIN-018; WI-000 corrections (CLAUDE.md, tag, file-count datapoints); MIN-014 companion; gate additions. Not picked up (post-merge or separate tracks): netbox mapping-noop (#144C), generic-FK, the speckit-* tooling items, the 0.11.9 reland, constitution/skills proposals |
| R4 | Blake's LOC/reviewability review, `/Users/blake/repos/opsmill/infrahub-sync-lab/.planning/reviews/run3-plan-apply-loc-review.md` (findings self-numbered R3-01…22, cited here as LOC-NN) | 2026-07-28 | diff size and reviewability only — not a bug hunt | yes — new: the **Compaction pass** section (P3/P4/P5 blocks, LOC-03…13 folded into the fix pass); supersedes MIN-018 (via LOC-19); LOC-01 superseded by OQ-1, LOC-02 already in WI-000 |
| R5 | Simplification/refactoring review, `/Users/blake/repos/opsmill/infrahub-sync-lab/.planning/reviews/simplify-review-2026-07-29.md` (SIM-01…07) | 2026-07-29 | structure, modularity, temporal scaffolding | yes — new: FIX-009 (SIM-01, verified), MIN-019 + OQ-10 (SIM-02), MIN-020/021/022 (SIM-03/04/05, follow-ups); SIM-06 folded into FIX-006, SIM-07 into FIX-005; concurs with FIX-001/002/006/008, MIN-010/012/014, LOC-03…07/13; confirms no OQ decision unsafe |
| R6 | Infrahub interop/operator-safety review, `/Users/blake/repos/opsmill/infrahub-sync-lab/.planning/reviews/infrahub-interop-review-2026-07-29.md` (IHR-01…04) | 2026-07-29 | Infrahub interoperability, NetOps failure modes, operator safety; ran gates in the record worktree | yes — new: **FIX-010 blocker** (IHR-01, mutable reviewed generation) + OQ-11, FIX-011 (IHR-03) superseding LOC-07's second bullet, FIX-012 (IHR-02) + OQ-12, MIN-023 (IHR-04); concurs: FIX-001/002/003/005/006/008/009, DISC-003/004; confirms no OQ-1…9 decision unsafe |
| R7 | Deep integrity review, `/Users/blake/repos/opsmill/infrahub-sync-lab/.planning/reviews/deep-integrity-review-2026-07-29.md` (RIG-01…11; deduped against the pre-R5 spec, so two of its majors re-found R5/R6 items) | 2026-07-29 | artifact/apply integrity, failure injection, scale | yes — new: FIX-013 (RIG-03), FIX-014 (RIG-06), MIN-024 (RIG-04, graded minor here — see row), MIN-025 (RIG-08); folded: RIG-01 → FIX-010, RIG-07 → FIX-003, RIG-09/10 → MIN-016, RIG-11 → MIN-023; duplicates: RIG-02 = FIX-009 (adds ACI live-GET evidence), RIG-05 = FIX-011; concurs with 20+ existing items; no OQ-1…9 decision unsafe |
*(Collection closed — no R8 expected.)*

---

## Verification gates (before opening the PR)

Per AGENTS.md, on the final tree:

- [ ] `uv sync` (with `--extra dev`) → `uv run invoke format` → `uv run invoke lint` clean
- [ ] `uv run ty check .` exits 0, no new overrides
- [ ] `uv run pytest -q` — unit suite green (baseline on the run branch: 715 passed, 11
  skipped, 1 xfailed)
- [ ] Live integration pass (`-m integration`) for the apply path, including the new FIX-001
  shrink test; R2 reports the current live suite at seven passes plus **one setup error** —
  diagnose and clear that error as part of the pass. Note the suite is **not repeatable**
  against a warm destination until DISC-003 is resolved (second run errors in fixture setup);
  budget a destination reset between passes
- [ ] CLI sanity: `--help`, `list --directory examples/`, targeted `generate`
- [ ] Docs regenerated if flags changed (`uv run invoke docs.generate`)
- [ ] One focused read of `dev/adr/` against the question *"does this stand alone for someone
  who never sees the full spec?"* — the ADRs and the design-subset archive are the only design
  trace on `main`; the critique rationale behind them stays on the record branch
- [ ] **Remove this spec from the branch** (`dev/specs/002-plan-apply-landing/`) in a final
  commit before opening the PR — it is process material, not product; its permanent home is
  the lab planning repo, and the net PR diff must not contain it
- [ ] PR body: problem/solution, before/after snippet, user-visible changes, the DISC-001…004
  statements, link to the tagged run branch as the provenance record, and a **review map**
  assigning reviewers to areas (product behavior / artifact safety / destination execution /
  evidence+docs) — destination execution (`adapters/infrahub.py`, ~620 lines resting on
  undocumented `infrahub_sdk` internals) wants the maintainer, named explicitly

## Open questions

1. ~~WI-000 slice 6: what ships of `dev/specs/archive/`?~~ **Answered 2026-07-29: the design
   subset ships (15 files, matching the infrahub `specs/` convention); process exhaust stays
   on the tagged record branch; condensing `spec.md` deferred.** Details in WI-000.
2. ~~MIN-008: empty many-peer set under a multi-candidate mapping?~~ **Answered 2026-07-29:
   treat empty as trivially resolved (bug fix, not a posture change); fix this round.**
3. ~~MIN-013: is "cannot clear a cardinality-one peer via a plan" an intended scope limit?~~
   **Answered 2026-07-29: yes — document as v1 parity with live sync; follow-up issue for
   clear support.**
4. ~~FIX-001: pin-and-reword or implement true removal mutations?~~ **Answered 2026-07-29:
   pin-and-reword + simplify; escalate only if the live shrink test disproves replace
   semantics.**
5. ~~FIX-002 partial filters: warn or refuse?~~ **Answered 2026-07-29: warn at apply time
   (per-kind, naming dropped components); empty filters refuse; refusal for partials ships
   with the `<rel>__ids` fallback follow-up.**
6. ~~FIX-005: bind plans to the effective destination or document the blindness?~~ **Answered
   2026-07-29: record endpoint+branch in the manifest and compare at apply, with an explicit
   override flag; old plans skip the check.**
7. ~~FIX-007: warn-and-proceed, refuse-with-override, or `<rel>__ids` fallback now?~~
   **Answered 2026-07-29: warn-and-proceed + prominent disclosure; `<rel>__ids` fallback is a
   named follow-up issue (also carrying OQ-5's partial-filter refusal).**
8. ~~DISC-002: disposition, and does the identity-coverage warning land this round?~~
   **Answered 2026-07-29: (c) documented limitation + follow-up issue; yes, the plan-time
   warning (with merge counts) lands this round.**
9. ~~DISC-003: which disposition, and what does the investigation gate?~~ **Answered
   2026-07-29: investigate first; the investigation gates opening the PR at all (no draft
   beforehand). Disposition (b) if NetBox-specific, escalate to (a) if general.**
10. ~~MIN-019: adapter-neutral protocol now, or docs narrowing + follow-up?~~ **Answered
    2026-07-29: (b) — narrow the adapter-writing docs to "Infrahub-only in v1"; the neutral
    boundary is a follow-up issue for the first real second implementer.**
11. ~~FIX-010 part 2: checksum shown at review + `apply --expected-checksum`?~~ **Answered
    2026-07-29: yes, both — approval names the exact bytes approved.**
12. ~~FIX-012 presentation: enrich `--detail` or add a deeper flag?~~ **Answered 2026-07-29:
    enrich `--detail` — it is already the opt-in depth; `--kind` handles volume.**
