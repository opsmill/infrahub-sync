# Alignment check: an external object store, every adapter's credentials and a private CA

**Date**: 2026-11-12

## Source

The source PRD is old specification 013 (`tmp-sync-specs/013-compose-images/spec.md`, 39 FRs and
13 SCs) plus the caller's decisions 1 to 3 of 2026-11-12, which [spec.md](spec.md) lists under
"Decisions". The recheck of 2026-11-12 (`tmp-sync-specs/recheck-013.md`) decides which old
requirements specifications 010 and 007 already delivered, delivered differently or made obsolete;
those are context, not drift. Changes made by decisions 1 to 3 are not drift.

## Verdict

⚠️ MINOR DRIFT (proceeding). One softened requirement was found and restored in `spec.md` during this
check. The remaining differences are small and recorded below.

## Findings

| Severity | Category | PRD reference | Spec reference | Description |
| --- | --- | --- | --- | --- |
| Significant, fixed | dropped | Old FR-027: release notes name the digest "so that an evaluator can pull the image by digest and find the SBOM of that release" | FR-021 | The SBOM part was missing. Restored: the release notes also give the `cosign verify-attestation` command for the SBOM attested to the digest. Task T045 updated |
| Minor | changed | Old FR-023: credential variables named by old 012's rule (FR-005, FR-006) | FR-008, "Decisions taken while writing this specification" | The fixed list follows the names the shipped examples and adapter pages use, because old 012's rule conflicts with them (gap-012). Still a fixed list, as in old 013. Marked to confirm at review |
| Minor | added | Old research R6 item 6, not an old FR | FR-002 | `sync-bootstrap` stops when the bundled store would hold an external store's credential. Old 013 designed it in research; the spec now requires it |
| Minor | added | not in old 013 | FR-002, critique P4 | `sync-bootstrap` stops with `object-store-endpoint-not-set` when the bundled store is left out and the endpoint keeps its default |
| Minor | changed | Old FR-024: "the Sync services" | FR-011 to FR-013 | Named as `sync-bootstrap`, `sync-api` and `sync-worker`. `prefect-server` and `cli` now run the Sync image (010) but make no outgoing TLS connection. Clarification |
| Minor | added | Old research R8, not an old FR | FR-015 | Rewrite of "Current limit of the Compose deployment" in `custom-certificates.mdx` |
| Minor | changed | Old SC-013 | SC-005, FR-018 | Leak checks of the worker and Prefect logs use credentials of six characters or more. Decision 2 |
| Minor | added | not in old 013 | FR-024 | Development PostgreSQL digest pin. Decision 3 |
| Minor | changed | Old User Story 5: "converted its v2 configuration ... (spec 012)" | User Story 2 | The conversion command of old 012 does not exist; the story starts from a package the team registers. Context |
| None | changed by merged specifications | Old FR-001 to FR-016, FR-019 to FR-021, FR-036 to FR-039, SC-001 to SC-004, SC-006, SC-007 | "Changes from old 013 made by merged specifications", traceability table | Delivered, delivered differently or made obsolete by 010 and 007 (recheck-013) |

Every other old FR and SC that the recheck classes as partly delivered or missing is in the spec with
the same meaning; see the traceability table in [spec.md](spec.md).

## Action

Proceed. No remediation pass was needed after FR-021 was restored. Remediation passes used: 0.
