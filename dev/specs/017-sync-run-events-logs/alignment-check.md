# Alignment check: spec against the idea brief

## Source

The source is the idea brief "Sync run events and logs, aligned with Infrahub", grilled with the user on 2026-11-06.
It has no URL; it was handed over as a local file. Every decision in it was confirmed by the user:

- logs are read from Prefect behind the Sync API, with no log store in Sync;
- events stay Sync audit events, in Infrahub's layout;
- the timeline and the logs ship together;
- readers poll; there is no streaming;
- any valid token reads every event and every log.

Its requirements are FR-001 to FR-012 and FR-014 to FR-016 (the brief has no FR-013), its user journey has three
scenarios (timeline, logs, failure), and it lists six success criteria, four edge cases, assumptions, out-of-scope
items and three open questions.

## Verdict

⚠️ MINOR DRIFT (proceeding)

## Findings

| Severity | Category | Brief reference | Spec reference | Description |
| --- | --- | --- | --- | --- |
| Minor | changed | FR-006, SC-004 ("search responses and CLI output for `prefect`") | FR-006 | The spec keeps three things that a plain search for `prefect` would match: existing field names (`prefect_executions`, `prefect_flow_run_url`), the value of the operator-configured `prefect_flow_run_url` link from spec 016, and the refused-claim line written when someone selects Retry in Prefect. Error messages that name Prefect today are reworded. Critique finding P2; plan.md, "Messages that name Prefect today". |
| Minor | changed | FR-009, Out of scope "events for requests rejected for a bad token" | FR-009, research R6 | The Sync API keeps writing its `refused-authentication` audit record, which spec 015 tests as security evidence, but the record has no event type and `GET /events` never returns it. The brief's test intent (no event for such a request) holds. |
| Minor | added | Scenario 1 (accepted, planned, applied) | User Story 1, scenario 1 | The spec lists four events: an apply is its own request, which the Sync API accepts and records today, so `run.accepted` appears twice. Critique finding P1. |
| Minor | added | Scenario 1 needs `run.planned` and `run.applied` | FR-012a | The brief does not say who writes stage-result events. Today only the Sync API's decisions are recorded, so the spec adds one event per stage result, written by the worker in the result's transaction. The worker event names the account whose request submitted the flow run (critique E1). |
| Minor | added | Edge case "Prefect unavailable when logs are read" | FR-013 | The edge case is made a requirement with HTTP 503 `log-unavailable`, and FR-010 gets HTTP 410 `log-expired`, following the existing `artifact-expired` answer. |
| Minor | added | Governance gates, constitution "Documentation" | FR-017 | Documentation and changelog fragments are made a requirement, as the constitution requires for user-visible changes. |
| Minor | added | FR-002 | research R5 | Every `DestinationSchemaUnavailableError` reason gets a fixed cause and next action, not only the missing branch, so that FR-001 holds for the whole error class. |
| None | — | Open questions 1–3 | research R1, R2, R3 | Answered from code: a flow can end `Failed` without raising, but Prefect's runtime entry point can still log one engine traceback, which Sync never serves; audit events are not in the decision's transaction today; Sync lines are told apart by the `infrahub_sync... \| ` prefix. Optional question on `TaskError` names: Sync's names are kept, and renaming is listed out of scope. |

No requirement, success criterion, edge case or out-of-scope item of the brief is missing or dropped.

## Action

Proceed to implementation. No remediation pass is needed: every finding refines or clarifies the brief and none
reopens a decision the user confirmed.
