import { describe, expect, it } from "vitest";

import { formatInZone, startsIn, startTimeText } from "./start-times";

describe("formatInZone", () => {
  it.each<[string, string, string]>([
    ["2026-03-29T01:00:00Z", "Europe/Paris", "Sun 29 Mar 2026, 03:00 CEST (+02:00)"],
    ["2026-03-28T23:30:00Z", "Europe/Paris", "Sun 29 Mar 2026, 00:30 CET (+01:00)"],
    ["2026-07-01T06:00:00Z", "America/New_York", "Wed 1 Jul 2026, 02:00 EDT (-04:00)"],
    ["2026-07-01T06:00:00Z", "UTC", "Wed 1 Jul 2026, 06:00 UTC (+00:00)"],
    ["2026-07-01T06:00:00Z", "Asia/Kolkata", "Wed 1 Jul 2026, 11:30 UTC+05:30"],
  ])("%s in %s", (iso, zone, expected) => {
    expect(formatInZone(iso, zone)).toBe(expected);
  });

  it("throws RangeError for an unknown zone", () => {
    expect(() => formatInZone("2026-07-01T06:00:00Z", "Mars/Olympus")).toThrow(RangeError);
  });
});

describe("startTimeText", () => {
  it.each<[string, string, string, { zoned: string; local?: string }]>([
    ["same zone", "Europe/Paris", "Europe/Paris", { zoned: "Wed 1 Jul 2026, 08:00 CEST (+02:00)" }],
    [
      "another zone with the same time",
      "Europe/Paris",
      "Europe/Berlin",
      { zoned: "Wed 1 Jul 2026, 08:00 CEST (+02:00)" },
    ],
    [
      "another time",
      "Europe/Paris",
      "UTC",
      {
        zoned: "Wed 1 Jul 2026, 08:00 CEST (+02:00)",
        local: "Wed 1 Jul 2026, 06:00 UTC (+00:00)",
      },
    ],
    [
      "unknown schedule zone",
      "Mars/Olympus",
      "UTC",
      {
        zoned:
          "Wed 1 Jul 2026, 06:00 UTC (+00:00) (time zone Mars/Olympus unknown to this browser)",
        local: "Wed 1 Jul 2026, 06:00 UTC (+00:00)",
      },
    ],
    ["unknown viewer zone", "UTC", "Mars/Olympus", { zoned: "Wed 1 Jul 2026, 06:00 UTC (+00:00)" }],
  ])("%s", (_, zone, viewer, expected) => {
    expect(startTimeText("2026-07-01T06:00:00Z", zone, viewer)).toEqual(expected);
  });
});

describe("startsIn", () => {
  const now = Date.parse("2026-07-01T06:00:00Z");
  it.each<[number, string]>([
    [30, "in less than a minute"],
    [90, "in 2 minutes"],
    [300, "in 5 minutes"],
    [2 * 3600, "in 2 hours"],
    [3 * 86_400, "in 3 days"],
    [-30, "less than a minute ago"],
    [-120, "2 minutes ago"],
  ])("%i seconds", (seconds, expected) => {
    expect(startsIn(new Date(now + seconds * 1000).toISOString(), now)).toBe(expected);
  });
});
