import type { SyncEvent } from "#/api/types";
import { formatAbsolute } from "#/lib/time";

import { MAX_CONSECUTIVE_FAILURES, stopReasonText } from "./schedules";

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

  if (details?.cause && CAUSE_TYPES.has(event.event_type)) detailParts.push(details.cause);
  if (event.event_type === "infrahub_sync.run.synced" && details?.left_out != null) {
    detailParts.push(
      `${details.left_out} ${details.left_out === 1 ? "record" : "records"} left out`
    );
  }
  return detailParts.length ? { text, detail: detailParts.join(". ") } : { text };
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
 * listed pages, in the order the Sync API returned them (contracts/http-api.md). The listed pages
 * keep their order, so the offsets of older pages do not move.
 */
export function withNewestEvents(newest: readonly SyncEvent[], listed: SyncEvent[]): SyncEvent[] {
  const shown = new Set(listed.map((event) => event.event_id));
  const added = newest.filter((event) => !shown.has(event.event_id));
  return added.length ? [...added, ...listed] : listed;
}
