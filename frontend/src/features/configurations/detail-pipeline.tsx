import { buttonVariants, Card } from "@infrahub/ui";
import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";
import { cn } from "tailwind-variants";

import type { Orchestration, Run, RunResource } from "#/api/types";
import { ChevronRightIcon } from "#/components/icons";
import { Bar, FOCUS, textAction } from "#/components/page";
import { BranchBadge, NODE, STEP_TONE, StateLabel, When } from "#/components/run-status";
import {
  type Counts,
  changesCount,
  deletesListed,
  type FailureDetail,
  failureDetail,
  fmt,
  isDeleteOnlyPlan,
  isOutdated,
  isVerified,
  phaseLabel,
  type RunState,
  runCounts,
  runState,
  type StepState,
  writtenCount,
} from "#/domain/run-state";
import type { System } from "#/domain/systems";

/** "optional": Verify of a plan waiting for review. "nothing": Verify and Apply of a plan with no writes. */
type StageState = StepState | "optional" | "nothing";
type StageKey = "plan" | "verify" | "apply" | "sync";

interface Stage {
  key: StageKey;
  state: StageState;
  word: string;
  value?: ReactNode;
  notes?: { id: string; node: ReactNode }[];
}

interface Clause {
  id: string;
  node: ReactNode;
}

export interface PipelineModel {
  state: RunState;
  lead: string;
  clauses: Clause[];
  /** Plan, Verify and Apply, or one Sync stage. */
  stages: Stage[];
  /** The aria-live text: the lead and the first stage's value, without times. */
  live: string;
  action?: { label: string; kind: "review" | "run" | "plan" };
}

export interface PipelineContext {
  latestVersion?: number;
  /** The execution of the latest run that has not finished, from `GET /runs/{run_id}`. */
  inFlight?: Orchestration;
  createdAt: string;
  /** The newest `latest_applied_run` across the configuration's versions. */
  lastApplied?: { run_id: string; finished_at: string };
  /** `GET /runs/{run_id}` of the latest run, read for abandoned runs. */
  latestRunDetail?: RunResource;
  sourceLabel?: string;
  destinationLabel?: string;
}

const NAME: Record<StageKey, string> = {
  plan: "Plan",
  verify: "Verify",
  apply: "Apply",
  sync: "Sync",
};

const WORD: Record<StageState, string> = {
  done: "Done",
  failed: "Failed",
  active: "Running",
  waiting: "Waiting for review",
  idle: "Not started",
  skipped: "Skipped",
  optional: "Optional",
  nothing: "Nothing to apply",
};

const stage = (
  key: StageKey,
  state: StageState,
  extra: Partial<Omit<Stage, "key" | "state">> = {}
): Stage => ({ key, state, word: WORD[state], ...extra });

const appliedWords = (written: number) =>
  written === 0 ? "No changes written" : `${changesCount(written)} written`;

function planWords(c: Counts | undefined): string {
  const parts = [
    c?.create && `${fmt(c.create)} to create`,
    c?.update && `${fmt(c.update)} to update`,
  ].filter(Boolean);
  return parts.length ? parts.join(", ") : "Nothing to write";
}

function failText(f: FailureDetail | undefined): string {
  if (f?.known) return f.sentence;
  return f?.errorLabel ?? "No error recorded";
}

const CODE = "font-mono text-xs [overflow-wrap:anywhere]";

function failValue(f: FailureDetail | undefined): ReactNode {
  if (f?.known) return f.sentence;
  if (f?.errorType) {
    return (
      <>
        <code className={CODE}>{f.errorType}</code>
        {f.causeType && (
          <>
            , caused by <code className={CODE}>{f.causeType}</code>
          </>
        )}
      </>
    );
  }
  if (f?.causeType) return <code className={CODE}>{f.causeType}</code>;
  return "No error recorded";
}

const NOT_READ_IN_FULL = "Deletes not listed because the destination was not read in full";

function kindsNote(summary: Record<string, unknown>): ReactNode | undefined {
  const byKind = summary.by_kind;
  if (!byKind || typeof byKind !== "object") return undefined;
  const entries = Object.entries(byKind as Record<string, unknown>)
    .filter((e): e is [string, number] => typeof e[1] === "number")
    .sort((a, b) => b[1] - a[1]);
  if (entries.length === 0) return undefined;
  const more = entries.length - 4;
  return (
    <span className="flex flex-wrap gap-x-3 tabular-nums">
      <span>Operations by kind:</span>
      {entries.slice(0, 4).map(([kind, n]) => (
        <span key={kind} className="whitespace-nowrap">
          {kind} {fmt(n)}
        </span>
      ))}
      {more > 0 && (
        <span className="whitespace-nowrap">
          and {fmt(more)} more {more === 1 ? "kind" : "kinds"}
        </span>
      )}
    </span>
  );
}

/** A `-failed` phase is written only when no write was dispatched or the failing operation did not write. */
const beforeAnyWrite = (run: Run, f: FailureDetail | undefined) =>
  run.phase.endsWith("-failed") && !f?.ambiguous && !f?.appliedCount;

function failedLead(stageWord: string, run: Run, f: FailureDetail | undefined) {
  if (f?.ambiguous) return `${stageWord} interrupted, may have written`;
  if (f?.appliedCount) {
    const c = runCounts(run);
    const written = c ? writtenCount(c) : 0;
    return written >= f.appliedCount
      ? `${stageWord} failed after ${fmt(f.appliedCount)} of ${changesCount(written)}`
      : `${stageWord} failed after ${changesCount(f.appliedCount)} ${f.appliedCount === 1 ? "was" : "were"} written`;
  }
  if (beforeAnyWrite(run, f)) return `${stageWord} failed before writing any change`;
  return phaseLabel(run);
}

function lastAppliedClause(run: Run, ctx: PipelineContext): Clause[] {
  const last = ctx.lastApplied;
  if (!last || last.run_id === run.run_id) return [];
  return [
    {
      id: "last-applied",
      node: (
        <Link
          to="/runs/$runId"
          params={{ runId: last.run_id }}
          className={cn(
            "relative whitespace-nowrap rounded-sm underline decoration-subtle-muted underline-offset-4 before:absolute before:-inset-x-1 before:-inset-y-3 before:content-[''] hover:decoration-foreground",
            FOCUS
          )}
        >
          Last applied <When iso={last.finished_at} />
        </Link>
      ),
    },
  ];
}

function abandonedClause(detail: RunResource | undefined): Clause[] {
  const last = detail?.orchestration.at(-1);
  if (!last) return [];
  const text = last.cancellation_requested_at
    ? "Cancelled before a worker picked it up"
    : last.claimed_at == null
      ? "No worker picked it up in time"
      : "Abandoned after a worker picked it up";
  return [{ id: "why", node: text }];
}

const live = (lead: string, value?: string) => (value ? `${lead}. ${value}` : lead);

/** The notes under the Plan stage of a plan that finished: listed deletes and counts by kind. */
function planNotes(run: Run): Stage["notes"] {
  const summary = (run.summary ?? {}) as Record<string, unknown>;
  const c = runCounts(run);
  const listedDeletes =
    typeof summary.deletes_not_executed === "number"
      ? summary.deletes_not_executed
      : (c?.delete ?? 0);
  const kinds = kindsNote(summary);
  return [
    ...(listedDeletes > 0 ? [{ id: "deletes", node: deletesListed(listedDeletes) }] : []),
    ...(c?.deletesComputed === false ? [{ id: "not-read", node: NOT_READ_IN_FULL }] : []),
    ...(kinds ? [{ id: "kinds", node: kinds }] : []),
  ];
}

/** The latest run of a configuration, as the stages of the pipeline and one footer line. */
export function pipelineModel(run: Run | undefined, ctx: PipelineContext): PipelineModel {
  const { latestVersion } = ctx;
  const inFlight = ctx.inFlight?.purpose;
  if (!run) {
    return {
      state: "idle",
      lead: "Never planned",
      clauses: [{ id: "at", node: <When iso={ctx.createdAt} prefix="Registered " /> }],
      stages: [
        stage("plan", "idle", { value: "No plan yet" }),
        stage("verify", "idle"),
        stage("apply", "idle"),
      ],
      live: live("Never planned", "No plan yet"),
      action: { label: "Create the first plan", kind: "plan" },
    };
  }

  const state = runState(run, { latestVersion, inFlight });
  const ended = run.finished_at ?? run.started_at;
  const c = runCounts(run);
  const written = c ? writtenCount(c) : 0;
  const planValue = planWords(c);
  const syncOp = run.operation === "sync";
  const verified = isVerified(run);
  const failedAt: Clause = { id: "at", node: <When iso={ended} prefix="Failed " /> };
  const idle = (value?: string): Stage[] =>
    syncOp
      ? [stage("sync", "idle", { value })]
      : [stage("plan", "idle", { value }), stage("verify", "idle"), stage("apply", "idle")];

  if (state === "running") {
    const lead = `${phaseLabel(run, { inFlight })}…`;
    const planDone = stage("plan", "done", { value: planValue, notes: planNotes(run) });
    const stages = syncOp
      ? [stage("sync", "active")]
      : inFlight === "verify"
        ? [planDone, stage("verify", "active"), stage("apply", "idle")]
        : inFlight === "apply"
          ? [
              planDone,
              verified
                ? stage("verify", "done", { word: "Verified" })
                : stage("verify", "optional"),
              stage("apply", "active"),
            ]
          : [stage("plan", "active"), stage("verify", "idle"), stage("apply", "idle")];
    // The run's start time and actor belong to its plan; a verify or apply has its own submission time.
    const step = inFlight === "verify" || inFlight === "apply" ? ctx.inFlight : undefined;
    const stepAt = step?.submitted_at ?? step?.claimed_at;
    return {
      state,
      lead,
      clauses: step
        ? stepAt
          ? [{ id: "at", node: <When iso={stepAt} prefix="Started " /> }]
          : []
        : [
            {
              id: "at",
              node: (
                <>
                  <When iso={run.started_at} prefix="Started " />
                  {run.actor && ` by ${run.actor}`}
                </>
              ),
            },
          ],
      stages,
      live: lead,
      action: { label: "View run", kind: "run" },
    };
  }

  if (syncOp && (state === "failed" || run.phase === "applied")) {
    const note = { id: "sync", node: "Plans and applies in one run, without review" };
    if (state === "failed") {
      const f = failureDetail(run);
      const lead = failedLead("Sync", run, f);
      return {
        state,
        lead,
        clauses: [failedAt],
        stages: [stage("sync", "failed", { value: failValue(f), notes: [note] })],
        live: live(lead, failText(f)),
        action: { label: f?.ambiguous ? "View run" : "View failure", kind: "run" },
      };
    }
    const value = appliedWords(written);
    return {
      state,
      lead: "Synced",
      clauses: [{ id: "at", node: <When iso={ended} prefix="Synced " /> }],
      stages: [
        stage("sync", "done", {
          value,
          notes: [
            ...(c && c.delete > 0 ? [{ id: "deletes", node: deletesListed(c.delete) }] : []),
            note,
          ],
        }),
      ],
      live: live("Synced", value),
      action: { label: "View run", kind: "run" },
    };
  }

  if (state === "failed") {
    const f = failureDetail(run);
    const stageName = run.phase.split("-")[0];
    if (stageName === "plan") {
      const lead = phaseLabel(run);
      return {
        state,
        lead,
        clauses: [failedAt],
        stages: [
          stage("plan", "failed", { value: failValue(f) }),
          stage("verify", "idle"),
          stage("apply", "idle"),
        ],
        live: live(lead, failText(f)),
        action: { label: "View failure", kind: "run" },
      };
    }
    if (run.results?.verify_failure) {
      return {
        state,
        lead: "Verify failed",
        clauses: [failedAt],
        stages: [
          stage("plan", "done", { value: planValue }),
          stage("verify", "failed", { value: failValue(f) }),
          stage("apply", "idle"),
        ],
        live: live("Verify failed", failText(f)),
        action: { label: "View failure", kind: "run" },
      };
    }
    const lead = failedLead("Apply", run, f);
    return {
      state,
      lead,
      clauses: f?.ambiguous
        ? [{ id: "next", node: "Inspect the destination, then plan again" }, failedAt]
        : [failedAt],
      stages: [
        stage("plan", "done", { value: planValue }),
        verified ? stage("verify", "done", { word: "Verified" }) : stage("verify", "skipped"),
        stage("apply", "failed", { value: failValue(f) }),
      ],
      live: live(lead, failText(f)),
      action: { label: f?.ambiguous ? "View run" : "View failure", kind: "run" },
    };
  }

  if (isOutdated(run, latestVersion)) {
    const lead = `Version ${latestVersion} not planned yet`;
    return {
      state,
      lead,
      clauses: [
        {
          id: "planned",
          node: (
            <When iso={run.started_at} prefix={`Version ${run.registry_version} last planned `} />
          ),
        },
        ...lastAppliedClause(run, ctx),
      ],
      stages: idle(lead),
      live: lead,
      action: { label: `Plan version ${latestVersion}`, kind: "plan" },
    };
  }

  if (run.phase === "abandoned" || run.phase === "cancelled") {
    const lead = run.phase === "abandoned" ? "Latest plan abandoned" : "Latest run cancelled";
    return {
      state,
      lead,
      clauses: [
        ...(run.phase === "abandoned" ? abandonedClause(ctx.latestRunDetail) : []),
        {
          id: "at",
          node: (
            <When iso={ended} prefix={run.phase === "abandoned" ? "Abandoned " : "Cancelled "} />
          ),
        },
        ...lastAppliedClause(run, ctx),
      ],
      stages: idle(),
      live: lead,
      action: { label: "Plan again", kind: "plan" },
    };
  }

  if (state === "review") {
    const deleteOnly = isDeleteOnlyPlan(run);
    const lead = phaseLabel(run);
    return {
      state,
      lead,
      clauses: [
        {
          id: "planned",
          node: (
            <>
              <When iso={ended} prefix="Planned " />
              {run.actor && ` by ${run.actor}`}
            </>
          ),
        },
        { id: "version", node: `Version ${run.registry_version}` },
        ...lastAppliedClause(run, ctx),
      ],
      stages: [
        stage("plan", "done", { value: planValue, notes: planNotes(run) }),
        verified
          ? stage("verify", "done", { word: "Verified" })
          : stage("verify", "optional", {
              // Apply never runs deletes, so for a plan that lists only deletes no apply follows.
              notes: deleteOnly
                ? undefined
                : [{ id: "checks", node: "Apply runs the same checks before it writes" }],
            }),
        deleteOnly ? stage("apply", "nothing") : stage("apply", "waiting"),
      ],
      live: live(lead, planValue),
      action: deleteOnly
        ? { label: "View plan", kind: "run" }
        : { label: "Review plan", kind: "review" },
    };
  }

  if (run.phase === "planned" && run.outcome === "no-change") {
    const source = ctx.sourceLabel ?? "the source";
    const destination = ctx.destinationLabel ?? "The destination";
    return {
      state,
      lead: "No differences",
      clauses: [
        // Without a full read of the destination, nodes only in the destination were not compared.
        ...(c?.deletesComputed === false
          ? []
          : [
              {
                id: "matched",
                node: (
                  <>
                    {destination} matched {source} for the mapped kinds
                    {run.destination_branch && (
                      <>
                        {" "}
                        on <BranchBadge branch={run.destination_branch} />
                      </>
                    )}
                  </>
                ),
              },
            ]),
        { id: "at", node: <When iso={ended} prefix="Checked " /> },
        ...lastAppliedClause(run, ctx),
      ],
      stages: [
        stage("plan", "done", {
          value: "No differences",
          notes:
            c?.deletesComputed === false ? [{ id: "not-read", node: NOT_READ_IN_FULL }] : undefined,
        }),
        stage("verify", "nothing", { word: "Nothing to verify" }),
        stage("apply", "nothing"),
      ],
      live: live("No differences"),
      action: { label: "View run", kind: "run" },
    };
  }

  if (run.phase === "applied") {
    const value = appliedWords(written);
    return {
      state,
      lead: "Applied",
      clauses: [
        { id: "at", node: <When iso={ended} prefix="Applied " /> },
        { id: "version", node: `Version ${run.registry_version}` },
        ...lastAppliedClause(run, ctx),
      ],
      stages: [
        stage("plan", "done"),
        stage("verify", "done", { word: verified ? "Verified" : "Checked by apply" }),
        stage("apply", "done", {
          value,
          notes: c && c.delete > 0 ? [{ id: "deletes", node: deletesListed(c.delete) }] : undefined,
        }),
      ],
      live: live("Applied", value),
      action: { label: "View run", kind: "run" },
    };
  }

  const lead = phaseLabel(run, { inFlight });
  return {
    state,
    lead,
    clauses: [{ id: "at", node: <When iso={ended} /> }],
    stages: idle(),
    live: lead,
    action: { label: "View run", kind: "run" },
  };
}

/* ---------- view ---------- */

const CONNECTOR =
  "not-last:before:absolute not-last:before:top-5 not-last:before:-bottom-3 not-last:before:left-[5.5px] not-last:before:w-px not-last:before:bg-border-strong not-last:before:content-[''] lg:not-last:before:hidden lg:not-last:after:absolute lg:not-last:after:top-[5.5px] lg:not-last:after:left-5 lg:not-last:after:-right-4 lg:not-last:after:h-px lg:not-last:after:bg-border-strong lg:not-last:after:content-['']";

const ITEM = cn("relative grid grid-cols-[12px_minmax(0,1fr)] gap-x-3 lg:block", CONNECTOR);

const MARKER =
  "relative z-10 mt-0.5 flex size-3 items-center justify-center bg-card lg:mt-0 lg:mb-2";

const markerNode = (state: StageState) =>
  state === "optional" || state === "nothing" ? NODE.idle : NODE[state];

const isFocus = (s: StageState) => s === "failed" || s === "waiting" || s === "active";

function SystemStage({
  name,
  system,
  pending,
  missing,
  branchLine = false,
  children,
}: {
  name: string;
  system?: System;
  pending: boolean;
  missing: string;
  /** Reserve the branch line while the version loads. */
  branchLine?: boolean;
  children?: ReactNode;
}) {
  return (
    <li className={ITEM}>
      <span className={MARKER} aria-hidden="true">
        <span className="size-2 rounded-[2px] bg-subtle-muted" />
      </span>
      <div className="min-w-0">
        <p className="font-medium text-foreground text-xs">{name}</p>
        {pending ? (
          <>
            <Bar className="mt-1 h-4 w-24" />
            <Bar className="mt-1 h-3 w-32" />
            {branchLine && <Bar className="mt-1 h-3 w-40 max-w-full" />}
          </>
        ) : !system ? (
          <p className="mt-1 text-pretty text-subtle text-xs">{missing}</p>
        ) : (
          <>
            <p
              className="mt-1 truncate font-medium text-[13px] text-foreground"
              title={system.label}
            >
              {system.label}
            </p>
            {system.host && (
              <p className="truncate font-mono text-subtle text-xs" title={system.host}>
                {system.host}
              </p>
            )}
            {children}
          </>
        )}
      </div>
    </li>
  );
}

function BranchLine({ label, branch }: { label: string; branch: string }) {
  return (
    <p className="mt-0.5 text-subtle text-xs">
      {label} <BranchBadge branch={branch} title={branch} truncate className="align-bottom" />
    </p>
  );
}

const GRID =
  "grid grid-cols-1 gap-y-4 p-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1.4fr)_minmax(0,1fr)_minmax(0,1fr)] lg:gap-x-6 lg:gap-y-0";

const FOOTER =
  "flex flex-col gap-3 border-t px-4 py-3 sm:flex-row sm:items-center sm:justify-between sm:gap-6";

/**
 * A dot sits in the gap before each clause. The list is clipped a few pixels to the left of its
 * items, so the dot of a clause that starts a wrapped line is hidden.
 */
const CLAUSES =
  "-ml-1 flex flex-wrap gap-x-4 overflow-x-clip pl-1 text-pretty text-subtle text-xs [&>li]:relative [&>li]:before:absolute [&>li]:before:top-1/2 [&>li]:before:-left-[9.5px] [&>li]:before:size-[3px] [&>li]:before:-translate-y-1/2 [&>li]:before:rounded-full [&>li]:before:bg-subtle-muted [&>li]:before:content-['']";

/** The pipeline card while the latest run loads, laid out like the loaded card so the page does not move. */
export function PipelineSkeleton() {
  return (
    <Card className="overflow-hidden" aria-hidden="true">
      <ol className={GRID}>
        {[0, 1, 2, 3, 4].map((i) => (
          <li key={i} className={ITEM}>
            <span className={MARKER}>
              {i < 2 ? (
                <span className="size-2 rounded-[2px] bg-subtle-muted" />
              ) : (
                <span className={cn("block rounded-full", NODE.idle)} />
              )}
            </span>
            <div className="min-w-0">
              <Bar className="h-3 w-14" />
              <Bar className="mt-1.5 h-4 w-24 max-w-full" />
              <Bar className="mt-1 h-3 w-32 max-w-full" />
            </div>
          </li>
        ))}
      </ol>
      <div className={FOOTER}>
        <div className="flex min-w-0 flex-col gap-1.5">
          <Bar className="h-4 w-28" />
          <Bar className="h-3 w-56 max-w-full" />
        </div>
        <Bar className="h-8 w-28 rounded-lg" />
      </div>
    </Card>
  );
}

/** The latest run as Source, Destination and its stages, with the state and the next action under them. */
export function Pipeline({
  model,
  run,
  source,
  destination,
  declaredBranch,
  versionPending,
  systemsMissing,
  onStart,
}: {
  model: PipelineModel;
  run?: Run;
  source?: System;
  destination?: System;
  declaredBranch?: string;
  versionPending: boolean;
  /** Why the systems are not shown when the version is not loaded. */
  systemsMissing: string;
  /** Undefined when no version is registered, so no plan can start. */
  onStart?: () => void;
}) {
  const focus = model.stages.findIndex((s) => isFocus(s.state));
  const action = model.action;
  return (
    <Card className="overflow-hidden">
      <ol aria-label="Latest run, from source to apply" className={GRID}>
        <SystemStage
          name="Source"
          system={source}
          pending={versionPending}
          missing={systemsMissing}
        />
        <SystemStage
          name="Destination"
          system={destination}
          pending={versionPending}
          missing={systemsMissing}
          branchLine
        >
          {run?.destination_branch ? (
            <BranchLine label="Latest run branch" branch={run.destination_branch} />
          ) : declaredBranch ? (
            <BranchLine label="Declared branch" branch={declaredBranch} />
          ) : null}
        </SystemStage>
        {model.stages.map((s, i) => (
          <li key={s.key} className={cn(ITEM, s.key === "sync" && "lg:col-span-3")}>
            <span className={MARKER} aria-hidden="true">
              <span className={cn("block rounded-full", markerNode(s.state))} />
            </span>
            <div className="min-w-0">
              <p className="font-medium text-foreground text-xs" aria-hidden="true">
                {NAME[s.key]}
              </p>
              <p
                className={cn(
                  "text-xs",
                  i === focus ? ["font-medium", STEP_TONE[s.state as StepState]] : "text-subtle"
                )}
              >
                <span className="sr-only">{NAME[s.key]}: </span>
                {s.word}
              </p>
              {s.value && <p className="mt-1 text-pretty text-[13px] text-foreground">{s.value}</p>}
              {s.notes?.map((n) => (
                <p key={n.id} className="text-pretty text-subtle text-xs">
                  {n.node}
                </p>
              ))}
            </div>
          </li>
        ))}
      </ol>
      <div className={FOOTER}>
        <div className="flex min-w-0 flex-col gap-0.5">
          <p className="flex flex-wrap items-center gap-x-2">
            <StateLabel state={model.state} label={model.lead} />
          </p>
          {model.clauses.length > 0 && (
            <ul className={CLAUSES}>
              {model.clauses.map((c) => (
                <li key={c.id}>{c.node}</li>
              ))}
            </ul>
          )}
        </div>
        {action && (
          <div className="sm:shrink-0">
            {action.kind === "review" && run && (
              <Link
                to="/runs/$runId"
                params={{ runId: run.run_id }}
                className={buttonVariants({ variant: "primary", size: "sm" })}
              >
                {action.label}
              </Link>
            )}
            {action.kind === "run" && run && (
              <Link to="/runs/$runId" params={{ runId: run.run_id }} className={textAction()}>
                {action.label}
                <ChevronRightIcon />
              </Link>
            )}
            {action.kind === "plan" && onStart && (
              <button type="button" onClick={onStart} className={textAction()}>
                {action.label}
                <ChevronRightIcon />
              </button>
            )}
          </div>
        )}
      </div>
      <p className="sr-only" aria-live="polite" aria-atomic="true">
        {model.live}
      </p>
    </Card>
  );
}
