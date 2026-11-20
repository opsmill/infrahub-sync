import { Card, Spinner } from "@infrahub/ui";
import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";
import { cn } from "tailwind-variants";

import type { Run, Schedule } from "#/api/types";
import { StateIcon } from "#/components/icons";
import { Bar, textAction } from "#/components/page";
import { noStartWord, type RunLine, runGroups, startGroups } from "#/domain/schedule-page";
import { formatInZone, startsIn } from "#/domain/start-times";
import { LINK } from "#/features/runs/form";
import { useNow } from "#/lib/time";
import { failureText, type NextStartsRead } from "./NextStarts";
import { TOUCH } from "./ScheduleListParts";

const GRID = "grid grid-cols-[2.75rem_1rem_minmax(0,1fr)] gap-x-2.5";

/** The vertical line through the icons. */
const RAIL =
  "relative flex justify-center before:absolute before:inset-y-0 before:left-1/2 before:w-px before:-translate-x-1/2 before:bg-border";

const hhmm = new Intl.DateTimeFormat(undefined, {
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});
const weekday = new Intl.DateTimeFormat(undefined, {
  weekday: "short",
  day: "numeric",
  month: "short",
});

/** The schedule's time zone on hover; a zone the browser does not know is named only. */
function zonedTitle(iso: string, timeZone: string): string {
  try {
    return `${formatInZone(iso, timeZone)} (${timeZone})`;
  } catch (error) {
    if (!(error instanceof RangeError)) throw error;
    return timeZone;
  }
}

function Time({ iso, timeZone }: { iso: string; timeZone: string }) {
  return (
    <time
      dateTime={iso}
      title={zonedTitle(iso, timeZone)}
      className="py-1 text-right text-[13px] text-foreground-muted tabular-nums"
    >
      {hhmm.format(new Date(iso))}
    </time>
  );
}

function GroupLabel({ children }: { children: ReactNode }) {
  return (
    <li className={GRID}>
      <span />
      <span className={RAIL} />
      <h4 className="pt-2 pb-0.5 font-medium text-[11px] text-subtle uppercase tracking-[0.06em]">
        {children}
      </h4>
    </li>
  );
}

function StartRow({ iso, far, timeZone }: { iso: string; far: boolean; timeZone: string }) {
  const now = useNow();
  return (
    <li className={GRID}>
      <Time iso={iso} timeZone={timeZone} />
      <span className={cn(RAIL, "pt-[9px]")}>
        <span className="relative size-2.5 rounded-full border-[1.5px] border-subtle-muted bg-background" />
      </span>
      <span className="truncate py-1 text-subtle text-xs leading-5 tabular-nums">
        {far ? weekday.format(new Date(iso)) : startsIn(iso, now)}
      </span>
    </li>
  );
}

function RunRow({ run, earlier, timeZone }: { run: RunLine; earlier: boolean; timeZone: string }) {
  return (
    <li className={GRID}>
      <Time iso={run.startedAt} timeZone={timeZone} />
      <span className={cn(RAIL, "pt-[7px]")}>
        <span className="relative flex bg-content">
          <StateIcon
            state={run.state}
            className={cn(
              "size-3.5",
              run.state === "failed"
                ? "text-danger"
                : run.state === "insync"
                  ? "text-success"
                  : run.state === "review"
                    ? "text-warning"
                    : "text-subtle"
            )}
          />
        </span>
      </span>
      <span className="flex min-w-0 flex-col py-1 text-[13px]">
        <span className="truncate font-medium text-foreground" title={run.label}>
          {run.label}
        </span>
        <span className="flex flex-wrap items-center gap-x-1.5 text-subtle text-xs">
          {earlier && (
            <>
              <span>{weekday.format(new Date(run.startedAt))}</span>
              <span aria-hidden="true">·</span>
            </>
          )}
          <Link
            to="/runs/$runId"
            params={{ runId: run.runId }}
            className={cn(LINK, "relative before:absolute before:-inset-y-1.5 before:-inset-x-1")}
          >
            View run<span className="sr-only">, {run.label}</span>
          </Link>
        </span>
      </span>
    </li>
  );
}

function Heading({ children }: { children: ReactNode }) {
  return (
    <li className="pt-1 pb-1">
      <h3 className="font-semibold text-sm tracking-tight">{children}</h3>
    </li>
  );
}

/** A sentence in place of timeline rows, such as why there is no start. */
export function TimelineMessage({
  children,
  alert = false,
}: {
  children: ReactNode;
  alert?: boolean;
}) {
  return (
    <li
      role={alert ? "alert" : undefined}
      className="pb-2 text-pretty text-foreground-muted text-sm"
    >
      {children}
    </li>
  );
}

/**
 * The next starts of a timeline, grouped under Today, Tomorrow, This week and Later, the soonest
 * last so that it sits nearest the "Now" marker below. `groups` comes from `startGroups`.
 */
export function NextStartGroups({
  groups,
  timeZone,
}: {
  groups: ReturnType<typeof startGroups>;
  timeZone: string;
}) {
  return (
    <>
      {groups.map((g) => (
        <li key={g.bucket}>
          <ol aria-label={g.bucket}>
            <GroupLabel>{g.bucket}</GroupLabel>
            {g.items.map((iso) => (
              <StartRow
                key={iso}
                iso={iso}
                timeZone={timeZone}
                far={g.bucket === "This week" || g.bucket === "Later"}
              />
            ))}
          </ol>
        </li>
      ))}
    </>
  );
}

/** The marker between the next starts above and the past below. */
export function NowMarker() {
  return (
    <li className={cn(GRID, "py-1.5")} aria-label="Now">
      <span className="text-right">
        <span className="rounded-full bg-accent px-1.5 py-0.5 font-semibold text-[11px] text-white">
          Now
        </span>
      </span>
      <span className={cn(RAIL, "items-center")}>
        <span className="relative size-2.5 rounded-full bg-accent ring-4 ring-content" />
      </span>
      <span className="self-center border-t border-dashed" />
    </li>
  );
}

/** The parts of the runs read that the timeline uses. */
export interface RunsRead {
  data?: { items: Run[] };
  isPending: boolean;
  error: unknown;
  refetch: () => unknown;
}

/**
 * The timeline of a schedule page (design-schedule-page.md): its next starts above "Now", the
 * soonest nearest it; the start that waits for a run in progress; its last runs below, newest
 * first; and a link to all its runs. Times are in the viewer's time zone, with the schedule's on
 * hover.
 */
export function ScheduleTimeline({
  schedule,
  starts,
  runs,
  count,
  runCount,
}: {
  schedule: Schedule;
  starts: NextStartsRead;
  runs: RunsRead;
  /** The number of starts asked for. */
  count: number;
  /** The number of runs asked for. */
  runCount: number;
}) {
  const now = useNow();
  const tz = schedule.timezone;
  const active = schedule.state === "active";
  const groups = startGroups(starts.data?.starts ?? [], now);
  const runLines = runs.data ? runGroups(runs.data.items, now, runCount) : [];
  const waiting = schedule.waiting_start_at;

  let upper: ReactNode;
  if (!active) {
    upper = (
      <TimelineMessage>No start while this schedule is {noStartWord(schedule)}.</TimelineMessage>
    );
  } else if (starts.isPending && starts.fetchStatus !== "idle") {
    upper = (
      <li className="flex items-center gap-2 pb-2 text-foreground-muted text-sm">
        <Spinner aria-hidden="true" className="motion-reduce:animate-none" />
        Computing the next start times…
      </li>
    );
  } else if (!starts.data) {
    upper = <TimelineMessage alert>{failureText(starts.error)}</TimelineMessage>;
  } else {
    upper = (
      <>
        <NextStartGroups groups={groups} timeZone={tz} />
        {starts.data.starts.length < count && (
          <TimelineMessage>
            {starts.data.starts.length === 0 ? "No start" : "No further start"} in the next 5 years.
          </TimelineMessage>
        )}
      </>
    );
  }

  let lower: ReactNode;
  if (runs.isPending) {
    lower = (
      <li aria-hidden="true" className="flex flex-col gap-2.5 py-2">
        {[0, 1, 2].map((i) => (
          <div key={i} className={GRID}>
            <Bar className="ml-auto h-3.5 w-9" />
            <span />
            <Bar className="h-3.5 w-28" />
          </div>
        ))}
      </li>
    );
  } else if (!runs.data) {
    lower = (
      <TimelineMessage alert>
        The runs could not be read.{" "}
        <button type="button" className={cn(textAction(), TOUCH)} onClick={() => runs.refetch()}>
          Try again
        </button>
      </TimelineMessage>
    );
  } else if (runLines.length === 0) {
    lower = <TimelineMessage>This schedule has started no run.</TimelineMessage>;
  } else {
    lower = runLines.map((g) => (
      <li key={g.bucket}>
        <ol aria-label={g.bucket}>
          <GroupLabel>{g.bucket}</GroupLabel>
          {g.items.map((r) => (
            <RunRow key={r.runId} run={r} earlier={g.bucket === "Earlier"} timeZone={tz} />
          ))}
        </ol>
      </li>
    ));
  }

  return (
    <section aria-labelledby="schedule-timeline" className="flex flex-col gap-2">
      <h2 id="schedule-timeline" className="sr-only">
        Next starts and last runs
      </h2>
      <Card className="bg-content bg-none px-3 py-3">
        <ol className="flex flex-col">
          <Heading>Next starts</Heading>
          {upper}
          {waiting && (
            <li>
              <ol aria-label="Waiting start">
                <GroupLabel>Waiting for the run in progress</GroupLabel>
                <li className={GRID}>
                  <Time iso={waiting} timeZone={tz} />
                  <span className={cn(RAIL, "pt-[9px]")}>
                    <span className="relative size-2.5 rounded-full border-[1.5px] border-warning bg-background" />
                  </span>
                  <span className="truncate py-1 text-subtle text-xs leading-5">
                    {weekday.format(new Date(waiting))}
                  </span>
                </li>
              </ol>
            </li>
          )}
          <NowMarker />
          <Heading>Last runs</Heading>
          {lower}
        </ol>
        <div className="mt-2 border-t pt-2">
          <Link
            to="/runs"
            search={{ schedule: schedule.schedule_id }}
            className={cn(textAction({ primary: true }), "ml-0", TOUCH)}
          >
            All runs <span aria-hidden="true">→</span>
          </Link>
        </div>
      </Card>
      {active && groups.length > 0 && (
        <p className="text-subtle text-xs">In your time zone. Hover a time for the schedule’s.</p>
      )}
    </section>
  );
}
