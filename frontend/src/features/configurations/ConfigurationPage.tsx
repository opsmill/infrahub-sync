import { Button, buttonVariants } from "@infrahub/ui";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { type ReactNode, useEffect, useRef, useState } from "react";
import { cn } from "tailwind-variants";

import {
  configQuery,
  configVersionQuery,
  describeError,
  failedFirstRead,
  isInProgress,
  runQuery,
  runsQuery,
  versionsQuery,
} from "#/api/queries";
import type { VersionSummary } from "#/api/types";
import { ArrowRightIcon, ChevronRightIcon, PlusIcon, StateIcon } from "#/components/icons";
import {
  Bar,
  CODE_CHIP,
  COUNT_BADGE,
  CopyButton,
  CRUMB,
  EmptyPanel,
  ErrorPanel,
  FOCUS,
  FOCUS_INSET,
  META_LIST,
  Notice,
  SectionHeading,
  StaleNotice,
  textAction,
} from "#/components/page";
import { branchPrefill, listRunStates } from "#/domain/run-state";
import { describeSystem, mappedKinds } from "#/domain/systems";
import { RunsTable, RunsTableSkeleton } from "#/features/runs/RunsTable";
import { StartRunDialog, type StartRunTarget } from "#/features/runs/StartRunDialog";
import { useDocumentTitle } from "#/lib/title";

import { Mapping } from "./detail-mapping";
import { Pipeline, PipelineSkeleton, pipelineModel } from "./detail-pipeline";
import { Versions } from "./detail-versions";

export type ConfigTab = "runs" | "versions" | "mapping";

const RUNS_ACTIVE_MS = 2_000;
const RUNS_IDLE_MS = 10_000;

/** The larger of the defined values: the configuration and its versions list can disagree for one refresh. */
const maxDefined = (...values: (number | null | undefined)[]) => {
  const defined = values.filter((v): v is number => typeof v === "number");
  return defined.length ? Math.max(...defined) : undefined;
};

/** The newest `latest_applied_run` across the versions. */
function newestApplied(items: VersionSummary[] | undefined) {
  let best: { run_id: string; finished_at: string } | undefined;
  for (const v of items ?? []) {
    const r = v.latest_applied_run;
    if (!r?.finished_at) continue;
    if (!best || new Date(r.finished_at) > new Date(best.finished_at))
      best = { run_id: r.run_id, finished_at: r.finished_at };
  }
  return best;
}

const META_LINK = cn(
  "relative rounded-sm tabular-nums before:absolute before:-inset-x-1 before:-inset-y-3 before:content-[''] underline decoration-dotted decoration-subtle-muted underline-offset-4 hover:text-foreground",
  FOCUS
);

function Breadcrumb({ current }: { current?: string }) {
  return (
    <nav aria-label="Breadcrumb" className="text-foreground-muted text-sm">
      <ol className="flex flex-wrap items-center gap-x-1.5">
        <li>
          <Link to="/" className={CRUMB}>
            Configurations
          </Link>
        </li>
        {current && (
          <>
            <li aria-hidden="true" className="text-subtle-muted">
              /
            </li>
            <li aria-current="page" className="min-w-0 [overflow-wrap:anywhere]">
              {current}
            </li>
          </>
        )}
      </ol>
    </nav>
  );
}

function Tabs({
  tab,
  counts,
  configId,
}: {
  tab: ConfigTab;
  counts: Record<ConfigTab, string | number | undefined>;
  configId: string;
}) {
  const items: [ConfigTab, string][] = [
    ["runs", "Runs"],
    ["versions", "Versions"],
    ["mapping", "Mapping"],
  ];
  return (
    <nav aria-label="Configuration sections" className="flex gap-4 border-b">
      {items.map(([id, label]) => (
        <Link
          key={id}
          to="/configs/$configId"
          params={{ configId }}
          search={{ tab: id }}
          replace
          resetScroll={false}
          aria-current={tab === id ? "page" : undefined}
          className={cn(
            "relative -mx-2 inline-flex h-11 items-center gap-1.5 rounded-md px-2 text-sm transition-colors duration-150 ease-out motion-reduce:transition-none",
            FOCUS_INSET,
            tab === id
              ? "text-foreground after:absolute after:inset-x-2 after:bottom-[-1px] after:h-0.5 after:rounded-full after:bg-foreground"
              : "text-foreground-muted hover:text-foreground"
          )}
        >
          {label}
          {counts[id] !== undefined && <span className={COUNT_BADGE}>{counts[id]}</span>}
        </Link>
      ))}
    </nav>
  );
}

function PageSkeleton() {
  return (
    <div className="pt-6" aria-busy="true">
      <output className="sr-only">Loading the configuration</output>
      <div className="flex h-8 items-center pointer-coarse:h-11">
        <Bar className="h-4 w-40" />
      </div>
      <div className="mt-4 flex items-start justify-between gap-6">
        <Bar className="h-7 w-80 max-w-full" />
        <Bar className="h-8 w-28 shrink-0 rounded-lg" />
      </div>
      <Bar className="mt-2 h-4 w-96 max-w-full" />
      <div className="mt-6 flex flex-col gap-2">
        <Bar className="h-4 w-20" />
        <PipelineSkeleton />
      </div>
      <Bar className="mt-8 h-11 w-64" />
      <Bar className="mt-4 h-64 w-full rounded-2xl" />
    </div>
  );
}

export function ConfigurationPage({
  configId,
  tab,
  version: requestedVersion,
}: {
  configId: string;
  tab: ConfigTab;
  /** The version the Mapping tab shows; the latest when undefined. */
  version?: number;
}) {
  const config = useQuery(configQuery(configId));
  const versions = useQuery(versionsQuery(configId));
  const runs = useQuery({
    ...runsQuery({ config_id: configId }),
    refetchInterval: (q) =>
      failedFirstRead(q)
        ? false
        : q.state.data?.items.some((r) => r.phase === "accepted")
          ? RUNS_ACTIVE_MS
          : RUNS_IDLE_MS,
  });
  const latestVersion = maxDefined(
    config.data?.latest_version,
    ...(versions.data?.items.map((v) => v.registry_version) ?? [])
  );
  const version = useQuery({
    ...configVersionQuery(configId, latestVersion ?? 0),
    enabled: latestVersion !== undefined,
  });
  const mappingNumber = requestedVersion ?? latestVersion;
  const mappingVersion = useQuery({
    ...configVersionQuery(configId, mappingNumber ?? 0),
    enabled: tab === "mapping" && mappingNumber !== undefined,
  });
  const runItems = runs.data?.items ?? [];
  const latestRun = runItems[0];
  const readDetail = latestRun?.phase === "planned" || latestRun?.phase === "abandoned";
  const latestRunDetail = useQuery({
    ...runQuery(latestRun?.run_id ?? ""),
    enabled: readDetail,
    // A verify or apply started elsewhere leaves the runs list at phase "planned"; only this record shows it.
    refetchInterval: (q) =>
      failedFirstRead(q)
        ? false
        : isInProgress(q.state.data)
          ? RUNS_ACTIVE_MS
          : latestRun?.phase === "planned"
            ? RUNS_IDLE_MS
            : false,
  });
  const openExecution = readDetail
    ? latestRunDetail.data?.orchestration.find((o) => o.terminal_at == null)
    : undefined;
  const inFlight = openExecution?.purpose;
  const lastInFlight = useRef(inFlight);
  const refetchRuns = runs.refetch;
  useEffect(() => {
    // The runs list keeps the run's earlier phase until it is read again after a verify or apply ends.
    if (lastInFlight.current && !inFlight) void refetchRuns();
    lastInFlight.current = inFlight;
  }, [inFlight, refetchRuns]);
  const [starting, setStarting] = useState<StartRunTarget | null>(null);

  const notFound = config.data === undefined && describeError(config.error).notFound;
  useDocumentTitle(
    ...(config.data
      ? [config.data.name ?? configId, "Configurations"]
      : notFound
        ? ["Configuration not found"]
        : config.isPending
          ? ["Configurations"]
          : [configId, "Configurations"])
  );

  if (config.isPending) return <PageSkeleton />;
  if (!config.data) {
    return (
      <>
        <div className="pt-6">
          <Breadcrumb />
        </div>
        {notFound ? (
          <ErrorPanel
            level="page"
            error={config.error}
            action={
              <Link to="/" className={buttonVariants({ variant: "outline", size: "sm" })}>
                View configurations
              </Link>
            }
          >
            <p>
              Configuration ID in the address: <code className={CODE_CHIP}>{configId}</code>
            </p>
          </ErrorPanel>
        ) : (
          <ErrorPanel level="page" error={config.error} onRetry={() => config.refetch()} />
        )}
      </>
    );
  }

  const name = config.data.name ?? configId;
  const cfg = version.data?.declared_content.configuration;
  /** The version the Mapping tab shows, which the tab count describes. */
  const shownMapping =
    requestedVersion === undefined ? cfg : mappingVersion.data?.declared_content.configuration;
  const source = cfg ? describeSystem(cfg.source) : undefined;
  const destination = cfg ? describeSystem(cfg.destination) : undefined;
  const kinds = cfg ? mappedKinds(cfg.schema_mapping) : undefined;
  const versionPending = latestVersion !== undefined && version.isPending;
  const versionFailed = latestVersion !== undefined && !version.data && version.isError;
  const versionsFailed = !versions.data && versions.isError;
  const systemsMissing = latestVersion === undefined ? "No version registered" : "Not loaded";

  const detailRun = latestRunDetail.data?.run;
  const currentRun =
    latestRun &&
    detailRun?.run_id === latestRun.run_id &&
    latestRunDetail.dataUpdatedAt > runs.dataUpdatedAt
      ? detailRun
      : latestRun;
  const shownRuns =
    currentRun && currentRun !== latestRun ? [currentRun, ...runItems.slice(1)] : runItems;
  const latestVersions =
    latestVersion !== undefined ? new Map([[configId, latestVersion]]) : new Map<string, number>();
  const runStates = listRunStates(shownRuns, {
    latestVersions,
    inFlight: currentRun && inFlight ? new Map([[currentRun.run_id, inFlight]]) : undefined,
  });
  const model = pipelineModel(currentRun, {
    latestVersion,
    inFlight: openExecution,
    createdAt: config.data.created_at,
    lastApplied: versions.data ? newestApplied(versions.data.items) : undefined,
    latestRunDetail: currentRun?.phase === "abandoned" ? latestRunDetail.data : undefined,
    sourceLabel: source?.label,
    destinationLabel: destination?.label,
  });

  const prefill = branchPrefill(runItems, configId);
  const start =
    latestVersion === undefined
      ? undefined
      : (v: number = latestVersion) =>
          setStarting({
            configId,
            name,
            version: v,
            branch: prefill?.branch,
            branchFromRunId: prefill?.runId,
          });

  const stale = [config, runs, versions, version].filter((q) => q.data !== undefined && q.isError);
  const readFailure = versionFailed
    ? {
        title: versionsFailed
          ? "Could not read the versions of this configuration"
          : `Could not read version ${latestVersion}`,
        error: version.error,
      }
    : versionsFailed
      ? { title: "Could not read the list of versions", error: versions.error }
      : undefined;
  const coveredLine = (text: string) => (
    <p className="text-pretty text-foreground-muted text-sm">
      {text} The reason is shown at the top of the page.
    </p>
  );

  let runsTab: ReactNode = null;
  if (tab === "runs") {
    if (runs.data) {
      runsTab =
        runItems.length === 0 ? (
          <EmptyPanel title="No runs yet">
            <p className="max-w-[52ch] text-pretty text-foreground-muted">
              A plan reads both systems and lists what would change. Nothing is written until you
              apply it.
            </p>
            {start && (
              <Button variant="outline" size="sm" onPress={() => start()}>
                Create the first plan
              </Button>
            )}
          </EmptyPanel>
        ) : (
          <div className="flex flex-col gap-3">
            <RunsTable runs={shownRuns} states={runStates} label={`Runs of ${name}`} />
            {runs.data.next_cursor && (
              <Link
                to="/runs"
                search={{ config: configId }}
                className={textAction({ className: "self-start" })}
              >
                View all runs of this configuration
                <ChevronRightIcon />
              </Link>
            )}
          </div>
        );
    } else if (runs.isPending) {
      runsTab = <RunsTableSkeleton rows={4} withConfig={false} />;
    } else {
      runsTab = (
        <p className="text-pretty text-foreground-muted text-sm">
          The runs could not be read. The reason is shown under Latest run.
        </p>
      );
    }
  }

  let versionsTab: ReactNode = null;
  if (tab === "versions") {
    if (versions.data) {
      versionsTab = (
        <Versions
          configId={configId}
          versions={versions.data.items}
          latestVersion={latestVersion}
          runStates={runStates}
          onStart={start}
        />
      );
    } else if (versions.isPending) {
      versionsTab = <Bar className="h-64 w-full rounded-2xl" />;
    } else {
      versionsTab = coveredLine("The versions could not be read.");
    }
  }

  let mappingTab: ReactNode = null;
  if (tab === "mapping") {
    if (mappingNumber === undefined) {
      mappingTab = versions.isPending ? (
        <Bar className="h-64 w-full rounded-2xl" />
      ) : (
        <EmptyPanel title="No version registered" />
      );
    } else if (mappingVersion.data) {
      mappingTab = (
        <Mapping
          configId={configId}
          version={mappingVersion.data}
          versionNumbers={versions.data?.items.map((v) => v.registry_version) ?? []}
          latestVersion={latestVersion}
        />
      );
    } else if (mappingVersion.isPending) {
      mappingTab = <Bar className="h-64 w-full rounded-2xl" />;
    } else if (describeError(mappingVersion.error).notFound) {
      mappingTab = (
        <EmptyPanel title={`Version ${mappingNumber} is not registered`}>
          <Link
            to="/configs/$configId"
            params={{ configId }}
            search={{ tab: "mapping" }}
            replace
            resetScroll={false}
            className={buttonVariants({ variant: "outline", size: "sm" })}
          >
            View the latest version
          </Link>
        </EmptyPanel>
      );
    } else if (mappingNumber === latestVersion && versionFailed) {
      mappingTab = coveredLine(`Version ${mappingNumber} could not be read.`);
    } else {
      mappingTab = (
        <ErrorPanel error={mappingVersion.error} onRetry={() => mappingVersion.refetch()} compact />
      );
    }
  }

  return (
    <>
      <header className="flex flex-col gap-4 pt-6 pb-6">
        <Breadcrumb current={name} />
        <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-4">
          <div className="flex min-w-0 flex-col gap-2">
            <h1 className="hyphens-none text-balance font-semibold text-2xl tracking-tight [overflow-wrap:anywhere] max-sm:text-xl">
              {name}
            </h1>
            <ul className={META_LIST}>
              {latestVersion !== undefined && !versionFailed && (
                <li className="inline-flex items-center gap-1.5 text-foreground">
                  {source && destination ? (
                    <>
                      {source.label}
                      <ArrowRightIcon />
                      <span className="sr-only">to</span>
                      {destination.label}
                    </>
                  ) : (
                    <Bar className="h-4 w-32" />
                  )}
                </li>
              )}
              <li>
                {latestVersion !== undefined ? (
                  <Link
                    to="/configs/$configId"
                    params={{ configId }}
                    search={{ tab: "versions" }}
                    replace
                    resetScroll={false}
                    className={META_LINK}
                  >
                    Version {latestVersion}
                  </Link>
                ) : (
                  "No version registered"
                )}
              </li>
              {kinds && (
                <li>
                  <Link
                    to="/configs/$configId"
                    params={{ configId }}
                    search={{ tab: "mapping" }}
                    replace
                    resetScroll={false}
                    className={META_LINK}
                  >
                    {kinds.length} {kinds.length === 1 ? "kind" : "kinds"}
                  </Link>
                </li>
              )}
              <li className="inline-flex items-center gap-1 font-mono text-subtle text-xs">
                {configId}
                <CopyButton value={configId} label="Copy configuration ID" />
              </li>
            </ul>
          </div>
          <div className="flex flex-col items-start gap-1 sm:items-end">
            <Button
              variant={runs.data && model.action?.kind !== "review" ? "primary" : "outline"}
              size="sm"
              isDisabled={!start}
              onPress={() => start?.()}
            >
              <PlusIcon />
              Start run
            </Button>
            {!start && (
              <p className="text-subtle text-xs">Register a version before starting a run.</p>
            )}
          </div>
        </div>
      </header>

      {readFailure && (
        <Notice
          tone="neutral"
          className="mb-4"
          role="alert"
          title={readFailure.title}
          icon={<StateIcon state="failed" className="mt-0.5 size-4 shrink-0 text-danger" />}
          action={
            describeError(readFailure.error).retryable ? (
              <Button
                size="xs"
                variant="ghost"
                className="-my-1"
                onPress={() => {
                  void version.refetch();
                  void versions.refetch();
                }}
              >
                Try again
              </Button>
            ) : undefined
          }
        >
          <p>{describeError(readFailure.error).title}</p>
        </Notice>
      )}
      {stale.length > 0 && (
        <StaleNotice
          error={stale[0].error}
          updatedAt={Math.min(...stale.map((q) => q.dataUpdatedAt))}
          onRetry={() => {
            void config.refetch();
            void runs.refetch();
            void versions.refetch();
          }}
        />
      )}

      <div className="flex flex-col gap-8">
        <section aria-labelledby="latest" className="flex flex-col gap-2">
          <SectionHeading id="latest" title="Latest run" />
          {runs.data ? (
            <Pipeline
              model={model}
              run={currentRun}
              source={source}
              destination={destination}
              declaredBranch={destination?.branch}
              versionPending={versionPending}
              systemsMissing={systemsMissing}
              onStart={start && (() => start())}
            />
          ) : runs.isPending ? (
            <PipelineSkeleton />
          ) : (
            <ErrorPanel error={runs.error} onRetry={() => runs.refetch()} compact />
          )}
        </section>

        <div className="flex flex-col gap-4">
          <Tabs
            tab={tab}
            configId={configId}
            counts={{
              runs: runs.data ? `${runItems.length}${runs.data.next_cursor ? "+" : ""}` : undefined,
              versions: versions.data?.items.length,
              mapping: shownMapping ? (shownMapping.schema_mapping ?? []).length : undefined,
            }}
          />
          {runsTab}
          {versionsTab}
          {mappingTab}
        </div>
      </div>
      {starting && <StartRunDialog target={starting} onClose={() => setStarting(null)} />}
    </>
  );
}
