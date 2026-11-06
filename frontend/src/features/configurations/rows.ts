import { useQueries, useQuery } from "@tanstack/react-query";

import { configsQuery, configVersionQuery, latestRunByConfig, runsQuery } from "#/api/queries";
import type { ConfigurationSummary, ConfigurationVersion, Run } from "#/api/types";
import { explain, type RunState, runState, runSteps } from "#/domain/run-state";
import { describeSystem, mappedKinds, type System } from "#/domain/systems";

export interface ConfigurationRow {
  summary: ConfigurationSummary;
  latestVersion: number;
  /** The newest run on the first page of `GET /runs`, of any version. */
  run?: Run;
  /** The newest applied run on that page. */
  lastApplied?: Run;
  /** The latest version's package; undefined while it loads or when it failed. */
  version?: ConfigurationVersion;
  versionError: boolean;
  source?: System;
  destination?: System;
  kinds: string[];
  state: RunState;
}

/** The branch a new run should default to: the last run's branch, else the declared one. */
export function defaultBranch(
  row: Pick<ConfigurationRow, "run" | "destination">
): string | undefined {
  return row.run?.destination_branch ?? row.destination?.branch;
}

export function useConfigurationRows() {
  const configs = useQuery(configsQuery());
  const runs = useQuery(runsQuery());
  const items = configs.data?.items ?? [];
  const versions = useQueries({
    queries: items.map((c) => configVersionQuery(c.config_id, c.latest_version ?? 1)),
  });

  const runItems = runs.data?.items ?? [];
  const latest = latestRunByConfig(runItems);
  const rows: ConfigurationRow[] = items.map((summary, i) => {
    const latestVersion = summary.latest_version ?? 1;
    const run = latest.get(summary.config_id);
    const version = versions[i]?.data;
    const cfg = version?.declared_content.configuration;
    return {
      summary,
      latestVersion,
      run,
      lastApplied: runItems.find((r) => r.config_id === summary.config_id && r.phase === "applied"),
      version,
      versionError: Boolean(versions[i]?.error),
      source: cfg ? describeSystem(cfg.source) : undefined,
      destination: cfg ? describeSystem(cfg.destination) : undefined,
      kinds: mappedKinds(cfg?.schema_mapping),
      state: runState(run, { latestVersion }),
    };
  });

  return {
    rows,
    isPending: configs.isPending || runs.isPending,
    error: configs.error ?? runs.error,
    /** True when more runs exist than one page holds; older configurations may then look never run. */
    runsTruncated: Boolean(runs.data?.next_cursor),
    refetch: () => {
      configs.refetch();
      runs.refetch();
    },
  };
}

export const rowSteps = (row: ConfigurationRow) =>
  runSteps(row.run, { latestVersion: row.latestVersion });

export const rowExplanation = (row: ConfigurationRow) =>
  explain(row.run, {
    latestVersion: row.latestVersion,
    createdAt: row.summary.created_at,
    lastApplied: row.lastApplied,
  });

export function matches(row: ConfigurationRow, query: string): boolean {
  const needle = query.trim().toLowerCase();
  if (!needle) return true;
  return [
    row.summary.name,
    row.summary.config_id,
    row.source?.label,
    row.source?.host,
    row.destination?.label,
    row.run?.destination_branch,
    row.run?.actor,
  ].some((v) => v?.toLowerCase().includes(needle));
}
