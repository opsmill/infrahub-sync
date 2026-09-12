# Fidelity Critique — Round 2 (bounded verification round)

**Lens**: Fidelity (brief DB-001 v2 ↔ remediated spec/plan/tasks/contracts/quickstart/checklists). Sole source of brief-gap findings and planner feedback.
**Round-2 scope**: (1) closure verification of fidelity-r1 findings F4/F5/F7 and F6-via-D011; (2) fidelity check of the round-1 remediation itself (D009, D010, T033a, X5, X12, X7); (3) decision-inventory reconstructibility D001–D011.
**Reviewer inputs**: brief snapshot `.../runs/DB-001/20260730T192231Z/brief-snapshot.md`; `critiques/fidelity-r1.md`; `critiques/collation-r1.md`; current `spec.md`, `plan.md`, `tasks.md`, `quickstart.md`, `data-model.md`, `contracts/*`, `checklists/*`. No repository source code read; no planning history read. Fresh agent; all verifications performed directly against the artifacts.
**Date**: 2026-07-30

---

## 1. Closure table (round-1 findings)

| ID | R1 severity | R2 verdict | Evidence |
|---|---|---|---|
| F1 (D005 3.5.0-for-3.7.2) | Must-Address | **PENDING-GATE (correctly carried)** | Not closable by artifact work — closes only by human ratification. Artifacts remain mutually consistent on 3.5.0 and the D005 marker survives at every load-bearing point (spec §Constraints + §Assumptions, plan §Summary + decision map, contracts/prefect-flow.md §1, tasks T012, quickstart Setup). No regression. Brief-repair feedback (dependency row + install-boundary-probe evidence method) stands for the planner. |
| F2 (D006 companion pins) | Recommended | **PENDING-GATE (correctly carried)** | Pins present and probe-cited in contracts/prefect-flow.md §1, plan Technical Context, tasks T012; still folded with D005 as one packet item. No regression. |
| F3 (D007 mirror file) | Recommended | **PENDING-GATE (correctly carried)** | D007 record intact in tasks.md with the collation-noted evidence nuance handled (record now says the Setup-block half is consistency-driven, the workflow block verbatim-mandated). Brief R-1 wording repair remains planner feedback. |
| F4 (stale pre-D005 checklist attestations) | Recommended | **CLOSED** | All five items F1 named now carry dated, append-only "Post-D005 revalidation note" blocks: `checklists/requirements.md` (the line-24 "Prefect 3.7.2" dependency item), `checklists/traceability.md` (CHK021, CHK027), `checklists/interfaces.md` (CHK026, CHK036). Original attestations untouched; each note re-checks the item's substance against the D005-remediated text and records PASS-in-amended-form. Exactly the recommended shape. (See F10 for a *new* staleness instance the D005-scoped notes do not cover.) |
| F5 (D002–D004 unreconstructible; unstamped clarifications) | Recommended | **CLOSED** | Both recommended fixes were applied: the four spec clarifications are now stamped "PROVISIONAL (CHECKPOINT, D001)"…"(…, D004)" in spec §Clarifications, AND plan.md carries a full "Decision-ID map (D001–D011 → artifact locations)" table explicitly labeled as the F5 remediation; tasks.md's traceability section repeats a D→task line. Every ID D001–D011 now resolves to at least one artifact location. |
| F6 (T035 docs page without a decision record) | Recommended | **CLOSED (via D011)** | D011 is a full decision record (question/evidence/options/recommendation/origin `brief-gap`/`systemic`, PROVISIONAL CHECKPOINT) in `critiques/collation-r1.md`; tasks T035 cites it inline ("governance-origin scope per **D011** … ratify at the checkpoint gate"); plan's decision map carries the D011 row. The asymmetry F6 flagged (D007 recorded, T035 not) is resolved; the systemic planner-feedback note (brief template should declare whether AGENTS.md docs governance is in scope) is preserved inside D011's origin field. |
| F7 (contract §1 PyPI install line vs T034) | Nit | **CLOSED** | contracts/prefect-flow.md §1 now uses the checkout-based form (`pip install -e '.[prefect]'`) and names T034's binding rule as the reason. Consistent with T034 and with the flow-module docstring requirement in §2/T014. |

No round-1 fidelity finding REGRESSED.

---

## 2. Remediation fidelity assessment

The round-1 remediation (D009 failure-semantics redesign, D010 tolerant resolution, T033a, X5, X12, X7, plus the E/X sweep) was checked item-by-item against the brief's scope and out-of-scope boundaries.

### D009 — sanitize-and-wrap moved to `run_remote_request`; CLI failure behavior preserved verbatim — FAITHFUL, correctly flagged

- **DBR-009 (absolute CLI preservation)**: strengthened, not weakened. The design is identity by construction: the CLI keeps its narrow `ValueError` handlers at today's sites (wrapper factory for the prefixed abort; `_serial_load_error` seam for the unprefixed abort), all other lifecycle failures re-raise original types, and T025/T026/T027 pin exact wording, exit codes, and the unmodified DBA-009 population. This is stricter fidelity than the round-1 `__cause__` mapping it replaced. (One puncture exists — X5, see F8 — but it is not part of D009's design.)
- **DBR-015 / brief shared-contract paragraph**: D009 reads the typed `RunValidationError`/`RunExecutionError` contract as binding the remote/programmatic composition (`run_remote_request` and the flow) while the CLI seam keeps its native failure surface. This does reinterpret the brief's "used by the CLI seam and Prefect flow" sentence — but the brief's own failure paragraph is remote-framed ("Prefect records the flow as failed"), the literal both-sides reading is jointly unsatisfiable with DBR-009 (root-confirmed E1/E2 evidence in collation-r1), and the decision is PROVISIONAL (CHECKPOINT) with its record and locus stamped in contracts/execution-surface.md, plan Constitution row IV, data-model §3, and tasks. Honest, complete, ratifiable. *Systemic planner note (non-blocking)*: the brief's shared-contract paragraph should state explicitly which callers the failure classes bind, so future briefs extending the contract do not assume CLI-typed errors.
- **DBA-006 refusal semantics**: unweakened. The unconfirmed-sync refusal remains a surface-owned `RunValidationError` raised in `execute_run` step 1 **before any adapter construction** (the round-1 strengthened single-gate reading survives in spec Assumptions), with adapter-load spies (spy `potenda_factory` never called) in T011/T023 and live destination observation in T024.
- **DBA-010**: intact — validation-failure and execution-failure tests exist at unit (T010/T011) and flow (T023) level; failures still yield a failed Prefect flow, sanitized, no successful result. The E5 whole-cause-chain redaction is a strengthening of the brief's "redacting configured secret values", not a softening.
- **New private seams** (`_lock_timeout`, `_serial_load_error`): internal, never remote-settable, documented; module layout is planning-owned per the brief. No scope effect.

### D010 — tolerant per-file configuration resolution — FAITHFUL, correctly flagged

- **DBR-005 boundary**: preserved — one directory, exact-name match, requested value never used to build a path; traversal/command-shaped values fail as unknown names (SC-004 set unchanged).
- **Brief edge case** ("an unreadable or invalid configuration fails before either adapter loads … names the configuration without printing its contents"): implemented for *the requested* configuration exactly as written; the tolerant skip applies only to non-matching neighbors, which the brief's edge case does not address. The added content-leak protection (parse detail never chained verbatim) strengthens DBA-008/secret hygiene. CLI resolution (`utils.get_instance`) untouched — no DBR-009 exposure. PROVISIONAL (CHECKPOINT), record in collation-r1, stamped in contract/data-model/plan/tasks.
- One staleness side-effect in the spec and one checklist attestation was left behind — see F10.

### T033a — fixture diagnosability — MOSTLY FAITHFUL; authority miscited — see F9

The change itself is outcome-preserving (five-device result unchanged) and serves DBA-011/DBR-012 evidence. But the task grounds itself in the brief's fixture-repair allowance, whose trigger condition ("impact if wrong" on the fixture-compatibility assumption) never occurred — R-3's smoke test passed. Part (1) (WARNING on missing `db_path`) is defensible as making the brief's own qualified demonstration reliable against the confirmed X1 silent-zero-plan hazard; part (2) (example `print()` → bridged `logging`) is an enhancement, not a repair, and changes the example's user-visible CLI narration. Neither is backlog leakage; both are small; but by the run's own standard (D007, D011) discretionary scope on shipped files should be a recorded gate item.

### X5 — adapter missing-credential message — UNFLAGGED DBR-009 DEVIATION — see F8 (Must-Address)

### X12 — deployment named `run` — FAITHFUL

The brief fixes no deployment name; "a locally served Prefect deployment" is in scope. The choice, its lookup-path rationale, and the rename-is-breaking caveat are recorded in contracts/prefect-flow.md §2 and consumed consistently by quickstart, T014/T017/T018, and data-model §7. Design detail within planning's remit; no decision record needed.

### X7 — contractual fixed-format summary line — FAITHFUL with one Nit (F11)

Operationalizes DBA-003/DBA-004 remote observability using only Prefect's own log API (DBR-011 respected; result persistence explicitly excluded). Not B-001 leakage — no custom HTTP service or Sync REST resource model is introduced; the "contract" is a log-line format inside Prefect's log records. See F11 for the durability-of-commitment wording.

### Sweep of the remaining remediation items — no fidelity issues

E3/X3 (subprocess-isolated import probe — SC-006 evidence unweakened; clean-venv Scenario 0/T030 stays authoritative); E4 (log-bridge level ownership — mechanism sharpening inside D004, stamped, makes SC-002's existing denominator achievable); E5/E10 (whole-chain redaction + name-pattern env credentials — strengthens DBA-008); E8/E7 (single-source row derivation + nested-diff caveat, pinned by T029 — protects DBA-009's unmodified-population guarantee); E9 (uv.lock in T012 commit); E12 (null normalization confined to the fingerprint sort key, stamped D001); E13/E14/E15 (no-print serve, MappingProxyType, sanctioned lock seam — internal); X1/E11/X6/X11 (CWD pin, directory scoping, README ops content — documentation of real constraints, no scope change); X4 (missing-extra serve message — new behavior in new code); X8 (skip guards + marker help text — test ergonomics); X9/X13 (docs content inside D011's ratification scope); X10 (test isolation). Backlog containment intact: no task implements B-001–B-007 and T038's audit survives unchanged. R-1..R-5 handling unchanged from round 1 (T001/T002 isolated commits; R-3 ceiling fallback; R-4/R-5 inherited-and-reported).

---

## 3. New findings

### F8 — X5's adapter-message change is an unflagged DBR-009 deviation that contradicts T025's own byte-identical claim

- **Severity**: Must-Address (blocking until recorded as a checkpoint decision or relocated).
- **Brief section vs artifact location**: brief DBR-009 ("Preserve existing user-visible CLI behavior for the touched `diff` lifecycle and `sync --no-parallel` branch", DERIVED with rationale "a shared seam must not turn the feature into an unrelated CLI behavior change") vs `tasks.md` T025 (final sentence: "Additionally (X5, in-scope adapter-message repair): extend the missing-credential message in `infrahub_sync/adapters/infrahub.py` … to the ipfabric pattern"), consumed by T011's assertion that the infrahub-adapter case "names the `INFRAHUB_ADDRESS`/`INFRAHUB_API_TOKEN` environment variables".
- **Exact divergence**: the adapter's missing-credential `ValueError` message flows through the CLI's preserved prefixed abort ("Failed to initialize the Sync Instance: Both url and token must be specified!") on the very lifecycles DBR-009 protects. Changing that message changes user-visible CLI failure output — while T025 itself and contracts/execution-surface.md "Failure semantics" simultaneously bind "exit codes and output identical to the current CLI at `9edc1bc`" / "log lines, run.json contents, and exit codes byte-identical to today". The task text is internally contradictory, and the deviation carries no decision ID, no PROVISIONAL (CHECKPOINT) marker, and no row in plan.md's D001–D011 map — unlike every comparable deviation this run made (D005, D007, D011). "In-scope adapter-message repair" cites no brief authority; the brief has no adapter-repair allowance, and the remote-side need (spec edge case: the `RunExecutionError` "names the missing input") is satisfiable at the `run_remote_request` wrap point without touching the adapter.
- **Classification**: a deviation the human must ratify — **flagged incompletely** (the collation table routed X5 as "task note; adapter-message addition as in-scope task text", which asserted in-scope-ness rather than recording a decision).
- **Disposition**: **Artifact defect**. Two equally small fixes: (a) drop the adapter edit and satisfy X5's ergonomic goal in `run_remote_request`'s wrapped message (remote-only; DBR-009 untouched; T011's assertion moves to the wrapper), or (b) keep the adapter edit under a new decision record (D012-style, PROVISIONAL CHECKPOINT, origin governance/ergonomics intersecting DBR-009), add it to plan.md's decision map and the gate packet, and amend T025's and the contract's "output identical" sentences to state the one ratified exception.
- **Recommendation**: (a) is cleaner — it keeps DBR-009 absolute by construction. If (b), the gate must see it explicitly.

### F9 — T033a rests on the brief's conditional fixture-repair allowance whose condition did not trigger

- **Severity**: Recommended.
- **Brief section vs artifact location**: brief §Assumptions row 2 ("Impact if wrong: The example must be repaired within this brief without changing its five-device outcome…") vs `tasks.md` T033a ("in scope under the brief's allowance to repair the fixture 'without changing its five-device outcome'").
- **Exact divergence**: the repair allowance is the *impact-if-wrong* consequence of the fixture-compatibility assumption — and that assumption held (R-3's smoke test produced the expected five creates). T033a quotes the allowance as standing authority for unconditional changes to the shipped `examples/custom_adapter` fixture: (1) a WARNING when `db_path` is missing, and (2) replacing the example adapter's `print()` narration with bridged `logging`. Part (1) is defensible on a demonstration-reliability reading (the confirmed X1 hazard means the fixture, when served, can silently produce a wrong answer for the brief's own qualified demonstration). Part (2) is an enhancement — it changes the example's user-visible CLI narration mechanism and is motivated by demo quality (X14), not repair. Neither has a decision record or packet entry, though the run recorded comparable-or-smaller discretionary scope as D007 and D011.
- **Disposition**: **Artifact defect** (missing decision/packet entry; miscited authority). Keep the task's content; re-ground it (DBA-011 diagnosability + DBR-012 observability, origin inherent/ergonomics) and surface it as a one-line gate-packet item so the human ratifies the example-behavior change; or fold it into an existing decision record.

### F10 — Spec and one checklist attestation are stale against D010's tolerant walk

- **Severity**: Recommended.
- **Brief section vs artifact location**: n/a (artifact-internal, same category as F4) — `spec.md` §Key Entities "Sync configuration" ("Resolution matches `sync_name` … **(the same lookup the CLI `--name`/`--directory` path performs today)**") and `checklists/interfaces.md` CHK004 (attests resolution as "the same lookup the CLI … performs today (`utils.get_instance`)").
- **Exact divergence**: after D010, remote resolution is *not* the same lookup — it shares the glob and the exact-name match rule but deliberately replaces `utils.get_all_sync`'s eager validate-everything pass with a tolerant per-file walk, and its observable behavior differs from the CLI lookup when a broken neighbor config is present (CLI lookup fails; remote resolution skips with a WARNING). The contracts, data-model, plan, and tasks all state this carefully; the spec parenthetical and CHK004's attestation predate D010 and were not revalidated (the F4 notes were correctly scoped to D005 only). A gate reviewer reading spec-only would conclude CLI and remote share the resolution implementation and its failure behavior.
- **Disposition**: **Artifact defect** (staleness). Fix: reword the spec parenthetical to "the same discovery glob and exact-name match rule as the CLI lookup (mechanism per D010's tolerant per-file walk; the CLI lookup itself is unchanged)" — or add a D010 marker — and append an F4-style dated revalidation note to interfaces CHK004.

### F11 — X7's "breaking contract change" wording makes a feature log line a permanent compatibility surface

- **Severity**: Nit.
- **Brief section vs artifact location**: brief §In scope ("Infrahub Sync lifecycle output available in the Prefect run log") and the brief-owned shared contract (request/result/errors only) vs `contracts/prefect-flow.md` §2 step 3 ("Any format change is a breaking contract change") and §5.
- **Exact divergence**: the brief's owned shared contract is the execution request, `RunResult`, and the two failure classes; the summary line is a feature observation mechanism. Declaring its format change "breaking" without a scope qualifier creates a durable commitment future briefs inherit alongside the brief-owned contract, though the surrounding text ("this feature's contract") suggests feature scope. No backlog leakage (B-001's REST resource model is not introduced).
- **Disposition**: **Artifact defect** (wording). Qualify the sentence ("a breaking change for consumers of this feature; a future API brief supersedes it via the owned contract's extend-not-fork rule") or list the line's contractual status as a gate-packet awareness item.

---

## 4. Decision-inventory reconstructibility (D001–D011)

**Confirmed reconstructible.** Verification method: located each ID's definition and stampings directly.

| ID | Definition located | Stamped at load-bearing points |
|---|---|---|
| D001 | spec §Clarifications #1 | contracts/run-result-and-errors.md §3, data-model §5, tasks T004/T009, plan map |
| D002 | spec §Clarifications #2 | contracts/prefect-flow.md §3, data-model §4, tasks T015/T016/T018, plan map |
| D003 | spec §Clarifications #3 | contracts/execution-surface.md, data-model §1, tasks T007/T008, plan map |
| D004 | spec §Clarifications #4 | contracts/prefect-flow.md §4, tasks T014/T016, plan map |
| D005 | research F1 (per plan) | spec §Constraints + §Assumptions, plan §Summary, contract §1, tasks T012, quickstart, checklist revalidation notes, plan map — BLOCKING at gate |
| D006 | research F2 (per plan) | contract §1, plan Technical Context, tasks T012, plan map |
| D007 | tasks.md decision record | tasks T001, plan map |
| D008 | plan "Deviation note — logging" | tasks header logging convention, plan map |
| D009 | collation-r1 record | contracts/execution-surface.md "Failure semantics", plan Constitution row IV, data-model §3, tasks T007/T008/T025/T026/T027, plan map |
| D010 | collation-r1 record | contracts/execution-surface.md `resolve_sync_instance`, data-model §1 step 4, tasks T006/T011, plan map |
| D011 | collation-r1 record | tasks T035, plan map |

Every decision touching brief text is flagged PROVISIONAL/CHECKPOINT for the gate (D005 additionally BLOCKING) — **with one exception**: the X5 adapter-message change touches DBR-009 and carries no decision ID (F8). T033a's example change is authority-miscited but at least trace-visible (F9).

---

## 5. Summary table

| ID | Severity | Disposition | One-line |
|---|---|---|---|
| F4 | — | CLOSED | Dated append-only post-D005 revalidation notes present on all five stale items across the three named checklists. |
| F5 | — | CLOSED | Clarifications stamped D001–D004; plan.md carries the full D001–D011 → artifact map; inventory reconstructible ID-by-ID. |
| F6 | — | CLOSED (via D011) | Full D011 record exists; T035 cites it for gate ratification; plan map row present. |
| F7 | — | CLOSED | Contract §1 now uses the repository-checkout install required by the README. |
| F1/F2/F3 | Must-Address / Rec / Rec | PENDING-GATE | Correctly carried; close by ratification + planner brief-repair feedback, not artifact work. No regression. |
| F8 | Must-Address | Artifact defect | X5's adapter missing-credential message change is an unflagged DBR-009 deviation contradicting T025's/the contract's own byte-identical claim — relocate to the remote wrap or record as a checkpoint decision. |
| F9 | Recommended | Artifact defect | T033a cites the brief's conditional fixture-repair allowance whose condition never triggered; surface the example-behavior change (esp. print→logging) as a gate item like D007/D011. |
| F10 | Recommended | Artifact defect | Spec "same lookup the CLI performs today" parenthetical and interfaces CHK004's `utils.get_instance` attestation are stale vs D010; reword + F4-style revalidation note. |
| F11 | Nit | Artifact defect | Scope-qualify X7's "breaking contract change" sentence so a feature log line does not read as a permanent compatibility surface. |

**Blocking count remaining**: 2 — F1 (pending human ratification at the gate; artifacts complete and consistent; no further artifact work) and F8 (new; small artifact fix or decision record required before the gate packet is complete).

**NEEDS_INTAKE_REVISION: NO.** The brief remains internally consistent and complete; all round-2 findings are artifact defects. Planner feedback unchanged from round 1 (F1/F3 instance-level brief-row repairs; F1's systemic install-boundary-probe evidence note; D011's systemic docs-governance template note) plus one new systemic note: the brief's shared-contract paragraph should state which callers the failure classes bind (remote composition vs CLI seam), per D009.
