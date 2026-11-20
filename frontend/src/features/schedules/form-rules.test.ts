import { describe, expect, it } from "vitest";

import {
  fieldRefusal,
  lateStartMinutesText,
  lateStartProblem,
  lateStartSeconds,
  nameAccepted,
} from "./form-rules";

const envelope = (code: string, status: number, message = "the sync api message") => ({
  error: { code, status, message },
});

describe("fieldRefusal", () => {
  it.each([
    ["schedule-cron-invalid", 422, "cron"],
    ["schedule-timezone-invalid", 422, "timezone"],
    ["schedule-name-in-use", 409, "name"],
    ["schedule-exists-for-configuration", 409, "config_id"],
    ["approval-required-for-schedule", 409, "write_target"],
    ["schedule-branch-declared", 409, "write_target"],
    ["schedule-left-out-not-applicable", 422, "write_when_left_out"],
    ["configuration-version-not-found", 404, "registry_version"],
  ])("puts %s (HTTP %i) under the field %s", (code, status, field) => {
    expect(fieldRefusal(envelope(code, status))?.field).toBe(field);
  });

  it.each([
    "approval-required-for-schedule",
    "schedule-branch-declared",
  ])("shows the Sync API message for the write target refusal %s", (code) => {
    expect(fieldRefusal(envelope(code, 409))?.detail).toBe("The sync api message");
  });

  it.each([
    ["schedule-cron-invalid", "The sync api message"],
    ["schedule-name-in-use", "Choose another name."],
  ])("keeps the detail of describeScheduleError for %s", (code, detail) => {
    expect(fieldRefusal(envelope(code, 409))?.detail).toBe(detail);
  });

  it("does not show the schedule identifier that the schedule-exists-for-configuration message holds", () => {
    const refusal = fieldRefusal({
      error: {
        code: "schedule-exists-for-configuration",
        status: 409,
        message:
          "the configuration 'c-netbox' already has the schedule 'nightly' (s-3f2a9c0d1e2b4a5c6d7e8f9a0b1c2d3e); a configuration has one schedule",
      },
    });
    expect(refusal?.field).toBe("config_id");
    expect(`${refusal?.title} ${refusal?.detail}`).not.toContain("s-3f2a9c0d");
  });

  it.each([
    ["secret-parameter-refused", envelope("secret-parameter-refused", 422)],
    ["forbidden", envelope("forbidden", 403)],
    ["a failure without an answer", new TypeError("failed")],
  ])("leaves %s to the error above the form's actions", (_, error) => {
    expect(fieldRefusal(error)).toBeNull();
  });
});

describe("lateStartSeconds", () => {
  it.each([
    ["60", 3600],
    ["1", 60],
    [" 10080 ", 604800],
    ["0", null],
    ["10081", null],
    ["", null],
    ["1.5", null],
    ["-5", null],
  ])("reads %j minutes as %j seconds", (minutes, seconds) => {
    expect(lateStartSeconds(minutes)).toBe(seconds);
  });
});

describe("lateStartProblem", () => {
  it.each([
    ["a valid limit in the create form", "60", undefined, undefined],
    [
      "an empty input in the create form",
      "",
      undefined,
      "Enter how late a missed start may begin.",
    ],
    ["an empty input for a schedule without a limit", "", null, undefined],
    [
      "an empty input for a schedule with a limit",
      "",
      3600,
      "Enter how late a missed start may begin.",
    ],
    [
      "an invalid limit for a schedule without a limit",
      "0",
      null,
      "Enter how late a missed start may begin.",
    ],
  ])("%s", (_name, text, opening, expected) => {
    expect(lateStartProblem(text, opening)).toBe(expected);
  });
});

describe("lateStartMinutesText", () => {
  it.each([
    [3600, "60"],
    [90, "2"],
    [null, ""],
  ])("writes %j seconds as %j", (seconds, text) => {
    expect(lateStartMinutesText(seconds)).toBe(text);
  });
});

describe("nameAccepted", () => {
  it.each([
    ["nightly-sync", true],
    ["Nightly.sync_2", true],
    ["-nightly", false],
    ["nightly sync", false],
    ["", false],
    ["a".repeat(129), false],
  ])("accepts %j: %s", (name, accepted) => {
    expect(nameAccepted(name)).toBe(accepted);
  });
});
