import { describe, expect, it } from "vitest";

import type { Principal, Schedule } from "#/api/types";

import {
  changeRequest,
  createRequest,
  differsFromOpening,
  formValuesOf,
  lateStartLabel,
  OWNER_ONLY_TEXT,
  type ScheduleAction,
  scheduleActions,
  scheduleErrorField,
  stateView,
} from "./schedules";

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

const me = (actor: string, administrator = false): Principal => ({
  actor,
  administrator,
  approval_required: false,
});

const OWNER = me("alice");
const ADMIN = me("root", true);
const OTHER = me("bob");

type Row = [string, Principal | undefined, Partial<Schedule>, ScheduleAction, boolean, boolean];

describe("scheduleActions", () => {
  it.each<Row>([
    ["owner may change", OWNER, {}, "change", true, true],
    ["owner may delete", OWNER, {}, "delete", true, true],
    ["owner may pause an active schedule", OWNER, {}, "pause", true, true],
    ["owner sees no take-over", OWNER, {}, "take-over", false, false],
    ["administrator may change", ADMIN, {}, "change", true, true],
    ["administrator who is not owner may take over", ADMIN, {}, "take-over", true, true],
    [
      "administrator who is owner sees no take-over",
      me("alice", true),
      {},
      "take-over",
      false,
      false,
    ],
    ["other account sees change, unavailable", OTHER, {}, "change", true, false],
    ["other account sees pause, unavailable", OTHER, {}, "pause", true, false],
    ["other account sees no take-over", OTHER, {}, "take-over", false, false],
    ["me undefined: change available", undefined, {}, "change", true, true],
    ["me undefined: take-over available", undefined, {}, "take-over", true, true],
    ["me undefined: pause available when active", undefined, {}, "pause", true, true],
    ["me undefined: resume hidden when active", undefined, {}, "resume", false, false],
    ["deleted: no change", ADMIN, { state: "deleted" }, "change", false, false],
    ["deleted: no delete", OWNER, { state: "deleted" }, "delete", false, false],
    ["deleted: no take-over", ADMIN, { state: "deleted" }, "take-over", false, false],
    ["deleted: no resume", undefined, { state: "deleted" }, "resume", false, false],
    [
      "deleted owner: other account cannot change",
      OTHER,
      { owner: "Deleted account 3f2a9c" },
      "change",
      true,
      false,
    ],
    [
      "deleted owner: administrator may take over",
      ADMIN,
      { owner: "Deleted account 3f2a9c" },
      "take-over",
      true,
      true,
    ],
    ["pause hidden when paused", OWNER, { state: "paused" }, "pause", false, false],
    ["pause hidden when stopped", OWNER, { state: "stopped" }, "pause", false, false],
    ["resume when paused", OWNER, { state: "paused" }, "resume", true, true],
    ["resume when stopped", OWNER, { state: "stopped" }, "resume", true, true],
    ["resume hidden when active", OWNER, {}, "resume", false, false],
  ])("%s", (_name, account, over, action, shown, available) => {
    const access = scheduleActions(account, schedule(over))[action];
    expect(access.shown).toBe(shown);
    expect(access.available).toBe(available);
    expect(access.unavailableReason).toBe(shown && !available ? OWNER_ONLY_TEXT : undefined);
  });
});

describe("stateView", () => {
  it.each([
    ["active", null, 0, { label: "Active" }],
    [
      "paused",
      null,
      0,
      {
        label: "Paused",
        panel: { text: "This schedule starts no run until it is resumed.", offers: ["resume"] },
      },
    ],
    [
      "stopped",
      "owner-not-found",
      0,
      {
        label: "Stopped",
        panel: {
          text: "The account that owns this schedule is disabled or deleted.",
          action:
            "An administrator takes the schedule over, or enables the owner's account and resumes the schedule. A deleted account cannot be enabled again.",
          offers: ["take-over"],
        },
      },
    ],
    [
      "stopped",
      "approval-required",
      0,
      {
        label: "Stopped",
        panel: {
          text: "This schedule writes to the default branch, and this Sync API requires an approved plan before every write to the default branch.",
          action: "Change the write target to branch review. The schedule is then active again.",
          offers: ["change"],
        },
      },
    ],
    [
      "stopped",
      "repeated-failures",
      6,
      {
        label: "Stopped",
        panel: {
          text: "6 runs in a row failed, were interrupted or ended with an unknown outcome.",
          action: "Check the last runs of the schedule, then resume it.",
          offers: ["runs", "resume"],
        },
      },
    ],
    ["stopped", "disk-full", 0, { label: "Stopped", panel: { text: "disk-full", offers: [] } }],
    ["stopped", null, 0, { label: "Stopped" }],
    [
      "deleted",
      "anything",
      0,
      {
        label: "Deleted",
        panel: { text: "This schedule was deleted. Its runs are kept.", offers: [] },
      },
    ],
    ["archived", "x", 0, { label: "archived" }],
  ])("%s / %s", (state, reason, failures, expected) => {
    expect(
      stateView(schedule({ state, state_reason: reason, consecutive_failures: failures }))
    ).toEqual(expected);
  });
});

describe("lateStartLabel", () => {
  it.each([
    [null, "Not set"],
    [3600, "1 hour"],
    [5400, "1 hour 30 minutes"],
    [1800, "30 minutes"],
    [172_800, "2 days"],
    [60, "1 minute"],
  ])("%s → %s", (seconds, label) => {
    expect(lateStartLabel(seconds)).toBe(label);
  });
});

describe("changeRequest", () => {
  const opening = formValuesOf(schedule());

  it.each([
    ["no change", {}, "", null],
    ["reason only", {}, "because", null],
    ["one field", { cron: "0 3 * * *" }, "", { cron: "0 3 * * *" }],
    [
      "one field and a reason",
      { overlap_policy: "queue" },
      "  busy hours  ",
      { overlap_policy: "queue", reason: "busy hours" },
    ],
    [
      "clearing the late-start limit is not sent",
      { start_late_by_at_most_seconds: null },
      "",
      null,
    ],
  ])("%s", (_name, edit, reason, expected) => {
    expect(changeRequest(opening, { ...opening, ...edit }, reason)).toEqual(expected);
  });

  it("leaves out a field another person changed after the form opened", () => {
    const later = schedule({ timezone: "UTC" });
    const form = { ...formValuesOf(schedule()), cron: "0 4 * * *" };
    expect(differsFromOpening(opening, later)).toBe(true);
    expect(changeRequest(opening, form, "")).toEqual({ cron: "0 4 * * *" });
  });

  it("finds no difference in an unchanged read", () => {
    expect(differsFromOpening(opening, schedule())).toBe(false);
  });
});

describe("createRequest", () => {
  it("sends the reason only when not empty", () => {
    const form = formValuesOf(schedule());
    expect(createRequest(form, " nightly ", "c-1", " ")).toEqual({
      name: "nightly",
      config_id: "c-1",
      registry_version: 3,
      cron: "0 2 * * *",
      timezone: "Europe/Paris",
      write_target: "branch-review",
      write_when_left_out: false,
      overlap_policy: "reject",
      start_late_by_at_most_seconds: 3600,
    });
    expect(createRequest(form, "nightly", "c-1", "first").reason).toBe("first");
  });
});

describe("scheduleErrorField", () => {
  it.each([
    ["schedule-cron-invalid", "cron"],
    ["schedule-timezone-invalid", "timezone"],
    ["schedule-name-in-use", "name"],
    ["schedule-exists-for-configuration", "config_id"],
    ["approval-required-for-schedule", "write_target"],
    ["schedule-branch-declared", "write_target"],
    ["schedule-left-out-not-applicable", "write_when_left_out"],
    ["configuration-version-not-found", "registry_version"],
    ["forbidden", null],
    ["toString", null],
  ])("%s → %s", (code, field) => {
    expect(scheduleErrorField(code)).toBe(field);
  });
});
