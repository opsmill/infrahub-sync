import { Card } from "@infrahub/ui";
import { Link } from "@tanstack/react-router";
import { type ReactNode, type RefObject, useEffect, useRef, useState } from "react";
import { cn } from "tailwind-variants";

import type { Plan, Run } from "#/api/types";
import { ArrowRightIcon, ChevronRightIcon, InfoIcon, StateIcon } from "#/components/icons";
import { expandChevron, FOCUS, SectionHeading } from "#/components/page";
import { BranchBadge, CHANGE_TONE, STEP_TONE, STEP_WORD } from "#/components/run-status";
import { planReads } from "#/domain/plan-reads";
import { fmt, type RunState, runCounts, type StepState } from "#/domain/run-state";
import {
  executionResult,
  inTimeline,
  type RunStep,
  ranFor,
  reviewWait,
  verificationNotes,
} from "#/domain/run-steps";
import type { System } from "#/domain/systems";
import { formatAbsolute } from "#/lib/time";

import { PlanReads } from "./PlanReads";
import { PlanFacts } from "./PlanReview";
import type { PlanRequest } from "./run-sections";
import { StepLog } from "./StepLog";
import { STEP_WIDTH, StepRows } from "./StepRows";
import { clock, stepNode, WaitTime } from "./step-time";

/* ---------- reaching a step from the strip ---------- */

export interface StepFocus {
  /** The step that flashes after it was chosen in the strip. */
  flash: number | null;
  /** The step last chosen in the strip. */
  selected?: number;
  headings: RefObject<(HTMLHeadingElement | null)[]>;
  /** Scroll the step into view and move focus to its heading. */
  select: (index: number) => void;
}

const FLASH_MS = 900;

export function useStepFocus(): StepFocus {
  const [flash, setFlash] = useState<number | null>(null);
  const [selected, setSelected] = useState<number | undefined>();
  const headings = useRef<(HTMLHeadingElement | null)[]>([]);

  const select = (index: number) => {
    // Cleared first, so choosing the same step again flashes it again.
    setFlash(null);
    requestAnimationFrame(() => setFlash(index));
    setSelected(index);
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    document
      .getElementById(`step-${index}`)
      ?.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
    headings.current[index]?.focus({ preventScroll: true });
  };

  useEffect(() => {
    if (flash === null) return;
    const id = window.setTimeout(() => setFlash(null), FLASH_MS);
    return () => window.clearTimeout(id);
  }, [flash]);

  return { flash, selected, headings, select };
}

/* ---------- events ---------- */

const ROW =
  "grid grid-cols-[5.5rem_0.75rem_minmax(0,1fr)] gap-x-4 max-sm:grid-cols-[0.75rem_minmax(0,1fr)]";

function Event({
  id,
  node,
  time,
  title,
  headingRef,
  children,
  rows = false,
  last = false,
  dashed = false,
  flash = false,
}: {
  id?: string;
  node: ReactNode;
  time?: string;
  title: ReactNode;
  headingRef?: (el: HTMLHeadingElement | null) => void;
  children?: ReactNode;
  /** The content is rows with their own padding, so it sits closer to the title. */
  rows?: boolean;
  last?: boolean;
  /** The line below the node is dashed: the run waited for review. */
  dashed?: boolean;
  flash?: boolean;
}) {
  return (
    <li id={id} className={ROW}>
      <div className="pt-0.5 text-right text-subtle text-xs tabular-nums max-sm:hidden">
        {time && (
          <time dateTime={time} title={formatAbsolute(time)}>
            {clock(time)}
          </time>
        )}
      </div>
      <div className="flex flex-col items-center">
        <span className="mt-1 flex size-3 items-center justify-center">{node}</span>
        {!last && (
          <span
            aria-hidden="true"
            className={cn(
              "mt-1 flex-1",
              dashed ? "w-0 border-border-strong border-l border-dashed" : "w-px bg-border-strong"
            )}
          />
        )}
      </div>
      <div
        className={cn(
          "-mx-3 -mt-2 flex min-w-0 flex-col rounded-lg px-3 pt-2 transition-colors duration-700 ease-out motion-reduce:transition-none",
          rows ? "gap-1.5" : "gap-3",
          !last && "pb-7",
          flash ? "bg-accent-surface duration-150" : "bg-transparent"
        )}
      >
        <div className="flex flex-col gap-0.5">
          {time && (
            <time dateTime={time} className="text-subtle text-xs tabular-nums sm:hidden">
              {clock(time)}
            </time>
          )}
          <h3 ref={headingRef} tabIndex={-1} className="font-medium text-sm outline-none">
            {title}
          </h3>
        </div>
        {children}
      </div>
    </li>
  );
}

const Dot = ({ className }: { className: string }) => (
  <span aria-hidden="true" className={cn("block rounded-full", className)} />
);

/* ---------- a step's state and time ---------- */

const ICON_STATE: Record<StepState, RunState> = {
  done: "insync",
  failed: "failed",
  waiting: "review",
  active: "running",
  idle: "idle",
  skipped: "idle",
};

/** In the timeline a done step is green: the state is said once, in its colour. */
const stepTone = (s: RunStep) => (s.state === "done" ? "text-success" : STEP_TONE[s.state]);

/** A review that ended with an apply request says so as its state, not in a sentence under it. */
const applyRequested = (s: RunStep) => s.kind === "review" && s.state === "done";

const stateLabel = (s: RunStep) =>
  applyRequested(s)
    ? "Apply requested"
    : s.exec
      ? executionResult(s.exec).label
      : STEP_WORD[s.state];

const PREPOSITION: Record<StepState, string> = {
  done: "in",
  failed: "after",
  active: "for",
  waiting: "for",
  idle: "after",
  skipped: "after",
};

/**
 * "in 44 s", "after 27 s", "for 2 h 16 min" while the step still runs or waits. The time in the
 * queue is in the step's Queue row.
 */
function StepTime({ s }: { s: RunStep }) {
  const wait = reviewWait(s);
  const o = s.exec;
  let time: ReactNode;
  if (wait) {
    time = <WaitTime wait={wait} />;
  } else if (o) {
    const from = o.claimed_at ?? o.submitted_at;
    if (o.terminal_at) time = ranFor(o);
    else if (from) time = <WaitTime wait={{ from }} />;
  }
  if (!time) return null;
  return (
    <span className="font-normal text-[13px] text-subtle tabular-nums leading-5">
      {applyRequested(s) ? "after" : PREPOSITION[s.state]} {time}
    </span>
  );
}

/** The sentence under the step, without the words its state already says. */
function stepSentence(s: RunStep) {
  if (applyRequested(s)) return undefined;
  const text = s.outcome?.replace(/^Waiting for review: /, "");
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : undefined;
}

/* ---------- under a step's rows ---------- */

/** What the Sync service noted when it checked the saved plan. */
function CheckNotes({ notes }: { notes: string[] }) {
  return (
    <ul className="flex flex-col gap-1 text-[13px] text-foreground-muted">
      {notes.map((n) => (
        <li key={n} className="flex items-start gap-1.5">
          <InfoIcon className="mt-[3px] size-3.5 shrink-0 text-subtle" />
          <span className="text-pretty">{n}</span>
        </li>
      ))}
    </ul>
  );
}

/** The plan record (checksum, schema fingerprint, lineage), behind one line of the Plan step. */
function PlanRecord({ run, plan }: { run: Run; plan: Plan }) {
  return (
    <details className="group">
      <summary
        className={cn(
          "relative -ml-1 inline-flex min-h-5 cursor-pointer list-none items-center gap-1.5 rounded-md px-1 before:absolute before:inset-x-0 before:-inset-y-2 text-foreground-muted text-xs transition-colors duration-150 ease-out hover:text-foreground pointer-coarse:min-h-11 motion-reduce:transition-none [&::-webkit-details-marker]:hidden",
          FOCUS
        )}
      >
        <ChevronRightIcon className={cn(expandChevron(), "group-open:rotate-90")} />
        Plan record
      </summary>
      <Card className="mt-2 p-4">
        <PlanFacts plan={plan} runBranch={run.destination_branch ?? null} />
      </Card>
    </details>
  );
}

/* ---------- the changes, at a glance ---------- */

/**
 * The plan's counts in one line, from the run record, with a link that opens the changes. The run
 * page never downloads the plan to show them.
 */
function ChangesGlance({ run, onOpenChanges }: { run: Run; onOpenChanges: () => void }) {
  const c = runCounts(run);
  const counts = { create: c?.create ?? 0, update: c?.update ?? 0, delete: c?.delete ?? 0 };
  const total = c?.total ?? 0;
  if (total === 0) return null;
  const parts: [keyof typeof CHANGE_TONE, number, string][] = [
    ["create", counts.create, counts.create === 1 ? "create" : "creates"],
    ["update", counts.update, counts.update === 1 ? "update" : "updates"],
    ["delete", counts.delete, counts.delete === 1 ? "delete, not applied" : "deletes, not applied"],
  ];
  return (
    <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[13px]">
      {parts
        .filter(([, n]) => n > 0)
        .map(([action, n, word]) => (
          <span key={action} className="inline-flex items-baseline gap-1 text-foreground-muted">
            <span aria-hidden="true" className={cn("font-medium", CHANGE_TONE[action].text)}>
              {CHANGE_TONE[action].sign}
            </span>
            <span className="font-medium text-foreground tabular-nums">{fmt(n)}</span>
            {word}
          </span>
        ))}
      <button
        type="button"
        onClick={onOpenChanges}
        className={cn(
          "inline-flex cursor-pointer items-center gap-1 rounded-sm font-medium text-accent hover:underline",
          FOCUS
        )}
      >
        View the {fmt(total)} {total === 1 ? "change" : "changes"}
        <ArrowRightIcon className="size-3" />
      </button>
    </p>
  );
}

/* ---------- the start ---------- */

const REQUEST: Record<string, string> = {
  plan: "Plan requested, to review before anything is written",
  sync: "Sync requested, without a review step",
};

/** A system's name, and its host unless the name already is the host (a REST API source). */
function SystemName({ system }: { system: System }) {
  return (
    <>
      <span className="font-medium">{system.label}</span>
      {system.host && system.host !== system.label && (
        <span className="font-mono text-subtle text-xs [overflow-wrap:anywhere]">
          {system.host}
        </span>
      )}
    </>
  );
}

const FACT_ROW = "col-span-2 grid grid-cols-subgrid items-start border-b py-1.5 last:border-b-0";
const FACT_LABEL = "text-subtle text-xs leading-6";
const FACT_VALUE = "flex min-h-6 min-w-0 flex-wrap items-center gap-x-2";

/**
 * What the run was started with, one line each: the source, the destination and the branch it
 * writes to, and the configuration version. A system is left out until its version is read. The
 * rows are as wide as the rows of the steps, so every rule of the timeline has one length.
 */
function StartFacts({
  run,
  source,
  destination,
}: {
  run: Run;
  source?: System;
  destination?: System;
}) {
  // The branch the run writes to, even when the run records none, as the Apply dialog resolves it.
  const branch = run.destination_branch ?? (destination ? (destination.branch ?? "main") : null);
  const versioned = run.registry_version != null && run.config_id;
  if (!source && !destination && !branch && !versioned) return null;
  return (
    <dl
      className={cn(STEP_WIDTH, "grid grid-cols-[5.5rem_minmax(0,1fr)] gap-x-4 pt-0.5 text-[13px]")}
    >
      {source && (
        <div className={FACT_ROW}>
          <dt className={FACT_LABEL}>Source</dt>
          <dd className={FACT_VALUE}>
            <SystemName system={source} />
          </dd>
        </div>
      )}
      {(destination || branch) && (
        <div className={FACT_ROW}>
          <dt className={FACT_LABEL}>Destination</dt>
          <dd className={FACT_VALUE}>
            {destination && <SystemName system={destination} />}
            {branch && (
              <>
                {destination && <span className="text-subtle text-xs">on</span>}
                <BranchBadge branch={branch} />
              </>
            )}
          </dd>
        </div>
      )}
      {versioned && (
        <div className={FACT_ROW}>
          <dt className={FACT_LABEL}>Configuration</dt>
          <dd className={FACT_VALUE}>
            <Link
              to="/configs/$configId"
              params={{ configId: versioned }}
              search={{ version: run.registry_version ?? undefined }}
              className={cn("rounded-sm text-accent tabular-nums hover:underline", FOCUS)}
            >
              Version {run.registry_version}
            </Link>
          </dd>
        </div>
      )}
    </dl>
  );
}

/* ---------- timeline ---------- */

/**
 * What happened in the run, in order: when it started, then each step with what its executions
 * did, one row each, and what it found or wrote. The notice on the run's outcome replaces the
 * sentence of the step it explains. The last step ends the timeline: its state says how the run
 * ended.
 * The events sit in a border without a fill: a card's white surface was too heavy for this much text.
 */
export function RunTimeline({
  run,
  steps,
  plan,
  hasPlan,
  source,
  destination,
  notice,
  focus,
  onOpenChanges,
}: {
  run: Run;
  steps: RunStep[];
  /** The saved plan once this session has downloaded it; the timeline never starts a download. */
  plan: PlanRequest;
  hasPlan: boolean;
  source?: System;
  destination?: System;
  /** Why the run failed, ended without running a step, or waits: shown under that step. */
  notice?: ReactNode;
  focus: StepFocus;
  /** Opens the changes sheet. */
  onOpenChanges: () => void;
}) {
  const noticeIndex = steps.findIndex(
    (s) =>
      s.state === "failed" ||
      s.exec?.terminal_outcome === "abandoned" ||
      s.exec?.terminal_outcome === "cancelled" ||
      (s.exec != null &&
        s.exec.terminal_at == null &&
        (s.exec.stalled_at != null || s.exec.cancellation_requested_at != null))
  );
  // A done plan with changes shows their counts and a link that opens them, not a sentence.
  const changeTotal = runCounts(run)?.total ?? 0;
  const glance = (s: RunStep) =>
    s.kind === "plan" && s.state === "done" && hasPlan && changeTotal > 0;

  const items: ReactNode[] = [
    <Event
      key="start"
      rows
      time={run.started_at}
      node={<Dot className="size-2 border-[1.5px] border-subtle-muted bg-content" />}
      title={
        <span className="inline-flex flex-wrap items-baseline gap-x-2">
          Started
          {REQUEST[run.operation] && (
            <span className="font-normal text-subtle text-xs">{REQUEST[run.operation]}</span>
          )}
        </span>
      }
    >
      <StartFacts run={run} source={source} destination={destination} />
    </Event>,
  ];

  const shown = steps.filter(inTimeline);
  for (const s of shown) {
    const wait = reviewWait(s);
    const sentence = stepSentence(s);
    // The notice on the run's outcome takes the place of the step's sentence; its rows follow.
    const stepNotice = s.index === noticeIndex ? notice : null;
    const reads = planReads(s, source, destination);
    // Rows directly under the title sit as close to it as the Started step's rows.
    const rowsClass = stepNotice ? undefined : "-mt-1.5";
    const notes = s.kind === "review" ? verificationNotes(run) : [];
    items.push(
      <Event
        key={s.id}
        id={s.id}
        flash={focus.flash === s.index}
        last={s === shown.at(-1)}
        time={s.end ?? s.exec?.claimed_at ?? (s.kind === "review" ? undefined : s.start)}
        dashed={Boolean(wait)}
        headingRef={(el) => {
          focus.headings.current[s.index] = el;
        }}
        node={<Dot className={stepNode(s)} />}
        title={
          <span className="flex flex-wrap items-center gap-x-2.5 gap-y-0.5">
            {s.name}
            <span className="inline-flex flex-wrap items-center gap-x-1">
              <span
                className={cn(
                  "inline-flex items-center gap-1 whitespace-nowrap font-normal text-[13px] leading-5",
                  stepTone(s)
                )}
              >
                <StateIcon state={ICON_STATE[s.state]} className="size-3.5 shrink-0" />
                <span className="first-letter:uppercase">{stateLabel(s)}</span>
              </span>
              <StepTime s={s} />
            </span>
          </span>
        }
      >
        {stepNotice}
        {reads ? (
          <PlanReads reads={reads} step={s} run={run} className={rowsClass} />
        ) : (
          <StepRows run={run} step={s} className={rowsClass} />
        )}
        <div className="flex flex-col gap-1 empty:hidden">
          {glance(s) ? (
            <ChangesGlance run={run} onOpenChanges={onOpenChanges} />
          ) : (
            sentence &&
            !stepNotice &&
            // The rows of a running plan, and the Write row of an apply, already say it.
            !(reads && s.state === "active") &&
            !(s.kind === "apply" && s.exec) && (
              <p className="text-pretty text-[13px] text-foreground-muted">{sentence}</p>
            )
          )}
          {notes.length > 0 && <CheckNotes notes={notes} />}
          {s.kind === "plan" && plan.data && plan.data.operations.length > 0 && (
            <PlanRecord run={run} plan={plan.data} />
          )}
          <StepLog run={run} step={s} />
        </div>
      </Event>
    );
  }

  return (
    <section aria-labelledby="timeline" className="flex flex-col gap-4">
      <SectionHeading id="timeline" title="Timeline" />
      {notice && noticeIndex < 0 && notice}
      <div className="rounded-2xl border border-border-strong p-4">
        <ol aria-labelledby="timeline">{items}</ol>
      </div>
    </section>
  );
}
