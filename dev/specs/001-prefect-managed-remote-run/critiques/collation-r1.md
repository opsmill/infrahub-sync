# Critique collation — Round 1

Three lenses ran in parallel against the brief, spec, plan, tasks, and contracts
(engineering-r1.md: 15 findings, 7 blocking; ergonomics-r1.md: 14 findings, 3 blocking;
fidelity-r1.md: 7 findings, 1 blocking). Deduped below into themes. Root verified every
blocking theme against the working tree before disposition.

## Cross-lens dedup — blocking themes

| Theme | Engineering | Ergonomics | Fidelity | Verification | Disposition |
|---|---|---|---|---|---|
| Wrap boundary and CLI error mapping (DBR-009 unsatisfiable as contracted) | E1, E2 (must) | — | — | CONFIRMED — root re-read `cli.py:135-160, 235-240, 260-290`: factory `ValueError` → prefixed abort; serial-load `ValueError` → unprefixed abort; all other lifecycle failures → broad-except mark-failed + bare re-raise | **D009 — blocking, remediate** |
| No-prefect-import test as specified fails/flakes in dev+prefect env | E3 (must) | X3 (must) | — | CONFIRMED — cross-lens; `pyproject.toml` addopts `--dist loadscope`; module-level importorskip imports at collection | Remediate (single sound fix: subprocess probe; no fork) |
| Log bridge never manages source logger level (SC-002 silently 0%) | E4 (must) | — | — | CONFIRMED — `cli.py:41-48` `_setup_logging` sets the level explicitly; handlers never defeat `isEnabledFor` | Remediate (sharpens D004 mechanism) |
| `raise ... from exc` re-leaks unredacted cause via traceback (DBA-008 fails by design) | E5 (must) | — | — | CONFIRMED — standard traceback rendering includes the cause chain; `potenda/__init__.py:230-246` embeds upstream text | Remediate (redact whole cause chain at wrap point) |
| Eager whole-directory config validation: one bad neighbor bricks all remote runs + leaks contents | E6 (must) | X2 (must) | — | CONFIRMED — cross-lens; `utils.py:123-148` validates every discovered config before name matching | **D010 — blocking, remediate** |
| CWD-dependent qualified example silently yields zero-create "success" | E11 (rec) | X1 (must) | — | CONFIRMED — cross-lens; `examples/custom_adapter/config.yml` `./` paths; `plugin_loader.py:235-238` CWD resolve; adapter `self.data = {}` fallback | Remediate (README/quickstart CWD pin + fixture-diagnosability task) |
| Parity/fingerprint test unsatisfiable with MagicMock fixtures; summary derivation must be single-source | E8 (must), E7 (rec) | — | — | CONFIRMED — root re-read `tests/cache/test_cli_sync_cache.py:17-33`: `write_plan` no-op, `diff()` returns MagicMock | Remediate (pin in-memory row derivation; behavioral FakePotenda) |
| Prefect pin ratification (brief row falsified) | — | — | F1 (must), F2 | CONFIRMED — root independently re-ran both resolutions | **D005/D006 — packet items** (artifact change limited to F4 checklist notes) |

## Cross-lens dedup — recommended and nits

| Theme | Findings | Disposition |
|---|---|---|
| uv.lock regeneration + ty gate environment | E9 | Remediate (amend T012/T039) |
| Redaction set: adapter env credentials by name pattern | E10 | Remediate (contract + T005) |
| Fingerprint null normalization | E12 | Remediate (contract + T009) |
| Serve missing-extra failure message | X4 | Remediate (T015 contract behavior) |
| Missing-credential message names env vars | X5 | Remediate (task note; adapter-message addition as in-scope task text) |
| Config directory scoped to `examples/custom_adapter` | X6 | Remediate (quickstart + T034) |
| RunResult remote retrieval: contractual fixed-format summary line | X7 | Remediate (flow contract + T035 mapping) |
| Live-test skip guards + marker help | X8 | Remediate (T017) |
| orchestration.mdx leads with packaged integration | X9 | Remediate (T035) |
| Flow-test isolation (prefect_test_harness / PREFECT_HOME) | X10 | Remediate (T016/T022/T023) |
| README ops gaps (empty-destination check/reset, port 4200, cleanup, artifact locality) | X11 | Remediate (T034) |
| D007 evidence nuance (Setup block consistency-driven, not verbatim-mandated) | F3 | Note in D007 record + packet; no artifact change |
| Stale pre-D005 checklist attestations | F4 | Remediate (dated revalidation notes, append-only) |
| Decision-ID stamping D001–D008 | F5 | Remediate (stamp clarifications; ID→artifact map) |
| T035 docs page lacks a decision record | F6 | **D011 — new decision record, packet item** |
| Contract §1 PyPI install line vs T034 rule | F7 | Remediate (contract line) |
| serve.py print/log mechanism; RunResult.summary mutability; lock-timeout seam; deployment name stutter; status-vocabulary mapping; example adapter print() | E13, E14, E15, X12, X13, X14 | Remediate (small artifact edits: pin logging mechanism, MappingProxyType, sanctioned `_lock_timeout` seam, deployment name `run`, T035 mapping table, task note for example logging) |

## New decision records (round 1)

### D009 — Sanitize-and-wrap boundary moves to the remote layer; CLI path preserves 9edc1bc failure behavior verbatim

**Question:** Where does the RunValidationError/RunExecutionError wrap-and-sanitize happen so DBR-009 (CLI behavior identical) and DBR-015 (typed sanitized remote failures) can both hold?
**Evidence:** E1 + E2, root-confirmed: today's CLI has three distinct failure behaviors, two sharing `ValueError`; both load paths wrap into `ValueError` (`potenda/__init__.py:230-246`); today's CLI itself uses the broad-except mark-failed + bare re-raise pattern the contract's single `__cause__` mapping cannot reproduce.
**Options:** A — `execute_run` keeps today's exact CLI pattern (narrow `ValueError` handling where today has it; broad `except Exception:` mark-`run.json`-failed + bare re-raise, explicitly documented as the preserved 9edc1bc pattern with a targeted `# noqa: BLE001`); the sanitize-and-wrap into `RunValidationError`/`RunExecutionError` lives only in `run_remote_request`, the remote-only composition. CLI failure behavior is byte-identical by construction. B — wrap inside `execute_run` with a stage-typed attribute and a CLI unmapping layer (more moving parts, must reproduce three behaviors from metadata).
**Recommendation:** A. **Rationale:** identity by construction beats identity by reconstruction; the broad catch is the preserved existing pattern, honestly documented, not new looseness. **Confidence:** High. **Origin:** `inherent`. **Status:** PROVISIONAL (CHECKPOINT).

### D010 — Tolerant per-file configuration resolution for the remote surface

**Question:** How does `resolve_sync_instance` behave when files under `INFRAHUB_SYNC_CONFIG_DIRECTORY` are unreadable or invalid?
**Evidence:** E6 + X2, root-confirmed: `utils.get_all_sync` eagerly validates every discovered `config.yml` before name matching (`utils.py:123-148`), so one broken neighbor blocks all names and pydantic's `input_value` echo can leak unresolvable-config contents the redactor never collected.
**Options:** A — `resolve_sync_instance` performs its own tolerant walk (same glob, same exact-name match): unrelated broken files are skipped with a WARNING naming the file; a file whose raw `name:` matches the request (or is unreadable where the name may live) raises `RunValidationError` naming the logical name and file path only, parse detail never chained verbatim. CLI keeps calling `get_instance` unchanged. B — reuse `get_instance` as-is and document the eager behavior (contract's promised failure shape becomes undeliverable; blast radius stays).
**Recommendation:** A. **Rationale:** the remote failure contract is otherwise unimplementable, and the blast radius contradicts DBR-005's one-directory boundary intent; CLI behavior untouched. **Confidence:** High. **Origin:** `inherent`. **Status:** PROVISIONAL (CHECKPOINT).

### D011 — Docs-site reference page (T035) is governance-mandated scope the brief did not enumerate

**Question:** Does the preview ship the Docusaurus reference page and orchestration.mdx revision (T035), given the brief's deliverable list names only "one example"?
**Evidence:** F6: AGENTS.md Documentation policy ("Update `docs/` for any user-visible changes") is incorporated by the brief's constraint "The implementation follows the repository workflow"; a new optional extra + remote flow is user-visible. The run recorded the analogous smaller expansion (D007) as a decision; consistency requires this one be ratified too. X9 additionally shows the existing page teaches the pattern this feature obsoletes.
**Options:** A — ship T035 (one reference page + sidebar entry + orchestration.mdx Prefect subsection revised to lead with the packaged integration). B — strike T035 and leave docs untouched (violates repo docs governance for a user-visible change). C — cross-link only (leaves obsolete DIY guidance primary).
**Recommendation:** A. **Rationale:** repo governance requires it; the brief's constraints incorporate repo workflow; leaving contradictory docs live is worse than the modest scope addition. **Confidence:** High. **Origin:** `brief-gap` / `systemic` — no current brief-template slot states whether AGENTS.md's docs-governance obligation is in scope for a preview feature; planners must be prompted to declare it. **Status:** PROVISIONAL (CHECKPOINT).

## Round-1 verdict

11 blocking findings dedupe to 8 blocking themes; all CONFIRMED; none REFUTED or OVERSTATED
(F3 records one minor evidence overstatement inside D007's rationale — noted, does not change
the decision). All 8 route to a single remediation agent (artifact-level only; no product code
exists yet). Round 2 re-runs all three lenses: remediation touches engineering- and
ergonomics-owned inputs, and D009–D011 move requirements/scope, which re-triggers fidelity.
