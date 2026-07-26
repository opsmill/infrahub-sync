# Review report: `review-ux.md` (plan review experience requirements)

**Checklist**: `dev/specs/001-plan-artifact-saved-apply/checklists/review-ux.md` (CHK001–CHK042)
**Spec under evaluation**: `dev/specs/001-plan-artifact-saved-apply/spec.md` (567 lines)
**Brief**: `/Users/blake/repos/opsmill/infrahub-sync-lab/.planning/delivery-briefs/batch-v3/briefs/db-001-plan-artifact-saved-apply.md` (brief_version 5)
**Reviewer**: clean-context requirements evaluator, read-only
**Date**: 2026-07-26

## Before-state facts verified in the tree

All claims below about current behavior were read out of the working tree, not assumed.

| Fact | Evidence |
|---|---|
| The CLI is a single flat Typer app; there are **no command groups at all** today (no `add_typer` anywhere in `infrahub_sync/`) | `infrahub_sync/cli.py:31` (`app = typer.Typer()`); grep for `add_typer` returns nothing |
| Top-level commands: `list`, `diff`, `sync`, `apply`, `generate` | `infrahub_sync/cli.py:77`, `:86`, `:166`, `:295`, `:355`; rendered list at `docs/docs/reference/cli.mdx:22-26` |
| `diff` already owns a `--run-id` option meaning *re-use a specific cache run id* for a live diff | `infrahub_sync/cli.py:98`; documented at `docs/docs/reference/cli.mdx:61` |
| `diff`'s current option set | `--name`, `--config-file`, `--directory`, `--branch`, `--show-progress/--no-show-progress`, `--adapter-path`, `--run-id`, `--concurrent-load/--no-concurrent-load`, `--full-extract/--no-full-extract` — `infrahub_sync/cli.py:89-110`; `docs/docs/reference/cli.mdx:55-64` |
| `diff` requires exactly one of `--name` / `--config-file` and takes an exclusive per-pipeline file lock before doing anything | `infrahub_sync/cli.py:113-114`, `:129`; `infrahub_sync/cache/locks.py:20-33` (60 s timeout) |
| Plan output today is emitted through the logger, i.e. the log stream (stdlib `logging.StreamHandler()` → stderr), not stdout | `infrahub_sync/cli.py:153` (`logger.info("\n%s", mydiff.str())`), handler at `infrahub_sync/cli.py:46-48` |
| Both adapters are imported **and instantiated** before a run directory is available; the Infrahub destination's live schema is already populated at `__init__` | `infrahub_sync/utils.py:183-184`, `:217`, `:232`, `:260` |
| A run directory is located only as `cache_root_for(sync_name)/<run_id>`, and `get_potenda_from_instance` **creates it if absent** | `infrahub_sync/cache/paths.py:26-59`; `infrahub_sync/utils.py:244-246` (`rdir.mkdir(parents=True, exist_ok=True)`) |
| A stored run is read today via `plan.parquet` (`read_plan`) and JSON sidecars (`run.json`, `schema-sub-hash.txt`) | `infrahub_sync/cache/parquet_io.py:81-89`; `infrahub_sync/cache/sidecars.py:68-105` |
| Today's plan rows are lossy exactly as the brief states (`dest_id` and `attribute` written as `""`) | `infrahub_sync/potenda/__init__.py:317-330` |
| `apply_plan` guards a missing planned-write surface with `NotImplementedError` naming the adapter class | `infrahub_sync/potenda/__init__.py:354-360` |
| No cache retention / pruning / GC exists — run directories accumulate indefinitely | grep for `retention|prune|cleanup|max_runs` in `infrahub_sync/` returns only a tmp-file comment (`infrahub_sync/cache/sidecars.py:22`) |
| There is no named public in-process API: `infrahub_sync/__init__.py` declares no `__all__` and exports config models only | `infrahub_sync/__init__.py` (read; no `__all__`) |
| `structlog` is a declared dependency but **unused** in the package; the package uses stdlib `logging`, and an in-tree test bans bare `print()` in `infrahub_sync/` (only `_print_callback` exempt) | `pyproject.toml:19`; `tests/test_logging.py:56-79` |
| CLI-help snapshot testing precedent exists (Typer `CliRunner` + ANSI stripping) | `tests/test_logging.py:114-139` |

## Verdict table

| Item | Verdict | Severity | Reason |
|---|---|---|---|
| CHK001 | DEFECT | BLOCKING | FR-006 names no per-object fields; nothing requires the operation identifier in review output, which SC-005/DBA-005 presupposes. |
| CHK002 | DEFECT | RECOMMENDED | "counts by action and by kind" never disambiguated between two breakdowns and the action×kind cross product. |
| CHK003 | DEFECT | RECOMMENDED | `--kind` narrowing lives only in AD005; no FR or SC carries it, yet US1 scenario 1 depends on it. |
| CHK004 | DEFECT | RECOMMENDED | No requirement on review-output shape or stability, although SC-005 and SC-010 both test against it. |
| CHK005 | DEFECT | RECOMMENDED | The in-process reader's inputs and outputs are unstated; no surface is named. |
| CHK006 | DEFECT | RECOMMENDED | Whether a torn or checksum-mismatched plan may be reviewed is unspecified; FR-009/FR-010 are apply-scoped. |
| CHK007 | DEFECT | BLOCKING | FR-018 states an outcome with no rule: no definition of a secret value and no mechanism, while FR-002 mandates full payloads. PRODUCT-AMBIGUITY on the mechanism. |
| CHK008 | DEFECT | RECOMMENDED | No requirement covers review-path failure messages; FR-023's actionability bar is apply-only. |
| CHK009 | DEFECT | RECOMMENDED | No requirement obliges documenting the new review flags, which the constitution requires in the same change. |
| CHK010 | DEFECT | RECOMMENDED | "reachable in-process" is undefined — supported entry point vs any importable function. |
| CHK011 | DEFECT | RECOMMENDED | Scope of the stdout requirement (read-from-artifact mode only vs the live command too) is ambiguous. PRODUCT-AMBIGUITY. |
| CHK012 | SATISFIED | — | FR-008 + SC-012 + Out of Scope + Assumptions together fix the bar at top-level groups and forbid new commands, leaving flags permitted. |
| CHK013 | DEFECT | RECOMMENDED | FR-008's "MUST NOT construct an adapter or extract either side" has no stated external observation or criterion. |
| CHK014 | SATISFIED | — | Both requirements that assert actionability state required content (name the adapter; direct to re-plan). |
| CHK015 | DEFECT | RECOMMENDED | FR-018 says "any review output"; SC-010 enumerates only two outputs — the FR is broader than its criterion. |
| CHK016 | DEFECT | NIT | "at any time after the run" is bounded only by the out-of-scope exclusion of plan expiration; retention is unstated. |
| CHK017 | DEFECT | RECOMMENDED | FR-008 forbids a new *command*; SC-012, Out of Scope, the brief's DBR-020/DBA-012 and D002/D026 forbid only a new *group*. Different bars. |
| CHK018 | DEFECT | RECOMMENDED | The constitution's structlog/no-print logging rule versus FR-008's stdout requirement is never reconciled in the spec. |
| CHK019 | SATISFIED | — | The output change from recorded deletes is stated in the edge case and carried by FR-015, which requires docs and fixtures updated in the same change. |
| CHK020 | SATISFIED | — | FR-007 and SC-009 state the same obligation; SC-009 adds only an evidence method (new process). |
| CHK021 | SATISFIED | — | AD005 states the command is a thin renderer over the single reader "so both paths in SC-009 exercise the same code". |
| CHK022 | SATISFIED | — | "summary" and "per-object detail" are used consistently; no third depth vocabulary appears. |
| CHK023 | DEFECT | RECOMMENDED | SC-009 lists four cases but states no per-case pass condition on output content. |
| CHK024 | DEFECT | RECOMMENDED | SC-010's canary scan names no credential values, no injection point, and no capture method. |
| CHK025 | DEFECT | NIT | SC-012 names "the top-level command list" but not how it is captured or stored as a reproducible artifact. |
| CHK026 | DEFECT | RECOMMENDED | No success criterion measures review *content*; DBR-002 is verified only for reachability and secret absence. |
| CHK027 | DEFECT | NIT | The no-latency/no-volume-target decision lives only in Open Design Decisions, not in Requirements or Out of Scope. |
| CHK028 | DEFECT | RECOMMENDED | FR-018's negative case is unverifiable beyond SC-010's unspecified canaries. |
| CHK029 | DEFECT | NIT | FR-022 covers an empty plan for apply; nothing says what the summary presents at zero operations. |
| CHK030 | DEFECT | RECOMMENDED | No behavior specified for a kind filter matching nothing or naming an unconfigured kind. |
| CHK031 | DEFECT | RECOMMENDED | FR-019's "The reader MUST NOT accept v1 rows" may or may not bind the review reader; "the reader" is undefined across FR-008/FR-019. |
| CHK032 | DEFECT | RECOMMENDED | Run directory present but no manifest and no v1 plan file is unclassified by FR-010/FR-019/AD001. |
| CHK033 | DEFECT | RECOMMENDED | Review with a missing or unknown run id is unspecified, and today's `--run-id` silently creates the directory. |
| CHK034 | DEFECT | NIT | Large-plan detail rendering is deferred in Open Design Decisions rather than requirement-excluded. |
| CHK035 | DEFECT | NIT | Partial-unreadability of the run directory is uncovered; folds into CHK008's fix. |
| CHK036 | DEFECT | RECOMMENDED | The existing `diff` contract is not recorded: an existing `--run-id` with different semantics, output on the log stream, mandatory `--name`/`--config-file`, and the exclusive pipeline lock. |
| CHK037 | DEFECT | BLOCKING | Same root cause as CHK001 — SC-005's dependency on identifiers in review output is stated nowhere in FR-006/FR-008. |
| CHK038 | DEFECT | NIT | The "foldable into the later `plan` group without behavior change" statement is prose in Open Design Decisions, not a requirement. |
| CHK039 | SATISFIED | — | The assumption and its escalation path ("a scope change requiring a new decision, not an implementer's call") are both recorded. |
| CHK040 | DEFECT | RECOMMENDED | AD005's substantive clauses are promoted into FR-008 as unconditional MUSTs, so the provisional marker no longer bounds them and no revisit list exists. |
| CHK041 | DEFECT | RECOMMENDED | No assumption recorded that review output is capturable in the in-process case; the in-process reader may return data rather than write stdout. |
| CHK042 | DEFECT | RECOMMENDED | No assumption recorded about whether secrets can arrive from source *data* as opposed to sync-configuration credentials. |

**Counts**: SATISFIED 7 · DEFECT(BLOCKING) 3 · DEFECT(RECOMMENDED) 25 · DEFECT(NIT) 7 · NOT-APPLICABLE 0 · PRODUCT-AMBIGUITY 2 (CHK007, CHK011).

---

## Detailed defect blocks

### CHK001 — BLOCKING — review output is not required to carry the operation identifier

**Anchor**: `spec.md:288-289` (FR-006), `spec.md:408-410` (SC-005), `spec.md:111` (US1 scenario 1).

**Evidence**. FR-006 in full: *"A saved plan MUST be reviewable at two depths: a summary giving counts by action and by kind, and per-object detail for the operations it contains."* (`spec.md:288-289`). It names no field of a per-object record. FR-008 (`spec.md:292-297`) constrains reachability, output channel and implementation shape, never content. The only field list in the spec is on the *artifact* record, not on review output: FR-002 (`spec.md:267-272`) and Key Entities "Planned operation" (`spec.md:371-375`).

SC-005 then asserts a comparison against something the requirements never oblige: *"The operation identifiers shown at review are the identifiers reported against each operation in the apply result — evidenced by a review-then-apply trace comparing both identifier sets."* (`spec.md:408-410`). An implementation that renders per-object detail as kind + identity + action + payload satisfies FR-006 and FR-008 in full and makes SC-005/DBA-005 unevidenceable, because there is no identifier set on the review side of the trace.

**Brief position**. The brief has the same gap: DBR-002 is *"Provide summary and per-object views of a saved plan."* (brief line 158) with no field list, while DBA-005 (brief line 187) asserts *"The operation identifiers shown at review …"*. This is a **brief-gap**: the brief's Requirements table (DBR-002 row, or a DERIVED companion alongside DBR-005) should have said that per-object review output presents each operation's identifier.

**Minimum fix**. Append one clause to FR-006: *"Per-object detail MUST present, per operation, at least its operation identifier, action, destination kind, and destination identity."* Nothing else in the spec needs to change; SC-005 then has a stated review-side source.

### CHK037 — BLOCKING — the SC-005 → FR dependency is unstated (same root cause as CHK001)

**Anchor**: `spec.md:408-410`, `spec.md:288-297`, traceability row `spec.md:512` (`DBR-005 | FR-003, FR-021; SC-005`).

**Evidence**. The traceability table routes DBR-005 to FR-003 (identifier derivation), FR-021 (uniqueness) and SC-005. Neither FR-003 nor FR-021 says anything about *display*: FR-003 is about derivation and stability (`spec.md:273-278`), FR-021 about collision (`spec.md:346-348`). So the chain DBR-005 → DBA-005 passes through no requirement that puts the identifier in front of a reviewer. The dependency exists only inside SC-005's own wording.

**Minimum fix**. The FR-006 clause in CHK001, plus adding `FR-006` to the `DBR-005` traceability row (`spec.md:512`) so the display obligation is traceable.

### CHK007 — BLOCKING (PRODUCT-AMBIGUITY) — FR-018 has no redaction rule

**Anchor**: `spec.md:337` (FR-018), `spec.md:267-272` (FR-002), `spec.md:428-430` (SC-010).

**Evidence**. FR-018 is one sentence: *"No secret value MUST appear in the plan artifact or in any review output."* Nothing in the spec defines what makes a value secret, and nothing states the mechanism — omit the field, mask the value, or refuse to plan. FR-002 pulls the other way: it requires *"the required source values as a full payload"* (`spec.md:269`), and the Key Entities "Planned operation" repeats it (`spec.md:373`). If any mapped source field holds a secret, FR-002 and FR-018 cannot both hold, and the spec offers no rule for choosing. On the qualified path credentials come from the environment, not the configuration (`examples/netbox_to_infrahub/config.yml:32-36`), so the only route a secret can take into a payload is source *data* — precisely the case nothing addresses (see CHK042).

**Brief position**. Also a **brief-gap**. DBR-017 (brief line 173) states only the outcome, deriving it from INFP-651's batch-wide rule; the brief's "Edge cases and failure behavior" section (brief lines 196-214) never treats a secret-bearing source field. The brief should have carried either a definition (which fields count) or the mechanism.

**PRODUCT-AMBIGUITY**: which of omit / mask / refuse-to-plan is correct is a product decision with different operator-visible consequences (a masked field makes the plan reviewable but no longer a faithful payload; omission breaks the "full payload" guarantee silently; refusal blocks the run). This report does not pick a reading.

**Minimum fix**. Record a decision as a new clarification (`AD006`) and cite it from FR-018 and FR-002: state what marks a value as secret (e.g. a declared field list, a name-pattern rule, or "nothing, because credentials never enter payloads") and state the mechanism. If the answer is "no secret can reach a payload by construction", say that explicitly in FR-018 and in Assumptions, which also makes SC-010's scan honest about being a regression guard rather than a redaction test.

### CHK002 — RECOMMENDED — summary breakdown shape not pinned

**Anchor**: `spec.md:288-289`, `spec.md:423-424`, `spec.md:93`.

**Evidence**. FR-006 says *"a summary giving counts by action and by kind"*; SC-009 says *"summarized by action and kind"*; US1 says *"(counts by action and kind)"*. Two independent breakdowns and the action×kind cross product both read as compliant, and they produce different output. SC-009 states no pass condition (CHK023), so nothing else resolves it.

**Minimum fix**. In FR-006, replace "counts by action and by kind" with either "a count per action and a count per kind" or "a count per (action, kind) pair", and mirror the wording in SC-009.

### CHK003 — RECOMMENDED — kind narrowing exists only in the clarification

**Anchor**: `spec.md:78-79` (AD005 `--kind <kind>`), `spec.md:288-289`, `spec.md:292-297`, `spec.md:107-111` (US1 scenario 1).

**Evidence**. AD005 introduces `--kind <kind>` as a user-visible flag. FR-006 and FR-008 never mention narrowing, and no SC exercises it. Yet US1 scenario 1 requires the operator to inspect *"its per-object detail for one kind"* (`spec.md:108-109`), and US1's narrative repeats it (`spec.md:93-94`) — a scenario step with no backing requirement.

**Minimum fix**. Add to FR-006: *"Per-object detail MUST be narrowable to a single destination kind."* AD005 then supplies only the spelling, as its parenthetical in FR-008 already claims.

### CHK004 — RECOMMENDED — no statement on review-output shape or stability

**Anchor**: `spec.md:292-297`, `spec.md:494-500` (Dependencies), `spec.md:408-410`, `spec.md:428-430`.

**Evidence**. The Dependencies section scopes the owned shared contract to the artifact — *"the manifest fields, the per-operation record, the deterministic serialization, and the checksum rule"* (`spec.md:495-497`) — and lists *"plan summaries in the UI"* among nine consumers. Nothing states whether the *rendered* review output is human-oriented only or itself a contract, even though two criteria assert properties of it (SC-005 compares identifier sets scraped from it; SC-010 scans it).

**Minimum fix**. One sentence in FR-008: *"Review output is human-oriented; its exact layout is not a compatibility contract, and machine consumption is out of scope for this outcome."* (Or the converse, if a stable form is intended.)

### CHK005 — RECOMMENDED — in-process reader contract is only "the single implementation"

**Anchor**: `spec.md:295-296`, `spec.md:83-84` (AD005), brief DBR-020 (brief line 176).

**Evidence**. FR-008: *"The in-process reader MUST be the single implementation, with the command a thin renderer over it."* No inputs, no return shape, no module or callable named. The tree offers no obvious answer: locating a stored run needs `(sync_name, run_id)` (`infrahub_sync/cache/paths.py:56-59`), which is why the CLI requires `--name`/`--config-file` (`infrahub_sync/cli.py:113-114`) — but a path-based reader is equally plausible, and `infrahub_sync/__init__.py` declares no public surface (`__all__` absent). The brief names *"the in-process API"* (DBR-020) as a delivery surface without naming one either.

**Minimum fix**. Add to FR-008: *"The in-process reader MUST accept a stored run's location (its run identifier within a named sync, or a run-directory path) and MUST return the summary and per-object records as data, independent of rendering."*

### CHK006 — RECOMMENDED — reviewability of an unverifiable plan is unspecified

**Anchor**: `spec.md:290-291` (FR-007), `spec.md:298-306` (FR-009, FR-010).

**Evidence**. FR-009 and FR-010 are both apply-scoped by their own wording (*"Before any destination write, an apply MUST verify…"*, *"MUST be refused on the same path as a mismatch"*). FR-007 requires the plan be readable *"at any time after the run"* with no verification precondition. Whether review verifies the checksum, warns, or reads regardless is undecided — and it matters, because the operator's whole purpose is to review before applying.

**Minimum fix**. One clause in FR-007 or FR-008: state whether review verifies the plan checksum (and what it does on mismatch — refuse, or render with a warning naming the failed check).

### CHK008 — RECOMMENDED — review-path failure messages have no stated bar

**Anchor**: `spec.md:352-353` (FR-023), `spec.md:298-300` (FR-009), `spec.md:338-343` (FR-019), `spec.md:292-297` (FR-008).

**Evidence**. The three places the spec fixes message content are all on the apply path: FR-023 *"MUST fail with a clear, actionable error naming the adapter"*; FR-009 *"naming the failed check"*; FR-019 *"a message directing the operator to re-plan"*. FR-008 sets no failure behavior at all, so unknown run, absent artifact, and unreadable artifact have no message requirement on review. The engine's existing precedent is a named-adapter message (`infrahub_sync/potenda/__init__.py:355-360`), and the constitution requires errors never *"crash opaquely"* (`dev/constitution.md:49-50`).

**Minimum fix**. Add FR-027: *"A review request that cannot be served — unknown run identifier, no plan artifact in the run directory, unreadable artifact, or a v1 plan — MUST fail with a clear, actionable message naming the run identifier and the reason, and MUST NOT present an empty plan instead."*

### CHK009 — RECOMMENDED — no documentation requirement for the new review flags

**Anchor**: `spec.md:292-297`, `spec.md:329-330` (FR-015), `dev/constitution.md:179-184`.

**Evidence**. The only documentation obligation in the spec is FR-015's, scoped to the delete-visibility change: *"Test fixtures and documentation affected by the change in plan content MUST be updated in the same change."* AD005 adds three user-visible flags to `diff`. The constitution: *"User-visible changes (CLI flags, config keys, adapters) MUST update `docs/` in the same change … 'Update later' is not acceptable."* The affected surface is a generated reference page listing `diff`'s options (`docs/docs/reference/cli.mdx:43-64`).

**Minimum fix**. Extend FR-008 with: *"The CLI reference documentation MUST be regenerated in the same change so the new review flags are documented."*

### CHK010 — RECOMMENDED — "reachable in-process" is undefined

**Anchor**: `spec.md:292-293`, `spec.md:423-427`.

**Evidence**. FR-008 opens *"Both review depths MUST be reachable in-process and by extending CLI commands that already exist"*; SC-009 evidences *"summary and per-object detail, each produced in-process and from the CLI"*. Neither says whether "in-process" means a supported, documented entry point or merely something importable. AD005 pins the CLI spelling exactly (`spec.md:76-84`) and pins nothing for the in-process side, so the two surfaces are specified at very different precision. Confirms the flagged concern: **only a CLI contract is named**.

**Minimum fix**. Same as CHK005 — name the in-process surface (module + callable, or "a supported public entry point exported from `infrahub_sync`").

### CHK011 — RECOMMENDED (PRODUCT-AMBIGUITY) — scope of the stdout requirement

**Anchor**: `spec.md:294-295` (FR-008), `spec.md:81-83` (AD005), `infrahub_sync/cli.py:146-163`.

**Evidence**. FR-008: *"Review MUST be carried by the existing non-mutating command, MUST NOT construct an adapter or extract either side, and MUST write its output to standard output so it can be captured and scanned."* AD005: *"Review output is written to standard output rather than the log stream."* Today the live `diff` plan is emitted at `infrahub_sync/cli.py:153` through the logger, whose handler is a plain `logging.StreamHandler()` (`infrahub_sync/cli.py:46`) — i.e. the log stream, not stdout. Two readings survive: (a) only the `--from-plan` mode writes stdout and the live path keeps the log stream, so one command emits its product on two different channels depending on mode; (b) the live path also moves to stdout, which is a second user-visible change to an existing command with pipeline consumers. FR-015's *"in the plan and in anything that renders it"* (`spec.md:326-327`) hints the live renderer is in view but decides nothing.

**PRODUCT-AMBIGUITY**: which channel the live `diff` output uses after this change is an operator-visible decision; not resolved here.

**Minimum fix**. In FR-008, scope the sentence explicitly — e.g. *"…MUST write its output to standard output in the read-from-artifact mode; the live comparison path's existing output channel is unchanged"* — or record the opposite as an additional user-visible change with the docs obligation of CHK009.

### CHK013 — RECOMMENDED — the no-adapter clause has no stated observation

**Anchor**: `spec.md:294-295`, `spec.md:423-427` (SC-009), `spec.md:390-392` (SC-001 for contrast).

**Evidence**. SC-001 gives its negative clause a method — *"a trace or inspection showing no comparison-engine diff/sync call on the apply path"* (`spec.md:391-392`). FR-008's equally strong negative (*"MUST NOT construct an adapter or extract either side"*) has none, and SC-009 tests only reachability. The tree makes an external observation cheap and obvious: today's path instantiates both adapters before any run directory exists (`infrahub_sync/utils.py:183-184`, `:217`, `:232`) and the Infrahub destination's schema is populated at `__init__` (`infrahub_sync/utils.py:260`), so review that succeeds with no source or destination reachable is direct evidence no adapter was constructed.

**Minimum fix**. Add to SC-009 (or as a fifth case): *"…and each case is produced with neither source nor destination reachable, evidencing that no adapter is constructed."*

### CHK015 — RECOMMENDED — "any review output" versus SC-010's two outputs

**Anchor**: `spec.md:337` (FR-018), `spec.md:428-430` (SC-010), `spec.md:354-355` (FR-024).

**Evidence**. FR-018 binds *"the plan artifact or … any review output"*. SC-010 evidences *"the artifact and both review outputs"* and enumerates *"in summary output, or in per-object output"*. Warnings (FR-024 names a kind and an identifier attribute), refusal messages, and log lines are inside FR-018's "any" and outside SC-010's enumeration.

**Minimum fix**. Either enumerate in FR-018 (*"summary output, per-object detail output, warnings, and refusal messages"*) or widen SC-010's evidence to the same list.

### CHK016 — NIT — availability is unbounded and retention unstated

**Anchor**: `spec.md:290-291` (FR-007), `spec.md:466` (Out of Scope).

**Evidence**. Out of Scope excludes *"Destination freshness checks, plan expiration, and conflict policies"*, which covers expiry policy but not retention: FR-007's *"at any time after the run"* is unqualified, and no pruning or GC exists in the tree (grep over `infrahub_sync/` finds none), so availability is in practice "as long as the operator keeps the run directory".

**Minimum fix**. Add to FR-007: *"…for as long as the run's directory is retained; retention and pruning are not managed by this outcome."*

### CHK017 — RECOMMENDED — FR-008 and SC-012 state different bars (flagged item CONFIRMED)

**Anchor**: `spec.md:293-294` (FR-008), `spec.md:434-437` (SC-012), `spec.md:464-465` (Out of Scope), `spec.md:487-489` (Assumptions), brief lines 176, 194, 111, 262, 264.

**Evidence**. FR-008: *"No new CLI command is added and no new CLI command group is introduced."* SC-012: *"The CLI command set gains no new command group, and review is reachable through commands that already exist."* Out of Scope: *"**Any new CLI command group.**"* Assumptions: *"no new top-level command group"*. The brief forbids only a group: DBR-020 *"no new CLI command group is introduced"* (brief line 176); DBA-012 *"gains no new command group"* (brief line 194); Out of scope *"Any new CLI command group … introducing `plan`, `runs`, or `configs` command groups belongs to DB-004"* (brief lines 111-113); D026 *"Scoped by D002: no new command groups"* (brief line 262); D002 *"DB-004 owns introducing new command groups"* (brief line 264). The brief's Constraints go further and make the choice explicitly free: *"**Which existing commands carry review, and their exact flag spelling, is an implementation choice** within one fixed constraint: no new top-level command group."* (brief lines 231-233).

So FR-008 alone forbids a new command (e.g. a sibling `plan-show` command inside the existing flat app), which the brief permits, and SC-012 does not test that stricter half. **Consequence for AD005**: extending `diff` is *required* only under FR-008's stricter reading; under the brief's actual bar it is one option among several. Note also that the current CLI has no groups at all (`infrahub_sync/cli.py:31`, no `add_typer`), so "no new group" is satisfied by any flat command addition.

**Minimum fix**. Pick one bar and use it in both places. Either relax FR-008 to *"No new CLI command group is introduced"* (matching DBR-020) and record separately that AD005 chooses to extend `diff` rather than add a command, or keep the stricter FR-008 and extend SC-012's evidence to *"…and no new top-level command is added"* — flagging that the stricter reading is a spec-side tightening of DBR-020 that a checkpoint decision should ratify.

### CHK018 — RECOMMENDED — logging standard versus stdout requirement not reconciled

**Anchor**: `spec.md:294-295`, `dev/constitution.md:174-177`, `tests/test_logging.py:56-79`.

**Evidence**. The constitution: *"Use `structlog` for structured logging — never `print`."* (`dev/constitution.md:176`). FR-008 requires review output on stdout. In the tree the package uses stdlib `logging`, not structlog (`structlog` is declared at `pyproject.toml:19` but unused in `infrahub_sync/`), and an in-tree test fails the build on any bare `print()` in the package except `_print_callback` (`tests/test_logging.py:56-79`). A mechanism that satisfies both exists (`typer.echo`, already used at `infrahub_sync/cli.py:69`, or an explicit `sys.stdout` write), but the spec records no reconciliation, so an implementer meets FR-008 with `print()` and trips the guard.

**Minimum fix**. One sentence in FR-008 or a clarification: *"Review output is the command's product, not a log record; it is written to standard output through the CLI's echo mechanism, which does not conflict with the logging standard."*

### CHK023 — RECOMMENDED — SC-009 has no per-case pass condition

**Anchor**: `spec.md:423-427`.

**Evidence**. SC-009 states the four cases and the read-in-a-new-process condition, but not what must appear in each output for the case to pass — the summary case inherits "by action and kind" (itself ambiguous, CHK002) and the detail case inherits nothing (CHK001).

**Minimum fix**. Extend SC-009's evidence clause: *"…each case passing when the output presents, for the summary, a count per action and per kind, and for the detail, one record per operation carrying its operation identifier, action, kind, and identity."*

### CHK024 — RECOMMENDED — SC-010's canary scan is unspecified

**Anchor**: `spec.md:428-430`, `spec.md:337`, `examples/netbox_to_infrahub/config.yml:32-36`.

**Evidence**. SC-010's method is *"a canary-credential scan over the artifact and both review outputs"* — which credential values, injected where, and how the outputs are captured are all unstated. On the qualified path credentials are environment-only (`examples/netbox_to_infrahub/config.yml:32-36`), so a canary must be injected either into the environment (in which case the scan proves the adapter settings never enter a payload) or into source *record data* (in which case it tests a redaction rule that CHK007 shows does not exist). Both review outputs *are* capturable once FR-008's stdout clause holds, so the flagged achievability question resolves as: **achievable, but vacuous without a redaction rule and a named injection point**.

**Brief position**. Also a **brief-gap** — DBA-010's "Verification evidence expected" column (brief line 192) says only *"A canary-credential scan over the artifact and both review outputs"* and should have named the injection point.

**Minimum fix**. State in SC-010 where the canary is injected (environment credential, configuration setting, or a source field value) and how each output is captured (stdout of each review invocation, and the artifact files).

### CHK025 — NIT — the before/after command list is not specified as an artifact

**Anchor**: `spec.md:434-437`.

**Evidence**. SC-012 says *"the top-level command list compared before and after showing no group added"* without saying that the list is the `--help` command listing, how it is captured, or where the before-state is recorded. Precedent for the mechanism exists in-tree (`tests/test_logging.py:114-127` invokes `--help` through Typer's `CliRunner` and strips ANSI).

**Minimum fix**. Name the artifact: *"…the `infrahub-sync --help` command listing, captured before and after and compared as text."*

### CHK026 — RECOMMENDED — no criterion measures review content

**Anchor**: `spec.md:423-437`, `spec.md:519` (traceability `DBR-002 | FR-006; User Story 1 scenario 2`).

**Evidence**. DBR-002 is carried by FR-006 and US1 scenario 2. The criteria that touch review are SC-009 (reachability), SC-010 (absence of secrets) and SC-012 (command set). None asserts that the counts are correct or that the per-object records contain anything in particular. Same root as CHK001 and CHK023.

**Minimum fix**. The SC-009 extension in CHK023 is sufficient; alternatively add a dedicated criterion tying summary counts to the artifact's `operations_count` and per-action totals.

### CHK027 — NIT — the performance non-target lives outside the requirements

**Anchor**: `spec.md:561-564`.

**Evidence**. The exclusion is explicit — *"The brief sets no volume or latency target, so none is invented here."* — but it sits in Open Design Decisions, not in Requirements or Out of Scope, so a reader working from the requirements alone sees no statement either way.

**Minimum fix**. Add a bullet to Out of Scope: *"Plan-size and review-latency targets. None is set by the brief and none is asserted here."*

### CHK028 — RECOMMENDED — FR-018's negative case is not verifiable in general

**Anchor**: `spec.md:337`, `spec.md:428-430`.

**Evidence**. "No secret value appears" is a universal claim; the only evidence method is SC-010's canary scan, which tests the specific values injected. With no definition of a secret (CHK007) the universal claim has no decision procedure at all.

**Minimum fix**. Once CHK007's rule exists, restate FR-018's verification as a bounded claim — e.g. *"no value drawn from the sync configuration's credential settings, and no field the redaction rule marks secret, appears in the artifact or in review output"* — which is scannable.

### CHK029 — NIT — empty-plan review output unspecified

**Anchor**: `spec.md:349-351` (FR-022), `spec.md:288-289`, `spec.md:238-240` (edge case).

**Evidence**. FR-022 and the edge case fix the artifact representation and the apply behavior for zero operations; the summary's presentation at zero (explicit "0 operations" versus empty output) is unstated, which matters because it is the same visual as an unknown run id today (CHK033).

**Minimum fix**. One clause in FR-006 or FR-022: *"A summary of a plan with zero operations MUST state that the plan contains no operations rather than producing empty output."*

### CHK030 — RECOMMENDED — kind filter matching nothing

**Anchor**: `spec.md:78-79` (AD005), `spec.md:288-289`.

**Evidence**. AD005 introduces `--kind <kind>` and says nothing about a kind with no operations or a kind absent from the configuration. The failure mode is silent: a mistyped kind renders empty detail, indistinguishable from "this kind has no changes" — a misread with review-before-write consequences.

**Minimum fix**. With the FR-006 clause from CHK003, add: *"A kind filter naming a kind that is not in the configuration MUST fail with a message naming the kind; a configured kind with no operations MUST report zero operations explicitly."*

### CHK031 — RECOMMENDED — v1 on the review path is ambiguous, not absent (checklist note partially refuted)

**Anchor**: `spec.md:338-343` (FR-019), `spec.md:295-296` (FR-008), `spec.md:431-433` (SC-011).

**Evidence**. FR-019 contains *"The reader MUST NOT accept v1 rows"* — and FR-008 introduces "the in-process reader" as the single review implementation, so on one reading FR-019 already binds review. But FR-019's first sentence and its criterion are apply-shaped (SC-011: *"evidenced by an apply attempted against a v1 fixture plan"*; US2 scenario 4 likewise, `spec.md:151-153`), and the term "the reader" is never defined. So the checklist's note ("v1 plan … specified for apply but not for review") is **partially refuted**: the obligation may exist, but which reader is bound is undecided.

**Minimum fix**. Define the term once — in FR-008, name the in-process reader as "the plan reader", and in FR-019 say *"the plan reader (review and apply alike) MUST NOT accept v1 rows"*.

### CHK032 — RECOMMENDED — run directory with neither manifest nor v1 plan is unclassified

**Anchor**: `spec.md:302-306` (FR-010), `spec.md:338-343` (FR-019), `spec.md:46-48` (AD001).

**Evidence**. AD001 classifies *"a run directory with `plan.parquet` but no `plan/manifest.json`"* as v1; FR-010 classifies "manifest present, operations or snapshot absent or truncated" as torn. A directory with neither — which the tree produces routinely, since `get_potenda_from_instance` creates the run directory up front (`infrahub_sync/utils.py:244-246`) before any plan is written — falls into no class, and review has no specified behavior for it.

**Minimum fix**. Add to FR-010 or the new FR-027 from CHK008: *"A run directory containing neither a new-format manifest nor a pre-existing plan file MUST be reported as having no plan artifact, distinctly from the v1 and torn cases."*

### CHK033 — RECOMMENDED — missing or unknown run identifier on review

**Anchor**: `spec.md:292-297`, `spec.md:290-291`, `infrahub_sync/utils.py:244-246`, `infrahub_sync/cli.py:98`.

**Evidence**. Nothing in the spec says what happens when review is requested without a run identifier, or with one that does not exist. The existing code makes silence dangerous: `--run-id` on `diff` today feeds `get_potenda_from_instance`, which does `rdir.mkdir(parents=True, exist_ok=True)` (`infrahub_sync/utils.py:246`), so an unknown run id creates an empty directory rather than failing. Reusing that plumbing for `--from-plan` would present a typo'd run id as a plan with zero operations — which FR-022 declares a legitimate, appliable state.

**Minimum fix**. Covered by the FR-027 wording in CHK008, provided it names the two cases explicitly (no run identifier supplied; run identifier not found) and forbids creating the run directory on the review path.

### CHK034 — NIT — large-plan detail rendering deferred rather than excluded

**Anchor**: `spec.md:561-564`, `spec.md:288-289`.

**Evidence**. Open Design Decisions notes the line-oriented encoding *"specifically so a large plan can be summarized and detailed without loading all of it"* and asserts no threshold. Pagination or truncation of per-object detail therefore has no requirement and no exclusion.

**Minimum fix**. Same bullet as CHK027, adding: *"…and no pagination or truncation of per-object detail is required."*

### CHK035 — NIT — partially unreadable run directory

**Anchor**: `spec.md:290-291`.

**Evidence**. FR-007 asserts readability without treating a permission or I/O failure on part of the run directory; the constitution requires explicit handling rather than opaque crashes (`dev/constitution.md:49-50`).

**Minimum fix**. Folded into FR-027 (CHK008) by including "unreadable artifact" among the named review failures — which the proposed wording already does.

### CHK036 — RECOMMENDED — the existing `diff` contract is not recorded as a dependency

**Anchor**: `spec.md:292-297`, `spec.md:76-84` (AD005), `spec.md:479-482` (Assumptions), `spec.md:491-500` (Dependencies).

**Evidence**. Assumptions records the engine and per-run layout (*"a saved plan is already read and dispatched per row… per-side snapshots and run sidecars are already written"*, `spec.md:479-482`) and Dependencies records the owned artifact contract, but nothing records the command being extended. Four concrete properties of that command are load-bearing and unmentioned:

1. **`--run-id` already exists on `diff`** with a different meaning — *"Re-use a specific cache run id."* (`infrahub_sync/cli.py:98`, documented at `docs/docs/reference/cli.mdx:61`). AD005 gives the same flag a read-the-stored-plan meaning, mode-switched by `--from-plan`. Two meanings for one flag on one command is a user-visible overload the spec never acknowledges.
2. **Output channel** — the plan is emitted via the logger to the log stream today (`infrahub_sync/cli.py:153`), which is what makes FR-008's stdout clause a change (CHK011).
3. **Mandatory `--name`/`--config-file`** (`infrahub_sync/cli.py:113-114`) — review still needs a sync name to locate `cache_root_for(sync_name)/<run_id>` (`infrahub_sync/cache/paths.py:26-59`), so an "adapter-free" review is still configuration-bound; FR-008 never says what inputs review takes.
4. **The exclusive pipeline lock** — `diff` wraps its whole body in `pipeline_lock(sync_instance.name)` with a 60 s timeout (`infrahub_sync/cli.py:129`; `infrahub_sync/cache/locks.py:20-33`). Nothing in the spec says a read-only review is exempt, so review of a stored plan could block, or be blocked by, a running sync.

**Minimum fix**. Add a Dependencies bullet recording the existing `diff` contract (its option set including `--run-id`, its current output channel, its required configuration inputs, and the pipeline lock), and one clause in FR-008 stating that review does not take the pipeline lock and does not create or modify anything in the run directory.

### CHK038 — NIT — foldability into the later command group is prose, not requirement

**Anchor**: `spec.md:551-553`, `spec.md:464-465`, brief lines 231-233.

**Evidence**. Open Design Decisions says AD005 *"will later be folded into a `plan` group without changing behavior"*; Out of Scope excludes new groups. No requirement states what must remain true for that fold to be behavior-preserving, so an implementer has nothing to check against.

**Minimum fix**. One clause in FR-008: *"The review surface MUST be expressible later as a subcommand of a `plan` group without a change in behavior, output, or flag semantics."*

### CHK040 — RECOMMENDED — AD005's provisional content is promoted to unconditional MUSTs

**Anchor**: `spec.md:31-34` (provisional preamble), `spec.md:76-84` (AD005), `spec.md:292-297` (FR-008), `spec.md:551-553`.

**Evidence**. The preamble scopes the markers as ratification handles for *"implementation decisions the brief either delegates explicitly or does not reach"*. FR-008's parenthetical narrows the dependency to *"(DBR-020; command and flag spelling per AD005)"*, yet FR-008's body now states as unconditional MUSTs three substantive AD005 commitments beyond spelling: carriage by the existing non-mutating command, the stdout channel, and the single-reader/thin-renderer split. If AD005 is not ratified, those clauses remain normative with no marker on them, and no list says what to revisit.

**Minimum fix**. Either widen FR-008's parenthetical to *"(DBR-020; carriage, output channel, and flag spelling per AD005)"*, or move the channel and thin-renderer clauses behind explicit `[PROVISIONAL AD005]` markers so the ratification handle covers what it actually governs.

### CHK041 — RECOMMENDED — capturability of review output in the in-process case

**Anchor**: `spec.md:294-295`, `spec.md:423-427`, `spec.md:428-430`.

**Evidence**. FR-008 justifies the stdout channel *"so it can be captured and scanned"*, and SC-010 scans *"both review outputs"*. But SC-009 also requires both depths in-process, and FR-008 says the in-process reader is the implementation with the command "a thin renderer" — implying the in-process path returns data and never writes stdout. Which artifact SC-010 scans for the in-process cases, and whether stdout capture is available in every environment the scan runs in, is nowhere recorded.

**Minimum fix**. Add an Assumptions bullet: *"SC-010's scan is performed over the artifact files and over the CLI's captured standard output; the in-process reader returns data rather than writing to a stream, and is scanned as data."*

### CHK042 — RECOMMENDED — no assumption about where a secret could come from

**Anchor**: `spec.md:337` (FR-018), `spec.md:267-272` (FR-002), `spec.md:469-489` (Assumptions), `examples/netbox_to_infrahub/config.yml:32-36`.

**Evidence**. Assumptions covers unique constraints, the qualified path, run-mode vocabulary, engine layout, the adapter write path and the configuration-version value — nothing about secrets. On the qualified path credentials come only from the environment (*"Credentials are read from the environment, not this file"*, `examples/netbox_to_infrahub/config.yml:32`), so the interesting case is a secret carried in source *record* data, which FR-002's full payload would faithfully record. The spec never states which of the two sources FR-018 is defending against.

**Brief position**. **Brief-gap** — DBR-017's derivation (brief line 173) reasons only that *"the plan carries full source payloads, so the rule binds here"*; the brief's Assumptions table (brief lines 266-271) should have carried the source-data assumption.

**Minimum fix**. One Assumptions bullet stating whether source record data on the qualified path is assumed to carry no secret values, and cross-referencing CHK007's rule for the case where it does.

---

## Answers to the flagged questions

1. **CHK001 / CHK037 — CONFIRMED.** No requirement obliges the operation identifier to appear in review output. FR-006 (`spec.md:288-289`) names no fields; FR-008 (`spec.md:292-297`) constrains reachability, channel and implementation only; the only field lists are on the artifact record (FR-002 `spec.md:267-272`, Key Entities `spec.md:371-375`). SC-005 (`spec.md:408-410`) and US1 scenario 1 (`spec.md:111`) both assume it. DBA-005 is therefore unachievable as specified. Root-caused in the brief too (DBR-002, brief line 158).
2. **CHK017 — CONFIRMED.** FR-008 (`spec.md:293-294`) forbids a new *command*; SC-012 (`spec.md:434-437`), Out of Scope (`spec.md:464-465`), Assumptions (`spec.md:488-489`), and the brief's DBR-020 (brief line 176), DBA-012 (brief line 194), D026 (brief line 262) and D002 (brief line 264) forbid only a new *group*. The brief's Constraints leave the carrier free (brief lines 231-233). Consequence: AD005's choice to extend `diff` is *required* only under FR-008's stricter, brief-exceeding bar; under the brief's bar it is one option. The current CLI has no groups at all (`infrahub_sync/cli.py:31`; no `add_typer`), so the group bar is trivially met by any flat addition.
3. **SC-010's scan against both review outputs — achievable but vacuous.** Both outputs are capturable once FR-008's stdout clause holds (`spec.md:294-295`), and there is no technical obstacle. But nothing in the spec says what redaction *does* — FR-018 (`spec.md:337`) states only the outcome; no clause chooses between omitting the field, masking the value, and refusing to plan; and no clause defines what makes a value secret. FR-002's *"full payload"* requirement (`spec.md:269`) pulls directly against it. CHK007 is BLOCKING, with the mechanism choice marked PRODUCT-AMBIGUITY.
4. **SC-009's "in-process" reachability — no named surface.** The only concrete surface in the spec is the CLI one (AD005 pins `--run-id --from-plan --detail --kind` exactly, `spec.md:76-84`). The in-process side has only *"The in-process reader MUST be the single implementation"* (`spec.md:295-296`) — no module, no callable, no inputs, no return shape. The tree offers no default: `infrahub_sync/__init__.py` declares no `__all__` and exports configuration models only. The brief's DBR-020 (brief line 176) says "through the in-process API" without naming one. CHK005 / CHK010, RECOMMENDED.
5. **Actionability of failure and refusal messages — stated only per case, and never for review.** Three apply-path requirements carry content bars: FR-023 *"a clear, actionable error naming the adapter"* (`spec.md:352-353`, matching the engine's existing message at `infrahub_sync/potenda/__init__.py:355-360`), FR-009 *"naming the failed check"* (`spec.md:299-300`), FR-019 *"a message directing the operator to re-plan"* (`spec.md:338-339`). There is no general definition of "actionable", and the review path has no message requirement at all — hence CHK008 (with CHK031, CHK032, CHK033, CHK035 folding into the same fix).

## Before-state for SC-012

Verbatim from `infrahub_sync/cli.py` — five commands on a single flat Typer app, no groups:

- `infrahub_sync/cli.py:31` — `app = typer.Typer()` (no `add_typer` anywhere in the package)
- `infrahub_sync/cli.py:77` — `@app.command(name="list")`
- `infrahub_sync/cli.py:86` — `@app.command(name="diff")`
- `infrahub_sync/cli.py:166` — `@app.command(name="sync")`
- `infrahub_sync/cli.py:295` — `@app.command(name="apply")`
- `infrahub_sync/cli.py:355` — `@app.command(name="generate")`

Rendered equivalent, `docs/docs/reference/cli.mdx:22-26`: `list`, `diff`, `sync`, `apply`, `generate`.

`diff`'s existing options (`infrahub_sync/cli.py:89-110`; `docs/docs/reference/cli.mdx:55-64`):

`--name`, `--config-file`, `--directory`, `--branch`, `--show-progress / --no-show-progress`, `--adapter-path` (repeatable), `--run-id`, `--concurrent-load / --no-concurrent-load`, `--full-extract / --no-full-extract`, `--help`.

Note for SC-012's comparison: `--run-id` is **already present** on `diff` with the meaning *"Re-use a specific cache run id."*
