import { describe, expect, it } from "vitest";

import { isHealthReport } from "./errors";

const worker = { name: "worker-1", state: "live", last_heartbeat: "2026-11-23T10:15:00Z" };
const workers = { state: "known", minimum: 1, live_workers: 1, queue_depth: 0, items: [worker] };
const report = {
  status: "healthy",
  dependencies: [{ name: "database", status: "up", error: "none" }],
  workers,
  timestamp: "2026-11-23T10:15:09Z",
};

describe("isHealthReport", () => {
  it("accepts a report with known workers", () => {
    expect(isHealthReport(report)).toBe(true);
  });

  it("accepts a report whose work pool could not be read", () => {
    const unknown = {
      state: "unknown",
      minimum: 1,
      live_workers: null,
      queue_depth: null,
      items: [],
    };
    expect(isHealthReport({ ...report, status: "unhealthy", workers: unknown })).toBe(true);
  });

  it.each([
    ["a status that is not healthy or unhealthy", { ...report, status: "degraded" }],
    ["dependencies that are not a list", { ...report, dependencies: {} }],
    ["a component that is not an object", { ...report, dependencies: ["database"] }],
    ["a component without a name", { ...report, dependencies: [{ status: "up", error: "none" }] }],
    [
      "a component without a status",
      { ...report, dependencies: [{ name: "database", error: "none" }] },
    ],
    ["a timestamp that is not text", { ...report, timestamp: 1 }],
    ["workers that are not an object", { ...report, workers: [] }],
    [
      "a worker state that is neither known nor unknown",
      { ...report, workers: { ...workers, state: "partial" } },
    ],
    ["a minimum that is not a number", { ...report, workers: { ...workers, minimum: "1" } }],
    [
      "a live worker count that is missing",
      { ...report, workers: { ...workers, live_workers: null } },
    ],
    [
      "a queue depth that is not a number",
      { ...report, workers: { ...workers, queue_depth: "0" } },
    ],
    ["workers items that are not a list", { ...report, workers: { ...workers, items: null } }],
    [
      "unknown workers without a list of workers",
      { ...report, status: "unhealthy", workers: { state: "unknown" } },
    ],
    [
      "a worker without a name",
      { ...report, workers: { ...workers, items: [{ ...worker, name: 1 }] } },
    ],
    [
      "a worker without a state",
      { ...report, workers: { ...workers, items: [{ ...worker, state: undefined }] } },
    ],
    [
      "a worker without a heartbeat",
      { ...report, workers: { ...workers, items: [{ ...worker, last_heartbeat: null }] } },
    ],
    ["a worker that is not an object", { ...report, workers: { ...workers, items: ["worker-1"] } }],
  ])("refuses a body with %s", (_case, body) => {
    expect(isHealthReport(body)).toBe(false);
  });
});
