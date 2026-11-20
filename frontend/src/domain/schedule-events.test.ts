import { describe, expect, it } from "vitest";

import type { SyncEvent } from "#/api/types";

import {
  DEFAULT_HISTORY_EVENT_TYPES,
  eventRunId,
  eventText,
  historyNeedsReread,
  untilAfter,
  withNewestEvents,
} from "./schedule-events";

const S = "infrahub_sync.schedule.";

function event(over: Partial<SyncEvent> = {}): SyncEvent {
  return {
    event_id: "e-1",
    event_type: `${S}created`,
    occurred_at: "2026-11-20T10:00:00Z",
    actor: "alice",
    operation: "schedule-create",
    outcome: "accepted",
    related: [],
    ...over,
  };
}

/** A fixed formatter, so the texts do not depend on the machine's time zone or locale. */
const at = (iso: string) => `<${iso}>`;

describe("eventText", () => {
  it.each<[string, string, string, string]>([
    ["created", "accepted", "schedule-create", "Schedule created"],
    ["changed", "accepted", "schedule-change", "Settings changed"],
    ["paused", "accepted", "schedule-pause", "Paused"],
    ["resumed", "accepted", "schedule-resume", "Resumed"],
    ["deleted", "accepted", "schedule-delete", "Deleted"],
    ["taken_over", "accepted", "schedule-take-over", "Taken over"],
    ["replayed", "accepted", "schedule-pause", "Request repeated with the same retry key"],
    [
      "start_skipped",
      "skipped-run-in-progress",
      "schedule-start",
      "Start skipped: a run of this configuration was in progress",
    ],
    [
      "start_skipped",
      "skipped-approved-change-open",
      "schedule-start",
      "Start skipped: an approved proposed change of this schedule is not merged yet",
    ],
    [
      "start_skipped",
      "skipped-start-waiting",
      "schedule-start",
      "Start skipped: another start was already waiting",
    ],
    [
      "start_skipped",
      "skipped-approval-required",
      "schedule-start",
      "Start skipped: this Sync API requires an approved plan before a write to the default branch",
    ],
    ["start_queued", "queued", "schedule-start", "Start waiting for the run in progress"],
    [
      "start_missed",
      "missed-skipped",
      "schedule-start",
      "Start missed: the worker or the task manager was not running",
    ],
    [
      "start_missed",
      "missed-started-late",
      "schedule-start",
      "Started late: the worker or the task manager was not running at the start time",
    ],
    [
      "stopped",
      "owner-not-found",
      "schedule-start",
      "Stopped: The account that owns this schedule is disabled or deleted.",
    ],
    [
      "stopped",
      "approval-required",
      "schedule-start",
      "Stopped: This schedule writes to the default branch, and this Sync API requires an approved plan before every write to the default branch.",
    ],
    [
      "stopped",
      "repeated-failures",
      "sync",
      "Stopped: 5 runs in a row failed, were interrupted or ended with an unknown outcome.",
    ],
    ["proposed_change_opened", "opened", "sync", "Proposed change opened"],
    ["proposed_change_replaced", "replaced", "sync", "Earlier proposed change replaced"],
    ["proposed_change_kept", "kept-merged", "sync", "Earlier proposed change kept: it was merged"],
    ["proposed_change_kept", "kept-closed", "sync", "Earlier proposed change kept: it was closed"],
    [
      "proposed_change_kept",
      "kept-missing",
      "sync",
      "Earlier proposed change kept: it was deleted in Infrahub",
    ],
    [
      "proposed_change_kept",
      "kept-approved",
      "sync",
      "Earlier proposed change kept: it was approved",
    ],
    [
      "start_skipped",
      "skipped-new-reason",
      "schedule-start",
      `${S}start_skipped: skipped-new-reason`,
    ],
    ["stopped", "new-reason", "schedule-start", `${S}stopped: new-reason`],
  ])("%s / %s", (type, outcome, operation, text) => {
    expect(eventText(event({ event_type: `${S}${type}`, outcome, operation }), at)).toEqual({
      text,
    });
  });

  it.each([
    ["schedule-pause", "Request refused: pause the schedule"],
    ["schedule-take-over", "Request refused: take the schedule over"],
    ["schedule-rename", "Request refused: schedule-rename"],
  ])("refused %s", (operation, text) => {
    expect(
      eventText(event({ event_type: `${S}refused`, operation, outcome: "forbidden" }), at)
    ).toEqual({ text, detail: "forbidden" });
  });

  it.each([
    ["start_failed", "failed", "Start failed", "configuration-version-not-found"],
    ["proposed_change_failed", "failed", "Proposed change failed", "InfrahubConnectionError"],
  ])("%s carries its cause", (type, outcome, text, cause) => {
    expect(
      eventText(event({ event_type: `${S}${type}`, outcome, details: { cause } }), at)
    ).toEqual({ text, detail: cause });
  });

  it("names the start time", () => {
    expect(
      eventText(
        event({
          event_type: `${S}start_failed`,
          outcome: "failed",
          details: { start_at: "2026-11-20T02:00:00Z", cause: "orchestration-unavailable" },
        }),
        at
      )
    ).toEqual({
      text: "Start failed",
      detail: "For the start at <2026-11-20T02:00:00Z>. orchestration-unavailable",
    });
  });

  it("reads a grouped missed-start event", () => {
    expect(
      eventText(
        event({
          event_type: `${S}start_missed`,
          outcome: "missed-skipped",
          details: {
            starts: 812,
            first_start_at: "2026-11-01T00:00:00Z",
            last_start_at: "2026-11-03T19:35:00Z",
          },
        }),
        at
      )
    ).toEqual({
      text: "812 starts missed between <2026-11-01T00:00:00Z> and <2026-11-03T19:35:00Z>",
    });
  });

  it.each([
    ["run started", "infrahub_sync.run.accepted", "sync", "accepted", {}, { text: "Run started" }],
    [
      "run synced with records left out",
      "infrahub_sync.run.synced",
      "sync",
      "applied",
      { left_out: 3 },
      { text: "Run wrote its changes", detail: "3 records left out" },
    ],
    [
      "run synced",
      "infrahub_sync.run.synced",
      "sync",
      "applied",
      {},
      { text: "Run wrote its changes" },
    ],
    ["run failed", "infrahub_sync.run.failed", "sync", "failed", {}, { text: "Run failed" }],
    [
      "other run event",
      "infrahub_sync.run.cancelled",
      "cancel",
      "cancelled",
      {},
      { text: "cancel: cancelled" },
    ],
    [
      "unknown type",
      "infrahub_sync.config.created",
      "config-create",
      "accepted",
      {},
      { text: "infrahub_sync.config.created: accepted" },
    ],
    [
      "cause on a type that does not show it",
      `${S}created`,
      "schedule-create",
      "accepted",
      { cause: "ignored" },
      { text: "Schedule created" },
    ],
  ])("%s", (_name, event_type, operation, outcome, details, expected) => {
    expect(eventText(event({ event_type, operation, outcome, details }), at)).toEqual(expected);
  });
});

describe("eventText of a change", () => {
  const changed = (details: SyncEvent["details"]) =>
    eventText(
      event({
        event_type: `${S}changed`,
        operation: "schedule-change",
        outcome: "accepted",
        details,
      }),
      at
    );

  it("reads 'Settings changed' only for an event recorded without the changes", () => {
    expect(changed(null)).toEqual({ text: "Settings changed" });
    expect(changed(undefined)).toEqual({ text: "Settings changed" });
    expect(changed({ start_at: null, changes: null })).toEqual({ text: "Settings changed" });
  });

  it("says that no value changed when the list is empty", () => {
    expect(changed({ changes: [] })).toEqual({
      text: "Settings changed",
      detail: "No setting changed: each value sent was already set",
    });
  });

  it("writes one line per changed setting, in the order recorded", () => {
    expect(
      changed({
        changes: [
          { field: "registry_version", before: 2, after: 3 },
          { field: "cron", before: "30 6 * * *", after: "0 2 * * *" },
          { field: "timezone", before: "Europe/Paris", after: "UTC" },
          { field: "write_target", before: "branch-review", after: "default-branch" },
          { field: "write_when_left_out", before: false, after: true },
          { field: "overlap_policy", before: "reject", after: "queue" },
          { field: "start_late_by_at_most_seconds", before: 1800, after: 2700 },
        ],
      })
    ).toEqual({
      text: "Settings changed",
      changes: [
        "Version 2 → 3",
        "Cadence: every day at 06:30 → every day at 02:00",
        "Time zone: Europe/Paris → UTC",
        "Write target: branch review → default branch",
        "Records left out: not written → written",
        "When a run is in progress: skip the start → wait for it",
        "Start late by at most: 30 minutes → 45 minutes",
      ],
    });
  });

  it.each([
    [
      "a custom cadence as its expression",
      { field: "cron", before: "0 2 * * *", after: "*/7 3 * * *" },
      "Cadence: every day at 02:00 → */7 3 * * *",
    ],
    [
      "a monthly cadence without its note on short months",
      { field: "cron", before: "0 2 31 * *", after: "0 2 1 * *" },
      "Cadence: every month on day 31 at 02:00 → every month on day 1 at 02:00",
    ],
    [
      "an unknown setting as returned",
      { field: "colour", before: "red", after: "blue" },
      "colour: red → blue",
    ],
    [
      "a value of an unexpected type as returned",
      { field: "start_late_by_at_most_seconds", before: "soon", after: 60 },
      "start_late_by_at_most_seconds: soon → 60",
    ],
  ])("writes %s", (_name, change, line) => {
    expect(changed({ changes: [change] }).changes).toEqual([line]);
  });

  it("does not read changes on another event type", () => {
    expect(
      eventText(
        event({
          event_type: `${S}paused`,
          details: { changes: [{ field: "cron", before: "0 2 * * *", after: "0 3 * * *" }] },
        }),
        at
      )
    ).toEqual({ text: "Paused" });
  });
});

describe("eventRunId", () => {
  it("reads the run from the primary resource", () => {
    expect(
      eventRunId(
        event({
          event_type: "infrahub_sync.run.accepted",
          primary_resource: { kind: "run", id: "r-1" },
          related: [{ kind: "schedule", id: "s-1" }],
        })
      )
    ).toBe("r-1");
  });

  it("finds no run behind a schedule primary resource", () => {
    expect(
      eventRunId(event({ primary_resource: { kind: "schedule", id: "s-1" } }))
    ).toBeUndefined();
  });

  it.each([
    [
      [
        { kind: "schedule", id: "s-1" },
        { kind: "run", id: "r-9" },
      ],
      "r-9",
    ],
    [[{ kind: "schedule", id: "s-1" }], undefined],
    [[], undefined],
  ])("%j", (related, runId) => {
    expect(eventRunId(event({ related }))).toBe(runId);
  });
});

describe("DEFAULT_HISTORY_EVENT_TYPES", () => {
  it("holds every schedule type of the events contract except replayed", () => {
    expect(new Set(DEFAULT_HISTORY_EVENT_TYPES)).toEqual(
      new Set(
        [
          "created",
          "changed",
          "paused",
          "resumed",
          "deleted",
          "taken_over",
          "refused",
          "start_skipped",
          "start_queued",
          "start_missed",
          "start_failed",
          "stopped",
          "proposed_change_opened",
          "proposed_change_replaced",
          "proposed_change_kept",
          "proposed_change_failed",
        ].map((suffix) => `${S}${suffix}`)
      )
    );
    expect(DEFAULT_HISTORY_EVENT_TYPES).not.toContain(`${S}replayed`);
  });
});

describe("withNewestEvents", () => {
  const ids = (events: SyncEvent[]) => events.map((e) => e.event_id);
  const listed = [
    event({ event_id: "e-3" }),
    event({ event_id: "e-2" }),
    event({ event_id: "e-1" }),
  ];

  it("adds the events not listed yet above the list, newest first", () => {
    const newest = [event({ event_id: "e-5" }), event({ event_id: "e-4" }), ...listed.slice(0, 2)];
    expect(ids(withNewestEvents(newest, [listed]))).toEqual(["e-5", "e-4", "e-3", "e-2", "e-1"]);
  });

  it("returns the listed entries in order when every newest event is listed", () => {
    expect(ids(withNewestEvents(listed.slice(0, 2), [listed]))).toEqual(["e-3", "e-2", "e-1"]);
  });

  it("does not list an event twice when the list already holds it", () => {
    const newest = [event({ event_id: "e-4" }), event({ event_id: "e-1" })];
    expect(ids(withNewestEvents(newest, [listed]))).toEqual(["e-4", "e-3", "e-2", "e-1"]);
  });

  it("shows an event once when two loaded pages overlap", () => {
    const first = [event({ event_id: "e-5" }), event({ event_id: "e-4" })];
    const second = [event({ event_id: "e-4" }), event({ event_id: "e-3" })];
    const newest = [event({ event_id: "e-6" }), event({ event_id: "e-5" })];
    expect(ids(withNewestEvents(newest, [first, second]))).toEqual(["e-6", "e-5", "e-4", "e-3"]);
  });

  it("shows the newest events when nothing is listed", () => {
    const newest = [event({ event_id: "e-1" })];
    expect(ids(withNewestEvents(newest, []))).toEqual(["e-1"]);
  });
});

describe("untilAfter", () => {
  it("is one millisecond after the newest event", () => {
    expect(untilAfter(event({ occurred_at: "2026-11-20T10:00:00Z" }))).toBe(
      "2026-11-20T10:00:00.001Z"
    );
  });

  it("cuts microseconds to milliseconds, so the result is still after the event", () => {
    expect(untilAfter(event({ occurred_at: "2026-11-20T10:00:00.123999Z" }))).toBe(
      "2026-11-20T10:00:00.124Z"
    );
  });

  it("is null without an event", () => {
    expect(untilAfter(undefined)).toBeNull();
  });
});

describe("historyNeedsReread", () => {
  const pages = [[event({ event_id: "e-2" }), event({ event_id: "e-1" })]];

  it("is true when more events exist than the newest read holds and none is listed", () => {
    const newest = {
      items: [event({ event_id: "e-9" }), event({ event_id: "e-8" })],
      has_more: true,
    };
    expect(historyNeedsReread(newest, pages)).toBe(true);
  });

  it("is false when the newest read reaches a listed event", () => {
    const newest = {
      items: [event({ event_id: "e-3" }), event({ event_id: "e-2" })],
      has_more: true,
    };
    expect(historyNeedsReread(newest, pages)).toBe(false);
  });

  it("is false when the newest read holds every event", () => {
    const newest = { items: [event({ event_id: "e-9" })], has_more: false };
    expect(historyNeedsReread(newest, pages)).toBe(false);
  });
});
