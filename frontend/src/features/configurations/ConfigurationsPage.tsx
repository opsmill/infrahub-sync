import { Card, Tooltip } from "@infrahub/ui";
import { Link } from "@tanstack/react-router";
import { type ReactNode, useMemo, useState } from "react";

import { ArrowRightIcon, ChevronRightIcon, STATE_TEXT, StateIcon } from "#/components/icons";
import {
  Bar,
  CLICKABLE,
  CopyCommand,
  EmptyPanel,
  ErrorPanel,
  SearchField,
  SectionHeading,
  STRETCHED,
  textAction,
} from "#/components/page";
import { BranchCode, Stepper, When } from "#/components/run-status";
import type { RunState } from "#/domain/run-state";
import { StartRunDialog, type StartRunTarget } from "#/features/runs/StartRunDialog";

import {
  type ConfigurationRow,
  defaultBranch,
  matches,
  rowExplanation,
  rowSteps,
  useConfigurationRows,
} from "./rows";

const GRID =
  "md:grid md:grid-cols-[minmax(220px,1fr)_140px_minmax(0,1.4fr)_170px] md:items-center md:gap-x-8";

export const configName = (row: Pick<ConfigurationRow, "summary">) =>
  row.summary.name ?? row.summary.config_id;

/* ---------- row ---------- */

export function SystemsLine({ row }: { row: ConfigurationRow }) {
  if (row.versionError) return <span className="text-subtle">Package unavailable</span>;
  if (!row.source || !row.destination) return <Bar className="mt-1 h-3.5 w-36" />;
  const branch = defaultBranch(row);
  return (
    <span className="inline-flex flex-wrap items-center gap-x-1.5">
      <span title={row.source.host}>{row.source.label}</span>
      <ArrowRightIcon />
      <span className="sr-only">to</span>
      <span title={row.destination.host}>{row.destination.label}</span>
      {branch && branch !== "main" && (
        <>
          <span className="text-subtle">on</span>
          <BranchCode branch={branch} />
        </>
      )}
    </span>
  );
}

function Reason({ row, quiet }: { row: ConfigurationRow; quiet: boolean }) {
  const e = rowExplanation(row);
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <p
        className={`flex items-start gap-1.5 ${quiet ? "text-foreground-muted" : `font-medium ${STATE_TEXT[row.state]}`}`}
      >
        <StateIcon
          state={row.state}
          className={`mt-[3px] size-3.5 shrink-0 ${quiet ? STATE_TEXT[row.state] : ""}`}
        />
        <span className="text-pretty">{e.short}</span>
      </p>
      {(e.note || e.at) && (
        <p
          className="line-clamp-2 pl-5 text-subtle text-xs [overflow-wrap:anywhere]"
          title={e.note}
        >
          {e.note}
          {e.note && e.at && <span aria-hidden="true"> · </span>}
          {e.at && <When iso={e.at.iso} prefix={e.at.prefix} />}
        </p>
      )}
    </div>
  );
}

function Row({ row, onStart }: { row: ConfigurationRow; onStart: (t: StartRunTarget) => void }) {
  const quiet = row.state === "insync" || row.state === "idle";
  const action = rowExplanation(row).action;
  return (
    <li className={`flex flex-col gap-2.5 px-4 py-3.5 ${GRID} ${CLICKABLE}`}>
      <div className="min-w-0">
        <Link
          to="/configs/$configId"
          params={{ configId: row.summary.config_id }}
          data-stretched=""
          className={`font-medium text-foreground [overflow-wrap:anywhere] decoration-subtle-muted underline-offset-2 hover:underline ${STRETCHED}`}
        >
          {configName(row)}
        </Link>
        <div className="text-[13px] text-foreground-muted">
          <SystemsLine row={row} />
        </div>
      </div>
      <Stepper steps={rowSteps(row)} quiet={quiet} />
      <Reason row={row} quiet={quiet} />
      <div className="md:justify-self-end">
        {action?.target === "run" && row.run && (
          <Link
            to="/runs/$runId"
            params={{ runId: row.run.run_id }}
            className={textAction(action.primary)}
          >
            {action.label}
            <ChevronRightIcon className="size-3.5 transition-transform duration-150 ease-out group-hover:translate-x-0.5 motion-reduce:transition-none" />
          </Link>
        )}
        {action?.target === "plan" && (
          <button
            type="button"
            className={textAction(false)}
            onClick={() =>
              onStart({
                configId: row.summary.config_id,
                name: configName(row),
                version: row.latestVersion,
                branch: defaultBranch(row),
              })
            }
          >
            {action.label}
            <ChevronRightIcon className="size-3.5 transition-transform duration-150 ease-out group-hover:translate-x-0.5 motion-reduce:transition-none" />
          </button>
        )}
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
    note: "Never planned, abandoned, or a newer version not planned yet",
    states: ["idle"],
  },
  {
    id: "insync",
    title: "In sync",
    note: "The latest run applied its changes or found none",
    states: ["insync"],
  },
];

function Group({
  id,
  title,
  note,
  rows,
  onStart,
}: {
  id: string;
  title: string;
  note: ReactNode;
  rows: ConfigurationRow[];
  onStart: (t: StartRunTarget) => void;
}) {
  if (rows.length === 0) return null;
  return (
    <section aria-labelledby={`group-${id}`} className="flex flex-col gap-2">
      <SectionHeading id={`group-${id}`} title={title} count={rows.length}>
        {note}
      </SectionHeading>
      <Card className="overflow-hidden">
        <ul className="divide-y">
          {rows.map((r) => (
            <Row key={r.summary.config_id} row={r} onStart={onStart} />
          ))}
        </ul>
      </Card>
    </section>
  );
}

/* ---------- header ---------- */

const SUMMARY: { state: RunState; label: string }[] = [
  { state: "failed", label: "failed" },
  { state: "review", label: "waiting for review" },
  { state: "running", label: "running" },
  { state: "idle", label: "no current plan" },
  { state: "insync", label: "in sync" },
];

function Summary({ rows }: { rows: ConfigurationRow[] }) {
  const parts = SUMMARY.map((s) => ({
    ...s,
    n: rows.filter((r) => r.state === s.state).length,
  })).filter((s) => s.n > 0);
  return (
    <ul
      className="flex flex-wrap items-center gap-x-4 gap-y-1 text-foreground-muted tabular-nums"
      aria-label="Latest run of each configuration"
    >
      {parts.map((s) => (
        <li key={s.state} className="inline-flex items-center gap-1.5">
          <StateIcon state={s.state} className={`size-3.5 ${STATE_TEXT[s.state]}`} />
          <span>
            <span className="font-semibold text-foreground">{s.n}</span> {s.label}
          </span>
        </li>
      ))}
    </ul>
  );
}

function Skeleton() {
  return (
    <div className="flex flex-col gap-2" aria-hidden="true">
      <Bar className="ml-1 h-4 w-44" />
      <Card className="overflow-hidden">
        <ul className="divide-y">
          {Array.from({ length: 5 }, (_, i) => (
            <li key={i} className={`flex flex-col gap-3 px-4 py-4 ${GRID}`}>
              <div className="flex flex-col gap-1.5">
                <Bar className="h-4 w-40" />
                <Bar className="h-3.5 w-28" />
              </div>
              <Bar className="h-2 w-full" />
              <Bar className="h-4 w-52" />
              <Bar className="h-4 w-24 md:justify-self-end" />
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}

/* ---------- page ---------- */

export function ConfigurationsPage() {
  const { rows, isPending, error, refetch, runsTruncated } = useConfigurationRows();
  const [query, setQuery] = useState("");
  const [starting, setStarting] = useState<StartRunTarget | null>(null);
  const shown = useMemo(() => rows.filter((r) => matches(r, query)), [rows, query]);

  return (
    <>
      <header className="flex flex-wrap items-end justify-between gap-x-6 gap-y-4 pt-8 pb-6">
        <div className="flex min-w-0 flex-col gap-1.5">
          <h1 className="text-balance font-semibold text-2xl tracking-tight">
            Sync configurations
          </h1>
          {isPending ? (
            <Bar className="h-5 w-80 max-w-full" />
          ) : error ? (
            <p className="text-subtle">Status unavailable</p>
          ) : rows.length === 0 ? (
            <p className="text-subtle">None registered</p>
          ) : (
            <Summary rows={rows} />
          )}
        </div>
      </header>

      <div aria-busy={isPending}>
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
          <div className="flex flex-col gap-6">
            <SearchField
              value={query}
              onChange={setQuery}
              count={shown.length}
              placeholder="Search by name, system or branch"
              noun={["configuration matches", "configurations match"]}
            />
            {shown.length === 0 ? (
              <EmptyPanel title={`No configurations match “${query}”`}>
                <p className="text-foreground-muted">
                  Search looks at names, IDs, systems, branches and who started the latest run.
                </p>
              </EmptyPanel>
            ) : (
              <div className="flex flex-col gap-8">
                {GROUPS.map((g) => (
                  <Group
                    key={g.id}
                    id={g.id}
                    title={g.title}
                    note={g.note}
                    rows={shown.filter((r) => g.states.includes(r.state))}
                    onStart={setStarting}
                  />
                ))}
              </div>
            )}
            {runsTruncated && (
              <p className="px-1 text-subtle text-xs">
                Run states come from the 200 most recent runs. A configuration whose latest run is
                older shows as never planned.
              </p>
            )}
          </div>
        )}
      </div>
      {starting && <StartRunDialog target={starting} onClose={() => setStarting(null)} />}
    </>
  );
}

export function KindsCount({ kinds }: { kinds: string[] }) {
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
      <span className="cursor-default tabular-nums underline decoration-subtle-muted decoration-dotted underline-offset-4">
        {label}
      </span>
    </Tooltip>
  );
}
