import { Card } from "@infrahub/ui";
import { Link } from "@tanstack/react-router";
import { cn } from "tailwind-variants";

import type { Schedule } from "#/api/types";
import { Bar, CLICKABLE, CODE_CHIP, STRETCHED } from "#/components/page";
import { StateLabel } from "#/components/run-status";
import type { RunState } from "#/domain/run-state";
import { stateView } from "#/domain/schedules";

import { StartTime } from "./NextStarts";

/** The icon of a schedule state: a stopped schedule needs attention, a paused or deleted one rests. */
const STATE_ICON: Record<string, RunState> = {
  active: "insync",
  paused: "idle",
  stopped: "failed",
  deleted: "idle",
};

/** The state badge of a schedule, with the words of `stateView`. */
export function ScheduleStateLabel({
  schedule,
  className,
}: {
  schedule: Schedule;
  className?: string;
}) {
  return (
    <StateLabel
      state={STATE_ICON[schedule.state] ?? "idle"}
      label={stateView(schedule).label}
      quiet={schedule.state === "deleted"}
      className={className}
    />
  );
}

/** The next start, or why there is none. */
function NextStart({ schedule }: { schedule: Schedule }) {
  if (!schedule.next_start_at) {
    return (
      <>
        <span aria-hidden="true" className="text-subtle">
          —
        </span>
        <span className="sr-only">No next start</span>
      </>
    );
  }
  return <StartTime iso={schedule.next_start_at} timezone={schedule.timezone} relative />;
}

function Cadence({ schedule }: { schedule: Schedule }) {
  return (
    <span className="flex min-w-0 flex-col items-start gap-0.5">
      <code className={cn(CODE_CHIP, "[overflow-wrap:anywhere]")}>{schedule.cron}</code>
      <span className="text-subtle text-xs [overflow-wrap:anywhere]">{schedule.timezone}</span>
    </span>
  );
}

function ConfigLine({ schedule, name }: { schedule: Schedule; name: string }) {
  return (
    <span className="min-w-0 [overflow-wrap:anywhere]">
      {name}{" "}
      <span className="whitespace-nowrap text-subtle">· version {schedule.registry_version}</span>
    </span>
  );
}

/** The schedule's link, stretched over its row. */
function ScheduleLink({ schedule, className }: { schedule: Schedule; className?: string }) {
  return (
    <Link
      to="/schedules/$scheduleId"
      params={{ scheduleId: schedule.schedule_id }}
      data-stretched=""
      data-schedule-id={schedule.schedule_id}
      className={cn("break-words font-medium text-foreground", STRETCHED, className)}
    >
      {schedule.name}
      <span className="sr-only">, {stateView(schedule).label}</span>
    </Link>
  );
}

const TH =
  "sticky top-[49px] z-20 h-9 bg-content bg-[linear-gradient(var(--content-muted),var(--content-muted))] px-4 font-medium text-subtle text-xs shadow-[inset_0_-1px_0_var(--color-border)]";
const TD = "px-4 py-3 align-top";
const CARD = "overflow-clip bg-content bg-none";

/** Six columns need the width of `lg`; below it each schedule is a list item, as in the runs list. */
const TABLE = "w-full table-fixed border-collapse text-left max-lg:hidden";
const LIST = "divide-y lg:hidden";

const COLUMNS: [string, string][] = [
  ["Name", "w-[19%]"],
  ["Configuration", "w-[19%]"],
  ["State", "w-[10%]"],
  ["Cadence", "w-[15%]"],
  ["Next start", "w-[23%]"],
  ["Owner", "w-[14%]"],
];

function Head() {
  return (
    <>
      <colgroup>
        {COLUMNS.map(([name, w]) => (
          <col key={name} className={w} />
        ))}
      </colgroup>
      <thead>
        <tr>
          {COLUMNS.map(([name]) => (
            <th key={name} scope="col" className={TH}>
              {name}
            </th>
          ))}
        </tr>
      </thead>
    </>
  );
}

/**
 * Schedules, newest first: a table from the `lg` breakpoint, a list below it. Configuration names
 * come from `configNames`; a configuration missing from it is shown by its identifier.
 */
export function SchedulesTable({
  schedules,
  label,
  configNames,
}: {
  schedules: Schedule[];
  /** The table's and the phone list's accessible name. */
  label: string;
  configNames: Map<string, string>;
}) {
  const nameOf = (s: Schedule) => configNames.get(s.config_id) ?? s.config_id;
  return (
    <Card className={CARD}>
      <table aria-label={label} className={TABLE}>
        <Head />
        <tbody className="divide-y">
          {schedules.map((s) => (
            <tr key={s.schedule_id} className={CLICKABLE}>
              <td className={TD}>
                <ScheduleLink schedule={s} className="line-clamp-2 lg:scroll-mt-12" />
              </td>
              <td className={cn(TD, "text-foreground-muted")}>
                <ConfigLine schedule={s} name={nameOf(s)} />
              </td>
              <td className={TD}>
                <ScheduleStateLabel schedule={s} />
              </td>
              <td className={TD}>
                <Cadence schedule={s} />
              </td>
              <td className={cn(TD, "text-foreground-muted")}>
                <NextStart schedule={s} />
              </td>
              <td className={cn(TD, "text-foreground-muted [overflow-wrap:anywhere]")}>
                {s.owner}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <ul aria-label={label} className={LIST}>
        {schedules.map((s) => (
          <li
            key={s.schedule_id}
            className={cn(
              CLICKABLE,
              "grid grid-cols-1 items-start gap-x-3 gap-y-1.5 px-4 py-3 sm:grid-cols-[minmax(0,1fr)_auto]"
            )}
          >
            <ScheduleLink schedule={s} className="line-clamp-3" />
            <ScheduleStateLabel schedule={s} className="text-sm" />
            <div className="min-w-0 text-foreground-muted text-xs sm:col-span-2">
              <ConfigLine schedule={s} name={nameOf(s)} />
              <span className="text-subtle"> · Owner: </span>
              <span className="[overflow-wrap:anywhere]">{s.owner}</span>
            </div>
            <div className="flex min-w-0 flex-wrap items-start gap-x-4 gap-y-1 text-xs sm:col-span-2">
              <Cadence schedule={s} />
              {s.next_start_at && (
                <span className="text-foreground-muted">
                  <span className="text-subtle">Next start</span>
                  <StartTime iso={s.next_start_at} timezone={s.timezone} relative />
                </span>
              )}
            </div>
          </li>
        ))}
      </ul>
    </Card>
  );
}

/** The loaded table's shape, so nothing moves when the schedules arrive. */
export function SchedulesTableSkeleton({ rows = 6 }: { rows?: number }) {
  return (
    <div aria-busy="true">
      <output className="sr-only">Loading schedules</output>
      <Card className={CARD}>
        <table aria-hidden="true" className={TABLE}>
          <Head />
          <tbody className="divide-y">
            {Array.from({ length: rows }, (_, i) => (
              <tr key={i} className="h-16">
                <td className={TD}>
                  <Bar className="h-3.5 w-32 max-w-full" />
                </td>
                <td className={TD}>
                  <Bar className="h-3.5 w-36 max-w-full" />
                </td>
                <td className={TD}>
                  <Bar className="h-3.5 w-16" />
                </td>
                <td className={TD}>
                  <Bar className="h-4 w-24 max-w-full" />
                  <Bar className="mt-1.5 h-3 w-20" />
                </td>
                <td className={TD}>
                  <Bar className="h-3.5 w-44 max-w-full" />
                  <Bar className="mt-1.5 h-3 w-24" />
                </td>
                <td className={TD}>
                  <Bar className="h-3.5 w-20" />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <ul aria-hidden="true" className={LIST}>
          {Array.from({ length: 4 }, (_, i) => (
            <li key={i} className="flex flex-col gap-2 px-4 py-3">
              <div className="flex items-center justify-between gap-3">
                <Bar className="h-3.5 w-40" />
                <Bar className="h-3.5 w-16" />
              </div>
              <Bar className="h-3 w-56 max-w-full" />
              <Bar className="h-3 w-32" />
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}
