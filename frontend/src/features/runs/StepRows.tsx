import type { ReactElement, ReactNode } from "react";
import { cn } from "tailwind-variants";

import type { Orchestration, Run } from "#/api/types";
import { StateIcon, WarningIcon } from "#/components/icons";
import {
  executableCount,
  failureDetail,
  fmt,
  type RunState,
  runCounts,
  writtenCount,
} from "#/domain/run-state";
import {
  executionResult,
  lastPassedCheck,
  planCheckMatches,
  queuedFor,
  type RunStep,
  ranFor,
} from "#/domain/run-steps";
import { formatSpan, useNow } from "#/lib/time";

import { clockSeconds } from "./step-time";

/* ---------- the grid ---------- */

/** The width of the rows of every event of the timeline, so their rules have one length. */
export const STEP_WIDTH = "w-full max-w-[47rem]";

/**
 * Label, state, result, duration, clock times. The columns are the same in every step, so they
 * line up down the timeline; the label column is as wide as the Started step's.
 */
export const STEP_GRID = cn(
  STEP_WIDTH,
  "grid grid-cols-[5.5rem_7.5rem_minmax(0,1fr)_5rem_9rem] gap-x-4 text-[13px] text-foreground-muted max-lg:grid-cols-[5.5rem_minmax(0,1fr)]"
);

const CELL = "min-w-0 leading-6 max-lg:col-start-2 empty:max-lg:hidden";

export function Row({
  label,
  state,
  result,
  duration,
  clock,
}: {
  label: string;
  state: ReactNode;
  result?: ReactNode;
  duration?: ReactNode;
  clock?: ReactNode;
}) {
  return (
    <div className="col-span-full grid grid-cols-subgrid items-baseline gap-y-0.5 border-b py-1.5 last:border-b-0">
      <dt className="text-subtle text-xs leading-6">{label}</dt>
      <dd className="contents">
        <span className={CELL}>{state}</span>
        <span className={CELL}>{result}</span>
        <span className={cn(CELL, "tabular-nums")}>{duration}</span>
        <span className={cn(CELL, "text-subtle tabular-nums lg:whitespace-nowrap")}>{clock}</span>
      </dd>
    </div>
  );
}

/** An icon and a word in one colour, as the step's own state on its title line. */
export function StateWord({
  icon,
  tone,
  children,
}: {
  icon: RunState | "warning";
  tone: string;
  children: ReactNode;
}) {
  return (
    <span className={cn("inline-flex items-center gap-1 whitespace-nowrap", tone)}>
      {icon === "warning" ? (
        <WarningIcon className="size-3.5 shrink-0" />
      ) : (
        <StateIcon state={icon} className="size-3.5 shrink-0" />
      )}
      {children}
    </span>
  );
}

export const DONE = { icon: "insync", tone: "text-success" } as const;
const ACTIVE = { icon: "running", tone: "text-accent" } as const;
const IDLE = { icon: "idle", tone: "text-subtle" } as const;

export const range = (from: string, to: string) => `${clockSeconds(from)} to ${clockSeconds(to)}`;

/** Time since `from`, counted every second. */
export function Since({ from }: { from: string }) {
  const now = useNow(1000);
  return formatSpan(from, Math.max(now, Date.parse(from)));
}

const Num = ({ n }: { n: number }) => (
  <span className="font-medium text-foreground tabular-nums">{fmt(n)}</span>
);

const RESULT_TONE: Record<RunState, string> = {
  insync: "text-success",
  failed: "text-danger",
  running: "text-accent",
  review: "text-warning",
  idle: "text-subtle",
};

/** An execution's result as the state of a row, in the short words a row has room for. */
function Result({ o }: { o: Orchestration }) {
  const e = executionResult(o);
  if (o.terminal_outcome === "ambiguous") {
    return (
      <StateWord icon="warning" tone="text-warning">
        Interrupted
      </StateWord>
    );
  }
  return (
    <StateWord icon={e.state} tone={RESULT_TONE[e.state]}>
      {o.terminal_outcome === "abandoned" ? "Not picked up" : e.label}
    </StateWord>
  );
}

const stalledText = (o: Orchestration) =>
  o.stalled_at ? `Reported as stalled at ${clockSeconds(o.stalled_at)}` : undefined;

/* ---------- rows any step can have ---------- */

/** The wait for a worker: picked up, never picked up, or still waiting. */
export function queueRow(o: Orchestration): ReactElement | null {
  if (!o.submitted_at) return null;
  if (o.claimed_at) {
    return (
      <Row
        key="queue"
        label="Queue"
        state={<StateWord {...DONE}>Picked up</StateWord>}
        result="By a worker"
        duration={queuedFor(o)}
        clock={range(o.submitted_at, o.claimed_at)}
      />
    );
  }
  if (o.terminal_at) {
    return (
      <Row
        key="queue"
        label="Queue"
        state={<Result o={o} />}
        result={stalledText(o)}
        duration={formatSpan(o.submitted_at, o.terminal_at)}
        clock={range(o.submitted_at, o.terminal_at)}
      />
    );
  }
  return (
    <Row
      key="queue"
      label="Queue"
      state={<StateWord {...ACTIVE}>Waiting</StateWord>}
      result={stalledText(o) ?? "For a worker"}
      duration={<Since from={o.submitted_at} />}
      clock={`since ${clockSeconds(o.submitted_at)}`}
    />
  );
}

/** One row per attempt of the step before the one its other rows describe. */
export function attemptRows(step: RunStep): ReactElement[] {
  if (step.kind === "review") return [];
  return step.execs.slice(0, -1).map((o) => {
    const from = o.submitted_at;
    return (
      <Row
        key={o.flow_run_id}
        label={`Attempt ${o.attempt}`}
        state={<Result o={o} />}
        result={o.claimed_at ? undefined : stalledText(o)}
        duration={from && o.terminal_at ? formatSpan(from, o.terminal_at) : undefined}
        clock={from && o.terminal_at ? range(from, o.terminal_at) : undefined}
      />
    );
  });
}

/** The request to cancel an execution, and whether a worker acknowledged it. */
export function cancelRow(o: Orchestration): ReactElement | null {
  const requested = o.cancellation_requested_at;
  if (!requested) return null;
  const acknowledged = o.cancellation_acknowledged_at;
  const deadline = o.cancellation_recovery_deadline_at;
  return (
    <Row
      key="cancel"
      label="Cancel"
      state={<StateWord {...IDLE}>{acknowledged ? "Acknowledged" : "Requested"}</StateWord>}
      result={
        acknowledged
          ? "By a worker"
          : deadline && !o.terminal_at
            ? `The Sync service ends it at ${clockSeconds(deadline)}`
            : "Not acknowledged by a worker"
      }
      duration={acknowledged ? formatSpan(requested, acknowledged) : undefined}
      clock={acknowledged ? range(requested, acknowledged) : clockSeconds(requested)}
    />
  );
}

/* ---------- review: one row per plan check ---------- */

function checkRows(run: Run, step: RunStep): ReactElement[] {
  const passed = lastPassedCheck(step.execs);
  const matches = planCheckMatches(run);
  return step.execs.map((o, i) => {
    const from = o.claimed_at ?? o.submitted_at;
    return (
      <Row
        key={o.flow_run_id}
        label={step.execs.length > 1 ? `Check ${i + 1}` : "Plan check"}
        state={
          o.terminal_outcome === "succeeded" ? (
            <StateWord {...DONE}>Passed</StateWord>
          ) : o.terminal_at ? (
            <Result o={o} />
          ) : o.claimed_at ? (
            <StateWord {...ACTIVE}>Checking</StateWord>
          ) : (
            <StateWord {...ACTIVE}>Waiting</StateWord>
          )
        }
        result={
          o === passed && matches !== undefined ? (
            matches ? (
              "Checksum matches the saved plan"
            ) : (
              <span className="text-danger">Checksum does not match the saved plan</span>
            )
          ) : !o.terminal_at && !o.claimed_at ? (
            "For a worker"
          ) : undefined
        }
        duration={o.terminal_at ? ranFor(o) : from ? <Since from={from} /> : undefined}
        clock={
          from && o.terminal_at
            ? range(from, o.terminal_at)
            : from
              ? `since ${clockSeconds(from)}`
              : undefined
        }
      />
    );
  });
}

/* ---------- apply ---------- */

function written(run: Run): ReactNode {
  const c = runCounts(run);
  if (!c || writtenCount(c) === 0) return "Nothing to write";
  const n = writtenCount(c);
  const parts = [c.create && `${fmt(c.create)} created`, c.update && `${fmt(c.update)} updated`]
    .filter(Boolean)
    .join(", ");
  return (
    <>
      <Num n={n} /> {n === 1 ? "change" : "changes"}: {parts}
    </>
  );
}

function writtenBeforeFailure(run: Run): ReactNode {
  const applied = failureDetail(run)?.appliedCount;
  const executable = executableCount(run) ?? 0;
  if (applied === undefined) return undefined;
  return (
    <>
      <Num n={applied} /> of {fmt(executable)} {executable === 1 ? "change" : "changes"} written
    </>
  );
}

/**
 * The apply in one row, from the time a worker picked it up to its end. The Sync API records one
 * start and one end for an apply, so the row covers its plan check, its schema check and its write.
 */
function writeRow(run: Run, o: Orchestration): ReactElement | null {
  if (!o.claimed_at) return null;
  if (!o.terminal_at) {
    const executable = executableCount(run) ?? 0;
    return (
      <Row
        key="write"
        label="Write"
        state={<StateWord {...ACTIVE}>Writing</StateWord>}
        result={
          <>
            <Num n={executable} /> {executable === 1 ? "change" : "changes"} to write
          </>
        }
        duration={<Since from={o.claimed_at} />}
        clock={`since ${clockSeconds(o.claimed_at)}`}
      />
    );
  }
  const done = o.terminal_outcome === "succeeded";
  return (
    <Row
      key="write"
      label="Write"
      state={done ? <StateWord {...DONE}>Written</StateWord> : <Result o={o} />}
      result={done ? written(run) : writtenBeforeFailure(run)}
      duration={ranFor(o)}
      clock={range(o.claimed_at, o.terminal_at)}
    />
  );
}

/** A plan whose reads the Sync API did not record: its queue, then the whole plan in one row. */
function planRow(o: Orchestration): ReactElement | null {
  if (!o.claimed_at) return null;
  return (
    <Row
      key="plan"
      label="Plan"
      state={o.terminal_at ? <Result o={o} /> : <StateWord {...ACTIVE}>Running</StateWord>}
      duration={o.terminal_at ? ranFor(o) : <Since from={o.claimed_at} />}
      clock={
        o.terminal_at ? range(o.claimed_at, o.terminal_at) : `since ${clockSeconds(o.claimed_at)}`
      }
    />
  );
}

/**
 * What a step's executions did, one row each with its state, duration and clock times: the plan
 * checks of Review, the queue and the write of Apply. The Plan step's rows are `PlanReads`, unless
 * the Sync API recorded no read.
 */
export function StepRows({
  run,
  step,
  className,
}: {
  run: Run;
  step: RunStep;
  className?: string;
}) {
  const o = step.exec;
  let rows: (ReactElement | null)[] = [];
  if (step.kind === "review") rows = checkRows(run, step);
  else if (o) {
    rows = [
      ...attemptRows(step),
      queueRow(o),
      step.kind === "plan" ? planRow(o) : writeRow(run, o),
      cancelRow(o),
    ];
  }
  const said = rows.filter(Boolean);
  if (said.length === 0) return null;
  return <dl className={cn(STEP_GRID, className)}>{said}</dl>;
}
