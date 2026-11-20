import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useState } from "react";

import { configScheduleQuery, scheduleQuery } from "#/api/queries";
import type { Run } from "#/api/types";
import { ChevronRightIcon } from "#/components/icons";
import { Bar, textAction } from "#/components/page";
import { scheduledForText, startTimeText, viewerTimeZone } from "#/domain/start-times";
import { LINK } from "#/features/runs/form";

import { ScheduleFormDialog } from "./ScheduleFormDialog";
import { ScheduleStateLabel } from "./SchedulesTable";

/**
 * "Started by schedule <name>, scheduled for <time>" for a run that a schedule started, with a
 * link to the schedule; nothing for any other run. The name and the time zone come from
 * `GET /schedules/{schedule_id}`, which also answers for a deleted schedule. The identifier is
 * never shown: while the schedule loads, or when it cannot be read, the line says "a schedule".
 */
export function RunScheduleLine({ run }: { run: Run }) {
  const scheduleId = run.schedule_id ?? "";
  const read = useQuery({ ...scheduleQuery(scheduleId), enabled: Boolean(scheduleId) });
  if (run.trigger_type !== "scheduled") return null;
  const schedule = read.data;
  const at = run.scheduled_start_at
    ? scheduledForText(run.scheduled_start_at, schedule?.timezone, viewerTimeZone())
    : undefined;
  const name = schedule ? (
    <>schedule {schedule.name}</>
  ) : scheduleId && read.isPending ? (
    <>
      schedule <Bar className="inline-block h-3.5 w-24 align-middle" />
      <span className="sr-only">loading</span>
    </>
  ) : (
    <>a schedule</>
  );
  return (
    <span className="[overflow-wrap:anywhere]">
      Started by{" "}
      {scheduleId ? (
        <Link to="/schedules/$scheduleId" params={{ scheduleId }} className={LINK}>
          {name}
        </Link>
      ) : (
        name
      )}
      {at && <>, scheduled for {at}</>}
    </span>
  );
}

/**
 * The configuration's schedule that is not deleted, on the configuration page (FR-011): its name
 * linked to its page, its state and its next start; or "No schedule" with "Create schedule", which
 * opens the create form with this configuration and its latest version. Nothing is shown while
 * the schedule is read, so "No schedule" never stands for a schedule not read yet.
 */
export function ConfigScheduleLine({
  configId,
  name,
  latestVersion,
}: {
  configId: string;
  /** The configuration's name, for the create form. */
  name: string;
  latestVersion: number | undefined;
}) {
  const read = useQuery(configScheduleQuery(configId));
  const [creating, setCreating] = useState(false);
  if (!read.data) {
    return read.isError ? (
      <p className="text-[13px] text-subtle">
        The schedule of this configuration could not be read.
      </p>
    ) : null;
  }
  const schedule = read.data.items[0];
  if (!schedule) {
    return (
      <>
        <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px] text-foreground-muted">
          <span>No schedule</span>
          {latestVersion !== undefined && (
            <>
              <span aria-hidden="true">·</span>
              <button
                type="button"
                className={textAction({ primary: false })}
                onClick={() => setCreating(true)}
              >
                Create schedule
                <ChevronRightIcon />
              </button>
            </>
          )}
        </p>
        {creating && (
          <ScheduleFormDialog
            target={{ mode: "create", config: { configId, name, latestVersion } }}
            onClose={() => setCreating(false)}
          />
        )}
      </>
    );
  }
  const next = schedule.next_start_at
    ? startTimeText(schedule.next_start_at, schedule.timezone, viewerTimeZone())
    : undefined;
  return (
    <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px] text-foreground-muted">
      <span>
        Schedule{" "}
        <Link
          to="/schedules/$scheduleId"
          params={{ scheduleId: schedule.schedule_id }}
          className={LINK}
        >
          <span className="[overflow-wrap:anywhere]">{schedule.name}</span>
        </Link>
      </span>
      <ScheduleStateLabel schedule={schedule} />
      {next && schedule.next_start_at && (
        <>
          <span aria-hidden="true">·</span>
          <span>
            Next start{" "}
            <time dateTime={schedule.next_start_at} className="tabular-nums">
              {next.zoned}
            </time>
            {next.local && <span className="text-subtle"> (your time: {next.local})</span>}
          </span>
        </>
      )}
    </p>
  );
}
