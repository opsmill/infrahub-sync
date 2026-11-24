import { describe, expect, it } from "vitest";

import {
  componentLabel,
  type Dependency,
  enoughWorkers,
  errorText,
  staleCount,
  type Worker,
  type Workers,
  workerKey,
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

describe("workerKey", () => {
  it("differs for two workers that share the name and the heartbeat", () => {
    const other = worker("other", "stale");
    expect(workerKey(other, 0)).not.toBe(workerKey(other, 1));
  });
});
