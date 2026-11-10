# Alignment check: spec against the request

## Source

The request is the inline feature description given on 2026-11-05. It has no URL. Its six requirements are
quoted in short form here:

1. Run-summary artifact `sync-run-<run_id>-<stage>`. It holds the run ID, stage, outcome, configuration and
   version, branch, counts by action and kind, and the next command. It is published only in a Prefect run
   context, built from templates and records, and redacted. A failed publish logs a warning and changes nothing.
2. The last log line, `service run <id> stage=<stage> outcome=<outcome>; next step: <action>`. The action is the
   recorded `next_action`, else `runs show`, and failures also end with the line.
3. `INFRAHUB_SYNC_PREFECT_UI_URL`, which adds `prefect_flow_run_url` to responses. `runs show` prints it. The URL
   is validated (http/https, no user info), and the field is null when the setting is unset. Wired into the
   development Compose file, and documented in the root Compose file.
4. `ExecutionClaimRefusedError` with a fixed ERROR message that uses the current command names.
5. A recorded terminal state wins over a live observation.
6. The CLI `hint:` field for a missing or invalid `service_url`, and for a missing token. The fields and exit code
   are unchanged.

Non-goals: no new HTTP routes; no change to stored formats beyond optional fields; no scheduling through
Prefect.

Acceptance:

- the unit tier, `ruff check .` and `invoke lint` pass;
- the orchestration, compose-deployment, sync-http-api and cli documentation is updated;
- there is one changelog fragment per user-visible change;
- the development stack check passes;
- the work is test-first, with no pull request and commits with explicit paths.

## Verdict

⚠️ MINOR DRIFT (proceeding)

## Findings

| Severity | Category | Request reference | Spec reference | Description |
| --- | --- | --- | --- | --- |
| Minor | added | Req. 3 "document it in the root docker-compose.yml" | FR-015 | The spec also passes the setting through to `sync-api` in the root Compose file, with an empty default. The default changes nothing for an operator who does not set it. The change came from critique finding P2. |
| Minor | added | Req. 3 "The URL is validated" | FR-012 | The spec decides what validation means: the Sync API refuses to start on an invalid value. The request did not say when to validate. This is a clarification, recorded in research R5. |
| Minor | added | Req. 2 | FR-008a | It defines the line for a failure that records nothing (`outcome=failed`). The request covered only recorded failures. This is a clarification from critique finding P4. |
| Minor | added | Req. 1 | FR-002, FR-006 | Kinds are sorted, the failure's `cause` is never shown, and the key is lowercased. These are refinements that follow from the redaction rule and Prefect's key rules. |
| None | — | Process: no pull request, explicit-path commits | Not in the spec | These are process instructions. The implement phase follows them, and `spec.md` does not need to hold them. |

No requirement, non-goal or acceptance item is missing, changed or dropped.

## Action

Proceed to implementation. No remediation pass is needed.
