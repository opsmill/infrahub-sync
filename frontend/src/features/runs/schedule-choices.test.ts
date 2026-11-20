import { describe, expect, it } from "vitest";

import { scheduleChoices, scheduleFilterText } from "./schedule-choices";

const NAMES = new Map([
  ["c-netbox", "from-netbox"],
  ["c-nautobot", "from-nautobot"],
]);

describe("scheduleChoices", () => {
  it("lists schedules that are not deleted by name, then deleted ones marked", () => {
    expect(
      scheduleChoices({
        live: [
          { schedule_id: "s-2", name: "nightly", config_id: "c-netbox" },
          { schedule_id: "s-1", name: "hourly", config_id: "c-nautobot" },
        ],
        deleted: [{ schedule_id: "s-3", name: "weekly", config_id: "c-netbox" }],
        configNames: NAMES,
      })
    ).toEqual([
      { id: "s-1", label: "hourly", detail: undefined },
      { id: "s-2", label: "nightly", detail: undefined },
      { id: "s-3", label: "weekly (deleted)", detail: undefined },
    ]);
  });

  it("names the configuration of each choice whose label another choice shares", () => {
    expect(
      scheduleChoices({
        live: [
          { schedule_id: "s-1", name: "nightly", config_id: "c-netbox" },
          { schedule_id: "s-2", name: "nightly", config_id: "c-nautobot" },
        ],
        deleted: [{ schedule_id: "s-3", name: "nightly", config_id: "c-netbox" }],
        configNames: NAMES,
      })
    ).toEqual([
      { id: "s-1", label: "nightly", detail: "from-netbox" },
      { id: "s-2", label: "nightly", detail: "from-nautobot" },
      { id: "s-3", label: "nightly (deleted)", detail: undefined },
    ]);
  });

  it("keeps a schedule once when both lists have it", () => {
    const s = { schedule_id: "s-1", name: "nightly", config_id: "c-netbox" };
    expect(scheduleChoices({ live: [s], deleted: [s], configNames: NAMES })).toEqual([
      { id: "s-1", label: "nightly", detail: undefined },
    ]);
  });

  it.each([
    ["not deleted", "active", "nightly"],
    ["deleted", "deleted", "nightly (deleted)"],
  ])("puts first the %s schedule of the address that no list has", (_, state, label) => {
    expect(
      scheduleChoices({
        live: [{ schedule_id: "s-1", name: "hourly", config_id: "c-netbox" }],
        deleted: [],
        selected: { schedule_id: "s-9", name: "nightly", config_id: "c-nautobot", state },
        configNames: NAMES,
      })
    ).toEqual([
      { id: "s-9", label, detail: undefined },
      { id: "s-1", label: "hourly", detail: undefined },
    ]);
  });
});

describe("scheduleFilterText", () => {
  it.each([
    [{ pending: true, notFound: false }, "Loading the schedule"],
    [{ pending: false, notFound: true }, "Schedule not found"],
    [{ pending: false, notFound: false }, "Schedule could not be read"],
  ])("says %o as %s, never the schedule identifier", (read, text) => {
    expect(scheduleFilterText(read)).toBe(text);
  });
});
