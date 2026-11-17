# Critique Report: Open the web interface from the address of the Sync API

**Date**: 2026-11-16 | **Spec**: [../spec.md](../spec.md) | **Plan**: [../plan.md](../plan.md)

## Executive Summary

The specification solves the need stated in issue
[#421](https://github.com/opsmill/infrahub-sync/issues/421) with a small design: one mount, one
catch-all route, one client rule and one Node build stage. Two findings had to be fixed before tasks:
the page that holds a token and starts applies sent no Content-Security-Policy or framing
protection (E3), and the planned "not a Sync API" detection would have refused artifact downloads,
which are not JSON by design (E2). Both fixes are applied to `spec.md`, `plan.md`, `research.md`
and the contracts. Five recommendations are applied as well.

**Verdict**: ⚠️ PROCEED WITH UPDATES. The updates are applied; tasks can be generated.

## Product Lens Findings 🎯

### Problem Validation

| ID | Severity | Finding | Suggestion |
|----|----------|---------|------------|
| P1 | 💡 | The need comes from the issue and from the deployment model; no customer request is cited. The cost of not building it is that the web interface added in #410 stays developer-only. | Keep the scope as is. No change. |

### User Value Assessment

| ID | Severity | Finding | Suggestion |
|----|----------|---------|------------|
| P2 | 💡 | A person is asked for a "Sync API token", but no requirement says where that person gets one. Tokens come from the operator's `INFRAHUB_SYNC_SERVICE_BEARER_TOKENS` (`infrahub_sync/service/auth.py:12`). | **Applied**: FR-031 now requires the docs to state that an operator issues each person their own token through that setting. |

### Alternative Approaches

| ID | Severity | Finding | Suggestion |
|----|----------|---------|------------|
| P3 | 💡 | Serving the web interface from a separate static server (for example a reverse proxy in Compose) would avoid moving the API. It keeps two addresses and a second container, which the issue rules out. | No change. Recorded in the ADR as a rejected alternative. |

### Edge Cases & UX

| ID | Severity | Finding | Suggestion |
|----|----------|---------|------------|
| P4 | 💡 | An operator who rolls back the image after the upgrade also has to roll back the CLI; the symptom of a mismatch with an older CLI is `error: compatibility` with `server_version: <none>`, which the older CLI cannot explain. | **Applied**: FR-032 now requires the changelog to name the rollback rule and that symptom. |

### Success Measurement

| ID | Severity | Finding | Suggestion |
|----|----------|---------|------------|
| P5 | 💡 | Success criteria are measurable in a test environment; nothing is observed after release. | No change; the service has no usage telemetry and this feature does not add any. |

## Engineering Lens Findings 🔬

### Architecture Soundness

| ID | Severity | Finding | Suggestion |
|----|----------|---------|------------|
| E1 | 💡 | The mount design keeps `create_app` and 265 test literals unchanged and forwards the lifespan explicitly. Risk: a future maintainer adds a second lifespan resource and serves `build_app` directly, losing it silently. | Plan requires a test that the reconciler task starts through `build_service()`. Already in research R-1; **applied** as an explicit line in the plan's test notes. |

### Failure Mode Analysis

| ID | Severity | Finding | Suggestion |
|----|----------|---------|------------|
| E2 | 🎯 | Contract `cli-client.md` raised `NotSyncAPIError` when an answer's `Content-Type` is not JSON. `SyncClient.get_artifact` (`infrahub_sync/client/client.py:645-667`) receives artifact content of any media type by design, so every artifact download would fail. | **Applied**: the check covers answers to operations that expect a JSON document, and error statuses of every operation; a 200 answer of `get_artifact` is excluded (its digest check already applies). Contract, data model, research R-7 and FR-023 updated. |

### Security & Privacy

| ID | Severity | Finding | Suggestion |
|----|----------|---------|------------|
| E3 | 🎯 | The page holds a bearer token in `sessionStorage` and offers verify, apply and cancel, but the plan sends no Content-Security-Policy and no framing protection. An injected script could read the token, and another site could frame the page and lead a person to press Apply. `frontend/index.html` contains one inline script (the theme), and the built style sheet loads a font from `https://rsms.me`. | **Applied**: new FR-034. Answers outside `/api` carry `Content-Security-Policy` with `script-src 'self'` plus the SHA-256 hash of each inline script, computed by the service from the built `index.html` when it starts; `connect-src 'self'`; `frame-ancestors 'none'`; `font-src 'self' https://rsms.me`; `object-src 'none'`; `base-uri 'none'`; plus `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY` and `Referrer-Policy: no-referrer`. The frontend is not changed. Contract and plan updated. |
| E4 | 💡 | The token entry screen allows any number of attempts. This matches the API today (every refusal is recorded server-side, `app.py:348`), and tokens have at least 16 characters (`auth.py`). | **Applied** as an assumption in the spec. No rate limit in this feature. |

### Performance & Scalability

| ID | Severity | Finding | Suggestion |
|----|----------|---------|------------|
| E5 | 💡 | Static files are served by the API process. 1.1 MB, hashed assets cached for a year, entry page revalidated. No concern at the expected scale (one team). | No change. |

### Testing Strategy

| ID | Severity | Finding | Suggestion |
|----|----------|---------|------------|
| E6 | 💡 | `frontend/` has no test runner, and the token logic (clear only on a 401 for the held token) is security-relevant. | **Applied**: plan requires the logic in `token.ts` and the 401 step to stay in small pure functions, and quickstart scenario 3 browser steps are required evidence in the implementation report. Adding a test runner is out of scope (another maintainer owns `frontend/`). |

### Operational Readiness

| ID | Severity | Finding | Suggestion |
|----|----------|---------|------------|
| E7 | 💡 | A configured `INFRAHUB_SYNC_WEB_DIRECTORY` without `index.html` is only logged. | No change: logged once at start; the API keeps working, which FR-029 requires. |

### Dependencies & Integration Risks

| ID | Severity | Finding | Suggestion |
|----|----------|---------|------------|
| E8 | 💡 | The Node stage uses Node 22 to match the frontend CI job, while the submodule declares Node 24 or later for its own build. If the design system starts using Node 24 features, the image build and CI break together. | **Applied**: plan notes that the Node version of the `web` stage and of `workflow-linter.yml` change together. |
| E9 | 💡 | The submodule tracks `develop` of `opsmill/infrahub`; the image builds the recorded revision only, and `frontend/pnpm-workspace.yaml` mirrors its catalog. | No change; the existing comment in `pnpm-workspace.yaml` covers it. |

## Cross-Lens Insights

| ID | Severity | Finding | Suggestion |
|----|----------|---------|------------|
| X1 | 💡 | Both lenses point at the upgrade: one address change for every client. The client adding `/api` (decision D-3) removes most of the operator work; the remaining cost is upgrading the CLI with the service. | Keep D-3. The changelog and the "Open the web interface" section state the rule. |

## Findings Summary

| ID | Lens | Severity | Category | Finding | Status |
|----|------|----------|----------|---------|--------|
| E2 | Engineering | 🎯 | Failure modes | Not-a-Sync-API check would refuse artifact downloads | Applied |
| E3 | Engineering | 🎯 | Security | No CSP or framing protection for a page holding a token | Applied (FR-034) |
| P2 | Product | 💡 | User value | Where a person gets a token is not documented | Applied (FR-031) |
| P4 | Product | 💡 | Edge cases | Rollback and old-CLI symptom not documented | Applied (FR-032) |
| E1 | Engineering | 💡 | Architecture | Lifespan forwarding needs a guarding test | Applied |
| E4 | Engineering | 💡 | Security | No rate limit on token attempts | Applied as assumption |
| E6 | Engineering | 💡 | Testing | Token logic without a frontend test runner | Applied |
| E8 | Engineering | 💡 | Dependencies | Node 22 versus the submodule's Node 24 | Applied |
| P1, P3, P5, E5, E7, E9, X1 | Both | 💡 | Various | No change needed | Recorded |
