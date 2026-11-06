import { Card } from "@infrahub/ui";
import { Link } from "@tanstack/react-router";

import type { Run } from "#/api/types";
import { Bar, CLICKABLE, STRETCHED } from "#/components/page";
import { BranchCode, CHANGE_TONE, StateLabel, When } from "#/components/run-status";
import {
  type Counts as ChangeCounts,
  countsSentence,
  failureDetail,
  fmt,
  type ListRunState,
  runCounts,
} from "#/domain/run-state";
import { formatAbsolute, formatRelative, useNow } from "#/lib/time";

const deletesListed = (n: number) => `${n === 1 ? "delete" : "deletes"} listed, not applied`;

const TENSE_WORDS = {
  plan: { create: "to create", update: "to update" },
  done: { create: "created", update: "updated" },
} as const;

/**
 * Counts of changes as +created ~updated −deletes listed, with words for screen readers. `inList`
 * spells out a zero, because the phone list has no "Changes" header.
 */
function Counts({
  run,
  counts: c,
  inList = false,
}: {
  run: Run;
  counts: ChangeCounts | undefined;
  inList?: boolean;
}) {
  if (!c) {
    return (
      <>
        <span aria-hidden="true" className="text-subtle">
          —
        </span>
        <span className="sr-only">No counts recorded</span>
      </>
    );
  }
  if (c.total === 0) {
    return <span className="text-subtle text-xs tabular-nums">{inList ? "0 changes" : "0"}</span>;
  }
  if (c.create + c.update === 0) {
    return (
      <span
        className="inline-flex flex-wrap items-baseline gap-x-2 text-subtle text-xs"
        title={`${fmt(c.delete)} ${deletesListed(c.delete)}`}
      >
        <span className="font-mono tabular-nums">
          {CHANGE_TONE.delete.sign}
          {fmt(c.delete)}
          <span className="sr-only"> {deletesListed(c.delete)}</span>
        </span>
        <span className="whitespace-nowrap">Nothing to write</span>
      </span>
    );
  }
  const tense = run.phase === "applied" ? "done" : "plan";
  const words = TENSE_WORDS[tense];
  return (
    <span
      className="inline-flex flex-wrap gap-x-2 font-mono text-xs tabular-nums"
      title={countsSentence(c, tense)}
    >
      {(["create", "update"] as const).map(
        (k) =>
          c[k] > 0 && (
            <span key={k} className={CHANGE_TONE[k].text}>
              {CHANGE_TONE[k].sign}
              {fmt(c[k])}
              <span className="sr-only"> {words[k]}</span>
            </span>
          )
      )}
      {c.delete > 0 && (
        <span className={CHANGE_TONE.delete.text}>
          {CHANGE_TONE.delete.sign}
          {fmt(c.delete)}
          <span className="sr-only"> {deletesListed(c.delete)}</span>
        </span>
      )}
    </span>
  );
}

/** The event that `finished_at` dates. */
function finishVerb(run: Run): string {
  if (run.outcome === "no-change") return "Checked";
  if (run.phase === "planned") return "Planned";
  if (run.phase === "applied") return run.operation === "sync" ? "Synced" : "Applied";
  if (run.phase.endsWith("-failed")) return "Failed";
  if (run.phase === "interrupted" || run.phase.endsWith("-interrupted")) return "Interrupted";
  if (run.phase === "abandoned") return "Abandoned";
  if (run.phase === "cancelled") return "Cancelled";
  return "Finished";
}

/**
 * An accepted run reads "Started", not a running time: `GET /runs` items carry no executions, so the
 * list cannot tell a run a worker is executing from one still waiting for a worker.
 */
function TimeLine({ run }: { run: Run }) {
  if (run.finished_at) {
    return (
      <span>
        {finishVerb(run)} <When iso={run.finished_at} />
      </span>
    );
  }
  return (
    <span>
      Started <When iso={run.started_at} />
    </span>
  );
}

function ActorLine({ run, className = "" }: { run: Run; className?: string }) {
  if (!run.actor) return null;
  return (
    <span
      className={`text-subtle text-xs ${className}`}
      title={`Started ${formatAbsolute(run.started_at)}`}
    >
      Started by {run.actor}
    </span>
  );
}

/** The one line under the state: a possible write, a known failure cause, or the list note. */
function StateNote({
  run,
  state,
  wide = false,
}: {
  run: Run;
  state: ListRunState;
  wide?: boolean;
}) {
  const f = state.state === "failed" ? failureDetail(run) : undefined;
  const line = `mt-0.5 block text-pretty break-words text-xs ${wide ? "" : "line-clamp-2"}`;
  if (f?.ambiguous)
    return <span className={`${line} text-danger`}>May have written to the destination</span>;
  if (f?.known) {
    return (
      <span
        className={`${line} ${state.quiet ? "text-subtle" : "text-foreground"}`}
        title={f.cause ?? f.sentence}
      >
        {f.sentence}
      </span>
    );
  }
  if (state.note) {
    return (
      <span className={`${line} text-subtle`} title={state.note}>
        {state.note}
      </span>
    );
  }
  return null;
}

const hasStateNote = (run: Run, state: ListRunState) => {
  if (state.note) return true;
  if (state.state !== "failed") return false;
  const f = failureDetail(run);
  return Boolean(f?.ambiguous || f?.known);
};

const Dot = () => <span aria-hidden="true">·</span>;

/**
 * Version, the selectable run ID and the sync note, on the line under the run's name. The ID comes
 * before the note so that it is never cut and lines up from row to row; the line wraps instead.
 * On touch the ID stays under the stretched link, so a tap anywhere opens the run.
 */
function IdLine({ run, withVersion }: { run: Run; withVersion: boolean }) {
  const version = withVersion && run.registry_version != null;
  const sync = run.operation === "sync";
  return (
    <div className="mt-0.5 flex min-w-0 flex-wrap items-center gap-x-1 text-subtle text-xs">
      {version && (
        <>
          <span className="whitespace-nowrap">Version {run.registry_version}</span>
          <Dot />
        </>
      )}
      <span className="pointer-fine:relative pointer-fine:z-10 cursor-text select-all whitespace-nowrap font-mono">
        {run.run_id}
      </span>
      {sync && (
        <>
          <Dot />
          <span className="whitespace-nowrap">Sync run, no review step</span>
        </>
      )}
    </div>
  );
}

interface RowProps {
  run: Run;
  state: ListRunState;
  name?: string;
  namesPending: boolean;
  now: number;
  className?: string;
}

/** The run's link, stretched over its row; its accessible name adds the version, state and start. */
function RunLink({ run, state, name, namesPending, now, className = "line-clamp-2" }: RowProps) {
  const withConfig = name !== undefined;
  const suffix = [
    withConfig && run.registry_version != null && `version ${run.registry_version}`,
    state.label,
    `started ${formatRelative(run.started_at, now)}`,
  ]
    .filter(Boolean)
    .join(", ");
  if (withConfig && namesPending) {
    return (
      <Link
        to="/runs/$runId"
        params={{ runId: run.run_id }}
        data-stretched=""
        data-run-id={run.run_id}
        className={`block py-0.5 ${STRETCHED} ${className}`}
      >
        <Bar className="h-3.5 w-40" />
        <span className="sr-only">
          {name}, {suffix}
        </span>
      </Link>
    );
  }
  const text = withConfig
    ? name
    : run.registry_version != null
      ? `Version ${run.registry_version}`
      : run.run_id;
  return (
    <Link
      to="/runs/$runId"
      params={{ runId: run.run_id }}
      data-stretched=""
      data-run-id={run.run_id}
      title={withConfig ? name : undefined}
      className={`break-words font-medium text-foreground ${STRETCHED} ${className}`}
    >
      {text}
      <span className="sr-only">, {suffix}</span>
    </Link>
  );
}

/** The branch chip truncates inside its border, so the ellipsis and the border stay visible. */
const TRUNCATE = "max-w-full truncate align-top";

function Branch({ run }: { run: Run }) {
  if (!run.destination_branch) {
    return (
      <>
        <span aria-hidden="true" className="text-subtle">
          —
        </span>
        <span className="sr-only">No branch</span>
      </>
    );
  }
  return (
    <div className="flex h-5 items-center">
      <BranchCode
        branch={run.destination_branch}
        title={run.destination_branch}
        className={TRUNCATE}
      />
    </div>
  );
}

/**
 * The muted tint is painted over an opaque `bg-content`: in dark mode `--content-muted` is a
 * translucent white, and rows would show through the sticky header.
 */
const TH =
  "sticky top-[49px] z-20 h-9 bg-content bg-[linear-gradient(var(--content-muted),var(--content-muted))] px-4 font-medium text-subtle text-xs shadow-[inset_0_-1px_0_var(--color-border)]";
const TD = "px-4 py-3 align-top";

/** A flat surface: the Card gradient would tint the header and first row as if they were selected. */
const CARD = "overflow-clip bg-content bg-none";

/** Column widths, so names wrap at most once and the columns stay put while rows load. */
function Columns({ withConfig }: { withConfig: boolean }) {
  const widths = withConfig
    ? ["w-[28%]", "w-[25%]", "w-[13%]", "w-[15%]", "w-[19%]"]
    : ["w-[20%]", "w-[30%]", "w-[14%]", "w-[16%]", "w-[20%]"];
  return (
    <colgroup>
      {widths.map((w, i) => (
        <col key={i} className={w} />
      ))}
    </colgroup>
  );
}

function Head({ withConfig }: { withConfig: boolean }) {
  return (
    <thead>
      <tr>
        <th scope="col" className={TH}>
          {withConfig ? "Configuration" : "Run"}
        </th>
        <th scope="col" className={TH}>
          State
        </th>
        <th scope="col" className={TH}>
          Changes
        </th>
        <th scope="col" className={TH}>
          Branch
        </th>
        <th scope="col" className={`${TH} text-right`}>
          Time
        </th>
      </tr>
    </thead>
  );
}

/**
 * The configuration's name, or its ID while names are missing. Without `config_id`, the ID is the
 * part of `configuration_reference` before "@": the Sync service writes `{config_id}@{version}`.
 */
function configLabel(run: Run, names: Map<string, string>): string {
  const id = run.config_id ?? run.configuration_reference.split("@")[0];
  return names.get(id) ?? id;
}

const TABLE = "w-full table-fixed border-collapse text-left max-lg:hidden";
const LIST = "divide-y lg:hidden";

function Legend() {
  return (
    <p className="text-subtle text-xs">
      {CHANGE_TONE.create.sign} nodes created or to create · {CHANGE_TONE.update.sign} nodes updated
      or to update · {CHANGE_TONE.delete.sign} deletes listed, not applied
    </p>
  );
}

/**
 * Runs, newest first: a table from the `lg` breakpoint, a list below it. With `configNames`, the
 * first column names each run's configuration; without it, the run's version.
 */
export function RunsTable({
  runs,
  states,
  label,
  configNames,
  namesPending = false,
}: {
  runs: Run[];
  /** From listRunStates(); RunsTable never calls runState itself. */
  states: Map<string, ListRunState>;
  /** The table's and the phone list's accessible name. */
  label: string;
  /** Present on /runs without a configuration filter: the first column shows the configuration name. */
  configNames?: Map<string, string>;
  /** True while GET /configs is pending; the name cell shows a placeholder bar. */
  namesPending?: boolean;
}) {
  const now = useNow();
  const withConfig = configNames !== undefined;
  const rows = runs.flatMap((run) => {
    const state = states.get(run.run_id);
    if (!state) return [];
    const name = configNames ? configLabel(run, configNames) : undefined;
    return [{ run, state, name, counts: runCounts(run) }];
  });

  return (
    <div className="flex flex-col gap-3">
      <Card className={CARD}>
        <table aria-label={label} className={TABLE}>
          <Columns withConfig={withConfig} />
          <Head withConfig={withConfig} />
          <tbody className="divide-y">
            {rows.map(({ run, state, name, counts }) => (
              <tr key={run.run_id} className={CLICKABLE}>
                <td className={TD}>
                  <RunLink
                    run={run}
                    state={state}
                    name={name}
                    namesPending={namesPending}
                    now={now}
                    className="line-clamp-2 lg:scroll-mt-12"
                  />
                  <IdLine run={run} withVersion={withConfig} />
                </td>
                <td className={TD}>
                  <StateLabel state={state.state} label={state.label} quiet={state.quiet} />
                  <StateNote run={run} state={state} />
                </td>
                <td className={`${TD} leading-5`}>
                  <Counts run={run} counts={counts} />
                </td>
                <td className={TD}>
                  <Branch run={run} />
                </td>
                <td className={`${TD} text-right text-foreground-muted`}>
                  <TimeLine run={run} />
                  <ActorLine run={run} className="block" />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <ul aria-label={label} className={LIST}>
          {rows.map(({ run, state, name, counts }) => (
            <li
              key={run.run_id}
              className={`${CLICKABLE} grid grid-cols-1 items-start gap-x-3 gap-y-1 px-4 py-3 sm:grid-cols-[minmax(0,1fr)_auto]`}
            >
              <RunLink
                run={run}
                state={state}
                name={name}
                namesPending={namesPending}
                now={now}
                className="line-clamp-3"
              />
              <StateLabel
                state={state.state}
                label={state.label}
                quiet={state.quiet}
                className="text-sm"
              />
              {hasStateNote(run, state) && (
                <div className="-mt-0.5 min-w-0 sm:col-span-2">
                  <StateNote run={run} state={state} wide />
                </div>
              )}
              <div className="min-w-0 sm:col-span-2">
                <IdLine run={run} withVersion={withConfig} />
              </div>
              <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-xs sm:col-span-2">
                {counts && <Counts run={run} counts={counts} inList />}
                {run.destination_branch && (
                  <BranchCode
                    branch={run.destination_branch}
                    title={run.destination_branch}
                    className={TRUNCATE}
                  />
                )}
                <span className="text-subtle">
                  <TimeLine run={run} />
                </span>
              </div>
            </li>
          ))}
        </ul>
      </Card>
      <Legend />
    </div>
  );
}

/** The loaded table's shape, so nothing moves when the runs arrive. */
export function RunsTableSkeleton({
  rows = 8,
  withConfig,
}: {
  rows?: number;
  withConfig: boolean;
}) {
  return (
    <div aria-busy="true">
      <output className="sr-only">Loading runs</output>
      <Card className={CARD}>
        <table aria-hidden="true" className={TABLE}>
          <Columns withConfig={withConfig} />
          <Head withConfig={withConfig} />
          <tbody className="divide-y">
            {Array.from({ length: rows }, (_, i) => (
              <tr key={i} className="h-16">
                <td className={TD}>
                  <Bar className="h-3.5 w-40 max-w-full" />
                  <Bar className="mt-1.5 h-3 w-56 max-w-full" />
                </td>
                <td className={TD}>
                  <Bar className="h-3.5 w-20" />
                </td>
                <td className={TD}>
                  <Bar className="h-3 w-10" />
                </td>
                <td className={TD}>
                  <Bar className="h-4 w-20" />
                </td>
                <td className={TD}>
                  <Bar className="ml-auto h-3.5 w-28 max-w-full" />
                  <Bar className="mt-1.5 ml-auto h-3 w-20" />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <ul aria-hidden="true" className={LIST}>
          {Array.from({ length: 6 }, (_, i) => (
            <li key={i} className="flex flex-col gap-2 px-4 py-3">
              <div className="flex items-center justify-between gap-3">
                <Bar className="h-3.5 w-40" />
                <Bar className="h-3.5 w-20" />
              </div>
              <Bar className="h-3 w-56 max-w-full" />
              <Bar className="h-3 w-32" />
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}
