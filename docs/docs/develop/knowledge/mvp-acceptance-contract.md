# MVP acceptance contract

**Contract version:** 1

This document is the normative, version-controlled definition of the Infrahub Sync MVP boundary.
The checked-out bytes define the criteria used for consistency validation; approval provenance and
release eligibility remain the responsibility of the release-qualification workflow in spec 014.

## Required MVP criteria

| ID | Owner spec | Class | Requirement | Validation | Evidence |
|---|---|---|---|---|---|
| MVP-009-001 | 009 | integrity | The qualification manifest accounts for every criterion in these exact contract bytes once and derives one consistent decision without waivers. | Run the acceptance validator against the selected contract and manifest. | acceptance-validation-report |
| MVP-010-001 | 010 | safety | Credentials and secret values do not appear in client-visible output, public artifacts, qualification evidence, logs, or failure details. | Execute the secret-containment suite with credential canaries across every public boundary. | secret-containment-report |
| MVP-011-001 | 011 | safety | Invalid or incomplete registered configurations are refused before planning or destination writes begin. | Exercise complete configuration preflight and confirm each invalid input produces a typed refusal. | preflight-validation-report |
| MVP-012-001 | 012 | safety | Refused, failed, and uncertain runs preserve distinct actionable evidence, and uncertain writes require operator reconciliation and a new plan. | Exercise each failure class and inspect the retained run result and prescribed next action. | failure-evidence-report |
| MVP-013-001 | 013 | integrity | REST, Python SDK, and CLI clients can list and inspect runs, verify and review plans, cancel eligible work, and list and retrieve public artifacts with equivalent semantics. | Run the lifecycle parity suite through the API, SDK, and CLI against one fixed service state. | lifecycle-parity-report |
| MVP-014-001 | 014 | release | One immutable NetBox-to-Infrahub candidate completes obtain, start, register, validate, plan, review, apply, and inspect and is approved by a non-implementing evaluator. | Execute the qualified journey against an existing Infrahub deployment and retain the independently approved qualification record. | qualification-record, evaluator-attestation |

## Service-first MVP boundary

The Sync HTTP API is the product boundary for registered configurations, runs, plans, artifacts,
and destination writes. The CLI uses the Python SDK, and the SDK calls the API; neither client
defines a second lifecycle contract or bypasses service admission. The qualified adapter journey is
NetBox to Infrahub against an existing Infrahub deployment and covers obtain, start, register,
validate, plan, review, apply, and inspect.

Historical requirements for offline CLI writes and in-process writes are superseded by this
service-first boundary and are not MVP acceptance requirements. Direct Prefect execution remains
read-only. Applying a reviewed plan requires the service write controls, while an uncertain write
stops the run for operator reconciliation and requires a new plan rather than automatic replay.

## MVP scope mapping

Each MVP obligation has exactly one owning specification. Completion of one specification alone
does not make a candidate releasable; all six owners must supply passing evidence for the immutable
candidate evaluated by spec 014.

- **009** — owns this normative contract, its exact criterion accounting, version and digest
  identity, no-waiver decision rule, and consistency validation.
- **010** — owns secret containment across client-visible output, public artifacts, qualification
  evidence, logs, and failure details.
- **011** — owns complete configuration preflight and refusal of invalid or incomplete registered
  configurations before planning or writing.
- **012** — owns distinct, actionable refused, failed, and uncertain-run evidence, including the
  reconciliation-and-new-plan response to uncertain writes.
- **013** — owns REST, Python SDK, and CLI lifecycle parity for run listing and inspection, plan
  verification and review, cancellation, and public-artifact listing and retrieval.
- **014** — owns the immutable candidate workflow, approved-contract provenance, artifact
  availability and byte verification, independent evaluation, and the final release-eligibility
  decision.

This version is the initial catalog established by spec 009. Approval of a downstream specification
that refines its evidence obligation or changes a criterion, scope boundary, or qualification
semantic must update this document and increment the contract version before spec 014 qualifies a
candidate. Qualification against earlier bytes remains immutable historical evidence, but it does
not establish eligibility under the latest approved contract.

## Explicit post-MVP deferrals

These larger-v3 capabilities remain valid product goals but do not block MVP qualification. A
deferral does not relax any required criterion above.

- **adapter-conformance** — full adapter conformance and custom-adapter onboarding beyond the
  qualified NetBox-to-Infrahub journey.
- **governed-replay** — governed crash recovery, checkpoints, and automatic replay; MVP instead
  requires operator reconciliation and a new plan after an uncertain write.
- **infrahub-branch-review** — Infrahub branch-based review and merge workflows.
- **scheduled-and-event-driven-execution** — schedules, event triggers, and trigger notifications.
- **incremental-extraction** — cursors, incremental extraction, and periodic full
  resynchronization.
- **large-run-planning** — batched or indexed planning and alternative planners not justified by
  benchmark evidence.
- **ownership-and-deletion** — explicit ownership scopes and safe deletion within those scopes.
- **parallel-execution** — parallel partitions and configurable destination-write concurrency.
- **production-operations** — Kubernetes, high availability, backup and restore, broader production
  operations, and telemetry policy.
- **operator-web-interface** — the operator web interface.

## Product-owner classification and versioning

Classify every new requirement before scheduling release work:

1. If it is necessary for the service-first qualified journey, protects an existing MVP safety or
   integrity guarantee, or supplies release qualification, assign it to exactly one of specs
   009–014 and add or revise a required criterion.
2. If it is a larger-v3 outcome that is not necessary to satisfy any required criterion, give it one
   unique entry in the explicit deferral list and explain the boundary. Do not classify the same
   capability as both required and deferred.
3. Do not use Jira-only text, historical standalone-write requirements, or an informal exception to
   change the release gate. If ownership or classification is unclear, resolve it in the contract
   review before implementation proceeds.

Increment the positive integer contract version whenever a criterion, owner, class, validation
method, accepted evidence type, service boundary, scope mapping, deferral, or qualification semantic
changes. In particular, approving specs 010–014 with refined evidence obligations requires a
contract increment. Purely editorial changes that preserve all obligations need not increment the
number, although their different byte digest still identifies them exactly. After any newly approved
contract revision, requalify the candidate against that revision; never rewrite an earlier manifest.
