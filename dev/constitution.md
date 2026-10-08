<!--
SYNC IMPACT REPORT
Version change: 1.0.1 → 2.0.0 (MAJOR)
Modified principles:
  - I. Read-Only / Dry-Run by Default: aligned with the service-first V3 CLI
  - II. Sync Idempotency & Safety: added reviewed-plan and uncertain-write rules
  - III. Adapter Symmetry & Pattern Consistency: aligned with registered runtime-schema execution
  - IV. Type Safety & Explicit Contracts: aligned with Python 3.10–3.13 profiles
  - V. Test Discipline: aligned with the repository's tiered Invoke tasks
Modified sections:
  - Development Workflow & Quality Gates: current setup, lint, CLI, docs, and changelog gates
Added sections: None
Removed sections: None
Templates requiring updates:
  - .specify/templates/plan-template.md ✅ reviewed, no change needed
  - .specify/templates/spec-template.md ✅ reviewed, no change needed
  - .specify/templates/tasks-template.md ✅ reviewed, no change needed
Follow-up TODOs: None
-->

# Infrahub Sync Constitution

`infrahub-sync` synchronizes infrastructure data through a service-first product boundary.
The Sync HTTP API owns registered configurations, runs, plans, artifacts, and writes; the Python
client and CLI are clients of that API. A direct Prefect integration exists for read-only planning.
Because writes affect live systems of record, safety, reproducibility, secret containment, and
adapter consistency take precedence over convenience and throughput.

## Core Principles

### I. Read-Only and Reviewed by Default

The non-mutating path is the default path, and applying destination changes is always deliberate.

- Configuration inspection, `runs plan`, and `diff` MUST remain safe and non-applying.
- `sync` and `apply` MUST require explicit user instruction, authenticated service admission,
  confirmed target configuration, and the write approvals defined by the execution contract.
- `apply` MUST operate on the immutable saved plan and checksum the operator reviewed.
- A mutating action MUST NOT occur as an implicit side effect of inspection, validation, or plan
  rendering.
- New mutating behavior MUST ship behind an explicit operation or flag, never an implicit default.

**Rationale:** The safe path must be the easy path. Reviewable, explicit writes prevent an ordinary
inspection command from changing infrastructure systems of record.

### II. Idempotency, Ownership, and Honest Outcomes

Reconciliation MUST be safe to repeat, and the product MUST not claim more certainty than the
destination evidence supports.

- Re-running a sync against an already converged destination MUST produce no spurious changes.
- Every destination write MUST pass the applicable configuration guard, plan verification, and
  write-ownership checks before dispatch.
- Timeouts, authentication failures, rate limits, empty pages, and pagination MUST be handled
  explicitly rather than hidden or silently skipped.
- Confirmed, failed, not-attempted, and uncertain writes MUST remain distinguishable in run evidence.
- An uncertain write MUST stop and require the documented reconciliation procedure; MVP behavior
  MUST NOT retry an operation whose repeat safety has not been proven.
- Plans and run state MUST be durable independently of the initiating client process.

**Rationale:** Idempotency prevents duplicates; explicit ownership prevents competing writers; and
honest outcome classification prevents an ambiguous partial write from being mistaken for safety.

### III. One Registered Execution Contract

Registered execution MUST use one configuration, schema, plan, and lifecycle contract across the
HTTP API, Python client, CLI, worker, and adapters.

- The Sync HTTP API is the product boundary for registered V3 execution. The CLI and Python client
  MUST NOT implement a second in-process write path.
- Registered configurations MUST be versioned, strictly validated, and resolved to immutable
  versions for runs.
- Runtime models and write order MUST derive from the destination schema, adapter capabilities,
  and configuration mapping rather than checked-in generated models.
- Built-in and extension adapters MUST declare and honor the shared adapter contract. Unsupported
  source/destination roles MUST be refused during admission.
- Internal generation and local filesystem plugins MAY support adapter development, but MUST NOT
  silently bypass registered-package admission.
- Direct Prefect execution MUST remain read-only unless a future decision explicitly revises this
  constitution and the service boundary.

**Rationale:** One execution contract prevents API, CLI, worker, and adapter behavior from drifting
into incompatible products.

### IV. Type Safety and Explicit Boundaries

The type system and validation boundaries enforce correctness where data crosses processes and
external systems.

- New or changed code MUST carry explicit types; public functions and classes require concise
  docstrings.
- The codebase MUST remain clean under `ty` with no `[[tool.ty.overrides]]` blocks. A targeted
  `# ty: ignore[<rule>]` requires a short call-site rationale or TODO.
- Code supporting the full service profile MUST work on Python 3.11–3.13. The documented direct
  Prefect profile MUST remain valid on Python 3.10 with its declared service exclusions.
- Public request, response, configuration, plan, and artifact shapes MUST be explicitly validated.
- Raise specific exceptions and preserve typed lifecycle errors. Broad exception handling is
  permitted only at a process boundary that sanitizes and translates otherwise unhandled failures.

**Rationale:** Explicit contracts catch integration mistakes before they reach a live destination
and keep failures meaningful across service and worker boundaries.

### V. Test Discipline and Evidence

Features and fixes ship with proportionate tests and retained evidence; a claim without a passing
check is not complete.

- Add focused unit tests for changed behavior and boundary cases. Prefer parametrization over loops.
- Network and live-system tests MUST be opt-in and assigned to the documented integration tier.
- Service, worker, storage, and adapter changes MUST test their failure boundary, including auth,
  timeout, pagination, empty data, and persistence behavior where applicable.
- Write-safety changes MUST cover the refusal path before the success path is accepted.
- Tests MUST be atomic and single-purpose. The offline unit tier is
  `uv run invoke tests.tests-unit`.
- Candidate or release claims MUST identify the exact revision and immutable artifact tested.

**Rationale:** External APIs and distributed execution introduce failures that happy-path unit tests
cannot reveal. Tiered, evidence-producing tests keep those failures reproducible.

### VI. Secrets and Untrusted Input Stay Contained

Credentials and private run data MUST never cross a public boundary.

- Configuration packages MUST contain credential references, never credential values.
- Credentials MUST come from the configured runtime provider and MUST NOT appear in logs,
  tracebacks, validation reports, plans, results, public artifacts, or release evidence.
- Every failure boundary MUST sanitize exception messages and causes before publication.
- Internal run bundles MAY preserve byte-stable private state only while remaining inaccessible to
  supported clients and public diagnostics.
- External API responses, configuration archives, plugin references, paths, and serialized values
  MUST be treated as untrusted input and validated before use.
- Examples and documentation MUST use authentic but sanitized values.

**Rationale:** Sync holds credentials and data for multiple systems of record. A leak in one output
surface can compromise every connected system.

### VII. Small, Reversible, Documented Changes

Prefer the smallest solution that satisfies an approved requirement and matches established
patterns.

- YAGNI applies: a new abstraction requires a demonstrated use in the requested scope.
- New dependencies MUST be justified and pinned according to repository policy.
- Generated artifacts MUST be regenerated from their source rather than hand-edited.
- Refactors and behavior changes SHOULD be separate unless the behavior cannot be changed safely
  without the focused refactor.
- User-visible behavior, configuration, deployment, and adapter changes MUST update documentation
  and add the required towncrier fragment in the same change.
- Commits MUST be small, scoped, reversible, and attributable to their human and agent authors.

**Rationale:** The project already spans clients, workers, stores, and many adapters. Small changes
with durable rationale are easier to review, qualify, and reverse.

## Security and Reliability Standards

### Security Requirements

- Mutating service actions require the configured authentication and authorization boundary.
- Credentials and raw private payloads never appear in public output.
- Configuration archives, filesystem paths, adapter plugins, and serialized artifacts are validated
  before use.
- Target servers, configuration versions, and reviewed plan fingerprints are explicit for writes.

### Reliability and Performance Requirements

- Adapters respect pagination, timeouts, and rate limits and avoid unbounded fetches.
- Run and artifact persistence failures are reported without hiding the original lifecycle failure.
- Structured logs include safe correlation fields such as run ID, operation, stage, and outcome.
- Performance changes require a recorded baseline and target; optimization MUST NOT weaken plan
  equivalence, write ownership, or secret containment.

## Development Workflow and Quality Gates

### Environment Setup

Use Python 3.11–3.13 for the full development profile:

```bash
uv sync --extra dev --extra prefect --extra service
```

On Python 3.10, use the direct Prefect profile; the Sync service is excluded:

```bash
uv sync --python 3.10 --extra dev --extra prefect
```

### Code Quality Gates

Run these aggregate gates in order before committing:

```bash
uv run invoke format
uv run invoke lint
```

`invoke lint` runs rumdl, Ruff, Pylint, yamllint, and `ty`. Fix underlying diagnostics rather
than adding global type-checker overrides or broad exclusions.

### CLI Sanity

After CLI, service-client, configuration, or packaging changes, verify:

```bash
uv run infrahub-sync --help
uv run infrahub-sync configs --help
uv run infrahub-sync runs --help
```

### Documentation

User-visible changes MUST update `docs/` and sanitized examples in the same change. When applicable,
run:

```bash
uv run invoke docs.generate
uv run invoke docs.docusaurus
```

Markdown and MDX MUST remain clean under the repository's rumdl configuration.

### Git and Release Workflow

- Do not force-push shared branches or amend to hide follow-up fixes.
- Every normal pull request adds a valid `changelog/<id>.<type>.md` fragment or carries the approved
  `ci/skip-changelog` label.
- Every normal pull request into `main` carries exactly one release-intent label:
  `changes/major`, `changes/minor`, or `changes/patch`.
- Versions, `CHANGELOG.md`, and release tags are created by the documented release workflow, never
  edited manually.

## Governance

This constitution is the authoritative development-policy reference for `infrahub-sync`. It
supersedes historical specifications and informal practices when they conflict.

- **Compliance:** Specifications, plans, pull requests, and reviews MUST verify the applicable
  principles and repository quality gates.
- **Amendments:** A change requires written rationale, maintainer approval, migration guidance when
  existing behavior is affected, and a constitution version increment.
- **Versioning:** MAJOR removes or redefines a principle; MINOR adds or materially expands one;
  PATCH clarifies wording without changing obligations.
- **Runtime guidance:** `AGENTS.md`, `dev/`, and the current documentation explain how to apply these
  principles. If they conflict, this constitution governs until the conflict is corrected.

**Version**: 2.0.0 | **Ratified**: 2026-06-22 | **Last Amended**: 2026-10-08
