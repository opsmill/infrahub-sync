import { Card } from "@infrahub/ui";
import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";
import { cn } from "tailwind-variants";

import type { Schedule } from "#/api/types";
import { WarningIcon } from "#/components/icons";
import { Bar, CLICKABLE, SectionHeading, STRETCHED, textAction } from "#/components/page";
import { cadenceWords, formFromCron } from "#/domain/cadence";
import { type NextStarts, needsAction } from "#/domain/schedule-list";
import { formatInZone, startsIn } from "#/domain/start-times";
import { useNow } from "#/lib/time";

import { ScheduleStateLabel } from "./ScheduleStateLabel";

/** Buttons and links reach 44 px on touch. */
export const TOUCH = "pointer-coarse:h-11";

/** "Every day at 02:00", or the cron expression when no plain wording exists. */
const cadenceText = (s: Schedule) => cadenceWords(formFromCron(s.cron)) ?? s.cron;

const hhmm = new Intl.DateTimeFormat(undefined, {
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});
const weekday = new Intl.DateTimeFormat(undefined, { weekday: "short" });

/** The schedule's name, a link stretched over its row or card. */
function NameLink({ s }: { s: Schedule }) {
  return (
    <Link
      to="/schedules/$scheduleId"
      params={{ scheduleId: s.schedule_id }}
      data-stretched=""
      className={cn("line-clamp-2 break-words font-medium text-foreground", STRETCHED)}
    >
      {s.name}
    </Link>
  );
}

/** The configuration and its version, a newer version when there is one, and the owner. */
function Meta({ s, configName }: { s: Schedule; configName: string }) {
  return (
    <p className="min-w-0 text-foreground-muted text-xs [overflow-wrap:anywhere]">
      {configName}
      <span className="text-subtle"> · version {s.registry_version}</span>
      {s.newer_version != null && (
        <span className="text-accent"> · version {s.newer_version} available</span>
      )}
      <span className="text-subtle"> · {s.owner}</span>
    </p>
  );
}

/** A stopped or failing schedule: the reason in a sentence and a link to the next step. */
export function AttentionCard({ s, configName }: { s: Schedule; configName: string }) {
  const why = needsAction(s);
  if (!why) return null;
  const stopped = s.state === "stopped";
  const stepClass = cn(textAction({ primary: true }), TOUCH);
  const stepText = (
    <>
      {why.step}
      <span className="sr-only">: {s.name}</span>
      <span aria-hidden="true"> →</span>
    </>
  );
  return (
    <li
      className={cn(
        CLICKABLE,
        "flex flex-col gap-2 rounded-xl border px-4 py-3",
        stopped ? "border-danger/30 bg-danger-surface" : "border-warning-border bg-warning-surface"
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <NameLink s={s} />
        {stopped ? (
          <ScheduleStateLabel schedule={s} className="text-sm" />
        ) : (
          <span className="inline-flex items-center gap-1.5 whitespace-nowrap font-medium text-sm text-warning">
            <WarningIcon className="size-3.5" />
            Failing
          </span>
        )}
      </div>
      <p className="max-w-[70ch] text-pretty text-foreground text-sm">{why.text}</p>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Meta s={s} configName={configName} />
        {why.target === "runs" ? (
          <Link to="/runs" search={{ schedule: s.schedule_id }} className={stepClass}>
            {stepText}
          </Link>
        ) : (
          <Link
            to="/schedules/$scheduleId"
            params={{ scheduleId: s.schedule_id }}
            className={stepClass}
          >
            {stepText}
          </Link>
        )}
      </div>
    </li>
  );
}

/** An active schedule in the list by name: its cadence, time zone and next start. */
export function ActiveRow({ s, configName }: { s: Schedule; configName: string }) {
  const now = useNow();
  return (
    <li
      className={cn(
        CLICKABLE,
        "grid grid-cols-1 gap-x-6 gap-y-1 px-4 py-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,13rem)]"
      )}
    >
      <div className="flex min-w-0 flex-col gap-0.5">
        <NameLink s={s} />
        <Meta s={s} configName={configName} />
      </div>
      <div className="flex min-w-0 flex-col text-xs">
        <span className="text-foreground-muted">{cadenceText(s)}</span>
        <span className="text-subtle tabular-nums">
          {s.timezone}
          {s.next_start_at && ` · next ${startsIn(s.next_start_at, now)}`}
        </span>
      </div>
    </li>
  );
}

/** A paused schedule, with the reason given when it was paused, or a deleted schedule. */
export function RestingRow({ s, configName }: { s: Schedule; configName: string }) {
  return (
    <li className={cn(CLICKABLE, "flex flex-col gap-1 px-4 py-3")}>
      <div className="flex items-start justify-between gap-3">
        <NameLink s={s} />
        <ScheduleStateLabel schedule={s} className="text-sm" />
      </div>
      {s.state_reason && s.state === "paused" && (
        <p className="text-pretty text-foreground-muted text-sm">“{s.state_reason}”</p>
      )}
      <Meta s={s} configName={configName} />
    </li>
  );
}

/** A titled group of schedules in one card; nothing when it is empty. */
export function Group({
  id,
  title,
  hint,
  items,
  children,
}: {
  id: string;
  title: string;
  hint?: string;
  items: Schedule[];
  children: (s: Schedule) => ReactNode;
}) {
  if (items.length === 0) return null;
  return (
    <section aria-labelledby={id} className="flex flex-col gap-2">
      <SectionHeading id={id} title={title} count={items.length}>
        {hint}
      </SectionHeading>
      <Card className="overflow-clip bg-content bg-none">
        <ul className="divide-y">{items.map(children)}</ul>
      </Card>
    </section>
  );
}

/** The next starts of the active schedules, by day in the viewer's time zone. */
export function NextStartsPanel({ starts }: { starts: NextStarts }) {
  const now = useNow();
  return (
    <section aria-labelledby="schedules-next-starts" className="flex flex-col gap-2">
      <SectionHeading id="schedules-next-starts" title="Next starts" />
      <Card className="overflow-clip bg-content bg-none">
        {starts.groups.length === 0 && (
          <p className="px-4 py-3 text-foreground-muted text-sm">No schedule starts a run.</p>
        )}
        {starts.groups.map((g) => (
          <div key={g.bucket} className="border-t first:border-t-0">
            <h3 className="px-4 pt-2.5 pb-1 font-medium text-[11px] text-subtle uppercase tracking-[0.06em]">
              {g.bucket}
            </h3>
            <ul>
              {g.items.map((s) => {
                const iso = s.next_start_at as string;
                const far = g.bucket === "This week" || g.bucket === "Later";
                return (
                  <li
                    key={s.schedule_id}
                    className={cn(
                      CLICKABLE,
                      "grid grid-cols-[3rem_minmax(0,1fr)] gap-x-3 px-4 py-1.5"
                    )}
                  >
                    <time
                      dateTime={iso}
                      title={`${formatInZone(iso, s.timezone)} (${s.timezone})`}
                      className="pt-px font-medium text-sm tabular-nums"
                    >
                      {hhmm.format(new Date(iso))}
                    </time>
                    <span className="flex min-w-0 flex-col">
                      <Link
                        to="/schedules/$scheduleId"
                        params={{ scheduleId: s.schedule_id }}
                        data-stretched=""
                        className={cn("truncate text-foreground text-sm", STRETCHED)}
                        title={s.name}
                      >
                        {s.name}
                      </Link>
                      <span className="text-subtle text-xs tabular-nums">
                        {far ? weekday.format(new Date(iso)) : startsIn(iso, now)}
                      </span>
                    </span>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
        {starts.more > 0 && (
          <p className="border-t px-4 py-2 text-subtle text-xs">{starts.more} more later</p>
        )}
      </Card>
      <p className="text-subtle text-xs">In your time zone. Hover a time for the schedule’s.</p>
    </section>
  );
}

/** Rows of a card while its schedules load. */
export function RowsSkeleton({ rows = 4 }: { rows?: number }) {
  return (
    <Card className="overflow-clip bg-content bg-none">
      <ul aria-hidden="true" className="divide-y">
        {Array.from({ length: rows }, (_, i) => (
          <li key={i} className="flex flex-col gap-2 px-4 py-3.5">
            <Bar className="h-3.5 w-48 max-w-full" />
            <Bar className="h-3 w-72 max-w-full" />
          </li>
        ))}
      </ul>
    </Card>
  );
}

/** The loaded page's shape, so nothing moves when the schedules arrive. */
export function SchedulesSkeleton() {
  return (
    <div aria-busy="true">
      <output className="sr-only">Loading schedules</output>
      <div
        aria-hidden="true"
        className="grid grid-cols-1 items-start gap-8 lg:grid-cols-[minmax(0,1fr)_18rem]"
      >
        <div className="flex min-w-0 flex-col gap-6">
          <div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
            <Bar className="h-9 w-full max-w-[26rem] pointer-coarse:h-12" />
            <Bar className="h-10 w-full sm:w-80" />
          </div>
          <div className="flex flex-col gap-2">
            <div className="flex h-5 items-center">
              <Bar className="h-3.5 w-28" />
            </div>
            <RowsSkeleton rows={5} />
          </div>
        </div>
        <div className="flex flex-col gap-2">
          <div className="flex h-5 items-center">
            <Bar className="h-3.5 w-24" />
          </div>
          <Card className="overflow-clip bg-content bg-none">
            <div className="flex flex-col gap-3 px-4 py-3">
              {Array.from({ length: 4 }, (_, i) => (
                <div key={i} className="grid grid-cols-[3rem_minmax(0,1fr)] gap-x-3">
                  <Bar className="h-3.5 w-10" />
                  <Bar className="h-3.5 w-36 max-w-full" />
                </div>
              ))}
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}
