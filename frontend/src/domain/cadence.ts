/**
 * The cadence helper (data-model "Cadence helper forms"): builds a cron expression from a form
 * and reads an expression back into a form. It never computes start times; the Sync API does.
 */

export const MINUTE_INTERVALS = [5, 10, 15, 20, 30] as const;
export const HOUR_INTERVALS = [2, 3, 4, 6, 8, 12] as const;

export type MinuteInterval = (typeof MINUTE_INTERVALS)[number];
export type HourInterval = (typeof HOUR_INTERVALS)[number];

/** Days of the week as cron numbers them: 0 = Sunday … 6 = Saturday. */
export const DAY_NAMES = [
  "Sunday",
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
] as const;

export type CadenceForm =
  | { kind: "minutes"; every: MinuteInterval }
  | { kind: "hourly"; minute: number }
  | { kind: "hours"; every: HourInterval; minute: number }
  | { kind: "daily"; hour: number; minute: number }
  | { kind: "weekly"; days: number[]; hour: number; minute: number }
  | { kind: "monthly"; day: number; hour: number; minute: number }
  | { kind: "custom"; cron: string };

/** Sorted, without repeats: the order the helper writes days in. */
function sortedDays(days: number[]): number[] {
  return [...new Set(days)].sort((a, b) => a - b);
}

/** The cron expression of a form. A weekly form needs at least one day; the form enforces it. */
export function cronFromForm(form: CadenceForm): string {
  switch (form.kind) {
    case "minutes":
      return `*/${form.every} * * * *`;
    case "hourly":
      return `${form.minute} * * * *`;
    case "hours":
      return `${form.minute} */${form.every} * * *`;
    case "daily":
      return `${form.minute} ${form.hour} * * *`;
    case "weekly":
      return `${form.minute} ${form.hour} * * ${sortedDays(form.days).join(",")}`;
    case "monthly":
      return `${form.minute} ${form.hour} ${form.day} * *`;
    case "custom":
      return form.cron;
  }
}

/** A plain decimal integer in `[min, max]`, without sign or leading zero; otherwise `null`. */
function plain(text: string, min: number, max: number): number | null {
  if (!/^(0|[1-9]\d*)$/.test(text)) return null;
  const n = Number(text);
  return n >= min && n <= max ? n : null;
}

function candidate(fields: string[]): CadenceForm | null {
  const [m, h, dom, mon, dow] = fields;
  if (mon !== "*") return null;
  const step = (text: string) => (text.startsWith("*/") ? plain(text.slice(2), 1, 59) : null);
  if (h === "*" && dom === "*" && dow === "*") {
    const every = step(m);
    if (every !== null) {
      const interval = MINUTE_INTERVALS.find((n) => n === every);
      return interval === undefined ? null : { kind: "minutes", every: interval };
    }
    const minute = plain(m, 0, 59);
    return minute === null ? null : { kind: "hourly", minute };
  }
  const minute = plain(m, 0, 59);
  if (minute === null) return null;
  if (dom === "*" && dow === "*") {
    const every = step(h);
    if (every !== null) {
      const interval = HOUR_INTERVALS.find((n) => n === every);
      return interval === undefined ? null : { kind: "hours", every: interval, minute };
    }
  }
  const hour = plain(h, 0, 23);
  if (hour === null) return null;
  if (dom === "*" && dow === "*") return { kind: "daily", hour, minute };
  if (dom === "*") {
    const days = dow.split(",").map((d) => plain(d, 0, 6));
    if (days.some((d) => d === null)) return null;
    return { kind: "weekly", days: days as number[], hour, minute };
  }
  if (dow === "*") {
    const day = plain(dom, 1, 31);
    return day === null ? null : { kind: "monthly", day, hour, minute };
  }
  return null;
}

/**
 * The form of an expression. Only the exact patterns of the table are recognized: a form is
 * returned only when it writes the expression back unchanged, so the helper never rewrites what
 * the person typed. Everything else is `custom`.
 */
export function formFromCron(cron: string): CadenceForm {
  const custom: CadenceForm = { kind: "custom", cron };
  const fields = cron.split(" ");
  if (fields.length !== 5) return custom;
  const form = candidate(fields);
  return form !== null && cronFromForm(form) === cron ? form : custom;
}

const two = (n: number) => String(n).padStart(2, "0");

function dayList(days: number[]): string {
  const names = sortedDays(days).map((d) => DAY_NAMES[d] ?? String(d));
  if (names.length <= 1) return names.join("");
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

/** The form in words; `null` for a custom expression, which gets no words (research R7). */
export function cadenceWords(form: CadenceForm): string | null {
  switch (form.kind) {
    case "minutes":
      return `Every ${form.every} minutes`;
    case "hourly":
      return `Every hour at minute ${form.minute}`;
    case "hours":
      return `Every ${form.every} hours, at minute ${form.minute}, from 00:${two(form.minute)}`;
    case "daily":
      return `Every day at ${two(form.hour)}:${two(form.minute)}`;
    case "weekly":
      return `Every week on ${dayList(form.days)} at ${two(form.hour)}:${two(form.minute)}`;
    case "monthly": {
      const words = `Every month on day ${form.day} at ${two(form.hour)}:${two(form.minute)}`;
      return form.day > 28 ? `${words}. Months without day ${form.day} have no start.` : words;
    }
    case "custom":
      return null;
  }
}

export const CRON_FIELD_LABELS = [
  "minute",
  "hour",
  "day of month",
  "month",
  "day of week",
] as const;

export interface CronField {
  label: (typeof CRON_FIELD_LABELS)[number];
  value: string;
}

/** The five fields of an expression with their labels; `null` when it does not have five fields. */
export function cronFields(cron: string): CronField[] | null {
  const values = cron.trim().split(/\s+/);
  if (values.length !== 5 || values[0] === "") return null;
  return CRON_FIELD_LABELS.map((label, i) => ({ label, value: values[i] }));
}
