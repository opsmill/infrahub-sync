# 34. A run's log is read from Prefect through the Sync API and holds only the lines that Sync wrote

**Status**: Accepted
**Date**: 2026-11-14
**Source**: `dev/specs/archive/017-sync-run-events-logs/research.md` (R1, R3, R4, R9),
`dev/specs/archive/017-sync-run-events-logs/spec.md` (Out of Scope)

## Context

A person who operates Sync reads the log of a run to find out what a stage did and why it failed.
Sync runs each stage as a Prefect flow run
([ADR 0019](0019-prefect-is-an-internal-component-of-sync.md)), and Prefect stores the log lines.
Before this decision, the person had to open the Prefect UI to read them. The log there also
holds the lines that Prefect's engine writes, and a traceback for each failure.

Prefect stores every line of a flow run under the same logger name, so the logger name does not
tell a line that Sync wrote from a line that the engine wrote.

## Decision

The Sync API serves the log of a run from `GET /runs/{run_id}/logs`. It reads the lines from
Prefect for each request, and returns only the lines that Sync's own code wrote.

- **No log store in Sync.** Sync keeps no copy of the lines. The `ServiceOrchestration` protocol
  has a `read_logs` method, which reads the lines of the flow runs that the run record names.
- **What a Sync line is.** A Sync line is a line whose message starts with an `infrahub_sync`
  logger name followed by ` | `. `RunLoggerBridge` gives this form to every line it forwards from
  the `infrahub_sync` logger tree. The service flow writes its own lines through `SyncFlowLines`,
  which adds the prefix `infrahub_sync.service.flow | `. The reader removes the prefix and returns
  the logger name as `source`.
- **Order and paging.** The reader returns the lines in the order of the run's executions, then
  by time, then by Prefect's log ID. `offset` and `limit` count the lines that are returned, not
  the lines that Prefect stores.
- **Redaction on read.** Each message is redacted with the Sync API's secrets, loses its control
  characters other than line breaks and tabs, is redacted again, and is then cut at 4,096
  characters.
- **A recognized failure writes no traceback.** When a stage fails with a recorded cause, the
  service flow writes one ERROR line with the cause and the next step, and ends the flow run by
  returning a `Failed` state with the same text. It does not raise the error again. A refused
  execution claim ends the same way. Any other failure writes the class name of the error and
  raises a sanitized copy. When the worker could not store the failure, or could not store the
  success of a `plan` or `verify` stage, the last line reports `outcome=failed` and the run
  record gets no terminal outcome: reconciliation decides it later.
- **Prefect cannot answer.** When Prefect no longer holds a flow run that the run record names,
  the route answers `410 log-expired`. When Prefect cannot be reached, it answers
  `503 log-unavailable`.
- **Wording.** No message that the Sync API returns holds the word "Prefect", and no message or
  served log line tells the person to open Prefect. A served log line can hold the word:
  the line of a refused execution claim states that a Prefect Retry is refused and that a Sync
  run is not replayed from Prefect. Field names such as `prefect_executions`, and the
  `prefect_flow_run_url` link that an operator configures, are kept.
- **Retention.** Sync deletes no flow run. How long a log can be read depends on the retention
  settings of Sync's Prefect server.

## Consequences

- Every line that the service flow writes itself must go through `SyncFlowLines`. A line written
  directly to the Prefect run logger has no prefix, and the route leaves it out.
- The route reads every line of the run from Prefect for each request, in pages of 200, and
  filters afterwards. No figure for the size of a run's log is known. A run with a very large log
  makes each read slower.
- A reader who polls sends `offset` equal to the number of lines it already holds. The offset
  stays valid when the engine writes more lines, because engine lines are not counted.
- Whether Prefect still stores an engine traceback for a flow run that ends with a returned
  `Failed` state depends on the entry point the worker uses, which was not checked. The route
  never returns an engine line.
- The prefix is a contract between the code that writes a line and the code that reads it.
  `tests/service/test_run_log.py` holds the pattern.
- `tests/service/test_prefect_wording.py` fails when a module under `infrahub_sync/` calls a
  Prefect flow run delete, or when an API message holds the word "Prefect".

## Alternatives considered

- **A log store in Sync.** Not chosen. The specification lists it as out of scope and gives no
  reason.
- **A child logger, so that the logger name tells the lines apart.** Not chosen: whether Prefect
  keeps the flow run ID for a child logger was not checked.
- **Count `offset` in the lines that Prefect stores.** Rejected: the offset would change meaning
  each time the engine writes a line.
- **Raise a custom exception that has no traceback text.** Rejected: Prefect logs every exception
  it catches with its traceback.
- **Streaming.** Not chosen. The specification lists it as out of scope and gives no reason.
  Readers poll.
