import { describe, expect, it } from "vitest";

import type { Schedule } from "#/api/types";

import {
  emptyTabText,
  groupSchedules,
  matchesSearch,
  NEXT_STARTS_LIMIT,
  needsAction,
  nextStarts,
  startBucket,
  tabShows,
} from "./schedule-list";
import { stopReasonText } from "./schedules";

let seq = 0;
function schedule(over: Partial<Schedule> = {}): Schedule {
  seq += 1;
  return {
    schedule_id: `s-${seq}`,
    name: `schedule ${seq}`,
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

/** A time in the viewer's time zone, so the day buckets hold in any zone the tests run in. */
const local = (day: number, hour: number, minute = 0) =>
  new Date(2026, 10, day, hour, minute).getTime();
const localIso = (day: number, hour: number, minute = 0) =>
  new Date(local(day, hour, minute)).toISOString();

describe("needsAction", () => {
  it.each<[string, string, string]>([
    ["owner-not-found", "Take over", "schedule"],
    ["approval-required", "Change the write target", "schedule"],
    ["repeated-failures", "Resume", "schedule"],
    ["some-new-reason", "Open", "schedule"],
  ])("a schedule stopped for %s offers %s", (reason, step, target) => {
    const s = schedule({ state: "stopped", state_reason: reason, consecutive_failures: 5 });
    expect(needsAction(s)).toEqual({ text: stopReasonText(reason, 5), step, target });
  });

  it("a stopped schedule without a reason opens the schedule", () => {
    expect(needsAction(schedule({ state: "stopped" }))).toEqual({
      text: "This schedule is stopped.",
      step: "Open",
      target: "schedule",
    });
  });

  it.each<[number, string]>([
    [1, "The last run failed. After 5 failed runs in a row, the schedule stops."],
    [3, "3 runs in a row failed. After 5, the schedule stops."],
  ])("an active schedule with %i failed runs leads to its runs", (failures, text) => {
    expect(needsAction(schedule({ consecutive_failures: failures }))).toEqual({
      text,
      step: "View the runs",
      target: "runs",
    });
  });

  it.each<Partial<Schedule>>([
    { state: "active" },
    { state: "paused", consecutive_failures: 3, state_reason: "maintenance" },
    { state: "deleted", consecutive_failures: 2 },
  ])("%o needs no action", (over) => {
    expect(needsAction(schedule(over))).toBeNull();
  });
});

describe("matchesSearch", () => {
  const s = schedule({ name: "Nightly NetBox import" });
  it.each<[string, string, boolean]>([
    ["", "dcim", true],
    ["  ", "dcim", true],
    ["netbox", "dcim", true],
    ["NIGHTLY", "dcim", true],
    ["DCIM", "from-dcim", true],
    ["weekly", "dcim", false],
  ])("%j with configuration %j: %s", (search, configName, expected) => {
    expect(matchesSearch(s, search, configName)).toBe(expected);
  });
});

describe("groupSchedules", () => {
  const names: Record<string, string> = { "c-1": "NetBox to Infrahub", "c-2": "ACI fabric" };
  const configName = (id: string) => names[id] ?? id;
  const failing = schedule({ name: "b failing", consecutive_failures: 2 });
  const stopped = schedule({
    name: "c stopped",
    state: "stopped",
    state_reason: "owner-not-found",
  });
  const active10 = schedule({ name: "Sync 10", config_id: "c-2" });
  const active2 = schedule({ name: "sync 2" });
  const paused = schedule({ name: "a paused", state: "paused" });
  const deleted = schedule({ name: "deleted one", state: "deleted" });
  const all = [active10, stopped, deleted, paused, failing, active2];

  it("sorts every group by name and leaves deleted schedules out", () => {
    const g = groupSchedules(all, "", configName);
    expect(g.matches.map((s) => s.name)).toEqual([
      "a paused",
      "b failing",
      "c stopped",
      "sync 2",
      "Sync 10",
    ]);
    expect(g.needsAction).toEqual([failing, stopped]);
    expect(g.activeResting).toEqual([active2, active10]);
    expect(g.active).toEqual([failing, active2, active10]);
    expect(g.paused).toEqual([paused]);
  });

  it("finds a schedule by its configuration's name", () => {
    expect(groupSchedules(all, "aci", configName).matches).toEqual([active10]);
  });
});

describe("tabShows", () => {
  it.each([
    ["all", { needsAction: true, active: "resting", paused: true, deleted: false }],
    ["attention", { needsAction: true, active: false, paused: false, deleted: false }],
    ["active", { needsAction: false, active: "all", paused: false, deleted: false }],
    ["paused", { needsAction: false, active: false, paused: true, deleted: false }],
    ["deleted", { needsAction: false, active: false, paused: false, deleted: true }],
  ] as const)("%s", (tab, expected) => {
    expect(tabShows(tab)).toEqual(expected);
  });
});

describe("emptyTabText", () => {
  it.each([
    ["paused", "", "No schedule is paused."],
    ["paused", "net", "No paused schedule matches “net”."],
    ["all", "net", "No schedule or configuration matches “net”."],
    ["attention", "", "No schedule needs action."],
  ] as const)("%s with %j", (tab, search, expected) => {
    expect(emptyTabText(tab, search)).toBe(expected);
  });
});

describe("startBucket", () => {
  const now = local(20, 23, 30);
  it.each<[string, string, string]>([
    ["earlier today", localIso(20, 0, 0), "Today"],
    ["later today", localIso(20, 23, 59), "Today"],
    ["just after midnight", localIso(21, 0, 1), "Tomorrow"],
    ["the end of tomorrow", localIso(21, 23, 59), "Tomorrow"],
    ["in two days", localIso(22, 0, 30), "This week"],
    ["in six days", localIso(26, 12), "This week"],
    ["in seven days", localIso(27, 0, 0), "Later"],
  ])("%s", (_, iso, expected) => {
    expect(startBucket(iso, now)).toBe(expected);
  });
});

describe("nextStarts", () => {
  const now = local(20, 10);

  it("lists active schedules soonest first, grouped by day", () => {
    const tomorrow = schedule({ next_start_at: localIso(21, 1) });
    const today = schedule({ next_start_at: localIso(20, 11) });
    const later = schedule({ next_start_at: localIso(30, 9) });
    const r = nextStarts(
      [
        tomorrow,
        schedule({ state: "paused", next_start_at: localIso(20, 10, 5) }),
        schedule({ state: "stopped", next_start_at: localIso(20, 10, 5) }),
        schedule({ next_start_at: null }),
        later,
        today,
      ],
      now
    );
    expect(r).toEqual({
      groups: [
        { bucket: "Today", items: [today] },
        { bucket: "Tomorrow", items: [tomorrow] },
        { bucket: "Later", items: [later] },
      ],
      more: 0,
    });
  });

  it(`lists ${NEXT_STARTS_LIMIT} starts and counts the others`, () => {
    const many = Array.from({ length: 11 }, (_, i) =>
      schedule({ next_start_at: localIso(20, 11 + i) })
    );
    const r = nextStarts([...many].reverse(), now);
    expect(r.groups.flatMap((g) => g.items)).toEqual(many.slice(0, NEXT_STARTS_LIMIT));
    expect(r.more).toBe(3);
  });

  it("is empty without an active schedule", () => {
    expect(nextStarts([schedule({ state: "paused" })], now)).toEqual({ groups: [], more: 0 });
  });
});
