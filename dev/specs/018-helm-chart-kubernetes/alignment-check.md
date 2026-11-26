# Alignment check: spec 018 against the ask

**Date**: 2026-11-16

## Source

- GitHub issue [#422](https://github.com/opsmill/infrahub-sync/issues/422), "deploy: Helm chart to
  run Infrahub Sync 3.x on Kubernetes": its "Need" and "Context" sections.
- The inline brief given to `speckit-opsmill-auto`, with:
  - four decisions made by the maintainer: the repository, the location of the spec and the
    documentation, the bundled stores with an external option, and the web interface out of
    scope;
  - the design constraints found during orientation;
  - the verification steps.

## Verdict

⚠️ MINOR DRIFT (proceeding)

## Findings

| Severity | Category | Ask reference | Spec reference | Description |
| --- | --- | --- | --- | --- |
| Minor | changed | Issue: "follow each release" | User Story 2, SC-003 | The spec does not promise that packages and runs survive a change of Sync version. It promises only what the chart controls: the volumes are kept and the bootstrap runs again. The Compose page states that the alpha "promises no in-place state migration". This narrows the ask to what the product supports. It does not drop it. |
| Minor | changed | Brief: "Pre-install hooks … plain Jobs are immutable and fail to patch on `helm upgrade`" | FR-007, research R-2 | The bootstrap Job is named by a hash of its inputs, so it runs again when the image or a bootstrap setting changes, rather than on every upgrade. On an upgrade that changes neither, the completed Job stays in place; the bootstrap is safe to repeat, so the effect is the same. This also lets tools that render the chart with `helm template` apply an image change. |
| Minor | added | Brief: "If the image cannot be pulled, report that" | research R-6, plan "Release order" | On 2026-11-16 the default image `3.0.0a5` was not found in the registry. The plan therefore verifies with an image built from this repository. It states that the chart is published only after a Sync release that contains this code is in the registry. |
| None | covered | Every other decision and constraint of the brief | spec D-1 to D-4, FR-001 to FR-033, plan design points | Each one is covered. This includes the repository, the conventions of `infrahub-helm`, the Prefect server from the Sync image, the bundled-store check, the credentials and the filesystem. It also includes the versions, the image pull Secrets, the CA, the probes, the network exposure, the replicas, and the install and upgrade test on minikube. |

## Action

Proceed to implementation. The three minor changes are recorded in the spec, the research and the
critique, with the reason for each.
