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
2. Confirm the current branch is a feature branch. If `git rev-parse --abbrev-ref HEAD`
   returns `main`, `master`, `develop`, or `stable`, **abort without committing**
   and surface the problem to the caller — the pipeline should have created a
   feature branch (jpd extension `before_specify` hook) before any phase ran.
3. Never push. Never amend. One new commit per invocation, nothing else.

## What to stage

Stage the changes attributable to the phase that just finished:

- Preparation phases (specify / plan / critique / tasks): the feature's spec
  directory (`specs/<feature>/**`) and any files those phases legitimately
  regenerate (e.g. agent context files).
- Implement chunks and review fixes: the source, test, and doc files the chunk
  changed, plus its tasks.md checkbox updates.

Inspect `git status --porcelain` before staging. If it lists changes that are
clearly unrelated to the phase (files the phase could not have touched), stage
only the relevant paths and mention the leftovers in your report instead of
committing them blindly. When everything listed belongs to the phase, `git add -A`
is fine.

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
