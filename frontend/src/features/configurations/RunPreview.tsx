import { type ReactNode, useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { cn } from "tailwind-variants";

import { type RunKind, runKind, runOutcomeText } from "#/domain/run-history";
import { fmt, phaseLabel, runCounts, writtenCount } from "#/domain/run-state";
import { formatAbsolute, formatDuration, formatRelative } from "#/lib/time";

import type { DayRuns, PreviewHandler } from "./RunsPerDay";

const OPEN_DELAY_MS = 150;
/** After a preview closes, the next one opens without waiting and without fading in. */
const WARM_MS = 300;
const HEADER_HEIGHT = 56;

const DOT: Record<RunKind, string> = {
  applied: "bg-success-strong",
  unchanged: "bg-subtle-muted/55",
  failed: "bg-danger-strong",
  review: "bg-warning-strong",
  running: "bg-accent-strong",
  other: "bg-subtle-muted/30",
};

interface Shown {
  day: DayRuns;
  rect: DOMRect;
  warm: boolean;
}

function Dot({ kind }: { kind: RunKind }) {
  return <span className={cn("h-3 w-[3px] shrink-0 rounded-[1px]", DOT[kind])} />;
}

function OneRun({ day }: { day: DayRuns }) {
  const run = day.runs[0];
  const outcome = runOutcomeText(run);
  return (
    <>
      <p className="flex items-center gap-2 font-medium">
        <Dot kind={runKind(run)} />
        {phaseLabel(run)}
      </p>
      {outcome && <p className="text-stone-200">{outcome}</p>}
      <p className="text-stone-400">
        {formatRelative(run.started_at)}
        {run.finished_at
          ? ` · took ${formatDuration(run.started_at, run.finished_at)}`
          : " · not finished"}
      </p>
      <p className="whitespace-nowrap text-stone-500">{formatAbsolute(run.started_at)}</p>
    </>
  );
}

function ManyRuns({ day }: { day: DayRuns }) {
  const failed = day.runs.filter((r) => runKind(r) === "failed").length;
  const written = day.runs.reduce((n, r) => {
    const counts = r.phase === "applied" ? runCounts(r) : undefined;
    return n + (counts ? writtenCount(counts) : 0);
  }, 0);
  const facts = [
    `${day.runs.length} runs`,
    failed > 0 && `${failed} failed`,
    written > 0 && `${fmt(written)} ${written === 1 ? "change" : "changes"}`,
  ].filter(Boolean);
  return (
    <>
      <p className="font-medium">
        {day.date.toLocaleDateString("en", { weekday: "long", month: "short", day: "numeric" })}
      </p>
      <p className="text-stone-400">{facts.join(" · ")}</p>
      <ul className="mt-1 flex flex-col gap-0.5">
        {day.runs.slice(0, 5).map((r) => (
          <li key={r.run_id} className="flex flex-col">
            <span className="flex items-center gap-2">
              <Dot kind={runKind(r)} />
              <span className="text-stone-400 tabular-nums">
                {new Date(r.started_at).toLocaleTimeString("en", {
                  hour: "numeric",
                  minute: "2-digit",
                })}
              </span>
              <span>{phaseLabel(r)}</span>
            </span>
            {runKind(r) === "failed" && (
              <span className="pl-[calc(3px+0.5rem)] text-stone-400">{runOutcomeText(r)}</span>
            )}
          </li>
        ))}
        {day.runs.length > 5 && <li className="text-stone-400">and {day.runs.length - 5} more</li>}
      </ul>
    </>
  );
}

function PreviewCard({ shown, onDismiss }: { shown: Shown; onDismiss: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const single = shown.day.runs.length === 1;

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const { rect } = shown;
    const width = el.offsetWidth;
    const height = el.offsetHeight;
    const left = Math.max(
      8,
      Math.min(
        rect.left + rect.width / 2 - width / 2,
        document.documentElement.clientWidth - width - 8
      )
    );
    const above = rect.top - height - 8;
    el.style.left = `${left}px`;
    el.style.top = `${above < HEADER_HEIGHT ? rect.bottom + 8 : above}px`;
    el.style.visibility = "visible";
  }, [shown]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onDismiss();
    window.addEventListener("scroll", onDismiss, true);
    window.addEventListener("resize", onDismiss);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("scroll", onDismiss, true);
      window.removeEventListener("resize", onDismiss);
      window.removeEventListener("keydown", onKey);
    };
  }, [onDismiss]);

  return createPortal(
    <div
      ref={ref}
      role="tooltip"
      style={{ position: "fixed", left: 0, top: 0, visibility: "hidden" }}
      className={cn(
        "pointer-events-none z-50 flex w-max max-w-72 flex-col gap-0.5 rounded-xl border border-stone-700 bg-stone-800 px-3 py-2 font-sans text-white text-xs drop-shadow-lg transition-none",
        !shown.warm && "motion-safe:fade-in motion-safe:animate-in motion-safe:duration-150"
      )}
    >
      {single ? <OneRun day={shown.day} /> : <ManyRuns day={shown.day} />}
      <p className="mt-1 text-[11px] text-stone-500">
        {single ? "Click to open the run" : "Click to open the newest run of this day"}
      </p>
    </div>,
    document.body
  );
}

/**
 * The hover preview of a bar of `RunsPerDay`. `host` is rendered once beside the charts; `show` and
 * `hide` go to every chart. The card opens after a short delay, and straight away while another
 * one is open or closed a moment ago.
 */
export function useRunPreview(): { show: PreviewHandler; host: ReactNode } {
  const [shown, setShown] = useState<Shown | null>(null);
  const current = useRef<Shown | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const warmUntil = useRef(0);

  const set = useCallback((next: Shown | null) => {
    current.current = next;
    setShown(next);
  }, []);

  const hide = useCallback(() => {
    window.clearTimeout(timer.current);
    if (!current.current) return;
    warmUntil.current = Date.now() + WARM_MS;
    set(null);
  }, [set]);

  const show = useCallback<PreviewHandler>(
    (day, el, immediate) => {
      window.clearTimeout(timer.current);
      if (!day || !el) {
        hide();
        return;
      }
      const warm = current.current !== null || Date.now() < warmUntil.current;
      const open = () => set({ day, rect: el.getBoundingClientRect(), warm });
      if (warm || immediate) open();
      else timer.current = window.setTimeout(open, OPEN_DELAY_MS);
    },
    [hide, set]
  );

  useEffect(() => () => window.clearTimeout(timer.current), []);

  const host = shown && (
    <PreviewCard
      key={shown.day.date.getTime() + shown.day.runs[0].run_id}
      shown={shown}
      onDismiss={hide}
    />
  );
  return { show, host };
}
