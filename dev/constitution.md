<!--
SYNC IMPACT REPORT
Version change: 0.0.0 (unfilled template) → 1.0.0
Modified principles: N/A (initial constitution)
Added sections:
  - Core Principles (I–VII)
  - Security & Performance Standards
  - Development Workflow & Quality Gates
  - Governance
Removed sections: None
Templates requiring updates:
  - .specify/templates/plan-template.md ✅ reviewed, no change needed
  - .specify/templates/spec-template.md ✅ reviewed, no change needed
  - .specify/templates/tasks-template.md ✅ reviewed, no change needed
Follow-up TODOs: None
-->

# Infrahub Sync Constitution

`infrahub-sync` synchronizes data between infrastructure sources and destinations
(Infrahub, NetBox, Nautobot, ACI, Prometheus, and others) through per-system adapters
and a core sync engine. Because every `sync` writes to a live system of record, the
principles below put safety, reproducibility, and connector consistency ahead of speed.

## Core Principles

### I. Read-Only / Dry-Run by Default

The non-mutating path is the default path, and applying changes is always a deliberate act.

- `list`, `diff`, and `generate` are non-applying and MUST stay safe to run at any time,
  against any environment, without approval.
- `sync` mutates a destination system and MUST require explicit user instruction,
  confirmed target servers, and human approval. It MUST NOT run as an implicit side
  effect of another command.
- A `diff` SHOULD precede a `sync`: surface what would change before changing it.
- New mutating behavior MUST ship behind explicit flags, never as implicit defaults.

**Rationale:** A sync writes to infrastructure systems of record. Making the safe path the
easy path — and every destructive action a reviewed choice — is what prevents an accidental
command from rewriting production data.

### II. Sync Idempotency & Safety

A sync reconciles a source into a destination, and reconciliation MUST be safe to re-run.

- Re-running `sync` on an already-converged source → destination MUST produce no spurious
  changes (idempotent).
- Operations MUST handle timeouts, authentication failures (401/403), rate limits, empty
  pages, and pagination explicitly — never crash opaquely or silently skip records.
- Partial failures MUST be surfaced clearly; the destination MUST NOT be left in an
  ambiguous state without the user being told.
- Favor operations that can be retried without compounding their effect.

**Rationale:** Syncs run repeatedly and over unreliable networks. Idempotency and explicit
error handling are what prevent duplicate objects, silent data loss, and corruption of the
destination system of record.

### III. Adapter Symmetry & Pattern Consistency

Adapters are the primary extension point; every connector MUST honor the same contract.

- A new adapter MUST live in `infrahub_sync/adapters/<name>.py` and follow the existing
  adapter patterns rather than inventing new structure.
- It MUST provide `list` and `diff` pathways before `sync` is enabled.
- It MUST ship a connection config schema and a sanitized example under `examples/`.
- It MUST document required environment variables and expected error cases, and add a page
  under `docs/docs/adapters/`.
- `list` / `diff` / `generate` / `sync` MUST flow through the core sync engine (`potenda`);
  no ad-hoc per-adapter sync logic that bypasses it.

**Rationale:** Consistent adapters keep the CLI predictable, make each new connector
reviewable against a known shape, and guarantee a read-only pathway exists before any
write path is exposed.

### IV. Type Safety & Explicit Contracts

The type system enforces correctness at the boundaries where data crosses systems.

- New or changed code MUST carry explicit type hints; public functions and classes get
  concise docstrings.
- Use modern syntax — `str | None` over `Optional[str]`.
- The codebase MUST stay clean under `ty` with no `[[tool.ty.overrides]]` blocks in
  `pyproject.toml`. Do not reintroduce overrides to mask errors — fix the underlying issue,
  or use a targeted `# ty: ignore[<rule>]` with a short TODO at the call site.
- Raise specific exceptions; a broad `except Exception:` is prohibited in touched code.

**Rationale:** Explicit types and narrow exceptions catch integration mistakes — wrong field
shapes, missing data, unhandled API errors — before they reach a live system, and they keep
adapters self-documenting.

### V. Test Discipline

Features and fixes ship with tests at the right level, written alongside the change — not deferred.

- Add unit tests for `utils` and adapter edge cases: timeouts, 401/403, empty pages, pagination.
- Prefer parametrized tests over loops for config parsing and adapter variants.
- Mark network/integration tests opt-in (e.g. `-m integration`); they MAY require running servers.
- Tests MUST be atomic and single-purpose. Run `uv run pytest -q`.

**Rationale:** Adapters touch many external APIs with brittle edge cases. Tests at the
boundary are the cheapest place to catch auth, pagination, and empty-response bugs — long
before a sync hits production.

### VI. Security, Secrets & Input Boundaries

Security is enforced at the boundary, and secrets never leak.

- Credentials MUST come from environment variables or a secret manager — never committed,
  printed, or logged.
- Never print or guess secrets; tracebacks and structured logs MUST NOT contain credentials.
- Example configs MUST be authentic but sanitized — no real tokens, internal hostnames as
  placeholders.
- Treat external input (API responses) defensively and validate it; error messages MUST NOT
  leak internal details.

**Rationale:** `infrahub-sync` holds credentials for multiple systems of record. A single
leaked token or logged secret is a cross-system breach, so secret hygiene is non-negotiable.

### VII. Simplicity & Maintainability

Prefer the simplest solution that works and matches the patterns already in the codebase.

- YAGNI: build what the task needs, not speculative abstraction. A new abstraction needs
  at least two real callers.
- New dependencies MUST be justified.
- Generated code (the Python that `generate` produces from YAML configs) MUST be regenerated
  from its YAML source, never hand-edited.
- Keep commits small and scoped; do not mix large refactors with behavior changes.

**Rationale:** A connector library accretes complexity quickly. Keeping each change small,
pattern-aligned, and dependency-light is what keeps the engine and adapters reviewable and
reversible.

## Security & Performance Standards

### Security Requirements

- Credentials only via environment variables or a secret manager; no secrets in code, logs,
  tracebacks, or example configs.
- Default to read-only; the mutating `sync` requires explicit approval and confirmed targets.
- Handle authentication failures (401/403) and authorization boundaries explicitly.

### Performance & Reliability Standards

- Respect pagination and rate limits on every adapter; avoid unbounded fetches.
- Handle timeouts and transient network errors with clear, retryable behavior.
- Log object counts and endpoints for observability — never secrets.

## Development Workflow & Quality Gates

### Code Quality Gates

Run in order before committing; all code MUST pass these before merge:

```bash
uv sync
uv run invoke format
uv run invoke lint   # ruff → pylint → yamllint → ty
```

New code is Ruff-clean and typed where touched. `ty` MUST exit clean with no overrides.

### CLI Sanity

After changes, verify the CLI still behaves:

```bash
uv run infrahub-sync --help
uv run infrahub-sync list --directory examples/
uv run infrahub-sync generate --name from-netbox --directory examples/
```

### Logging

Use `structlog` for structured logging — never `print`. Include context (endpoints, object
counts, request IDs) but never secrets.

### Documentation

User-visible changes (CLI flags, config keys, adapters) MUST update `docs/` in the same
change. Generate CLI docs with `uv run invoke docs.generate`; build the site with
`uv run invoke docs.docusaurus`; lint Markdown/MDX with `markdownlint-cli2`. "Update later"
is not acceptable.

### Git Workflow

- Do not force-push shared branches; use follow-up commits rather than amending to hide fixes.
- Small, scoped, reversible commits; imperative subject line, rationale in the PR body.
- Apply PR labels (`bugs`, `breaking`, `enhancements`, `features`; default `enhancements`).

## Governance

This constitution is the authoritative reference for development standards in the
`infrahub-sync` project. It supersedes informal practices and ad-hoc decisions.

- **Compliance:** All pull requests and reviews MUST verify adherence to these principles.
  Reviewers SHOULD reference principle numbers when flagging an issue (e.g. "Principle I
  violation — `sync` runs without approval").
- **Amendments:** Changes require (1) a written proposal with rationale, (2) maintainer
  review and approval, (3) a migration plan when existing code or config is affected, and
  (4) a version increment per the scheme below.
- **Versioning:** This constitution carries its own semantic version, independent of the
  `infrahub-sync` package version.
  - **MAJOR:** Principle removal or redefinition, or a backward-incompatible governance change.
  - **MINOR:** A new principle or materially expanded guidance.
  - **PATCH:** Clarifications, wording fixes, and non-semantic refinements.
- **Runtime guidance:** Day-to-day standards live in `AGENTS.md` and `dev/` (guides,
  guidelines, knowledge, and ADRs); this constitution sets the principles those documents
  implement. Where they appear to conflict, the constitution governs.

**Version**: 1.0.0 | **Ratified**: 2026-06-22 | **Last Amended**: 2026-06-22
