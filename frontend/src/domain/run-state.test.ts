import { describe, expect, it } from "vitest";

import type { Run } from "#/api/types";

import { failureDetail } from "./run-state";

/** A sync run whose write could not take the configuration's write guard. */
function contendedRun(failure: Record<string, unknown>): Run {
  return {
    run_id: "20261123T1015-bbbbbbbb",
    operation: "sync",
    configuration_reference: "c-1",
    audit_links: [],
    started_at: "2026-11-23T10:15:00Z",
    phase: "sync-failed",
    outcome: "failed",
    reconciliation_required: false,
    trigger_type: "manual",
    artifact_refs: [],
    prefect_executions: [],
    automatic_resume_count: 0,
    results: {
      sync_failure: {
        stage: "sync",
        failure_type: "write_guard_contended",
        cause: "Another write to this configuration is running.",
        next_action: "Wait for that run to end, then start a new sync.",
        ...failure,
      },
    },
  } as Run;
}

describe("failureDetail activeRunId", () => {
  it("sets activeRunId when the failure record names the run that holds the write guard", () => {
    const f = failureDetail(contendedRun({ active_run_id: "20261123T1012-aaaaaaaa" }));
    expect(f?.activeRunId).toBe("20261123T1012-aaaaaaaa");
  });

  it.each([
    ["null", { active_run_id: null }],
    ["absent", {}],
    ["an empty string", { active_run_id: "" }],
    ["a number", { active_run_id: 42 }],
  ])("leaves activeRunId unset when active_run_id is %s", (_label, failure) => {
    const f = failureDetail(contendedRun(failure));
    expect(f).toBeDefined();
    expect(f?.activeRunId).toBeUndefined();
  });
});
