import { afterEach, describe, expect, it, vi } from "vitest";

import { deleteToken, readToken, TOKEN_KEY, writeToken } from "./token";

describe("the stored token", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("reads nothing when no token is stored", () => {
    expect(readToken()).toBeNull();
  });

  it("stores a token under its key and reads it back", () => {
    writeToken("operator-token-0000001");

    expect(localStorage.getItem(TOKEN_KEY)).toBe("operator-token-0000001");
    expect(readToken()).toBe("operator-token-0000001");
  });

  it("deletes the token", () => {
    writeToken("operator-token-0000001");

    deleteToken();

    expect(localStorage.getItem(TOKEN_KEY)).toBeNull();
    expect(readToken()).toBeNull();
  });

  it("acts as if no token is stored when the browser blocks storage", () => {
    const blocked = () => {
      throw new DOMException("blocked", "SecurityError");
    };
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(blocked);
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(blocked);
    vi.spyOn(Storage.prototype, "removeItem").mockImplementation(blocked);

    expect(() => writeToken("operator-token-0000001")).not.toThrow();
    expect(() => deleteToken()).not.toThrow();
    expect(readToken()).toBeNull();
  });
});
