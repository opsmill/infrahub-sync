import {
  Button,
  buttonVariants,
  Label,
  Select,
  SelectItem,
  SelectList,
  SelectTrigger,
  Spinner,
} from "@infrahub/ui";
import { useInfiniteQuery, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { cn } from "tailwind-variants";

import { configsQuery, describeError, runPagesQuery, runQuery } from "#/api/queries";
import type { Run, RunResource } from "#/api/types";
import {
  Bar,
  EmptyPanel,
  ErrorPanel,
  FilterTabs,
  SearchField,
  StaleNotice,
} from "#/components/page";
import { fmt, type ListRunState, listRunStates } from "#/domain/run-state";
import { useNow } from "#/lib/time";
import { useDocumentTitle } from "#/lib/title";

import { RunsTable, RunsTableSkeleton } from "./RunsTable";

export const FILTER_IDS = ["all", "action", "running", "failed", "applied"] as const;
export type FilterId = (typeof FILTER_IDS)[number];

export interface RunsSearch {
  q?: string;
  show?: FilterId;
  config?: string;
}

const FILTERS: { id: FilterId; label: string; test: (run: Run, s: ListRunState) => boolean }[] = [
  { id: "all", label: "All", test: () => true },
  { id: "action", label: "Needs action", test: (_, s) => s.needsAction },
  { id: "running", label: "Running", test: (_, s) => s.state === "running" },
  { id: "failed", label: "Failed", test: (_, s) => s.state === "failed" },
  { id: "applied", label: "Applied", test: (run) => run.phase === "applied" },
];

const ALL_CONFIGS = "__all__";

const plural = (n: number, one: string, many: string) => `${fmt(n)} ${n === 1 ? one : many}`;

/** Button sizes reach 44 px on touch, like the filter tabs. */
const TOUCH = "pointer-coarse:h-11";

const readDetails = (results: { data?: RunResource }[]) =>
  results.flatMap((r) => (r.data ? [r.data] : []));

/** Verify and apply of a plan run that is still open, keyed by run ID. */
function openStages(details: RunResource[]): Map<string, string> {
  return new Map(
    details.flatMap((d) => {
      const open = d.orchestration.find((o) => o.terminal_at == null);
      return open?.purpose === "verify" || open?.purpose === "apply"
        ? [[d.run.run_id, open.purpose] as const]
        : [];
    })
  );
}

/** Runs whose detail read is newer than the list: an apply or verify finished after the last list read. */
function changedRuns(details: RunResource[], listed: Run[]): Map<string, Run> {
  const byId = new Map(listed.map((r) => [r.run_id, r]));
  return new Map(
    details.flatMap((d) => {
      const l = byId.get(d.run.run_id);
      return l && (l.phase !== d.run.phase || l.finished_at !== d.run.finished_at)
        ? [[d.run.run_id, d.run] as const]
        : [];
    })
  );
}

const SUBTITLE = "Verify and apply are steps of a plan run.";

export function RunsPage({ q, show = "all", config }: RunsSearch) {
  useDocumentTitle("Runs");
  const navigate = useNavigate();
  const setSearch = (patch: RunsSearch) =>
    navigate({
      to: "/runs",
      search: (prev) => ({ ...prev, ...patch }),
      replace: true,
      resetScroll: false,
    });

  const queryClient = useQueryClient();
  const runs = useInfiniteQuery(runPagesQuery(config));
  const configs = useQuery(configsQuery());

  const listed = useMemo(() => runs.data?.pages.flatMap((p) => p.items) ?? [], [runs.data]);
  const configNames = useMemo(
    () => new Map((configs.data?.items ?? []).map((c) => [c.config_id, c.name ?? c.config_id])),
    [configs.data]
  );
  const latestVersions = useMemo(
    () =>
      new Map(
        (configs.data?.items ?? []).flatMap((c) =>
          c.latest_version != null ? [[c.config_id, c.latest_version] as [string, number]] : []
        )
      ),
    [configs.data]
  );

  // Plans waiting for review are read one by one: the list items carry no executions, so only the
  // detail read shows a verify or apply in progress, and it sees the end of that stage first.
  const baseStates = useMemo(
    () => listRunStates(listed, { latestVersions }),
    [listed, latestVersions]
  );
  const reviewable = useMemo(
    () =>
      listed.filter((r) => {
        const s = baseStates.get(r.run_id);
        return s?.needsAction && s.state === "review";
      }),
    [listed, baseStates]
  );
  const details = useQueries({
    queries: reviewable.map((r) => ({ ...runQuery(r.run_id), staleTime: 10_000 })),
    combine: readDetails,
  });
  const changed = useMemo(() => changedRuns(details, listed), [details, listed]);
  const all = useMemo(
    () => (changed.size > 0 ? listed.map((r) => changed.get(r.run_id) ?? r) : listed),
    [listed, changed]
  );
  const changedKey = [...changed.values()].map((r) => `${r.run_id}:${r.phase}`).join();
  useEffect(() => {
    if (changedKey) void queryClient.invalidateQueries({ queryKey: ["get", "/runs", "pages"] });
  }, [changedKey, queryClient]);
  const states = useMemo(
    () => listRunStates(all, { latestVersions, inFlight: openStages(details) }),
    [all, latestVersions, details]
  );

  const counts = useMemo(() => {
    const n = Object.fromEntries(FILTER_IDS.map((id) => [id, 0])) as Record<FilterId, number>;
    for (const run of all) {
      const s = states.get(run.run_id);
      if (!s) continue;
      for (const f of FILTERS) if (f.test(run, s)) n[f.id] += 1;
    }
    return n;
  }, [all, states]);

  const needle = (q ?? "").trim().toLowerCase();
  const { shown, searchMatches } = useMemo(() => {
    const test = FILTERS.find((f) => f.id === show)?.test ?? (() => true);
    const found = all.filter((r) => {
      const s = states.get(r.run_id);
      if (!s) return false;
      if (!needle) return true;
      return [
        r.config_id && configNames.get(r.config_id),
        r.configuration_reference,
        r.registry_version != null && `Version ${r.registry_version}`,
        r.run_id,
        r.destination_branch,
        r.actor,
        s.label,
      ].some((v) => typeof v === "string" && v.toLowerCase().includes(needle));
    });
    return {
      shown: found.filter((r) => test(r, states.get(r.run_id) as ListRunState)),
      searchMatches: found.length,
    };
  }, [all, states, show, needle, configNames]);

  const listedConfig = config ? configs.data?.items.find((c) => c.config_id === config) : undefined;
  // Only a complete configuration list proves that an ID is not registered.
  const missingConfig = Boolean(
    config &&
      configs.data &&
      !configs.data.next_cursor &&
      !listedConfig &&
      runs.data &&
      listed.length === 0
  );
  const configName = config ? (listedConfig?.name ?? config) : undefined;
  const more = runs.hasNextPage;
  const countLabel = (n: number) => `${fmt(n)}${more ? "+" : ""}`;
  const summaryRef = useRef<HTMLParagraphElement>(null);

  const loadOlder = async () => {
    const r = await runs.fetchNextPage();
    if (r.isError) return;
    const id = r.data?.pages.at(-1)?.items[0]?.run_id;
    requestAnimationFrame(() => {
      const link = id
        ? [...document.querySelectorAll<HTMLElement>(`a[data-run-id="${CSS.escape(id)}"]`)].find(
            (a) => a.offsetParent !== null
          )
        : undefined;
      (link ?? summaryRef.current)?.focus();
    });
  };

  const showAll = () => setSearch({ q: undefined, show: undefined });
  const showAllConfigs = () => setSearch({ config: undefined, show: undefined });
  const retryRefresh = async () => {
    const r = await runs.refetch();
    if (!r.isError) summaryRef.current?.focus();
  };

  const toolbar = runs.data !== undefined || configs.data !== undefined;
  const noRunsAnywhere = !config && runs.data !== undefined && listed.length === 0;
  const filterCount = (id: FilterId) =>
    runs.data === undefined ? undefined : counts[id] === 0 && !more ? 0 : countLabel(counts[id]);
  const loadedNoun: [string, string] = more ? ["loaded run", "loaded runs"] : ["run", "runs"];

  let body: ReactNode;
  if (runs.isPending) {
    body = (
      <>
        <SlowAnswer onRetry={() => runs.refetch({ cancelRefetch: true })} />
        <div className="flex h-4 items-center" aria-hidden="true">
          <Bar className="h-3 w-24" />
        </div>
        <RunsTableSkeleton withConfig={!config} />
      </>
    );
  } else if (!runs.data) {
    body = <ErrorPanel error={runs.error} onRetry={() => runs.refetch()} />;
  } else if (missingConfig) {
    body = (
      <EmptyPanel title={`No configuration has the ID ${config}`}>
        <p className="max-w-[52ch] text-pretty text-foreground-muted">
          The Sync API does not list this configuration. It may have been removed, or the link is
          wrong.
        </p>
        <Button variant="outline" size="sm" className={cn("mt-1", TOUCH)} onPress={showAllConfigs}>
          Show all configurations
        </Button>
      </EmptyPanel>
    );
  } else if (config && listed.length === 0) {
    body = (
      <EmptyPanel title="No runs for this configuration yet">
        <p className="max-w-[52ch] text-pretty text-foreground-muted">
          Open the configuration and select Start run.
        </p>
        <div className="mt-1 flex flex-wrap justify-center gap-2">
          <Link
            to="/configs/$configId"
            params={{ configId: config }}
            className={buttonVariants({ variant: "outline", size: "sm", className: TOUCH })}
          >
            View configuration
          </Link>
          <Button variant="outline" size="sm" className={TOUCH} onPress={showAllConfigs}>
            Show all configurations
          </Button>
        </div>
      </EmptyPanel>
    );
  } else {
    body = (
      <>
        <div className="flex flex-col gap-1">
          <p
            ref={summaryRef}
            tabIndex={-1}
            aria-live="polite"
            className="text-subtle text-xs tabular-nums outline-none"
          >
            {shown.length === all.length
              ? plural(all.length, ...loadedNoun)
              : `${fmt(shown.length)} of ${plural(all.length, ...loadedNoun)}`}
            {more && " · Older runs are not loaded, so search and filters do not cover them."}
          </p>
          {!config && configs.error && !configs.data && (
            <output className="block text-subtle text-xs">
              Configuration names could not be loaded, so rows show configuration IDs.
            </output>
          )}
        </div>
        {shown.length === 0 ? (
          <NoMatch
            query={q?.trim()}
            filter={show}
            loaded={all.length}
            searchMatches={searchMatches}
            configName={configName}
            onShowAll={showAll}
            older={
              more ? (
                <>
                  <p className="text-foreground-muted text-sm">Older runs are not loaded yet.</p>
                  <LoadOlder runs={runs} onLoad={loadOlder} />
                </>
              ) : undefined
            }
          />
        ) : (
          <>
            <RunsTable
              runs={shown}
              states={states}
              label={configName ? `Runs of ${configName}` : "Runs"}
              configNames={config ? undefined : configNames}
              namesPending={configs.isPending}
            />
            {more ? (
              <div className="flex flex-col items-center gap-2">
                <LoadOlder runs={runs} onLoad={loadOlder} />
              </div>
            ) : (
              runs.data.pages.length > 1 && (
                <p className="text-center text-subtle text-xs">
                  All {fmt(all.length)} runs are loaded.
                </p>
              )
            )}
          </>
        )}
      </>
    );
  }

  return (
    <>
      <header className="flex flex-col gap-1.5 pt-8 pb-6">
        <h1 className="text-balance font-semibold text-2xl tracking-tight">Runs</h1>
        {!missingConfig && (
          <p className="text-pretty text-foreground-muted">
            {configName ? (
              <>
                Plan and sync runs of <span className="text-foreground">{configName}</span>, newest
                first. {SUBTITLE}
              </>
            ) : (
              <>Every plan and sync run, newest first. {SUBTITLE}</>
            )}
          </p>
        )}
      </header>
      {runs.isRefetchError && (
        <StaleNotice error={runs.error} updatedAt={runs.dataUpdatedAt} onRetry={retryRefresh} />
      )}
      {!toolbar && runs.isPending ? (
        <div className="flex flex-col gap-4">
          <SlowAnswer onRetry={() => runs.refetch({ cancelRefetch: true })} />
          <ToolbarSkeleton />
          <div className="flex h-4 items-center" aria-hidden="true">
            <Bar className="h-3 w-24" />
          </div>
          <RunsTableSkeleton withConfig={!config} />
        </div>
      ) : !toolbar ? (
        <ErrorPanel error={runs.error} onRetry={() => runs.refetch()} />
      ) : noRunsAnywhere ? (
        <EmptyPanel title="No runs yet">
          <p className="max-w-[52ch] text-pretty text-foreground-muted">
            Runs start from a configuration. Open one and select Start run.
          </p>
          <Link
            to="/"
            className={buttonVariants({
              variant: "outline",
              size: "sm",
              className: ["mt-1", TOUCH],
            })}
          >
            View configurations
          </Link>
        </EmptyPanel>
      ) : (
        <div className="flex flex-col gap-4">
          <div className="flex flex-wrap items-end gap-3">
            <ConfigSelect
              value={config}
              configs={configs.data?.items ?? []}
              onChange={(id) => setSearch({ config: id, show: undefined })}
            />
            <SearchField
              value={q ?? ""}
              onChange={(v) => setSearch({ q: v || undefined })}
              placeholder="Search runs"
              label="Search runs by configuration, version, run ID, branch, actor or state"
            />
            <div className="hidden h-10 items-center sm:flex xl:ml-auto">
              <FilterTabs
                label="Show runs"
                options={FILTERS.map((f) => ({
                  id: f.id,
                  label: f.label,
                  count: filterCount(f.id),
                }))}
                value={show}
                onChange={(id) => setSearch({ show: id === "all" ? undefined : id })}
              />
            </div>
            <div className="flex w-full flex-col gap-1.5 sm:hidden">
              <Select
                selectedKey={show}
                onSelectionChange={(k) =>
                  setSearch({ show: k === "all" || k == null ? undefined : (k as FilterId) })
                }
              >
                <Label className="text-subtle text-xs">Show</Label>
                <SelectTrigger size="md" />
                <SelectList className="bg-content">
                  {FILTERS.map((f) => (
                    <SelectItem key={f.id} id={f.id} textValue={f.label}>
                      {f.id === "all" ? "All runs" : f.label}
                      {runs.data && ` (${countLabel(counts[f.id])})`}
                    </SelectItem>
                  ))}
                </SelectList>
              </Select>
            </div>
          </div>
          {body}
        </div>
      )}
    </>
  );
}

/** The toolbar's shape on the first load, including the phone-only Show select. */
function ToolbarSkeleton() {
  const field = (className: string) => (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <div className="flex h-4 items-center">
        <Bar className="h-3 w-24" />
      </div>
      <Bar className="h-10 w-full" />
    </div>
  );
  return (
    <div className="flex flex-wrap items-end gap-3" aria-hidden="true">
      {field("w-full sm:w-64")}
      <Bar className="h-10 w-full sm:w-80" />
      <Bar className="mb-0.5 hidden h-9 w-[440px] sm:block xl:ml-auto" />
      {field("w-full sm:hidden")}
    </div>
  );
}

function ConfigSelect({
  value,
  configs,
  onChange,
}: {
  value?: string;
  configs: { config_id: string; name?: string | null }[];
  onChange: (id: string | undefined) => void;
}) {
  const options = configs.map((c) => ({ id: c.config_id, name: c.name ?? c.config_id }));
  if (value && !options.some((o) => o.id === value)) options.push({ id: value, name: value });
  return (
    <Select
      className="flex w-full flex-col gap-1.5 sm:w-64"
      selectedKey={value ?? ALL_CONFIGS}
      onSelectionChange={(k) => onChange(k === ALL_CONFIGS || k == null ? undefined : String(k))}
    >
      <Label className="text-subtle text-xs">Configuration</Label>
      <SelectTrigger size="md" />
      <SelectList
        width="min-trigger"
        className="max-h-80 max-w-[min(32rem,calc(100vw-2rem))] bg-content"
      >
        <SelectItem id={ALL_CONFIGS} textValue="All configurations">
          All configurations
        </SelectItem>
        {options.map((o) => (
          <SelectItem key={o.id} id={o.id} textValue={o.name}>
            <span className="flex min-w-0 flex-col">
              <span className="truncate">{o.name}</span>
              {o.name !== o.id && (
                <span className="truncate font-mono text-subtle text-xs in-[button]:hidden">
                  {o.id}
                </span>
              )}
            </span>
          </SelectItem>
        ))}
      </SelectList>
    </Select>
  );
}

function LoadOlder({
  runs,
  onLoad,
}: {
  runs: { isFetchingNextPage: boolean; isFetchNextPageError: boolean; error: unknown };
  onLoad: () => void;
}) {
  const d = runs.isFetchNextPageError ? describeError(runs.error) : undefined;
  return (
    <>
      {d && !runs.isFetchingNextPage && (
        <p role="alert" className="max-w-[60ch] text-pretty text-center text-danger text-sm">
          Could not load older runs. {d.detail ?? `${d.title}.`}
        </p>
      )}
      <Button
        variant="outline"
        size="sm"
        className={TOUCH}
        isDisabledAndFocusable={runs.isFetchingNextPage}
        onPress={onLoad}
      >
        {runs.isFetchingNextPage ? (
          <>
            <Spinner aria-hidden="true" className="motion-reduce:animate-none" />
            Loading older runs…
          </>
        ) : runs.isFetchNextPageError ? (
          "Try again"
        ) : (
          "Load older runs"
        )}
      </Button>
    </>
  );
}

const NO_MATCH: Record<
  Exclude<FilterId, "all">,
  (loaded: number, configName?: string) => [string, string]
> = {
  action: (_, name) => [
    "No run needs action",
    name
      ? `The newest run of ${name} did not fail and has no plan waiting for review.`
      : "No configuration has a newest run that failed or has a plan waiting for review.",
  ],
  running: () => [
    "No run is in progress",
    "A run appears here while it plans, verifies, applies or syncs.",
  ],
  failed: (n) => [
    "No failed runs",
    `None of the ${plural(n, "loaded run", "loaded runs")} failed.`,
  ],
  applied: (n) => [
    "No applied runs",
    `None of the ${plural(n, "loaded run", "loaded runs")} was applied.`,
  ],
};

/** The runs a filter keeps, as the subject of "No … match". */
const FILTER_SUBJECT: Record<Exclude<FilterId, "all">, string> = {
  action: "runs that need action",
  running: "runs in progress",
  failed: "failed runs",
  applied: "applied runs",
};

function NoMatch({
  query,
  filter,
  loaded,
  searchMatches,
  configName,
  onShowAll,
  older,
}: {
  query?: string;
  filter: FilterId;
  loaded: number;
  /** Runs that match the search when the filter is ignored. */
  searchMatches: number;
  configName?: string;
  onShowAll: () => void;
  older?: ReactNode;
}) {
  let title: string;
  let body: string;
  if (query && filter !== "all" && searchMatches > 0) {
    const label = FILTERS.find((f) => f.id === filter)?.label;
    title = `No ${FILTER_SUBJECT[filter]} match “${query}”`;
    body = `The search matches ${plural(searchMatches, "loaded run", "loaded runs")}, but the ${label} filter hides ${searchMatches === 1 ? "it" : "them"}.`;
  } else if (query || filter === "all") {
    title = `No runs match “${query ?? ""}”`;
    body =
      "Search looks at configuration names and references, versions, run IDs, branches, actors and states.";
  } else {
    [title, body] = NO_MATCH[filter](loaded, configName);
  }
  return (
    <EmptyPanel title={title}>
      <p className="max-w-[56ch] text-pretty text-foreground-muted">{body}</p>
      <Button variant="outline" size="sm" className={cn("mt-1", TOUCH)} onPress={onShowAll}>
        Show all runs
      </Button>
      {older && <div className="mt-2 flex flex-col items-center gap-2">{older}</div>}
    </EmptyPanel>
  );
}

/** Says how long the first answer has taken, once it passes 8 seconds. */
function SlowAnswer({ onRetry }: { onRetry: () => void }) {
  const [start, setStart] = useState(() => Date.now());
  const now = useNow(1000);
  const seconds = Math.floor((now - start) / 1000);
  const slow = seconds >= 8;
  return (
    <>
      <output className="sr-only">{slow ? "The Sync API has not answered yet." : ""}</output>
      {slow && (
        <div className="flex flex-wrap items-center gap-3">
          <p className="text-foreground-muted text-sm tabular-nums">
            The Sync API has not answered for {plural(seconds, "second", "seconds")}.
          </p>
          <Button
            variant="outline"
            size="sm"
            className={TOUCH}
            onPress={() => {
              setStart(Date.now());
              onRetry();
            }}
          >
            Try again
          </Button>
        </div>
      )}
    </>
  );
}
