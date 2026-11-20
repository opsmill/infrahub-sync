import type { Run, Schedule } from "#/api/types";

import { phaseLabel, type RunState, runState } from "./run-state";
import { failuresText, START_BUCKETS, type StartBucket, startBucket } from "./schedule-list";
import { type ActionAccess, type ScheduleAction, stateView } from "./schedules";
import { startsIn } from "./start-times";

/* ---------- status card ---------- */

/** The tint of the status card, as the cards of the schedules list: red, amber or neutral. */
export type StatusTone = "neutral" | "warning" | "danger";

/** The icon of the status card. */
export type StatusIcon = "active" | "failing" | "stopped" | "resting";

/** The dialog a step of the status card opens. */
export type StepDialog = "take-over" | "change" | "resume" | "change-version";

/** A step of the status card: a dialog of the page, or the runs of the schedule. */
export type StatusStep = { label: string; dialog: StepDialog } | { label: string; runs: true };

export interface ScheduleStatus {
  tone: StatusTone;
  icon: StatusIcon;
  /** The situation in one sentence. */
  text: string;
  /** Further sentences: what to do, the reason of a pause, the next start of a failing schedule. */
  details: string[];
  /** The next step, shown as the main button. */
  step?: StatusStep;
  /** A second step, shown as a quieter button. */
  second?: StatusStep;
}

/** A fact the status card adds below its sentence. */
export interface StatusNote {
  kind: "not-handed-over" | "newer-version";
  text: string;
  detail?: string;
  /** "Change version", when the newer version is not already the main step. */
  step?: StatusStep;
}

export const NOT_HANDED_OVER =
  "This schedule starts no run until the Sync API hands it to the task manager. Restarting the Sync API or changing the schedule hands it over again.";

const RUNS_STEP: StatusStep = { label: "View the runs", runs: true };

/** "Paris", "Buenos Aires", "UTC". */
export function zoneCity(timeZone: string): string {
  return timeZone.split("/").at(-1)?.replaceAll("_", " ") ?? timeZone;
}

/**
 * "Starts in 9 hours, Saturday 02:00 Paris time." The day and time are in the schedule's time
 * zone. A time zone the browser does not know leaves them out.
 */
export function startSentence(iso: string, timeZone: string, now: number): string {
  const rel = startsIn(iso, now);
  let when: string;
  try {
    const parts = new Intl.DateTimeFormat("en-GB", {
      timeZone,
      weekday: "long",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    }).formatToParts(new Date(iso));
    const get = (type: Intl.DateTimeFormatPartTypes) =>
      parts.find((p) => p.type === type)?.value ?? "";
    when = `${get("weekday")} ${get("hour")}:${get("minute")}`;
  } catch (error) {
    if (!(error instanceof RangeError)) throw error;
    return `Starts ${rel}.`;
  }
  return `Starts ${rel}, ${when} ${zoneCity(timeZone)} time.`;
}

/** A step for an action, only when the signed-in account may use it. */
function allowed(
  access: Record<ScheduleAction, ActionAccess>,
  action: ScheduleAction,
  step: StatusStep
): StatusStep | undefined {
  return access[action].available ? step : undefined;
}

/**
 * The situation of a schedule in one sentence and its next step (design-schedule-page.md): the stop
 * reason of a stopped schedule, the failed runs of a failing one, a paused or deleted schedule, and
 * otherwise its next start. A step for an action is offered only when `access` allows it.
 */
export function scheduleStatus(
  s: Schedule,
  access: Record<ScheduleAction, ActionAccess>,
  now: number
): ScheduleStatus {
  const panel = stateView(s).panel;
  if (s.state === "stopped") {
    const details = panel?.action ? [panel.action] : [];
    switch (s.state_reason) {
      case "owner-not-found":
        return {
          tone: "danger",
          icon: "stopped",
          text: panel?.text ?? "This schedule is stopped.",
          details,
          step: allowed(access, "take-over", { label: "Take over", dialog: "take-over" }),
        };
      case "approval-required":
        return {
          tone: "danger",
          icon: "stopped",
          text: panel?.text ?? "This schedule is stopped.",
          details,
          step: allowed(access, "change", { label: "Change the write target", dialog: "change" }),
        };
      case "repeated-failures":
        return {
          tone: "danger",
          icon: "stopped",
          text: panel?.text ?? "This schedule is stopped.",
          details: [...details, `Failed runs in a row: ${s.consecutive_failures}`],
          step: allowed(access, "resume", { label: "Resume", dialog: "resume" }),
          second: RUNS_STEP,
        };
      default:
        // A stop reason the web interface does not know offers no step, as the stop panel did.
        return {
          tone: "danger",
          icon: "stopped",
          text: panel?.text ?? "This schedule is stopped.",
          details,
        };
    }
  }
  if (s.state === "paused") {
    return {
      tone: "neutral",
      icon: "resting",
      text: panel?.text ?? "This schedule starts no run until it is resumed.",
      details: s.state_reason ? [`Reason: “${s.state_reason}”`] : [],
      step: allowed(access, "resume", { label: "Resume", dialog: "resume" }),
    };
  }
  if (s.state === "deleted") {
    return {
      tone: "neutral",
      icon: "resting",
      text: panel?.text ?? "This schedule was deleted. Its runs are kept.",
      details: [],
      second: RUNS_STEP,
    };
  }
  const next = s.next_start_at
    ? startSentence(s.next_start_at, s.timezone, now)
    : "This schedule has no next start.";
  if (s.consecutive_failures > 0) {
    return {
      tone: "warning",
      icon: "failing",
      text: failuresText(s.consecutive_failures),
      details: [next],
      step: RUNS_STEP,
    };
  }
  const newer = newerVersionNote(s);
  if (newer && s.state === "active") {
    return {
      tone: "neutral",
      icon: "active",
      text: next,
      details: [`${newer.text}. ${newer.detail}`],
      step: allowed(access, "change", {
        label: `Change to version ${s.newer_version}`,
        dialog: "change-version",
      }),
    };
  }
  return {
    tone: "neutral",
    icon: s.state === "active" ? "active" : "resting",
    text: next,
    details: [],
  };
}

function newerVersionNote(s: Schedule): StatusNote | null {
  if (s.newer_version == null || s.state === "deleted") return null;
  return {
    kind: "newer-version",
    text: `Version ${s.newer_version} of this configuration is registered`,
    detail: `This schedule starts runs of version ${s.registry_version}.`,
  };
}

/**
 * The facts the status card adds below its sentence: the schedule is not handed to the task
 * manager, and a newer version is registered when that is not already the card's sentence.
 */
export function statusNotes(
  s: Schedule,
  access: Record<ScheduleAction, ActionAccess>,
  status: ScheduleStatus
): StatusNote[] {
  const notes: StatusNote[] = [];
  const live = s.state !== "deleted";
  if (!s.prefect_synced && live) notes.push({ kind: "not-handed-over", text: NOT_HANDED_OVER });
  const newer = newerVersionNote(s);
  const isStep = status.step && "dialog" in status.step && status.step.dialog === "change-version";
  const inSentence = status.details.some((d) => newer && d.startsWith(newer.text));
  if (newer && !isStep && !inSentence) {
    notes.push({
      ...newer,
      step: allowed(access, "change", { label: "Change version", dialog: "change-version" }),
    });
  }
  return notes;
}

/** The header actions of the page, without the one the status card offers as its step. */
export function headerActions(
  access: Record<ScheduleAction, ActionAccess>,
  status: ScheduleStatus
): ScheduleAction[] {
  const inCard = new Set<ScheduleAction>(
    [status.step, status.second].flatMap((step) =>
      step && "dialog" in step && step.dialog !== "change-version" ? [step.dialog] : []
    )
  );
  return (["change", "pause", "resume", "take-over", "delete"] as const).filter(
    (a) => access[a].shown && !inCard.has(a)
  );
}

/* ---------- timeline ---------- */

/** The starts of the timeline by day, the soonest nearest "Now": the farthest day and start first. */
export function startGroups(
  starts: readonly string[],
  now: number
): { bucket: StartBucket; items: string[] }[] {
  return START_BUCKETS.map((bucket) => ({
    bucket,
    items: starts
      .filter((iso) => startBucket(iso, now) === bucket)
      .sort((a, b) => Date.parse(b) - Date.parse(a)),
  }))
    .filter((g) => g.items.length > 0)
    .reverse();
}

export const RUN_BUCKETS = ["Today", "Yesterday", "Earlier"] as const;
export type RunBucket = (typeof RUN_BUCKETS)[number];

function startOfDay(t: number): number {
  const d = new Date(t);
  d.setHours(0, 0, 0, 0);
  return d.getTime();
}

/** The day of a past run, in the viewer's time zone. */
export function runBucket(iso: string, now: number): RunBucket {
  const days = Math.round((startOfDay(now) - startOfDay(Date.parse(iso))) / 86_400_000);
  if (days <= 0) return "Today";
  return days === 1 ? "Yesterday" : "Earlier";
}

/** One run of the timeline. */
export interface RunLine {
  runId: string;
  state: RunState;
  label: string;
  startedAt: string;
}

export function runLine(run: Run): RunLine {
  return {
    runId: run.run_id,
    state: runState(run),
    label: phaseLabel(run),
    startedAt: run.started_at,
  };
}

/** The timeline shows this many runs. */
export const TIMELINE_RUNS = 5;

/** The last runs by day, newest first, nearest "Now". */
export function runGroups(
  runs: readonly Run[],
  now: number,
  limit = TIMELINE_RUNS
): { bucket: RunBucket; items: RunLine[] }[] {
  const lines = [...runs]
    .sort((a, b) => Date.parse(b.started_at) - Date.parse(a.started_at))
    .slice(0, limit)
    .map(runLine);
  return RUN_BUCKETS.map((bucket) => ({
    bucket,
    items: lines.filter((r) => runBucket(r.startedAt, now) === bucket),
  })).filter((g) => g.items.length > 0);
}

/** "stopped", "paused" or "deleted": the words of "No start while this schedule is …". */
export function noStartWord(s: Schedule): string {
  return s.state === "paused" ? "paused" : s.state === "deleted" ? "deleted" : "stopped";
}
