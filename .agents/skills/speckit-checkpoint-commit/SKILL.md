---
name: speckit-checkpoint-commit
description: Commit the artifacts produced by the speckit phase that just completed
compatibility: Requires spec-kit project structure with .specify/ directory
metadata:
  author: opsmill
  source: infrahub-sync repo (referenced by speckit-opsmill-prep/-implement/-auto; not provided by any extension)
user-invocable: true
disable-model-invocation: false
---

# Speckit Checkpoint Commit

Create exactly one commit capturing the artifacts produced by the speckit phase
that just completed (specify, plan, critique, tasks, an implement chunk, review
fixes, or the final report). The opsmill orchestrators invoke this skill after
every phase so each phase is independently reviewable and revertable.

## Guard rails — check these first

1. Confirm you are inside a git work tree: `git rev-parse --is-inside-work-tree`.
   If not, warn and skip — never initialize a repository from here.
2. Reject detached HEAD: if `git symbolic-ref -q HEAD` fails, **abort without
   committing** and surface the problem — a checkpoint on a detached HEAD is
   unreachable the moment anything else is checked out.
3. Hard-deny protected branches. If `git rev-parse --abbrev-ref HEAD` returns
   `main`, `master`, `develop`, `stable`, `trunk`, or anything under
   `release/`, **abort without committing** and surface the problem to the
   caller — the pipeline should have created a feature branch (jpd extension
   `before_specify` hook) before any phase ran.
4. Soft-check the branch name. If it does not match one of the speckit
   feature-branch patterns — sequential `^[0-9]{3,}-`, timestamp
   `^[0-9]{8}-[0-9]{6}-`, or ticket-suffixed `-(infp|ifc)-[0-9]+$` — proceed,
   but flag the non-standard name prominently in your report so the
   orchestrator log records it. (Hard-blocking here would break legitimate
   runs on hand-named branches; the hard guards above cover the dangerous
   cases.)
5. Check the index before staging anything: run `git diff --cached
   --name-only`. If it lists files that are not attributable to the phase
   that just finished, **abort without committing and without modifying the
   index** — never `git reset`, unstage, or otherwise touch pre-staged
   entries; they are someone else's work in flight. Surface the offending
   paths in your report. If the pre-staged entries are all phase-attributable
   (e.g. a prior invocation staged but failed before committing), proceed and
   note this in the report.
6. Never push. Never amend. One new commit per invocation, nothing else.

## What to stage

Stage the changes attributable to the phase that just finished:

- Preparation phases (specify / plan / critique / tasks): the feature
  directory's contents. Resolve the feature directory the way the tooling
  does: `SPECIFY_FEATURE_DIRECTORY` when set, else the `feature_directory`
  key from `.specify/feature.json` when present and parseable, else
  `dev/specs/<feature>/` (the default). Also stage any files those phases
  legitimately regenerate (e.g. agent context files).
- Implement chunks and review fixes: the source, test, and doc files the chunk
  changed, plus its tasks.md checkbox updates.

Inspect `git status --porcelain` before staging, then stage **explicit paths
only** — name each file or directory you are committing (`git add <path> ...`).
Never use `git add -A`, `git add .`, or `git add -u`: the working tree may
carry changes the phase did not produce, and a checkpoint must never absorb
them. Stage paths exactly as `git status --porcelain` reports them — never
through a symlink alias (e.g. `specs` -> `dev/specs`), which `git add`
rejects.

If the status output lists changes that are clearly unrelated to the phase
(files the phase could not have touched), leave them unstaged and mention the
leftovers in your report.

If there is nothing to commit, say so and return — that is a success, not an error.

## Commit message

- Subject: imperative "what changed", scoped to the phase, e.g.
  `spec: add specification for <feature>`, `plan: add implementation plan`,
  `tasks: generate task breakdown`, or the chunk's own summary for implement
  chunks. Follow any commit conventions in the repository's AGENTS.md.
- Body: one or two lines of context at most (phase name, spec directory).
- Identify the agent per repository convention (e.g. a `Co-Authored-By:` trailer).

## Output

Report the created commit hash and subject (or the skip reason) back to the
caller so the orchestrator can include it in its phase log.
