import { Button, Popover, PopoverDialog, PopoverTrigger } from "@infrahub/ui";
import type { ReactNode } from "react";
import { cn } from "tailwind-variants";

import type { Run } from "#/api/types";
import { CODE_CHIP, FOCUS_RAC } from "#/components/classes";
import type { ReadState, PlanReads as Reads, SideRead } from "#/domain/plan-reads";
import { fmt, plural, type RunState, runCounts } from "#/domain/run-state";
import type { RunStep } from "#/domain/run-steps";
import { formatSpan } from "#/lib/time";

import {
  attemptRows,
  cancelRow,
  DONE,
  queueRow,
  Row,
  range,
  Since,
  STEP_GRID,
  StateWord,
} from "./StepRows";
import { clockSeconds } from "./step-time";

/* ---------- a read's state ---------- */

const READ_WORD: Record<ReadState, string> = {
  waiting: "Waiting to start",
  reading: "Reading",
  succeeded: "Read",
  failed: "Read failed",
  not_started: "Not started",
  interrupted: "Interrupted",
  unrecorded: "End not recorded",
};

const READ_TONE: Record<ReadState, string> = {
  waiting: "text-subtle",
  reading: "text-accent",
  succeeded: "text-success",
  failed: "text-danger",
  not_started: "text-subtle",
  interrupted: "text-warning",
  unrecorded: "text-subtle",
};

const READ_ICON: Partial<Record<ReadState, RunState>> = {
  reading: "running",
  succeeded: "insync",
  failed: "failed",
};

const ReadStateWord = ({ v }: { v: SideRead }) => (
  <StateWord
    icon={v.state === "interrupted" ? "warning" : (READ_ICON[v.state] ?? "idle")}
    tone={READ_TONE[v.state]}
  >
    {READ_WORD[v.state]}
  </StateWord>
);

/* ---------- nodes by kind ---------- */

const NotRead = () => (
  <span className="text-subtle-muted">
    <span aria-hidden="true">—</span>
    <span className="sr-only">Not read</span>
  </span>
);

/** Nodes read per kind, one column per side that was read, largest kind first. */
function KindsTable({ reads }: { reads: Reads }) {
  const cols = reads.sides.filter((v) => v.state === "succeeded");
  const maps = cols.map((v) => new Map(v.kinds));
  const size = (k: string) => Math.max(...maps.map((m) => m.get(k) ?? 0));
  const names = [...new Set(cols.flatMap((v) => v.kinds.map(([k]) => k)))].sort(
    (a, b) => size(b) - size(a) || a.localeCompare(b)
  );
  const th = "sticky top-0 z-10 border-b bg-content px-3 py-2 font-normal text-subtle text-xs";
  return (
    <div className="max-h-[70vh] min-h-0 overflow-auto rounded-xl">
      <table className="w-full border-separate border-spacing-0 text-[13px]">
        <thead>
          <tr>
            <th scope="col" className={cn(th, "text-left")}>
              Kind
            </th>
            {cols.map((v) => (
              <th key={v.side} scope="col" className={cn(th, "max-w-40 text-right")}>
                <span className="block truncate" title={v.system}>
                  {v.system}
                </span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {names.map((k) => (
            <tr key={k}>
              <th
                scope="row"
                className="border-b px-3 py-1.5 text-left font-normal [overflow-wrap:anywhere]"
              >
                {k}
              </th>
              {maps.map((m, i) => (
                <td key={cols[i].side} className="border-b px-3 py-1.5 text-right tabular-nums">
                  {m.has(k) ? fmt(m.get(k) ?? 0) : <NotRead />}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr>
            <th scope="row" className="px-3 py-2 text-left font-medium">
              {plural(names.length, "kind", "kinds")}
            </th>
            {cols.map((v) => (
              <td key={v.side} className="px-3 py-2 text-right font-medium tabular-nums">
                {fmt(v.nodes)}
              </td>
            ))}
          </tr>
        </tfoot>
      </table>
    </div>
  );
}

/** "177 nodes of 9 kinds": the kinds count opens the nodes read from each system, by kind. */
function ReadCount({ v, reads }: { v: SideRead; reads: Reads }) {
  const kinds = plural(v.kinds.length, "kind", "kinds");
  return (
    <>
      <span className="font-medium text-foreground tabular-nums">{fmt(v.nodes)}</span>{" "}
      {v.nodes === 1 ? "node" : "nodes"} of{" "}
      {v.kinds.length > 0 ? (
        <PopoverTrigger>
          <Button
            variant="ghost"
            size="xs"
            className={cn(
              "-mx-1.5 h-6 rounded-md px-1.5 font-normal text-[13px] text-accent pointer-coarse:h-11",
              FOCUS_RAC
            )}
          >
            {kinds}
          </Button>
          <Popover placement="bottom start" className="bg-content">
            <PopoverDialog
              aria-label="Nodes read, by kind"
              className="flex max-h-[inherit] w-max min-w-72 max-w-[min(40rem,calc(100vw-2rem))] flex-col"
            >
              <KindsTable reads={reads} />
            </PopoverDialog>
          </Popover>
        </PopoverTrigger>
      ) : (
        kinds
      )}
    </>
  );
}

/* ---------- rows ---------- */

function readDuration(v: SideRead): ReactNode {
  if (!v.startedAt) return null;
  if (v.endedAt) return formatSpan(v.startedAt, v.endedAt);
  return v.state === "reading" ? <Since from={v.startedAt} /> : null;
}

function readClock(v: SideRead) {
  if (!v.startedAt) return null;
  if (v.endedAt) return range(v.startedAt, v.endedAt);
  return `${v.state === "reading" ? "since" : "started"} ${clockSeconds(v.startedAt)}`;
}

function readResult(v: SideRead, reads: Reads): ReactNode {
  if (v.state === "succeeded") {
    return (
      <>
        <ReadCount v={v} reads={reads} />
        {/* A full read is the usual case, so only another mode is named. */}
        {v.mode && v.mode !== "full" && <> · {v.mode} read</>}
      </>
    );
  }
  if (v.state === "failed" && v.errorClass) {
    return <code className={CODE_CHIP}>{v.errorClass}</code>;
  }
  return null;
}

function differences(run: Run): ReactNode {
  const total = runCounts(run)?.total;
  if (total === undefined) return "Plan saved";
  if (total === 0) return "No differences";
  return (
    <>
      <span className="font-medium text-foreground tabular-nums">{fmt(total)}</span>{" "}
      {total === 1 ? "difference" : "differences"}
    </>
  );
}

/**
 * What the Plan step did, one row each with its duration and clock times: its earlier attempts,
 * the wait for a worker, the read of each system, then the comparison.
 */
export function PlanReads({
  reads,
  step,
  run,
  className,
}: {
  reads: Reads;
  step: RunStep;
  run: Run;
  className?: string;
}) {
  const o = step.exec;
  const c = reads.compare;
  return (
    <dl className={cn(STEP_GRID, className)}>
      {attemptRows(step)}
      {o?.claimed_at && queueRow(o)}
      {reads.sides.map((v) => (
        <Row
          key={v.side}
          label={v.label}
          state={<ReadStateWord v={v} />}
          result={readResult(v, reads)}
          duration={readDuration(v)}
          clock={readClock(v)}
        />
      ))}
      {c && !c.to && (
        <Row
          label="Compare"
          state={
            <StateWord icon="running" tone="text-accent">
              Comparing
            </StateWord>
          }
          result="Both reads"
          duration={<Since from={c.from} />}
          clock={`since ${clockSeconds(c.from)}`}
        />
      )}
      {c?.to && (
        <Row
          label="Compare"
          state={<StateWord {...DONE}>Compared</StateWord>}
          result={differences(run)}
          duration={formatSpan(c.from, c.to)}
          clock={range(c.from, c.to)}
        />
      )}
      {!c && reads.savedAt && step.state === "done" && (
        <Row
          label="Plan saved"
          state={<StateWord {...DONE}>Saved</StateWord>}
          result="Plan file"
          clock={clockSeconds(reads.savedAt)}
        />
      )}
      {o && cancelRow(o)}
    </dl>
  );
}
