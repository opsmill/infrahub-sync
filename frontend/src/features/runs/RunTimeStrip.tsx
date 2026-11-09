import { Card, Popover } from "@infrahub/ui";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { cn } from "tailwind-variants";

import type { Orchestration, Run } from "#/api/types";
import { ArrowRightIcon, StateIcon } from "#/components/icons";
import { FOCUS, SectionHeading } from "#/components/page";
import { CHANGE_TONE, STEP_TONE, STEP_WORD } from "#/components/run-status";
import {
  executableCount,
  failureDetail,
  fmt,
  type RunState,
  runCounts,
  type StepState,
} from "#/domain/run-state";
import { executionResult, inTimeline, type RunStep, reviewWait } from "#/domain/run-steps";
import { formatSpan, useNow } from "#/lib/time";

import { clock, WaitTime } from "./step-time";

/** What the strip reads from the run page. */
interface RunView {
  run: Run;
  planExpired: boolean;
}

/* ---------- words ---------- */

const ICON_STATE: Record<StepState, RunState> = {
  done: "insync",
  failed: "failed",
  waiting: "review",
  active: "running",
  idle: "idle",
  skipped: "idle",
};
const ICON_TONE: Record<StepState, string> = {
  done: "text-success",
  failed: "text-danger",
  waiting: "text-warning",
  active: "text-accent",
  idle: "text-subtle-muted",
  skipped: "text-subtle-muted",
};

const plural = (n: number, one: string, many: string) => `${fmt(n)} ${n === 1 ? one : many}`;

const isInterrupted = (m: RunView, s: RunStep) =>
  s.exec?.terminal_outcome === "ambiguous" || (s.kind !== "plan" && m.run.reconciliation_required);

/** What the step found or did, in a few words, for a step that is done or failed. */
function shortOutcome(m: RunView, s: RunStep): string {
  const { run } = m;
  const c = runCounts(run);
  const n = executableCount(run) ?? 0;
  if (s.state === "failed" && s.kind !== "review") {
    if (isInterrupted(m, s)) return "Interrupted: result unknown";
    return failureDetail(run)?.sentence ?? "Failed";
  }
  switch (s.kind) {
    case "plan":
      if (!c) return "Plan saved";
      return c.total === 0 ? "No differences" : plural(c.total, "difference", "differences");
    case "review":
      if (s.state === "failed") return "Plan check failed";
      return "Apply requested";
    case "apply":
      if (c && c.total === 0) return "Nothing to write";
      return n === 0 ? "Nothing to write" : `${plural(n, "change", "changes")} written`;
  }
}

/** The state in words for a step that is not done or failed, from its execution when it says more. */
function rowWord(s: RunStep, ended = false): { text: string; tone: string } {
  const o = s.exec;
  if (s.state === "active" && o && !o.terminal_at) {
    if (o.cancellation_requested_at) return { text: "cancelling", tone: "text-warning" };
    if (!o.claimed_at && o.stalled_at) {
      return { text: "still waiting for a worker", tone: "text-warning" };
    }
    if (!o.claimed_at) return { text: "waiting for a worker", tone: STEP_TONE.active };
  }
  if (s.state === "skipped" && o?.terminal_at) {
    return { text: executionResult(o).label.toLowerCase(), tone: STEP_TONE.skipped };
  }
  if (s.state === "skipped" || (s.state === "idle" && ended)) {
    return { text: "not run", tone: STEP_TONE.skipped };
  }
  return { text: STEP_WORD[s.state], tone: STEP_TONE[s.state] };
}

/** A note on the chart for a step with no bar, when there is something worth saying. */
function trackNote(m: RunView, s: RunStep): string | undefined {
  const { run } = m;
  const c = runCounts(run);
  const n = executableCount(run) ?? 0;
  if (s.kind === "review" && s.state === "skipped") {
    if (run.phase === "planned" && run.outcome === "no-change") return "Not needed: no differences";
    if (m.planExpired) return "Plan expired";
    if (n === 0 && (c?.delete ?? 0) > 0) return "Nothing to apply: deletes only";
  }
  if (s.kind === "apply" && s.state === "skipped" && !s.exec && s.outcome) return s.outcome;
  return undefined;
}

function StepIcon({ s, className }: { s: RunStep; className?: string }) {
  return (
    <StateIcon
      state={ICON_STATE[s.state]}
      className={cn("size-4 shrink-0", ICON_TONE[s.state], className)}
    />
  );
}

/* ---------- time ---------- */

const sec = (iso: string | number) => new Date(iso).getTime() / 1000;
const hm = (iso: string) => clock(iso).split(", ")[1] ?? "";
const dayOf = (iso: string) => clock(iso).split(", ")[0] ?? "";
/** "15:19:45", with the date in front when it is not the day the run started. */
const when = (iso: string, startDay: string) => {
  const t = new Date(iso).toLocaleTimeString("en-GB");
  return dayOf(iso) === startDay ? t : `${dayOf(iso)} ${t}`;
};

interface Attempt {
  o: Orchestration;
  from: number;
  claimed?: number;
  to?: number;
  last: boolean;
}

/** The step's executions in order. Two halves of one sync execution count as one attempt each. */
function attemptsOf(s: RunStep): Attempt[] {
  const list = s.shared || s.execs.length === 0 ? (s.exec ? [s.exec] : []) : s.execs;
  const out: Attempt[] = [];
  list.forEach((o, i) => {
    const start = o.submitted_at ?? o.claimed_at;
    if (!start) return;
    out.push({
      o,
      from: sec(start),
      claimed: o.claimed_at ? sec(o.claimed_at) : undefined,
      to: o.terminal_at ? sec(o.terminal_at) : undefined,
      last: i === list.length - 1,
    });
  });
  return out;
}

interface Zone {
  /** Position and width on the axis, in percent. */
  left: number;
  width: number;
  /** The time the zone spans, in seconds. */
  from: number;
  to: number;
  /** Review drawn shorter than its real length, so the steps around it stay readable. */
  shortened: boolean;
}

const MIN_SHARE = 10;
const REVIEW_SHARE = 28;
/** Review is shortened only when it is this many times longer than everything else. */
const SHORTEN_RATIO = 3;

function zones(steps: RunStep[], now: number): (Zone | undefined)[] {
  const spans = steps.map((s) => {
    const wait = reviewWait(s);
    if (wait) return { from: sec(wait.from), to: wait.to ? sec(wait.to) : now, review: true };
    const as = attemptsOf(s);
    if (as.length === 0) return undefined;
    return { from: as[0].from, to: as.at(-1)?.to ?? now, review: false };
  });
  const placed = spans.filter(Boolean) as { from: number; to: number; review: boolean }[];
  const workSeconds = placed.filter((p) => !p.review).reduce((t, p) => t + (p.to - p.from), 0);
  const reviewSpan = placed.find((p) => p.review);
  const reviewSeconds = reviewSpan ? reviewSpan.to - reviewSpan.from : 0;
  const shorten = Boolean(reviewSpan) && reviewSeconds > SHORTEN_RATIO * Math.max(workSeconds, 1);
  const reviewOpen = steps.some((s) => s.kind === "review" && s.state === "waiting");
  const reviewShare = shorten ? (reviewOpen ? 100 - MIN_SHARE * 3 : REVIEW_SHARE) : 0;

  // Everything not shortened shares the rest of the width by duration, with a floor per step.
  const scaled = placed.filter((p) => !(shorten && p.review));
  const rest = 100 - reviewShare;
  const total = scaled.reduce((t, p) => t + (p.to - p.from), 0) || 1;
  const raw = scaled.map((p) => Math.max(((p.to - p.from) / total) * rest, MIN_SHARE));
  const fit = scaled.length ? rest / raw.reduce((a, b) => a + b, 0) : 1;

  let left = 0;
  let k = 0;
  return spans.map((p) => {
    if (!p) return undefined;
    const width = shorten && p.review ? reviewShare : raw[k++] * fit;
    const z = { left, width, from: p.from, to: p.to, shortened: shorten && p.review };
    left += width;
    return z;
  });
}

/* ---------- bars ---------- */

/** The step ran: calm enough to sit in a summary, still read as its state. */
const RUN_BAR: Record<StepState, string> = {
  done: "bg-success-strong/80",
  failed: "bg-danger-strong",
  active: "bg-accent-strong animate-pulse motion-reduce:animate-none",
  waiting: "bg-warning-strong",
  idle: "bg-subtle-muted",
  skipped: "bg-subtle-muted",
};
/** The step waited for a worker: the start of the same bar, in the same hue, faded. */
const QUEUE_BAR: Record<StepState, string> = {
  done: "bg-success-strong/25 dark:bg-success-strong/40",
  failed: "bg-danger-strong/25 dark:bg-danger-strong/40",
  active: "bg-accent-strong/25 dark:bg-accent-strong/40",
  waiting: "bg-warning-strong/25 dark:bg-warning-strong/40",
  idle: "bg-subtle-muted/30 dark:bg-subtle-muted/50",
  skipped: "bg-subtle-muted/30 dark:bg-subtle-muted/50",
};
/** No worker has picked it up for longer than the service expects. */
const STALLED_BAR = "bg-warning-strong/40 dark:bg-warning-strong/50";
/** The worker stopped reporting: what happened after it was picked up is not known. */
const UNKNOWN_BAR =
  "bg-warning-strong/20 bg-[repeating-linear-gradient(135deg,var(--warning-strong)_0_2px,transparent_2px_6px)]";

function attemptClasses(s: RunStep, a: Attempt): { queue: string; run: string } {
  if (!a.last) {
    const failed = a.o.terminal_outcome !== "succeeded";
    return {
      queue: failed ? QUEUE_BAR.failed : QUEUE_BAR.skipped,
      run: failed ? "bg-danger-strong/50" : "bg-subtle-muted",
    };
  }
  const stalled = !a.claimed && Boolean(a.o.stalled_at) && !a.to;
  return {
    queue: stalled ? STALLED_BAR : QUEUE_BAR[s.state],
    run: a.o.terminal_outcome === "ambiguous" ? UNKNOWN_BAR : RUN_BAR[s.state],
  };
}

function AttemptPill({ s, a, zone, now }: { s: RunStep; a: Attempt; zone: Zone; now: number }) {
  const span = Math.max(zone.to - zone.from, 1);
  const to = a.to ?? now;
  const queued = (a.claimed ?? to) - a.from;
  const ran = a.claimed ? to - a.claimed : 0;
  const sum = Math.max(queued + ran, 0.001);
  const c = attemptClasses(s, a);
  return (
    <span
      className={cn(
        "absolute top-1/2 flex h-3 -translate-y-1/2 overflow-hidden rounded-full",
        !a.last && "opacity-70"
      )}
      style={{
        left: `${((a.from - zone.from) / span) * 100}%`,
        width: `${((to - a.from) / span) * 100}%`,
        minWidth: "0.75rem",
      }}
    >
      {queued > 0 && <span className={c.queue} style={{ flexGrow: queued / sum }} />}
      {(ran > 0 || queued <= 0) && (
        <span className={c.run} style={{ flexGrow: Math.max(ran / sum, 0.03) }} />
      )}
    </span>
  );
}

/** Two short slanted strokes: the axis is cut here. */
const AxisBreak = ({ tone }: { tone: string }) => (
  <svg viewBox="0 0 12 12" className={cn("size-3", tone)} aria-hidden="true">
    <path
      d="M3.5 10.5l3-9M7 10.5l3-9"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
    />
  </svg>
);

const CHECK_DOT: Record<string, string> = {
  succeeded: "bg-success-strong",
  failed: "bg-danger-strong",
};

function ReviewLine({ s, zone }: { s: RunStep; zone: Zone }) {
  const open = s.state === "waiting";
  const span = Math.max(zone.to - zone.from, 1);
  return (
    <span
      className="absolute inset-y-0 flex items-center justify-center"
      style={{ left: `${zone.left}%`, width: `${zone.width}%` }}
    >
      {/* The dashed line, in two halves around the shortened mark so nothing has to mask it */}
      {(zone.shortened
        ? ["left-0 right-[calc(50%+0.625rem)]", "right-0 left-[calc(50%+0.625rem)]"]
        : ["inset-x-0"]
      ).map((pos) => (
        <span
          key={pos}
          className={cn(
            "absolute top-1/2 h-0 border-dashed",
            pos,
            open
              ? "border-warning-strong border-t-2"
              : s.state === "failed"
                ? "border-danger-strong/70 border-t-[1.5px]"
                : "border-subtle-muted border-t-[1.5px]"
          )}
        />
      ))}
      {zone.shortened && (
        <span className="relative">
          <AxisBreak
            tone={
              open
                ? "text-warning-strong"
                : s.state === "failed"
                  ? "text-danger-strong"
                  : "text-subtle"
            }
          />
        </span>
      )}
      {/* Each check of the saved plan, where it happened during the review */}
      {s.execs.map((o) => {
        const t = o.terminal_at ?? o.claimed_at ?? o.submitted_at;
        if (!t) return null;
        const at = Math.min(Math.max((sec(t) - zone.from) / span, 0.04), 0.96);
        return (
          <span
            key={o.flow_run_id}
            className={cn(
              "absolute top-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full ring-2 ring-content",
              o.terminal_at
                ? (CHECK_DOT[o.terminal_outcome ?? ""] ?? "bg-subtle-muted")
                : "bg-accent-strong motion-safe:animate-pulse"
            )}
            style={{ left: `${at * 100}%` }}
          />
        );
      })}
      {open && (
        <span className="absolute top-1/2 right-0 size-2 translate-x-1/2 -translate-y-1/2 rounded-full bg-warning-strong ring-4 ring-warning-surface motion-safe:animate-pulse" />
      )}
    </span>
  );
}

/* ---------- hover card content ---------- */

/** Time since `from`, counted every second, for a row still running or waiting. */
function Elapsed({ from }: { from: string }) {
  const now = useNow(1000);
  return <>{formatSpan(from, now)} so far</>;
}

function TipRow({
  label,
  from,
  to,
  swatch,
  startDay,
}: {
  label: string;
  from?: string | null;
  to?: string | null;
  swatch: string;
  startDay: string;
}) {
  if (!from) return null;
  return (
    <div className="grid grid-cols-[0.625rem_minmax(0,1fr)_auto] items-baseline gap-x-2">
      <span className={cn("size-2.5 translate-y-px rounded-full", swatch)} />
      <span className="text-foreground-muted">{label}</span>
      <span className="text-right font-medium text-foreground tabular-nums">
        {to ? formatSpan(from, to) : <Elapsed from={from} />}
      </span>
      <span />
      <span className="col-span-2 text-subtle text-[11px] tabular-nums">
        {when(from, startDay)} → {to ? when(to, startDay) : "now"}
      </span>
    </div>
  );
}

function AttemptTip({ s, a, startDay }: { s: RunStep; a: Attempt; startDay: string }) {
  const { o } = a;
  const c = attemptClasses(s, a);
  return (
    <>
      <TipRow
        label={
          !o.claimed_at && o.stalled_at && !o.terminal_at
            ? "Still waiting for a worker"
            : "Waiting for a worker"
        }
        from={o.submitted_at}
        to={o.claimed_at ?? o.terminal_at}
        swatch={c.queue}
        startDay={startDay}
      />
      <TipRow
        label={o.terminal_outcome === "ambiguous" ? "No report from the worker" : "Running"}
        from={o.claimed_at}
        to={o.terminal_at}
        swatch={c.run}
        startDay={startDay}
      />
      {o.cancellation_requested_at && (
        <span className="text-foreground-muted">
          Cancellation requested at {when(o.cancellation_requested_at, startDay)}
        </span>
      )}
    </>
  );
}

type CountRow = {
  action: keyof typeof CHANGE_TONE;
  n: number;
  /** The label after the number, singular then plural: "create", "creates". */
  label: [string, string];
  /** A short qualifier, such as "not applied". */
  extra?: string;
};

/** The counts as short rows that line up: sign, number, label. Only actions with changes. */
function CountRows({ rows }: { rows: CountRow[] }) {
  const shown = rows.filter((r) => r.n > 0);
  if (shown.length === 0) return null;
  return (
    <span className="grid grid-cols-[0.75rem_auto_minmax(0,1fr)] items-baseline gap-x-1.5 gap-y-1">
      {shown.map((r) => (
        <span key={r.action} className="contents">
          <span
            aria-hidden="true"
            className={cn("text-center font-medium", CHANGE_TONE[r.action].text)}
          >
            {CHANGE_TONE[r.action].sign}
          </span>
          <span className="text-right font-medium text-foreground tabular-nums">{fmt(r.n)}</span>
          <span className="text-foreground-muted">
            {r.n === 1 ? r.label[0] : r.label[1]}
            {r.extra && <span className="text-subtle"> · {r.extra}</span>}
          </span>
        </span>
      ))}
    </span>
  );
}

/** What the step found or did: a headline, the counts as rows, and what to do next. */
function TipSummary({ m, s }: { m: RunView; s: RunStep }) {
  const { run } = m;
  const c = runCounts(run);
  const n = executableCount(run) ?? 0;
  const f = failureDetail(run);
  const settled = s.state === "done" || s.state === "failed";
  let headline = settled
    ? shortOutcome(m, s)
    : s.state === "active"
      ? (s.outcome ?? rowWord(s).text)
      : (trackNote(m, s) ?? rowWord(s).text);
  let rows: CountRow[] = [];
  let note: string | undefined;
  let aside: string | undefined;
  let next: string | undefined;

  if (s.kind === "plan" && s.state === "done" && c && c.total > 0) {
    rows = [
      { action: "create", n: c.create, label: ["create", "creates"] },
      { action: "update", n: c.update, label: ["update", "updates"] },
      { action: "delete", n: c.delete, label: ["delete", "deletes"], extra: "not applied" },
    ];
  } else if (s.kind === "apply" && s.state === "done" && c && n > 0) {
    // The backend counts the apply from the saved plan: on success, every create and update was written.
    rows = [
      { action: "create", n: c.create, label: ["created", "created"] },
      { action: "update", n: c.update, label: ["updated", "updated"] },
      { action: "delete", n: c.delete, label: ["delete skipped", "deletes skipped"] },
    ];
    note = "Checked the plan before writing.";
    if (c.delete > 0) aside = "Apply never deletes.";
  } else if (s.kind === "review" && s.state === "waiting") {
    headline = `${plural(n, "change", "changes")} to review`;
    next = "Apply them, or plan again.";
  } else if (s.state === "failed" && s.kind !== "review") {
    if (isInterrupted(m, s)) {
      headline = "Result unknown";
      note = "The worker stopped without reporting back. Some changes may have been written.";
      next = "Check the branch, then plan again.";
    } else {
      if (f?.appliedCount !== undefined) {
        note = `${fmt(f.appliedCount)} of ${fmt(n)} changes were written before the failure.`;
      }
      next = "Plan again to retry.";
    }
  } else if (s.kind === "review" && s.state === "failed") {
    next = "Plan again to apply these changes.";
  } else if (s.state === "skipped" && (s.outcome?.startsWith("Can't run") || m.planExpired)) {
    next = "Plan again to apply these changes.";
  }

  const failed = s.state === "failed";
  return (
    <span className="flex flex-col gap-2">
      <span
        className={cn(
          "text-pretty font-medium text-[13px] first-letter:uppercase",
          failed ? "text-danger" : "text-foreground"
        )}
      >
        {headline}
      </span>
      {f?.errorLabel && failed && s.kind !== "review" && !isInterrupted(m, s) && (
        <span className="font-mono text-[11px] text-subtle [overflow-wrap:anywhere]">
          {f.errorLabel}
        </span>
      )}
      <CountRows rows={rows} />
      {note && <span className="text-pretty text-foreground-muted">{note}</span>}
      {aside && <span className="text-pretty text-subtle">{aside}</span>}
      {next && <span className="text-pretty font-medium text-foreground">{next}</span>}
    </span>
  );
}

/** What happened during the step: its result, its counts and its times. */
function StepTip({
  m,
  s,
  startDay,
  onOpen,
}: {
  m: RunView;
  s: RunStep;
  startDay: string;
  /** Scroll to the step in the timeline and open its details. */
  onOpen: () => void;
}) {
  const wait = reviewWait(s);
  const attempts = s.kind === "review" ? [] : attemptsOf(s);
  const status = isInterrupted(m, s)
    ? "Interrupted"
    : s.exec && !s.shared
      ? executionResult(s.exec).label
      : rowWord(s).text;
  return (
    <span className="flex w-76 cursor-text select-text flex-col gap-2.5 p-3 text-left text-xs">
      <span className="flex items-center gap-2">
        <StepIcon s={s} className="size-3.5" />
        <span className="font-medium text-[13px]">{s.name}</span>
        <span
          className={cn(
            "ml-auto first-letter:uppercase",
            s.state === "done" ? "text-subtle" : STEP_TONE[s.state]
          )}
        >
          {status}
        </span>
      </span>
      <TipSummary m={m} s={s} />
      {(attempts.length > 0 || wait) && (
        <span className="flex flex-col gap-2 border-t pt-2.5">
          {attempts.map((a, i) => (
            <span key={a.o.flow_run_id + String(i)} className="flex flex-col gap-1.5">
              {attempts.length > 1 && (
                <span className="font-medium text-foreground">
                  Attempt {i + 1}
                  {!a.last && (
                    <span className="font-normal text-subtle"> · {executionResult(a.o).label}</span>
                  )}
                </span>
              )}
              <AttemptTip s={s} a={a} startDay={startDay} />
            </span>
          ))}
          {wait && (
            <TipRow
              label={wait.to ? "In review" : "In review, still waiting"}
              from={wait.from}
              to={wait.to}
              swatch={wait.to ? "bg-subtle-muted" : "bg-warning-strong"}
              startDay={startDay}
            />
          )}
          {s.kind === "review" &&
            s.execs.map((o) => (
              <span key={o.flow_run_id} className="flex items-center gap-2 text-foreground-muted">
                <span
                  className={cn(
                    "size-2.5 rounded-full",
                    o.terminal_at
                      ? (CHECK_DOT[o.terminal_outcome ?? ""] ?? "bg-subtle-muted")
                      : "bg-accent-strong"
                  )}
                />
                {o.terminal_at
                  ? `Plan check ${o.terminal_outcome === "succeeded" ? "passed" : "failed"}`
                  : "Checking the plan"}
                <span className="ml-auto text-subtle tabular-nums">
                  {o.terminal_at ? when(o.terminal_at, startDay) : "now"}
                </span>
              </span>
            ))}
          {s.shared && (
            <span className="text-pretty text-foreground-muted">
              One execution, without review, split when the plan was saved.
            </span>
          )}
        </span>
      )}
      <button
        type="button"
        onClick={onOpen}
        className={cn(
          "-mx-1 -mb-1 inline-flex min-h-7 cursor-pointer items-center gap-1 self-start rounded-md px-1 font-medium text-accent transition-colors duration-150 ease-out hover:underline motion-reduce:transition-none",
          FOCUS
        )}
      >
        Show details below
        <ArrowRightIcon className="size-3 rotate-90" />
      </button>
    </span>
  );
}

/* ---------- rows ---------- */

/** Columns: the step, its bar on the axis, its duration. On phones the step sits above its bar. */
const COLS = "grid-cols-[minmax(0,1fr)_5.5rem] sm:grid-cols-[minmax(0,10rem)_minmax(0,1fr)_5.5rem]";

function StepLabel({ m, s }: { m: RunView; s: RunStep }) {
  const settled = s.state === "done" || s.state === "failed";
  const word = settled ? undefined : rowWord(s, Boolean(m.run.finished_at));
  const attempts = s.shared ? 0 : s.kind === "review" ? 0 : s.execs.length;
  return (
    <span className="col-span-2 flex min-w-0 items-center gap-2 overflow-hidden sm:col-span-1 sm:flex-col sm:items-stretch sm:gap-0.5">
      <span className="flex min-w-0 items-center gap-2">
        <StepIcon s={s} />
        <span className={cn("font-medium text-sm", !inTimeline(s) && "text-foreground-muted")}>
          {s.name}
        </span>
      </span>
      <span
        className={cn(
          "min-w-0 max-w-full truncate text-xs sm:pl-6",
          settled ? (s.state === "failed" ? "text-danger" : "text-subtle") : word?.tone
        )}
      >
        {settled ? shortOutcome(m, s) : word?.text}
        {attempts > 1 && <span className="text-subtle"> · {attempts} attempts</span>}
      </span>
    </span>
  );
}

function Duration({ s, z, now }: { s: RunStep; z?: Zone; now: number }) {
  const wait = reviewWait(s);
  const as = s.kind === "review" ? [] : attemptsOf(s);
  const last = as.at(-1);
  if (!z || (!wait && !last)) {
    return (
      <span aria-hidden="true" className="text-subtle">
        —
      </span>
    );
  }
  const from = wait ? wait.from : (as[0].o.submitted_at ?? as[0].o.claimed_at ?? "");
  const to = wait ? wait.to : (last?.o.terminal_at ?? undefined);
  const queued =
    last?.o.submitted_at && last.o.claimed_at
      ? sec(last.o.claimed_at) - sec(last.o.submitted_at)
      : 0;
  return (
    <>
      <span
        className={cn(
          "font-medium text-[13px]",
          wait && !wait.to ? "text-warning" : "text-foreground"
        )}
      >
        {wait ? <WaitTime wait={wait} /> : formatSpan(from, to ?? now * 1000)}
      </span>
      {queued >= 1 && last?.o.submitted_at && last.o.claimed_at && (
        <span className="text-[11px] text-subtle">
          {formatSpan(last.o.submitted_at, last.o.claimed_at)} in queue
        </span>
      )}
      {z.shortened && wait?.to && <span className="text-[11px] text-subtle">shortened</span>}
      {/* No hover on touch screens: the clock times go here instead */}
      <span className="hidden text-[11px] text-subtle pointer-coarse:block">
        {hm(from)}–{to ? hm(to) : "now"}
      </span>
    </>
  );
}

const ROW = cn(
  "relative grid w-full items-center gap-x-5 gap-y-1 rounded-lg px-3 py-2 text-left",
  COLS
);

function GanttRow({
  m,
  s,
  z,
  selected,
  onSelect,
  now,
  startDay,
  noteAt = 0,
  card,
  dimmed,
}: {
  m: RunView;
  s: RunStep;
  z?: Zone;
  /** The hover card of the strip, shared by its rows. */
  card: HoverCard;
  /** Another row is hovered or focused. */
  dimmed: boolean;
  /** Where a note in place of a bar starts: the end of the step before it, in percent. */
  noteAt?: number;
  selected: boolean;
  onSelect: (i: number) => void;
  now: number;
  startDay: string;
}) {
  const wait = reviewWait(s);
  const note = z ? undefined : trackNote(m, s);
  // The card points at the bar, not at the whole row.
  const anchor = useRef<HTMLSpanElement>(null);
  const track = (
    <span className="relative block h-7 min-w-0">
      <span
        ref={anchor}
        aria-hidden="true"
        className="pointer-events-none absolute inset-y-0"
        style={z ? { left: `${z.left}%`, width: `${z.width}%` } : { left: 0, right: 0 }}
      />
      {z && wait && <ReviewLine s={s} zone={z} />}
      {z && !wait && (
        <span
          className="absolute inset-y-0 transition-[filter] duration-150 ease-out group-hover:brightness-105 motion-reduce:transition-none"
          style={{ left: `${z.left}%`, width: `${z.width}%` }}
        >
          {attemptsOf(s).map((a, i) => (
            <AttemptPill key={a.o.flow_run_id + String(i)} s={s} a={a} zone={z} now={now} />
          ))}
        </span>
      )}
      {note && (
        <span
          className="absolute inset-y-0 flex items-center whitespace-nowrap text-foreground-muted text-xs"
          style={
            noteAt < 65
              ? { left: `calc(${noteAt}% + 0.625rem)` }
              : { right: `calc(${100 - noteAt}% + 0.625rem)` }
          }
        >
          {note}
        </span>
      )}
    </span>
  );
  const duration = (
    <span className="flex flex-col items-end whitespace-nowrap text-right tabular-nums">
      <Duration s={s} z={z} now={now} />
    </span>
  );

  // A step with nothing to open stays a plain row: no hover card, no click.
  if (!inTimeline(s)) {
    return (
      <li
        className={cn(
          "transition-opacity duration-150 ease-out motion-reduce:transition-none",
          dimmed && "opacity-50"
        )}
      >
        <div className={ROW}>
          <StepLabel m={m} s={s} />
          {track}
          {duration}
        </div>
      </li>
    );
  }

  const open = card.tip === s.index;
  return (
    <li
      className={cn(
        "transition-opacity duration-150 ease-out motion-reduce:transition-none",
        dimmed && "opacity-50"
      )}
    >
      <button
        type="button"
        aria-current={selected ? "true" : undefined}
        onClick={() => {
          card.dismiss(s.index);
          onSelect(s.index);
        }}
        onPointerEnter={(e) => e.pointerType === "mouse" && card.enter(s.index)}
        onPointerLeave={() => card.leave(s.index)}
        onFocus={(e) => e.currentTarget.matches(":focus-visible") && card.enter(s.index, true)}
        onBlur={() => card.leave(s.index)}
        onKeyDown={(e) => {
          if (e.key === "Escape" && open) {
            e.preventDefault();
            card.dismiss(s.index);
          }
        }}
        className={cn(
          ROW,
          "group cursor-pointer transition-colors duration-150 ease-out motion-reduce:transition-none [-webkit-tap-highlight-color:transparent]",
          "hover:bg-foreground/[0.04] active:bg-foreground/[0.07]",
          "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
          "aria-[current=true]:bg-foreground/[0.04]",
          "before:absolute before:inset-y-2 before:left-0 before:w-0.5 before:rounded-full before:bg-transparent before:content-[''] aria-[current=true]:before:bg-foreground-muted"
        )}
      >
        <StepLabel m={m} s={s} />
        {track}
        {duration}
        <span className="sr-only">. Show this step in the timeline.</span>
      </button>
      {/* A non-modal popover: above the page, flips when there is no room, and its text can be selected */}
      <Popover
        isNonModal
        isOpen={open}
        onOpenChange={(isOpen) => !isOpen && card.dismiss(s.index)}
        triggerRef={anchor}
        placement="top"
        offset={10}
        containerPadding={60}
        className="rounded-xl border-border bg-content shadow-lg backdrop-blur-none data-exiting:animate-none"
      >
        <div
          role="presentation"
          onPointerEnter={() => card.keep(s.index)}
          onPointerLeave={() => card.leave(s.index)}
        >
          <StepTip
            m={m}
            s={s}
            startDay={startDay}
            onOpen={() => {
              card.dismiss(s.index);
              onSelect(s.index);
            }}
          />
        </div>
      </Popover>
    </li>
  );
}

/* ---------- hover card state ---------- */

/** The first card waits; once one has opened, moving to the next row shows its card at once. */
const OPEN_DELAY = 300;
const WARM_FOR = 500;
/** Time to move the pointer from the bar into the card before it closes. */
const CLOSE_GRACE = 150;
/**
 * While a card is open, another row's card waits this long: on the way up to the open card, the
 * pointer may cross other rows, and reaching the card cancels the switch.
 */
const SWITCH_DELAY = 120;

interface HoverCard {
  /** The row whose card is open. */
  tip: number | null;
  /** The row the pointer or keyboard focus is on, or whose card is open. */
  active: number | null;
  enter: (index: number, now?: boolean) => void;
  leave: (index: number) => void;
  /** The pointer is inside the card: keep it open. */
  keep: (index: number) => void;
  /** Escape, a click or a click outside: hide the card until the pointer or focus leaves the row. */
  dismiss: (index: number) => void;
}

function useHoverCard(): HoverCard {
  const [tip, setTip] = useState<number | null>(null);
  const [hovered, setHovered] = useState<number | null>(null);
  const openTimer = useRef<number | undefined>(undefined);
  const closeTimer = useRef<number | undefined>(undefined);
  const warmUntil = useRef(0);
  const dismissed = useRef<number | null>(null);
  // The open card's row, read synchronously when the pointer moves on to the next row.
  const openIndex = useRef<number | null>(null);

  useEffect(
    () => () => {
      window.clearTimeout(openTimer.current);
      window.clearTimeout(closeTimer.current);
    },
    []
  );

  const close = () => {
    if (openIndex.current !== null) warmUntil.current = Date.now() + WARM_FOR;
    openIndex.current = null;
    setTip(null);
  };
  const enter = (index: number, now = false) => {
    setHovered(index);
    if (dismissed.current === index) return;
    window.clearTimeout(openTimer.current);
    window.clearTimeout(closeTimer.current);
    if (openIndex.current === index) return;
    const show = () => {
      openIndex.current = index;
      setTip(index);
    };
    if (now) show();
    else if (openIndex.current !== null) openTimer.current = window.setTimeout(show, SWITCH_DELAY);
    else if (Date.now() < warmUntil.current) show();
    else openTimer.current = window.setTimeout(show, OPEN_DELAY);
  };
  const leave = (index: number) => {
    window.clearTimeout(openTimer.current);
    if (dismissed.current === index) dismissed.current = null;
    setHovered((h) => (h === index ? null : h));
    if (openIndex.current === index) {
      window.clearTimeout(closeTimer.current);
      closeTimer.current = window.setTimeout(close, CLOSE_GRACE);
    }
  };
  const keep = (index: number) => {
    if (openIndex.current !== index) return;
    window.clearTimeout(closeTimer.current);
    window.clearTimeout(openTimer.current);
    setHovered(index);
  };
  const dismiss = (index: number) => {
    window.clearTimeout(openTimer.current);
    window.clearTimeout(closeTimer.current);
    dismissed.current = index;
    openIndex.current = null;
    setTip(null);
  };
  return { tip, active: hovered ?? tip, enter, leave, keep, dismiss };
}

/* ---------- axis ---------- */

interface Mark {
  at: number;
  iso?: string;
}

/** The width of the chart column, so axis labels that would touch can be dropped. */
function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    setWidth(el.clientWidth);
    return () => ro.disconnect();
  }, []);
  return [ref, width] as const;
}

/**
 * The axis labels that fit, one per divider. Labels show seconds when two dividers fall in the
 * same minute, and a label shows its date when it is on another day than the label before it.
 */
function axisLabels(marks: Mark[], width: number, startDay: string) {
  const minute = (iso: string) => `${dayOf(iso)} ${hm(iso)}`;
  // One format for the whole axis: seconds everywhere as soon as two dividers share a minute.
  const minutes = marks.flatMap((o) => (o.iso ? [minute(o.iso)] : []));
  const withSeconds = new Set(minutes).size < minutes.length;
  const clockOf = (iso: string) =>
    withSeconds ? new Date(iso).toLocaleTimeString("en-GB") : hm(iso);
  const labelled: { mark: Mark; text: string; align: "start" | "center" | "end" }[] = [];
  let prevDay = startDay;
  for (const mark of marks) {
    const day = mark.iso ? dayOf(mark.iso) : prevDay;
    const t = mark.iso ? clockOf(mark.iso) : "now";
    const text = mark.iso && day !== prevDay ? `${day} ${t}` : t;
    prevDay = day;
    const align = mark.at === 0 ? "start" : mark.at >= 99.5 ? "end" : "center";
    labelled.push({ mark, text, align });
  }
  // Keep a label only when it does not touch the one before it; the end label always wins.
  const px = (l: (typeof labelled)[number]) => {
    const x = (l.mark.at / 100) * width;
    const w = l.text.length * 6.5 + 10;
    return l.align === "start"
      ? [x, x + w]
      : l.align === "end"
        ? [x - w, x]
        : [x - w / 2, x + w / 2];
  };
  const kept: typeof labelled = [];
  for (const l of labelled) {
    const [a] = px(l);
    while (kept.length && px(kept[kept.length - 1])[1] > a) {
      if (l.align === "end") kept.pop();
      else break;
    }
    const prev = kept[kept.length - 1];
    // The same time twice says nothing; keep the later one only when it ends the axis.
    if (prev && prev.text === l.text) {
      if (l.align === "end") kept[kept.length - 1] = l;
      continue;
    }
    if (!prev || px(prev)[1] <= a) kept.push(l);
  }
  // Dates come last, from the labels that stay: a label shows its day when the one before it is on another day.
  let day = startDay;
  for (const l of kept) {
    if (!l.mark.iso) continue;
    const d = dayOf(l.mark.iso);
    const t = clockOf(l.mark.iso);
    l.text = d === day ? t : `${d} ${t}`;
    day = d;
  }
  return kept;
}

/* ---------- the strip ---------- */

/**
 * The steps of a run on one time axis: a row per step, its bar placed where it ran, its duration
 * on the right. Hovering a row shows what happened; clicking it opens the step in the timeline.
 */
export function RunTimeStrip({
  run,
  steps,
  planExpired,
  onSelect,
  selected,
}: {
  run: Run;
  steps: RunStep[];
  planExpired: boolean;
  onSelect: (index: number) => void;
  /** The step last opened from the strip. */
  selected?: number;
}) {
  const nowMs = useNow(1000);
  const now = nowMs / 1000;
  const m: RunView = { run, planExpired };
  const zs = zones(steps, now);
  const startDay = dayOf(run.started_at);
  const [chartRef, chartWidth] = useWidth<HTMLSpanElement>();
  const card = useHoverCard();

  // Marks where each placed step starts; the end of the last one, or now while something runs or waits.
  const open = steps.some(
    (s) => (s.exec && !s.exec.terminal_at) || (s.kind === "review" && s.state === "waiting")
  );
  const marks: Mark[] = [];
  steps.forEach((s, i) => {
    const z = zs[i];
    if (!z || marks.some((mk) => Math.abs(mk.at - z.left) < 0.5)) return;
    const wait = reviewWait(s);
    const first = attemptsOf(s)[0];
    const iso = wait ? wait.from : (first?.o.submitted_at ?? first?.o.claimed_at ?? undefined);
    marks.push({ at: z.left, iso: iso ?? undefined });
  });
  const lastEvent = steps
    .flatMap((s) => [s.exec?.terminal_at, s.kind === "review" ? s.end : undefined])
    .filter((t): t is string => Boolean(t))
    .sort()
    .at(-1);
  const endIso = run.finished_at ?? (open ? undefined : lastEvent);
  const placed = zs.filter(Boolean) as Zone[];
  const endAt = placed.length ? Math.max(...placed.map((z) => z.left + z.width)) : 100;
  if (endAt > 90) marks.push({ at: 100, iso: endIso });
  const labels = axisLabels(marks, chartWidth, startDay);

  return (
    <section aria-labelledby="run-summary" className="flex flex-col gap-3">
      <SectionHeading id="run-summary" title="Run summary" />
      <Card className="flex flex-col gap-3 p-4">
        <p className="text-foreground-muted text-xs">
          Started <time dateTime={run.started_at}>{clock(run.started_at)}</time>
          {run.operation === "sync" && <> · Sync without review</>}
        </p>
        <div className="-mx-3">
          <div className="relative">
            {/* Faint guides at each labelled time, behind the rows */}
            <div
              aria-hidden="true"
              className="pointer-events-none absolute inset-y-0 right-[calc(5.5rem+2rem)] left-3 sm:left-[calc(10rem+2rem)]"
            >
              {labels.map((l) => (
                <span
                  key={l.mark.at}
                  className="absolute inset-y-0 border-border border-l"
                  style={{ left: `${l.mark.at}%` }}
                />
              ))}
            </div>
            <ol className="relative flex flex-col gap-0.5">
              {steps.map((s, i) => (
                <GanttRow
                  key={s.id}
                  m={m}
                  s={s}
                  z={zs[i]}
                  card={card}
                  dimmed={card.active !== null && card.active !== s.index}
                  noteAt={Math.max(0, ...zs.slice(0, i).map((p) => (p ? p.left + p.width : 0)))}
                  selected={selected === s.index}
                  onSelect={onSelect}
                  now={now}
                  startDay={startDay}
                />
              ))}
            </ol>
          </div>
          <div aria-hidden="true" className={cn("grid items-start gap-x-5 px-3 pt-1", COLS)}>
            <span className="max-sm:hidden" />
            <span
              ref={chartRef}
              className="relative h-6 border-border-strong border-t text-subtle text-xs tabular-nums"
            >
              {labels.map((l) => (
                <span
                  key={`${l.mark.at}-${l.text}`}
                  className={cn(
                    "absolute top-0 flex flex-col whitespace-nowrap",
                    l.align === "start" && "items-start",
                    l.align === "center" && "-translate-x-1/2 items-center",
                    l.align === "end" && "-translate-x-full items-end"
                  )}
                  style={{ left: `${l.mark.at}%` }}
                >
                  <span className="h-1 w-px bg-border-strong" />
                  <span className="pt-0.5">{l.text}</span>
                </span>
              ))}
            </span>
            <span className="flex flex-col items-end border-border-strong border-t pt-1 text-right tabular-nums">
              <span className="font-semibold text-[13px]">
                {formatSpan(run.started_at, endIso ?? nowMs)}
              </span>
              <span className="text-[11px] text-subtle">
                {!endIso ? "so far" : run.finished_at ? "total" : "run not finished"}
              </span>
            </span>
          </div>
        </div>
      </Card>
    </section>
  );
}
