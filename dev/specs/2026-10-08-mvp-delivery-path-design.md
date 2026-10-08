# Infrahub Sync MVP delivery path

**Date:** 2026-10-08
**Status:** Proposed
**Source:** `/tmp/infrahub-sync-v3-gap-analysis.md`

## Intent

Turn the remaining MVP gaps into independently reviewable specifications that lead to one
qualified, service-first Infrahub Sync release. The path covers the supported NetBox-to-Infrahub
journey on one host. It does not pull the broader post-MVP v3 capability set into the release.

## Scope decisions

- The Sync HTTP API remains the product boundary. Offline CLI execution and in-process writes are
  not MVP requirements.
- Run listing, verification, cancellation, and artifact inspection are MVP capabilities across the
  interfaces appropriate to each operation.
- The MVP uses operator reconciliation after uncertain writes. Governed replay is post-MVP.
- NetBox to Infrahub is the qualified adapter journey. Full adapter conformance is post-MVP.
- Kubernetes, scheduling, event triggers, high availability, incremental extraction, parallel
  partitions, the alternative planner, and the operator web UI are post-MVP.

## Specification sequence

| Order | Specification | Purpose |
| --- | --- | --- |
| 1 | [009 MVP acceptance contract](../../specs/009-mvp-acceptance-contract/spec.md) | Establish the authoritative boundary and evidence model. |
| 2 | [010 Secret containment](../../specs/010-secret-containment/spec.md) | Make every client-visible and qualification surface safe. |
| 3 | [011 Configuration preflight](../../specs/011-configuration-preflight/spec.md) | Refuse invalid configurations before planning or writing. |
| 4 | [012 Run failure evidence](../../specs/012-run-failure-evidence/spec.md) | Make refusal, failure, and uncertain-write outcomes actionable. |
| 5 | [013 Run inspection and review](../../specs/013-run-inspection-review/spec.md) | Complete lifecycle parity and practical plan review. |
| 6 | [014 MVP release qualification](../../specs/014-mvp-release-qualification/spec.md) | Publish and independently qualify the immutable release. |

## Dependencies

The acceptance contract is normative for every later specification. Secret containment,
configuration preflight, and failure evidence may be delivered independently after that contract.
Run inspection depends on their public result and error shapes. Release qualification consumes
the completed behavior and is the final MVP gate.

```text
009 MVP acceptance contract
├── 010 Secret containment
├── 011 Configuration preflight
└── 012 Run failure evidence
             ↓
013 Run inspection and review
             ↓
014 MVP release qualification
```

## Completion rule

The MVP is complete only when all six specifications meet their success criteria and the final
qualification evidence identifies one immutable image digest. Completing an individual spec does
not by itself make the MVP releasable.

## Explicitly deferred v3 work

The following remain valid v3 goals but do not block this path: adapter conformance and custom
adapter onboarding; crash-safe replay; Infrahub branch review; schedules; events; incremental
extraction; batched and indexed planning; ownership scopes; parallel partitions; production
Kubernetes/HA/backup operations; telemetry policy; the operator UI; and any alternative planner
not justified by benchmark evidence.
