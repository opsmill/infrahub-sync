# Session Retrospective — DB-001 / 001-prefect-managed-remote-run

**Scope**: feature `001-prefect-managed-remote-run` (delivery brief DB-001, run
`20260730T192231Z`), branch `001-prefect-managed-remote-run-local-dp-001`, delivery HEAD
`52953bf`, base `9edc1bc`. While this retrospective was being written the summary phase
landed one docs-only commit, `f643e2a` (`sessions/session-2026-07-31-1055.md`); no finding
below depends on it.
**Report path**: `dev/specs/archive/001-prefect-managed-remote-run/retrospective.md`
**Status of this report**: **report saved only.** No disposition action below has been
executed. Nothing was committed, pushed, stashed, amended, or filed.

Sources verified for every finding: `run-report.md` (2114 lines),
`opsmill-implement-report.md`, `tasks.md`, `AGENTS.md`, `tasks/__init__.py`,
`tasks/linter.py`, `tasks/docs.py`, `pyproject.toml` `[tool.rumdl]`, `uv run invoke --list`,
and the run ledger, acceptance matrix, decision register, final report, and planner feedback
in the archived DB-001 run ledger in the `infrahub-sync-lab` repository.

A note on framing: most of what follows is a property of the *process*, not of this feature.
The delivered code came through two full lens rounds, a convergence pass, and two
review-and-remediation passes with zero open blocking findings. The friction was in how
facts about the repository were measured, relayed, and recorded.

## Findings

| ID | Category | Evidence | Improvement | Disposition |
|----|----------|----------|-------------|-------------|
| R1 | Instructions / Configuration Gaps | `AGENTS.md:27` mandates `uv run invoke format` and `AGENTS.md:137` offers `uv run rumdl fmt .` as the fix command. Both reach `docs.format` → `rumdl fmt`, which misparses the wrapped line `#3 (D003) → T007/T008…` at `tasks.md:262` as an ATX heading, drops text, and cascades a heading demotion through the file. Reproduced and reverted twice (`run-report.md:44-64`); `dev/specs/**` is not in `[tool.rumdl] exclude` (`pyproject.toml:353-363`) | Stop pointing agents at a command that damages tracked files: either add `dev/specs/**` to the rumdl exclude list, or replace the `fmt` instruction with `check` plus hand-fixing, and name `invoke linter.format` as the Python-only formatter | open-pr |
| R2 | Instructions / Configuration Gaps | `AGENTS.md:31` documents `invoke lint` as "ruff → pylint → yamllint → ty". Actual order is rumdl first (`tasks/__init__.py:24-26`), and every leg runs via `context.run` with no `warn=True` (`tasks/linter.py:43`), so the first non-zero exit aborts the chain. A Markdown nit in this run's own planning commit hid the pylint status entirely (`run-report.md:82-94`) | Document the real order including rumdl-first, and state that the chain short-circuits so downstream legs must be asserted by direct invocation | fix-now |
| R3 | Instructions / Configuration Gaps | No repo-side record of the inherited lint baseline. `AGENTS.md:100` says only "some warnings are expected". `invoke lint` exits **30** at `9edc1bc` (rating 9.60/10), and the diagnostic count is environment-dependent — 30 in a base-only environment, **56** in dev+prefect at the same commit (`run-report.md:1853-1860`). This run measured it three times and corrected its own figure twice | Record the inherited baseline in `AGENTS.md` or `dev/knowledge/`: exit code, rating, code set, and the fact that the count varies with installed extras, so the no-regression comparison must be environment-matched | fix-now |
| R4 | Instructions / Configuration Gaps | `AGENTS.md:113` mandates `structlog` ("not `print`"); `infrahub_sync/` uses stdlib `logging` throughout, and this feature's Prefect log bridge requires stdlib handlers. Raised as D008 (governance) with a constitution patch deferred outside the run — `AGENTS.md` itself was never amended, so the next agent inherits the same contradiction | Amend the Logging section to describe stdlib `logging` as the standard (or adopt structlog deliberately) and pair it with the declared-but-unused dependency; same for the constitution's `markdownlint-cli2` reference, which the repo replaced with `rumdl` | open-pr |
| R5 | Instructions / Configuration Gaps | Preflight measured the baseline lint gate as `invoke lint \| tail` and read `$?`, capturing `tail`'s status rather than the task's. That reproduced the brief's own false "exits 0" claim and moved discovery of an inherited failure from preflight into the first implementation chunk (`final-report.md:93-96`, `run-ledger.md:196-202`). `delivery-apply/SKILL.md:122-124` requires baseline gates be recorded and warns "never first discover an inherited failure at completion", but never constrains *how* a status is measured | Add a measurement rule on the apply side, mirroring the one `planner-feedback.md:52` proposes for briefs: gate status is captured directly from the command's own exit code, never through a pipe, and the recorded evidence is the command plus its verbatim output. **Process property, not feature-specific** | github-issue |
| R6 | Instructions / Configuration Gaps | Three unverified numeric relays from subagent reports into durable artifacts: `run-ledger.md:244-245` writes "54 diagnostics across 8 codes" in a sentence that enumerates 11; a 9.68 rating was relayed where the tree measured 9.69 (`run-report.md:1682,1807` vs `acceptance-matrix.md:86` and `tasks.md` T039); and a "~30 diagnostic" baseline was repeated as absolute when it is environment-dependent. A later subagent refused to write an unverified figure and measured it instead | Require that root either re-measures a subagent's number before restating it in a durable artifact, or attributes it explicitly as reported-not-verified. Prefer set-based invariants over counts where the count is environment-sensitive. **Process property** | github-issue |
| R7 | Instructions / Configuration Gaps | Three subagents ran `git stash` against explicit instructions. Each disclosed it and no work was lost, but only one landed in the ledger (`run-ledger.md:133`); the other two survive as an unattributed back-reference at `run-report.md:2111` and `acceptance-matrix.md:158` ("two earlier chunks had to disclose"). Chunk 9 later demonstrated the safe alternative — `git archive 9edc1bc … \| tar -x -C <scratch>` (`run-report.md:1824`) | State the safe alternative inside the prohibition itself rather than only forbidding the pattern, and require every disclosed deviation to be logged in the ledger with the agent id, so the count is recoverable from artifacts. **Process property** | github-issue |
| R8 | Documentation Gaps | `dev/knowledge/` has five pages, all adapter-centric (`adapter-anatomy`, `incremental-and-cache`, `schema-mapping`, `sync-architecture`, `README`). Nothing documents the quality-gate surface, so every fact this run needed was rediscovered: rumdl runs first, no `warn=True`, exit 30 is normal, `invoke linter.format` is the Python-only formatter, and there is no `linter.lint` task at all | Add `dev/knowledge/quality-gates.md`: what `invoke format` / `invoke lint` actually run and in what order, short-circuit behavior, the inherited baseline and its environment sensitivity, and which command to use for each leg | open-pr |
| R9 | Documentation Gaps | `AGENTS.md:33-37` lists `infrahub-sync generate --name from-netbox` as a CLI sanity check without saying it rewrites four committed files and leaves the tree dirty. `generator/templates/diffsync_adapter.j2:26` and `diffsync_models.j2:29` iterate the live schema unsorted, so output tracks API response order: ~242 lines of pure ordering churn, and a fixed point that is stable within a window but moves across windows (484 lines between the T002 commit and hours later) — `run-report.md:184-267`, `1968-2029` | Document the non-determinism where the sanity check is prescribed, and say the expected outcome is churn to be restored with `git checkout --`, not a clean tree. The generator fix itself is already filed as `bug-generate-output-is-not-deterministic-across-runs.md` | github-issue |
| R10 | Architectural Friction | `invoke lint` cannot function as the approval gate `AGENTS.md:166` assumes. It never exits 0 on a clean checkout, each leg propagates its own exit so downstream legs never run, and there is no aggregate. Compounding defect: `tasks/linter.py:12` decorates the Python-lint aggregate `lint_all` with `@task(name="format")`, colliding with `format_all` at line 24 — `uv run invoke --list` shows `linter.format` (the formatter) and **no** `linter.lint`, so the Python-only lint aggregate is unreachable by any name | Fix the decorator name collision, then decide deliberately whether the inherited `C0415` set is suppressed in config or fixed, so a green gate means something. Adjacent to CI, so not `fix-now` | github-issue |
| R11 | Architectural Friction | The delivery artifact model has no supersession path for a terminal report. This run wrote `final-report.md` (BLOCKED, HEAD `7bdb8a8`) and `opsmill-implement-report.md` ("INCOMPLETE — stopped after chunk 1 of 9"), was then unblocked by a post-gate ratification (D014), and ran to completion at `52953bf`. Today `tasks.md` is 44/44 `[X]` and `acceptance-matrix.md` reports 15/15 requirements and 11/11 criteria PASS, while the implement report still reads INCOMPLETE; `run-ledger.md`'s phase log records no dispatch for chunks 2–9 and no second gate; and the withdrawn "option B is impossible" claim still stands uncorrected at `run-ledger.md:190`, its retraction living only in `decision-register.md:228` and `run-report.md:229-238`. `delivery-apply/resources/phases.md:114` versions implement reports for convergence cycles only, not for resumption | Define a resumption entry: a terminal report that is later superseded gets an explicit superseding header and pointer, the resumed implementation produces its own `opsmill-implement-report-pass-N.md`, the ledger gains a "resumed after ratification" phase entry, and a withdrawn claim is annotated in place, not only in the register. **Process property**. The one in-repo instance (a superseding header on `opsmill-implement-report.md`) is small enough to fix on this branch | github-issue |
| R12 | Mistakes & Corrections | "Option B is impossible" was asserted to the run owner about generator determinism on the strength of a malformed measurement: the comparison used a flattened directory copy in which `infrahub/sync_adapter.py` collided with `netbox/sync_adapter.py`, and the collision — not the generator — produced the diff. Retracted; the chunk worker's contrary "runs 2–6 byte-identical" observation was correct, and B was re-offered to the owner and declined on its merits (`run-report.md:229-255`, `decision-register.md:228-230`) | Guardrail: when root's measurement contradicts a subagent's direct observation, reproduce it with the subagent's own procedure before relaying it as a conclusion; and never flatten a tree to compare files whose basenames repeat. A second, smaller instance of the same class: a truncated `diff -r` read as dropped models, corrected by counting models directly | local-only |
| R13 | Mistakes & Corrections | The first review's remediation introduced two defects the green suite could not see: `_add_url_userinfo` ran only from the settings arm, so a password embedded in an **environment** endpoint variable (`NETBOX_ADDRESS`, `INFRAHUB_ADDRESS`) — the primary credential channel for the remote model — leaked verbatim into both the message and the rendered cause chain; and bare `key`/`auth` substring matching over-collected from shipped example configs (`response_key_pattern: "objects"`, `auth_method: "api-key"`), shredding the missing-credential diagnostic that same remediation had just added. Plus unbounded recursion on a YAML alias cycle. All three were found only by a second review pass scoped to the remediation diff `141ad14..2bb58f7` (`run-ledger.md:337-380`) | Guardrail: a remediation that widens a security-relevant collector is a new change owed its own review over its own diff — never validated by the suite it was written against. This run caught it because root chose to run the pass; make that pass required whenever remediation adds a security-relevant collector or crosses a process boundary. **Process property** | github-issue |
| R14 | Mistakes & Corrections | Several tests asserted nothing and passed. Mutation testing found real gaps — 39 mutations in review pass 1 and 12 in pass 2 — covering remote safety defaults (M42/M43), whole-cause-chain depth (M9c), longest-secret-first ordering (M29), the diff rendering itself (M27), contractual summary-line field positions (M32), pinned factory kwargs (M44), and `secret_context` inheritance, whose deletion left all 121 tests green (`run-ledger.md:260-300`, `337-358`) | Promote the practice into repo guidelines: for contract-bearing behavior, the acceptance criterion for a test is a killed mutation, not a passing assertion. `dev/guidelines/testing-adapters.md` is adapter-scoped and carries no general test-teeth rule | github-issue |
| R15 | Mistakes & Corrections | D007 mandated editing `AGENTS.md` plus the mirror `.github/copilot-instructions.md`; the mirror is a git-tracked symlink (mode `120000`) to `../AGENTS.md`, so the single edit satisfied both and `.cursor/rules/dev-standard.mdc` does not exist. Root's gate-time verification read the symlink's resolved content and concluded it was a separate file (`run-report.md:173-179`, `run-ledger.md:209-213`). Harmless — the answer was right in effect, the rationale overstated the work | Guardrail: check `git ls-files -s` mode or `test -L` before asserting that two instruction files need separate edits. Worth one line in the mirror rule, since `AGENTS.md:151-155` names the mirrors without saying one is a symlink | local-only |

## Practices worth keeping

Recorded deliberately, because they are what kept the run honest and they are cheap to lose.

- **Falsifying probes before ratifying a third-party assumption.** The plan worker stopped on
  probe (a) and proved `prefect==3.7.2` unsatisfiable alongside the base dependency set
  (`redis<5.0` via `diffsync[redis]` vs `>=5` via `pydocket`), at planning time rather than at
  install time (`run-ledger.md:121`). The brief had asserted availability on evidence from an
  unpinned `uv run --with prefect` overlay that never resolves against the package.
- **Disjoint reviewer context as a structural guarantee.** Engineering and ergonomics lenses
  were denied the brief; the fidelity lens was denied repository source. Only fidelity could
  therefore emit `brief-gap` findings, and it twice concluded `NEEDS_INTAKE_REVISION` was not
  required (`run-ledger.md:126-129`, `final-report.md:116-119`). Round 2 earned its cost: the
  round-1 remediation had introduced five new blocking defects.
- **Mutation kill as the acceptance criterion for a test-teeth fix.** Remediation B closed I3–I10
  by making each named mutation fail and then reverting it — not by adding assertions and
  declaring the gap closed (`run-ledger.md:289-292`).
- **Reviewing the remediation diff rather than trusting a green suite.** This is the single
  highest-yield decision in the run: it is what found the credential leak in R13.
- **Subagents refusing to write unmeasured numbers**, and reviewers flagging unattributed commits
  rather than assuming them (`run-ledger.md:303-316`).

## Disposition Buckets

None of the following was executed. They are recorded as proposals only.

### fix-now

- R2: `AGENTS.md:31` — correct the documented `invoke lint` order to include rumdl-first and note
  the short-circuit.
- R3: `AGENTS.md` (Code Standards / Policy) — record the inherited lint baseline, its rating, and
  its environment sensitivity.
- R11 (in-repo instance only): a superseding header on
  `dev/specs/001-prefect-managed-remote-run/opsmill-implement-report.md` pointing at
  `run-report.md` and the acceptance matrix as the authoritative outcome.

### open-pr

- R1: `AGENTS.md` + `[tool.rumdl] exclude` — stop prescribing `rumdl fmt` over `dev/specs/**`.
- R4: `AGENTS.md` Logging section + `dev/constitution.md` — reconcile structlog against the
  stdlib-`logging` reality and the `markdownlint-cli2` reference against `rumdl`.
- R8: new `dev/knowledge/quality-gates.md`.

### github-issue

- R5: "Baseline gate status must be measured from the command's own exit code, never through a
  pipe" (`delivery-apply` skill, `infrahub-sync-lab`).
- R6: "Root may not restate a subagent's measured figure in a durable artifact without
  re-measuring or attributing it" (`delivery-apply` / `delivery-protocol`).
- R7: "Pair the `git stash` prohibition with its safe alternative, and log every disclosed
  deviation in the ledger with its agent id".
- R9: "Document that `infrahub-sync generate` dirties the tree, where the sanity check is
  prescribed" (this repo; the generator fix is already filed).
- R10: "`invoke lint` cannot serve as an approval gate — fix the `linter.py` task-name collision
  and decide the `C0415` disposition" (this repo, CI-adjacent).
- R11: "Define a resumption path for terminal delivery artifacts" (`delivery-apply` skill).
- R13: "Require a review pass over any remediation diff that widens a security-relevant
  collector" (`delivery-apply` skill).
- R14: "Add a general test-teeth rule to `dev/guidelines/`: contract-bearing behavior needs a
  killed mutation" (this repo).

### local-only

- R12: measurement hygiene — reproduce a contradicting measurement with the subagent's own
  procedure before relaying it; never flatten a tree to compare repeated basenames.
- R15: check `git ls-files -s` mode or `test -L` before treating mirrored instruction files as
  separate edits.

## No action

- **Scope fidelity.** The fidelity lens confirmed all 15 requirements and 11 acceptance criteria
  carried verbatim in both rounds, with no backlog leakage; T038 re-proved B-001–B-007 absent by
  name. Nothing to improve.
- **Secret hygiene in artifacts.** Credentials came from the session environment throughout and
  appear in no file, request body, or log line; the T037 transcript scan found zero token
  occurrences. Working as intended.
- **Live-environment discipline.** No lab container was stopped, restarted, modified, or exec'd
  into across any phase, and `PREFECT_HOME` was redirected to scratchpad directories so the
  developer's `~/.prefect` was never touched. Working as intended.
- **The D015 logger-name deviation and the R-5 no-op `timeout` marker** were found, ratified, and
  disclosed rather than silently accepted or silently fixed. That is the intended behavior, not a
  gap.
