import { Button, Card, LinkButton } from "@infrahub/ui";
import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";

import { isEnvelope } from "#/api/errors";
import { describeError } from "#/api/queries";
import type { Orchestration, Plan, Run } from "#/api/types";
import { DownloadIcon } from "#/components/icons";
import { Bar, CopyButton, ErrorPanel, FOCUS, SectionHeading } from "#/components/page";
import { BranchCode, StateLabel, When } from "#/components/run-status";
import { type RunState, runCounts } from "#/domain/run-state";
import type { System } from "#/domain/systems";
import { formatAbsolute, formatDuration } from "#/lib/time";

import { changesSentence, PlanChanges, PlanFacts } from "./PlanReview";

export const PLAN_ARTIFACT_ID = "plan-review";

const MUTED_LINE = "flex flex-wrap items-center gap-3 text-foreground-muted text-sm";

/* ---------- changes ---------- */

interface PlanRequest {
  data?: Plan;
  error: unknown;
  isPending: boolean;
  refetch: () => unknown;
}

function PlanSkeleton({ count }: { count?: number }) {
  return (
    <section aria-busy="true" className="flex flex-col gap-3">
      <output className="sr-only">Loading the plan</output>
      <SectionHeading title="Changes" count={count} />
      <Card className="overflow-clip">
        <div className="flex h-11 items-center border-b bg-content-muted px-4 sm:h-10">
          <Bar className="h-3.5 w-32" />
        </div>
        {Array.from({ length: 6 }, (_, i) => (
          <div
            key={i}
            className="flex h-11 items-center gap-3 border-b px-4 last:border-b-0 sm:h-9"
          >
            <Bar className="size-4 rounded-[5px]" />
            <Bar className="h-3.5 w-48 max-w-[60%]" />
          </div>
        ))}
      </Card>
    </section>
  );
}

function PlanError({ run, plan }: { run: Run; plan: PlanRequest }) {
  const d = describeError(plan.error);
  const retry = (
    <Button variant="outline" size="sm" onPress={() => plan.refetch()}>
      Try again
    </Button>
  );
  let body: ReactNode;
  if (d.status === 410) {
    const expires = run.artifact_refs.find((a) => a.artifact_id === PLAN_ARTIFACT_ID)?.expires_at;
    body = (
      <p className={MUTED_LINE}>
        <span>
          The saved plan expired
          {expires && (
            <>
              {" "}
              <When iso={expires} />
            </>
          )}
          . Create a new plan to review the changes again.
        </span>
      </p>
    );
  } else if (d.status === 503) {
    body = (
      <div className={MUTED_LINE}>
        <p className="flex flex-col gap-0.5">
          The Sync API could not read the saved plan of this run.
          {isEnvelope(plan.error) && (
            <span className="text-subtle text-xs">{plan.error.error.message}</span>
          )}
        </p>
        {retry}
      </div>
    );
  } else {
    body = <ErrorPanel compact error={plan.error} onRetry={() => plan.refetch()} />;
  }
  return (
    <section aria-labelledby="changes" className="flex flex-col gap-3">
      <SectionHeading id="changes" title="Changes" />
      {body}
    </section>
  );
}

/** The saved plan: what the run proposes or wrote, then the record that identifies it. */
export function PlanSection({ run, plan }: { run: Run; plan: PlanRequest }) {
  if (plan.isPending) return <PlanSkeleton count={runCounts(run)?.total} />;
  if (!plan.data) return <PlanError run={run} plan={plan} />;
  const data = plan.data;
  if (data.summary.total === 0) {
    return (
      <section aria-labelledby="changes" className="flex flex-col gap-3">
        <SectionHeading id="changes" title="Changes" />
        <div className="flex flex-col gap-1 text-foreground-muted text-sm">
          <p>
            The plan found no differences between the source and the destination
            {data.destination_branch && (
              <>
                {" "}
                on branch <BranchCode branch={data.destination_branch} />
              </>
            )}
            .
          </p>
          {!data.summary.delete_operations_computed && (
            <p className="text-subtle text-xs">
              Deletes were not listed, because the destination was not read in full.
            </p>
          )}
        </div>
      </section>
    );
  }
  return (
    <>
      <section aria-labelledby="changes" className="flex flex-col gap-3">
        <SectionHeading id="changes" title="Changes" count={data.summary.total}>
          {changesSentence(data, run.phase)}
        </SectionHeading>
        <PlanChanges plan={data} phase={run.phase} />
      </section>
      {data.operations.length > 0 && (
        <section aria-labelledby="plan-record" className="flex flex-col gap-3">
          <SectionHeading id="plan-record" title="Plan record" />
          <Card className="p-4">
            <PlanFacts plan={data} runBranch={run.destination_branch ?? null} />
          </Card>
        </section>
      )}
    </>
  );
}

/* ---------- files ---------- */

function formatBytes(n: number) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

const kindTitle = (kind: string) => {
  const words = kind.replaceAll("-", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
};

/** Files the Sync service keeps for the run, from `run.artifact_refs`. */
export function RunFiles({ run }: { run: Run }) {
  const items = run.artifact_refs;
  if (items.length === 0) return null;
  return (
    <section aria-labelledby="files" className="flex flex-col gap-3">
      <SectionHeading id="files" title="Files kept for this run" count={items.length} />
      <Card className="overflow-hidden">
        <ul className="divide-y">
          {items.map((a) => {
            const title = kindTitle(a.kind);
            const json = a.media_type === "application/json";
            return (
              <li
                key={a.artifact_id}
                className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-2.5 text-[13px]"
              >
                <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                  <span className="font-medium">{title}</span>
                  <span className="text-subtle text-xs">
                    {json ? "JSON" : a.media_type} ·{" "}
                    <span className="tabular-nums">{formatBytes(a.size)}</span> ·{" "}
                    <When prefix="Saved " iso={a.created_at} />
                    {a.expires_at && (
                      <>
                        {" "}
                        · <When prefix="Expires " iso={a.expires_at} />
                      </>
                    )}
                  </span>
                  <span
                    className="flex min-w-0 items-center gap-1 font-mono text-subtle text-xs"
                    title={a.digest}
                  >
                    SHA-256 {a.digest.slice(0, 12)}…
                    <CopyButton value={a.digest} label="Copy SHA-256 digest" />
                  </span>
                </div>
                <LinkButton
                  variant="ghost"
                  size="sm"
                  href={`/api/runs/${encodeURIComponent(run.run_id)}/artifacts/${encodeURIComponent(a.artifact_id)}`}
                  download={`${run.run_id}-${a.artifact_id}${json ? ".json" : ""}`}
                  aria-label={`Download ${title}`}
                  className="text-foreground-muted pointer-coarse:h-11"
                >
                  <DownloadIcon />
                  Download
                </LinkButton>
              </li>
            );
          })}
        </ul>
      </Card>
    </section>
  );
}

/* ---------- executions ---------- */

function executionResult(o: Orchestration): { state: RunState; label: string } {
  if (o.terminal_at == null) {
    if (o.cancellation_requested_at) return { state: "running", label: "Cancelling" };
    if (o.claimed_at) return { state: "running", label: "Running" };
    return { state: "running", label: "Waiting for a worker" };
  }
  switch (o.terminal_outcome) {
    case "succeeded":
      return { state: "insync", label: "Succeeded" };
    case "failed":
      return { state: "failed", label: "Failed" };
    case "ambiguous":
      return { state: "failed", label: "Interrupted, outcome unknown" };
    case "cancelled":
      return { state: "idle", label: "Cancelled" };
    case "abandoned":
      return { state: "idle", label: "Not picked up by a worker" };
    default:
      return { state: "idle", label: "Ended, outcome not recorded" };
  }
}

const queued = (o: Orchestration) =>
  o.submitted_at && o.claimed_at ? formatDuration(o.submitted_at, o.claimed_at) : undefined;

function ran(o: Orchestration): string {
  if (o.claimed_at && o.terminal_at) return formatDuration(o.claimed_at, o.terminal_at);
  return o.terminal_at ? "Did not run" : "Still running";
}

function FlowRunId({ id }: { id: string }) {
  return (
    <span className="inline-flex items-center gap-1">
      <span className="font-mono text-subtle text-xs" title={id}>
        {id.slice(0, 8)}
      </span>
      <CopyButton value={id} label="Copy flow run ID" />
    </span>
  );
}

function StageName({ o }: { o: Orchestration }) {
  return (
    <>
      <span className="capitalize">{o.purpose}</span>
      {o.attempt > 1 && <span className="text-subtle"> · attempt {o.attempt}</span>}
    </>
  );
}

const TH = "px-4 py-2 font-medium";
const TD = "px-4 py-2.5";

/** Each execution submitted to Prefect for this run, oldest first. */
export function Executions({ orchestration }: { orchestration: Orchestration[] }) {
  if (orchestration.length === 0) return null;
  return (
    <section aria-labelledby="executions" className="flex flex-col gap-3">
      <SectionHeading id="executions" title="Executions" count={orchestration.length}>
        Each stage submitted for this run, oldest first
      </SectionHeading>
      <Card className="overflow-hidden">
        <table
          aria-labelledby="executions"
          className="w-full table-fixed border-collapse text-left text-[13px] max-md:hidden"
        >
          <colgroup>
            <col className="w-[14%]" />
            <col className="w-[26%]" />
            <col className="w-[18%]" />
            <col className="w-[12%]" />
            <col className="w-[12%]" />
            <col className="w-[18%]" />
          </colgroup>
          <thead>
            <tr className="border-b text-subtle text-xs">
              <th scope="col" className={TH}>
                Stage
              </th>
              <th scope="col" className={TH}>
                Result
              </th>
              <th scope="col" className={TH}>
                Submitted
              </th>
              <th scope="col" className={TH}>
                Queued
              </th>
              <th scope="col" className={TH}>
                Ran
              </th>
              <th scope="col" className={TH}>
                Flow run ID
              </th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {orchestration.map((o) => {
              const r = executionResult(o);
              return (
                <tr key={o.flow_run_id}>
                  <td className={TD}>
                    <StageName o={o} />
                  </td>
                  <td className={TD}>
                    <StateLabel state={r.state} label={r.label} />
                  </td>
                  <td className={`${TD} text-foreground-muted`}>
                    {o.submitted_at ? <When iso={o.submitted_at} /> : "—"}
                  </td>
                  <td className={`${TD} whitespace-nowrap text-foreground-muted tabular-nums`}>
                    {queued(o) ?? "—"}
                  </td>
                  <td className={`${TD} whitespace-nowrap text-foreground-muted tabular-nums`}>
                    {ran(o)}
                  </td>
                  <td className={`${TD} py-1.5`}>
                    <FlowRunId id={o.flow_run_id} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <ul aria-labelledby="executions" className="divide-y md:hidden">
          {orchestration.map((o) => {
            const r = executionResult(o);
            const q = queued(o);
            return (
              <li key={o.flow_run_id} className="flex flex-col gap-1 px-4 py-3 text-[13px]">
                <div className="flex flex-wrap items-center justify-between gap-x-2 gap-y-0.5">
                  <span>
                    <StageName o={o} />
                  </span>
                  <StateLabel state={r.state} label={r.label} />
                </div>
                <p className="text-foreground-muted text-xs">
                  {o.submitted_at && (
                    <>
                      <When prefix="Submitted " iso={o.submitted_at} /> ·{" "}
                    </>
                  )}
                  {q && <>queued {q} · </>}
                  {o.claimed_at && o.terminal_at ? `ran ${ran(o)}` : ran(o).toLowerCase()}
                </p>
                <FlowRunId id={o.flow_run_id} />
              </li>
            );
          })}
        </ul>
      </Card>
    </section>
  );
}

/* ---------- details ---------- */

function SystemValue({ system }: { system: System }) {
  return (
    <>
      {system.label}
      {system.host && (
        <span className="font-mono text-subtle text-xs [overflow-wrap:anywhere]">
          {system.host}
        </span>
      )}
    </>
  );
}

/** Reference facts about the run that the header does not already show. */
export function Details({
  run,
  name,
  source,
  destination,
}: {
  run: Run;
  name: string;
  source?: System;
  destination?: System;
}) {
  const rows: [string, ReactNode][] = [
    [
      "Run ID",
      <>
        <span className="font-mono text-xs">{run.run_id}</span>
        <CopyButton value={run.run_id} label="Copy run ID" />
      </>,
    ],
    [
      "Configuration",
      <span className="[overflow-wrap:anywhere]">
        {run.config_id ? (
          <Link
            to="/configs/$configId"
            params={{ configId: run.config_id }}
            search={{ tab: "versions" }}
            className={`relative rounded-sm text-accent before:absolute before:inset-x-0 before:-inset-y-3 before:content-[''] hover:underline ${FOCUS}`}
          >
            {name}
          </Link>
        ) : (
          name
        )}
        {run.registry_version != null && (
          <span className="whitespace-nowrap text-subtle"> · version {run.registry_version}</span>
        )}
      </span>,
    ],
  ];
  if (source) rows.push(["Source", <SystemValue system={source} />]);
  if (destination) rows.push(["Destination", <SystemValue system={destination} />]);
  if (run.destination_branch) {
    rows.push(["Destination branch", <BranchCode branch={run.destination_branch} />]);
  }
  rows.push(["Started", formatAbsolute(run.started_at)]);
  rows.push(["Finished", run.finished_at ? formatAbsolute(run.finished_at) : "Not finished"]);
  if (run.finished_at) {
    rows.push([
      "Duration",
      <span className="tabular-nums">{formatDuration(run.started_at, run.finished_at)}</span>,
    ]);
  }
  rows.push([
    "Package checksum",
    run.package_checksum ? (
      <span className="flex w-full min-w-0 items-center gap-1">
        <span className="min-w-0 truncate font-mono text-xs">{run.package_checksum}</span>
        <CopyButton value={run.package_checksum} label="Copy package checksum" />
      </span>
    ) : (
      "Not recorded"
    ),
  ]);
  return (
    <section aria-labelledby="details" className="flex flex-col gap-3">
      <SectionHeading id="details" title="Details" />
      <Card className="p-4">
        <dl className="grid grid-cols-1 gap-x-8 gap-y-3 text-[13px] sm:grid-cols-2 lg:grid-cols-3">
          {rows.map(([k, v]) => (
            <div key={k} className="flex min-w-0 flex-col gap-0.5">
              <dt className="text-subtle text-xs">{k}</dt>
              <dd className="flex min-h-6 min-w-0 flex-wrap items-center gap-1">{v}</dd>
            </div>
          ))}
        </dl>
      </Card>
    </section>
  );
}
