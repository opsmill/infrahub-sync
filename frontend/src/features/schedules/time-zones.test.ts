import { afterEach, describe, expect, it, vi } from "vitest";

import { browserTimeZone, timeZoneChoices } from "./time-zones";

const supported = Intl.supportedValuesOf;
const LISTED = ["Europe/Paris", "America/New_York", "Asia/Tokyo"];

afterEach(() => {
  Intl.supportedValuesOf = supported;
  vi.restoreAllMocks();
});

describe("timeZoneChoices", () => {
  it.each([
    ["no stored time zone", undefined, ["America/New_York", "Asia/Tokyo", "Europe/Paris", "UTC"]],
    [
      "a stored time zone already listed",
      "Europe/Paris",
      ["America/New_York", "Asia/Tokyo", "Europe/Paris", "UTC"],
    ],
    [
      "a stored alias the list leaves out",
      "Europe/Kiev",
      ["America/New_York", "Asia/Tokyo", "Europe/Kiev", "Europe/Paris", "UTC"],
    ],
    ["UTC stored", "UTC", ["America/New_York", "Asia/Tokyo", "Europe/Paris", "UTC"]],
  ])("adds UTC and the stored time zone, sorted: %s", (_, stored, expected) => {
    vi.spyOn(Intl, "supportedValuesOf").mockReturnValue(LISTED);
    expect(timeZoneChoices(stored)).toEqual(expected);
  });

  it("keeps UTC once when the browser lists it", () => {
    vi.spyOn(Intl, "supportedValuesOf").mockReturnValue(["UTC", "Europe/Paris"]);
    expect(timeZoneChoices("UTC")).toEqual(["Europe/Paris", "UTC"]);
  });

  it("returns null when the browser cannot list its time zones", () => {
    // @ts-expect-error: removed to stand for a browser without Intl.supportedValuesOf.
    Intl.supportedValuesOf = undefined;
    expect(timeZoneChoices("Europe/Paris")).toBeNull();
  });
});

describe("browserTimeZone", () => {
  const resolved = (timeZone: string) =>
    vi.spyOn(Intl.DateTimeFormat.prototype, "resolvedOptions").mockReturnValue({
      ...new Intl.DateTimeFormat().resolvedOptions(),
      timeZone,
    });

  it.each([
    ["Europe/Paris", "Europe/Paris"],
    ["", "UTC"],
  ])("returns %j as %j", (zone, expected) => {
    resolved(zone);
    expect(browserTimeZone()).toBe(expected);
  });
});
