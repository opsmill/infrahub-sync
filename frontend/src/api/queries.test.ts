import { describe, expect, it, vi } from "vitest";

import { DEFAULT_HISTORY_EVENT_TYPES } from "#/domain/schedule-events";

import { fetchClient } from "./client";

import {
  describeScheduleError,
  nextStartsQuery,
  runPagesQuery,
  type ScheduleEventPage,
  scheduleEventsQuery,
  scheduleNewestEventsQuery,
} from "./queries";

const envelope = (code: string, status: number, message = "the sync api message") => ({
  error: { code, status, message },
});

describe("describeScheduleError", () => {
  it.each([
    ["forbidden", 403, "Only the owner of this schedule or an administrator can do this"],
    ["schedule-not-found", 404, "Schedule not found"],
    ["schedule-deleted", 409, "This schedule is deleted"],
    ["schedule-changed", 409, "Another change was saved at the same time. Save again."],
    ["schedule-name-in-use", 409, "A schedule with this name exists"],
    [
      "approval-required-for-schedule",
      409,
      "This Sync service requires an approved plan before any write",
    ],
    [
      "schedule-branch-declared",
      409,
      "The configuration version declares its own destination branch",
    ],
    [
      "schedule-left-out-not-applicable",
      422,
      "Writing when records are left out applies only to the default branch",
    ],
    ["configuration-version-not-found", 404, "No such version of this configuration"],
    ["schedule-exists-for-configuration", 409, "This configuration already has a schedule"],
    [
      "orchestration-unavailable",
      503,
      "The Sync service saved the schedule, but the task manager did not confirm the change",
    ],
    [
      "secret-parameter-refused",
      422,
      "A value in this form matches a secret the Sync service holds",
    ],
  ])("%s has its own title and detail, not the Sync API message", (code, status, title) => {
    const described = describeScheduleError(envelope(code, status));
    expect(described.title).toBe(title);
    expect(described.detail).toBeDefined();
    expect(described.detail).not.toContain("sync api message");
    expect(described.code).toBe(code);
  });

  it.each([
    ["schedule-cron-invalid", 422, "The Sync service refused the cron expression"],
    ["schedule-timezone-invalid", 422, "The Sync service does not know this time zone"],
    ["schedule-stopped", 409, "This schedule cannot be resumed yet"],
    ["request-invalid", 422, "The Sync service refused the values in this form"],
  ])("%s shows the Sync API message as its detail", (code, status, title) => {
    const described = describeScheduleError(envelope(code, status));
    expect(described.title).toBe(title);
    expect(described.detail).toBe("The sync api message");
  });

  it.each([
    ["schedule-not-found", 404, true],
    ["schedule-deleted", 409, false],
    ["forbidden", 403, false],
  ])("%s (HTTP %i) sets notFound to %s", (code, status, notFound) => {
    expect(describeScheduleError(envelope(code, status)).notFound).toBe(notFound);
  });

  it("falls back to describeError for an unknown code", () => {
    const described = describeScheduleError(envelope("something-new", 409, "something happened"));
    expect(described.title).toBe("Something happened");
    expect(described.code).toBe("something-new");
  });

  it("falls back to describeError for an answer that is not an envelope", () => {
    expect(describeScheduleError(new TypeError("failed")).kind).toBe("no-answer");
  });
});

describe("nextStartsQuery", () => {
  it.each([
    ["0 2 * * *", true],
    ["*/15 9-17 * * 1-5", true],
    ["0 2 * *", false],
    ["0 2 * * * *", false],
    ["", false],
  ])("asks for %j only when it has five fields: %s", (cron, enabled) => {
    expect(nextStartsQuery(cron, "UTC", 5).enabled).toBe(enabled);
  });

  it("keys the query by the cron expression, the time zone and the count", () => {
    expect(nextStartsQuery("0 2 * * *", "Europe/Paris", 5).queryKey).toEqual([
      "post",
      "/schedules/next-starts",
      { cron: "0 2 * * *", timezone: "Europe/Paris", count: 5 },
    ]);
  });
});

describe("scheduleEventsQuery", () => {
  const page = (count: number, hasMore: boolean, until: string): ScheduleEventPage =>
    ({
      items: Array.from({ length: count }, (_, i) => ({ event_id: `e${i}` })),
      limit: 50,
      offset: 0,
      has_more: hasMore,
      until,
    }) as unknown as ScheduleEventPage;

  const nextParam = (pages: ScheduleEventPage[]) => {
    const options = scheduleEventsQuery("sched_1");
    return options.getNextPageParam(pages[pages.length - 1], pages, undefined as never, []);
  };

  it("asks for the next page at the count of entries loaded, with the until of the first page", () => {
    const pages = [
      page(50, true, "2026-11-20T10:00:00.000Z"),
      page(50, true, "2026-11-20T10:05:00.000Z"),
    ];
    expect(nextParam(pages)).toEqual({ offset: 100, until: "2026-11-20T10:00:00.000Z" });
  });

  it("stops after a page without more entries", () => {
    expect(nextParam([page(12, false, "2026-11-20T10:00:00.000Z")])).toBeUndefined();
  });

  const requestOf = async (
    includeAll: boolean,
    pageParam: { offset: number; until: string | null },
    items: unknown[] = []
  ): Promise<{ path: string; query: Record<string, unknown>; page: ScheduleEventPage }> => {
    const get = vi.spyOn(fetchClient, "GET").mockResolvedValue({
      data: { items, limit: 50, offset: 0, has_more: items.length > 0 },
      error: undefined,
      response: new Response(),
    } as never);
    try {
      const options = scheduleEventsQuery("sched_1", { includeAll });
      const queryFn = options.queryFn as (context: unknown) => Promise<ScheduleEventPage>;
      const page = await queryFn({ pageParam, signal: new AbortController().signal });
      expect(get).toHaveBeenCalledTimes(1);
      const [path, init] = get.mock.calls[0] as [
        string,
        { params: { query: Record<string, unknown> } },
      ];
      return { path, query: init.params.query, page };
    } finally {
      get.mockRestore();
    }
  };

  it.each([
    [false, { event_type: DEFAULT_HISTORY_EVENT_TYPES }],
    [true, {}],
  ])("with includeAll %s, asks for the schedule events newest first", async (includeAll, types) => {
    const { path, query } = await requestOf(includeAll, {
      offset: 50,
      until: "2026-11-20T10:00:00.000Z",
    });
    expect(path).toBe("/events");
    expect(query).toEqual({
      related: ["schedule:sched_1"],
      order: "desc",
      until: "2026-11-20T10:00:00.000Z",
      limit: "50",
      offset: "50",
      ...types,
    });
  });

  it("the first page is read without until and fixes it after its newest event", async () => {
    const newestEvent = { event_id: "e-2", occurred_at: "2026-11-20T10:00:00.123456Z" };
    const { query, page } = await requestOf(false, { offset: 0, until: null }, [newestEvent]);
    expect(query).not.toHaveProperty("until");
    expect(page.until).toBe("2026-11-20T10:00:00.124Z");
  });

  it("an older page keeps the until of the first page", async () => {
    const until = "2026-11-20T10:00:00.124Z";
    const { page } = await requestOf(false, { offset: 50, until }, [
      { event_id: "e-1", occurred_at: "2026-11-20T09:00:00Z" },
    ]);
    expect(page.until).toBe(until);
  });

  it("an empty first page has no until", async () => {
    const { page } = await requestOf(false, { offset: 0, until: null });
    expect(page.until).toBeNull();
  });

  it("the default history types leave out the replayed event", () => {
    expect(DEFAULT_HISTORY_EVENT_TYPES).not.toContain("infrahub_sync.schedule.replayed");
  });
});

describe("scheduleNewestEventsQuery", () => {
  it.each([
    [false, { event_type: DEFAULT_HISTORY_EVENT_TYPES }],
    [true, {}],
  ])("with includeAll %s, asks for the newest events without until", async (includeAll, types) => {
    const get = vi.spyOn(fetchClient, "GET").mockResolvedValue({
      data: { items: [], limit: 50, offset: 0, has_more: false },
      error: undefined,
      response: new Response(),
    } as never);
    try {
      const options = scheduleNewestEventsQuery("sched_1", { includeAll });
      const queryFn = options.queryFn as (context: unknown) => Promise<unknown>;
      await queryFn({ signal: new AbortController().signal });
      const [path, init] = get.mock.calls[0] as [
        string,
        { params: { query: Record<string, unknown> } },
      ];
      expect(path).toBe("/events");
      expect(init.params.query).toEqual({
        related: ["schedule:sched_1"],
        order: "desc",
        limit: "50",
        offset: "0",
        ...types,
      });
    } finally {
      get.mockRestore();
    }
  });

  it("is kept apart from the paged history", () => {
    expect(scheduleNewestEventsQuery("sched_1").queryKey).not.toEqual(
      scheduleEventsQuery("sched_1").queryKey
    );
  });
});

describe("runPagesQuery", () => {
  const queryOf = async (config?: string, schedule?: string) => {
    const get = vi.spyOn(fetchClient, "GET").mockResolvedValue({
      data: { items: [], next_cursor: null },
      error: undefined,
      response: new Response(),
    } as never);
    try {
      const options = runPagesQuery(config, schedule);
      const queryFn = options.queryFn as (context: unknown) => Promise<unknown>;
      await queryFn({ pageParam: null, signal: new AbortController().signal });
      const [path, init] = get.mock.calls[0] as [
        string,
        { params: { query: Record<string, unknown> } },
      ];
      return { path, query: init.params.query, key: options.queryKey };
    } finally {
      get.mockRestore();
    }
  };

  it.each([
    [undefined, undefined, { limit: "50" }],
    ["netbox", undefined, { limit: "50", config_id: "netbox" }],
    [undefined, "sched_1", { limit: "50", schedule_id: "sched_1" }],
    ["netbox", "sched_1", { limit: "50", config_id: "netbox", schedule_id: "sched_1" }],
  ])("with config %s and schedule %s, sends %o", async (config, schedule, expected) => {
    const { path, query } = await queryOf(config, schedule);
    expect(path).toBe("/runs");
    expect(query).toEqual(expected);
  });

  it("keys the query by the schedule", () => {
    expect(runPagesQuery(undefined, "sched_1").queryKey).not.toEqual(
      runPagesQuery(undefined, "sched_2").queryKey
    );
    expect(runPagesQuery("netbox").queryKey).toEqual([
      "get",
      "/runs",
      "pages",
      { config: "netbox", schedule: undefined },
    ]);
  });
});
