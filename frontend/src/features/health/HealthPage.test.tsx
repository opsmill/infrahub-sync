// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchClient } from "#/api/client";
import type { HealthReport } from "#/api/types";

import { HealthPage } from "./HealthPage";

const report: HealthReport = {
  status: "healthy",
  dependencies: [{ name: "database", status: "up", error: "none" }],
  workers: {
    state: "known",
    minimum: 1,
    live_workers: 1,
    queue_depth: 0,
    items: [{ name: "worker-1", state: "live", last_heartbeat: "2026-11-23T10:15:00Z" }],
  },
  timestamp: "2026-11-23T10:15:09Z",
};

type Answer = Awaited<ReturnType<typeof fetchClient.GET>>;

/** The Sync API answers `GET /health` with the report. */
const answers = (get: ReturnType<typeof mockGet>) =>
  get.mockResolvedValueOnce({ data: report, response: new Response() } as Answer);

/** The request for `GET /health` does not reach the Sync API. */
const fails = (get: ReturnType<typeof mockGet>) =>
  get.mockRejectedValueOnce(new TypeError("Failed to fetch"));

const mockGet = () => vi.spyOn(fetchClient, "GET");

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <HealthPage />
    </QueryClientProvider>
  );
}

const press = (name: string) => fireEvent.click(screen.getByRole("button", { name }));

/** The report as the page draws it: the element around the board. */
const board = () => screen.getByText("Database").closest(".grayscale");

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("HealthPage", () => {
  it("draws the report in colour while the last read succeeded", async () => {
    answers(mockGet());
    renderPage();

    await screen.findByText("Database");
    expect(board()).toBeNull();
    expect(screen.queryByRole("note")).toBeNull();
  });

  it("keeps the last report, draws it grey and shows a notice when a refresh fails", async () => {
    const get = mockGet();
    answers(get);
    renderPage();
    await screen.findByText("Database");

    fails(get);
    press("Check again");

    const notice = await screen.findByRole("note");
    expect(notice.textContent).toContain("Last updated");
    expect(board()).not.toBeNull();
    expect(screen.getByRole("button", { name: "Try again" })).toBeTruthy();
  });

  it("reads the report again and removes the notice after Try again", async () => {
    const get = mockGet();
    answers(get);
    renderPage();
    await screen.findByText("Database");
    fails(get);
    press("Check again");
    await screen.findByRole("note");

    answers(get);
    press("Try again");

    await waitFor(() => expect(screen.queryByRole("note")).toBeNull());
    expect(get).toHaveBeenCalledTimes(3);
    expect(board()).toBeNull();
  });

  it("shows the error without a report when the first read fails, and reads again", async () => {
    const get = mockGet();
    fails(get);
    renderPage();

    await screen.findByRole("button", { name: "Try again" });
    expect(screen.queryByText("Database")).toBeNull();
    expect(screen.queryByRole("note")).toBeNull();

    answers(get);
    press("Try again");

    await screen.findByText("Database");
    expect(board()).toBeNull();
  });
});
