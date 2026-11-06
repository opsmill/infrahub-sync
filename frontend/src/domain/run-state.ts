import type { Run } from "#/api/types";

/**
 * Where a configuration or run stands, for grouping and colour:
 * - `failed`: the latest run failed or was interrupted,
 * - `review`: a plan with changes waits for someone to apply it,
 * - `running`: an execution has not finished,
 * - `idle`: never planned, abandoned, or a newer version was registered after the latest run,
 * - `insync`: the latest run applied its changes or found none.
 */
export type RunState = "failed" | "review" | "running" | "idle" | "insync";
export type StepState = "done" | "failed" | "active" | "waiting" | "idle" | "skipped";
export const STEP_NAMES = ["Plan", "Verify", "Apply"] as const;

type Json = Record<string, unknown>;

export interface RunContext {
  /** The configuration's latest version; a run of an older version is outdated. */
  latestVersion?: number;
  /** The purpose ("verify", "apply") of an execution that has not finished, from `GET /runs/{run_id}`. */
  inFlight?: string;
}

const failure = (run: Run) =>
  ((run.results?.apply_failure ??
    run.results?.sync_failure ??
    run.results?.plan_failure ??
    run.results?.verify_failure) as Json | undefined) ?? undefined;

export const isFailedPhase = (phase: string) =>
  phase.endsWith("-failed") || phase.endsWith("-interrupted") || phase === "interrupted";

export const isOutdated = (run: Run, latestVersion?: number) =>
  latestVersion !== undefined &&
  run.registry_version != null &&
  run.registry_version < latestVersion;

export const isVerified = (run: Run) => Boolean(run.results?.verification);

export function runState(run: Run | undefined, ctx: RunContext = {}): RunState {
  if (!run) return "idle";
  if (run.phase === "accepted" || ctx.inFlight) return "running";
  if (isFailedPhase(run.phase) || run.reconciliation_required || run.results?.verify_failure)
    return "failed";
  if (run.phase === "abandoned" || isOutdated(run, ctx.latestVersion)) return "idle";
  if (run.phase === "planned" && run.outcome === "planned") return "review";
  return "insync";
}

export function runSteps(
  run: Run | undefined,
  ctx: RunContext = {}
): [StepState, StepState, StepState] {
  if (!run || run.phase === "abandoned") return ["idle", "idle", "idle"];
  const verified = isVerified(run);
  if (ctx.inFlight === "verify") return ["done", "active", "idle"];
  if (ctx.inFlight === "apply") return ["done", verified ? "done" : "skipped", "active"];
  if (run.phase === "accepted")
    return run.operation === "sync" ? ["active", "skipped", "idle"] : ["active", "idle", "idle"];
  const stage = run.phase.replace(/-(failed|interrupted)$/, "");
  if (stage === "plan") return ["failed", "idle", "idle"];
  if (run.results?.verify_failure) return ["done", "failed", "idle"];
  if (stage === "sync" && !failure(run)?.may_have_partially_written && !run.reconciliation_required)
    return ["failed", "skipped", "idle"];
  if (stage === "apply" || stage === "sync" || run.phase === "interrupted")
    return ["done", verified ? "done" : "skipped", "failed"];
  if (run.phase === "planned" && run.outcome === "planned")
    return verified ? ["done", "done", "waiting"] : ["done", "waiting", "idle"];
  if (run.phase === "planned") return ["done", "skipped", "skipped"];
  // Apply checks the saved plan against the destination again before its first write.
  return ["done", run.operation === "sync" ? "skipped" : "done", "done"];
}

/* ---------- counts ---------- */

const num = (v: unknown) => (typeof v === "number" ? v : 0);
export const fmt = (n: number) => n.toLocaleString("en-US");

export interface Counts {
  create: number;
  update: number;
  delete: number;
  total: number;
}

/** Planned or applied changes: a plan summary nests them in `by_action`, an apply summary does not. */
export function runCounts(run: Run): Counts | undefined {
  const s = (run.summary ?? {}) as Json;
  const by = (s.by_action as Json | undefined) ?? s;
  if (!("create" in by || "update" in by || "delete" in by)) return undefined;
  const create = num(by.create);
  const update = num(by.update);
  const del = num(by.delete);
  return { create, update, delete: del, total: create + update + del };
}

/**
 * Counts in words. An applied run's counts come from the plan it applied, and apply never runs
 * deletes, so its deletes read "not run".
 */
export function countsSentence(c: Counts, tense: "plan" | "done"): string {
  const parts =
    tense === "plan"
      ? [
          c.create && `${fmt(c.create)} to create`,
          c.update && `${fmt(c.update)} to update`,
          c.delete && `${fmt(c.delete)} to delete`,
        ]
      : [
          c.create && `${fmt(c.create)} created`,
          c.update && `${fmt(c.update)} updated`,
          c.delete && `${fmt(c.delete)} ${c.delete === 1 ? "delete" : "deletes"} not run`,
        ];
  return parts.filter(Boolean).join(", ");
}

/** Changes an apply writes: creates and updates. */
export const writtenCount = (c: Counts) => c.create + c.update;

/* ---------- failures ---------- */

/** Known error classes, in words. Anything else is shown by its class name. */
const ERROR_WORDS: Record<string, string> = {
  DestinationSchemaUnavailableError: "Could not read the destination schema on this branch",
  PlanSchemaChangedError: "The destination schema changed after the plan",
  ConnectError: "Could not connect",
  ConnectTimeout: "The connection timed out",
  ReadTimeout: "The system stopped answering",
  ConnectionError: "Could not connect to a system",
  OperationApplyFailedError: "The destination refused a write",
};

export interface FailureDetail {
  stage: string;
  sentence: string;
  errorType?: string;
  causeType?: string;
  cause?: string;
  nextAction?: string;
  ambiguous: boolean;
  appliedCount?: number;
  failedOperation?: string;
}

export function failureDetail(run: Run): FailureDetail | undefined {
  const f = failure(run);
  if (!f && !isFailedPhase(run.phase) && !run.reconciliation_required) return undefined;
  const stage = String(f?.stage ?? run.phase.split("-")[0]);
  const errorType = typeof f?.error_type === "string" ? f.error_type : undefined;
  const causeType = typeof f?.cause_type === "string" ? f.cause_type : undefined;
  const s = (run.summary ?? {}) as Json;
  return {
    stage,
    errorType,
    causeType,
    sentence:
      (causeType && ERROR_WORDS[causeType]) ??
      (errorType && ERROR_WORDS[errorType]) ??
      (errorType && causeType ? `${errorType} caused by ${causeType}` : errorType) ??
      "No error recorded",
    cause: typeof f?.cause === "string" ? f.cause : undefined,
    nextAction: typeof f?.next_action === "string" ? f.next_action : undefined,
    ambiguous: run.outcome === "ambiguous" || run.reconciliation_required,
    appliedCount: Array.isArray(s.applied_operations) ? s.applied_operations.length : undefined,
    failedOperation: typeof s.failed_operation === "string" ? s.failed_operation : undefined,
  };
}

const STAGE_WORD: Record<string, string> = {
  plan: "Plan",
  verify: "Verify",
  apply: "Apply",
  sync: "Sync",
};

/* ---------- words ---------- */

/** A short name for a run's phase and outcome, as the runs table shows it. */
export function phaseLabel(run: Run, ctx: RunContext = {}): string {
  if (ctx.inFlight === "verify") return "Verifying";
  if (ctx.inFlight === "apply") return "Applying";
  if (run.phase === "accepted") return run.operation === "sync" ? "Syncing" : "Planning";
  if (isFailedPhase(run.phase)) {
    const stage = STAGE_WORD[run.phase.split("-")[0]] ?? "Run";
    return run.phase.endsWith("interrupted") || run.phase === "interrupted"
      ? `${stage} interrupted`
      : `${stage} failed`;
  }
  if (run.results?.verify_failure) return "Verify failed";
  if (run.phase === "abandoned") return "Abandoned";
  if (run.phase === "planned")
    return run.outcome === "no-change"
      ? "No differences"
      : isVerified(run)
        ? "Verified"
        : "Plan ready";
  if (run.phase === "applied") return run.outcome === "no-change" ? "No differences" : "Applied";
  return run.phase;
}

export interface Explanation {
  short: string;
  note?: string;
  noteMono?: boolean;
  at?: { iso: string; prefix: string };
  action?: { label: string; primary: boolean; target: "run" | "plan" };
}

/** The reason line for a configuration's latest run. */
export function explain(
  run: Run | undefined,
  ctx: RunContext & { createdAt: string; lastApplied?: Run }
): Explanation {
  if (!run) {
    return {
      short: "Never planned",
      at: { iso: ctx.createdAt, prefix: "Registered " },
      action: { label: "Plan first run", primary: false, target: "plan" },
    };
  }
  const state = runState(run, ctx);
  if (state === "running") {
    return {
      short: `${phaseLabel(run, ctx)}…`,
      note: run.actor ? `Started by ${run.actor}` : undefined,
      at: { iso: run.started_at, prefix: "" },
      action: { label: "View run", primary: false, target: "run" },
    };
  }
  if (state === "failed") {
    const f = failureDetail(run);
    const stage = STAGE_WORD[f?.stage ?? ""] ?? "Run";
    const c = runCounts(run);
    const short = f?.ambiguous
      ? `${stage} interrupted, may have written`
      : f?.appliedCount !== undefined && c && c.total > 0
        ? `${stage} failed after ${fmt(f.appliedCount)} of ${fmt(c.total)} changes`
        : `${stage} failed`;
    const note = f?.ambiguous
      ? "Inspect the destination, then plan again"
      : (f?.nextAction ?? f?.cause ?? f?.sentence);
    return {
      short,
      note,
      at: { iso: run.finished_at ?? run.started_at, prefix: "" },
      action: { label: f?.ambiguous ? "View run" : "View failure", primary: false, target: "run" },
    };
  }
  if (isOutdated(run, ctx.latestVersion)) {
    return {
      short: `Version ${ctx.latestVersion} not planned yet`,
      note: ctx.lastApplied?.finished_at
        ? `Version ${ctx.lastApplied.registry_version} applied`
        : `Version ${run.registry_version} last planned`,
      at: { iso: ctx.lastApplied?.finished_at ?? run.started_at, prefix: "" },
      action: { label: `Plan version ${ctx.latestVersion}`, primary: false, target: "plan" },
    };
  }
  if (run.phase === "abandoned") {
    return {
      short: "Latest plan abandoned",
      note: "No worker finished it",
      at: { iso: run.finished_at ?? run.started_at, prefix: "" },
      action: { label: "Plan again", primary: false, target: "plan" },
    };
  }
  if (state === "review") {
    const c = runCounts(run);
    return {
      short: c ? `Plan ready: ${countsSentence(c, "plan")}` : "Plan ready",
      note: isVerified(run) ? "Verified against the destination" : "Not verified",
      at: { iso: run.finished_at ?? run.started_at, prefix: "Planned " },
      action: {
        label: c ? `Review ${fmt(c.total)} ${c.total === 1 ? "change" : "changes"}` : "Review plan",
        primary: true,
        target: "run",
      },
    };
  }
  if (run.phase === "planned") {
    return {
      short: "No differences",
      note: ctx.lastApplied?.finished_at ? undefined : "Nothing applied yet",
      at: { iso: run.finished_at ?? run.started_at, prefix: "Checked " },
    };
  }
  const c = runCounts(run);
  const written = c ? writtenCount(c) : 0;
  return {
    short:
      written > 0
        ? `Applied ${fmt(written)} ${written === 1 ? "change" : "changes"}`
        : "Applied, no changes",
    note: run.operation === "sync" ? "Synced without review" : undefined,
    at: { iso: run.finished_at ?? run.started_at, prefix: "" },
  };
}

const ORDER: Record<RunState, number> = { failed: 0, review: 1, running: 2, idle: 3, insync: 4 };
export const byAttention = (a: RunState, b: RunState) => ORDER[a] - ORDER[b];

/** State and label of a run reference, which carries only its phase, outcome and times. */
export function refState(ref: { phase: string; outcome?: string | null }): RunState {
  if (ref.phase === "accepted") return "running";
  if (isFailedPhase(ref.phase)) return "failed";
  if (ref.phase === "abandoned") return "idle";
  if (ref.phase === "planned" && ref.outcome === "planned") return "review";
  return "insync";
}

export function refLabel(ref: { phase: string; outcome?: string | null }): string {
  if (ref.phase === "accepted") return "Running";
  if (isFailedPhase(ref.phase)) {
    const stage = STAGE_WORD[ref.phase.split("-")[0]] ?? "Run";
    return ref.phase.endsWith("interrupted") || ref.phase === "interrupted"
      ? `${stage} interrupted`
      : `${stage} failed`;
  }
  if (ref.phase === "abandoned") return "Abandoned";
  if (ref.phase === "planned") return ref.outcome === "no-change" ? "No differences" : "Plan ready";
  if (ref.phase === "applied") return ref.outcome === "no-change" ? "No differences" : "Applied";
  return ref.phase;
}
