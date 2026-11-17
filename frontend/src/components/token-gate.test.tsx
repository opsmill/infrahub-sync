import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { readToken, TOKEN_KEY, writeToken } from "#/api/token";
import { TokenGate } from "./token-gate";

const ACCEPTED = "operator-token-0000001";
const REFUSED = "wrong-token-0000000001";

/** Answers `GET /api/me` as the Sync API would: 200 for the accepted token, 401 otherwise. */
function answerMe(request: Request): Response {
  const accepted = request.headers.get("Authorization") === `Bearer ${ACCEPTED}`;
  if (accepted) {
    return Response.json({ actor: "operator", administrator: true, approval_required: false });
  }
  return Response.json(
    {
      error: { code: "unauthenticated", message: "a valid bearer token is required", status: 401 },
    },
    { status: 401 }
  );
}

describe("the token page", () => {
  let requests: Request[];

  beforeEach(() => {
    requests = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const request = input as Request;
      requests.push(request);
      return answerMe(request);
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("explains the refusal, names the setting, links to the documentation and offers a field", () => {
    render(<TokenGate storedTokenRefused={false} onChange={() => {}} />);

    expect(screen.getByRole("heading", { name: "A token is required" })).toBeInTheDocument();
    expect(screen.getByText("INFRAHUB_SYNC_SERVICE_ANONYMOUS_ACTOR")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open the web interface" })).toHaveAttribute(
      "href",
      "https://docs.infrahub.app/sync/compose-deployment#open-the-web-interface"
    );
    expect(screen.getByLabelText("Bearer token")).toHaveAttribute("type", "password");
    expect(screen.queryByRole("button", { name: "Forget token" })).not.toBeInTheDocument();
  });

  it("shows the refusal of a pasted token and does not store it", async () => {
    const onChange = vi.fn();
    render(<TokenGate storedTokenRefused={false} onChange={onChange} />);

    await userEvent.type(screen.getByLabelText("Bearer token"), REFUSED);
    await userEvent.click(screen.getByRole("button", { name: "Use token" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("The Sync API refused this token");
    expect(localStorage.getItem(TOKEN_KEY)).toBeNull();
    expect(onChange).not.toHaveBeenCalled();
  });

  it("checks a pasted token with the Sync API, then stores it", async () => {
    const onChange = vi.fn();
    render(<TokenGate storedTokenRefused={false} onChange={onChange} />);

    await userEvent.type(screen.getByLabelText("Bearer token"), ACCEPTED);
    await userEvent.click(screen.getByRole("button", { name: "Use token" }));

    await waitFor(() => expect(onChange).toHaveBeenCalledOnce());
    expect(readToken()).toBe(ACCEPTED);
    expect(new URL(requests[0].url).pathname).toBe("/api/me");
  });

  it("says a stored token was refused and offers to replace or forget it", async () => {
    writeToken(REFUSED);
    const onChange = vi.fn();
    render(<TokenGate storedTokenRefused onChange={onChange} />);

    expect(
      screen.getByRole("heading", { name: "The Sync API refused the stored token" })
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Replace" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Forget token" }));

    expect(readToken()).toBeNull();
    expect(onChange).toHaveBeenCalledOnce();
  });

  it("replaces a refused stored token with an accepted one", async () => {
    writeToken(REFUSED);
    render(<TokenGate storedTokenRefused onChange={() => {}} />);

    await userEvent.type(screen.getByLabelText("New bearer token"), ACCEPTED);
    await userEvent.click(screen.getByRole("button", { name: "Replace" }));

    await waitFor(() => expect(readToken()).toBe(ACCEPTED));
  });

  it("says the Sync API failed, not that it refused the token, for an error other than 401", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      Response.json(
        { error: { code: "internal-error", message: "the Sync API failed", status: 500 } },
        { status: 500 }
      )
    );
    const onChange = vi.fn();
    render(<TokenGate storedTokenRefused={false} onChange={onChange} />);

    await userEvent.type(screen.getByLabelText("Bearer token"), ACCEPTED);
    await userEvent.click(screen.getByRole("button", { name: "Use token" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("could not check this token");
    expect(alert).not.toHaveTextContent("refused");
    expect(readToken()).toBeNull();
    expect(onChange).not.toHaveBeenCalled();
  });

  it("says the Sync API did not answer and stores nothing when the request fails", async () => {
    vi.mocked(fetch).mockRejectedValueOnce(new TypeError("Failed to fetch"));
    const onChange = vi.fn();
    render(<TokenGate storedTokenRefused={false} onChange={onChange} />);

    await userEvent.type(screen.getByLabelText("Bearer token"), ACCEPTED);
    await userEvent.click(screen.getByRole("button", { name: "Use token" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("The Sync API did not answer");
    expect(readToken()).toBeNull();
    expect(onChange).not.toHaveBeenCalled();
  });

  it("says so when the browser blocks storage and an accepted token cannot be kept", async () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("blocked", "SecurityError");
    });
    const onChange = vi.fn();
    render(<TokenGate storedTokenRefused={false} onChange={onChange} />);

    await userEvent.type(screen.getByLabelText("Bearer token"), ACCEPTED);
    await userEvent.click(screen.getByRole("button", { name: "Use token" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("it cannot keep the token");
    expect(onChange).not.toHaveBeenCalled();
  });

  it("says so when the browser blocks storage and a token cannot be forgotten", async () => {
    writeToken(REFUSED);
    vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => {
      throw new DOMException("blocked", "SecurityError");
    });
    const onChange = vi.fn();
    render(<TokenGate storedTokenRefused onChange={onChange} />);

    await userEvent.click(screen.getByRole("button", { name: "Forget token" }));

    // The page itself is an alert while a stored token is refused, so find the message by its text.
    expect(await screen.findByText(/it could not forget the token/)).toHaveAttribute(
      "role",
      "alert"
    );
    expect(onChange).not.toHaveBeenCalled();
  });
});
