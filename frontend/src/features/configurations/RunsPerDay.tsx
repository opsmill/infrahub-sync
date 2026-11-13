import { Link } from "@tanstack/react-router";
import { type KeyboardEvent, useRef, useState } from "react";
import { cn } from "tailwind-variants";

import type { Run } from "#/api/types";
import { DAY_MS, type RunKind, runKind, runsByDay } from "#/domain/run-history";

/** Days the chart covers, today included. */
export const CHART_DAYS = 30;

const TICKS = [30, 20, 10, 0];
const position = (daysAgo: number) => (1 - daysAgo / CHART_DAYS) * 100;

/** From the failed runs at the bottom of a bar to the quiet ones at the top. */
const STACK: RunKind[] = ["failed", "applied", "unchanged", "review", "running", "other"];

const SEGMENT: Record<RunKind, string> = {
  applied: "bg-success-strong",
  unchanged: "bg-subtle-muted/55",
  failed: "bg-danger-strong",
  review: "bg-warning-strong",
  running: "bg-accent-strong",
  other: "bg-subtle-muted/30",
};

export interface DayRuns {
  date: Date;
  /** Newest first. */
  runs: Run[];
}

export type PreviewHandler = (day: DayRuns | null, el?: HTMLElement, immediate?: boolean) => void;

const dayLabel = (date: Date) => date.toLocaleDateString("en", { month: "long", day: "numeric" });

/** One tab stop for the whole chart; the arrow keys move between its bars. */
function useRoving(ids: string[]) {
  const [active, setActive] = useState<string>();
  const ref = useRef<HTMLFieldSetElement>(null);
  const current = active && ids.includes(active) ? active : ids[0];
  const onKeyDown = (e: KeyboardEvent<HTMLElement>) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
    const bars = [...(ref.current?.querySelectorAll<HTMLElement>("[data-day]") ?? [])];
    const at = bars.indexOf(document.activeElement as HTMLElement);
    if (at < 0) return;
    e.preventDefault();
    const to =
      e.key === "ArrowLeft"
        ? at - 1
        : e.key === "ArrowRight"
          ? at + 1
          : e.key === "Home"
            ? 0
            : bars.length - 1;
    bars[Math.max(0, Math.min(bars.length - 1, to))]?.focus();
  };
  return { ref, current, setActive, onKeyDown };
}

/** Labels the time axis over a list of charts. */
export function RunsPerDayAxis() {
  return (
    <div aria-hidden="true" className="relative h-4 w-full max-w-md px-1 text-subtle text-xs">
      <span className="relative block h-full">
        {TICKS.map((daysAgo, i) => (
          <span
            key={daysAgo}
            className={cn(
              "absolute top-0 whitespace-nowrap tabular-nums",
              i === 0 ? "" : daysAgo === 0 ? "-translate-x-full" : "-translate-x-1/2"
            )}
            style={{ left: `${Math.min(position(daysAgo), 100)}%` }}
          >
            {daysAgo === 0 ? "today" : i === 0 ? `${daysAgo} days ago` : `${daysAgo} d`}
          </span>
        ))}
      </span>
    </div>
  );
}

/**
 * One bar per day for the last 30 days, as tall as the runs of that day compared with the busiest
 * day of this configuration. A bar stacks its runs by outcome. The chart passes clicks on anything
 * but a bar to the row behind it; a bar opens the newest run of its day.
 */
export function RunsPerDay({
  name,
  runs,
  now,
  loadedSince,
  onPreview,
}: {
  name: string;
  /** Newest first. */
  runs: Run[];
  now: number;
  /** When runs older than the loaded ones exist: days that ended before this are not known to be empty. */
  loadedSince?: number;
  onPreview: PreviewHandler;
}) {
  const days = runsByDay(runs, now, CHART_DAYS);
  const peak = Math.max(1, ...days.map((d) => d.length));
  const startOfToday = new Date(now).setHours(0, 0, 0, 0);
  const roving = useRoving([...days].reverse().flatMap((d) => (d.length ? [d[0].run_id] : [])));

  return (
    <fieldset
      ref={roving.ref}
      aria-label={`Runs per day of ${name}, today on the right. Use the arrow keys to move between days.`}
      onKeyDown={roving.onKeyDown}
      className="pointer-events-none relative z-10 m-0 grid h-7 min-w-0 p-0 w-full max-w-md items-end gap-x-[3px] border-border/70 border-b"
      style={{ gridTemplateColumns: `repeat(${CHART_DAYS}, minmax(0, 1fr))` }}
    >
      {days.map((dayRuns, i) => {
        const dayStart = startOfToday - (CHART_DAYS - 1 - i) * DAY_MS;
        const date = new Date(dayStart);
        if (dayRuns.length === 0) {
          const unknown = loadedSince !== undefined && dayStart + DAY_MS <= loadedSince;
          return (
            <span key={dayStart} aria-hidden="true" className="flex h-full items-end">
              <span
                className={cn(
                  "w-full",
                  unknown ? "border-border border-t border-dashed" : "h-px bg-border"
                )}
              />
            </span>
          );
        }
        const failed = dayRuns.filter((r) => runKind(r) === "failed").length;
        const newest = dayRuns[0];
        return (
          <Link
            key={dayStart}
            to="/runs/$runId"
            params={{ runId: newest.run_id }}
            data-day=""
            aria-label={`${dayLabel(date)}: ${dayRuns.length} ${dayRuns.length === 1 ? "run" : "runs"}${failed ? `, ${failed} failed` : ""}`}
            tabIndex={roving.current === newest.run_id ? 0 : -1}
            onPointerEnter={(e) =>
              e.pointerType !== "touch" && onPreview({ date, runs: dayRuns }, e.currentTarget)
            }
            onPointerLeave={() => onPreview(null)}
            onFocus={(e) => {
              roving.setActive(newest.run_id);
              onPreview({ date, runs: dayRuns }, e.currentTarget, true);
            }}
            onBlur={() => onPreview(null)}
            className="group/day pointer-events-auto flex h-full w-full cursor-pointer items-end rounded-[2px] focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ring"
          >
            <span
              className="flex w-full origin-bottom flex-col-reverse overflow-hidden rounded-t-[2px] transition-transform duration-150 ease-out group-hover/day:scale-y-[1.08] motion-reduce:transition-none"
              style={{ height: `${Math.max(0.18, dayRuns.length / peak) * 100}%` }}
            >
              {STACK.map((kind) => {
                const count = dayRuns.filter((r) => runKind(r) === kind).length;
                return count === 0 ? null : (
                  <span
                    key={kind}
                    className={SEGMENT[kind]}
                    style={{ flexGrow: count, flexBasis: 0, minHeight: 3 }}
                  />
                );
              })}
            </span>
          </Link>
        );
      })}
    </fieldset>
  );
}
