import { Card } from "@infrahub/ui";
import type { ReactNode } from "react";
import { cn } from "tailwind-variants";

import type { Orchestration, RunResource } from "#/api/types";
import { BranchCode, NODE, QUIET_DONE, STEP_TONE, STEP_WORD, When } from "#/components/run-status";
import {
  changesCount,
  countsSentence,
  executableCount,
  fmt,
  runCounts,
  runSteps,
  type StepState,
  stepNames,
} from "#/domain/run-state";
import { formatDuration, useNow } from "#/lib/time";

/** Time since `from`, counted every second; mounted only for the step that is running. */
function Elapsed({ from }: { from: string }) {
  const now = useNow(1000);
  return <span className="tabular-nums">{formatDuration(from, now)}</span>;
}

const lastExecution = (orchestration: Orchestration[], purpose: string) =>
  [...orchestration].reverse().find((o) => o.purpose === purpose);

const PURPOSES: readonly string[] = ["plan", "verify", "apply"];

/** Done steps stay grey, so colour marks only the step that failed, waits or runs. */
const nodeClass = (state: StepState) => (state === "done" ? QUIET_DONE : NODE[state]);

/**
 * The step of the abandoned execution and every step after it did not run. A step before it keeps
 * `done` when its execution succeeded, for example the plan of a run abandoned at its apply.
 */
function abandonedSteps(orchestration: Orchestration[]): {
  states: [StepState, StepState, StepState];
  from: number;
} {
  const last = orchestration.at(-1);
  const at = last?.purpose === "sync" ? 0 : PURPOSES.indexOf(last?.purpose ?? "");
  const from = Math.max(at, 0);
  const state = (i: number): StepState =>
    i < from && lastExecution(orchestration, PURPOSES[i])?.terminal_outcome === "succeeded"
      ? "done"
      : "skipped";
  return { states: [state(0), state(1), state(2)], from };
}

function stepTiming(o: Orchestration | undefined, state: StepState): ReactNode {
  if (!o || state === "idle" || state === "skipped") return null;
  if (o.terminal_at) {
    return o.claimed_at ? (
      <>
        Finished <When iso={o.terminal_at} /> · ran {formatDuration(o.claimed_at, o.terminal_at)}
      </>
    ) : (
      <>
        Ended <When iso={o.terminal_at} /> before a worker picked it up
      </>
    );
  }
  if (o.claimed_at) {
    return (
      <>
        Running for <Elapsed from={o.claimed_at} />
      </>
    );
  }
  if (o.submitted_at) {
    return (
      <>
        Waiting for a worker for <Elapsed from={o.submitted_at} />
      </>
    );
  }
  return null;
}

/** A plan run has one execution per step; a sync run's single execution belongs to one step only. */
function stepExecutions(
  resource: RunResource,
  states: readonly StepState[]
): (Orchestration | undefined)[] {
  const { run, orchestration } = resource;
  if (run.operation !== "sync") {
    return PURPOSES.map((p) => lastExecution(orchestration, p));
  }
  const sync = lastExecution(orchestration, "sync");
  const open = states.findIndex((s) => s === "failed" || s === "active");
  const target = open >= 0 ? open : states[2] === "done" ? 2 : -1;
  return states.map((_, i) => (i === target ? sync : undefined));
}

interface Step {
  state: StepState;
  word?: string;
  details: ReactNode[];
}

function buildSteps(
  resource: RunResource,
  inFlight: string | undefined,
  planExpired: boolean
): Step[] {
  const { run } = resource;
  const abandoned = run.phase === "abandoned";
  const cut = abandoned ? abandonedSteps(resource.orchestration) : undefined;
  const states: StepState[] = cut ? [...cut.states] : [...runSteps(run, { inFlight })];
  const expired = planExpired && states[2] === "waiting";
  if (expired) {
    states[1] = states[1] === "idle" ? "skipped" : states[1];
    states[2] = "skipped";
  }
  const [planExec, verifyExec, applyExec] = stepExecutions(resource, states);
  const counts = runCounts(run);
  const executable = executableCount(run);
  const waitingPlan = run.phase === "planned" && run.outcome === "planned" && !inFlight;
  const noChange = run.phase === "planned" && run.outcome === "no-change";
  const deleteOnly = waitingPlan && executable === 0 && (counts?.delete ?? 0) > 0;
  const verification = run.results?.verification as
    | { checksum_ok?: unknown; verification_notes?: unknown }
    | undefined;
  const verificationNotes = Array.isArray(verification?.verification_notes)
    ? verification.verification_notes.filter((n): n is string => typeof n === "string")
    : [];
  const words = states.map((_, i): string | undefined =>
    cut && i >= cut.from
      ? "not run"
      : expired && states[i] === "skipped"
        ? "not available"
        : undefined
  );
  const line = (key: string, content: ReactNode) => <span key={key}>{content}</span>;

  const plan: ReactNode[] = [];
  if (states[0] === "done" && counts) {
    plan.push(
      line(
        "found",
        counts.total === 0 && counts.deletesComputed !== false
          ? "No differences found"
          : countsSentence(counts, "plan")
      )
    );
  }

  const verify: ReactNode[] = [];
  if (verification?.checksum_ok === true) {
    verify.push(line("checksum", "The saved plan passed its checks"));
  } else if (verification?.checksum_ok === false) {
    verify.push(line("checksum", "The plan checksum did not match"));
  }
  for (const note of verificationNotes) verify.push(line(`note-${note}`, note));
  if (!verification) {
    // A failed check is recorded as `verify_failure`, so a verify that succeeded passed its checks.
    if (states[1] === "done" && verifyExec?.terminal_outcome === "succeeded") {
      verify.push(line("checksum", "The saved plan passed its checks"));
    } else if (states[1] === "done" && !verifyExec) {
      verify.push(
        line(
          "by-apply",
          run.operation === "sync"
            ? "The sync verified its saved plan before its first write"
            : "The apply checked the saved plan and the destination schema before its first write"
        )
      );
    } else if (states[1] === "idle" && waitingPlan && !expired) {
      verify.push(
        line(
          "optional",
          "Checks that the saved plan is complete and unchanged. It does not read the destination or write anything."
        )
      );
    } else if (states[1] === "skipped" && !abandoned) {
      if (noChange) {
        verify.push(line("skipped", "Not needed: the plan found no differences"));
      } else if (states[2] !== "idle" && states[2] !== "skipped") {
        verify.push(line("skipped", "Not verified separately before the apply"));
      }
    }
  }

  const apply: ReactNode[] = [];
  let applyWord = words[2];
  if (expired) {
    apply.push(line("expired", "The saved plan expired, so it cannot be applied"));
  } else if (run.phase === "applied" && counts) {
    apply.push(
      line("written", counts.total === 0 ? "Nothing to write" : countsSentence(counts, "done"))
    );
  } else if (states[2] === "waiting" && executable !== undefined) {
    apply.push(
      line(
        "waiting",
        <>
          Waiting for someone to apply {changesCount(executable)}
          {run.destination_branch && (
            <>
              {" "}
              to <BranchCode branch={run.destination_branch} />
            </>
          )}
        </>
      )
    );
  } else if (deleteOnly && counts) {
    applyWord = "nothing to apply";
    apply.push(
      line(
        "deletes",
        `The plan lists ${fmt(counts.delete)} ${counts.delete === 1 ? "delete" : "deletes"}, and an apply does not run deletes.`
      )
    );
  } else if (noChange) {
    apply.push(line("not-needed", "Not needed: nothing to write"));
  }

  const timed = (details: ReactNode[], o: Orchestration | undefined, state: StepState) => {
    const timing = stepTiming(o, state);
    return timing ? [...details, line("timing", timing)] : details;
  };

  return [
    { state: states[0], word: words[0], details: timed(plan, planExec, states[0]) },
    {
      state: states[1],
      word: words[1] ?? (states[1] === "idle" && waitingPlan ? "optional" : undefined),
      details: timed(verify, verifyExec, states[1]),
    },
    { state: states[2], word: applyWord, details: timed(apply, applyExec, states[2]) },
  ];
}

function StepCard({ index, name, step }: { index: number; name: string; step: Step }) {
  return (
    <li className="grid min-w-0 grid-cols-[0.75rem_minmax(0,1fr)] content-start gap-x-2.5 gap-y-1.5 px-3 py-3 sm:px-4 sm:py-3.5">
      <span className="mt-1 flex size-3 items-center justify-center">
        <span
          aria-hidden="true"
          className={cn("block shrink-0 rounded-full", nodeClass(step.state))}
        />
      </span>
      <div className="flex min-w-0 flex-col sm:flex-row sm:items-baseline sm:gap-2">
        <span className="font-medium">
          <span className="sr-only">Step {index + 1}: </span>
          {name}
        </span>
        <span className={cn("text-xs", STEP_TONE[step.state])}>
          <span className="sr-only">, </span>
          {step.word ?? STEP_WORD[step.state]}
        </span>
      </div>
      {step.details.length > 0 && (
        <div className="col-start-2 flex min-w-0 flex-col gap-0.5 text-pretty text-foreground-muted text-xs max-sm:sr-only">
          {step.details}
        </div>
      )}
    </li>
  );
}

const isQuiet = (s: StepState) => s === "done" || s === "idle" || s === "skipped";

/**
 * Plan, Verify and Apply side by side; on phones the step that needs attention explains itself
 * below. An abandoned run with no step done shows no strip: its notice says nothing ran.
 */
export function RunSteps({
  resource,
  inFlight,
  planExpired = false,
}: {
  resource: RunResource;
  inFlight?: string;
  /** `GET /runs/{run_id}/plan` answered 410: the saved plan can no longer be verified or applied. */
  planExpired?: boolean;
}) {
  const steps = buildSteps(resource, inFlight, planExpired);
  const names = stepNames(resource.run);
  if (resource.run.phase === "abandoned" && !steps.some((s) => s.state === "done")) return null;
  const focusIndex = steps.findIndex((s) => !isQuiet(s.state));
  const focus = steps[focusIndex];
  return (
    <Card>
      <h2 id="steps" className="sr-only">
        Progress of this run
      </h2>
      <ol aria-labelledby="steps" className="grid grid-cols-3 divide-x">
        {steps.map((step, i) => (
          <StepCard key={names[i]} index={i} name={names[i]} step={step} />
        ))}
      </ol>
      {focus && focus.details.length > 0 && (
        <div
          aria-hidden="true"
          className="flex flex-col gap-0.5 text-pretty border-t px-3 py-3 text-foreground-muted text-xs sm:hidden"
        >
          <span className="font-medium text-foreground">{names[focusIndex]}</span>
          {focus.details}
        </div>
      )}
    </Card>
  );
}
