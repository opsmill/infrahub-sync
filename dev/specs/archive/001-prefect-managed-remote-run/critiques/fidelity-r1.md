# Fidelity Critique — Round 1

**Lens**: Fidelity (brief DB-001 v2 ↔ generated spec/plan/tasks). Sole source of brief-gap findings and planner feedback.
**Reviewer scope**: brief snapshot `.../runs/DB-001/20260730T192231Z/brief-snapshot.md` vs `dev/specs/001-prefect-managed-remote-run/{spec.md, plan.md, tasks.md, research.md, data-model.md, quickstart.md, contracts/*, checklists/*}`. No repository source code read.
**Date**: 2026-07-30

## Overall verdict

The delivery artifacts carry the brief with unusual discipline. All fifteen requirements (DBR-001–015) and all eleven acceptance criteria (DBA-001–011) are present verbatim under their original IDs, each with at least one owning implementation task and at least one owning verification task in tasks.md's traceability table. The high-stakes vocabulary was compared word by word and survives intact: the `status` literal set (`planned`/`applied`/`no-change`) with the brief's exact outcome mapping; the seven-field `RunResult` with exact types and the always-present three-key `summary`; the `RunValidationError`/`RunExecutionError` split with "specific human-readable cause while redacting configured secret values ... no successful `RunResult` ... Prefect records the flow as failed"; the refusal semantics of DBR-004/DBA-006 unified into a single adapter-construction gate that is strictly stronger than either brief phrasing, not weaker. The twelve out-of-scope items retain their B-001–B-007 backlog references; no task implements any of them, and T038 audits that explicitly. R-1/R-2 are the first two isolated commits (T001/T002); R-3's schema authoring is in scope (T032) with the live-environment-ceiling fallback operationalized; R-4/R-5 are treated as inherited-and-reported, never fixed (T003/T039/T040, with an explicit "do not add pytest-timeout"). Spec sharpenings (SC-002's denominator, SC-004's negative-test set, the fingerprint definition, cross-field invariants) are measurability anchors derived from brief text, not new capability.

**The brief does NOT need intake revision.** It is internally consistent and its acceptance criteria are complete. Two instance-level brief defects were found and correctly handled as flagged checkpoint decisions (F1, F3); they require brief repair by the planner, not a return to intake.

**Blocking findings: 1** (F1, Must-Address — already queued at the human gate as D005; nothing new to build, but delivery cannot close while the brief and the artifacts disagree on the pinned Prefect version unratified).

---

## Findings

### F1 — D005: `prefect==3.5.0` replaces the brief's 3.7.2 pin — deviation correctly flagged; brief repair required

- **Severity**: Must-Address (blocking — pending human ratification at the checkpoint gate, where it is already queued as PROVISIONAL/CHECKPOINT/BLOCKING).
- **Brief section vs artifact location**: brief §Dependencies and shared contracts, row "Prefect 3.7.2 | External optional dependency | Available; version fixed for preview" vs `research.md` F1/D005, `plan.md` Summary and Constitution-gate note, `spec.md` Constraints ("Prefect 3.5.0 is the pinned optional-extra version ... PROVISIONAL (CHECKPOINT, D005)") and Assumptions dependency row, `tasks.md` T012, `contracts/prefect-flow.md` §1, `quickstart.md` Setup.
- **Exact divergence**: brief — "Prefect 3.7.2 ... Available; version fixed for preview ... current stable release selected for the Developer Preview". Artifacts — "the brief's 3.7.2 pin is unsatisfiable alongside the unchanged base dependency set (redis <5.0 via diffsync[redis] vs >=5 via pydocket in prefect>=3.6); 3.5.0 is the newest resolvable Prefect 3 release."
- **Classification (per reviewer mandate)**: **(b) a deviation the human must ratify — and the flagging is honest and complete.** The falsification is probe-evidenced (research probes a₁/a₂: metadata-level `redis<5.0` vs `redis>=5` conflict, resolver-independent), the D005 marker appears at every point where the version is load-bearing (spec Constraints, spec Assumptions, plan, tasks T012, contract §1, quickstart), the rejected alternatives are recorded (path A: redis major bump for all existing users — correctly rejected inside a preview; path C: return for intake — correctly judged unnecessary given B), and the decision is explicitly BLOCKING at the gate rather than silently absorbed. Not (c): no scope changed — the brief's own out-of-scope item "Supporting multiple Prefect major versions" is respected, and the substitution stays within Prefect 3.
- **Disposition**: **Brief-gap** (brief §Dependencies and shared contracts). Repair level: **instance** — the Prefect dependency row's version and its "Satisfaction evidence" cell are false as written and must be corrected in the next brief version (to 3.5.0 + the D006 companion pins, once ratified). Additionally a **systemic** planner-feedback note: the row's satisfaction evidence rested on VAL-6, which supplied prefect via a `uv run --with` overlay (unpinned) and therefore never exercised extra-vs-base dependency resolution. Briefs that fix a dependency version should require an install-boundary probe (`pip/uv install <pin>` alongside the base package) as satisfaction evidence, not a runtime-overlay demonstration.
- **Recommendation**: ratify D005 at the gate; issue the brief-repair feedback above so DB-001 v3 (or the batch record) carries the corrected dependency row. No artifact change needed beyond F4's checklist refresh.

### F2 — D006: companion pins (`importlib-metadata>=4.4`, `fastapi>=0.111,<0.121`) inside the optional extra

- **Severity**: Recommended.
- **Brief section vs artifact location**: brief §In scope ("An optional Prefect package dependency") vs `research.md` F2/D006, `tasks.md` T012, `contracts/prefect-flow.md` §1.
- **Exact divergence**: the brief names one optional dependency; the extra declares three. Both additions repair probe-evidenced packaging defects in prefect 3.5.0 itself (missing `importlib_metadata` declaration crashing `prefect server start`; a `fastapi<1.0` bound admitting router-incompatible releases that 500 on the deployment-name route) — without them, DBA-002's "documented commands" fail on a fresh install.
- **Classification**: **(a) a faithful technical completion of the brief** (DBR-001/DBR-002/DBA-002 require the documented commands to work), correctly flagged PROVISIONAL (CHECKPOINT) with origin "inherent" — accurate, since these are third-party defects discovered by mandated probes, not brief errors. Both pins live entirely inside the optional extra; the base dependency set is untouched (DBR-014/DBA-001 unaffected), and T012 verifies redis stays <5.0.
- **Disposition**: no artifact defect and no independent brief-gap; fold into F1's brief repair — the corrected dependency row should record the effective extra composition so the brief-owned shared contract stays truthful.
- **Recommendation**: ratify alongside D005 as one packet item.

### F3 — D007: R-1's file set expanded to include `.github/copilot-instructions.md` in commit 1

- **Severity**: Recommended.
- **Brief section vs artifact location**: brief §Pre-implementation readiness, R-1 ("As the first committed task, update the setup and workflow commands in `AGENTS.md` from `uv sync` to `uv sync --extra dev`") vs `tasks.md` T001 + decision record D007.
- **Exact divergence**: the brief's literal file list is `AGENTS.md` only; T001 also edits the verbatim mirror `.github/copilot-instructions.md` (same two lines) inside commit 1.
- **Classification**: **(b) a deviation the human must ratify — flagged honestly and (essentially) completely.** D007 is a full decision record in tasks.md, marked PROVISIONAL (CHECKPOINT), with options, evidence, and a governance origin. The rationale is sound: R-1's own stated purpose ("so every later agent reading repository instructions gets the working command") covers the mirror, and AGENTS.md's Platform-Specific Notes mandate the mirror's consistency. One evidentiary nuance the gate reviewer should know: AGENTS.md mandates *verbatim* mirroring only for the "Required Development Workflow" block and the "Approval checklist" — the Setup block (the other `uv sync` line) is consistency-driven rather than strictly governance-mandated. This does not change the recommendation; it slightly overstates "verbatim inclusion" as covering both lines.
- **Disposition**: **Brief-gap** (brief §Pre-implementation readiness, R-1 row). Repair level: **instance** — R-1's required action should name the platform mirror files (or explicitly scope the change to AGENTS.md alone if drift is acceptable), since the brief's own rationale already reaches them.
- **Recommendation**: ratify option 2 (as encoded in T001); feed the R-1 wording repair back to the planner.

### F4 — Checklists still assert the pre-D005 spec (stale "3.7.2" claims)

- **Severity**: Recommended.
- **Brief section vs artifact location**: n/a (artifact-internal) — `checklists/requirements.md` line 24 ("satisfied dependencies (CLI/Potenda, Prefect 3.7.2)"), `checklists/traceability.md` CHK021 (quotes a spec constraint "Prefect 3.7.2 is the fixed external dependency version" that no longer exists) and CHK027, `checklists/interfaces.md` CHK026 and CHK036.
- **Exact divergence**: five checklist items describe and verify a spec text that D005's remediation replaced. The spec now reads "Prefect 3.5.0 is the pinned optional-extra version ... PROVISIONAL (CHECKPOINT, D005)"; the checklists still attest that the spec pins 3.7.2 and that this "restates the brief dependency row". A gate reviewer reading the checklists as current attestations would be misled about what the spec says and about its agreement with the brief.
- **Disposition**: **Artifact defect** (staleness in review records; the spec/plan/tasks/contracts themselves are mutually consistent on 3.5.0).
- **Recommendation**: append a dated post-D005 revalidation note to the three affected checklists (or re-run just the affected items), stating that the version references reflect the pre-D005 spec and that the D005-remediated text was re-checked. Do not silently rewrite the original attestations.

### F5 — Decision-ID ledger gap: D002–D004 appear in no artifact

- **Severity**: Recommended.
- **Brief section vs artifact location**: n/a (checkpoint-packet traceability) — grep over all spec-directory artifacts finds explicit records for D001 (tasks.md header), D005/D006 (research.md et al.), D007 (tasks.md), D008 (plan.md); **D002, D003, and D004 are referenced nowhere**. Meanwhile the spec carries four PROVISIONAL (CHECKPOINT) clarifications (canonical plan fingerprint; `INFRAHUB_SYNC_CONFIG_DIRECTORY`; pinned engine defaults + pipeline lock; log bridging) that bear no decision IDs at all.
- **Exact divergence**: the run's decision inventory is D001–D008, but the artifacts only let a reader reconstruct five of the eight ID→content mappings. If D002–D004 are the un-numbered clarifications (the natural reading), nothing in the artifacts says so; the gate packet and the artifacts cannot be cross-checked ID-by-ID.
- **Disposition**: **Artifact defect** (traceability of the pending decision packet; substance of the decisions themselves is fine and each is properly marked PROVISIONAL).
- **Recommendation**: stamp each spec clarification with its decision ID (e.g. "PROVISIONAL (CHECKPOINT, D00x)") or add a one-table ID→artifact map (plan.md or tasks.md) covering D001–D008.

### F6 — T035 docs page: governance-origin scope beyond the brief's deliverable list, without a decision record

- **Severity**: Recommended.
- **Brief section vs artifact location**: brief §In scope ("One example containing setup instructions and remote request examples" — the only documentation deliverable the brief names) vs `tasks.md` T035 (new `docs/docs/reference/prefect-remote-run.mdx`, sidebar registration, cross-link from `docs/docs/orchestration.mdx`).
- **Exact divergence**: the brief authorizes one example; T035 additionally ships a docs-site reference page. This is not backlog leakage (it is none of B-001–B-007) and it is plausibly mandated: repository governance (AGENTS.md Documentation: "Update `docs/` for any user-visible changes (flags, config, adapters)") is incorporated by the brief's constraint "The implementation follows the repository workflow", and a new optional extra + flow contract is user-visible. But the run treated a *smaller* governance-intersecting expansion (D007, one mirror file) as a recorded checkpoint decision, while a whole new docs page carries only a Trace note. The asymmetry means the human gate ratifies D007 but never explicitly sees T035's scope expansion.
- **Disposition**: **Artifact defect** (missing decision record / packet entry for governance-origin scope), with a minor **systemic brief-gap** note for the planner: delivery briefs for this repository should state whether AGENTS.md's docs-governance obligation is in scope for preview features, so implementers need not infer it.
- **Recommendation**: record T035 as a governance-origin decision (or an explicit line item in the checkpoint packet) so the human can ratify or strike it; keep the task otherwise as written.

### F7 — Contract §1 install command contradicts T034's binding install-source rule

- **Severity**: Nit.
- **Brief section vs artifact location**: brief DBA-011 (README-only reproduction) vs `contracts/prefect-flow.md` §1 ("Install: `pip install 'infrahub-sync[prefect]'` (or `uv pip install ...`)") vs `tasks.md` T034 ("a preview-accurate install source — installing from the repository checkout at this branch ... NOT `pip install 'infrahub-sync[prefect]'` from PyPI").
- **Exact divergence**: the contract documents the PyPI-form install command; T034 correctly forbids it for the README because the preview exists only on this branch and is unpublished — a stranger following the contract's command would fail the DBA-011 walkthrough. T034 governs the README, so the deliverable is safe, but the contract line could leak into the docs page (T035) or mislead an implementer.
- **Disposition**: **Artifact defect** (internal inconsistency between contract and tasks).
- **Recommendation**: amend `contracts/prefect-flow.md` §1 to show the repo-checkout install as the preview-accurate form (with the PyPI form noted as the post-publication shape).

---

## Non-findings worth recording (checked, faithful)

- **DBR/DBA carriage**: all 15 requirements and 11 acceptance criteria verbatim, original IDs, origins and source references preserved; every ID has owning tasks (tasks.md traceability table is total in both directions).
- **Refusal semantics**: DBR-004 / DBA-006 / Scenario 3 unified to the single adapter-construction gate — a strengthening, documented as an informed default; the unconfirmed-sync message content sharpened, not softened.
- **Status/field vocabulary**: RunResult fields, types, `status` and `summary` literals match the brief character-for-character; immutability and "no successful result on failure" preserved and made testable.
- **Concurrency stance**: the spec's pipeline-lock statement describes pre-existing behavior moved behind the seam (guaranteed vs not-guaranteed explicitly delineated); it does not manufacture a new concurrency guarantee beyond the brief's "no guarantee" stance, and it is marked PROVISIONAL.
- **Backlog containment**: no task implements B-001–B-007; T038 audits the final diff against each item by name.
- **R-1..R-5**: R-1/R-2 as first two isolated commits (T001/T002, orchestrator-owned); R-3 schema authoring in scope with credential rules and ceiling fallback intact; R-4/R-5 inherited-and-reported, never fixed.
- **Illustrative vs committed**: the brief's illustrative material (docker-inspect token re-derivation, lab facts) stays assumption/procedure-level; no illustrative example was promoted to a delivery commitment, and no commitment was demoted to an example.
- **D008 (stdlib logging vs the structlog sentence)**: governance-origin, does not touch brief text; correctly recorded in plan.md with the constitution PATCH explicitly queued outside this run — a real deferral, recorded.

## Summary table

| ID | Severity | Disposition | One-line |
|---|---|---|---|
| F1 | Must-Address | Brief-gap (instance; + systemic evidence-method note) | D005's 3.5.0-for-3.7.2 substitution is honestly and completely flagged (classification: deviation for human ratification); the brief's falsified dependency row must be repaired by the planner. |
| F2 | Recommended | No defect (fold into F1's brief repair) | D006 companion pins are a faithful, probe-evidenced completion of DBA-002 given D005; ratify with D005 and record the effective extra composition in the brief. |
| F3 | Recommended | Brief-gap (instance) | D007's expansion of R-1 to the copilot mirror is honestly flagged and governance-sound (minor evidence overstatement noted); brief's R-1 should name the mirror files. |
| F4 | Recommended | Artifact defect | Five checklist items still attest the pre-D005 "3.7.2" spec text; add dated post-D005 revalidation notes so the gate isn't misled. |
| F5 | Recommended | Artifact defect | D002–D004 are referenced in no artifact while four spec clarifications carry no decision IDs; stamp IDs or add an ID→artifact map for D001–D008. |
| F6 | Recommended | Artifact defect (+ systemic brief-gap note) | T035's docs page is governance-origin scope beyond the brief's deliverable list with no decision record — surface it in the checkpoint packet like D007 was. |
| F7 | Nit | Artifact defect | contracts/prefect-flow.md §1's PyPI install command contradicts T034's binding repo-checkout rule; fix the contract line. |

**Blocking count**: 1 (F1). No RETHINK findings. NEEDS_INTAKE_REVISION: not required — the brief is consistent and complete; both brief defects are instance-level row repairs.
