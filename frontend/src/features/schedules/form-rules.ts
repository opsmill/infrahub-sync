import { isEnvelope } from "#/api/errors";
import { describeScheduleError } from "#/api/queries";
import { type ScheduleField, scheduleErrorField } from "#/domain/schedules";

/** A refusal shown under one field of the schedule form (data-model "Refusals mapped to a field"). */
export interface FieldRefusal {
  field: ScheduleField;
  code: string;
  title: string;
  detail?: string;
}

/**
 * The refusals of a write target show the Sync API message in place of the fixed detail, because
 * it says why this version or this Sync API refuses the target (spec Edge Cases).
 */
const MESSAGE_UNDER_FIELD = new Set(["approval-required-for-schedule", "schedule-branch-declared"]);

const capitalize = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);

/** The field a refusal belongs under, with its words; `null` for any other failure. */
export function fieldRefusal(error: unknown): FieldRefusal | null {
  if (!isEnvelope(error)) return null;
  const { code, message } = error.error;
  const field = scheduleErrorField(code);
  if (field === null) return null;
  const described = describeScheduleError(error);
  return {
    field,
    code,
    title: described.title,
    detail: MESSAGE_UNDER_FIELD.has(code) && message ? capitalize(message) : described.detail,
  };
}

/** The shortest and the longest late start the Sync API accepts, in minutes (1 minute to 7 days). */
export const LATE_START_MIN_MINUTES = 1;
export const LATE_START_MAX_MINUTES = 7 * 24 * 60;

/** The late-start limit typed in minutes, in seconds; `null` when it is not a whole number in range. */
export function lateStartSeconds(minutes: string): number | null {
  const text = minutes.trim();
  if (!/^\d+$/.test(text)) return null;
  const n = Number(text);
  if (n < LATE_START_MIN_MINUTES || n > LATE_START_MAX_MINUTES) return null;
  return n * 60;
}

/**
 * Why the late-start input cannot be saved; `undefined` when it can. `opening` is the limit the
 * change form opened with: there, an empty input keeps a schedule without a limit as it is,
 * because the Sync API reads an absent limit as "not changed" and cannot remove one.
 */
export function lateStartProblem(minutes: string, opening?: number | null): string | undefined {
  if (opening === null && minutes.trim() === "") return undefined;
  return lateStartSeconds(minutes) === null
    ? "Enter how late a missed start may begin."
    : undefined;
}

/** The text of the minutes input for a stored limit; whole minutes, as the input accepts. */
export function lateStartMinutesText(seconds: number | null): string {
  if (seconds === null) return "";
  return String(Math.max(LATE_START_MIN_MINUTES, Math.round(seconds / 60)));
}

/** The names the Sync API accepts: a letter or digit, then letters, digits, `.`, `_` or `-`; at most 128. */
const NAME_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;

export const NAME_HINT =
  "Letters, digits, dots, hyphens and underscores, starting with a letter or a digit. At most 128 characters.";

export function nameAccepted(name: string): boolean {
  return NAME_PATTERN.test(name.trim());
}

/** The fields of the schedule form that a problem can block, in the order the form checks them. */
export type ProblemField = "name" | "config" | "cron" | "timezone" | "late";

export interface FormProblem {
  field: ProblemField;
  text: string;
}

/** What the form knows when it checks its values before a save. */
export interface FormCheck {
  /** The change form shows the name as text: it is not checked. */
  changing: boolean;
  name: string;
  configId: string | undefined;
  version: number | undefined;
  /** The versions of a fixed configuration are still being read. */
  versionsPending: boolean;
  /** `null` while the cadence helper misses a value. */
  cron: string | null;
  timezone: string;
  /** `lateStartProblem` of the late-start input. */
  lateProblem: string | undefined;
}

/**
 * Why the form cannot be saved yet, one problem per field, in the order of the form. Empty when it
 * can be saved; the Sync API checks the rest (the cron expression, the time zone) on save.
 */
export function formProblems(check: FormCheck): FormProblem[] {
  const problems: FormProblem[] = [];
  if (!check.changing) {
    if (!check.name.trim()) problems.push({ field: "name", text: "Enter a name." });
    else if (!nameAccepted(check.name))
      problems.push({
        field: "name",
        text: "Change the name: it has a character the Sync API does not accept.",
      });
  }
  if (!check.configId) problems.push({ field: "config", text: "Choose a configuration version." });
  else if (check.version === undefined)
    problems.push({
      field: "config",
      text: check.versionsPending
        ? "Loading the versions of this configuration."
        : "This configuration has no registered version.",
    });
  if (check.cron === null) problems.push({ field: "cron", text: "Complete the cadence." });
  if (!check.timezone.trim()) problems.push({ field: "timezone", text: "Enter a time zone." });
  if (check.lateProblem) problems.push({ field: "late", text: check.lateProblem });
  return problems;
}
