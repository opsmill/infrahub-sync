import type { Orchestration, Run, RunResource } from "#/api/types";
import { formatDuration } from "#/lib/time";

import {
  countsSentence,
  executableCount,
  failureDetail,
  fmt,
  type RunState,
  runCounts,
  runSteps,
  type StepState,
} from "./run-state";

/** The saved plan's file among a run's artifacts. */
export const PLAN_ARTIFACT_ID = "plan-review";

export type StepKind = "plan" | "review" | "apply";

export type Artifact = Run["artifact_refs"][number];

/**
 * One step of a run as a person reads it: Plan, Review, Apply. A sync without review has no Review.
 * Review has no execution of its own: it is the time between the saved plan and the apply request.
 */
export interface RunStep {
  kind: StepKind;
  name: string;
  state: StepState;
  index: number;
  /** The anchor of the step in the timeline. */
  id: string;
  /** The execution whose times the step shows. */
  exec?: Orchestration;
  /** Every execution of the step: its attempts, or the checks of the saved plan for Review. */
  execs: Orchestration[];
  start?: string;
  end?: string;
  /** One sentence on what the step found, wrote or why it did not run. */
  outcome?: string;
  /** Plan and Apply of a sync without review: two halves of one execution. */
  shared?: boolean;
  /** Files the Sync service saved while the step ran. */
  files: Artifact[];
}

export interface StepContext {
  /** The purpose of the execution that has not finished. */
  inFlight?: string;
  /** `GET /runs/{run_id}/plan` answered 410. */
  planExpired?: boolean;
  /** When a newer plan of the configuration was made: this plan no longer waits for review. */
  replacedAt?: string;
}

export interface Wait {
  from: string;
  /** Absent while the run still waits. */
  to?: string;
}

type Draft = Omit<RunStep, "index" | "id" | "files">;

const time = (iso: string) => new Date(iso).getTime();

export const ranFor = (o: Orchestration) =>
  o.claimed_at && o.terminal_at ? formatDuration(o.claimed_at, o.terminal_at) : undefined;

export const queuedFor = (o: Orchestration) =>
  o.submitted_at && o.claimed_at ? formatDuration(o.submitted_at, o.claimed_at) : undefined;

/** An execution's result in words, with the state that colours it. */
export function executionResult(o: Orchestration): { state: RunState; label: string } {
  if (o.terminal_at == null) {
    if (o.cancellation_requested_at) return { state: "running", label: "Cancelling" };
    if (o.claimed_at) return { state: "running", label: "Running" };
    return { state: "running", label: "Waiting for a worker" };
  }
  switch (o.terminal_outcome) {
    case "succeeded":
      return { state: "insync", label: "Succeeded" };
    case "failed":
      return { state: "failed", label: "Failed" };
    case "ambiguous":
      return { state: "failed", label: "Interrupted: result unknown" };
    case "cancelled":
      return { state: "idle", label: "Cancelled" };
    case "abandoned":
      return { state: "idle", label: "Never picked up" };
    default:
      return { state: "idle", label: "Ended, result not recorded" };
  }
}

const withVerb = (verb: string, sentence: string) =>
  sentence.startsWith("Nothing") ? sentence : `${verb} ${sentence}`;

/** "Checked the plan, then wrote 15 changes: 3 created, 12 updated. 2 deletes not applied: …" */
function wroteSentence(c: { create: number; update: number; delete: number }): string {
  const n = c.create + c.update;
  const parts = [c.create && `${fmt(c.create)} created`, c.update && `${fmt(c.update)} updated`]
    .filter(Boolean)
    .join(", ");
  const wrote =
    n === 0
      ? "Nothing to write."
      : `Checked the plan, then wrote ${fmt(n)} ${n === 1 ? "change" : "changes"}: ${parts}.`;
  const deletes = c.delete
    ? ` ${fmt(c.delete)} ${c.delete === 1 ? "delete" : "deletes"} not applied: apply never deletes.`
    : "";
  return wrote + deletes;
}

const INTERRUPTED =
  "The worker stopped without reporting back. Some changes may have been written.";

const succeeded = (o?: Orchestration) => o?.terminal_outcome === "succeeded";

/**
 * A sync without review plans and applies in one execution. The saved plan's file is written when
 * the plan ends, so its time splits that execution into Plan, then Apply. Without the file (the
 * sync failed or is still planning), the whole execution belongs to Plan.
 */
function syncSteps(run: Run, orchestration: Orchestration[]): Draft[] {
  const counts = runCounts(run);
  const f = failureDetail(run);
  const execs = orchestration.filter((o) => o.purpose === "sync");
  const exec = execs.at(-1);
  const savedAt = run.artifact_refs.find((a) => a.artifact_id === PLAN_ARTIFACT_ID)?.created_at;
  const planSaved =
    exec?.claimed_at && savedAt && time(savedAt) >= time(exec.claimed_at) ? savedAt : undefined;
  const ended = Boolean(exec?.terminal_at);
  const failed =
    Boolean(f) || exec?.terminal_outcome === "failed" || exec?.terminal_outcome === "ambiguous";
  const stopped = run.phase === "abandoned" || run.phase === "cancelled";
  const ambiguous = exec?.terminal_outcome === "ambiguous" || run.reconciliation_required;
  const noChange = run.outcome === "no-change";

  // The two halves of the one execution, as executions of their own so every view reads them alike.
  const planExec: Orchestration | undefined =
    exec && planSaved ? { ...exec, terminal_at: planSaved, terminal_outcome: "succeeded" } : exec;
  const applyExec: Orchestration | undefined =
    exec && planSaved ? { ...exec, submitted_at: null, claimed_at: planSaved } : undefined;

  let planState: StepState;
  let applyState: StepState;
  if (stopped) {
    planState = planSaved ? "done" : "skipped";
    applyState = planSaved ? "skipped" : "idle";
  } else if (planSaved) {
    planState = "done";
    applyState = !ended ? "active" : failed ? "failed" : "done";
  } else {
    planState = !exec || !ended ? "active" : failed ? "failed" : "done";
    applyState = planState === "done" ? "done" : "idle";
  }

  let planOutcome: string | undefined;
  if (planState === "done" && counts) {
    planOutcome =
      counts.total === 0
        ? "No differences found"
        : withVerb("Found", countsSentence(counts, "plan"));
  } else if (planState === "failed") planOutcome = f?.sentence;
  else if (planState === "active") planOutcome = "Reading and comparing both systems";

  let applyOutcome: string | undefined;
  if (applyState === "done") {
    applyOutcome =
      !counts || noChange || counts.total === 0 ? "Nothing to write" : wroteSentence(counts);
  } else if (applyState === "failed") {
    applyOutcome = ambiguous ? INTERRUPTED : f?.sentence;
  } else if (applyState === "active") {
    applyOutcome = "Checking the plan, then writing to the branch";
  }

  return [
    {
      kind: "plan",
      name: "Plan",
      state: planState,
      exec: planExec,
      execs,
      start: planExec?.submitted_at ?? undefined,
      end: planExec?.terminal_at ?? undefined,
      outcome: planOutcome,
      shared: Boolean(planSaved),
    },
    {
      kind: "apply",
      name: "Apply",
      state: applyState,
      exec: applyExec,
      execs: [],
      start: applyExec?.claimed_at ?? undefined,
      end: applyExec?.terminal_at ?? undefined,
      outcome: applyOutcome,
      shared: Boolean(planSaved),
    },
  ];
}

function reviewedSteps(run: Run, orchestration: Orchestration[], ctx: StepContext): Draft[] {
  const counts = runCounts(run);
  const f = failureDetail(run);
  const stopped = run.phase === "abandoned" || run.phase === "cancelled";
  const states = runSteps(run, { inFlight: ctx.inFlight });
  const of = (purpose: string) => orchestration.filter((o) => o.purpose === purpose);
  const planExecs = of("plan");
  const planExec = planExecs.at(-1);
  const checks = of("verify");
  const applyExecs = of("apply");
  const applyExec = applyExecs.at(-1);

  const planState: StepState = stopped ? (succeeded(planExec) ? "done" : "skipped") : states[0];
  let planOutcome: string | undefined;
  if (planState === "done" && counts) {
    planOutcome =
      counts.total === 0 && counts.deletesComputed !== false
        ? "No differences found"
        : withVerb("Found", countsSentence(counts, "plan"));
  } else if (planState === "failed") planOutcome = f?.sentence;
  else if (planState === "active") planOutcome = "Reading and comparing both systems";

  // Review: the time between the saved plan and the apply request, while the changes are read.
  const executable = executableCount(run) ?? 0;
  const noChange = run.phase === "planned" && run.outcome === "no-change";
  const waiting =
    run.phase === "planned" &&
    run.outcome === "planned" &&
    (!ctx.inFlight || ctx.inFlight === "verify");
  const checkFailed = Boolean(run.results?.verify_failure) && !run.results?.verification;
  const applyStarted = Boolean(applyExec) || run.phase === "applied" || states[2] === "failed";
  let reviewState: StepState = "idle";
  let reviewOutcome: string | undefined;
  if (planState !== "done") {
    reviewState = planState === "failed" || stopped ? "skipped" : "idle";
  } else if (noChange) {
    reviewState = "skipped";
    reviewOutcome = "Not needed: no differences";
  } else if (applyStarted) {
    reviewState = "done";
    reviewOutcome = "Apply requested";
  } else if (checkFailed) {
    reviewState = "failed";
    reviewOutcome = "The plan check failed, so this plan can't be applied. Plan again.";
  } else if (ctx.planExpired) {
    reviewState = "skipped";
    reviewOutcome = "The plan expired before it was applied. Plan again.";
  } else if (waiting && ctx.replacedAt) {
    reviewState = "skipped";
    reviewOutcome = "Replaced by a newer plan";
  } else if (waiting && executable === 0) {
    reviewState = "skipped";
    reviewOutcome = "Nothing to apply: the plan has only deletes, and apply never deletes.";
  } else if (waiting) {
    reviewState = "waiting";
    reviewOutcome = `Waiting for review: ${fmt(executable)} ${executable === 1 ? "change" : "changes"} to apply`;
  } else if (stopped) {
    reviewState = "skipped";
  }
  const reviewStart =
    reviewState === "idle" || planState !== "done"
      ? undefined
      : (planExec?.terminal_at ?? undefined);
  // Review ends when the apply is requested, when the saved plan fails its check, or when a
  // newer plan replaces this one.
  const reviewEnd =
    applyExec?.submitted_at ??
    (checkFailed ? checks.at(-1)?.terminal_at : undefined) ??
    (waiting ? ctx.replacedAt : undefined) ??
    undefined;

  let applyState: StepState = stopped ? (applyExec ? "skipped" : "idle") : states[2];
  if (applyState === "waiting") applyState = "idle";
  if (!applyExec && (checkFailed || ctx.planExpired)) applyState = "skipped";
  let applyOutcome: string | undefined;
  if (applyState === "done" && counts) {
    applyOutcome = counts.total === 0 ? "Nothing to write" : wroteSentence(counts);
  } else if (applyState === "failed") {
    applyOutcome =
      applyExec?.terminal_outcome === "ambiguous" || run.reconciliation_required
        ? INTERRUPTED
        : f?.sentence;
  } else if (applyState === "active") {
    applyOutcome = "Checking the plan, then writing to the branch";
  } else if (applyState === "skipped" && (checkFailed || ctx.planExpired)) {
    applyOutcome = "Can't run: plan again";
  }

  return [
    {
      kind: "plan",
      name: "Plan",
      state: planState,
      exec: planExec,
      execs: planExecs,
      start: planExec?.submitted_at ?? undefined,
      end: planExec?.terminal_at ?? undefined,
      outcome: planOutcome,
    },
    {
      kind: "review",
      name: "Review",
      state: reviewState,
      execs: checks,
      start: reviewStart,
      end: reviewStart ? reviewEnd : undefined,
      outcome: reviewOutcome,
    },
    {
      kind: "apply",
      name: "Apply",
      state: applyState,
      exec: applyExec,
      execs: applyExecs,
      start: applyExec?.submitted_at ?? undefined,
      end: applyExec?.terminal_at ?? undefined,
      outcome: applyOutcome,
    },
  ];
}

/** The steps of a run, each with the files the Sync service saved while it ran. */
export function runStepList(resource: RunResource, ctx: StepContext = {}): RunStep[] {
  const { run, orchestration } = resource;
  const drafts =
    run.operation === "sync"
      ? syncSteps(run, orchestration)
      : reviewedSteps(run, orchestration, ctx);
  const steps: RunStep[] = drafts.map((d, index) => ({
    ...d,
    index,
    id: `step-${index}`,
    files: [],
  }));
  // A file belongs to the step whose execution was running when the Sync service saved it.
  for (const a of run.artifact_refs) {
    const t = time(a.created_at);
    const owner = steps.find((s) =>
      s.execs.some(
        (o) =>
          o.submitted_at &&
          t >= time(o.submitted_at) &&
          t <= (o.terminal_at ? time(o.terminal_at) + 10_000 : Number.POSITIVE_INFINITY)
      )
    );
    (owner ?? steps[0]).files.push(a);
  }
  return steps;
}

/** Whether the step has an entry in the timeline: it ran, waits, or has something to say. */
export const inTimeline = (s: RunStep) =>
  s.state !== "idle" || Boolean(s.exec) || Boolean(s.outcome);

/** The time Review lasted, or has lasted so far while it still waits. */
export function reviewWait(s: RunStep): Wait | undefined {
  if (s.kind !== "review" || !s.start) return undefined;
  if (s.end) return { from: s.start, to: s.end };
  return s.state === "waiting" ? { from: s.start } : undefined;
}

/** The notes the Sync service recorded when it checked the saved plan. */
export function verificationNotes(run: Run): string[] {
  const v = run.results?.verification as { verification_notes?: unknown } | undefined;
  return Array.isArray(v?.verification_notes)
    ? v.verification_notes.filter((n): n is string => typeof n === "string")
    : [];
}
