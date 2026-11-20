import { describe, expect, it } from "vitest";

import type { SyncEvent } from "#/api/types";

import { DEFAULT_HISTORY_EVENT_TYPES, eventRunId, eventText } from "./schedule-events";

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
