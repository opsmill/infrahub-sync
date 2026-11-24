import { describe, expect, it } from "vitest";

import { cronFromForm } from "#/domain/cadence";

import { type CadenceDraft, draftOf, formOfDraft, PROBLEMS } from "./cadence-draft";

const cronOf = (draft: CadenceDraft) => {
  const result = formOfDraft(draft);
  return result.form ? cronFromForm(result.form) : null;
};

describe("draftOf and formOfDraft", () => {
  it.each([
    ["0 2 * * *", "daily"],
    ["30 6 * * 1,4", "weekly"],
    ["15 * * * *", "hourly"],
    ["*/15 * * * *", "minutes"],
    ["10 */4 * * *", "hours"],
    ["0 3 31 * *", "monthly"],
    ["*/15 9-17 * * 1-5", "custom"],
    ["0 2 * * 7", "custom"],
  ])("opens %j on the form %s and writes it back unchanged", (cron, kind) => {
    const draft = draftOf(cron);
    expect(draft.kind).toBe(kind);
    expect(cronOf(draft)).toBe(cron);
  });

  it("opens an empty expression on every day at 02:00", () => {
    const draft = draftOf("");
    expect(draft.kind).toBe("daily");
    expect(cronOf(draft)).toBe("0 2 * * *");
  });

  it("keeps the custom expression of a built form, for a switch to Custom", () => {
    expect(draftOf("30 6 * * 1,4").custom).toBe("30 6 * * 1,4");
  });

  it("builds the expressions of the spec's scenarios", () => {
    const base = draftOf("");
    expect(cronOf({ ...base, kind: "weekly", days: [4, 1], time: "06:30" })).toBe("30 6 * * 1,4");
    expect(cronOf({ ...base, kind: "minutes", everyMinutes: 15 })).toBe("*/15 * * * *");
    expect(cronOf({ ...base, kind: "hours", everyHours: 4, minute: "10" })).toBe("10 */4 * * *");
    expect(cronOf({ ...base, kind: "daily", time: "02:00:00" })).toBe("0 2 * * *");
  });

  it.each<[Partial<CadenceDraft>, keyof typeof PROBLEMS]>([
    [{ kind: "hourly", minute: "" }, "minute"],
    [{ kind: "hourly", minute: "60" }, "minute"],
    [{ kind: "hours", minute: "-1" }, "minute"],
    [{ kind: "daily", time: "" }, "time"],
    [{ kind: "weekly", days: [] }, "days"],
    [{ kind: "monthly", day: "0" }, "day"],
    [{ kind: "monthly", day: "32" }, "day"],
    [{ kind: "monthly", day: "" }, "day"],
    [{ kind: "custom", custom: "  " }, "custom"],
  ])("refuses %j with the problem of the %s input", (patch, input) => {
    const result = formOfDraft({ ...draftOf(""), ...patch });
    expect(result.form).toBeUndefined();
    expect(result.input).toBe(input);
    expect(result.problem).toBe(PROBLEMS[input]);
  });

  it("passes a custom expression on as typed, trimmed, without checking it", () => {
    const result = formOfDraft({ ...draftOf(""), kind: "custom", custom: " 61 * * * " });
    expect(result.form).toEqual({ kind: "custom", cron: "61 * * *" });
  });
});
