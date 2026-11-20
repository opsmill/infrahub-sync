import type { SyncEvent } from "#/api/types";
import { formatAbsolute } from "#/lib/time";

import { cadenceWords, formFromCron } from "./cadence";
import {
  lateStartLabel,
  MAX_CONSECUTIVE_FAILURES,
  overlapPolicyLabel,
  stopReasonText,
  writeTargetLabel,
} from "./schedules";

const SCHEDULE = "infrahub_sync.schedule.";

/**
 * The schedule's own event types that the history shows by default: every
 * `infrahub_sync.schedule.*` type of the spec 021 events contract except `replayed`, which records
 * no change (spec Clarifications, 2026-11-20).
 */
export const DEFAULT_HISTORY_EVENT_TYPES = [
  "created",
  "changed",
  "paused",
  "resumed",
  "deleted",
  "taken_over",
  "refused",
  "start_skipped",
  "start_queued",
  "start_missed",
  "start_failed",
  "stopped",
  "proposed_change_opened",
  "proposed_change_replaced",
  "proposed_change_kept",
  "proposed_change_failed",
].map((suffix) => `${SCHEDULE}${suffix}`);

/** Texts that depend on the event type only. */
const TYPE_TEXTS: Record<string, string> = {
  [`${SCHEDULE}created`]: "Schedule created",
  [`${SCHEDULE}changed`]: "Settings changed",
  [`${SCHEDULE}paused`]: "Paused",
  [`${SCHEDULE}resumed`]: "Resumed",
  [`${SCHEDULE}deleted`]: "Deleted",
  [`${SCHEDULE}taken_over`]: "Taken over",
  [`${SCHEDULE}replayed`]: "Request repeated with the same retry key",
  "infrahub_sync.run.accepted": "Run started",
  "infrahub_sync.run.synced": "Run wrote its changes",
  "infrahub_sync.run.failed": "Run failed",
};

/** Texts that depend on the event type and its outcome. */
const OUTCOME_TEXTS: Record<string, Record<string, string>> = {
  [`${SCHEDULE}start_skipped`]: {
    "skipped-run-in-progress": "Start skipped: a run of this configuration was in progress",
    "skipped-approved-change-open":
      "Start skipped: an approved proposed change of this schedule is not merged yet",
    "skipped-start-waiting": "Start skipped: another start was already waiting",
    "skipped-approval-required":
      "Start skipped: this Sync API requires an approved plan before a write to the default branch",
  },
  [`${SCHEDULE}start_queued`]: { queued: "Start waiting for the run in progress" },
  [`${SCHEDULE}start_missed`]: {
    "missed-skipped": "Start missed: the worker or the task manager was not running",
    "missed-started-late":
      "Started late: the worker or the task manager was not running at the start time",
  },
  [`${SCHEDULE}start_failed`]: { failed: "Start failed" },
  [`${SCHEDULE}proposed_change_opened`]: { opened: "Proposed change opened" },
  [`${SCHEDULE}proposed_change_replaced`]: { replaced: "Earlier proposed change replaced" },
  [`${SCHEDULE}proposed_change_kept`]: {
    "kept-merged": "Earlier proposed change kept: it was merged",
    "kept-closed": "Earlier proposed change kept: it was closed",
    "kept-missing": "Earlier proposed change kept: it was deleted in Infrahub",
    "kept-approved": "Earlier proposed change kept: it was approved",
  },
  [`${SCHEDULE}proposed_change_failed`]: { failed: "Proposed change failed" },
};

/** The request a refused event names, in words. */
const OPERATION_WORDS: Record<string, string> = {
  "schedule-create": "create the schedule",
  "schedule-change": "change the schedule",
  "schedule-pause": "pause the schedule",
  "schedule-resume": "resume the schedule",
  "schedule-delete": "delete the schedule",
  "schedule-take-over": "take the schedule over",
};

/** The types whose `details.cause` the history shows: start and proposed-change failures. */
const CAUSE_TYPES = new Set([`${SCHEDULE}start_failed`, `${SCHEDULE}proposed_change_failed`]);

const STOP_REASONS = new Set(["owner-not-found", "approval-required", "repeated-failures"]);

export interface EventText {
  text: string;
  detail?: string;
  /** One line per setting that a change altered, for `infrahub_sync.schedule.changed`. */
  changes?: string[];
}

/** One entry of `details.changes`: a setting and its stored value before and after the change. */
export type SettingChange = NonNullable<NonNullable<SyncEvent["details"]>["changes"]>[number];

const lowerFirst = (text: string) => text.charAt(0).toLowerCase() + text.slice(1);

/** A cadence in words, as the settings show it; the expression itself when it has no words. */
function cadenceText(cron: string): string {
  const words = cadenceWords(formFromCron(cron));
  // The note on months without the day is left out: the line compares two cadences.
  return words === null ? cron : lowerFirst(words.split(". ")[0]);
}

/** How a history line writes each setting: its label and its value in words. */
const SETTING_TEXTS: Record<string, { label: string; value: (value: never) => string }> = {
  cron: { label: "Cadence", value: cadenceText },
  timezone: { label: "Time zone", value: (zone: string) => zone },
  write_target: {
    label: "Write target",
    value: (target: string) => lowerFirst(writeTargetLabel(target)),
  },
  write_when_left_out: {
    label: "Records left out",
    value: (written: boolean) => (written ? "written" : "not written"),
  },
  overlap_policy: {
    label: "When a run is in progress",
    value: (policy: string) => lowerFirst(overlapPolicyLabel(policy)),
  },
  start_late_by_at_most_seconds: {
    label: "Start late by at most",
    value: (seconds: number) => lateStartLabel(seconds),
  },
};

const SETTING_VALUE_TYPES: Record<string, "string" | "number" | "boolean"> = {
  registry_version: "number",
  cron: "string",
  timezone: "string",
  write_target: "string",
  write_when_left_out: "boolean",
  overlap_policy: "string",
  start_late_by_at_most_seconds: "number",
};

/**
 * A changed setting in words, such as "Version 2 → 3" or "Start late by at most: 30 minutes →
 * 45 minutes". A setting or a value this page does not know is shown as returned.
 */
export function settingChangeText(change: SettingChange): string {
  const known =
    typeof change.before === SETTING_VALUE_TYPES[change.field] &&
    typeof change.after === SETTING_VALUE_TYPES[change.field];
  if (known && change.field === "registry_version") {
    return `Version ${change.before} → ${change.after}`;
  }
  const texts = lookup(SETTING_TEXTS, change.field);
  if (known && texts !== undefined) {
    const value = texts.value as (value: SettingChange["before"]) => string;
    return `${texts.label}: ${value(change.before)} → ${value(change.after)}`;
  }
  return `${change.field}: ${String(change.before)} → ${String(change.after)}`;
}

const lookup = <T>(table: Record<string, T>, key: string): T | undefined =>
  Object.hasOwn(table, key) ? table[key] : undefined;

/**
 * What an event of a schedule's history says (data-model "History entries"). Types and outcomes
 * the table does not name are shown as returned. `formatTime` writes the start times of
 * `details`; tests pass their own.
 */
export function eventText(
  event: SyncEvent,
  formatTime: (iso: string) => string = formatAbsolute
): EventText {
  const details = event.details;
  const detailParts: string[] = [];
  if (details?.start_at) detailParts.push(`For the start at ${formatTime(details.start_at)}`);

  let text: string;
  const fixed = lookup(TYPE_TEXTS, event.event_type);
  const byOutcome = lookup(OUTCOME_TEXTS, event.event_type);
  if (
    event.event_type === `${SCHEDULE}start_missed` &&
    details?.starts != null &&
    details.first_start_at &&
    details.last_start_at
  ) {
    // One event groups the oldest of more than 500 missed starts.
    text = `${details.starts} starts missed between ${formatTime(details.first_start_at)} and ${formatTime(details.last_start_at)}`;
  } else if (event.event_type === `${SCHEDULE}refused`) {
    text = `Request refused: ${lookup(OPERATION_WORDS, event.operation) ?? event.operation}`;
    detailParts.push(event.outcome);
  } else if (event.event_type === `${SCHEDULE}stopped` && STOP_REASONS.has(event.outcome)) {
    // The event holds no count; a schedule stops for repeated failures at this many in a row.
    text = `Stopped: ${stopReasonText(event.outcome, MAX_CONSECUTIVE_FAILURES)}`;
  } else if (fixed !== undefined) {
    text = fixed;
  } else if (byOutcome !== undefined && lookup(byOutcome, event.outcome) !== undefined) {
    text = byOutcome[event.outcome];
  } else if (event.event_type.startsWith("infrahub_sync.run.")) {
    text = `${event.operation}: ${event.outcome}`;
  } else {
    text = `${event.event_type}: ${event.outcome}`;
  }

  let changes: string[] | undefined;
  if (event.event_type === `${SCHEDULE}changed` && details?.changes) {
    // An event recorded before the changes were recorded has no list, and reads "Settings changed" only.
    changes = details.changes.map(settingChangeText);
    if (changes.length === 0)
      detailParts.push("No setting changed: each value sent was already set");
  }
  if (details?.cause && CAUSE_TYPES.has(event.event_type)) detailParts.push(details.cause);
  if (event.event_type === "infrahub_sync.run.synced" && details?.left_out != null) {
    detailParts.push(
      `${details.left_out} ${details.left_out === 1 ? "record" : "records"} left out`
    );
  }
  const result: EventText = detailParts.length
    ? { text, detail: detailParts.join(". ") }
    : { text };
  return changes?.length ? { ...result, changes } : result;
}

/**
 * The run an event names. The Sync API puts an event's run in `primary_resource`
 * (`AuditEvent.primary_resource`), not in `related`; a `related` entry of kind `run` is read too.
 */
export function eventRunId(event: SyncEvent): string | undefined {
  if (event.primary_resource?.kind === "run") return event.primary_resource.id;
  return event.related.find((entry) => entry.kind === "run")?.id;
}

/**
 * The history as shown: the newest events read without `until` that are not listed yet, above the
 * listed pages, in the order the Sync API returned them (contracts/http-api.md). Each event is shown
 * once (FR-034): an event that a page repeats, for example because an event was saved between two
 * reads, is shown where it first appears. Only the shown list leaves repeats out; the offsets of
 * older pages count every event the pages returned, so no older event is skipped.
 */
export function withNewestEvents(
  newest: readonly SyncEvent[],
  pages: readonly (readonly SyncEvent[])[]
): SyncEvent[] {
  const shown = new Set<string>();
  const once = (event: SyncEvent) => {
    if (shown.has(event.event_id)) return false;
    shown.add(event.event_id);
    return true;
  };
  const entries = pages.flat().filter(once);
  const added = newest.filter(once);
  return [...added, ...entries];
}

/**
 * The `until` that the pages of a history use: one millisecond after the newest event of the first
 * page, read without `until`. The store keeps `created_at < until`, so every event of the first page
 * is kept and an event saved after that read is left out; no clock of the browser or of a proxy is
 * involved. `occurred_at` can hold microseconds, which `Date.parse` does not read the same way in
 * every browser, so the fraction is cut to milliseconds first; the result is still later than the
 * event. `null` when the first page is empty: no older page is read then.
 */
export function untilAfter(newestEvent: SyncEvent | undefined): string | null {
  if (!newestEvent) return null;
  const iso = newestEvent.occurred_at.replace(/(\.\d{3})\d+/, "$1");
  const time = Date.parse(iso);
  if (Number.isNaN(time)) return null;
  return new Date(time + 1).toISOString();
}

/**
 * True when the newest events read without `until` may leave a gap above the listed pages: the
 * Sync API holds more events than the newest read returned, and none of them is listed. The events
 * between are then shown nowhere, so the pages must be read again, which moves `until` forward.
 */
export function historyNeedsReread(
  newest: { items: readonly SyncEvent[]; has_more: boolean },
  pages: readonly (readonly SyncEvent[])[]
): boolean {
  if (!newest.has_more) return false;
  const listed = new Set(pages.flat().map((event) => event.event_id));
  return !newest.items.some((event) => listed.has(event.event_id));
}
