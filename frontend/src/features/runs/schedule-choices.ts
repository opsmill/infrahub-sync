import type { SelectOption } from "#/features/schedules/form-parts";

/** The fields of a schedule that name a choice of the runs list's schedule filter. */
export interface ScheduleChoiceSource {
  schedule_id: string;
  name: string;
  config_id: string;
}

const byName = (a: ScheduleChoiceSource, b: ScheduleChoiceSource) =>
  a.name.localeCompare(b.name, "en");

/**
 * The choices of the schedule filter: schedules that are not deleted, by name, then deleted ones
 * marked "(deleted)". `selected` is the schedule of the address, added when neither list has it
 * (another configuration's schedule, or one read before the lists). A label that two choices share
 * gets the configuration name as a second line; the schedule identifier is never shown.
 */
export function scheduleChoices({
  live,
  deleted,
  selected,
  configNames,
}: {
  live: ScheduleChoiceSource[];
  deleted: ScheduleChoiceSource[];
  selected?: ScheduleChoiceSource & { state: string };
  configNames: ReadonlyMap<string, string>;
}): SelectOption<string>[] {
  const seen = new Set<string>();
  const entries: { schedule: ScheduleChoiceSource; label: string }[] = [];
  const entry = (schedule: ScheduleChoiceSource, isDeleted: boolean) => ({
    schedule,
    label: isDeleted ? `${schedule.name} (deleted)` : schedule.name,
  });
  const add = (schedule: ScheduleChoiceSource, isDeleted: boolean) => {
    if (seen.has(schedule.schedule_id)) return;
    seen.add(schedule.schedule_id);
    entries.push(entry(schedule, isDeleted));
  };
  for (const s of [...live].sort(byName)) add(s, false);
  for (const s of [...deleted].sort(byName)) add(s, true);
  if (selected && !seen.has(selected.schedule_id)) {
    entries.unshift(entry(selected, selected.state === "deleted"));
  }

  const counts = new Map<string, number>();
  for (const e of entries) counts.set(e.label, (counts.get(e.label) ?? 0) + 1);
  return entries.map(({ schedule, label }) => ({
    id: schedule.schedule_id,
    label,
    detail: (counts.get(label) ?? 0) > 1 ? configNames.get(schedule.config_id) : undefined,
  }));
}

/**
 * The text of the runs list's active schedule filter when its link cannot name the schedule: while
 * `GET /schedules/{schedule_id}` loads, when the Sync API has no such schedule, or when the read
 * failed. The schedule identifier is never shown as text (FR-007, SC-006).
 */
export function scheduleFilterText(read: { pending: boolean; notFound: boolean }): string {
  if (read.pending) return "Loading the schedule";
  if (read.notFound) return "Schedule not found";
  return "Schedule could not be read";
}
