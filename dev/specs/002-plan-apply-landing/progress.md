# Progress ledger — Spec 002 (INFP-653 landing)

Orchestrator ledger. Removed with the spec before the PR (per the gates checklist).

## Pre-flight facts (2026-07-29)

- PR #143: **OPEN, not merged** → WI-000 slice 4 is a pure byte-exact restore, no re-apply.
- `main` == `origin/main` == `9edc1bc` == merge-base with the run branch → no interim `main`
  drift; all six slices restore byte-exact with no exceptions.
- No `speckit-run/*` tag exists yet — WP-0 creates and pushes it.

## WP status

| WP | Status | Agent | Commits | Gates | Notes |
|----|--------|-------|---------|-------|-------|
| WP-0 | **done** (2026-07-29) | agent 1 | `92b0e51` `9646da7` `758c62a` `5034044` `e271cc7` `2853309` | format clean (0 files changed); pytest 715/11/1 exact baseline match; ty exit 0; yamllint 0; pylint exit 28 (see deviation 2) | Byte-exact verified per slice; slice 6 = exactly the 15 design artifacts; CLAUDE.md kept at main's; residual diff vs run branch = exhaust + CLAUDE.md + spec-002 only. Tag created locally (see deviation 1). |
| WP-1 | **done** (2026-07-29) | agent 2 | `aeac2e4` (FIX-008) `54c848b` (FIX-009) `54407c3` (LOC-06) `3b1ece2` (LOC-03) `1ee3b31` (LOC-04) `376dd9e` (LOC-05) `ee6fb28`/`19f42e7` (FIX-009 lint follow-ups) | format clean; ruff/yamllint/ty clean; pylint 9.77 (only reductions); pytest 718/11/1; fail-before evidence captured for FIX-008/009 tests | `utils.py` touched (FIX-009's own finding names it). Design note: `verify_plan` takes the raw-bytes bundle, not `LoadedPlan` — torn artifacts must still be verifiable (FR-009); analysis allows "equivalent immutable bundle". |
| WP-2 | **done** (2026-07-29) | agent 4 | `76bde6b` (LOC-10) `77d88e2` (FIX-003+RIG-07) `b04f9e3` (FIX-005+SIM-07) | format clean; ruff/yamllint/ty clean; pylint no new messages; pytest 761/12/1 (+39); fail-before evidence captured | Manifest field `destination_binding` `{url, branch}` (absent when unbound, checksum-covered); flag `--allow-destination-change`; check name `destination_binding` in the new `VerificationCheck` TypeAlias. Declared deviations: one-kwarg touch in `potenda/__init__.py` (only seam into the writer; accepted), `utils.py` seam work, `b04f9e3` amended pre-push to fold regenerated cli.mdx (disclosed, no remote branch). |
| WP-3 | **done** (2026-07-29) | agent 6 | `09b25a5` (FIX-006+SIM-06) `8b09f68` (FIX-011) `a56516c` (LOC-07) `c1001d6` (MIN-016+RIG-09/10) `29c4e76` `9c99b34` (follow-through) | format/ruff/yamllint/ty clean; zero new pylint msgs; pytest 806/12/1 | `ApplyRecord.failed_operation: str \| None`; computed `may_have_partially_written`, `skipped_delete_count`; `run.json` summary now 5 keys. Boundary `OPERATIONAL_APPLY_FAILURES = (PlanArtifactError, SkippedDeleteOperation, infrahub_sdk.exceptions.Error)`. Reviewer notes: `httpx` deliberately excluded from the boundary (undeclared dep); SDK error imported at potenda module level (+~335 ms import, documented trade). Touched errors.py/adapter/reader messages with stated reasons. |
| WP-4 | **done** (2026-07-30) | agent 7 (resumed after interruption) | `2b752e4` (FIX-010 p1+RIG-01) `bf627b2` (FIX-010 p2) `66807db` (FIX-012) `6200e9c` (FIX-004) `27c8bd3` (MIN-005) `af29761` (MIN-006) | format clean; ruff/yamllint/ty clean; pylint 9.80, no new msg ids; pytest 833/12/1 (+27); cli.mdx regenerated | Flag `apply --expected-checksum <sha256-hex>` compared against the checksum **recomputed from stored bytes**, enforced in `apply_cmd` above the seam (zero destination construction). Redaction policy: field-name match on password/passwd/passphrase/secret/token/credential/api_key/apikey/private_key at every nesting level → `<redacted: …>`; values >200 chars elided with length stated (deliberately distinct from redaction). FIX-012 renderer lives in `cli.py` (review.py returns data by contract; MIN-022 moves it post-merge). `sync` has no `--run-id`, so its half is the writer-level invariant. **`cli.py` is at 994 lines, 6 under pylint C0302** — anything added before MIN-022's split trips it. |
| WP-5 | **done** (2026-07-29, landed) | agent 3 | on-branch: `418de6d` (MIN-012) `0664c7d` (FIX-002) `729fbf0` (FIX-001) `5f4012b` (MIN-010 pin) | worktree gates clean (pylint 9.76, no new msgs; pytest 719/12/1); cherry-picked clean; combined-tree gates below | MIN-010 **resolved** as FIX-001 by-product (flush now stamps peer lineage; PR #143 parity). MIN-009 **moot** — flush path now issues zero destination reads; WP-10 records "resolved by FIX-001", no perf issue. Live shrink test written (`tests/integration/test_infrahub_replace_set_shrink_integration.py`), runs in WP-8. `tests/plan/test_apply_conformance.py` touched with stated reason. |
| WP-6 | **done** (2026-07-29, landed) | agent 5 | on-branch: `f1c1dba` (MIN-015) `81fbe6c` (FIX-013) `352d2d3` (MIN-024) `00cf432` (MIN-001) `92fbb54` (MIN-002) `f014272` (MIN-003) `67ddaac` (MIN-025) | worktree gates clean (760/12/1, +38); import-block conflicts vs WP-2 in models.py/verify.py resolved at landing (orchestrator); merged-tree: format clean, ruff/yamllint/ty clean, pylint 9.79, pytest **799/12/1** | FIX-013's apply-level evidence showed the mismatched record was previously dispatched to the destination; MIN-003's showed an out-of-run-dir snapshot previously verified clean. |
| WP-7 | **done** (2026-07-30, landed) | agent 8 (resumed after interruption) | on-branch: `f8a29c8`… → see `git log`; worktree SHAs `8930d00` (FIX-014) `8dcfd8a` (MIN-007) `86a361a` (MIN-008) `baf9514` (DISC-002) `5479fe9` (MIN-013) `e47d1cb` (MIN-014) `2e2269c` (MIN-023) | **`invoke lint` exit 0 end-to-end** (its deliverable); pylint 9.79→9.89; pytest 832/12/1 (+26); cherry-picked clean (errors.py auto-merged) | FIX-014 byte-identity proven by replaying the pre-fix implementation verbatim: identical digest+row count across batch sizes 1/2/7/13/10000 × 3 shapes (0/1/40 rows, 6 row groups) = 15/15. **Deviations to review:** (a) the pylint gate needed two flags — `--fail-under=9.5 --fail-on=E,F` — because pre-existing W/R messages leave `--fail-on=E` alone at exit 12, so this is a repo-wide lint-posture change riding in a feature PR (one-line revert); (b) MIN-023's sweep exempts all of `dev/specs/` (spec 002 itself quotes the stale prefix while describing the fix), guarded against vacuous passing; (c) MIN-008 labels an empty multi-candidate set with `candidates[0]`, inert at apply time; (d) MIN-007 raises rather than warns, before the no-op filter. DISC-002 needed no review-surface change (`review.py` renders no warnings; plan warnings reach the reviewer via the log stream). |
| WP-8 | blocked on review collation | — | — | — | gates the PR (DISC-003) |
| WP-9 | blocked on review collation | — | — | — | **must also carry the three unassigned spec items below** |
| WP-10 | blocked on WP-8, WP-9 | — | — | — | drafts only; human posts |

## Deviations

1. **Tag push blocked.** `speckit-run/001-plan-artifact-saved-apply` was created locally
   (points at `2a98449`, the run branch tip) but `remote.origin.pushurl` is set to `DISABLED`
   in this checkout — a deliberate push guard the agents will not override. Pending human
   action: `git push origin speckit-run/001-plan-artifact-saved-apply`.
2. **Lint baseline drift.** `uv run invoke lint` exits 28 (pylint 4.0.5 C/R/W warnings;
   invoke aborts before yamllint/ty). Reproduced byte-identically against the run branch tree
   with the same venv, so the spec's "lint exit 0" baseline came from a different resolved
   toolchain — not an intake regression. Individually: ruff clean, `yamllint .` exit 0,
   `uv run ty check .` exit 0. Working gate definition for WP-1…9: ruff/yamllint/ty clean and
   **no new pylint warnings vs this baseline**; MIN-014 (WP-7) addresses the touched-code
   pylint warnings and the optional `--fail-on=E` severity gate.
   **Correction (WP-2):** the earlier "lint exit 0 after WP-1" note was an orchestrator
   measurement error (a pipe captured `tail`'s exit code, not invoke's). `invoke lint`
   still exits 28 from pre-existing potenda C0415 messages; the working gate stands:
   ruff/yamllint/ty clean + zero new pylint messages, until WP-7 (MIN-014) restores exit 0.

## Spec gaps found by the orchestrator's coverage audit (2026-07-30)

Three items the spec marks **fix now** appear in no WP row of the work-package table, so no
agent was assigned them. Folded into WP-9's dispatch (all three are prose/docstring work,
which is WP-9's medium):

1. **MIN-004** — the `_atomic_write_bytes` fsync tradeoff docstring caveat ("no code change",
   per its own disposition). Named only in the Execution-order prose, never in a WP row.
2. **MIN-019** — narrow the adapter-writing docs to "Infrahub-only in v1" (OQ-10 decided (b));
   the follow-up issue half is WP-10's.
3. **FIX-007's docs disclosure** — OQ-7 requires the warn-and-proceed v1 limit disclosed
   "prominently in the PR body **and docs**". WP-10 owns the PR-body half; the docs half was
   unassigned. The strict-xfail conformance test already stands as the in-tree marker.

## Fix-pass completion audit (2026-07-30)

All 14 FIX items accounted for: FIX-001/002 (WP-5), FIX-003/005 (WP-2), FIX-004/010/012 (WP-4),
FIX-006/011 (WP-3), FIX-008/009 (WP-1), FIX-013 (WP-6), FIX-014 (WP-7). FIX-007 is a decision
to confirm, not code — see gap 3 above. Every changed source file falls inside some WP's
declared scope; `cache/parquet_io.py`, `utils.py`, and `tasks/linter.py` were declared
out-of-row touches with stated reasons.

Combined-tree gates after all seven packages (HEAD `4e6eb30`): `invoke format` clean,
**`invoke lint` exit 0 end-to-end**, `ty check` exit 0 (3 known pre-existing warnings),
`pytest -q` **859 passed / 12 skipped / 1 xfailed**. Source diff vs the intake baseline:
16 files, +1705 / −494.

## Session interruption (2026-07-30)

The orchestrator session was interrupted while WP-4 and WP-7 were in flight. Both trees were
clean at `9c99b34` with no stashes and no partial commits — nothing lost. Both agents were
resumed from their own context with the verified state and an instruction not to trust
remembered file contents; both then completed normally.

Backup posture: the local push guard (`remote.origin.pushurl=DISABLED`) was lifted by Blake on
2026-07-30. The tag `speckit-run/001-plan-artifact-saved-apply` → `2a98449` and the branch
`feature/plan-apply-infp653` are both now on GitHub (branch pushed at each WP boundary; no PR
opened, per the work order and DISC-003's gate). A local bundle also exists in the session
scratchpad. History is append-only — no force-push has been or will be needed.

## Cross-cutting consistency review (2026-07-30) — WP-1…7 combined diff

Verdict: coherent enough for human reviewers **once six blocking findings are fixed**; no
shipped guarantee found broken (bytes-verified = bytes-applied holds; FIX-010 immutability
holds at writer and pre-extraction; FIX-014 digest byte-identity structurally sound). Findings
dispatched as RF-1…RF-8 on worktree branch `rf/review-findings`:

- **RF-1** (reproduced crash) `apply --expected-checksum` dies with a raw `UnicodeDecodeError`
  and zero operator output on a non-UTF-8 manifest — WP-4 hand-copied a manifest read that
  WP-1/WP-2 had already hardened, the exact divergence LOC-03 existed to remove.
- **RF-2** MIN-012's refusal says "re-plan" while FIX-011's defect arm says "do not re-plan";
  both reach the operator in one line. Fix strips the remedy, keeps the deliberate `ValueError`.
- **RF-3** FIX-002's empty-filter refusal inherits "Create the peer at the destination", which
  would duplicate a peer that almost certainly exists.
- **RF-4** dev docs still show `verify_plan(run_dir=…)` and load-before-verify (both removed by
  FIX-008). **RF-5** `destination_binding` absent from both manifest format references.
  **RF-6** `ApplyRecord` docstring still says three summary keys (now five).
- **RF-7** FIX-003's own "consider the same at plan-write time" was never folded in — a
  read-denied snapshot still escapes `diff` as a raw `PermissionError`. **RF-8** the three new
  write-path taxonomy members reach operators as tracebacks (deferred if `cli.py` would pass
  pylint's 1000-line ceiling; it is at 994).

Non-blocking duplication/style findings (D-1…D-6 and the stale line citations) are WP-9's.

## WP-8 live batch (2026-07-30)

**FIX-001's live shrink test PASSES — no escalation.** Infrahub's `<kind>Update` **replaces**
cardinality-many relationship lists rather than merging: N=3 → 1 kept exactly the surviving
peer, 1 → 0 emptied the set, and `shrunk_id == emptied_id == team_id` proves the assertions are
not vacuous. OQ-4's pin-and-reword stands; ADR-0003's reworded claim is now pinned by live
evidence; PD-005/AD038's "declared unknowable" is now known. Peer removal on apply works.

**E-2 — DISC-003 escalation: RESOLVED BY BLAKE 2026-07-30 → disposition (b), strengthened
caveat.** The investigation proved the defect **general to the extraction engine, not
NetBox-specific**, which is the pre-decided escalation trigger. Isolated mechanism:
`model_loader` fetches `client.all(kind=…, include=list(model._attributes), populate_store=True)`
— attributes only — which writes relationship-less **brief peer nodes** into the SDK store,
overwriting the complete nodes cached when the peer's own kind loaded earlier in tier order;
`resolve_peer_node`'s guard `_node_has_complete_attributes` inspects only non-optional
attributes, never relationships, so it approves the stub and the corrective re-fetch never
fires; `infrahub_node_to_diffsync` then drops the id-less cardinality-one relationship and
`_resolve_peer_unique_id` cannot rebuild a peer identity whose `identifiers` cross a
relationship → `PeerIdentifierError`. Every function in that chain is **AST-identical to
`origin/main`**. Source-independent (reproduced with the source adapter never constructed), not
incremental-cache dependent, and the trigger shape appears in **6 of 14 shipped example configs
across 5 source adapters**. Whether it bites is accidental: a `DcimDevice` stub fails the guard
and is re-fetched (works); an `InterfaceLag` stub passes it (fails).

Why (b) rather than the literal (a): OQ-9's blocking rationale is "a re-plan step that reliably
crashes is not shippable **without a stated workaround**". A workaround exists, is named by the
error message itself, and was verified live — `--continue-on-error` completes extraction,
dropping only the unresolvable peer link. Combined with the defect being provably pre-existing
on `main`, blocking this PR would not make users safer. Fixing it here was offered and declined
as scope expansion: it would invalidate the "extraction path is byte-identical to `main`" claim
that four reviews relied on.

Also found on the tutorial path, both pre-existing and both to be stated in the PR body: the
stale generated example adapters break `diff` outright (`AttributeError: 'NetboxSync' object
has no attribute 'IpamRouteTarget'` — DISC-004, deferred to INFP-652), and the public demo data
carries a duplicate `IpamIPAddress` identity that fails source extraction.

## Review-finding fixes (RF-1…RF-9) — landed 2026-07-30

All nine on the branch (cherry-picked from `rf/review-findings`, no conflicts): `3122aa4`
(RF-1) `63317a1` (RF-2) `25bbef0` (**RF-9** lint-gate revert) `cc7bda8` (RF-3) `85c0fc8`
(RF-4) `a3a75ce` (RF-5) `b79d5a2` (RF-6) `cc17911` (RF-7) `57940cb` (RF-1 follow-up rename).
Every finding reproduced as the reviewer described it. RF-9's revert is **byte-exact** against
the pre-fix-pass baseline (`git diff 69063ff HEAD -- tasks/linter.py` is empty).

**RF-8 deferred on measurement, not assumption.** The agent implemented it fully, measured
`cli.py` at **1014** lines (pylint `C0302` at 1000 → a new message), and reverted; even a bare
8-line version reaches 1003. `cli.py` final: **995**, zero `C0302`. The real unblock is
MIN-022's `cli.py` split, already a post-merge follow-up. WP-10 should file RF-8 with it.

Two disclosed judgment calls, both accepted: the RF-1 helper is named `manifest_mapping_or_none`
because `manifest_mapping` collided with an existing `plan_checksum_failure` keyword (a new
`W0621` otherwise), landing as a second commit rather than an amendment; and RF-6's identical
"three keys" wording in `dev/specs/archive/…/data-model.md` + `tasks.md` was deliberately left
alone as archived spec-001 history adjacent to WI-000's byte-exactness constraint.

### Final pylint numbers for the PR body (measured post-revert)

`uv run pylint infrahub_sync/` → **exit 28** (bitmask 16 C + 8 R + 4 W, not a failure count),
**31 messages: 15 C, 11 R, 5 W, 0 E, 0 F**, score **9.89/10**. Versus the pre-fix-pass baseline
`69063ff` the fix pass **net removed 32** messages (63 → 31), and at file+message-id granularity
there is **not one new pair**; `infrahub_sync/plan/`, where this feature mostly lives, carries
zero messages. One existing threshold deepened rather than a new message: `cli.py` `R0917`
6/5 → 8/5, from `apply_cmd`'s two new flags. State it that way.

### `ty` gate: a pynetbox-dependent result worth a follow-up

On the final tree `ty` reports **6 diagnostics, exit 1** in the WP-8 checkout but **3
diagnostics, exit 0** in the RF worktree — on **byte-identical** Python and test code
(`git diff rf/review-findings HEAD -- infrahub_sync/ tests/` is empty). Cause: WP-8's ad-hoc
`uv pip install pynetbox` (the runbook's §5 step; `pynetbox` is declared in **no** extra). With
it installed, `ty` can resolve the import and then surfaces two genuine **errors** —
`unresolved-attribute: Unresolved attribute 'devices' on type 'App'` at
`tests/adapters/test_netbox_incremental.py:88,132` — plus a now-unused `ty: ignore` at
`infrahub_sync/adapters/netbox.py:8`. Both files are **untouched by this branch** (no commits in
`main..HEAD`), so these are pre-existing and masked in CI. **The gate is met** (exit 0 with the
declared dependency set, which is what CI runs), but the underlying hole — an adapter whose
dependency is undeclared is never type-checked or tested in CI — is a real follow-up for WP-10.
A `uv sync` restores the 3/exit-0 result locally.

## WP-8 completion (2026-07-30) — the PR gate is cleared

Commits: `a2826b4` (tutorial caveat, DISC-003 disposition (b)) and `f340654` (shrink-fixture
schema-readiness poll).

**Integration gate GREEN: 9 passed / 2 skipped / 0 errors, exit 0** against a freshly reset
destination (7 → 9 over R2 = the shrink test plus `test_applying_a_stored_plan_runs_no_extraction`).

- **R2's "one setup error" — WP-8's own prediction was FALSIFIED, and the truth is better.**
  It was never DISC-003. It is SC-016's live half (`test_an_ambiguous_peer_refuses_the_operation`),
  already converted from a hard error into a reasoned skip by `12d7a27` **before this landing work
  began**: seeding a genuinely ambiguous peer needs a kind whose uniqueness constraints do not
  cover the resolver's filters, and every kind the qualified config touches declares one that does.
  Nothing was left to clear.
- **A different, real error was found and fixed** (`f340654`): the new shrink test passed alone but
  errored in-suite with `SchemaNotFoundError: Unable to find the schema 'TestShrinkTag'`.
  `POST /api/schema/load` returns when a payload is *accepted*, not when its kinds are *queryable* —
  confirmed outside pytest (`infrahubctl` reported "16 schemas processed" while `/api/schema` served
  56 of 82 kinds). Fix is a bounded 90s readiness poll; no assertion touched.
- **The suite's non-repeatability IS DISC-003**, now proven: a deliberate warm second pass gave
  `2 passed / 1 skipped / 8 errors`, all eight dying in fixture setup on the
  `PeerIdentifierError … missing identifier key(s) ['device']` chain.
- **SC-003's `InjectedCrashError` verified live** — `test_the_write_class_conformance_matrix` passed
  for all three write classes; **6 crash injections all escaped unwrapped** and each post-crash
  re-apply returned state to its clean single-run counts. WP-3's FIX-011 change is confirmed
  against a real destination.

Evidence coverage: **SC-001, SC-002, SC-003, SC-008 and SC-007's live half** evidenced live
(DBA-001/002/003/008 + DBA-007's live half), plus FIX-001/OQ-4's replace-semantics pin.
**SC-016's live half is unseedable by design** and stays a reasoned skip. **The unbounded
tutorial path end-to-end is not evidenced**, blocked by two pre-existing defects, neither
this feature's: the stale checked-in example adapters (`AttributeError: 'NetboxSync' object has
no attribute 'IpamRouteTarget'` — INFP-652) and a duplicate identity in the public demo data
(two of 8,382 IPs share `address`+`vrf(None)`). Neither can reach the suite, which narrows the
qualified config to nine kinds, generates its own adapters into a temp workspace, and drops every
`IpamIPAddress`/`IpamVLAN`-referencing field.

Accepted deviation: the tutorial caveat carries **no follow-up issue link**, matching the
convention of the two existing recorded limitations in `running-a-sync.mdx` (prose, no link); a
placeholder URL would ship broken. WP-10 adds links after filing.

**A FOURTH unassigned spec item, found by WP-8:** DISC-001's action requires a docs/release-note
entry stating derivation failures are fatal even under `--continue-on-error`. No such statement
exists in `docs/`. Added to WP-9's dispatch alongside MIN-004, MIN-019 and FIX-007's docs half.

## Escalations

**E-1 — RESOLVED BY BLAKE 2026-07-30: do not loosen the repo-wide lint standards.** WP-7's
MIN-014 companion shipped `pylint --fail-under=9.5 --fail-on=E,F` in `tasks/linter.py`, which
made `invoke lint` exit 0 but stopped any new C/R/W message from failing the task while the
aggregate score stays ≥ 9.5. Blake's decision: this cannot land without a team discussion, and
belongs in a separate branch/PR if pursued at all. **Action: reverted as RF-9** (`tasks/linter.py`
back to `pylint infrahub_sync/`); MIN-014's import hoists in `potenda/__init__.py` are kept, as
they fix code this feature touched. The RF agent's gate was corrected mid-flight accordingly.

Orchestrator-measured evidence for the PR body (2026-07-30) — **this branch does not regress
lint; it improves it, and `invoke lint` has never passed on `main`**:

| Tree | pylint exit | C | E | R | W | Score |
|------|-------------|---|---|---|---|-------|
| `main` (`9edc1bc`) | 30 | 39 | **1** | 11 | 5 | 9.60 |
| this branch | 28 | 15 | **0** | 11 | 5 | **9.89** |

The exit codes are category bitmasks (16+8+4 = 28; +2 for E = 30), not failure counts. Main's
sole error-class message — `E0213 no-self-argument` on the `convert_str_to_enum` pydantic
validator in `infrahub_sync/__init__.py:98` — is **fixed on this branch** (came in with intake
slice 5, `e271cc7`). So if the team later adopts a `--fail-on=E` gate, this branch already
satisfies it while `main` does not: useful framing for that separate discussion.
