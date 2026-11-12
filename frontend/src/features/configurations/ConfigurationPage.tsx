import { Button, buttonVariants, Card } from "@infrahub/ui";
import { Link, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { cn } from "tailwind-variants";

import { describeError } from "#/api/queries";
import { ChevronRightIcon, PlusIcon, STATE_TEXT, StateIcon } from "#/components/icons";
import {
  Bar,
  CODE_CHIP,
  CRUMB,
  EmptyPanel,
  ErrorPanel,
  Notice,
  SectionHeading,
  StaleNotice,
  textAction,
} from "#/components/page";
import { When } from "#/components/run-status";
import { mappingEntries, removedEntries } from "#/domain/mapping";
import { type Explanation, explain, type RunState, runState } from "#/domain/run-state";
import { RunsTable, RunsTableSkeleton } from "#/features/runs/RunsTable";
import { StartRunDialog, type StartRunTarget } from "#/features/runs/StartRunDialog";
import { useDocumentTitle } from "#/lib/title";

import { type ConfigurationDetail, useConfigurationDetail } from "./detail";
import { MappingList, MappingPanel } from "./detail-mapping";
import { MAPPING_SECTION_ID, Systems } from "./detail-systems";
import { VersionBar } from "./detail-version-bar";

/** The runs of the version shown before "Show all". */
const RUNS_SHOWN = 5;
/** The page's content, which the mapping panel moves left. */
const SHIFT_TARGET_ID = "main";

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
      <Bar className="mt-6 h-[88px] w-full rounded-2xl" />
      <Bar className="mt-8 h-4 w-24" />
      <Bar className="mt-3 h-64 w-full rounded-2xl" />
    </div>
  );
}

interface VersionState {
  state: RunState;
  headline: string;
  at?: Explanation["at"];
  action?: Explanation["action"];
}

/** Whether the selected version needs someone, from its newest run. */
function versionState(detail: ConfigurationDetail): VersionState | undefined {
  const { selected, isLatest, config, runs, currentRun, source, destination, inFlight } = detail;
  if (selected === undefined || !runs.data || !config.data) return undefined;
  if (!currentRun) {
    return {
      state: "idle",
      headline: `Version ${selected} has no run yet`,
      action: { label: `Plan version ${selected}`, primary: isLatest, target: "plan" },
    };
  }
  const ctx = {
    latestVersion: selected,
    inFlight,
    createdAt: config.data.created_at,
    source,
    destination,
  };
  const e = explain(currentRun, ctx);
  return {
    state: runState(currentRun, ctx),
    headline: e.short,
    at: e.at,
    action: e.action && { ...e.action, primary: e.action.primary && isLatest },
  };
}

/** The page of one version of a configuration: what it does, its mapping, then its latest runs. */
export function ConfigurationPage({
  configId,
  version: requestedVersion,
  compare,
  kind,
}: {
  configId: string;
  /** The version shown; the latest when undefined. */
  version?: number;
  /** An earlier version the mapping is compared with. */
  compare?: number;
  /** The key of the mapping open in the panel. */
  kind?: string;
}) {
  const navigate = useNavigate({ from: "/configs/$configId" });
  const detail = useConfigurationDetail(configId, requestedVersion, compare);
  const [starting, setStarting] = useState<StartRunTarget | null>(null);
  const [allRuns, setAllRuns] = useState(false);
  const { config, versions, version, runs, name, selected, latest, isLatest } = detail;
  const { source, destination, mappings, diff, runItems } = detail;

  const notFound = config.data === undefined && describeError(config.error).notFound;
  useDocumentTitle(
    ...(config.data
      ? [name, "Configurations"]
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

  const start = (v: number) =>
    setStarting({
      configId,
      name,
      version: v,
      branch: detail.prefill?.branch,
      branchFromRunId: detail.prefill?.runId,
    });
  const state = versionState(detail);
  const entries = mappingEntries(mappings, diff);
  const removed = removedEntries(detail.baseMappings, diff);
  const shownRuns = allRuns ? runItems : runItems.slice(0, RUNS_SHOWN);

  const versionMissing = !version.data && describeError(version.error).notFound;
  const versionFailed = selected !== undefined && !version.data && version.isError;
  const versionsFailed = !versions.data && versions.isError;
  const readFailure =
    versionFailed && !versionMissing
      ? { title: `Could not read version ${selected}`, error: version.error }
      : versionsFailed
        ? { title: "Could not read the list of versions", error: versions.error }
        : undefined;
  const stale = [config, runs, versions].filter((q) => q.data !== undefined && q.isError);

  return (
    <>
      <header className="flex flex-col gap-4 pt-6 pb-6">
        <Breadcrumb current={name} />
        <div className="grid grid-cols-1 items-start gap-x-6 gap-y-4 sm:grid-cols-[minmax(0,1fr)_auto]">
          <div className="flex min-w-0 flex-col gap-2">
            <h1 className="hyphens-none text-balance font-semibold text-2xl tracking-tight [overflow-wrap:anywhere] max-sm:text-xl">
              {name}
            </h1>
            {state && selected !== undefined && (
              <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px] text-foreground-muted">
                <span
                  className={cn(
                    "inline-flex items-center gap-1.5 font-medium",
                    STATE_TEXT[state.state]
                  )}
                >
                  <StateIcon state={state.state} className="size-3.5 shrink-0" />
                  {state.headline}
                </span>
                {state.at && (
                  <>
                    <span aria-hidden="true">·</span>
                    <When iso={state.at.iso} prefix={state.at.prefix} />
                  </>
                )}
                {state.action &&
                  (state.action.target === "run" && detail.currentRun ? (
                    <Link
                      to="/runs/$runId"
                      params={{ runId: detail.currentRun.run_id }}
                      className={textAction({ primary: state.action.primary })}
                    >
                      {state.action.label}
                      <ChevronRightIcon />
                    </Link>
                  ) : (
                    <button
                      type="button"
                      onClick={() => start(selected)}
                      className={textAction({ primary: state.action.primary })}
                    >
                      {state.action.label}
                      <ChevronRightIcon />
                    </button>
                  ))}
              </p>
            )}
          </div>
          {selected !== undefined ? (
            <Button
              variant={!state?.action?.primary && isLatest ? "primary" : "outline"}
              size="sm"
              onPress={() => start(selected)}
            >
              <PlusIcon />
              {isLatest ? "Start run" : `Plan version ${selected}`}
            </Button>
          ) : (
            !versions.isPending && (
              <p className="text-subtle text-xs">Register a version before starting a run.</p>
            )
          )}
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

      {selected === undefined ? (
        versions.isPending ? (
          <Bar className="h-64 w-full rounded-2xl" />
        ) : (
          <EmptyPanel title="No version registered">
            <p className="max-w-[52ch] text-pretty text-foreground-muted">
              A run needs a registered version of the configuration. Register one with{" "}
              <code className={CODE_CHIP}>infrahub-sync configs register</code>.
            </p>
          </EmptyPanel>
        )
      ) : versionMissing ? (
        <EmptyPanel title={`Version ${selected} is not registered`}>
          <Link
            to="/configs/$configId"
            params={{ configId }}
            replace
            className={buttonVariants({ variant: "outline", size: "sm" })}
          >
            View the latest version
          </Link>
        </EmptyPanel>
      ) : (
        <div className="flex flex-col gap-8">
          <div className="flex flex-col gap-3">
            {!isLatest && latest !== undefined && (
              <Notice
                role="note"
                title={`You are viewing version ${selected}`}
                action={
                  <Link
                    to="/configs/$configId"
                    params={{ configId }}
                    resetScroll={false}
                    className={textAction({ primary: true })}
                  >
                    View version {latest}
                    <ChevronRightIcon />
                  </Link>
                }
              >
                <p>
                  Version {latest} is the latest. The systems, the mapping and the runs below belong
                  to version {selected}.
                </p>
              </Notice>
            )}
            {source && destination && version.data ? (
              <Systems
                configId={configId}
                source={source}
                destination={destination}
                adapters={{
                  source: version.data.declared_content.configuration.source.name,
                  destination: version.data.declared_content.configuration.destination.name,
                }}
                kinds={detail.kinds.length}
              />
            ) : (
              !versionFailed && <Bar className="h-[88px] w-full rounded-2xl" />
            )}
          </div>

          <section
            id={MAPPING_SECTION_ID}
            aria-labelledby="mapping-title"
            className="flex scroll-mt-16 flex-col gap-2"
          >
            <SectionHeading
              id="mapping-title"
              title="Mapping"
              count={version.data ? mappings.length : undefined}
            />
            <VersionBar detail={detail} />
            {version.data ? (
              mappings.length === 0 && removed.length === 0 ? (
                <Card>
                  <p className="px-4 py-6 text-center text-foreground-muted text-sm">
                    Version {selected} declares no mapping, so a run synchronizes nothing.
                  </p>
                </Card>
              ) : (
                <MappingList
                  configId={configId}
                  entries={entries}
                  removed={removed}
                  diff={diff}
                  open={kind}
                />
              )
            ) : versionFailed ? (
              <p className="text-pretty text-foreground-muted text-sm">
                The mapping could not be read. The reason is shown at the top of the page.
              </p>
            ) : (
              <Bar className="h-64 w-full rounded-2xl" />
            )}
          </section>

          <section aria-labelledby="runs" className="flex flex-col gap-2">
            <SectionHeading
              id="runs"
              title={`Latest runs of version ${selected}`}
              count={runs.data ? runItems.length : undefined}
            />
            {runs.data ? (
              runItems.length === 0 ? (
                <Card>
                  <p className="px-4 py-6 text-center text-foreground-muted text-sm">
                    No run has used version {selected} yet.
                  </p>
                </Card>
              ) : (
                <>
                  <RunsTable
                    runs={shownRuns}
                    states={detail.runStates}
                    label={`Latest runs of version ${selected}`}
                  />
                  <div className="flex flex-wrap items-center gap-x-4">
                    {runItems.length > RUNS_SHOWN && (
                      <button
                        type="button"
                        aria-expanded={allRuns}
                        onClick={() => setAllRuns((v) => !v)}
                        className={textAction()}
                      >
                        {allRuns ? "Show less" : "Show more"}
                        <ChevronRightIcon
                          className={cn("size-3.5", allRuns ? "-rotate-90" : "rotate-90")}
                        />
                      </button>
                    )}
                    {runs.data.next_cursor && (
                      <Link to="/runs" search={{ config: configId }} className={textAction()}>
                        View all runs of this configuration
                        <ChevronRightIcon />
                      </Link>
                    )}
                  </div>
                </>
              )
            ) : runs.isPending ? (
              <RunsTableSkeleton rows={4} withConfig={false} />
            ) : (
              <ErrorPanel error={runs.error} onRetry={() => runs.refetch()} compact />
            )}
          </section>
        </div>
      )}

      <MappingPanel
        configId={configId}
        version={selected}
        entries={entries}
        open={kind}
        against={diff?.against}
        sourceLabel={source?.label ?? "Source"}
        destinationLabel={destination?.label ?? "Destination"}
        shiftTargetId={SHIFT_TARGET_ID}
        onClose={() =>
          void navigate({
            search: (s) => ({ ...s, kind: undefined }),
            replace: true,
            resetScroll: false,
          })
        }
      />
      {starting && <StartRunDialog target={starting} onClose={() => setStarting(null)} />}
    </>
  );
}
