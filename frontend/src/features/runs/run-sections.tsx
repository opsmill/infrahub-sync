import { Button, Card, LinkButton } from "@infrahub/ui";
import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";
import { cn } from "tailwind-variants";

import { isEnvelope } from "#/api/errors";
import { describeError } from "#/api/queries";
import type { Plan, Run } from "#/api/types";
import { DownloadIcon } from "#/components/icons";
import { Bar, CopyButton, ErrorPanel, FOCUS, SectionHeading } from "#/components/page";
import { BranchCode, When } from "#/components/run-status";
import { runCounts } from "#/domain/run-state";
import { PLAN_ARTIFACT_ID } from "#/domain/run-steps";
import type { System } from "#/domain/systems";
import { formatAbsolute, formatDuration } from "#/lib/time";

import { changesSentence, PlanChanges, PlanFacts } from "./PlanReview";

export { PLAN_ARTIFACT_ID };

const MUTED_LINE = "flex flex-wrap items-center gap-3 text-foreground-muted text-sm";

/* ---------- changes ---------- */

export interface PlanRequest {
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

/**
 * The changes of the saved plan, as their own section after the timeline. A plan still loading,
 * unreadable or empty falls back to `PlanSection`, which says why. A run with no saved plan has none.
 */
export function ChangesSection({ run, plan }: { run: Run; plan: PlanRequest }) {
  const data = plan.data;
  if (!data || data.summary.total === 0) return <PlanSection run={run} plan={plan} />;
  return (
    <section aria-labelledby="changes" className="flex scroll-mt-16 flex-col gap-3">
      <SectionHeading id="changes" title="Changes" count={data.summary.total}>
        {changesSentence(data, run.phase)}
      </SectionHeading>
      <PlanChanges plan={data} phase={run.phase} />
    </section>
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
            className={cn(
              "relative rounded-sm text-accent before:absolute before:inset-x-0 before:-inset-y-3 before:content-[''] hover:underline",
              FOCUS
            )}
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
