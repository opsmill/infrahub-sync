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

The completed contract defines the supported service-first journey and superseded standalone-write
requirements.

## MVP scope mapping

The completed contract maps every required MVP capability to its owning specification and retained
evidence.

## Explicit post-MVP deferrals

The completed contract lists larger-v3 capabilities that do not gate MVP qualification.
