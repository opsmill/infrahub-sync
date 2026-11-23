import { describe, expect, it } from "vitest";

import type { AutomaticResume, Resume } from "#/api/types";

import {
  AUTOMATIC_RESUME_NAME,
  automaticResumeBlock,
  automaticResumePending,
  resumeEntries,
} from "./automatic-resume";

const row = (fields: Partial<AutomaticResume>): AutomaticResume => ({
  interrupted_flow_run_id: "flow-1",
  cause: "rule-8",
  cause_text: "the worker infrahub-sync-worker-3b2f stopped sending heartbeats",
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
      "cause: the worker infrahub-sync-worker-3b2f stopped sending heartbeats",
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
    expect(block.lines.at(-1)?.text).toBe(`from the first operation by ${AUTOMATIC_RESUME_NAME}`);
  });

  it.each([
    ["waiting-check", "Automatic resume 1: waiting for a live worker to run the check", false],
    ["checking", "Automatic resume 1: checking", false],
    ["waiting-resume", "Automatic resume 1: waiting for a live worker to resume", true],
    ["resuming", "Automatic resume 1: resuming", true],
    ["a-later-state", "Automatic resume 1: a-later-state", false],
  ])("shows the state %s of a record that has not ended", (state, title, checked) => {
    const block = automaticResumeBlock(row({ state, operations_not_checked: 3 }), 1);
    expect(block.title).toBe(title);
    expect(block.notStarted).toBe(false);
    expect(block.lines.some((l) => l.text === "passed, 0 differs, 3 not checked")).toBe(checked);
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
