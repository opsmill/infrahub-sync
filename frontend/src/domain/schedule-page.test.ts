import { describe, expect, it } from "vitest";

import type { Principal, Run, Schedule } from "#/api/types";

import {
  headerActions,
  NOT_HANDED_OVER,
  noStartWord,
  runBucket,
  runGroups,
  runLine,
  scheduleStatus,
  startGroups,
  startSentence,
  statusNotes,
  zoneCity,
} from "./schedule-page";
import { scheduleActions, stopReasonText } from "./schedules";

function schedule(over: Partial<Schedule> = {}): Schedule {
  return {
    schedule_id: "s-1",
    name: "nightly",
    config_id: "c-1",
    registry_version: 3,
    cron: "0 2 * * *",
    timezone: "Europe/Paris",
    write_target: "branch-review",
    write_when_left_out: false,
    overlap_policy: "reject",
    start_late_by_at_most_seconds: 3600,
    state: "active",
    owner: "alice",
    prefect_synced: true,
    consecutive_failures: 0,
    created_at: "2026-11-01T00:00:00Z",
    updated_at: "2026-11-01T00:00:00Z",
    ...over,
  };
}

const ADMIN: Principal = { actor: "admin", administrator: true, approval_required: false };
const OWNER: Principal = { actor: "alice", administrator: false, approval_required: false };
const OTHER: Principal = { actor: "bob", administrator: false, approval_required: false };

/** Friday 20 November 2026, 16:00 UTC (17:00 in Paris). */
const NOW = Date.parse("2026-11-20T16:00:00Z");

/** A time on a day of November 2026, in the viewer's time zone. */
const local = (day: number, hour: number) => new Date(2026, 10, day, hour).toISOString();

const status = (s: Schedule, me: Principal = OWNER) =>
  scheduleStatus(s, scheduleActions(me, s), NOW);

describe("startSentence", () => {
  it("names the day and time in the schedule's time zone", () => {
    expect(startSentence("2026-11-21T01:00:00Z", "Europe/Paris", NOW)).toBe(
      "Starts in 9 hours, Saturday 02:00 Paris time."
    );
  });

  it("uses the past tense for a start before now", () => {
    expect(startSentence("2026-11-20T15:00:00Z", "Europe/Paris", NOW)).toBe(
      "Was due 1 hour ago, Friday 16:00 Paris time."
    );
  });

  it("writes the city of a time zone with spaces", () => {
    expect(zoneCity("America/Argentina/Buenos_Aires")).toBe("Buenos Aires");
    expect(zoneCity("UTC")).toBe("UTC");
  });

  it("leaves the day out for a time zone the browser does not know", () => {
    expect(startSentence("2026-11-21T01:00:00Z", "Mars/Olympus", NOW)).toBe("Starts in 9 hours.");
  });
});

describe("scheduleStatus", () => {
  it("states the next start of a healthy schedule, with no step", () => {
    const s = status(schedule({ next_start_at: "2026-11-21T01:00:00Z" }));
    expect(s).toEqual({
      tone: "neutral",
      icon: "active",
      text: "Starts in 9 hours, Saturday 02:00 Paris time.",
      details: [],
    });
  });

  it("offers the newer version as the step of a healthy schedule", () => {
    const s = status(schedule({ next_start_at: "2026-11-21T01:00:00Z", newer_version: 4 }));
    expect(s.step).toEqual({ label: "Change to version 4", dialog: "change-version" });
    expect(s.details).toEqual([
      "Version 4 of this configuration is registered. This schedule starts runs of version 3.",
    ]);
  });

  it("offers no step to an account that may not change the schedule", () => {
    const s = status(schedule({ newer_version: 4 }), OTHER);
    expect(s.step).toBeUndefined();
  });

  it("puts the failed runs of an active schedule first, in amber, with its runs as the step", () => {
    const s = status(schedule({ consecutive_failures: 3, next_start_at: "2026-11-21T01:00:00Z" }));
    expect(s.tone).toBe("warning");
    expect(s.text).toBe("3 runs in a row failed. After 5, the schedule stops.");
    expect(s.details).toEqual(["Starts in 9 hours, Saturday 02:00 Paris time."]);
    expect(s.step).toEqual({ label: "View the runs", runs: true });
  });

  it.each<[string, Principal, string | undefined]>([
    ["owner-not-found", ADMIN, "Take over"],
    ["owner-not-found", OWNER, undefined],
    ["approval-required", OWNER, "Change the write target"],
    ["repeated-failures", OWNER, "Resume"],
    ["something-new", OWNER, undefined],
  ])("states the stop reason %s in red, with the step for %o", (reason, me, step) => {
    const s = status(
      schedule({ state: "stopped", state_reason: reason, consecutive_failures: 5 }),
      me
    );
    expect(s.tone).toBe("danger");
    expect(s.text).toBe(stopReasonText(reason, 5));
    expect(s.step?.label).toBe(step);
  });

  it("gives a schedule stopped by failures its count and its runs", () => {
    const s = status(
      schedule({ state: "stopped", state_reason: "repeated-failures", consecutive_failures: 5 })
    );
    expect(s.details).toEqual([
      "Check the last runs of the schedule, then resume it.",
      "Failed runs in a row: 5",
    ]);
    expect(s.second).toEqual({ label: "View the runs", runs: true });
  });

  it("quotes the reason of a pause and offers Resume", () => {
    const s = status(schedule({ state: "paused", state_reason: "NetBox upgrade" }));
    expect(s.text).toBe("This schedule starts no run until it is resumed.");
    expect(s.details).toEqual(["Reason: “NetBox upgrade”"]);
    expect(s.step).toEqual({ label: "Resume", dialog: "resume" });
  });

  it("says a deleted schedule keeps its runs", () => {
    const s = status(schedule({ state: "deleted" }));
    expect(s.text).toBe("This schedule was deleted. Its runs are kept.");
    expect(s.step).toBeUndefined();
    expect(s.second).toEqual({ label: "View the runs", runs: true });
  });
});

describe("statusNotes", () => {
  const notes = (s: Schedule, me: Principal = OWNER) => {
    const access = scheduleActions(me, s);
    return statusNotes(s, access, scheduleStatus(s, access, NOW));
  };

  it("warns when the schedule is not handed to the task manager", () => {
    expect(notes(schedule({ prefect_synced: false }))).toEqual([
      { kind: "not-handed-over", text: NOT_HANDED_OVER },
    ]);
    expect(notes(schedule({ prefect_synced: false, state: "deleted" }))).toEqual([]);
  });

  it("keeps the newer version of a stopped schedule as a note with Change version", () => {
    expect(
      notes(schedule({ state: "stopped", state_reason: "owner-not-found", newer_version: 4 }))
    ).toEqual([
      {
        kind: "newer-version",
        text: "Version 4 of this configuration is registered",
        detail: "This schedule starts runs of version 3.",
        step: { label: "Change version", dialog: "change-version" },
      },
    ]);
  });

  it("does not repeat the newer version that the card already states", () => {
    expect(notes(schedule({ newer_version: 4 }))).toEqual([]);
  });
});

describe("headerActions", () => {
  it("leaves out the action that the status card offers", () => {
    const s = schedule({ state: "stopped", state_reason: "owner-not-found" });
    const access = scheduleActions(ADMIN, s);
    expect(headerActions(access, scheduleStatus(s, access, NOW))).toEqual([
      "change",
      "resume",
      "delete",
    ]);
  });

  it("keeps Change when the card offers the newer version", () => {
    const s = schedule({ newer_version: 4 });
    const access = scheduleActions(OWNER, s);
    expect(headerActions(access, scheduleStatus(s, access, NOW))).toEqual([
      "change",
      "pause",
      "delete",
    ]);
  });
});

describe("startGroups", () => {
  it("lists the farthest start first, so the soonest is nearest Now", () => {
    // In the viewer's time zone, whatever it is: the buckets are the viewer's days.
    const now = Date.parse(local(20, 17));
    const starts = [local(20, 19), local(21, 2), local(22, 2), local(30, 2)];
    expect(startGroups(starts, now)).toEqual([
      { bucket: "Later", items: [local(30, 2)] },
      { bucket: "This week", items: [local(22, 2)] },
      { bucket: "Tomorrow", items: [local(21, 2)] },
      { bucket: "Today", items: [local(20, 19)] },
    ]);
  });

  it("is empty without starts", () => {
    expect(startGroups([], NOW)).toEqual([]);
  });
});

function run(over: Partial<Run> = {}): Run {
  return {
    run_id: "r-1",
    operation: "sync",
    configuration_reference: "c-1",
    audit_links: [],
    started_at: "2026-11-20T01:00:00Z",
    phase: "applied",
    outcome: "applied",
    ...over,
  } as Run;
}

describe("runGroups", () => {
  it("maps a run to its state, its label and its start", () => {
    expect(runLine(run())).toEqual({
      runId: "r-1",
      state: "insync",
      label: "Applied",
      startedAt: "2026-11-20T01:00:00Z",
    });
  });

  it("keeps the 5 newest runs, newest first, by day", () => {
    const now = Date.parse(local(20, 17));
    const runs = [15, 20, 19, 18, 17, 16].map((day, i) =>
      run({ run_id: `r-${i}`, started_at: local(day, 2) })
    );
    const groups = runGroups(runs, now);
    expect(groups.map((g) => [g.bucket, g.items.map((r) => r.startedAt)])).toEqual([
      ["Today", [local(20, 2)]],
      ["Yesterday", [local(19, 2)]],
      ["Earlier", [local(18, 2), local(17, 2), local(16, 2)]],
    ]);
  });

  it("names the day of a run in the viewer's time zone", () => {
    // Both instants are local times, so the day boundary is the viewer's midnight in any zone.
    const now = Date.parse(local(20, 1));
    expect(runBucket(local(20, 0), now)).toBe("Today");
    expect(runBucket(new Date(2026, 10, 19, 23, 59).toISOString(), now)).toBe("Yesterday");
  });
});

describe("noStartWord", () => {
  it.each([
    ["stopped", "stopped"],
    ["paused", "paused"],
    ["deleted", "deleted"],
  ])("names a %s schedule", (state, word) => {
    expect(noStartWord(schedule({ state }))).toBe(word);
  });
});
