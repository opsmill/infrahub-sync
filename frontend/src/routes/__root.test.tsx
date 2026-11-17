import { QueryClientProvider } from "@tanstack/react-query";
import { createMemoryHistory, createRouter, RouterProvider } from "@tanstack/react-router";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { queryClient } from "#/api/client";
import { meQuery } from "#/api/queries";
import { TOKEN_KEY, writeToken } from "#/api/token";
import { routeTree } from "#/routeTree.gen";

// The development tools render in tests, where they have no panel to mount into.
vi.mock("@tanstack/react-devtools", () => ({ TanStackDevtools: () => null }));

const TOKEN = "operator-token-0000001";

const ANSWERS: Record<string, unknown> = {
  "/api/me": { actor: "operator", administrator: false, approval_required: false },
  "/api/status": {
    service: "ready",
    worker: { state: "ready", detail_available: false, observed_at: null },
  },
  "/api/version": { server_version: "3.0.0", api_versions: ["v3-unstable"], stability: "unstable" },
};

const UNAUTHENTICATED = {
  error: { code: "unauthenticated", message: "a valid bearer token is required", status: 401 },
};

/** Paths whose next answers are refusals, as `[status, body]`, before the usual answer. */
let refusals: Record<string, [number, unknown][]>;

function answer(request: Request): Response {
  const path = new URL(request.url).pathname;
  const refusal = refusals[path]?.shift();
  if (refusal) return Response.json(refusal[1], { status: refusal[0] });
  const body = ANSWERS[path];
  if (body) return Response.json(body);
  return Response.json(
    { error: { code: "not-found", message: "no Sync API route matches this path", status: 404 } },
    { status: 404 }
  );
}

function renderApp() {
  const router = createRouter({
    routeTree,
    context: { queryClient },
    history: createMemoryHistory({ initialEntries: ["/runs"] }),
  });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  );
}

describe("the top bar and the stored token", () => {
  let requests: Request[];

  const meRequests = () => requests.filter((r) => new URL(r.url).pathname === "/api/me");

  beforeEach(() => {
    requests = [];
    refusals = {};
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const request = input as Request;
      requests.push(request);
      return answer(request);
    });
  });

  afterEach(() => {
    queryClient.clear();
    vi.restoreAllMocks();
  });

  async function openServiceDetails() {
    await userEvent.click(
      await screen.findByRole("button", { name: /operator\. Open Sync service details/ })
    );
  }

  it("offers Forget token while a token is stored, and stops sending it once forgotten", async () => {
    writeToken(TOKEN);
    renderApp();
    await waitFor(() => expect(meRequests().length).toBeGreaterThan(0));
    expect(meRequests()[0].headers.get("Authorization")).toBe(`Bearer ${TOKEN}`);

    await openServiceDetails();
    await userEvent.click(await screen.findByRole("button", { name: "Forget token" }));

    expect(localStorage.getItem(TOKEN_KEY)).toBeNull();
    await waitFor(() => expect(meRequests().length).toBeGreaterThan(1));
    expect(meRequests().at(-1)?.headers.get("Authorization")).toBeNull();
  });

  it("does not offer Forget token when no token is stored", async () => {
    renderApp();

    await openServiceDetails();

    expect(await screen.findByText("Requests sent as")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Forget token" })).not.toBeInTheDocument();
  });

  it("reloads its data when another tab changes the stored token", async () => {
    renderApp();
    await waitFor(() => expect(meRequests()).toHaveLength(1));

    localStorage.setItem(TOKEN_KEY, TOKEN);
    window.dispatchEvent(new StorageEvent("storage", { key: TOKEN_KEY, newValue: TOKEN }));

    await waitFor(() => expect(meRequests()).toHaveLength(2));
    expect(meRequests()[1].headers.get("Authorization")).toBe(`Bearer ${TOKEN}`);
  });

  it("ignores a storage event for another key", async () => {
    renderApp();
    await waitFor(() => expect(meRequests()).toHaveLength(1));

    window.dispatchEvent(
      new StorageEvent("storage", { key: "infrahub-sync.theme", newValue: "dark" })
    );
    window.dispatchEvent(new StorageEvent("storage", { key: TOKEN_KEY, newValue: TOKEN }));

    // Only the token event reloads, so exactly one more request follows.
    await waitFor(() => expect(meRequests()).toHaveLength(2));
    expect(meRequests()).toHaveLength(2);
  });
});

describe("the token page in place of the page", () => {
  let requests: Request[];

  beforeEach(() => {
    requests = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const request = input as Request;
      requests.push(request);
      return answer(request);
    });
  });

  afterEach(() => {
    queryClient.clear();
    vi.restoreAllMocks();
  });

  it("shows the token page on a 401, then the page once an accepted token is stored", async () => {
    refusals = { "/api/me": [[401, UNAUTHENTICATED]] };
    renderApp();

    expect(await screen.findByRole("heading", { name: "A token is required" })).toBeInTheDocument();

    await userEvent.type(screen.getByLabelText("Bearer token"), TOKEN);
    await userEvent.click(screen.getByRole("button", { name: "Use token" }));

    expect(
      await screen.findByRole("button", { name: /operator\. Open Sync service details/ })
    ).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "A token is required" })).not.toBeInTheDocument();
    expect(requests.at(-1)?.headers.get("Authorization")).toBe(`Bearer ${TOKEN}`);
  });

  it("says a stored token was refused and offers to forget it", async () => {
    writeToken(TOKEN);
    refusals = { "/api/me": [[401, UNAUTHENTICATED]] };
    renderApp();

    expect(
      await screen.findByRole("heading", { name: "The Sync API refused the stored token" })
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Forget token" })).toBeInTheDocument();
  });

  it("does not show the token page for a cross-origin refusal", async () => {
    const crossOrigin = {
      error: { code: "cross-origin-refused", message: "another origin", status: 403 },
    };
    refusals = { "/api/me": Array.from({ length: 5 }, () => [403, crossOrigin]) };
    renderApp();

    await waitFor(() =>
      expect(queryClient.getQueryState(meQuery().queryKey)?.status).toBe("error")
    );
    expect(screen.queryByRole("heading", { name: "A token is required" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Bearer token")).not.toBeInTheDocument();
  });
});
