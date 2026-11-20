import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";

import { scheduleQuery } from "#/api/queries";
import type { Run } from "#/api/types";
import { Bar } from "#/components/page";
import { scheduledForText, viewerTimeZone } from "#/domain/start-times";
import { LINK } from "#/features/runs/form";

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
