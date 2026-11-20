import { describe, expect, it } from "vitest";

import {
  CONFIGURATION_NAME_IN_USE,
  nameInUseOf,
  otherNamesake,
  sameNameHint,
  sameNameText,
} from "./package-review";

const envelope = (code: string, status: number, details?: Record<string, unknown> | null) => ({
  error: {
    code,
    status,
    message: "A configuration named 'from-netbox' already exists (cfg-1).",
    ...(details === undefined ? {} : { details }),
  },
});

describe("nameInUseOf", () => {
  it("names the configuration of a configuration-name-in-use refusal", () => {
    const error = envelope(CONFIGURATION_NAME_IN_USE, 409, {
      config_id: "cfg-1",
      name: "from-netbox",
    });
    expect(nameInUseOf(error)).toEqual({ configId: "cfg-1", name: "from-netbox" });
  });

  it("still reports the refusal when details are missing or not strings", () => {
    expect(nameInUseOf(envelope(CONFIGURATION_NAME_IN_USE, 409))).toEqual({});
    expect(nameInUseOf(envelope(CONFIGURATION_NAME_IN_USE, 409, null))).toEqual({});
    expect(
      nameInUseOf(envelope(CONFIGURATION_NAME_IN_USE, 409, { config_id: 7, name: "" }))
    ).toEqual({});
  });

  it.each([
    ["idempotency-conflict", 409],
    ["idempotency-in-progress", 409],
    ["forbidden", 403],
  ])("ignores the refusal %s (HTTP %i)", (code, status) => {
    expect(nameInUseOf(envelope(code, status, { config_id: "cfg-1", name: "x" }))).toBeUndefined();
  });

  it.each([
    null,
    undefined,
    new TypeError("Failed to fetch"),
  ])("ignores an error without an envelope: %s", (error) => {
    expect(nameInUseOf(error)).toBeUndefined();
  });
});

describe("otherNamesake", () => {
  const items = [
    { config_id: "cfg-1", name: "from-netbox" },
    { config_id: "cfg-2", name: "from-netbox" },
  ];

  it("returns any configuration with the name for a new configuration", () => {
    expect(otherNamesake(items, undefined)).toEqual({ configId: "cfg-1", name: "from-netbox" });
  });

  it("skips the configuration the version is added to", () => {
    expect(otherNamesake(items.slice(0, 1), "cfg-1")).toBeUndefined();
    expect(otherNamesake(items, "cfg-1")).toEqual({ configId: "cfg-2", name: "from-netbox" });
  });

  it("returns nothing while the configurations are not read", () => {
    expect(otherNamesake(undefined, undefined)).toBeUndefined();
    expect(otherNamesake([], undefined)).toBeUndefined();
  });
});

describe("the words of a name in use", () => {
  it("names the configuration when its name is known", () => {
    expect(sameNameText("from-netbox")).toBe("A configuration named from-netbox already exists.");
    expect(sameNameHint("from-netbox")).toBe(
      "Add the package as a new version of from-netbox instead."
    );
  });

  it("never shows a configuration identifier", () => {
    expect(sameNameText(undefined)).not.toContain("cfg-");
    expect(sameNameHint(undefined)).not.toContain("cfg-");
  });
});
