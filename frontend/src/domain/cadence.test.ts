import { describe, expect, it } from "vitest";

import { type CadenceForm, cadenceWords, cronFields, cronFromForm, formFromCron } from "./cadence";

describe("cadence forms, both directions", () => {
  it.each<[string, CadenceForm, string]>([
    ["0 2 * * *", { kind: "daily", hour: 2, minute: 0 }, "Every day at 02:00"],
    [
      "30 6 * * 1,4",
      { kind: "weekly", days: [1, 4], hour: 6, minute: 30 },
      "Every week on Monday and Thursday at 06:30",
    ],
    ["15 * * * *", { kind: "hourly", minute: 15 }, "Every hour at minute 15"],
    ["*/15 * * * *", { kind: "minutes", every: 15 }, "Every 15 minutes"],
    [
      "10 */4 * * *",
      { kind: "hours", every: 4, minute: 10 },
      "Every 4 hours, at minute 10, from 00:10",
    ],
    [
      "0 3 31 * *",
      { kind: "monthly", day: 31, hour: 3, minute: 0 },
      "Every month on day 31 at 03:00. Months without day 31 have no start.",
    ],
    [
      "0 3 28 * *",
      { kind: "monthly", day: 28, hour: 3, minute: 0 },
      "Every month on day 28 at 03:00",
    ],
    [
      "5 9 * * 1,3,5",
      { kind: "weekly", days: [1, 3, 5], hour: 9, minute: 5 },
      "Every week on Monday, Wednesday and Friday at 09:05",
    ],
  ])("%s", (cron, form, words) => {
    expect(formFromCron(cron)).toEqual(form);
    expect(cronFromForm(form)).toBe(cron);
    expect(cadenceWords(form)).toBe(words);
  });

  it("writes weekly days sorted and once each", () => {
    expect(cronFromForm({ kind: "weekly", days: [4, 1, 4], hour: 6, minute: 30 })).toBe(
      "30 6 * * 1,4"
    );
  });
});

describe("expressions that stay custom", () => {
  it.each([
    "*/7 * * * *",
    "0 */5 * * *",
    "*/15 9-17 * * 1-5",
    "0 2 * * 7",
    "0 2 * * 4,1",
    "0 2 * * 1,1",
    "0 2 1 1 *",
    "00 2 * * *",
    "0 24 * * *",
    "0  2 * * *",
    "0 2 * *",
    "",
  ])("%j", (cron) => {
    const form = formFromCron(cron);
    expect(form).toEqual({ kind: "custom", cron });
    expect(cronFromForm(form)).toBe(cron);
    expect(cadenceWords(form)).toBeNull();
  });
});

describe("cronFields", () => {
  it("labels the five fields", () => {
    expect(cronFields("*/15 9-17 * * 1-5")).toEqual([
      { label: "minute", value: "*/15" },
      { label: "hour", value: "9-17" },
      { label: "day of month", value: "*" },
      { label: "month", value: "*" },
      { label: "day of week", value: "1-5" },
    ]);
  });

  it.each(["0 2 * *", "0 0 2 * * *", "", "   "])("%j → null", (cron) => {
    expect(cronFields(cron)).toBeNull();
  });
});
