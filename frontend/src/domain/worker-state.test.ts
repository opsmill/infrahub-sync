import { describe, expect, it } from "vitest";

import type { HealthReport } from "#/api/types";

import { workerState } from "./run-state";

const up = (name: HealthReport["dependencies"][number]["name"]) => ({
  name,
  status: "up" as const,
  error: "none" as const,
});

const report = (
  workers: Partial<HealthReport["workers"]>,
  overrides: Partial<HealthReport> = {}
): HealthReport => ({
  status: "healthy",
  dependencies: [up("database"), up("object_store"), up("task_manager"), up("task_manager_db")],
  workers: {
    state: "known",
    minimum: 1,
    live_workers: 1,
    queue_depth: 0,
    items: [],
    ...workers,
  },
  timestamp: "2026-11-23T10:15:09Z",
  ...overrides,
});

describe("workerState", () => {
  it("is unavailable when the work pool could not be read", () => {
    expect(
      workerState(
        report({ state: "unknown", live_workers: null, queue_depth: null }, { status: "unhealthy" })
      )
    ).toBe("unavailable");
  });

  it("is no-live-worker when no worker is live", () => {
    expect(workerState(report({ live_workers: 0, queue_depth: 2 }))).toBe("no-live-worker");
  });

  it("is busy when executions wait for a live worker", () => {
    expect(workerState(report({ live_workers: 2, queue_depth: 3 }))).toBe("busy");
  });

  it("is ready when a worker is live and nothing waits", () => {
    expect(workerState(report({ live_workers: 1, queue_depth: 0 }))).toBe("ready");
  });

  it("reads only the workers: a component down does not change the state", () => {
    const unhealthy = report(
      { live_workers: 1, queue_depth: 0 },
      {
        status: "unhealthy",
        dependencies: [
          { name: "database", status: "down", error: "timeout" },
          up("object_store"),
          up("task_manager"),
          up("task_manager_db"),
        ],
      }
    );
    expect(workerState(unhealthy)).toBe("ready");
  });
});
