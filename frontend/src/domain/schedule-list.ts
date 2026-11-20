import type { Schedule } from "#/api/types";

import { MAX_CONSECUTIVE_FAILURES, stateView, stopReasonText } from "./schedules";

/* ---------- tabs ---------- */

/** The tabs of the schedules list; `all` is the default and is left out of the address. */
export const SCHEDULE_TABS = ["all", "attention", "active", "paused", "deleted"] as const;
export type ScheduleTab = (typeof SCHEDULE_TABS)[number];

export const TAB_LABEL: Record<ScheduleTab, string> = {
  all: "All",
  attention: "Needs action",
  active: "Active",
  paused: "Paused",
  deleted: "Deleted",
};

/* ---------- needs action ---------- */

/** Where the next step of a schedule that needs action leads. */
export type NextStepTarget = "schedule" | "runs";

export interface NeedsAction {
  /** Why the schedule needs a person, in a full sentence. */
  text: string;
  /** The words of the next step. */
  step: string;
  /** The schedule's page, which holds the action buttons, or the schedule's runs. */
  target: NextStepTarget;
}

/** "3 runs in a row failed. After 5, the schedule stops." */
export function failuresText(failures: number): string {
  return failures === 1
    ? `The last run failed. After ${MAX_CONSECUTIVE_FAILURES} failed runs in a row, the schedule stops.`
    : `${failures} runs in a row failed. After ${MAX_CONSECUTIVE_FAILURES}, the schedule stops.`;
}

/**
 * Why a person should act on this schedule now, or null: every stopped schedule, and every active
 * schedule whose last runs failed. A paused or deleted schedule never needs action.
 */
export function needsAction(s: Schedule): NeedsAction | null {
  if (s.state === "stopped") {
    const text = s.state_reason
      ? stopReasonText(s.state_reason, s.consecutive_failures)
      : "This schedule is stopped.";
    switch (s.state_reason) {
      case "owner-not-found":
        return { text, step: "Take over", target: "schedule" };
      case "approval-required":
        return { text, step: "Change the write target", target: "schedule" };
      default:
        // A stop reason the web interface does not know offers no action: open the schedule.
        return {
          text,
          step: stateView(s).panel?.action ? "Resume" : "Open",
          target: "schedule",
        };
    }
  }
  if (s.state === "active" && s.consecutive_failures > 0) {
    return { text: failuresText(s.consecutive_failures), step: "View the runs", target: "runs" };
  }
  return null;
}

/* ---------- sorting, search and grouping ---------- */

/** By name, as a person reads it: "Sync 2" before "Sync 10". */
export const byName = (a: Schedule, b: Schedule) =>
  a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: "base" });

/** Soonest next start first; a schedule without a next start comes last. */
export const byNextStart = (a: Schedule, b: Schedule) =>
  (a.next_start_at ? Date.parse(a.next_start_at) : Number.POSITIVE_INFINITY) -
  (b.next_start_at ? Date.parse(b.next_start_at) : Number.POSITIVE_INFINITY);

/**
 * Whether the schedule's name or its configuration's name contains the search text, ignoring case.
 * An empty search matches every schedule.
 */
export function matchesSearch(s: Schedule, search: string, configName: string): boolean {
  const needle = search.trim().toLowerCase();
  return (
    !needle || s.name.toLowerCase().includes(needle) || configName.toLowerCase().includes(needle)
  );
}

export interface ScheduleGroups {
  /** The schedules that are not deleted and match the search. */
  matches: Schedule[];
  /** Stopped schedules and failing active schedules, by name. */
  needsAction: Schedule[];
  /** Active schedules that need no action, by name: the "Active" group of the "All" tab. */
  activeResting: Schedule[];
  /** Every active schedule, failing ones included, by name: the "Active" tab. */
  active: Schedule[];
  paused: Schedule[];
}

/** The groups of the list, from schedules that are not deleted, after the search. */
export function groupSchedules(
  schedules: Schedule[],
  search: string,
  configName: (configId: string) => string
): ScheduleGroups {
  const matches = schedules
    .filter((s) => s.state !== "deleted" && matchesSearch(s, search, configName(s.config_id)))
    .sort(byName);
  const active = matches.filter((s) => s.state === "active");
  return {
    matches,
    needsAction: matches.filter((s) => needsAction(s) !== null),
    activeResting: active.filter((s) => needsAction(s) === null),
    active,
    paused: matches.filter((s) => s.state === "paused"),
  };
}

/** Which groups a tab shows. */
export function tabShows(tab: ScheduleTab): {
  needsAction: boolean;
  active: "resting" | "all" | false;
  paused: boolean;
  deleted: boolean;
} {
  return {
    needsAction: tab === "all" || tab === "attention",
    active: tab === "all" ? "resting" : tab === "active" ? "all" : false,
    paused: tab === "all" || tab === "paused",
    deleted: tab === "deleted",
  };
}

/** What a tab shows when it holds no schedule; `search` is the trimmed search text. */
export function emptyTabText(tab: ScheduleTab, search: string): string {
  const quoted = `“${search}”`;
  switch (tab) {
    case "attention":
      return search
        ? `No schedule that needs action matches ${quoted}.`
        : "No schedule needs action.";
    case "active":
      return search ? `No active schedule matches ${quoted}.` : "No schedule is active.";
    case "paused":
      return search ? `No paused schedule matches ${quoted}.` : "No schedule is paused.";
    case "deleted":
      return search ? `No deleted schedule matches ${quoted}.` : "No schedule is deleted.";
    default:
      return `No schedule or configuration matches ${quoted}.`;
  }
}

/* ---------- next starts ---------- */

export const START_BUCKETS = ["Today", "Tomorrow", "This week", "Later"] as const;
export type StartBucket = (typeof START_BUCKETS)[number];

/** The "Next starts" panel lists this many starts and counts the others. */
export const NEXT_STARTS_LIMIT = 8;

const DAY_MS = 86_400_000;

function startOfDay(t: number): number {
  const d = new Date(t);
  d.setHours(0, 0, 0, 0);
  return d.getTime();
}

/** The day of a start, in the viewer's time zone: Today, Tomorrow, within 7 days, or later. */
export function startBucket(iso: string, now: number): StartBucket {
  // Rounded, because a day across a daylight saving change lasts 23 or 25 hours.
  const days = Math.round((startOfDay(Date.parse(iso)) - startOfDay(now)) / DAY_MS);
  if (days <= 0) return "Today";
  if (days === 1) return "Tomorrow";
  return days < 7 ? "This week" : "Later";
}

export interface NextStarts {
  groups: { bucket: StartBucket; items: Schedule[] }[];
  /** The starts after the listed ones. */
  more: number;
}

/**
 * The next start of every active schedule, soonest first: the first `limit` grouped by day in the
 * viewer's time zone, and a count of the others.
 */
export function nextStarts(
  schedules: Schedule[],
  now: number,
  limit = NEXT_STARTS_LIMIT
): NextStarts {
  const starting = schedules
    .filter((s) => s.state === "active" && s.next_start_at)
    .sort(byNextStart);
  const shown = starting.slice(0, limit);
  const groups = START_BUCKETS.map((bucket) => ({
    bucket,
    items: shown.filter((s) => startBucket(s.next_start_at as string, now) === bucket),
  })).filter((g) => g.items.length > 0);
  return { groups, more: Math.max(0, starting.length - limit) };
}
