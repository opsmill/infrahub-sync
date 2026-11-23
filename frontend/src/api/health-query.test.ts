import { afterEach, describe, expect, it, vi } from "vitest";

// The typed client builds requests to the relative address `/api`, which Node cannot parse
// without a page. The client keeps the `Request` it finds when it is created, so the stub runs
// before the imports.
vi.hoisted(() => {
  const NodeRequest = globalThis.Request;
  globalThis.Request = class extends NodeRequest {
    constructor(input: RequestInfo | URL, init?: RequestInit) {
      super(typeof input === "string" ? new URL(input, "http://sync.test") : input, init);
    }
  };
});

import { SyncApiUnavailable } from "./errors";
import { healthQuery } from "./queries";

const report = {
  status: "unhealthy",
  dependencies: [{ name: "database", status: "down", error: "timeout" }],
  workers: { state: "known", minimum: 1, live_workers: 1, queue_depth: 0, items: [] },
  timestamp: "2026-11-23T10:15:09Z",
};

const answer = (status: number, body: string) =>
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () => new Response(body, { status, headers: { "Content-Type": "application/json" } })
    )
  );

const read = () => {
  const queryFn = healthQuery().queryFn as (context: { signal: AbortSignal }) => Promise<unknown>;
  return queryFn({ signal: new AbortController().signal });
};

describe("healthQuery", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("returns the report of a 200", async () => {
    const healthy = { ...report, status: "healthy" };
    answer(200, JSON.stringify(healthy));
    await expect(read()).resolves.toEqual(healthy);
  });

  it("returns the report of a 503, so a component outage keeps the worker information", async () => {
    answer(503, JSON.stringify(report));
    await expect(read()).resolves.toEqual(report);
  });

  it("fails on a 503 that carries the error envelope, as a failed status check did", async () => {
    const envelope = {
      error: { code: "service-unavailable", status: 503, message: "temporarily unavailable" },
    };
    answer(503, JSON.stringify(envelope));
    await expect(read()).rejects.toEqual(envelope);
  });

  it("fails on an answer that is neither a report nor an envelope", async () => {
    answer(502, "<html>Bad Gateway</html>");
    await expect(read()).rejects.toBeInstanceOf(SyncApiUnavailable);
  });
});
