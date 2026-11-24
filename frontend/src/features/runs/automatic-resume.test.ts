import { describe, expect, it } from "vitest";

import type { AutomaticResume, Resume } from "#/api/types";

import {
  automaticResumeBlock,
  automaticResumeNext,
  automaticResumePending,
  resumeEntries,
} from "./automatic-resume";

const row = (fields: Partial<AutomaticResume>): AutomaticResume => ({
  interrupted_flow_run_id: "flow-1",
  actor: "Sync (automatic resume)",
  cause: "rule-8",
  cause_text: "the worker infrahub-sync-service-3b2f stopped sending heartbeats",
  state: "ended",
  decided_at: "2026-11-23T21:04:11Z",
  updated_at: "2026-11-23T21:06:40Z",
  ...fields,
});

const text = (block: ReturnType<typeof automaticResumeBlock>) => [
  block.title,
  ...block.lines.map((l) => `${l.label}: ${l.text}`),
];

describe("automaticResumeBlock", () => {
  it("reads as the started block of runs show", () => {
    const block = automaticResumeBlock(
      row({ decision: "resume", operations_not_checked: 0, start_after: 6000 }),
      1
    );
    expect(text(block)).toEqual([
      "Automatic resume 1: started",
      "cause: the worker infrahub-sync-service-3b2f stopped sending heartbeats",
      "check: passed, 0 differs, 0 not checked",
      "resumed: after operation 6000 by Sync (automatic resume)",
    ]);
    expect(block.notStarted).toBe(false);
  });

  it("reads as the not started block of runs show", () => {
    const block = automaticResumeBlock(
      row({
        cause: "rule-6",
        cause_text: "Prefect reported the flow run as crashed",
        decision: "no-resume",
        reason: "check-found-differs",
        reason_text: "the check found 1 node that differs from the plan",
      }),
      2
    );
    expect(text(block)).toEqual([
      "Automatic resume 2: not started",
      "cause: Prefect reported the flow run as crashed",
      "reason: the check found 1 node that differs from the plan",
    ]);
    expect(block.notStarted).toBe(true);
  });

  it("names the first operation when no position was saved", () => {
    const block = automaticResumeBlock(row({ decision: "resume", start_after: null }), 1);
    expect(block.lines.at(-1)?.text).toBe("from the first operation by Sync (automatic resume)");
  });

  it("names the actor the Sync API returns", () => {
    const block = automaticResumeBlock(
      row({ decision: "resume", start_after: 6000, actor: "Sync (renamed)" }),
      1
    );
    expect(block.lines.at(-1)?.text).toBe("after operation 6000 by Sync (renamed)");
  });

  it.each([
    ["waiting-check", "Automatic resume 1: waiting for a live worker to run the check", false],
    ["checking", "Automatic resume 1: checking", false],
    ["waiting-resume", "Automatic resume 1: waiting for a live worker to resume", true],
    ["resuming", "Automatic resume 1: resuming", true],
    ["a-later-state", "Automatic resume 1: a-later-state", false],
  ])("shows the state %s of a record that has not ended", (state, title, checked) => {
    // The check flow run exists from `checking` on; only a passed check records the count.
    const block = automaticResumeBlock(
      row({
        state,
        check_flow_run_id: state === "waiting-check" ? null : "flow-check",
        operations_not_checked: checked ? 3 : null,
      }),
      1
    );
    expect(block.title).toBe(title);
    expect(block.notStarted).toBe(false);
    expect(block.lines.some((l) => l.text === "passed, 0 differs, 3 not checked")).toBe(checked);
  });

  it.each([
    ["refused", "the Sync API refused the automatic resume with the code run-not-resumable"],
    [
      "person-took-over",
      "a person ran `verify` or `runs resume` while the automatic resume waited for a live worker",
    ],
  ])("shows the passed check of a record that ended %s after the check", (reason, reasonText) => {
    const block = automaticResumeBlock(
      row({
        decision: "no-resume",
        reason,
        reason_text: reasonText,
        check_flow_run_id: "flow-check",
        operations_not_checked: 2,
      }),
      1
    );
    expect(text(block)).toEqual([
      "Automatic resume 1: not started",
      "cause: the worker infrahub-sync-service-3b2f stopped sending heartbeats",
      "check: passed, 0 differs, 2 not checked",
      `reason: ${reasonText}`,
    ]);
    expect(block.notStarted).toBe(true);
  });

  it("shows a resume that Sync submitted and no worker started", () => {
    const block = automaticResumeBlock(
      row({
        decision: "no-resume",
        reason: "not-picked-up",
        reason_text: "no worker picked up the automatic resume in time",
        check_flow_run_id: "flow-check",
        resume_flow_run_id: "flow-resume",
        operations_not_checked: 0,
        start_after: 6000,
      }),
      1
    );
    expect(text(block)).toEqual([
      "Automatic resume 1: submitted, no worker started it",
      "cause: the worker infrahub-sync-service-3b2f stopped sending heartbeats",
      "check: passed, 0 differs, 0 not checked",
      "submitted: after operation 6000 by Sync (automatic resume)",
      "reason: no worker picked up the automatic resume in time",
    ]);
    expect(block.notStarted).toBe(true);
  });

  it("shows no check line for a check that no worker picked up", () => {
    const block = automaticResumeBlock(
      row({
        decision: "no-resume",
        reason: "not-picked-up",
        reason_text: "no worker picked up the automatic check in time",
        check_flow_run_id: "flow-check",
      }),
      1
    );
    expect(block.title).toBe("Automatic resume 1: not started");
    expect(block.lines.map((l) => l.label)).toEqual(["cause", "reason"]);
  });
});

describe("automaticResumeNext", () => {
  const interrupted = { run_id: "run-1", reconciliation_required: true };
  const notStarted = (reason: string) =>
    row({ decision: "no-resume", reason, reason_text: `the reason ${reason}` });

  it("gives the next line of runs show for the latest record that did not start", () => {
    const next = automaticResumeNext(notStarted("check-found-differs"), true, interrupted);
    expect(next).toBe(
      "run `infrahub-sync verify run-1`, then a new plan or `infrahub-sync runs resume run-1 --expected-checksum CHECKSUM`"
    );
    expect(text(automaticResumeBlock(notStarted("check-found-differs"), 1, next))).toEqual([
      "Automatic resume 1: not started",
      "cause: the worker infrahub-sync-service-3b2f stopped sending heartbeats",
      "reason: the reason check-found-differs",
      `next: ${next}`,
    ]);
  });

  it("does not name runs resume for a run it cannot resume", () => {
    expect(automaticResumeNext(notStarted("not-resumable"), true, interrupted)).toBe(
      "create a new plan; `infrahub-sync runs resume` cannot resume this run"
    );
  });

  it.each([
    ["an earlier record", notStarted("check-found-differs"), false, interrupted],
    ["a record that started", row({ decision: "resume" }), true, interrupted],
    ["a record that has not ended", row({ state: "waiting-check" }), true, interrupted],
    [
      "a run with no interrupted write",
      notStarted("check-found-differs"),
      true,
      { run_id: "run-1", reconciliation_required: false },
    ],
  ])("gives nothing for %s", (_name, record, latest, run) => {
    expect(automaticResumeNext(record, latest, run)).toBeUndefined();
  });
});

describe("automaticResumePending", () => {
  it.each([
    ["is false with no records", [], false],
    [
      "is false when every record ended",
      [row({ decision: "no-resume" }), row({ decision: "resume" })],
      false,
    ],
    [
      "is true while a record waits for the check",
      [row({ decision: "no-resume" }), row({ state: "waiting-check" })],
      true,
    ],
    ["is true while a record is resuming", [row({ state: "resuming" })], true],
  ])("%s", (_name, rows, pending) => {
    expect(automaticResumePending(rows)).toBe(pending);
  });

  it("is false for a Sync API that returns no records", () => {
    expect(automaticResumePending(undefined)).toBe(false);
  });
});

describe("resumeEntries", () => {
  const resume = (fields: Partial<Resume>): Resume => ({
    actor: "alice",
    requested_at: "2026-11-23T21:06:00Z",
    ...fields,
  });

  it("shows the actor the Sync API returns, for a person and for the automatic resume", () => {
    const entries = resumeEntries([
      resume({ actor: "alice" }),
      resume({ actor: "Sync (automatic resume)" }),
      resume({ actor: "Deleted account 1a2b3c" }),
    ]);
    expect(entries.map((e) => e.actor)).toEqual([
      "alice",
      "Sync (automatic resume)",
      "Deleted account 1a2b3c",
    ]);
    expect(entries.map((e) => e.title)).toEqual(["Resume 1", "Resume 2", "Resume 3"]);
  });

  it("states where each resume started and how it ended", () => {
    expect(
      resumeEntries([
        resume({ start_after: 6000, outcome: "succeeded", operations_written: 1 }),
        resume({ start_after: null, outcome: "ambiguous", operations_written: 12 }),
        resume({ start_after: 7 }),
      ]).map((e) => [e.start, e.outcome])
    ).toEqual([
      ["Started after operation 6000", "Succeeded, wrote 1 operation"],
      ["Started from the first operation", "Interrupted: result unknown, wrote 12 operations"],
      ["Started after operation 7", "Not finished"],
    ]);
  });
});
