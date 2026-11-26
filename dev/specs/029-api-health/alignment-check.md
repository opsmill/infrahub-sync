# Alignment check: spec 029 against the approved brief

## Source

The `/speckit-opsmill-prep` command was run with no input. The source of truth is the idea brief
the product owner approved in the grilling session of 2026-11-23 (`opsmill-dev-grilling-ideas`),
with the product owner's later answers: the product owner builds the web health page; the
database check does not write; the 24-hour stale period is fixed; `/api/status` is removed because
it was never released. The brief follows INFP-663 requirement 3
([INFP-663](https://opsmill.atlassian.net/browse/INFP-663)) and Infrahub PR
[opsmill/infrahub#11473](https://github.com/opsmill/infrahub/pull/11473).

## Verdict

⚠️ MINOR DRIFT (proceeding)

## Findings

| Severity | Category | Brief reference | Spec reference | Description |
| --- | --- | --- | --- | --- |
| Minor | changed | FR-011: the worker healthcheck "fails when the worker has not recorded a successful heartbeat" | FR-011, US2 | The check now fails when the worker's process has not run, recorded by the worker's own timer. In Compose the Prefect heartbeat depends on the PostgreSQL that also holds the product database, so the approved wording would make the worker container unhealthy during a PostgreSQL outage, which the product owner ruled out (brief question 5: the check looks only at the worker's own process). The intent is kept; the approved phrase is not. Report to the product owner. |
| Minor | changed | SC-003: "a component recovers → 200 within 15 seconds" | SC-003, US1-2 | PostgreSQL recovery is 30 seconds, because the workers must send a heartbeat again through the Prefect database in the same PostgreSQL. The other components keep 15 seconds. The timings are measured on the development stack and documented. |
| Minor | dropped | FR-005: health requests not counted in request metrics | Assumptions | The Sync API has no request metrics or tracing, so the requirement has nothing to apply to. Recorded as an assumption for when either is added. |
| Minor | added | FR-007: 503 when live workers are below the minimum | FR-007, Edge Cases | Also 503 when the workers cannot be read (`unknown`) while every component is up, because the minimum cannot be confirmed. Follows from FR-007; not in the brief's words. |
| Minor | added | FR-012: no host name, IP address, credential name or version | FR-013 | Worker names other than `infrahub-sync-service-<uuid>` are shown as `other`, so a name set outside Sync never reaches an unauthenticated caller. A stricter form of FR-012. |
| Minor | added | (none) | FR-012 | The documentation states that `/api/health` is a readiness signal, not a liveness probe, and that a probe's timeout must exceed the check timeout. Guidance only. |

No brief requirement is missing; no acceptance scenario was softened except the PostgreSQL recovery
time above; no out-of-scope item entered the spec.

## Action

Proceed. Report the FR-011 wording change and the PostgreSQL recovery time to the product owner at
the top of the completion summary.
