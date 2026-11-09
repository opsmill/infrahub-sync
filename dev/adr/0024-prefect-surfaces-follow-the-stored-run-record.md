# 24. What Prefect shows about a Sync run comes from the stored run record

**Status**: Accepted
**Date**: 2026-11-09
**Source**: dev/specs/archive/016-prefect-run-observability/research.md (R1, R4, R7)

## Context

Sync runs every stage of a registered run as a Prefect flow run
([ADR 0019](0019-prefect-is-an-internal-component-of-sync.md)). The Sync API keeps the run record
(`ProductRun`), and Prefect keeps the live execution state and the logs. An operator who opens a flow
run in the Prefect UI must be able to read what the stage did and what to do next.

Before this decision, the worker wrote a stage's only final log line inside `_execute_stage`, before
it committed the result. A stage whose commit then failed logged `outcome=<success>` and failed
afterwards. The run response also reported the orchestration `state` from the Prefect observation,
which can be older than the outcome that Sync recorded. A completed execution could therefore read
`Running`.

## Decision

Everything that Prefect shows about the outcome of a Sync run comes from the stored run record, and
is produced only after that record is stored.

- **Order of side effects.** `service_sync_run` stores the outcome first: `_commit_success` on
  success, `_record_failure` on failure. It then writes the stage's last log line, and then publishes
  the run summary artifact. Both use the outcome and the next action that were stored.
- **Artifact content.** `infrahub_sync/service/run_artifact.py` builds the markdown from named
  `ProductRun` fields and fixed templates only, together with the failure's `next_action`, which the
  failure boundary also builds from fixed templates. It never reads a failure's `cause`. The finished
  text passes through `redact` with the stage's secrets. The commands it lists are commands that
  exist on the CLI: `runs show`, and `runs plan <id> --detail` when the run holds a saved plan review.
- **Reported state.** When an orchestration entry has a recorded `terminal_state`, the run response
  reports that verdict as its `state`. Otherwise it reports the live observation when available, and
  the retained observation when not.

## Consequences

- The last log line, the artifact and the run response cannot report different outcomes for the same
  stage, because all three read the stored outcome.
- A stage whose run record cannot be read gets no artifact. The worker logs a warning, and the run
  does not fail. A failure to publish the artifact is also logged as a warning and never fails the run.
- A new field in the artifact or the last log line has to come from the run record or from fixed
  text. Text from an exception, a provider or the configuration cannot be added there.
- The artifact is published only inside a real Prefect flow run. The publishing function is passed
  in, so unit tests replace it with a recorder.

## Alternatives Considered

- **Keep the final log line in `_execute_stage` and add a second line on failure.** Rejected, because
  the success line would still be written before the commit. The earlier implementation that the spec
  restored (commit `dcbf391a`) also moved the line.
- **Copy the earlier artifact module unchanged (commit `43986fa9`).** Rejected, because that module
  names `runs problems` and `runs left-out` commands that do not exist on `feature/v3-develop`, and
  reads summary keys that this branch does not have.
