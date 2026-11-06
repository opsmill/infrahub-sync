import { Button, Card } from "@infrahub/ui";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { type ReactNode, useState } from "react";

import {
  artifactsQuery,
  configQuery,
  configVersionQuery,
  describeError,
  isInProgress,
  planQuery,
  runQuery,
} from "#/api/queries";
import type { Orchestration, Plan, Run, RunResource } from "#/api/types";
import { DownloadIcon, STATE_TEXT, StateIcon } from "#/components/icons";
import { Bar, CopyButton, ErrorPanel, SectionHeading } from "#/components/page";
import { BranchCode, NODE, STEP_TONE, STEP_WORD, When } from "#/components/run-status";
import {
  countsSentence,
  failureDetail,
  fmt,
  isVerified,
  phaseLabel,
  type RunState,
  runCounts,
  runState,
  runSteps,
  STEP_NAMES,
  type StepState,
} from "#/domain/run-state";
import { describeSystem } from "#/domain/systems";
import { formatAbsolute, formatDuration } from "#/lib/time";

import { PlanChanges, PlanFacts } from "./PlanReview";
import { ApplyButton, ApplyDialog, CancelDialog, VerifyDialog } from "./RunActions";
import { StartRunDialog } from "./StartRunDialog";

const OPERATION_TITLE: Record<string, string> = {
  plan: "Plan",
  sync: "Sync",
  verify: "Verify",
  apply: "Apply",
};

/* ---------- steps ---------- */

function lastExecution(orchestration: Orchestration[], purposes: string[]) {
  return [...orchestration].reverse().find((o) => purposes.includes(o.purpose));
}

function stepTiming(o: Orchestration | undefined): ReactNode {
  if (!o) return null;
  const start = o.claimed_at ?? o.submitted_at;
  if (o.terminal_at && start) {
    return (
      <>
        Finished <When iso={o.terminal_at} /> · ran {formatDuration(start, o.terminal_at)}
      </>
    );
  }
  if (start)
    return (
      <>
        Started <When iso={start} />
      </>
    );
  return null;
}

function StepCard({
  index,
  state,
  title,
  children,
}: {
  index: number;
  state: StepState;
  title: string;
  children?: ReactNode;
}) {
  return (
    <li className="flex min-w-0 flex-col gap-1.5 px-4 py-3.5">
      <div className="flex items-center gap-2.5">
        <span className="flex size-3 items-center justify-center">
          <span className={`block shrink-0 rounded-full ${NODE[state]}`} aria-hidden="true" />
        </span>
        <span className="font-medium">
          <span className="sr-only">Step {index + 1}: </span>
          {title}
        </span>
        <span className={`text-xs ${state === "done" ? "text-success" : STEP_TONE[state]}`}>
          {STEP_WORD[state]}
        </span>
      </div>
      <div className="flex flex-col gap-0.5 pl-[22px] text-foreground-muted text-xs">
        {children}
      </div>
    </li>
  );
}

function Steps({ resource, inFlight }: { resource: RunResource; inFlight?: string }) {
  const { run, orchestration } = resource;
  const steps = runSteps(run, { inFlight });
  const plan = lastExecution(orchestration, run.operation === "sync" ? ["sync"] : ["plan"]);
  const verify = lastExecution(orchestration, ["verify"]);
  const apply = lastExecution(orchestration, run.operation === "sync" ? ["sync"] : ["apply"]);
  const counts = runCounts(run);
  const verification = run.results?.verification as
    | { checksum_ok?: boolean; verification_notes?: string[] }
    | undefined;
  return (
    <Card>
      <ol
        className="grid grid-cols-1 divide-y sm:grid-cols-3 sm:divide-x sm:divide-y-0"
        aria-label="Steps of this run"
      >
        <StepCard index={0} state={steps[0]} title={STEP_NAMES[0]}>
          {run.phase !== "accepted" && counts && steps[0] === "done" && (
            <span>
              {counts.total === 0
                ? "No differences found"
                : `${fmt(counts.total)} ${counts.total === 1 ? "change" : "changes"} found`}
            </span>
          )}
          <span>{stepTiming(plan)}</span>
        </StepCard>
        <StepCard index={1} state={steps[1]} title={STEP_NAMES[1]}>
          {verification ? (
            <span>
              {verification.checksum_ok
                ? "The plan still matches the destination"
                : "The checksum did not match"}
            </span>
          ) : steps[1] === "done" && !verify ? (
            <span>Checked against the destination by the apply, before its first write</span>
          ) : steps[1] === "skipped" ? (
            <span>
              {run.operation === "sync"
                ? "A sync applies without a separate verification"
                : "Not verified before the apply"}
            </span>
          ) : steps[1] === "waiting" ? (
            <span>Optional. Checks the plan against the destination without writing</span>
          ) : null}
          {verification?.verification_notes?.map((n) => (
            <span key={n}>{n}</span>
          ))}
          <span>{stepTiming(verify)}</span>
        </StepCard>
        <StepCard index={2} state={steps[2]} title={STEP_NAMES[2]}>
          {run.phase === "applied" && counts && (
            <span>
              {counts.total === 0 ? "Nothing to write" : `${countsSentence(counts, "done")}`}
            </span>
          )}
          {steps[2] === "waiting" && <span>Waiting for someone to apply the reviewed plan</span>}
          {run.phase === "planned" && run.outcome === "no-change" && <span>Nothing to apply</span>}
          <span>{stepTiming(apply)}</span>
        </StepCard>
      </ol>
    </Card>
  );
}

/* ---------- failure ---------- */

function FailurePanel({ run }: { run: Run }) {
  const f = failureDetail(run);
  if (!f) return null;
  return (
    <Card className="border-danger/35">
      <div className="flex flex-col gap-3 p-4" role="alert">
        <div className="flex items-start gap-2.5">
          <StateIcon state="failed" className="mt-0.5 size-4 shrink-0 text-danger" />
          <div className="flex min-w-0 flex-col gap-1">
            <p className="font-medium text-danger">
              {f.ambiguous ? "The write may have reached the destination" : f.sentence}
            </p>
            {(f.nextAction || f.cause) && <p className="text-pretty">{f.nextAction ?? f.cause}</p>}
            {f.ambiguous && (
              <p className="text-pretty text-foreground-muted">
                The service could not prove what was written. Inspect the destination, then create a
                new plan: it proposes only what is still outstanding.
              </p>
            )}
          </div>
        </div>
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 pl-[26px] text-[13px]">
          <dt className="text-subtle">Stage</dt>
          <dd className="capitalize">{f.stage}</dd>
          {f.errorType && (
            <>
              <dt className="text-subtle">Error</dt>
              <dd className="font-mono text-xs leading-5 [overflow-wrap:anywhere]">
                {f.errorType}
                {f.causeType && <span className="text-subtle"> caused by </span>}
                {f.causeType}
              </dd>
            </>
          )}
          {f.appliedCount !== undefined && (
            <>
              <dt className="text-subtle">Written before</dt>
              <dd>{fmt(f.appliedCount)} operations</dd>
            </>
          )}
          {f.failedOperation && (
            <>
              <dt className="text-subtle">Failed on</dt>
              <dd className="font-mono text-xs leading-5 [overflow-wrap:anywhere]">
                {f.failedOperation}
              </dd>
            </>
          )}
          {run.reconciliation_required && (
            <>
              <dt className="text-subtle">Reconciliation</dt>
              <dd>Required</dd>
            </>
          )}
        </dl>
      </div>
    </Card>
  );
}

/* ---------- plan, artifacts, history ---------- */

function PlanSection({
  run,
  plan,
  error,
  isPending,
  hasPlan,
}: {
  run: Run;
  plan?: Plan;
  error: unknown;
  isPending: boolean;
  hasPlan: boolean;
}) {
  const counts = runCounts(run);
  if (!hasPlan) return null;
  if (isPending) {
    return (
      <section className="flex flex-col gap-2">
        <SectionHeading title="Changes" />
        <Card className="p-4">
          <Bar className="h-4 w-60" />
        </Card>
      </section>
    );
  }
  if (!plan) {
    const d = describeError(error);
    return (
      <section className="flex flex-col gap-2">
        <SectionHeading title="Changes" />
        <Card>
          <p className="px-6 py-8 text-center text-foreground-muted">
            {d.status === 404 || d.status === 503
              ? "This run kept no plan to review."
              : d.status === 410
                ? "The retained plan expired."
                : d.title}
          </p>
        </Card>
      </section>
    );
  }
  return (
    <section aria-labelledby="changes" className="flex flex-col gap-3">
      <SectionHeading id="changes" title="Changes" count={plan.operations.length}>
        {counts && counts.total > 0
          ? countsSentence(counts, run.phase === "applied" ? "done" : "plan")
          : "No differences"}
      </SectionHeading>
      <Card className="p-4">
        <PlanFacts plan={plan} />
      </Card>
      <PlanChanges plan={plan} />
    </section>
  );
}

function formatBytes(n: number) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

function Artifacts({ runId }: { runId: string }) {
  const artifacts = useQuery(artifactsQuery(runId));
  const items = artifacts.data?.artifacts ?? [];
  if (!artifacts.data || items.length === 0) return null;
  return (
    <section aria-labelledby="artifacts" className="flex flex-col gap-2">
      <SectionHeading id="artifacts" title="Files kept for this run" count={items.length} />
      <Card className="overflow-hidden">
        <ul className="divide-y">
          {items.map((a) => (
            <li
              key={a.artifact_id}
              className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-2.5 text-[13px]"
            >
              <span className="min-w-0 flex-1">
                <span className="font-medium">{a.kind.replaceAll("-", " ")}</span>
                <span className="block text-subtle text-xs">
                  {a.media_type} · {formatBytes(a.size)} · <When iso={a.created_at} />
                  {a.expires_at && (
                    <>
                      {" "}
                      · expires <When iso={a.expires_at} />
                    </>
                  )}
                </span>
              </span>
              <a
                href={`/api/runs/${encodeURIComponent(runId)}/artifacts/${encodeURIComponent(a.artifact_id)}`}
                download={`${runId}-${a.artifact_id}`}
                className="inline-flex h-8 items-center gap-1.5 rounded-md px-2 font-medium text-foreground-muted transition-colors duration-150 ease-out hover:bg-highlight hover:text-foreground focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring-halo"
              >
                <DownloadIcon />
                Download
              </a>
            </li>
          ))}
        </ul>
      </Card>
    </section>
  );
}

function History({ orchestration }: { orchestration: Orchestration[] }) {
  if (orchestration.length === 0) return null;
  return (
    <section aria-labelledby="history" className="flex flex-col gap-2">
      <SectionHeading id="history" title="Executions" count={orchestration.length}>
        Each stage the worker ran, oldest first
      </SectionHeading>
      <Card className="overflow-hidden">
        <table className="w-full border-collapse text-left text-[13px]">
          <thead className="max-md:sr-only">
            <tr className="border-b text-subtle text-xs">
              <th scope="col" className="px-4 py-2 font-medium">
                Stage
              </th>
              <th scope="col" className="px-4 py-2 font-medium">
                Result
              </th>
              <th scope="col" className="px-4 py-2 font-medium">
                Submitted
              </th>
              <th scope="col" className="px-4 py-2 font-medium">
                Queued
              </th>
              <th scope="col" className="px-4 py-2 font-medium">
                Ran
              </th>
              <th scope="col" className="px-4 py-2 font-medium">
                Prefect flow run
              </th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {orchestration.map((o) => (
              <tr
                key={o.flow_run_id}
                className="max-md:flex max-md:flex-col max-md:gap-0.5 max-md:px-4 max-md:py-2.5"
              >
                <td className="px-4 py-2.5 capitalize max-md:p-0">
                  {o.purpose}
                  {o.attempt > 1 && (
                    <span className="text-subtle normal-case"> · attempt {o.attempt}</span>
                  )}
                </td>
                <td className="px-4 py-2.5 max-md:p-0">
                  {o.terminal_state ? (
                    <span
                      className={
                        o.terminal_outcome === "succeeded"
                          ? "text-success"
                          : o.terminal_outcome === "cancelled" || o.terminal_outcome === "abandoned"
                            ? "text-foreground-muted"
                            : "text-danger"
                      }
                    >
                      {o.terminal_state}
                      {o.terminal_outcome && o.terminal_outcome !== o.terminal_state && (
                        <span className="text-subtle"> · {o.terminal_outcome}</span>
                      )}
                    </span>
                  ) : (
                    <span className="text-accent">
                      {o.cancellation_requested_at ? "cancelling" : (o.state ?? "submitted")}
                    </span>
                  )}
                </td>
                <td className="px-4 py-2.5 text-foreground-muted max-md:p-0">
                  {o.submitted_at ? <When iso={o.submitted_at} /> : "—"}
                </td>
                <td className="px-4 py-2.5 text-foreground-muted tabular-nums max-md:p-0">
                  <span className="md:sr-only">Queued </span>
                  {o.submitted_at && o.claimed_at
                    ? formatDuration(o.submitted_at, o.claimed_at)
                    : "—"}
                </td>
                <td className="px-4 py-2.5 text-foreground-muted tabular-nums max-md:p-0">
                  <span className="md:sr-only">Ran </span>
                  {o.claimed_at && o.terminal_at
                    ? formatDuration(o.claimed_at, o.terminal_at)
                    : "—"}
                </td>
                <td className="px-4 py-2.5 font-mono text-[11px] text-subtle max-md:p-0">
                  {o.flow_run_id}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </section>
  );
}

function Details({ run }: { run: Run }) {
  const rows: [string, ReactNode][] = [
    [
      "Run ID",
      <span key="id" className="inline-flex items-center gap-1 font-mono text-xs">
        {run.run_id}
        <CopyButton value={run.run_id} label="Copy run ID" />
      </span>,
    ],
    [
      "Configuration",
      <span key="ref" className="font-mono text-xs">
        {run.configuration_reference}
      </span>,
    ],
    [
      "Package checksum",
      <span
        key="pc"
        className="block truncate font-mono text-xs"
        title={run.package_checksum ?? undefined}
      >
        {run.package_checksum ?? "—"}
      </span>,
    ],
    ["Started by", run.actor ?? "—"],
    [
      "Started",
      <span key="s" title={formatAbsolute(run.started_at)}>
        {formatAbsolute(run.started_at)}
      </span>,
    ],
    ["Finished", run.finished_at ? formatAbsolute(run.finished_at) : "Not finished"],
  ];
  return (
    <section aria-labelledby="details" className="flex flex-col gap-2">
      <SectionHeading id="details" title="Details" />
      <Card className="p-4">
        <dl className="grid grid-cols-1 gap-x-8 gap-y-3 text-[13px] sm:grid-cols-3">
          {rows.map(([k, v]) => (
            <div key={k} className="flex min-w-0 flex-col gap-0.5">
              <dt className="text-subtle text-xs">{k}</dt>
              <dd className="min-w-0">{v}</dd>
            </div>
          ))}
        </dl>
      </Card>
    </section>
  );
}

/* ---------- page ---------- */

type Dialog = "verify" | "apply" | "cancel" | "plan" | null;

function Header({
  resource,
  name,
  state,
  inFlight,
  plan,
  onOpen,
}: {
  resource: RunResource;
  name: string;
  state: RunState;
  inFlight?: string;
  plan?: Plan;
  onOpen: (d: Dialog) => void;
}) {
  const { run } = resource;
  const counts = runCounts(run);
  const reviewable = run.phase === "planned" && run.outcome === "planned" && !inFlight;
  const executable = counts && plan ? counts.total - plan.summary.deletes_not_executed : 0;
  const running = isInProgress(resource);
  return (
    <header className="flex flex-col gap-4 pt-6 pb-6">
      <nav aria-label="Breadcrumb" className="text-foreground-muted text-sm">
        <ol className="flex flex-wrap items-center gap-1.5">
          <li>
            <Link to="/" className="rounded-sm hover:text-foreground hover:underline">
              Configurations
            </Link>
          </li>
          <li aria-hidden="true" className="text-border-strong">
            /
          </li>
          <li>
            {run.config_id ? (
              <Link
                to="/configs/$configId"
                params={{ configId: run.config_id }}
                className="rounded-sm [overflow-wrap:anywhere] hover:text-foreground hover:underline"
              >
                {name}
              </Link>
            ) : (
              name
            )}
          </li>
          <li aria-hidden="true" className="text-border-strong">
            /
          </li>
          <li aria-current="page" className="font-mono text-xs">
            {run.run_id}
          </li>
        </ol>
      </nav>
      <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-4">
        <div className="flex min-w-0 flex-col gap-2">
          <h1 className="text-balance font-semibold text-2xl tracking-tight [overflow-wrap:anywhere]">
            {OPERATION_TITLE[run.operation] ?? run.operation} of {name}
          </h1>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-foreground-muted">
            <span className={`inline-flex items-center gap-1.5 font-medium ${STATE_TEXT[state]}`}>
              <StateIcon state={state} className="size-3.5" />
              {phaseLabel(run, { inFlight })}
            </span>
            {run.registry_version != null && <span>Version {run.registry_version}</span>}
            {run.destination_branch && (
              <span className="inline-flex items-center gap-1.5">
                into <BranchCode branch={run.destination_branch} />
              </span>
            )}
            <span>
              started <When iso={run.started_at} />
              {run.actor && <> by {run.actor}</>}
            </span>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {running && (
            <Button variant="danger-outline" size="sm" onPress={() => onOpen("cancel")}>
              Cancel run
            </Button>
          )}
          {reviewable && !isVerified(run) && (
            <Button variant="outline" size="sm" onPress={() => onOpen("verify")}>
              Verify
            </Button>
          )}
          {reviewable && plan && executable > 0 && (
            <ApplyButton
              label={`Apply ${fmt(executable)} ${executable === 1 ? "change" : "changes"}`}
              onPress={() => onOpen("apply")}
            />
          )}
          {!running && !reviewable && run.config_id && (
            <Button variant="outline" size="sm" onPress={() => onOpen("plan")}>
              Plan again
            </Button>
          )}
        </div>
      </div>
    </header>
  );
}

export function RunPage({ runId }: { runId: string }) {
  const runQ = useQuery(runQuery(runId));
  const resource = runQ.data;
  const run = resource?.run;
  const config = useQuery({
    ...configQuery(run?.config_id ?? ""),
    enabled: Boolean(run?.config_id),
  });
  const version = useQuery({
    ...configVersionQuery(run?.config_id ?? "", run?.registry_version ?? 1),
    enabled: Boolean(run?.config_id && run.registry_version),
  });
  const hasPlan = Boolean(
    run &&
      (run.phase === "planned" ||
        run.phase === "applied" ||
        run.phase.startsWith("apply") ||
        run.operation === "sync") &&
      run.phase !== "accepted"
  );
  const plan = useQuery({ ...planQuery(runId), enabled: hasPlan });
  const [dialog, setDialog] = useState<Dialog>(null);

  if (runQ.isPending) {
    return (
      <div className="flex flex-col gap-4 pt-6" aria-busy="true">
        <output className="sr-only">Loading the run</output>
        <Bar className="h-4 w-64" />
        <Bar className="h-8 w-96 max-w-full" />
        <Bar className="h-4 w-80" />
        <Bar className="mt-4 h-24 w-full" />
      </div>
    );
  }
  if (!resource || !run) {
    return (
      <div className="pt-8">
        <ErrorPanel error={runQ.error} onRetry={() => runQ.refetch()} />
      </div>
    );
  }

  const inFlight = resource.orchestration.find(
    (o) => o.terminal_at == null && run.phase !== "accepted"
  )?.purpose;
  const latestVersion = config.data?.latest_version ?? undefined;
  const state = runState(run, { inFlight });
  const name =
    config.data?.name ??
    (run.summary?.sync_name as string | undefined) ??
    run.configuration_reference;
  const destination = version.data
    ? describeSystem(version.data.declared_content.configuration.destination)
    : undefined;

  return (
    <>
      <Header
        resource={resource}
        name={name}
        state={state}
        inFlight={inFlight}
        plan={plan.data}
        onOpen={setDialog}
      />
      <div className="flex flex-col gap-8">
        {latestVersion !== undefined &&
          run.registry_version != null &&
          run.registry_version < latestVersion && (
            <p className="rounded-xl bg-content-strong px-3 py-2 text-foreground-muted text-sm">
              This run used version {run.registry_version}. Version {latestVersion} is the latest.
            </p>
          )}
        <Steps resource={resource} inFlight={inFlight} />
        <FailurePanel run={run} />
        <PlanSection
          run={run}
          plan={plan.data}
          error={plan.error}
          isPending={hasPlan && plan.isPending}
          hasPlan={hasPlan}
        />
        <Artifacts runId={runId} />
        <History orchestration={resource.orchestration} />
        <Details run={run} />
      </div>
      {dialog === "verify" && <VerifyDialog run={run} onClose={() => setDialog(null)} />}
      {dialog === "apply" && plan.data && (
        <ApplyDialog run={run} plan={plan.data} onClose={() => setDialog(null)} />
      )}
      {dialog === "cancel" && (
        <CancelDialog
          run={run}
          writing={inFlight === "apply" || run.operation === "sync"}
          onClose={() => setDialog(null)}
        />
      )}
      {dialog === "plan" && run.config_id && (
        <StartRunDialog
          target={{
            configId: run.config_id,
            name,
            version: latestVersion ?? run.registry_version ?? 1,
            branch: run.destination_branch ?? destination?.branch,
          }}
          onClose={() => setDialog(null)}
        />
      )}
    </>
  );
}
