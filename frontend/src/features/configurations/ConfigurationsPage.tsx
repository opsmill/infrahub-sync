import {
  Button,
  Card,
  Label,
  Select,
  SelectItem,
  SelectList,
  SelectTrigger,
  Tooltip,
} from "@infrahub/ui";
import { Link, useNavigate } from "@tanstack/react-router";
import {
  type KeyboardEvent,
  type ReactNode,
  type RefObject,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";

import { describeError } from "#/api/queries";
import type { Run } from "#/api/types";
import {
  ArrowRightIcon,
  BranchIcon,
  ChevronRightIcon,
  STATE_TEXT,
  StateIcon,
} from "#/components/icons";
import {
  Bar,
  CLICKABLE,
  CopyCommand,
  EmptyPanel,
  ErrorPanel,
  FilterTabs,
  SearchField,
  SectionHeading,
  STRETCHED,
  StaleNotice,
  textAction,
} from "#/components/page";
import { Stepper, When } from "#/components/run-status";
import { failureDetail, type RunState, stepNames } from "#/domain/run-state";
import type { System } from "#/domain/systems";
import { StartRunDialog, type StartRunTarget } from "#/features/runs/StartRunDialog";
import { useDocumentTitle } from "#/lib/title";

import {
  byUrgency,
  type ConfigurationRow,
  configName,
  defaultBranch,
  type MatchHint,
  matches,
  mayHaveWritten,
  rowExplanation,
  rowSteps,
  useConfigurationRows,
} from "./rows";

export type StateFilter = RunState | "all";

// The rows of a card share its columns through subgrid, so the action column takes the width of
// the widest action in the card and every row stays in line with the column header.
const TABLE =
  "md:grid md:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)_minmax(9rem,max-content)] md:gap-x-6 lg:grid-cols-[minmax(240px,1.15fr)_140px_minmax(0,1fr)_minmax(9rem,max-content)]";
const SUBGRID = "md:col-span-full md:grid md:grid-cols-subgrid";
const ROW = `flex flex-col gap-y-2 px-4 py-3.5 md:items-start ${SUBGRID}`;

// Tooltip triggers sit above the stretched row link for a mouse; on a touch screen a tap on them
// opens the configuration, because touch does not open tooltips.
const LIFT = "pointer-fine:relative pointer-fine:z-10";

/* ---------- row ---------- */

function HostTip({ system }: { system: System }) {
  if (!system.host) return <span>{system.label}</span>;
  return (
    <Tooltip
      nonInteractiveTrigger
      placement="top"
      message={<span className="font-mono">{system.host}</span>}
    >
      <span className={`${LIFT} cursor-default`}>{system.label}</span>
    </Tooltip>
  );
}

function SystemsLine({ row }: { row: ConfigurationRow }) {
  if (row.latestVersion === undefined)
    return <span className="text-subtle">Latest version not known</span>;
  if (row.versionError)
    return <span className="text-subtle">Could not read the configuration package</span>;
  if (!row.source || !row.destination) return <Bar className="my-0.5 h-3.5 w-36" />;
  const branch = defaultBranch(row);
  const fromRun = Boolean(row.run?.destination_branch);
  return (
    <span className="inline-flex max-w-full flex-wrap items-center gap-x-1.5">
      <HostTip system={row.source} />
      <ArrowRightIcon />
      <span className="sr-only">to</span>
      <HostTip system={row.destination} />
      {branch && branch !== "main" && (
        <Tooltip
          nonInteractiveTrigger
          placement="top"
          message={fromRun ? "Branch of the latest run" : "Branch declared in the configuration"}
        >
          <span
            className={`${LIFT} inline-flex min-w-0 max-w-full cursor-default items-center gap-1 font-mono text-[12px] text-subtle`}
          >
            <BranchIcon className="size-3.5 shrink-0" />
            <span className="sr-only">{fromRun ? "latest run on branch" : "on branch"}</span>
            <span className="min-w-0 [overflow-wrap:anywhere]">{branch}</span>
          </span>
        </Tooltip>
      )}
      <span className="inline-flex items-center gap-1.5 whitespace-nowrap">
        <span aria-hidden="true" className="text-subtle">
          ·
        </span>
        <KindsCount kinds={row.kinds} />
      </span>
    </span>
  );
}

const HINT_WORDS: Record<MatchHint["field"], string> = {
  id: "Matches ID ",
  branch: "Matches branch ",
  host: "Matches host ",
  kind: "Maps the kind ",
};

function SearchHint({ hint }: { hint: MatchHint }) {
  return (
    <p className="mt-0.5 text-subtle text-xs [overflow-wrap:anywhere]">
      {HINT_WORDS[hint.field]}
      <code className="font-mono">{hint.value}</code>
    </p>
  );
}

/**
 * When the configuration last wrote to the destination, for rows that wait on a person. "Never" is
 * stated only when every run was read and the latest run cannot have written.
 */
function lastApplied(row: ConfigurationRow, runsTruncated: boolean) {
  const asks = row.state === "failed" || row.state === "review" || row.run?.phase === "abandoned";
  if (!asks) return null;
  const applied = row.lastApplied;
  if (!applied?.finished_at) return runsTruncated || mayHaveWritten(row.run) ? null : "never";
  const branch = applied.destination_branch;
  return {
    iso: applied.finished_at,
    branch: branch && branch !== defaultBranch(row) ? branch : undefined,
  };
}

function LastApplied({ last }: { last: Exclude<ReturnType<typeof lastApplied>, null> }) {
  if (last === "never") return <>Never applied</>;
  if (!last.branch) return <When iso={last.iso} prefix="Last applied " className={LIFT} />;
  return (
    <>
      Last applied to <span className="font-mono">{last.branch}</span>{" "}
      <When iso={last.iso} className={LIFT} />
    </>
  );
}

/** The recorded error classes in monospace, joined by plain words. */
function ErrorClasses({ run, label }: { run?: Run; label: string }) {
  const f = run ? failureDetail(run) : undefined;
  const mono = "font-mono text-[12px]";
  return (
    <>
      <span className="sr-only">Error type recorded by the Sync API: </span>
      {f?.errorType && f.causeType ? (
        <>
          <code className={mono}>{f.errorType}</code>
          <span className="text-subtle"> caused by </span>
          <code className={mono}>{f.causeType}</code>
        </>
      ) : (
        <code className={mono}>{label}</code>
      )}
    </>
  );
}

function Reason({
  row,
  quiet,
  runsTruncated,
}: {
  row: ConfigurationRow;
  quiet: boolean;
  runsTruncated: boolean;
}) {
  const id = `reason-${row.summary.config_id}`;
  if (!row.state) {
    if (row.runUnknown === "pending") {
      return (
        <div id={id} className="flex min-w-0 flex-col gap-1">
          <span className="sr-only">Reading the latest run</span>
          <Bar className="h-[18px] w-52 max-w-full" />
          <Bar className="h-4 w-36" />
        </div>
      );
    }
    return (
      <p id={id} className="min-w-0 text-foreground-muted">
        Latest run not known
      </p>
    );
  }
  const e = rowExplanation(row);
  const last = lastApplied(row, runsTruncated);
  return (
    <div id={id} className="flex min-w-0 flex-col gap-0.5">
      <p
        className={`flex items-start gap-1.5 ${quiet ? "text-foreground-muted" : "text-foreground"}`}
      >
        <StateIcon
          state={row.state}
          className={`mt-[3px] size-3.5 shrink-0 ${STATE_TEXT[row.state]}`}
        />
        <span className="text-pretty">{e.short}</span>
      </p>
      {e.note && (
        <p
          className={`${LIFT} line-clamp-2 select-text pl-5 text-foreground-muted text-xs [overflow-wrap:anywhere] pointer-fine:cursor-text`}
        >
          {e.noteMono ? <ErrorClasses run={row.run} label={e.note} /> : e.note}
        </p>
      )}
      {(e.at || last) && (
        <p className="pl-5 text-subtle text-xs">
          {e.at && <When iso={e.at.iso} prefix={e.at.prefix} className={LIFT} />}
          {e.by && <> by {e.by}</>}
          {/* The space before the dot is the only place the line can break between the two facts. */}
          {e.at && last && (
            <>
              <span className="sr-only">.</span> <span aria-hidden="true">·</span>
              {" "}
            </>
          )}
          {last && <LastApplied last={last} />}
        </p>
      )}
    </div>
  );
}

const ACTION_AREA =
  "before:absolute before:inset-x-0 before:-inset-y-1.5 before:content-[''] active:bg-highlight";
const CHEVRON =
  "size-3.5 transition-transform duration-150 ease-out group-hover:translate-x-0.5 motion-reduce:transition-none";

function RowAction({
  row,
  name,
  onStart,
}: {
  row: ConfigurationRow;
  name: string;
  onStart: (t: StartRunTarget) => void;
}) {
  const id = row.summary.config_id;
  const forName = <span className="sr-only"> for {name}</span>;
  if (!row.state) {
    if (row.runUnknown !== "failed") return null;
    return (
      <Link to="/runs" search={{ config: id }} className={`${textAction(false)} ${ACTION_AREA}`}>
        View runs
        {forName}
        <ChevronRightIcon className={CHEVRON} />
      </Link>
    );
  }
  const action = rowExplanation(row).action;
  const version = row.latestVersion;
  if (action?.target === "run" && row.run) {
    return (
      <Link
        to="/runs/$runId"
        params={{ runId: row.run.run_id }}
        className={`${textAction(action.primary)} ${ACTION_AREA}`}
      >
        {action.label}
        {forName}
        <ChevronRightIcon className={CHEVRON} />
      </Link>
    );
  }
  if (action?.target === "plan" && version !== undefined) {
    return (
      <button
        type="button"
        className={`${textAction(false)} ${ACTION_AREA}`}
        onClick={() =>
          onStart({
            configId: id,
            name,
            version,
            branch: row.prefill?.branch,
            branchFromRunId: row.prefill?.runId,
          })
        }
      >
        {action.label}
        {forName}
        <ChevronRightIcon className={CHEVRON} />
      </button>
    );
  }
  return null;
}

function Row({
  row,
  hint,
  runsTruncated,
  onStart,
}: {
  row: ConfigurationRow;
  hint?: MatchHint;
  runsTruncated: boolean;
  onStart: (t: StartRunTarget) => void;
}) {
  const id = row.summary.config_id;
  const name = configName(row);
  const quiet = row.state === "insync" || row.state === "idle";
  return (
    <li data-config-id={id} className={`${ROW} ${CLICKABLE}`}>
      <div className="min-w-0">
        <Link
          to="/configs/$configId"
          params={{ configId: id }}
          data-stretched=""
          aria-describedby={`reason-${id}`}
          className={`font-medium text-foreground [overflow-wrap:anywhere] decoration-subtle-muted underline-offset-2 hover:underline ${STRETCHED}`}
        >
          {name}
        </Link>
        <div className="mt-0.5 text-[13px] text-foreground-muted">
          <SystemsLine row={row} />
        </div>
        {hint && <SearchHint hint={hint} />}
      </div>
      <div className="hidden lg:block lg:pt-[3px]">
        {row.state && (
          <Stepper steps={rowSteps(row)} names={stepNames(row.run)} quiet={quiet} label={false} />
        )}
      </div>
      <Reason row={row} quiet={quiet} runsTruncated={runsTruncated} />
      <div className="max-md:-mt-1 md:-mt-1.5 md:justify-self-end">
        <RowAction row={row} name={name} onStart={onStart} />
      </div>
    </li>
  );
}

/* ---------- groups ---------- */

const GROUPS: { id: string; title: string; note: string; states: RunState[] }[] = [
  {
    id: "attention",
    title: "Needs your attention",
    note: "Failed runs and plans waiting for review",
    states: ["failed", "review"],
  },
  {
    id: "running",
    title: "Running",
    note: "Runs continue when you leave this page",
    states: ["running"],
  },
  {
    id: "idle",
    title: "No current plan",
    note: "Never planned, abandoned, cancelled, or a newer version not planned yet",
    states: ["idle"],
  },
  {
    id: "insync",
    title: "Applied or no differences",
    note: "The latest run applied its changes or found no differences",
    states: ["insync"],
  },
];

const FILTERS: { id: RunState; label: string }[] = [
  { id: "failed", label: "Failed" },
  { id: "review", label: "Waiting for review" },
  { id: "running", label: "Running" },
  { id: "idle", label: "No current plan" },
  { id: "insync", label: "Applied or no differences" },
];

/** Names the three steps over the stepper column; each dot also carries its name for screen readers. */
function ColumnHeader() {
  return (
    <div
      aria-hidden="true"
      className="hidden border-b px-4 py-2 font-medium text-subtle text-xs lg:col-span-full lg:grid lg:grid-cols-subgrid"
    >
      <span>Configuration</span>
      <span className="relative h-4">
        <span className="absolute left-0">Plan</span>
        <span className="absolute left-1/2 -translate-x-1/2">Verify</span>
        <span className="absolute right-0">Apply</span>
      </span>
      <span>Latest run</span>
      <span />
    </div>
  );
}

function Table({ children }: { children: ReactNode }) {
  return (
    <Card className="overflow-hidden">
      <div className={TABLE}>
        <ColumnHeader />
        <ul className={`divide-y ${SUBGRID}`}>{children}</ul>
      </div>
    </Card>
  );
}

function Group({
  id,
  title,
  note,
  children,
  count,
}: {
  id: string;
  title: string;
  note?: ReactNode;
  count: number;
  children: ReactNode;
}) {
  return (
    <section aria-labelledby={`group-${id}`} className="flex flex-col gap-2">
      <SectionHeading id={`group-${id}`} title={title} count={count}>
        {note}
      </SectionHeading>
      <Table>{children}</Table>
    </section>
  );
}

// The same sizes as the loaded toolbar: the search field is taller below md, where its text is
// 16 px; the state filter is a select below sm.
const SEARCH_BOX = "h-10 w-full rounded-xl max-md:h-[42px] sm:w-96";
const FILTER_BAR = "hidden h-10 w-[490px] max-w-full rounded-xl pointer-coarse:h-[50px] sm:block";

function Skeleton() {
  return (
    <div className="flex flex-col gap-6" aria-hidden="true">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Bar className={SEARCH_BOX} />
        <Bar className={FILTER_BAR} />
        <div className="w-full sm:hidden">
          <Bar className="my-0.5 h-4 w-36" />
          <Bar className="h-10 w-full rounded-lg" />
        </div>
      </div>
      <div className="flex flex-col gap-2">
        <div className="flex h-5 items-center gap-2">
          <Bar className="h-4 w-40" />
          <Bar className="h-5 w-6 rounded-full" />
        </div>
        <Table>
          {Array.from({ length: 5 }, (_, i) => (
            <li key={i} className={ROW}>
              <div className="flex flex-col gap-1">
                <Bar className="h-[18px] w-40" />
                <Bar className="h-[18px] w-28" />
              </div>
              <div className="hidden h-4 items-center pt-[3px] lg:flex">
                <Bar className="h-1 w-full" />
              </div>
              <div className="flex flex-col gap-1">
                <Bar className="h-[18px] w-52 max-w-full" />
                <Bar className="ml-5 h-4 w-36" />
              </div>
              <Bar className="h-8 w-28 md:justify-self-end" />
            </li>
          ))}
        </Table>
      </div>
    </div>
  );
}

/* ---------- focus and announcements ---------- */

/** Keeps keyboard focus on a row's link when a refresh moves the row to another group. */
function useKeepRowFocus(container: RefObject<HTMLDivElement | null>, shown: unknown) {
  const focused = useRef<string | null>(null);
  useEffect(() => {
    const el = container.current;
    if (!el) return;
    const onFocusIn = (e: FocusEvent) => {
      const li = (e.target as HTMLElement).closest<HTMLElement>("[data-config-id]");
      focused.current = li?.dataset.configId ?? null;
    };
    const onFocusOut = (e: FocusEvent) => {
      const next = e.relatedTarget as Node | null;
      if (next && el.contains(next)) return;
      const target = e.target as Node;
      // A row that moves to another group is removed with focus inside; keep its ID for the move.
      window.setTimeout(() => {
        if (target.isConnected || next) focused.current = null;
      });
    };
    el.addEventListener("focusin", onFocusIn);
    el.addEventListener("focusout", onFocusOut);
    return () => {
      el.removeEventListener("focusin", onFocusIn);
      el.removeEventListener("focusout", onFocusOut);
    };
  }, [container]);
  useLayoutEffect(() => {
    const id = focused.current;
    if (!id || document.activeElement !== document.body) return;
    container.current
      ?.querySelector<HTMLAnchorElement>(`[data-config-id="${CSS.escape(id)}"] a[data-stretched]`)
      ?.focus({ preventScroll: true });
  }, [container, shown]);
}

/** One message for screen readers when a refresh changes the state of a configuration. */
function useStateAnnouncement(rows: ConfigurationRow[], ready: boolean): string {
  const previous = useRef<Map<string, RunState> | null>(null);
  const [message, setMessage] = useState("");
  const entries = JSON.stringify(
    rows.flatMap((r) =>
      r.state
        ? [[r.summary.config_id, r.state, `${configName(r)}: ${rowExplanation(r).short}.`]]
        : []
    )
  );
  useEffect(() => {
    if (!ready) return;
    const list = JSON.parse(entries) as [string, RunState, string][];
    const before = previous.current;
    previous.current = new Map(list.map(([id, state]) => [id, state]));
    if (!before) return;
    const changed = list.filter(([id, state]) => before.has(id) && before.get(id) !== state);
    if (changed.length > 0) setMessage(changed.map(([, , text]) => text).join(" "));
  }, [entries, ready]);
  return message;
}

/* ---------- page ---------- */

// Below md the input text is 16 px, so the full placeholder no longer fits the field.
const NARROW = "(max-width: 767px)";
const subscribeNarrow = (onChange: () => void) => {
  const query = window.matchMedia(NARROW);
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
};
const useNarrow = () =>
  useSyncExternalStore(subscribeNarrow, () => window.matchMedia(NARROW).matches);

const UNKNOWN_GROUP = {
  id: "unknown",
  title: "Latest run not known",
  note: "The Sync API did not return the latest run of these configurations",
};

export function ConfigurationsPage({
  query,
  state,
  onQueryChange,
  onStateChange,
}: {
  query: string;
  state: StateFilter;
  onQueryChange: (q: string) => void;
  onStateChange: (s: StateFilter) => void;
}) {
  const {
    rows,
    isPending,
    error,
    refreshError,
    updatedAt,
    settled,
    failureCount,
    failureReason,
    runsTruncated,
    configsTruncated,
    refetch,
  } = useConfigurationRows();
  const navigate = useNavigate();
  const [starting, setStarting] = useState<StartRunTarget | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const filterRef = useRef<HTMLDivElement>(null);
  const focusFilter = useRef(false);
  const narrow = useNarrow();
  const groupsRef = useRef<HTMLDivElement>(null);

  const attention = rows.filter((r) => r.state === "failed" || r.state === "review").length;
  useDocumentTitle(attention > 0 ? `(${attention}) Configurations` : "Configurations");

  const searched = rows.flatMap((row) => {
    const m = matches(row, query);
    return m ? [{ row, hint: m.hint }] : [];
  });
  searched.sort((a, b) => byUrgency(a.row, b.row));
  const shown = state === "all" ? searched : searched.filter((s) => s.row.state === state);
  const countOf = (s: RunState) => searched.filter((x) => x.row.state === s).length;
  const filterOptions: { id: StateFilter; label: string; count: number }[] = [
    { id: "all", label: "All", count: searched.length },
    ...FILTERS.map((f) => ({ ...f, count: countOf(f.id) })).filter(
      (f) => f.count > 0 || f.id === state
    ),
  ];
  // With no state to choose, the filter keeps its place so the content below does not move.
  const nothingToFilter = filterOptions.length === 1 && state === "all";

  useKeepRowFocus(groupsRef, shown);
  const announcement = useStateAnnouncement(rows, settled);

  // "Show all" unmounts the empty panel that holds it; focus moves to the filter that now shows all.
  useEffect(() => {
    if (!focusFilter.current || state !== "all") return;
    focusFilter.current = false;
    const candidates = filterRef.current?.querySelectorAll<HTMLElement>(
      'button[aria-pressed="true"], button[aria-haspopup]'
    );
    [...(candidates ?? [])].find((el) => el.checkVisibility())?.focus();
  }, [state]);

  const retrying = isPending && failureCount > 0;
  const onSearchKey = (e: KeyboardEvent<HTMLElement>) => {
    if (e.target !== searchRef.current) return;
    if (e.key === "ArrowDown") {
      const first = groupsRef.current?.querySelector<HTMLAnchorElement>("a[data-stretched]");
      if (first) {
        e.preventDefault();
        first.focus();
      }
    } else if (e.key === "Enter" && shown.length === 1) {
      e.preventDefault();
      navigate({ to: "/configs/$configId", params: { configId: shown[0].row.summary.config_id } });
    }
  };
  const clearSearch = () => {
    onQueryChange("");
    searchRef.current?.focus();
  };
  const showAll = () => {
    focusFilter.current = true;
    onStateChange("all");
  };

  const filterTitle = FILTERS.find((f) => f.id === state)?.label;
  const groups = [
    ...GROUPS.map((g) => ({
      id: g.id,
      title: filterTitle ?? g.title,
      note: filterTitle ? undefined : g.note,
      items: shown.filter((s) => s.row.state !== undefined && g.states.includes(s.row.state)),
    })),
    { ...UNKNOWN_GROUP, items: shown.filter((s) => s.row.state === undefined) },
  ];

  return (
    <>
      <header className="flex flex-col gap-1.5 pt-8 pb-6">
        <h1 className="text-balance font-semibold text-2xl tracking-tight">Sync configurations</h1>
        {retrying ? (
          <output className="inline-flex h-5 items-center gap-2 text-subtle">
            <StateIcon state="running" className="size-3.5 shrink-0" />
            {describeError(failureReason).kind === "no-answer"
              ? "The Sync API did not answer. Trying again…"
              : "The Sync API returned an error. Trying again…"}
          </output>
        ) : (
          <p className="text-pretty text-foreground-muted">
            Each configuration, grouped by the state of its latest run.
          </p>
        )}
      </header>

      <output aria-live="polite" className="sr-only">
        {announcement}
      </output>

      <div ref={groupsRef} aria-busy={isPending}>
        {isPending ? (
          <>
            <output className="sr-only">Loading sync configurations</output>
            <Skeleton />
          </>
        ) : error ? (
          <ErrorPanel error={error} onRetry={refetch} />
        ) : rows.length === 0 ? (
          <EmptyPanel title="No sync configurations yet">
            <p className="max-w-[52ch] text-pretty text-foreground-muted">
              A configuration says what to read from a source system, such as NetBox, and how to
              write it to Infrahub. Register one from a configuration package:
            </p>
            <CopyCommand command="infrahub-sync configs register package.yml" />
          </EmptyPanel>
        ) : (
          <>
            {refreshError && (
              <StaleNotice error={refreshError} updatedAt={updatedAt} onRetry={refetch} />
            )}
            <div className="flex flex-col gap-6">
              <div className="flex flex-col gap-2">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <search className="contents sm:[&>div]:w-96" onKeyDown={onSearchKey}>
                    <SearchField
                      value={query}
                      onChange={onQueryChange}
                      inputRef={searchRef}
                      count={searched.length}
                      placeholder={
                        narrow
                          ? "Name, system, host, branch or kind"
                          : "Search by name, system, host, branch or kind"
                      }
                      label="Search configurations by name, ID, system, host, branch or mapped kind"
                      noun={["configuration matches", "configurations match"]}
                    />
                  </search>
                  <div ref={filterRef} className={`contents ${nothingToFilter ? "invisible" : ""}`}>
                    {/* Buttons 2 px taller than the shared filter's make it as tall as the search field. */}
                    <div className="hidden sm:contents pointer-fine:[&_button]:h-[34px]">
                      <FilterTabs
                        label="Filter by state of the latest run"
                        options={filterOptions}
                        value={state}
                        onChange={onStateChange}
                      />
                    </div>
                    <StateSelect options={filterOptions} value={state} onChange={onStateChange} />
                  </div>
                </div>
                {configsTruncated && (
                  <p className="text-subtle text-xs">
                    Showing the 200 most recently registered configurations. Search and the filter
                    cover only these.
                  </p>
                )}
              </div>
              {shown.length === 0 ? (
                <NoMatches
                  query={query}
                  state={state}
                  searchedCount={searched.length}
                  onClearSearch={clearSearch}
                  onShowAll={showAll}
                  onClearBoth={() => {
                    onStateChange("all");
                    clearSearch();
                  }}
                />
              ) : (
                <div className="flex flex-col gap-8">
                  {groups.map((g) =>
                    g.items.length === 0 ? null : (
                      <Group
                        key={g.id}
                        id={g.id}
                        title={g.title}
                        note={g.note}
                        count={g.items.length}
                      >
                        {g.items.map(({ row, hint }) => (
                          <Row
                            key={row.summary.config_id}
                            row={row}
                            hint={hint}
                            runsTruncated={runsTruncated}
                            onStart={setStarting}
                          />
                        ))}
                      </Group>
                    )
                  )}
                </div>
              )}
            </div>
          </>
        )}
      </div>
      {starting && <StartRunDialog target={starting} onClose={() => setStarting(null)} />}
    </>
  );
}

/** The state filter on phones, where the row of buttons does not fit. */
function StateSelect({
  options,
  value,
  onChange,
}: {
  options: { id: StateFilter; label: string; count: number }[];
  value: StateFilter;
  onChange: (s: StateFilter) => void;
}) {
  return (
    <Select
      className="w-full sm:hidden"
      selectedKey={value}
      onSelectionChange={(k) => {
        if (k != null) onChange(k as StateFilter);
      }}
    >
      <Label className="text-subtle text-xs">State of the latest run</Label>
      <SelectTrigger size="md" />
      <SelectList className="bg-content">
        {options.map((o) => (
          <SelectItem key={o.id} id={o.id} textValue={o.label}>
            {o.id === "all" ? "All configurations" : o.label} ({o.count})
          </SelectItem>
        ))}
      </SelectList>
    </Select>
  );
}

function NoMatches({
  query,
  state,
  searchedCount,
  onClearSearch,
  onShowAll,
  onClearBoth,
}: {
  query: string;
  state: StateFilter;
  /** Configurations that match the search in any state. */
  searchedCount: number;
  onClearSearch: () => void;
  onShowAll: () => void;
  onClearBoth: () => void;
}) {
  const q = query.trim();
  const filtering = state !== "all";
  if (!q) {
    return (
      <EmptyPanel title="No configurations are in this state now">
        <Button variant="outline" size="sm" onPress={onShowAll}>
          Show all
        </Button>
      </EmptyPanel>
    );
  }
  if (filtering && searchedCount > 0) {
    return (
      <EmptyPanel title={`No configurations in this state match “${q}”`}>
        <p className="text-pretty text-foreground-muted">
          {searchedCount} {searchedCount === 1 ? "configuration matches" : "configurations match"}{" "}
          in other states.
        </p>
        <div className="flex flex-wrap justify-center gap-2">
          <Button variant="outline" size="sm" onPress={onShowAll}>
            Show all states
          </Button>
          <Button variant="ghost" size="sm" onPress={onClearBoth}>
            Clear search and filter
          </Button>
        </div>
      </EmptyPanel>
    );
  }
  return (
    <EmptyPanel title={`No configurations match “${q}”`}>
      <p className="text-pretty text-foreground-muted">
        Search looks at names, IDs, systems, hosts, branches and kinds.
      </p>
      <Button variant="outline" size="sm" onPress={filtering ? onClearBoth : onClearSearch}>
        {filtering ? "Clear search and filter" : "Clear search"}
      </Button>
    </EmptyPanel>
  );
}

function KindsCount({ kinds }: { kinds: string[] }) {
  const label = `${kinds.length} ${kinds.length === 1 ? "kind" : "kinds"}`;
  if (kinds.length === 0) return <span>{label}</span>;
  const shown = kinds.slice(0, 14);
  return (
    <Tooltip
      nonInteractiveTrigger
      placement="top"
      message={
        <span className="block max-w-72 font-mono text-[11px] leading-5">
          {shown.join(", ")}
          {kinds.length > shown.length && ` and ${kinds.length - shown.length} more`}
        </span>
      }
    >
      <span
        className={`${LIFT} cursor-default tabular-nums underline decoration-subtle-muted decoration-dotted underline-offset-4`}
      >
        {label}
      </span>
    </Tooltip>
  );
}
