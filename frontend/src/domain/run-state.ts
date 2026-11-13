import type { Run, RunReference, VersionSummary } from "#/api/types";

import type { System } from "./systems";

/**
 * Where a configuration or run stands, for grouping and colour:
 * - `failed`: the latest run failed or was interrupted,
 * - `review`: a plan waits for a person, including a plan that lists only deletes,
 * - `running`: an execution has not finished,
 * - `idle`: never planned, abandoned, cancelled, or a newer version was registered after the latest run,
 * - `insync`: the latest run applied its changes or found none.
 */
export type RunState = "failed" | "review" | "running" | "idle" | "insync";
export type StepState = "done" | "failed" | "active" | "waiting" | "idle" | "skipped";
export const STEP_NAMES = ["Plan", "Verify", "Apply"] as const;

/** The names of a run's three steps: a sync run computes and writes in its first step. */
export const stepNames = (run?: Run): readonly [string, string, string] =>
  run?.operation === "sync" ? (["Sync", "Verify", "Apply"] as const) : STEP_NAMES;

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

/**
 * The step a run in phase "interrupted" stopped in. The Sync service sets
 * `reconciliation_required` only for an interrupted apply or sync, so any other interrupted run
 * stopped in its plan, or in its verify when the plan had already recorded counts.
 */
function interruptedStage(run: Run): "plan" | "verify" | "apply" | "sync" {
  if (run.reconciliation_required) return run.operation === "sync" ? "sync" : "apply";
  return runCounts(run) ? "verify" : "plan";
}

const isWaitingPlan = (run: Run) => run.phase === "planned" && run.outcome === "planned";

export function runState(run: Run | undefined, ctx: RunContext = {}): RunState {
  if (!run) return "idle";
  if (run.phase === "accepted" || ctx.inFlight) return "running";
  if (isFailedPhase(run.phase) || run.reconciliation_required || run.results?.verify_failure)
    return "failed";
  if (run.phase === "cancelled" || run.phase === "abandoned") return "idle";
  if (isOutdated(run, ctx.latestVersion)) return "idle";
  if (isWaitingPlan(run)) return "review";
  if (run.phase === "applied" || (run.phase === "planned" && run.outcome === "no-change"))
    return "insync";
  return "idle";
}

export function runSteps(
  run: Run | undefined,
  ctx: RunContext = {}
): [StepState, StepState, StepState] {
  if (!run || run.phase === "abandoned") return ["idle", "idle", "idle"];
  if (run.phase === "cancelled") return [runCounts(run) ? "done" : "skipped", "skipped", "skipped"];
  const verified = isVerified(run);
  if (ctx.inFlight === "verify") return ["done", "active", "idle"];
  if (ctx.inFlight === "apply") return ["done", verified ? "done" : "skipped", "active"];
  if (run.phase === "accepted") return ["active", "idle", "idle"];
  const stage =
    run.phase === "interrupted"
      ? interruptedStage(run)
      : run.phase.replace(/-(failed|interrupted)$/, "");
  if (stage === "plan") return ["failed", "idle", "idle"];
  if (stage === "verify") return ["done", "failed", "idle"];
  if (run.results?.verify_failure) return ["done", "failed", "idle"];
  if (stage === "sync" && !failure(run)?.may_have_partially_written && !run.reconciliation_required)
    return ["failed", "idle", "idle"];
  // A sync verifies its plan before the first write, so a partial write follows a passed verify.
  if (stage === "sync") return ["done", "done", "failed"];
  if (stage === "apply") return ["done", verified ? "done" : "skipped", "failed"];
  if (isOutdated(run, ctx.latestVersion)) return ["idle", "idle", "idle"];
  if (isWaitingPlan(run)) {
    if (executableCount(run) === 0) return ["done", "idle", "skipped"];
    return verified ? ["done", "done", "waiting"] : ["done", "idle", "waiting"];
  }
  if (run.phase === "planned") return ["done", "skipped", "skipped"];
  if (run.phase !== "applied") return ["idle", "idle", "idle"];
  // Apply checks the saved plan and the destination schema before its first write, and a sync
  // verifies its plan before it applies it.
  return ["done", "done", "done"];
}

/* ---------- counts ---------- */

const num = (v: unknown) => (typeof v === "number" ? v : 0);
export const fmt = (n: number) => n.toLocaleString("en-US");

/** A count with its noun: `1 mapping`, `1,204 fields`. */
export const plural = (n: number, one: string, many: string) => `${fmt(n)} ${n === 1 ? one : many}`;

export interface Counts {
  create: number;
  update: number;
  delete: number;
  total: number;
  /** `summary.delete_operations_computed`: false when the destination was not read in full, so deletes were not listed. */
  deletesComputed?: boolean;
}

/**
 * Planned or applied changes: a plan summary nests them in `by_action`, an apply summary does not.
 * An empty `by_action` (a plan with no differences) gives zero counts.
 */
export function runCounts(run: Run): Counts | undefined {
  const s = (run.summary ?? {}) as Json;
  const nested = "by_action" in s;
  const by = nested ? ((s.by_action as Json | null) ?? {}) : s;
  if (!nested && !("create" in by || "update" in by || "delete" in by)) return undefined;
  const create = num(by.create);
  const update = num(by.update);
  const del = num(by.delete);
  return {
    create,
    update,
    delete: del,
    total: create + update + del,
    deletesComputed:
      typeof s.delete_operations_computed === "boolean" ? s.delete_operations_computed : undefined,
  };
}

/** Changes an apply writes: creates and updates. */
export const writtenCount = (c: Counts) => c.create + c.update;

/** The changes an apply of this run writes, or undefined when the run records no counts. */
export const executableCount = (run: Run): number | undefined => {
  const c = runCounts(run);
  return c ? writtenCount(c) : undefined;
};

/** A plan waiting for review whose only operations are deletes, which apply does not run. */
export const isDeleteOnlyPlan = (run: Run): boolean =>
  isWaitingPlan(run) && executableCount(run) === 0 && (runCounts(run)?.delete ?? 0) > 0;

/** "1 delete listed, not applied": the count wording for deletes, which apply never runs. */
export const deletesListed = (n: number) =>
  `${fmt(n)} ${n === 1 ? "delete" : "deletes"} listed, not applied`;

function writesWords(c: Counts, tense: "plan" | "done"): string {
  return [
    c.create && `${fmt(c.create)} ${tense === "plan" ? "to create" : "created"}`,
    c.update && `${fmt(c.update)} ${tense === "plan" ? "to update" : "updated"}`,
  ]
    .filter(Boolean)
    .join(", ");
}

/**
 * Counts in words: the writes first, then the deletes, which apply never runs, in both tenses.
 * A plan with no counts at all gives an empty string unless its deletes were not listed.
 */
export function countsSentence(c: Counts, tense: "plan" | "done"): string {
  const writes = writesWords(c, tense);
  const parts = [writes];
  if (c.delete > 0) {
    if (!writes) parts[0] = "Nothing to write";
    parts.push(deletesListed(c.delete));
  }
  if (c.deletesComputed === false) {
    if (!parts[0]) parts[0] = "Nothing to write";
    parts.push("deletes not listed because the destination was not read in full");
  }
  return parts.filter(Boolean).join("; ");
}

/* ---------- failures ---------- */

/** Known error classes, in words. Anything else is shown by its class name. */
const ERROR_WORDS: Record<string, string> = {
  DestinationSchemaUnavailableError: "Could not read the destination schema on this branch",
  PlanSchemaChangedError: "The destination schema changed after the plan",
  PlanVerificationError: "A check before the apply failed, so the Sync service refused the apply",
  ConnectError: "Could not connect to the source or the destination",
  ConnectTimeout: "The connection to the source or the destination timed out",
  ReadTimeout: "The source or the destination stopped answering",
  ConnectionError: "Could not connect to the source or the destination",
  OperationApplyFailedError: "The destination refused a write",
};

const errorWords = (type: string | undefined) =>
  type && Object.hasOwn(ERROR_WORDS, type) ? ERROR_WORDS[type] : undefined;

export type RecoveryAction = "rebuild" | "compatible_version";

export interface FailureDetail {
  stage: string;
  sentence: string;
  errorType?: string;
  causeType?: string;
  cause?: string;
  nextAction?: string;
  /**
   * How to recover from an apply that refused the saved plan for its format version, when the Sync
   * service recorded it. `nextAction` then holds the same recovery in command-line words.
   */
  recoveryAction?: RecoveryAction;
  ambiguous: boolean;
  appliedCount?: number;
  failedOperation?: string;
  /** True when the cause or error class has words in this app, so `sentence` is not a class name. */
  known: boolean;
  /** The recorded class names: `${errorType}, caused by ${causeType}`, or `errorType` alone. */
  errorLabel?: string;
}

export function failureDetail(run: Run): FailureDetail | undefined {
  const f = failure(run);
  if (!f && !isFailedPhase(run.phase) && !run.reconciliation_required) return undefined;
  const stage = String(
    f?.stage ?? (run.phase === "interrupted" ? interruptedStage(run) : run.phase.split("-")[0])
  );
  const errorType = typeof f?.error_type === "string" ? f.error_type : undefined;
  const causeType = typeof f?.cause_type === "string" ? f.cause_type : undefined;
  const words = errorWords(causeType) ?? errorWords(errorType);
  const errorLabel =
    errorType && causeType ? `${errorType}, caused by ${causeType}` : (errorType ?? causeType);
  const s = (run.summary ?? {}) as Json;
  return {
    stage,
    errorType,
    causeType,
    known: words !== undefined,
    errorLabel,
    sentence: words ?? errorLabel ?? "No error recorded",
    cause: typeof f?.cause === "string" ? f.cause : undefined,
    nextAction: typeof f?.next_action === "string" ? f.next_action : undefined,
    recoveryAction:
      f?.recovery_action === "rebuild" || f?.recovery_action === "compatible_version"
        ? f.recovery_action
        : undefined,
    // An interrupted plan or verify does not write, so only an interrupted apply or sync is ambiguous.
    ambiguous: run.reconciliation_required,
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

const CONNECT_ERRORS = new Set(["ConnectError", "ConnectTimeout", "ConnectionError"]);

/* ---------- words ---------- */

function failedPhaseLabel(phase: string, run?: Run): string {
  const key = phase === "interrupted" && run ? interruptedStage(run) : phase.split("-")[0];
  const stage = STAGE_WORD[key] ?? "Run";
  return phase.endsWith("interrupted") ? `${stage} interrupted` : `${stage} failed`;
}

/** A short name for a run's phase and outcome, as the runs table shows it. */
export function phaseLabel(run: Run, ctx: RunContext = {}): string {
  if (ctx.inFlight === "verify") return "Verifying";
  if (ctx.inFlight === "apply") return "Applying";
  if (run.phase === "accepted") return run.operation === "sync" ? "Syncing" : "Planning";
  if (isFailedPhase(run.phase)) return failedPhaseLabel(run.phase, run);
  if (run.results?.verify_failure) return "Verify failed";
  if (run.phase === "abandoned") return "Abandoned";
  if (run.phase === "cancelled") return "Cancelled";
  if (run.phase === "planned") {
    if (run.outcome === "no-change") return "No differences";
    if (isDeleteOnlyPlan(run)) return "Plan lists only deletes";
    return isVerified(run) ? "Verified" : "Plan ready";
  }
  if (run.phase === "applied") return run.outcome === "no-change" ? "No differences" : "Applied";
  return `Unknown state: ${run.phase}`;
}

export interface Explanation {
  short: string;
  note?: string;
  noteMono?: boolean;
  /** The actor that started a running run. */
  by?: string;
  at?: { iso: string; prefix: string };
  action?: { label: string; primary: boolean; target: "run" | "plan" };
}

/** What `explain` needs besides the run: the configuration's registration time and systems. */
export interface ExplainContext extends RunContext {
  createdAt: string;
  /** The newest applied run of the configuration, when one is loaded. */
  lastApplied?: { finished_at?: string | null; registry_version?: number | null };
  source?: System;
  destination?: System;
}

export const changesCount = (n: number) => `${fmt(n)} ${n === 1 ? "change" : "changes"}`;
const NOT_READ_IN_FULL = "Deletes not listed because the destination was not read in full";

/** The reason line for a configuration's latest run. */
export function explain(run: Run | undefined, ctx: ExplainContext): Explanation {
  if (!run) {
    return {
      short: "Never planned",
      at: { iso: ctx.createdAt, prefix: "Registered " },
      action: { label: "Plan first run", primary: false, target: "plan" },
    };
  }
  const ended = run.finished_at ?? run.started_at;
  const state = runState(run, ctx);
  if (state === "running") {
    return {
      short: `${phaseLabel(run, ctx)}…`,
      by: run.actor ?? undefined,
      at: { iso: run.started_at, prefix: "Started " },
      action: { label: "View run", primary: false, target: "run" },
    };
  }
  const c = runCounts(run);
  if (state === "failed") {
    const f = failureDetail(run);
    const stage = STAGE_WORD[f?.stage ?? ""] ?? "Run";
    const written = c ? writtenCount(c) : 0;
    const short = f?.ambiguous
      ? `${stage} interrupted, may have written`
      : run.phase.endsWith("interrupted")
        ? `${stage} interrupted`
        : f?.appliedCount !== undefined && written > 0
          ? `${stage} failed after ${fmt(f.appliedCount)} of ${changesCount(written)}`
          : `${stage} failed`;
    const { source, destination } = ctx;
    const connect =
      [f?.causeType, f?.errorType].some((t) => t !== undefined && CONNECT_ERRORS.has(t)) &&
      source?.host &&
      destination?.host &&
      ctx.latestVersion !== undefined &&
      run.registry_version === ctx.latestVersion;
    const reason: Pick<Explanation, "note" | "noteMono"> = f?.ambiguous
      ? { note: "Inspect the destination, then plan again" }
      : connect
        ? {
            note: `Could not connect to ${source.host} (${source.label}) or ${destination.host} (${destination.label})`,
          }
        : f?.known
          ? { note: f.sentence }
          : f?.errorLabel
            ? { note: f.errorLabel, noteMono: true }
            : { note: "No error recorded" };
    return {
      short,
      ...reason,
      at: { iso: ended, prefix: "Failed " },
      action: { label: f?.ambiguous ? "View run" : "View failure", primary: false, target: "run" },
    };
  }
  if (run.phase === "cancelled") {
    return {
      short: "Latest run cancelled",
      note: run.actor ? `Started by ${run.actor}` : undefined,
      at: { iso: ended, prefix: "Cancelled " },
      action: { label: "Plan again", primary: false, target: "plan" },
    };
  }
  if (isOutdated(run, ctx.latestVersion)) {
    const applied = ctx.lastApplied;
    return {
      short: `Version ${ctx.latestVersion} not planned yet`,
      at: applied?.finished_at
        ? {
            iso: applied.finished_at,
            prefix:
              applied.registry_version != null
                ? `Version ${applied.registry_version} applied `
                : "Applied ",
          }
        : { iso: run.started_at, prefix: `Version ${run.registry_version} planned ` },
      action: { label: `Plan version ${ctx.latestVersion}`, primary: false, target: "plan" },
    };
  }
  if (run.phase === "abandoned") {
    return {
      short: "Latest plan abandoned",
      note: "No worker picked it up",
      at: { iso: ended, prefix: "Abandoned " },
      action: { label: "Plan again", primary: false, target: "plan" },
    };
  }
  const summary = (run.summary ?? {}) as Json;
  const deletes =
    typeof summary.deletes_not_executed === "number"
      ? summary.deletes_not_executed
      : (c?.delete ?? 0);
  if (isDeleteOnlyPlan(run)) {
    const dest = ctx.destination?.label ?? "the destination";
    return {
      short: "Plan lists only deletes",
      note: `${fmt(deletes)} ${deletes === 1 ? "node exists" : "nodes exist"} only in ${dest}. Apply does not delete nodes.`,
      at: { iso: ended, prefix: "Planned " },
      action: { label: "View plan", primary: false, target: "run" },
    };
  }
  if (state === "review") {
    const writes = c ? writesWords(c, "plan") : "";
    const notes = [
      deletes > 0 && deletesListed(deletes),
      isVerified(run) && "Saved plan verified",
      c?.deletesComputed === false && NOT_READ_IN_FULL,
    ].filter(Boolean);
    return {
      short: writes ? `Plan ready: ${writes}` : "Plan ready",
      note: notes.length ? notes.join(" · ") : undefined,
      at: { iso: ended, prefix: "Planned " },
      action: {
        label: c ? `Review ${changesCount(writtenCount(c))}` : "Review plan",
        primary: true,
        target: "run",
      },
    };
  }
  if (run.phase === "planned" && run.outcome === "no-change") {
    return {
      short: "No differences",
      note: c?.deletesComputed === false ? NOT_READ_IN_FULL : undefined,
      at: { iso: ended, prefix: "Checked " },
    };
  }
  if (run.phase !== "applied") {
    return { short: phaseLabel(run, ctx), at: { iso: ended, prefix: "" } };
  }
  const written = c ? writtenCount(c) : 0;
  const notes = [
    c && c.delete > 0 && deletesListed(c.delete),
    run.operation === "sync" && "Synced without review",
  ].filter(Boolean);
  return {
    short: written > 0 ? `Applied ${changesCount(written)}` : "Applied, no changes",
    note: notes.length ? notes.join(" · ") : undefined,
    at: { iso: ended, prefix: "Applied " },
  };
}

const ORDER: Record<RunState, number> = { failed: 0, review: 1, running: 2, idle: 3, insync: 4 };
export const byAttention = (a: RunState, b: RunState) => ORDER[a] - ORDER[b];

/**
 * State and label of a run reference, which carries only its phase, outcome and times. A reference
 * does not carry counts, so a plan that lists only deletes reads "Plan ready" here.
 */
export function refState(ref: { phase: string; outcome?: string | null }): RunState {
  if (ref.phase === "accepted") return "running";
  if (isFailedPhase(ref.phase)) return "failed";
  if (ref.phase === "cancelled" || ref.phase === "abandoned") return "idle";
  if (ref.phase === "planned" && ref.outcome === "planned") return "review";
  if (ref.phase === "applied" || (ref.phase === "planned" && ref.outcome === "no-change"))
    return "insync";
  return "idle";
}

export function refLabel(ref: { phase: string; outcome?: string | null }): string {
  if (ref.phase === "accepted") return "Running";
  if (isFailedPhase(ref.phase)) return failedPhaseLabel(ref.phase);
  if (ref.phase === "abandoned") return "Abandoned";
  if (ref.phase === "cancelled") return "Cancelled";
  if (ref.phase === "planned") return ref.outcome === "no-change" ? "No differences" : "Plan ready";
  if (ref.phase === "applied") return ref.outcome === "no-change" ? "No differences" : "Applied";
  return `Unknown state: ${ref.phase}`;
}

/** The version whose `latest_applied_run` ended last; undefined when none was applied. */
export function lastAppliedVersion(
  versions: VersionSummary[]
): { version: number; run: RunReference } | undefined {
  const when = (run: RunReference) => run.finished_at ?? run.started_at;
  let last: { version: number; run: RunReference } | undefined;
  for (const v of versions) {
    const run = v.latest_applied_run;
    if (run && (!last || when(run) > when(last.run))) last = { version: v.registry_version, run };
  }
  return last;
}

/* ---------- lists of runs ---------- */

/** How the runs table shows one run. */
export interface ListRunState {
  /** Drives the colour and the icon. */
  state: RunState;
  /** `phaseLabel(run, { inFlight })`. */
  label: string;
  /** Not the newest run of its configuration: the icon keeps its colour and the label is muted. */
  quiet: boolean;
  /** Second line in the state cell. */
  note?: string;
  /** The newest run of its configuration, and failed or waiting for review. */
  needsAction: boolean;
}

/** Only the newest run of a configuration asks for action; an older plan or failure does not. */
export function listRunState(
  run: Run,
  ctx: { newest: boolean; latestVersion?: number; inFlight?: string }
): ListRunState {
  const { newest, latestVersion, inFlight } = ctx;
  const base = runState(run, { latestVersion, inFlight });
  let state = base;
  let note: string | undefined;
  if (base !== "failed" && base !== "running" && isOutdated(run, latestVersion)) {
    state = "idle";
    note = `Version ${latestVersion} is newer`;
  } else if (base === "review" && !newest) {
    state = "idle";
    note = "A newer run of this configuration exists";
  }
  return {
    state,
    label: phaseLabel(run, { inFlight }),
    quiet: !newest,
    note,
    needsAction: newest && (state === "failed" || state === "review"),
  };
}

/** `runs` newest first. A run is newest when no earlier item has its config_id; a null config_id is newest of itself. */
export function listRunStates(
  runs: Run[],
  ctx: { latestVersions: Map<string, number>; inFlight?: Map<string, string> }
): Map<string, ListRunState> {
  const seen = new Set<string>();
  const states = new Map<string, ListRunState>();
  for (const run of runs) {
    const configId = run.config_id;
    const newest = !configId || !seen.has(configId);
    if (configId) seen.add(configId);
    states.set(
      run.run_id,
      listRunState(run, {
        newest,
        latestVersion: configId ? ctx.latestVersions.get(configId) : undefined,
        inFlight: ctx.inFlight?.get(run.run_id),
      })
    );
  }
  return states;
}

/* ---------- sibling runs ---------- */

const time = (iso: string) => new Date(iso).getTime();

/** Other runs on the same destination branch that finished after `run`. */
function laterSiblings(run: Run, siblings: Run[]): Run[] {
  const { finished_at } = run;
  if (!finished_at) return [];
  return siblings.filter(
    (s) =>
      s.run_id !== run.run_id &&
      (s.destination_branch ?? null) === (run.destination_branch ?? null) &&
      s.finished_at != null &&
      time(s.finished_at) > time(finished_at)
  );
}

const latestBy = (runs: Run[], key: (r: Run) => string) =>
  runs.reduce<Run | undefined>(
    (best, r) => (!best || time(key(r)) > time(key(best)) ? r : best),
    undefined
  );

const wrote = (run: Run) =>
  (run.phase === "applied" && run.outcome === "applied") ||
  run.reconciliation_required ||
  failure(run)?.may_have_partially_written === true;

/** The latest candidate that wrote: applied/applied, reconciliation_required, or a failure with may_have_partially_written. */
export function laterWrite(run: Run, siblings: Run[]): Run | undefined {
  return latestBy(laterSiblings(run, siblings).filter(wrote), (r) => r.finished_at ?? r.started_at);
}

/** The candidate with phase planned/planned and the latest started_at. */
export function newerPlan(run: Run, siblings: Run[]): Run | undefined {
  return latestBy(laterSiblings(run, siblings).filter(isWaitingPlan), (r) => r.started_at);
}

/* ---------- start run ---------- */

/** The newest run of the configuration with phase "planned" or "applied" and a destination branch. `runs` newest first. */
export function branchPrefill(
  runs: Run[],
  configId: string
): { branch: string; runId: string } | undefined {
  const run = runs.find(
    (r) =>
      r.config_id === configId &&
      (r.phase === "planned" || r.phase === "applied") &&
      Boolean(r.destination_branch)
  );
  return run?.destination_branch
    ? { branch: run.destination_branch, runId: run.run_id }
    : undefined;
}

/* ---------- workers ---------- */

/** The notice when no worker is online. */
export const NO_WORKER_SENTENCE =
  "No worker is online, so new runs do not start. The Sync service keeps them waiting, and a run that no worker picks up in time is abandoned.";

/** `queue_depth` from `GET /status` counts scheduled executions, not runs. */
export const queueSentence = (n: number) =>
  `${fmt(n)} ${n === 1 ? "execution is" : "executions are"} waiting for a worker.`;
