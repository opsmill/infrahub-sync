import { useQueries, useQuery } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import {
  configsQuery,
  configVersionQuery,
  failedFirstRead,
  isInProgress,
  LIST_REFRESH_MS,
  latestRunByConfig,
  latestRunQuery,
  runQuery,
  runsQuery,
} from "#/api/queries";
import type { ConfigurationSummary, ConfigurationVersion, Run, RunResource } from "#/api/types";
import {
  branchPrefill,
  byAttention,
  type Explanation,
  explain,
  failureDetail,
  isOutdated,
  type RunState,
  runState,
  runSteps,
  type StepState,
} from "#/domain/run-state";
import { describeSystem, mappedKinds, type System } from "#/domain/systems";

/** A run is re-read this often while one of its executions has not finished. */
const ACTIVE_REFRESH_MS = 2_000;

export interface ConfigurationRow {
  summary: ConfigurationSummary;
  /** `summary.latest_version`; undefined when the Sync API does not return it. */
  latestVersion?: number;
  /** The newest run of the configuration, of any version. */
  run?: Run;
  /** The newest applied run on the first page of `GET /runs`. */
  lastApplied?: Run;
  /** The purpose of an execution of `run` that has not finished, such as "apply". */
  inFlight?: string;
  /** When the unfinished execution of `run` was submitted, while no worker has picked it up. */
  queuedAt?: string;
  /** True when a run on the first page of `GET /runs` used `latestVersion`. */
  latestVersionRun: boolean;
  /** The latest version's package; undefined while it loads or when it failed. */
  version?: ConfigurationVersion;
  /** The last read of the package failed; stays true while it is read again. */
  versionError: boolean;
  source?: System;
  destination?: System;
  kinds: string[];
  /** Undefined while the latest run is not known; `runUnknown` says why. */
  state?: RunState;
  /** The `GET /runs?config_id=` read of a configuration missing from the first page is pending or failed. */
  runUnknown?: "pending" | "failed";
  /** The branch a new run starts on, from the newest planned or applied run. */
  prefill?: { branch: string; runId: string };
}

export const configName = (row: Pick<ConfigurationRow, "summary">) =>
  row.summary.name ?? row.summary.config_id;

/** The branch shown on the row: the latest run's branch, else the declared one. */
export function defaultBranch(
  row: Pick<ConfigurationRow, "run" | "destination">
): string | undefined {
  return row.run?.destination_branch ?? row.destination?.branch;
}

/** True when the systems line names the systems, so their labels and the branch can be seen. */
const systemsShown = (row: ConfigurationRow) =>
  row.latestVersion !== undefined && !row.versionError && Boolean(row.source && row.destination);

const isWaitingPlan = (run: Run) => run.phase === "planned" && run.outcome === "planned";

const openExecution = (resource: RunResource | undefined) =>
  resource?.orchestration.find((o) => o.terminal_at == null);

type Read = { data?: unknown; isPending: boolean; errorUpdateCount: number; error: unknown };
/** A read with no data that has failed at least once, including while it is read again. */
const hasFailed = (q: Read | undefined) =>
  Boolean(q && q.data === undefined && (q.error || (q.isPending && q.errorUpdateCount > 0)));
const firstReadPending = (q: Read) => q.isPending && q.errorUpdateCount === 0;
/** Reads that do not change once they succeed are read again only after a failure. */
const retryAfterFailure = (q: { state: { status: string } }) =>
  q.state.status === "error" ? LIST_REFRESH_MS : false;

export function useConfigurationRows() {
  // A reviewed apply keeps the run "planned" until it ends, so the list polls faster while one runs.
  const fast = useRef(false);
  const configs = useQuery(configsQuery());
  const runs = useQuery({
    ...runsQuery(),
    refetchInterval: (q) =>
      failedFirstRead(q)
        ? false
        : fast.current || q.state.data?.items.some((r) => r.phase === "accepted")
          ? ACTIVE_REFRESH_MS
          : LIST_REFRESH_MS,
  });
  const items = configs.data?.items ?? [];
  const runItems = runs.data?.items ?? [];
  const runsTruncated = Boolean(runs.data?.next_cursor);

  const versioned = items.flatMap((c) =>
    c.latest_version != null ? [{ configId: c.config_id, version: c.latest_version }] : []
  );
  const versionResults = useQueries({
    queries: versioned.map((v) => ({
      ...configVersionQuery(v.configId, v.version),
      refetchInterval: retryAfterFailure,
    })),
  });
  const versions = new Map(versioned.map((v, i) => [v.configId, versionResults[i]]));

  const latest = latestRunByConfig(runItems);
  const missing = runsTruncated ? items.filter((c) => !latest.has(c.config_id)) : [];
  // A new run of these configurations appears on the first page of `GET /runs`, so one answer is enough.
  const older = useQueries({
    queries: missing.map((c) => ({
      ...latestRunQuery(c.config_id),
      refetchInterval: retryAfterFailure,
    })),
  });
  const unknown = new Map<string, "pending" | "failed">();
  missing.forEach((c, i) => {
    const q = older[i];
    const run = q?.data?.items[0];
    if (run) latest.set(c.config_id, run);
    else if (hasFailed(q)) unknown.set(c.config_id, "failed");
    else if (q?.isPending) unknown.set(c.config_id, "pending");
  });

  const watched = [...latest.values()].filter((r) => r.phase === "accepted" || isWaitingPlan(r));
  const resources = useQueries({
    queries: watched.map((r) => ({
      ...runQuery(r.run_id),
      refetchInterval: (q: { state: { data?: unknown; status: string } }) =>
        failedFirstRead(q)
          ? false
          : isInProgress(q.state.data as RunResource | undefined)
            ? ACTIVE_REFRESH_MS
            : LIST_REFRESH_MS,
    })),
  });
  const open = new Map<string, { purpose: string; queuedAt?: string }>();
  watched.forEach((r, i) => {
    const execution = openExecution(resources[i]?.data as RunResource | undefined);
    if (!execution) return;
    open.set(r.run_id, {
      purpose: execution.purpose,
      queuedAt: execution.claimed_at == null ? (execution.submitted_at ?? r.started_at) : undefined,
    });
  });
  const anyInFlight = open.size > 0;
  useEffect(() => {
    fast.current = anyInFlight;
  }, [anyInFlight]);

  const rows: ConfigurationRow[] = items.map((summary) => {
    const latestVersion = summary.latest_version ?? undefined;
    const run = latest.get(summary.config_id);
    const execution = run ? open.get(run.run_id) : undefined;
    // An accepted run is running whatever its execution does; `inFlight` names work on a plan.
    const inFlight = run && isWaitingPlan(run) ? execution?.purpose : undefined;
    const versionResult = versions.get(summary.config_id);
    const version = versionResult?.data;
    const cfg = version?.declared_content.configuration;
    const runUnknown = unknown.get(summary.config_id);
    return {
      summary,
      latestVersion,
      run,
      lastApplied: runItems.find((r) => r.config_id === summary.config_id && r.phase === "applied"),
      inFlight,
      queuedAt: execution?.queuedAt,
      latestVersionRun:
        latestVersion !== undefined &&
        runItems.some(
          (r) => r.config_id === summary.config_id && r.registry_version === latestVersion
        ),
      version,
      versionError: hasFailed(versionResult),
      source: cfg ? describeSystem(cfg.source) : undefined,
      destination: cfg ? describeSystem(cfg.destination) : undefined,
      kinds: mappedKinds(cfg?.schema_mapping),
      state: runUnknown ? undefined : runState(run, { latestVersion, inFlight }),
      runUnknown,
      prefill: branchPrefill(run ? [run, ...runItems] : runItems, summary.config_id),
    };
  });

  const hasData = Boolean(configs.data && runs.data);
  // The page waits for the latest runs of configurations missing from the first page on its
  // first load only, so a configuration registered later does not bring the skeleton back.
  const olderPending = older.some(firstReadPending);
  const loadedOnce = useRef(false);
  const settled = hasData && !olderPending && !resources.some(firstReadPending);
  useEffect(() => {
    if (hasData && !olderPending) loadedOnce.current = true;
  }, [hasData, olderPending]);
  return {
    rows,
    isPending:
      configs.isPending || runs.isPending || (hasData && !loadedOnce.current && olderPending),
    /** A failed request with nothing loaded yet. */
    error: (configs.data ? null : configs.error) ?? (runs.data ? null : runs.error),
    /** A failed refresh of a list that is still on screen. */
    refreshError: (configs.data && configs.error) || (runs.data && runs.error) || null,
    /** The time of the older of the two last successful responses. */
    updatedAt: Math.min(configs.dataUpdatedAt, runs.dataUpdatedAt),
    /** Every read that sets a row's state has answered once. */
    settled,
    failureCount: Math.max(configs.failureCount, runs.failureCount),
    failureReason: configs.failureReason ?? runs.failureReason,
    /** The newest runs of every configuration, as the first page of `GET /runs` returns them. */
    runs: runItems,
    /** Older runs exist than the first page holds; "Never applied" cannot be stated then. */
    runsTruncated,
    /** More configurations exist than the first page holds. */
    configsTruncated: Boolean(configs.data?.next_cursor),
    refetch: () => {
      void configs.refetch();
      void runs.refetch();
      for (const q of [...versionResults, ...older]) if (q.error) void q.refetch();
    },
  };
}

export function rowSteps(row: ConfigurationRow): StepState[] {
  const steps = runSteps(row.run, { latestVersion: row.latestVersion, inFlight: row.inFlight });
  return row.queuedAt ? steps.map((s) => (s === "active" ? "idle" : s)) : steps;
}

export function rowExplanation(row: ConfigurationRow): Explanation {
  const { run, latestVersion } = row;
  const e = explain(run, {
    latestVersion,
    inFlight: row.inFlight,
    createdAt: row.summary.created_at,
    lastApplied: row.lastApplied,
    source: row.source,
    destination: row.destination,
  });
  if (run && row.queuedAt && row.state === "running") {
    return { ...e, short: "Waiting for a worker", at: { iso: row.queuedAt, prefix: "Queued " } };
  }
  // The newest version was planned before, and a run of an older version came after it.
  if (run && row.latestVersionRun && row.state === "idle" && isOutdated(run, latestVersion)) {
    return {
      ...e,
      short: `Latest run used version ${run.registry_version}; version ${latestVersion} is the newest`,
    };
  }
  return e;
}

/** Why a row matched a search, when the matching text is not on the row. */
export type MatchHint = { field: "id" | "branch" | "host" | "kind"; value: string };

/** The best match: equal to the query, then starting with it, then containing it. */
function bestMatch(values: (string | null | undefined)[], needle: string): string | undefined {
  const present = values.filter((v): v is string => Boolean(v));
  const lower = (v: string) => v.toLowerCase();
  return (
    present.find((v) => lower(v) === needle) ??
    present.find((v) => lower(v).startsWith(needle)) ??
    present.find((v) => lower(v).includes(needle))
  );
}

/** Null when the row does not match; otherwise a hint when only a field the row hides matched. */
export function matches(row: ConfigurationRow, query: string): { hint?: MatchHint } | null {
  const needle = query.trim().toLowerCase();
  if (!needle) return {};
  const has = (v: string | null | undefined) => v?.toLowerCase().includes(needle) ?? false;
  const systems = systemsShown(row);
  const branch = defaultBranch(row);
  const branchShown = systems && branch !== undefined && branch !== "main";
  const shown = [
    configName(row),
    systems && row.source?.label,
    systems && row.destination?.label,
    branchShown && branch,
  ];
  if (shown.some((v) => typeof v === "string" && has(v))) return {};
  if (has(row.summary.config_id)) return { hint: { field: "id", value: row.summary.config_id } };
  if (branch && has(branch)) return { hint: { field: "branch", value: branch } };
  const host = bestMatch([row.source?.host, row.destination?.host], needle);
  if (host) return { hint: { field: "host", value: host } };
  const kind = bestMatch(row.kinds, needle);
  if (kind) return { hint: { field: "kind", value: kind } };
  return null;
}

const time = (iso: string) => new Date(iso).getTime();

/** True when the run may have written to the destination although it did not finish. */
export function mayHaveWritten(run?: Run): boolean {
  if (!run) return false;
  if (run.reconciliation_required || run.outcome === "ambiguous") return true;
  const failure = failureDetail(run);
  if (!failure) return false;
  const partial = Object.values(run.results ?? {}).some(
    (r) =>
      typeof r === "object" &&
      r !== null &&
      (r as { may_have_partially_written?: unknown }).may_have_partially_written === true
  );
  return partial || (failure.appliedCount ?? 0) > 0;
}

/** A write that may have reached the destination first, then failures before plans, then newest. */
export function byUrgency(a: ConfigurationRow, b: ConfigurationRow): number {
  const written = Number(mayHaveWritten(b.run)) - Number(mayHaveWritten(a.run));
  if (written) return written;
  if (a.state && b.state) {
    const state = byAttention(a.state, b.state);
    if (state) return state;
  }
  if (a.run && b.run)
    return (
      time(b.run.finished_at ?? b.run.started_at) - time(a.run.finished_at ?? a.run.started_at)
    );
  if (a.run || b.run) return a.run ? -1 : 1;
  return time(b.summary.created_at) - time(a.summary.created_at);
}
