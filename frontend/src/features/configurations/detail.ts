import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import {
  configQuery,
  configVersionQuery,
  failedFirstRead,
  isInProgress,
  runQuery,
  runsQuery,
  versionsQuery,
} from "#/api/queries";
import { diffMappings } from "#/domain/mapping";
import { branchPrefill, listRunStates } from "#/domain/run-state";
import { describeSystem, mappedKinds } from "#/domain/systems";

const RUNS_ACTIVE_MS = 2_000;
const RUNS_IDLE_MS = 10_000;

/** The larger of the defined values: the configuration and its versions list can disagree for one refresh. */
const maxDefined = (...values: (number | null | undefined)[]) => {
  const defined = values.filter((v): v is number => typeof v === "number");
  return defined.length ? Math.max(...defined) : undefined;
};

/**
 * Everything the page of one version of a configuration shows. The version is the latest unless
 * `requestedVersion` is set; its mapping is compared with `requestedCompare` when that is an earlier
 * version.
 */
export function useConfigurationDetail(
  configId: string,
  requestedVersion?: number,
  requestedCompare?: number
) {
  const config = useQuery(configQuery(configId));
  const versions = useQuery(versionsQuery(configId));
  const numbers = versions.data?.items.map((v) => v.registry_version) ?? [];
  const latest = maxDefined(config.data?.latest_version, ...numbers);
  const selected = requestedVersion ?? latest;
  const compareWith =
    requestedCompare !== undefined && selected !== undefined && requestedCompare < selected
      ? requestedCompare
      : undefined;

  const version = useQuery({
    ...configVersionQuery(configId, selected ?? 0),
    enabled: selected !== undefined,
  });
  const base = useQuery({
    ...configVersionQuery(configId, compareWith ?? 0),
    enabled: compareWith !== undefined,
  });
  const runs = useQuery({
    ...runsQuery({ config_id: configId, registry_version: selected }),
    enabled: selected !== undefined,
    refetchInterval: (q) =>
      failedFirstRead(q)
        ? false
        : q.state.data?.items.some((r) => r.phase === "accepted")
          ? RUNS_ACTIVE_MS
          : RUNS_IDLE_MS,
  });
  // The branch a new run is offered comes from the latest runs of the configuration, of any version.
  const configRuns = useQuery(runsQuery({ config_id: configId }));

  const runItems = runs.data?.items ?? [];
  const newestRun = runItems[0];
  const readDetail = newestRun?.phase === "planned" || newestRun?.phase === "abandoned";
  const newestRunDetail = useQuery({
    ...runQuery(newestRun?.run_id ?? ""),
    enabled: readDetail,
    // A verify or apply started elsewhere leaves the runs list at phase "planned"; only this record shows it.
    refetchInterval: (q) =>
      failedFirstRead(q)
        ? false
        : isInProgress(q.state.data)
          ? RUNS_ACTIVE_MS
          : newestRun?.phase === "planned"
            ? RUNS_IDLE_MS
            : false,
  });
  const inFlight = readDetail
    ? newestRunDetail.data?.orchestration.find((o) => o.terminal_at == null)?.purpose
    : undefined;
  const lastInFlight = useRef(inFlight);
  const refetchRuns = runs.refetch;
  useEffect(() => {
    // The runs list keeps the run's earlier phase until it is read again after a verify or apply ends.
    if (lastInFlight.current && !inFlight) void refetchRuns();
    lastInFlight.current = inFlight;
  }, [inFlight, refetchRuns]);

  const detailRun = newestRunDetail.data?.run;
  const currentRun =
    newestRun &&
    detailRun?.run_id === newestRun.run_id &&
    newestRunDetail.dataUpdatedAt > runs.dataUpdatedAt
      ? detailRun
      : newestRun;
  const shownRuns =
    currentRun && currentRun !== newestRun ? [currentRun, ...runItems.slice(1)] : runItems;

  const declared = version.data?.declared_content.configuration;
  const mappings = declared?.schema_mapping ?? [];
  const baseMappings = base.data?.declared_content.configuration.schema_mapping;

  return {
    configId,
    config,
    versions,
    version,
    base,
    runs,
    name: config.data?.name ?? configId,
    latest,
    selected,
    isLatest: selected !== undefined && selected === latest,
    /** The versions list's entry of the selected version. */
    summary: versions.data?.items.find((v) => v.registry_version === selected),
    summaries: versions.data?.items ?? [],
    numbers,
    mappings,
    kinds: mappedKinds(mappings),
    source: declared ? describeSystem(declared.source) : undefined,
    destination: declared ? describeSystem(declared.destination) : undefined,
    compareWith,
    baseMappings,
    diff:
      compareWith !== undefined && baseMappings && declared
        ? diffMappings(compareWith, baseMappings, mappings)
        : undefined,
    /** The runs of the selected version, newest first, the newest one as its own record reads. */
    runItems: shownRuns,
    currentRun,
    inFlight,
    // Within one version, the newest run is the one that describes the version.
    runStates: listRunStates(shownRuns, {
      latestVersions: selected !== undefined ? new Map([[configId, selected]]) : new Map(),
      inFlight: currentRun && inFlight ? new Map([[currentRun.run_id, inFlight]]) : undefined,
    }),
    prefill: branchPrefill(configRuns.data?.items ?? [], configId),
  };
}

export type ConfigurationDetail = ReturnType<typeof useConfigurationDetail>;
