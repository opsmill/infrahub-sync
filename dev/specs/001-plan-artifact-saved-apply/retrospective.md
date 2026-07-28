---
description: "Session retrospective — delivery-apply run for DB-001 / INFP-653"
---

# Session retrospective — saved plan artifact and apply

**Scope**: the `delivery-apply` run for delivery brief DB-001 (JPD card INFP-653)
**Branch**: `001-plan-artifact-saved-apply-infp-653`, base `9edc1bc` → head `12d7a27`
**Run date**: 2026-07-26 to 2026-07-28
**Outcome**: `INCOMPLETE`, narrowly — 107 of 107 tasks and 182 of 182 checklist items closed; the
sole unmet check is `uv run invoke lint` (exit 28), which finding R1 below shows has never passed on
this repository.

> **Dispositions in this report were proposed, not executed.** The brief owner authorized saving the
> retrospective without acting on it. Every `fix-now`, `open-pr` and `github-issue` row below is a
> recommendation awaiting a separate approval.

## Findings

### Instructions / configuration gaps

**R1 — the mandated lint gate cannot pass, and CI does not run the step that fails it.**
`AGENTS.md:21-31` makes `uv run invoke lint` a required pre-commit gate and states that it "runs
ruff → pylint → yamllint → ty". `tasks/linter.py:41` runs bare `pylint infrahub_sync/` with no
`--fail-on` or `--fail-under`, so **any** emitted message is a non-zero exit: `main` emits 56
messages and exits 30. Two further facts make the contradiction plain — `AGENTS.md:95` says "Pylint:
fix actionable issues in touched code; some warnings are expected", which the gate's all-or-nothing
exit code cannot express; and `.github/workflows/workflow-linter.yml:95` has the pylint step
**commented out**, so CI enforces ruff and `ty` but never the step that fails the local gate. No
delivery brief can make this gate pass. Recorded during this run as **AD093** (`governance`).

*Improvement*: reconcile the three surfaces — either give `lint_pylint` a `--fail-on`/`--fail-under`
threshold that matches "some warnings are expected", or amend `AGENTS.md` to say the pylint step is
advisory and name the criterion the repository actually holds.

*Disposition*: `github-issue`. It changes the meaning of the project's own gate and touches the
tooling CI is meant to mirror; it needs an owner, and triaging `main`'s 56 messages is its own task.

**R2 — nothing requires an integration-marked test to have run before its task closes.**
`AGENTS.md:109-115` covers marking network and integration tests opt-in, but says nothing about
executing them. This run closed seven Phase H tasks on tests that were not merely unexecuted but
**non-functional**: the generate step was missing from the fixture, so every one errored in setup
(**AD090**). A test that has never run is not evidence of anything, including of its own validity —
"authored, not satisfied" was too weak a description of that state.

*Improvement*: add to the Testing section that an `integration`-marked test may be marked done only
with a recorded run — a pass, or a skip whose reason is verifiable.

*Disposition*: `fix-now`. `AGENTS.md` prose only; no Ask First topic.

**R3 — magnitude claims are not required to name their source.**
**AD091**: the run's V30 claimed ten mapping entries across nine kinds carried a
relationship-crossing convergence key. The real figure was five across five — it had been read from
the source-side sync configuration rather than the destination schema. Four decisions cited it, and
it survived three rounds of three-lens critique. A count read off the artifact under review is
indistinguishable from a verified one for as long as the schema it describes is unreachable; only
live data exposed it.

*Improvement*: `.specify/templates/spec-template.md` and `tasks-template.md` should require any count
or magnitude claim to cite the artifact it was read from and the command that produced it.

*Disposition*: `open-pr`. `.specify/templates/` is kept at SpecKit upstream parity (`f5c4f05`), so a
local edit will drift and needs review of its own.

### Documentation gaps

**R4 — no knowledge page describes the destination write surface.**
`dev/knowledge/adapter-anatomy.md` and `dev/knowledge/sync-architecture.md` describe the pre-change
engine. Nothing states what a destination adapter is permitted to do at write time. The replace-set
flush (**AD085 → AD088**) took two shipped attempts and one owner escalation, partly because no page
said what the boundary was.

*Improvement*: a `dev/knowledge/` page covering the planned-write surface, apply-time peer
resolution, and replace-set flush semantics.

*Disposition*: `local-only`. The extraction step that runs next owns exactly this; recorded here so
it is not lost, with no separate action proposed.

**R5 — the repository's lint and type baseline is nowhere recorded.**
No page states what `main` itself emits, so every delivery run must re-derive which diagnostics are
inherited and which it introduced. This run derived it twice: 56 pylint messages and exit 30, and 3
`ty` diagnostics.

*Improvement*: record the baseline and the commands that regenerate it under `dev/guidelines/`.

*Disposition*: `github-issue`, filed together with R1 — the two are one governance problem.

### Architectural friction

**R6 — the destination write path had no typed boundary until this run added one.**
AD086/AD087 introduced a Protocol for the write surface. Before it, **AD054** prescribed a change to
`update_node` without checking that its only caller is the live `sync` write path; the fidelity lens
caught that and it was withdrawn as **AD070**. An untyped, undocumented write boundary is what let a
plausible-looking prescription reach a gate. `dev/adr/` currently holds only `README.md`.

*Improvement*: an ADR fixing the write-surface Protocol as a reviewed boundary, so future changes to
it are reviewed as boundary changes.

*Disposition*: `github-issue`. An ADR needs human authorship.

**R7 — emptying a peer set took three shapes.**
The replace-set flush went from a full `node.update(do_full_update=True)` (`92ce0dc`) to a targeted
relationship write (`364bf0d`), by way of an owner escalation. The underlying SDK constraint that
forced the change was not re-verified in this session, so it is stated here as an open question
rather than a conclusion.

*Improvement*: establish the precise SDK behavior, then either record it as a known limitation or
raise it upstream.

*Disposition*: `github-issue`, after the constraint is verified.

### Mistakes and corrections

**R8 — every repair that went unverified turned out partly wrong.**
Twice a write-producing agent's self-report claimed a fix it had not made: a keyedness gate that
never moved, and a "fetch first" prescription that was a provable no-op because `fetch()`
self-guards. Round 3's single blocking finding was a defect *inside* the repair of a repair, and the
narrow check of that repair found it still incomplete. Separating reviewers from remediators is the
only reason any of these surfaced.

*Improvement*: state in `AGENTS.md`'s Review Process (`:166`) that a remediator's self-report is not
evidence of the repair, and that the verifying read is performed by something other than the agent
that wrote the change.

*Disposition*: `fix-now`. `AGENTS.md` prose only.

**R9 — overclaiming in the run's own records recurred four times.**
Corrected at `40df068`, plus R3's wrong figure: a taxonomy claimed to "replace any broad catch" while
broad handlers remained; a task asserted every gate exits 0; convergence was claimed unconditionally
when it held only for an all-direct key.

*Improvement*: require universal or quantified claims in spec, task and report text to carry the
command that establishes them — the same rule R3 asks of the templates, applied to prose.

*Disposition*: `fix-now`, as one edit with R8.

**R10 — a ratified override that does not reach the brief text becomes debt for the run's whole life.**
Brief v5 shipped text contradicting **AD055**, which its own approver had ratified. v6 closed the
contradiction only at the very end, so the delivery run carried it throughout.

*Improvement*: the delivery protocol should require the brief revision in the same turn as any
ratified override to it.

*Disposition*: `local-only`. `delivery-protocol` lives under `~/.claude/skills/`, outside this
repository, and cannot be fixed from here.

**R11 — self-reported orchestration defects.**
The Phase 4 decision packet omitted the standing authorizations. **AD074** records a ground that does
not hold. **AD054**'s withdrawal (R6) belongs to the same class: a prescription written without
checking its only caller.

*Improvement*: same out-of-repo skill surface as R10.

*Disposition*: `local-only`.

## Disposition buckets

| Bucket | Findings |
|--------|----------|
| `fix-now` | R2, R8, R9 — all `AGENTS.md` prose |
| `open-pr` | R3 — SpecKit template text, reviewed apart from this feature |
| `github-issue` | R1 + R5 as one lint-governance issue; R6 write-surface ADR; R7 after verification |
| `local-only` | R4 (extraction owns it), R10, R11 (skills outside this repository) |

**None of these were executed.** This report is the only artifact produced.
