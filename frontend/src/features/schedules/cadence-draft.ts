import {
  type CadenceForm,
  formFromCron,
  type HourInterval,
  type MinuteInterval,
} from "#/domain/cadence";

/**
 * What the cadence field holds while the person edits it: the chosen form and the text of every
 * input, kept when the person switches forms, so switching back keeps what they entered.
 */
export type CadenceKind = CadenceForm["kind"];

export interface CadenceDraft {
  kind: CadenceKind;
  everyMinutes: MinuteInterval;
  everyHours: HourInterval;
  /** The minute of the hour of "Every hour" and "Every N hours". */
  minute: string;
  /** "HH:MM", as a time input gives it, for "Every day", "Every week" and "Every month". */
  time: string;
  /** Cron day numbers: 0 = Sunday … 6 = Saturday. */
  days: number[];
  /** The day of the month of "Every month". */
  day: string;
  /** The expression of "Custom". */
  custom: string;
}

export const KIND_LABELS: Record<CadenceKind, string> = {
  minutes: "Every N minutes",
  hourly: "Every hour",
  hours: "Every N hours",
  daily: "Every day",
  weekly: "Every week",
  monthly: "Every month",
  custom: "Custom",
};

const two = (n: number) => String(n).padStart(2, "0");

/** The form a new schedule starts with (tasks T035): every day at 02:00. */
const DEFAULT_DRAFT: CadenceDraft = {
  kind: "daily",
  everyMinutes: 15,
  everyHours: 2,
  minute: "0",
  time: "02:00",
  days: [1],
  day: "1",
  custom: "",
};

/**
 * The draft of an expression: the form `formFromCron` chooses, with its values in the inputs. An
 * empty expression gives "Every day" at 02:00.
 */
export function draftOf(cron: string): CadenceDraft {
  if (cron.trim() === "") return { ...DEFAULT_DRAFT, custom: "0 2 * * *" };
  const form = formFromCron(cron);
  const draft: CadenceDraft = { ...DEFAULT_DRAFT, kind: form.kind, custom: cron };
  switch (form.kind) {
    case "minutes":
      return { ...draft, everyMinutes: form.every };
    case "hourly":
      return { ...draft, minute: String(form.minute) };
    case "hours":
      return { ...draft, everyHours: form.every, minute: String(form.minute) };
    case "daily":
      return { ...draft, time: `${two(form.hour)}:${two(form.minute)}` };
    case "weekly":
      return { ...draft, days: form.days, time: `${two(form.hour)}:${two(form.minute)}` };
    case "monthly":
      return { ...draft, day: String(form.day), time: `${two(form.hour)}:${two(form.minute)}` };
    case "custom":
      return draft;
  }
}

/** An integer typed into a number input, within `[min, max]`; otherwise `null`. */
function wholeNumber(text: string, min: number, max: number): number | null {
  if (!/^\d{1,2}$/.test(text.trim())) return null;
  const n = Number(text.trim());
  return n >= min && n <= max ? n : null;
}

/** The hour and minute of a time input's value ("HH:MM", possibly with seconds). */
function timeOf(text: string): { hour: number; minute: number } | null {
  const match = /^(\d{2}):(\d{2})(:\d{2})?$/.exec(text);
  if (!match) return null;
  const hour = Number(match[1]);
  const minute = Number(match[2]);
  return hour <= 23 && minute <= 59 ? { hour, minute } : null;
}

export const PROBLEMS = {
  minute: "Enter a minute from 0 to 59.",
  time: "Enter a time.",
  days: "Choose at least one day.",
  day: "Enter a day of the month from 1 to 31.",
  custom: "Enter a cron expression.",
} as const;

export type DraftResult =
  | { form: CadenceForm; problem?: undefined; input?: undefined }
  | { form?: undefined; problem: string; input: keyof typeof PROBLEMS };

/**
 * The form a draft describes, or what is missing for the helper to write a cron expression. A
 * custom expression is passed on as typed; the Sync API checks it.
 */
export function formOfDraft(draft: CadenceDraft): DraftResult {
  const missing = (input: keyof typeof PROBLEMS): DraftResult => ({
    problem: PROBLEMS[input],
    input,
  });
  switch (draft.kind) {
    case "minutes":
      return { form: { kind: "minutes", every: draft.everyMinutes } };
    case "hourly":
    case "hours": {
      const minute = wholeNumber(draft.minute, 0, 59);
      if (minute === null) return missing("minute");
      return draft.kind === "hourly"
        ? { form: { kind: "hourly", minute } }
        : { form: { kind: "hours", every: draft.everyHours, minute } };
    }
    case "daily":
    case "weekly":
    case "monthly": {
      const time = timeOf(draft.time);
      if (time === null) return missing("time");
      if (draft.kind === "daily") return { form: { kind: "daily", ...time } };
      if (draft.kind === "weekly") {
        if (draft.days.length === 0) return missing("days");
        return { form: { kind: "weekly", days: draft.days, ...time } };
      }
      const day = wholeNumber(draft.day, 1, 31);
      if (day === null) return missing("day");
      return { form: { kind: "monthly", day, ...time } };
    }
    case "custom": {
      const cron = draft.custom.trim();
      return cron === "" ? missing("custom") : { form: { kind: "custom", cron } };
    }
  }
}
