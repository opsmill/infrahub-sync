/**
 * Start times of a schedule in words: in the schedule's time zone with its offset, and in the
 * viewer's time zone when that reads differently (data-model "Cadence preview").
 */

const DATE_PARTS: Intl.DateTimeFormatOptions = {
  weekday: "short",
  day: "numeric",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
};

/** A zone name that is only an offset ("GMT+2") is not an abbreviation worth showing. */
const OFFSET_NAME = /^(GMT|UTC)[+−-]/;

function part(parts: Intl.DateTimeFormatPart[], type: Intl.DateTimeFormatPartTypes): string {
  return parts.find((p) => p.type === type)?.value ?? "";
}

/** "GMT+2" → "+02:00", "GMT+5:30" → "+05:30", "GMT" → "+00:00". */
function offsetOf(date: Date, timeZone: string): string {
  const name = part(
    new Intl.DateTimeFormat("en-GB", { timeZone, timeZoneName: "shortOffset" }).formatToParts(date),
    "timeZoneName"
  );
  const match = /([+−-])(\d{1,2})(?::(\d{2}))?$/.exec(name);
  if (!match) return "+00:00";
  const sign = match[1] === "+" ? "+" : "-";
  return `${sign}${match[2].padStart(2, "0")}:${match[3] ?? "00"}`;
}

/** The zone's abbreviation ("CEST", "EDT", "UTC"), or null when the browser knows none. */
function abbreviationOf(date: Date, timeZone: string): string | null {
  for (const locale of ["en-GB", "en-US"]) {
    const name = part(
      new Intl.DateTimeFormat(locale, { timeZone, timeZoneName: "short" }).formatToParts(date),
      "timeZoneName"
    );
    if (name && !OFFSET_NAME.test(name)) return name;
  }
  return null;
}

/**
 * "Sun 29 Mar 2026, 03:00 CEST (+02:00)", or "Sun 29 Mar 2026, 08:30 UTC+05:30" for a zone
 * without an abbreviation. Throws `RangeError` for a zone the browser does not know.
 */
export function formatInZone(iso: string, timeZone: string): string {
  const date = new Date(iso);
  const parts = new Intl.DateTimeFormat("en-GB", { ...DATE_PARTS, timeZone }).formatToParts(date);
  const day = `${part(parts, "weekday")} ${part(parts, "day")} ${part(parts, "month")} ${part(parts, "year")}`;
  const time = `${part(parts, "hour")}:${part(parts, "minute")}`;
  const offset = offsetOf(date, timeZone);
  const abbreviation = abbreviationOf(date, timeZone);
  const zone = abbreviation ? `${abbreviation} (${offset})` : `UTC${offset}`;
  return `${day}, ${time} ${zone}`;
}

export interface StartTimeText {
  /** The time in the schedule's time zone. */
  zoned: string;
  /** The time in the viewer's time zone; absent when it reads the same as `zoned`. */
  local?: string;
}

/**
 * A start time in the schedule's time zone and, when it reads differently, in the viewer's. A
 * schedule zone the browser does not know is shown in UTC with its name, so the time stays readable.
 */
export function startTimeText(
  iso: string,
  timeZone: string,
  viewerTimeZone: string
): StartTimeText {
  let zoned: string;
  try {
    zoned = formatInZone(iso, timeZone);
  } catch (error) {
    if (!(error instanceof RangeError)) throw error;
    zoned = `${formatInZone(iso, "UTC")} (time zone ${timeZone} unknown to this browser)`;
  }
  let local: string | undefined;
  try {
    local = formatInZone(iso, viewerTimeZone);
  } catch (error) {
    if (!(error instanceof RangeError)) throw error;
    local = undefined;
  }
  return local === undefined || local === zoned ? { zoned } : { zoned, local };
}

/**
 * The time a schedule meant to start a run, on one line: in the schedule's time zone, followed by
 * the viewer's time when it reads differently. Before the schedule is read, its time zone is not
 * known, and the time is given in the viewer's time zone.
 */
export function scheduledForText(
  iso: string,
  scheduleTimeZone: string | undefined,
  viewer: string
): string {
  const text = startTimeText(iso, scheduleTimeZone ?? viewer, viewer);
  return text.local ? `${text.zoned} (your time: ${text.local})` : text.zoned;
}

/** The viewer's time zone, or `UTC` when the browser does not give one. */
export function viewerTimeZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
}

const relativeFormat = new Intl.RelativeTimeFormat("en", { numeric: "auto" });

const UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ["year", 365 * 24 * 60 * 60],
  ["month", 30 * 24 * 60 * 60],
  ["week", 7 * 24 * 60 * 60],
  ["day", 24 * 60 * 60],
  ["hour", 60 * 60],
  ["minute", 60],
];

/**
 * "in 3 minutes", "in 2 days", "in less than a minute", "1 minute ago". Unlike `formatRelative`,
 * a start a minute or two ahead is not "just now": a schedule that starts every 5 minutes is often
 * that close to its next start.
 */
export function startsIn(iso: string, now: number): string {
  const seconds = (new Date(iso).getTime() - now) / 1000;
  if (seconds >= 0 && seconds < 60) return "in less than a minute";
  if (seconds < 0 && seconds > -60) return "less than a minute ago";
  for (const [unit, size] of UNITS) {
    if (Math.abs(seconds) >= size) return relativeFormat.format(Math.round(seconds / size), unit);
  }
  return "in less than a minute";
}
