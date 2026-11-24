import { describe, expect, it } from "vitest";

import {
  componentLabel,
  type Dependency,
  enoughWorkers,
  errorText,
  lastReadFailed,
  staleCount,
  type Worker,
  type Workers,
  workerKeys,
} from "./health";

const workers = (overrides: Partial<Workers>): Workers => ({
  state: "known",
  minimum: 1,
  live_workers: 1,
  queue_depth: 0,
  items: [],
  ...overrides,
});

const worker = (name: string, state: Worker["state"]): Worker => ({
  name,
  state,
  last_heartbeat: "2026-11-24T10:00:00Z",
});

describe("componentLabel", () => {
  it.each([
    ["database", "Database"],
    ["object_store", "Object store"],
    ["task_manager", "Task manager"],
    ["task_manager_db", "Task manager database"],
  ])("names %s", (name, label) => {
    expect(componentLabel(name)).toBe(label);
  });

  it.each(["cache", "toString"])("keeps the name %s that the page does not know", (name) => {
    expect(componentLabel(name)).toBe(name);
  });
});

describe("errorText", () => {
  const down = (error: Dependency["error"]): Dependency => ({
    name: "database",
    status: "down",
    error,
  });

  it("says that a timed-out check did not answer in time", () => {
    expect(errorText(down("timeout"))).toBe("Did not answer within the time limit.");
  });

  it("falls back to a general sentence for a category the page does not know", () => {
    expect(errorText(down("constructor" as Dependency["error"]))).toBe("The check failed.");
  });
});

describe("enoughWorkers", () => {
  it.each([
    [1, 1, true],
    [3, 1, true],
    [1, 3, false],
    [0, 1, false],
    [0, 0, true],
  ])("with %i live and %i required is %s", (live, minimum, expected) => {
    expect(enoughWorkers(workers({ live_workers: live, minimum }))).toBe(expected);
  });

  it("is null when the workers could not be read", () => {
    expect(enoughWorkers(workers({ state: "unknown", live_workers: null }))).toBeNull();
  });
});

describe("staleCount", () => {
  it("counts only the stale workers", () => {
    const items = [worker("a", "live"), worker("b", "stale"), worker("c", "stale")];
    expect(staleCount(workers({ items }))).toBe(2);
  });
});

describe("workerKeys", () => {
  it("differ for two workers that share the name and the heartbeat", () => {
    const other = worker("other", "stale");
    const keys = workerKeys([other, other]);
    expect(keys[0]).not.toBe(keys[1]);
  });

  it("keep the key of a worker whose heartbeat changes", () => {
    const before = worker("a", "live");
    const after = { ...before, last_heartbeat: "2026-11-24T10:00:30Z" };
    expect(workerKeys([after])).toEqual(workerKeys([before]));
  });

  it("keep the key of a worker that moves in the list", () => {
    const a = worker("a", "live");
    const b = worker("b", "live");
    expect(workerKeys([b, a])).toEqual(workerKeys([a, b]).reverse());
  });
});

describe("lastReadFailed", () => {
  it("is false while the first read runs", () => {
    expect(lastReadFailed({ errorUpdatedAt: 0, dataUpdatedAt: 0 }, false)).toBe(false);
  });

  it("is true when the first read failed", () => {
    expect(lastReadFailed({ errorUpdatedAt: 5, dataUpdatedAt: 0 }, true)).toBe(true);
  });

  it("is true when a read failed after the report", () => {
    expect(lastReadFailed({ errorUpdatedAt: 9, dataUpdatedAt: 5 }, true)).toBe(true);
  });

  it("is false when the report is newer than the failure", () => {
    expect(lastReadFailed({ errorUpdatedAt: 5, dataUpdatedAt: 9 }, true)).toBe(false);
  });

  it("is false when the page opens while a failed first read is tried again", () => {
    // The failure is newer than the missing report, but the page has no error to show.
    expect(lastReadFailed({ errorUpdatedAt: 5, dataUpdatedAt: 0 }, false)).toBe(false);
  });
});
