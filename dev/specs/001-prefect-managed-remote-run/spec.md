# Feature Specification: Prefect-Managed Remote Infrahub Sync Run (Developer Preview)

**Feature Branch**: `001-prefect-managed-remote-run-local-dp-001`

**Created**: 2026-07-30

**Status**: Draft

**Input**: Delivery brief DB-001 v2 (`batch-developer-preview`), primary card `LOCAL-DP-001` — "Prefect-managed remote Infrahub Sync run". The brief is the sole product and scope authority for this specification.

**Traceability**: Brief `DB-001` (v2, READY, approved 2026-07-30 by Blake Ellis) / card `LOCAL-DP-001`. Requirement IDs `DBR-001`–`DBR-015` and acceptance IDs `DBA-001`–`DBA-011` are carried verbatim below and must survive into planning and tasks unchanged.

## Overview

Infrahub Sync is operated today only through a local CLI. A developer who wants to run it remotely must build an external control plane and wrap the CLI by hand. This Developer Preview answers a bounded product question through working software: can a developer run and observe a real Infrahub Sync operation remotely using the Prefect server and UI they get from a default installation?

The preview delivers: a narrow, typed Python execution surface shared by the existing CLI (`diff` lifecycle and the serial branch of `sync --no-parallel`) and a package-owned Prefect flow; an optional Prefect dependency (the base package stays Prefect-free); a default self-hosted Prefect Server with a locally served deployment; remote submission and inspection through Prefect's own REST API; and one reproducible example plus one qualified demonstration. The remote operation name `plan` maps to the existing `diff` lifecycle.

## Mandated Enabling Work (pre-implementation readiness)

These items come from the brief's readiness check of the repository at commit `9edc1bc` and are in-scope, ordered, mandated work — not optional cleanup:

- **R-1 (first committed task)**: Plain `uv sync` does not install development tooling (dev tools live in the `dev` extra), so `uv run pytest -q` fails in a fresh worktree. Update the setup and workflow commands in `AGENTS.md` from `uv sync` to `uv sync --extra dev`, committed on its own as the first task of the feature branch. Every fresh worktree/venv must use `uv sync --extra dev`.
- **R-2 (second committed task)**: The checked-in generated example is stale — `uv run infrahub-sync generate --name from-netbox --directory examples/` deterministically rewrites four committed files under `examples/netbox_to_infrahub/` (~400 lines of template drift). Run that command once and commit the regenerated files as an isolated baseline-hygiene commit, so later CLI sanity runs leave the tree clean.
- **R-3 (environment preparation, not a commit)**: The lab Infrahub 1.9.8 at `http://localhost:8000` is healthy with an `InfraDevice(name, type)` schema loaded on branch `main` and zero `InfraDevice` objects; a read-only `infrahub-sync diff --name custom-example` smoke test produced the expected five creates. No loadable `InfraDevice` schema YAML ships in the repository yet — authoring that schema file is in-scope enabling work for the example setup instructions (DBR-013, DBA-002). Verify the destination is empty (or reset a disposable one) before the demonstration. Credentials (`INFRAHUB_ADDRESS`, `INFRAHUB_API_TOKEN`) come only from the runner environment per DBR-006; if absent, re-derive on the lab host from the Infrahub container's `INFRAHUB_INITIAL_ADMIN_TOKEN` — never written into tracked files, flow parameters, or results. If the live instance or credentials are unavailable, record the live-environment ceiling rather than blocking.
- **R-4 (inherited baseline, informational)**: At `9edc1bc`: `uv run pytest -q` passes (110 passed, 3 skipped integration), `uv run invoke lint` exits 0 with pre-existing pylint `import-outside-toplevel` warnings in `infrahub_sync/potenda/__init__.py`, and `uv run invoke format` produces no diffs. Anything worse is a regression introduced by this run.
- **R-5 (inherited, do not fix)**: `tests/test_potenda_parallel.py` uses `@pytest.mark.timeout(5)` without `pytest-timeout` installed (silent no-op, unknown-mark warning). Pre-existing; do not add the dependency unless the feature work requires it.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Remote plan through Prefect (Priority: P1)

A developer installs the optional Prefect integration, starts a default self-hosted Prefect Server, and serves the packaged Infrahub Sync flow. With a known Sync configuration installed on the runner and credentials supplied through its environment, a remote caller uses Prefect's REST API to start a read-only plan and then observes the run state and Infrahub Sync logs through Prefect's API and UI.

**Why this priority**: This is the core product question of the preview — remote, observable execution of a real read-only Sync operation. Without it nothing else in the brief has value, and it is safe (non-mutating) per Constitution Principle I.

**Independent Test**: Against an empty qualified destination, submit `operation=plan` for the qualified configuration through Prefect's REST API; verify a flow-run ID is returned, the run reaches completed, the plan shows five creates, and the run's lifecycle plus Infrahub Sync log lines are visible in Prefect. (Maps to brief Scenario 1; verified by DBA-002, DBA-003, DBA-004.)

**Acceptance Scenarios**:

1. **Given** a default Prefect Server, a served Infrahub Sync deployment, a known configuration on the runner, and credentials in its environment, **When** a remote caller creates a deployment run with that configuration and `operation=plan`, **Then** Prefect returns a flow-run ID, the run becomes observable in its API and UI, and Infrahub Sync completes a real read-only comparison and writes its normal local plan artifact.
2. **Given** the same served deployment, **When** the completed plan run is inspected through Prefect's API or UI, **Then** the run lifecycle and the Infrahub Sync lifecycle log messages are visible, with no credential values anywhere in parameters, logs, or results.

---

### User Story 2 - Explicitly confirmed destination write (Priority: P2)

A remote caller who has reviewed the plan starts a synchronization by explicitly confirming writes. The flow applies the change through the same execution surface the CLI uses, Prefect records the run as completed, and the change is observable at the destination.

**Why this priority**: The confirmed write completes the vertical slice (plan → confirmed apply → converged no-change plan) that the preview must demonstrate, but it depends on the plan path existing first.

**Independent Test**: Against a reset empty qualified destination, submit `operation=sync` with `confirm_writes=true`; verify the run completes, the five expected `InfraDevice` objects exist at the destination, and a follow-up plan reports no changes. (Maps to brief Scenario 2; verified by DBA-005.)

**Acceptance Scenarios**:

1. **Given** the served deployment and a configuration with a known destination change, **When** a remote caller creates a run with `operation=sync` and `confirm_writes=true`, **Then** Infrahub Sync applies the change, Prefect records the run as completed, and the change is observable at the destination.
2. **Given** the destination has just been synchronized, **When** a follow-up `operation=plan` run executes, **Then** its result reports no changes (idempotent reconciliation, Constitution Principle II).

---

### User Story 3 - Safety refusals: unconfirmed writes and invalid configurations (Priority: P3)

A remote caller who requests a write without explicit confirmation, or who names a configuration that cannot be resolved in the server-configured directory, receives a clear failure before any source extraction or destination mutation — and the request can never be turned into an arbitrary filesystem path, command, credential, or environment override.

**Why this priority**: These are the safety and input-boundary guarantees (Constitution Principles I and VI) that make it acceptable to expose Sync remotely at all; they gate the write path delivered in Story 2.

**Independent Test**: Submit `operation=sync` with `confirm_writes` absent/false and verify failure before either adapter loads with the destination unchanged; submit unknown, path-like, and command-like `sync_name` values and verify refusal without reading outside the configured directory or starting a subprocess. (Maps to brief Scenarios 3 and 4; verified by DBA-006, DBA-007, DBA-008, DBA-010.)

**Acceptance Scenarios**:

1. **Given** a remote caller requests `operation=sync`, **When** `confirm_writes` is absent or false, **Then** the flow fails before source extraction or destination mutation with a clear message explaining how to confirm the write.
2. **Given** the runner is configured with an allowed configuration directory, **When** a request names a configuration that cannot be resolved there, **Then** the flow fails clearly without interpreting the value as an arbitrary filesystem path or command argument.
3. **Given** any validation or execution failure, **When** the flow ends, **Then** Prefect records the run as failed with a specific, human-readable, secret-redacted cause and no successful result is produced.

---

### User Story 4 - Base installation stays Prefect-free (Priority: P4)

A user of the existing CLI who does not install the optional extra sees no change: the base package installs and imports without Prefect, ordinary CLI use neither imports, starts, nor contacts Prefect, and the touched `diff` lifecycle and `sync --no-parallel` branch behave exactly as before.

**Why this priority**: It protects every existing user from the preview. It is listed after the demonstration stories because it constrains rather than delivers the new capability, but it must hold from the first refactoring commit.

**Independent Test**: In a clean environment without the optional extra, import the package and run the CLI sanity commands with Prefect unavailable; run the existing targeted tests for `diff` and `sync --no-parallel` through the shared execution surface; compare CLI `diff` and remote `plan` outputs on reset copies of the qualified fixture. (Verified by DBA-001, DBA-009.)

**Acceptance Scenarios**:

1. **Given** a clean base installation without the optional extra, **When** the CLI is imported and exercised, **Then** it runs without importing or contacting Prefect.
2. **Given** reset copies of the qualified fixture, **When** CLI `diff` and remote `operation=plan` both run, **Then** they produce the same status, changed flag, action summary, and canonical plan fingerprint.

---

### User Story 5 - Reproducible example (Priority: P5)

A developer unfamiliar with the implementation follows one shipped example README to install the optional integration, author/load the destination schema, configure the runner, start Prefect, serve the flow, remotely invoke a plan, and inspect the run — without reading package source.

**Why this priority**: The example is how the preview transfers to other developers; it packages the earlier stories but cannot exist before them.

**Independent Test**: A clean-context walkthrough following only the example README reproduces the qualified plan demonstration. (Verified by DBA-002, DBA-011.)

**Acceptance Scenarios**:

1. **Given** the example README and a machine meeting its stated prerequisites, **When** a developer follows only the documented commands, **Then** the default Prefect Server and locally served flow start without any external database or worker service, and the qualified plan run reproduces.
2. **Given** the example content, **When** it is inspected, **Then** it contains setup instructions, remote request examples, and no real credentials.

### Edge Cases

- `operation` values other than `plan` and `sync` fail Prefect parameter validation without starting the flow body.
- An unreadable or invalid configuration fails before either adapter loads and names the configuration without printing its contents.
- A missing credential fails clearly while leaving the credential value absent from Prefect-visible state and logs.
- A Prefect Server outage prevents remote submission but does not change ordinary local CLI behavior.
- Concurrent requests retain Prefect's default served-run behavior. The preview makes no overlap or same-configuration concurrency guarantee, and the qualified demonstration must not issue concurrent destination-writing runs.
- Failure after destination writes begin retains today's Sync behavior; the preview makes no durable-recovery claim.

## Requirements *(mandatory)*

### Functional Requirements

Requirement IDs are the brief's own; origin (QUOTED/DERIVED) and source references are preserved from DB-001.

- **DBR-001**: Provide a remotely runnable Prefect deployment for a real Infrahub Sync operation. *(QUOTED — LOCAL-DP-001 SRC-DP-001, SRC-DP-002)*
- **DBR-002**: Use the default self-hosted Prefect experience for the preview: local server database, built-in UI, and a locally served deployment. *(QUOTED — LOCAL-DP-001 SRC-DP-003)*
- **DBR-003**: Expose `plan` and `sync` through typed flow parameters: `sync_name`, `operation`, `confirm_writes`, and optional `branch`; `plan` is the default. *(DERIVED — LOCAL-DP-001 SRC-DP-002, SRC-DP-003; minimum input needed to exercise the two approved operations safely)*
- **DBR-004**: Refuse `sync` before extraction or mutation unless `confirm_writes=true`. *(DERIVED — LOCAL-DP-001 SRC-DP-A02; AGENTS.md read-only and approval policy)*
- **DBR-005**: Resolve `sync_name` only within one server-configured directory; do not accept arbitrary paths, CLI fragments, credentials, or environment overrides from remote parameters. *(DERIVED — LOCAL-DP-001 SRC-DP-003; the remote API must not become command or secret injection)*
- **DBR-006**: Keep credentials and system endpoints in the runner environment, never in Prefect parameters or returned results. *(DERIVED — LOCAL-DP-001 SRC-DP-003; AGENTS.md secret-handling policy)*
- **DBR-007**: Introduce a narrow typed Python execution surface used by the CLI `diff` lifecycle, the serial branch selected by `sync --no-parallel`, and the Prefect flow; remote `operation=plan` maps to the existing `diff` lifecycle. *(QUOTED — LOCAL-DP-001 SRC-DP-004, SRC-DP-005)*
- **DBR-008**: The Prefect flow calls the Python execution surface directly and does not invoke the CLI as a subprocess. *(QUOTED — LOCAL-DP-001 SRC-DP-004, SRC-DP-005)*
- **DBR-009**: Preserve existing user-visible CLI behavior for the touched `diff` lifecycle and `sync --no-parallel` branch. *(DERIVED — DBR-007; a shared seam must not turn the preview into an unrelated CLI behavior change)*
- **DBR-010**: Keep Prefect optional: base installation and ordinary CLI use do not import, start, or contact Prefect. *(QUOTED — LOCAL-DP-001 SRC-DP-004; accepted architecture boundary)*
- **DBR-011**: Use Prefect's deployment and flow-run APIs as the remote API for the preview; do not add a custom product HTTP service. *(QUOTED — LOCAL-DP-001 SRC-DP-006)*
- **DBR-012**: Forward Infrahub Sync lifecycle logs into the Prefect flow-run log without exposing credentials. *(DERIVED — LOCAL-DP-001 SRC-DP-A01; remote observability is not useful without the underlying run output)*
- **DBR-013**: Ship one reproducible example that installs, configures, serves, remotely invokes, and inspects the preview. *(QUOTED — LOCAL-DP-001 SRC-DP-004, SRC-DP-A03)*
- **DBR-014**: Keep the API façade, reviewed-plan workflow, stage tasks, workers, triggers, and production profile outside this outcome and recorded only in `backlog.md`. *(QUOTED — LOCAL-DP-001 SRC-DP-007)*
- **DBR-015**: Return the fixed `RunResult` fields defined under Key Entities; on validation or execution failure, raise a specific sanitized exception and return no successful result. *(DERIVED — DBR-007 requires a typed structured result shared by the CLI seam and Prefect flow)*

### Key Entities

- **Execution request (shared contract, owned by this brief)**: The minimal input accepted by the shared execution surface and the Prefect flow. Fixed to `sync_name` (logical configuration name), `operation` (`plan` or `sync`; `plan` is the default), `confirm_writes` (explicit write gate), and optional Infrahub `branch`. Remote parameters never carry paths, CLI fragments, credentials, or environment overrides. Future API or orchestration briefs must extend this contract rather than create a second run lifecycle.
- **RunResult (immutable success result)**: One immutable result returned by the shared execution surface, with exactly these fields:

  | Field | Type | Meaning |
  |---|---|---|
  | `sync_name` | `str` | Resolved logical configuration name |
  | `operation` | `Literal["plan", "sync"]` | Requested remote operation |
  | `run_id` | `str` | Infrahub Sync cache/run identifier |
  | `status` | `Literal["planned", "applied", "no-change"]` | Terminal domain outcome; a plan with changes is `planned`, a sync that writes is `applied`, and either operation with no changes is `no-change` |
  | `changed` | `bool` | Whether the plan contained destination changes |
  | `summary` | `dict[Literal["create", "update", "delete"], int]` | Flat per-action counts; all three keys are always present, including zero counts |
  | `artifact_path` | `str` | Absolute runner-local path containing the ordinary Sync artifacts |

- **Failure contract**: Request or configuration failure raises `RunValidationError`; adapter or engine failure raises `RunExecutionError`. Both preserve a specific human-readable cause while redacting configured secret values. A failure produces no successful `RunResult`, and Prefect records the flow as failed.
- **Sync configuration**: An existing Infrahub Sync project (YAML config plus generated models) installed manually on the runner and selected by logical name from one server-configured directory.
- **Qualified demonstration fixture**: `examples/custom_adapter` — its local JSON MockDB source (`custom_adapter_src/mock_db.json`), the five named device records already in that file, and a live Infrahub destination whose schema contains `InfraDevice` with `name` and `type`. Against an empty qualified destination: the expected first plan is five creates, the confirmed sync creates those five devices, and the next plan has no changes.

### Acceptance Criteria (verbatim traceability from DB-001)

| ID | Acceptance criterion | Trace | Verification evidence expected |
|---|---|---|---|
| DBA-001 | From a clean base installation without the optional extra, the existing CLI imports and runs without importing or contacting Prefect. | DBR-010 | A clean-environment import and CLI sanity test with Prefect unavailable |
| DBA-002 | With the optional extra installed, a default Prefect Server and locally served package flow start using the documented commands and no external database or worker service. | DBR-001, DBR-002 | Reproduction transcript and healthy server/deployment evidence |
| DBA-003 | Against an empty qualified `examples/custom_adapter` destination, a remote API request for `operation=plan` returns a Prefect flow-run ID, reaches completed, and produces a plan of five `InfraDevice` creates. | LOCAL-DP-001 SRC-DP-A01; DBR-001, DBR-003 | End-to-end API call, flow-run record, and canonical plan summary/artifact |
| DBA-004 | The Prefect API or UI shows the run lifecycle and the Infrahub Sync lifecycle log messages for the qualified plan. | LOCAL-DP-001 SRC-DP-A01; DBR-012 | API/UI inspection of one completed run |
| DBA-005 | Against the same reset fixture, `operation=sync` with `confirm_writes=true` reaches completed, creates the five expected devices, and is followed by a plan containing no changes. | LOCAL-DP-001 SRC-DP-A02; DBR-003, DBR-004 | End-to-end API call, exact destination observation, and no-change follow-up plan |
| DBA-006 | A request for `operation=sync` without confirmation fails before either adapter loads and leaves the destination unchanged. | DBR-004 | Negative test with adapter-load spies plus destination observation |
| DBA-007 | Unknown configuration names and path-like or command-like values are refused without reading outside the configured directory or starting a subprocess. | DBR-005, DBR-008 | Parametrized negative tests |
| DBA-008 | Seeded canary credentials do not appear in flow parameters, results, Prefect-visible logs, or example request bodies. | DBR-006, DBR-012 | Canary scan over captured output and Prefect run metadata |
| DBA-009 | Existing targeted tests for CLI `diff` and `sync --no-parallel` pass through the shared execution surface; on reset copies of the qualified fixture, CLI `diff` and remote `operation=plan` produce the same status, changed flag, action summary, and canonical plan fingerprint. | DBR-007, DBR-009 | Targeted CLI tests and a paired CLI-versus-flow plan comparison |
| DBA-010 | A successful plan and sync each expose every defined `RunResult` field with the specified meaning; a validation or execution fault yields a failed Prefect flow with a sanitized specific error and no successful result. | DBR-015 | Result-schema assertions plus one validation-failure and one execution-failure test |
| DBA-011 | A developer unfamiliar with the implementation can reproduce the plan demonstration from the example README without reading package source. | DBR-013 | Clean-context walkthrough following only the README |

## Scope Boundaries

### In Scope

- A narrow reusable execution surface for the lifecycle behind the existing CLI `diff` command and the serial branch of the existing CLI `sync` command; the remote operation name `plan` maps to the `diff` lifecycle.
- The CLI `diff` command and the serial branch selected by `sync --no-parallel` calling that execution surface without user-visible behavior changes; the existing parallel sync branch remains untouched.
- An optional Prefect package dependency; the base package remains installable and importable without Prefect.
- A package-owned Prefect flow that calls the execution surface directly rather than spawning or wrapping the CLI.
- A default self-hosted Prefect Server using its default local database and UI; a locally served Prefect deployment; no work pool or separate worker service.
- Direct remote submission and inspection through Prefect's REST API.
- Manually installed Sync configurations selected by logical name from one server-configured directory.
- Credentials and endpoints supplied to the runner through environment variables.
- Flow parameters for `sync_name`, `operation`, `confirm_writes`, and an optional Infrahub branch; `plan` as the default operation and an explicit confirmation gate for `sync`.
- Infrahub Sync lifecycle output available in the Prefect run log.
- One example containing setup instructions and remote request examples, including authoring the loadable `InfraDevice(name, type)` schema file (per R-3).
- One qualified demonstration using `examples/custom_adapter` as defined under Key Entities.
- Mandated enabling work R-1 and R-2 as the first two commits.

### Out of Scope

- A custom FastAPI service or Sync-specific REST resource model; see backlog B-001.
- Remote saved-plan browsing, approval, and apply-by-run-ID; see backlog B-002.
- Prefect tasks for each extract, plan, and apply stage; see backlog B-003.
- Work pools, separate workers, retries, crash recovery, PostgreSQL, Redis, object storage, or Kubernetes; see backlog B-004 and B-007.
- Schedules, overlap policies, event triggers, and notifications; see backlog B-005 and B-006.
- Production authentication, authorization, audit, high availability, backup, or upgrade guarantees; see backlog B-007.
- A custom operator UI.
- Configuration registration or versioning.
- Adding Prefect to the base dependency set.
- Supporting multiple Prefect major versions.
- Moving the current parallel CLI sync branch behind the new execution surface.
- Refactoring parallel, incremental, apply-plan, or recovery paths not required by the preview's serial `plan` and `sync` operations.

### Constraints

- Prefect is the selected orchestration technology for this Developer Preview; a queue-versus-Prefect comparison is not part of delivery.
- The execution engine and adapters remain Prefect-independent.
- The integration is packaged capability; `examples/` contains only consumption and demonstration material.
- The default self-hosted Prefect Server is for a trusted development environment and must not be documented as safe for public internet exposure.
- The implementation follows the repository workflow (format → lint → CLI sanity) and preserves required CLI sanity behavior.
- Prefect 3.7.2 is the fixed external dependency version for the preview.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A remote caller can submit a plan for the qualified configuration through the orchestration REST API and receive a run identifier immediately; the run reaches a completed state and reports exactly five creates, zero updates, and zero deletes (DBA-003).
- **SC-002**: 100% of the run lifecycle transitions and the Infrahub Sync lifecycle log lines for a qualified plan are observable remotely without shell access to the runner (DBA-004).
- **SC-003**: A confirmed remote synchronization against the empty qualified destination results in exactly five `InfraDevice` objects at the destination, and the immediately following plan reports zero changes (DBA-005).
- **SC-004**: 100% of write requests lacking explicit confirmation, and 100% of unknown/path-like/command-like configuration names in the negative-test set, are refused before any source extraction, destination mutation, out-of-directory read, or subprocess start (DBA-006, DBA-007).
- **SC-005**: Zero occurrences of seeded canary credential values in flow parameters, results, remotely visible logs, or example request bodies (DBA-008).
- **SC-006**: With the optional extra absent, base installation, package import, and the existing CLI sanity commands all succeed without importing or contacting the orchestrator; the inherited test baseline (110 passed, 3 skipped at `9edc1bc`) does not regress (DBA-001, R-4).
- **SC-007**: On reset copies of the qualified fixture, the local CLI plan and the remote plan agree exactly on status, changed flag, per-action summary, and canonical plan fingerprint (DBA-009).
- **SC-008**: Every successful run exposes all seven `RunResult` fields with their specified meanings, and every induced validation or execution fault produces a failed remote run with a specific sanitized error and no successful result (DBA-010).
- **SC-009**: A developer with no prior exposure to the implementation reproduces the plan demonstration end-to-end using only the example README, with zero references to package source (DBA-011).

## Assumptions

Documented defaults and inherited facts; none require interactive clarification. The brief records "Unresolved questions: None."

- **Brief-owned assumption**: Direct use of Prefect's REST API is acceptable for a developer-facing preview; if wrong, a Sync-shaped façade must be promoted from backlog B-001 before delivery.
- **Brief-owned assumption**: The existing `examples/custom_adapter` fixture can run against a lab Infrahub schema containing `InfraDevice(name, type)`. If wrong, the example must be repaired within this brief without changing its five-device outcome, or the brief returns for intake rather than selecting an unspecified substitute. (Repository check on 2026-07-30 confirms the fixture exists with its MockDB source at `examples/custom_adapter/custom_adapter_src/mock_db.json`.)
- **Brief-owned assumption**: Refactoring the CLI `diff` lifecycle and the serial branch selected by `sync --no-parallel` behind one execution surface is small enough for the preview; if wrong, the brief returns for intake rather than silently falling back to an example-only subprocess wrapper.
- **Environment**: Lab facts per R-3 (Infrahub 1.9.8 at `http://localhost:8000`, `InfraDevice(name, type)` schema loaded on `main` with deprecation warnings for `display_labels` and `default_filter`, zero existing `InfraDevice` objects). If the live instance or credentials are unavailable at demonstration time, the run records the live-environment ceiling rather than blocking.
- **Informed default (documented, not a clarification)**: "Before either adapter loads" (DBA-006, edge cases) is interpreted as: validation of `confirm_writes`, `operation`, and `sync_name` resolution completes before any source or destination adapter object is constructed or any network connection is attempted.
- **Informed default (documented, not a clarification)**: The "canonical plan fingerprint" (DBA-009) is a deterministic digest over the ordered plan actions in the run artifact, defined once during planning and used identically for the CLI and remote comparison.
- **Informed default (documented, not a clarification)**: The server-configured configuration directory (DBR-005) is supplied through the runner's environment/serve-time setup — not through remote parameters — consistent with DBR-006; its exact configuration mechanism is a planning decision.
- **Dependency (satisfied)**: Existing CLI `diff` and serial `sync --no-parallel` behavior in `infrahub_sync/cli.py`, and the `Potenda` engine's load/diff/write-plan/sync operations in `infrahub_sync/potenda/__init__.py`, provide everything the shared execution surface must move behind the seam.
- **Dependency (available)**: Prefect 3.7.2 — flow, served deployment, REST run creation, state, and logging behavior; VAL-6 and VAL-12 already exercised Prefect locally (supporting evidence only).
- **Approved decisions carried forward**: Build a thin package integration plus one example (no example-only CLI wrapper, no engine rewrite around Prefect); record speculative follow-ons in `backlog.md` instead of creating DB-002 now.

## Completion Conditions (from DB-001)

- Every requirement (DBR-001–DBR-015) and acceptance criterion (DBA-001–DBA-011) above has inspectable passing evidence.
- Required format, lint, type, targeted test, and CLI sanity checks pass per repository governance, with any pre-existing baseline failure (R-4, R-5) reported rather than hidden.
- The example contains no real credentials and reproduces the qualified plan run.
- No backlog item (B-001–B-007) is implemented as part of this outcome.
