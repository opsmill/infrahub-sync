import { Button, buttonVariants, Card } from "@infrahub/ui";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { type ReactNode, useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { cn } from "tailwind-variants";

import {
  configQuery,
  configVersionQuery,
  describeError,
  isInProgress,
  LIST_REFRESH_MS,
  meQuery,
  runQuery,
  runsQuery,
} from "#/api/queries";
import type { Run, RunResource } from "#/api/types";
import { ArrowRightIcon, PlusIcon } from "#/components/icons";
import {
  Bar,
  CODE_CHIP,
  CRUMB,
  ErrorPanel,
  FOCUS,
  META_LIST,
  StaleNotice,
} from "#/components/page";
import { BranchBadge, StateLabel, When } from "#/components/run-status";
import {
  executableCount,
  failureDetail,
  fmt,
  isDeleteOnlyPlan,
  isVerified,
  laterWrite,
  newerPlan,
  phaseLabel,
  type RunState,
  runCounts,
  runState,
} from "#/domain/run-state";
import { runStepList } from "#/domain/run-steps";
import { describeSystem, type System } from "#/domain/systems";
import { useDocumentTitle } from "#/lib/title";
import { ChangeCount, ChangesDock, type ChangesState, useChangesShortcut } from "./ChangesDock";
import { ChangesSheet, useChangesSheet } from "./ChangesSheet";
import { usePlanDownload } from "./plan-download";
import { ApplyButton, ApplyWithPlan, CancelDialog, VerifyDialog } from "./RunActions";
import { RunTimeline, useStepFocus } from "./RunTimeline";
import { RunTimeStrip } from "./RunTimeStrip";
import {
  AbandonedNotice,
  CancelledNotice,
  FailureNotice,
  hasInFlightNotice,
  InFlightNotice,
  LaterWriteNotice,
  NewerPlanNotice,
  NewerRunNotice,
  type NoticeTone,
} from "./run-notices";
import { Details, PLAN_ARTIFACT_ID, type PlanRequest, RunFiles } from "./run-sections";
import { StartRunDialog } from "./StartRunDialog";

const LINK = cn(
  "relative rounded-sm text-accent before:absolute before:inset-x-0 before:-inset-y-3 before:content-[''] hover:underline",
  FOCUS
);

const EYEBROW: Record<string, string> = {
  plan: "Run with a review step",
  sync: "Sync without a review step",
};

/** True once `active` has stayed true for `ms`. */
function useAfter(active: boolean, ms: number) {
  const [passed, setPassed] = useState(false);
  useEffect(() => {
    setPassed(false);
    if (!active) return;
    const id = window.setTimeout(() => setPassed(true), ms);
    return () => window.clearTimeout(id);
  }, [active, ms]);
  return active && passed;
}

/** Where the three bars of the strip sit while the run loads. */
const SKELETON_BARS = [
  "h-3 w-1/2 rounded-full",
  "ml-[50%] h-3 w-1/4 rounded-full",
  "ml-[75%] h-3 w-1/4 rounded-full",
];

function RunSkeleton({ slow }: { slow: boolean }) {
  return (
    <div aria-busy="true">
      <output className="sr-only">Loading the run</output>
      <div className="flex flex-col gap-4 pt-6 pb-6">
        <div className="flex h-8 items-center pointer-coarse:h-11">
          <Bar className="h-4 w-72 max-w-full" />
        </div>
        <div className="flex flex-col gap-2">
          <div className="flex h-4 items-center">
            <Bar className="h-3 w-32" />
          </div>
          <Bar className="h-8 w-[28rem] max-w-full max-sm:h-7" />
          <div className="flex h-5 items-center">
            <Bar className="h-4 w-96 max-w-full" />
          </div>
          <div className="-mt-1 flex h-5 items-center sm:hidden">
            <Bar className="h-4 w-48" />
          </div>
        </div>
        <Bar className="h-8 w-28 rounded-lg sm:hidden" />
      </div>
      <Card className="flex flex-col gap-4 p-4 sm:p-5">
        <div className="flex flex-col gap-1.5">
          <Bar className="h-4 w-32" />
          <Bar className="h-3 w-40" />
        </div>
        {["plan", "review", "apply"].map((k, i) => (
          <div
            key={k}
            className="grid grid-cols-[minmax(0,1fr)_5.5rem] items-center gap-x-5 sm:grid-cols-[minmax(0,10rem)_minmax(0,1fr)_5.5rem]"
          >
            <Bar className="h-4 w-20 max-sm:hidden" />
            <Bar className={SKELETON_BARS[i]} />
            <Bar className="ml-auto h-4 w-12" />
          </div>
        ))}
      </Card>
      {slow && (
        <output className="mt-4 block text-foreground-muted text-sm">
          Waiting for the Sync API to answer.
        </output>
      )}
    </div>
  );
}

function Breadcrumb({ run, name }: { run: Run; name: string }) {
  const configId = run.config_id;
  return (
    <nav aria-label="Breadcrumb" className="text-foreground-muted text-sm">
      <ol className="flex flex-wrap items-center gap-x-1.5 max-sm:hidden">
        <li>
          <Link to="/" className={CRUMB}>
            Configurations
          </Link>
        </li>
        <li aria-hidden="true" className="text-subtle-muted">
          /
        </li>
        <li className="min-w-0">
          {configId ? (
            <Link
              to="/configs/$configId"
              params={{ configId }}
              className={cn(CRUMB, "[overflow-wrap:anywhere]")}
            >
              {name}
            </Link>
          ) : (
            <span className="[overflow-wrap:anywhere]">{name}</span>
          )}
        </li>
        <li aria-hidden="true" className="text-subtle-muted">
          /
        </li>
        <li aria-current="page">
          Run <span className="font-mono text-[13px]">{run.run_id}</span>
        </li>
      </ol>
      {configId ? (
        <Link
          to="/configs/$configId"
          params={{ configId }}
          className={cn(CRUMB, "gap-1 sm:hidden")}
        >
          <ArrowRightIcon className="size-3.5 shrink-0 rotate-180 text-subtle" />
          <span className="[overflow-wrap:anywhere]">{name}</span>
        </Link>
      ) : (
        <Link to="/" className={cn(CRUMB, "gap-1 sm:hidden")}>
          <ArrowRightIcon className="size-3.5 shrink-0 rotate-180 text-subtle" />
          Configurations
        </Link>
      )}
    </nav>
  );
}

function MetaLine({
  run,
  state,
  label,
  source,
  destination,
  latestVersion,
  changes,
}: {
  run: Run;
  state: RunState;
  label: string;
  source?: System;
  destination?: System;
  latestVersion?: number;
  /** The plan's counts, which open the changes. */
  changes?: ReactNode;
}) {
  const items: [string, ReactNode][] = [
    [
      "state",
      <output aria-live="polite" aria-atomic="true">
        <StateLabel state={state} label={label} />
      </output>,
    ],
  ];
  if (source && destination) {
    items.push([
      "systems",
      <span className="inline-flex items-center gap-1 text-foreground">
        {source.label}
        <span className="sr-only"> to </span>
        <ArrowRightIcon />
        {destination.label}
      </span>,
    ]);
  }
  if (run.registry_version != null) {
    const newer =
      latestVersion !== undefined && latestVersion > run.registry_version && run.config_id;
    items.push([
      "version",
      <span>
        <span className="tabular-nums">Version {run.registry_version}</span>
        {newer && (
          <span className="text-subtle">
            {" "}
            (
            <Link to="/configs/$configId" params={{ configId: newer }} className={LINK}>
              version {latestVersion} is the latest
            </Link>
            )
          </span>
        )}
      </span>,
    ]);
  }
  if (run.destination_branch) {
    items.push(["branch", <BranchBadge branch={run.destination_branch} />]);
  }
  if (changes) items.push(["changes", changes]);
  return (
    <ul className={META_LIST}>
      {items.map(([key, node]) => (
        <li key={key} className="flex min-w-0 max-w-full">
          {node}
        </li>
      ))}
    </ul>
  );
}

type Dialog = "verify" | "apply" | "cancel" | "plan" | null;

const STOPPED = new Set(["failed", "cancelled", "abandoned", "ambiguous"]);

/**
 * What the run can show of its changes: a list to open, a plan still running, no differences, or
 * counts whose saved plan is gone. A run that stopped before saving a plan shows none.
 */
function changesState(
  run: Run,
  hasPlan: boolean,
  planExpired: boolean,
  counts: ReturnType<typeof runCounts>
): ChangesState | undefined {
  if (hasPlan && counts && counts.total > 0)
    return planExpired ? { kind: "unavailable", counts } : { kind: "list", counts };
  if (hasPlan) return { kind: "none" };
  if (run.outcome == null) return { kind: "pending" };
  if (counts && counts.total > 0) return { kind: "unavailable", counts };
  if (STOPPED.has(run.outcome)) return undefined;
  return { kind: "none" };
}

const isWaitingPlan = (resource: RunResource | undefined) =>
  resource?.run.phase === "planned" &&
  resource.run.outcome === "planned" &&
  !isInProgress(resource);

const time = (iso: string) => new Date(iso).getTime();

export function RunPage({ runId }: { runId: string }) {
  const baseRunQuery = runQuery(runId);
  // A plan waiting for review can be verified, applied or cancelled from the command line or
  // another tab, so it is re-read on the list interval as well.
  const runQ = useQuery({
    ...baseRunQuery,
    refetchInterval: (query) =>
      isWaitingPlan(query.state.data as RunResource | undefined)
        ? LIST_REFRESH_MS
        : typeof baseRunQuery.refetchInterval === "function"
          ? baseRunQuery.refetchInterval(query)
          : false,
  });
  const resource = runQ.data;
  const run = resource?.run;
  const configId = run?.config_id ?? undefined;
  const registryVersion = run?.registry_version ?? undefined;
  const config = useQuery({ ...configQuery(configId ?? ""), enabled: Boolean(configId) });
  const version = useQuery({
    ...configVersionQuery(configId ?? "", registryVersion ?? 0),
    enabled: Boolean(configId) && registryVersion !== undefined,
  });
  const hasPlan = Boolean(run?.artifact_refs.some((a) => a.artifact_id === PLAN_ARTIFACT_ID));
  // The saved plan downloads only when someone opens the changes or the Apply dialog.
  const download = usePlanDownload(run);
  const sheet = useChangesSheet();
  useChangesShortcut(() => sheet.open(), hasPlan && !sheet.isOpen);
  const me = useQuery(meQuery());
  const running = isInProgress(resource);
  const inFlight =
    run?.phase === "accepted"
      ? undefined
      : resource?.orchestration.find((o) => o.terminal_at == null)?.purpose;
  const waitingPlan = run?.phase === "planned" && run.outcome === "planned" && !inFlight;
  const siblings = useQuery({
    ...runsQuery({ config_id: configId ?? "" }),
    enabled: Boolean(configId) && Boolean(run) && !running,
  });
  const [dialog, setDialog] = useState<Dialog>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const focusInActions = useRef(false);
  const slow = useAfter(runQ.isPending && runQ.fetchStatus === "fetching", 5_000);
  const focus = useStepFocus();

  // A poll can remove the focused action (Cancel run once the run ends, Verify once it is verified).
  // The browser then moves focus to the body, so it goes to the title instead, whose state label is
  // announced.
  useLayoutEffect(() => {
    const active = document.activeElement;
    if (focusInActions.current && (!active || active === document.body)) {
      focusInActions.current = false;
      headingRef.current?.focus();
    }
  });

  const trackActionFocus = useCallback((row: HTMLDivElement) => {
    const enter = () => {
      focusInActions.current = true;
    };
    const leave = (e: FocusEvent) => {
      if (row.contains(e.relatedTarget as Node | null)) return;
      window.setTimeout(() => {
        if (!row.contains(document.activeElement)) focusInActions.current = false;
      });
    };
    row.addEventListener("focusin", enter);
    row.addEventListener("focusout", leave);
    return () => {
      row.removeEventListener("focusin", enter);
      row.removeEventListener("focusout", leave);
    };
  }, []);

  const summaryName = run?.summary?.sync_name;
  const name =
    config.data?.name ??
    (typeof summaryName === "string" ? summaryName : undefined) ??
    run?.configuration_reference ??
    runId;
  const siblingRuns = running || !run ? [] : (siblings.data?.items ?? []);
  const wrote = run && waitingPlan ? laterWrite(run, siblingRuns) : undefined;
  // A plan that a newer plan replaced no longer waits for review.
  const newer = run && waitingPlan && !wrote ? newerPlan(run, siblingRuns) : undefined;
  const label = run
    ? newer
      ? "Replaced by a newer plan"
      : phaseLabel(run, { inFlight })
    : undefined;
  const notFound = !resource && runQ.isError && describeError(runQ.error).notFound;
  useDocumentTitle(...(label ? [label, name] : notFound ? ["Run not found"] : ["Runs"]));

  if (runQ.isPending) return <RunSkeleton slow={slow} />;
  if (!resource || !run || !label) {
    return (
      <>
        <nav aria-label="Breadcrumb" className="pt-6 text-foreground-muted text-sm">
          <Link to="/runs" className={CRUMB}>
            Runs
          </Link>
        </nav>
        <ErrorPanel
          level="page"
          error={runQ.error}
          onRetry={() => runQ.refetch()}
          action={
            notFound ? (
              <Link to="/runs" className={buttonVariants({ variant: "outline", size: "sm" })}>
                View runs
              </Link>
            ) : undefined
          }
        >
          {notFound && (
            <p>
              Run ID in the address: <code className={CODE_CHIP}>{runId}</code>
            </p>
          )}
        </ErrorPanel>
      </>
    );
  }

  const { orchestration } = resource;
  const declared = version.data?.declared_content.configuration;
  const source = declared ? describeSystem(declared.source) : undefined;
  const destination = declared ? describeSystem(declared.destination) : undefined;
  const latestVersion = config.data?.latest_version ?? undefined;
  const headerState: RunState = newer ? "idle" : runState(run, { inFlight });
  const refreshError = runQ.isError;
  const open = orchestration.find((o) => o.terminal_at == null);
  const f = failureDetail(run);
  // Runs are listed newest first, so the first later one is the newest run of the configuration.
  const laterRun =
    wrote || newer
      ? undefined
      : siblingRuns.find(
          (r) => r.run_id !== run.run_id && time(r.started_at) > time(run.started_at)
        );

  const verifyFailed = Boolean(run.results?.verify_failure) && !isVerified(run);
  const reviewable = waitingPlan && !refreshError && !verifyFailed;
  const expiresAt = run.artifact_refs.find((a) => a.artifact_id === PLAN_ARTIFACT_ID)?.expires_at;
  const planExpired =
    (expiresAt != null && time(expiresAt) <= Date.now()) ||
    (download.status === "error" && describeError(download.error).status === 410);
  const planRefused = download.status === "error" || download.plan?.checksum_ok === false;
  const steps = runStepList(resource, { inFlight, planExpired, replacedAt: newer?.started_at });
  const executable = executableCount(run) ?? 0;
  const deleteOnly = isDeleteOnlyPlan(run);
  const outdated =
    latestVersion !== undefined && registryVersion !== undefined && latestVersion > registryVersion;
  const canAct = !me.data || me.data.administrator || me.data.actor === run.actor;
  const wouldCancel = Boolean(open) && !refreshError && !open?.cancellation_requested_at;
  const wouldVerify = reviewable && !newer && !isVerified(run) && !planExpired;
  const wouldApply =
    reviewable && executable > 0 && hasPlan && !planExpired && download.plan?.checksum_ok !== false;
  const planVariant: "primary" | "outline" | undefined =
    !configId || running || latestVersion === undefined
      ? undefined
      : reviewable
        ? planExpired || wrote
          ? "primary"
          : outdated || deleteOnly || planRefused
            ? "outline"
            : undefined
        : headerState === "insync" || laterRun
          ? "outline"
          : headerState === "failed" || headerState === "idle"
            ? "primary"
            : undefined;

  const counts = runCounts(run);
  const changes = changesState(run, hasPlan, planExpired, counts);
  // The branch the run writes to, even when the run records none, as the Apply dialog resolves it.
  const writesTo = run.destination_branch ?? (destination ? (destination.branch ?? "main") : null);
  const planRequest: PlanRequest = {
    data: download.plan,
    error: download.error,
    isPending: false,
    refetch: download.start,
  };

  // Notices about other runs go in the run summary; the run's own outcome goes under its step.
  const notices: { id: string; tone: NoticeTone; node: ReactNode }[] = wrote
    ? [
        {
          id: "later-write",
          tone: "warning",
          node: <LaterWriteNotice run={run} other={wrote} deleteOnly={deleteOnly} />,
        },
      ]
    : newer
      ? [{ id: "newer-plan", tone: "neutral", node: <NewerPlanNotice other={newer} /> }]
      : laterRun
        ? [{ id: "newer-run", tone: "neutral", node: <NewerRunNotice other={laterRun} /> }]
        : [];
  const outcome = f ? (
    <FailureNotice
      run={run}
      f={f}
      orchestration={orchestration}
      plan={download.plan}
      source={source}
      destination={destination}
    />
  ) : run.phase === "abandoned" ? (
    <AbandonedNotice run={run} orchestration={orchestration} newerRun={Boolean(laterRun)} />
  ) : run.phase === "cancelled" ? (
    <CancelledNotice orchestration={orchestration} />
  ) : open && hasInFlightNotice(open) ? (
    <InFlightNotice open={open} />
  ) : undefined;

  const close = () => setDialog(null);
  const focusHeading = () => requestAnimationFrame(() => headingRef.current?.focus());

  return (
    <>
      <header className="flex flex-col gap-4 pt-6 pb-6">
        <Breadcrumb run={run} name={name} />
        <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-4">
          <div className="flex min-w-0 flex-col gap-2">
            {EYEBROW[run.operation] && (
              <p className="font-medium text-subtle text-xs">{EYEBROW[run.operation]}</p>
            )}
            <h1
              ref={headingRef}
              tabIndex={-1}
              className="hyphens-none text-balance font-semibold text-2xl tracking-tight outline-none [overflow-wrap:anywhere] max-sm:text-xl"
            >
              {name}
            </h1>
            <MetaLine
              run={run}
              state={headerState}
              label={label}
              source={source}
              destination={destination}
              latestVersion={latestVersion}
              changes={
                changes?.kind === "list" ? (
                  <ChangeCount counts={changes.counts} onOpen={() => sheet.open()} />
                ) : undefined
              }
            />
          </div>
          <div
            ref={trackActionFocus}
            className="flex flex-wrap items-center gap-2 pointer-coarse:[&>button]:h-11"
          >
            {run.phase === "accepted" && !open && (
              <p className="text-foreground-muted text-sm">
                Waiting for the Sync service to submit this run to Prefect.
              </p>
            )}
            {open?.cancellation_requested_at && (
              <p className="text-foreground-muted text-sm">
                <output>Cancellation requested</output>{" "}
                <When iso={open.cancellation_requested_at} />
              </p>
            )}
            {me.data && !canAct && (wouldCancel || wouldVerify || wouldApply) && (
              <p className="max-w-[40ch] text-foreground-muted text-xs">
                Only {run.actor} or an administrator can verify, apply or cancel this run.
              </p>
            )}
            {canAct && wouldCancel && (
              <Button variant="danger-outline" size="sm" onPress={() => setDialog("cancel")}>
                Cancel run
              </Button>
            )}
            {canAct && wouldVerify && (
              <Button variant="outline" size="sm" onPress={() => setDialog("verify")}>
                Check the plan
              </Button>
            )}
            {canAct && wouldApply && (
              <ApplyButton
                label={`Apply ${fmt(executable)} ${executable === 1 ? "change" : "changes"}`}
                variant={wrote || newer ? "outline" : "primary"}
                onPress={() => setDialog("apply")}
              />
            )}
            {planVariant && (
              <Button variant={planVariant} size="sm" onPress={() => setDialog("plan")}>
                <PlusIcon />
                {outdated ? `Plan version ${latestVersion}` : "Plan again"}
              </Button>
            )}
          </div>
        </div>
      </header>
      {refreshError && (
        <StaleNotice
          error={runQ.error}
          updatedAt={runQ.dataUpdatedAt}
          onRetry={() => runQ.refetch()}
        />
      )}
      <div className="flex flex-col gap-8">
        <RunTimeStrip
          run={run}
          steps={steps}
          planExpired={planExpired}
          onSelect={focus.select}
          selected={focus.selected}
          notices={notices}
          replaced={Boolean(newer)}
        />
        <RunTimeline
          run={run}
          steps={steps}
          plan={planRequest}
          hasPlan={hasPlan}
          onOpenChanges={() => sheet.open()}
          source={source}
          destination={destination}
          state={headerState}
          label={label}
          focus={focus}
          notice={outcome}
        />
        <RunFiles run={run} />
        <Details run={run} name={name} source={source} destination={destination} />
      </div>
      {dialog === "verify" && <VerifyDialog run={run} onClose={close} onDone={focusHeading} />}
      {dialog === "apply" && <ApplyWithPlan run={run} onClose={close} onDone={focusHeading} />}
      {changes && !sheet.isOpen && (
        <ChangesDock
          run={run}
          state={changes}
          branch={writesTo}
          onOpen={() => sheet.open()}
          morph={sheet.morph}
          replaced={Boolean(newer)}
        />
      )}
      {sheet.isOpen && changes?.kind === "list" && (
        <ChangesSheet
          run={run}
          counts={changes.counts}
          branch={writesTo}
          filter={sheet.filter}
          onFilterChange={sheet.setFilter}
          close={sheet.close}
          morph={sheet.morph}
          replaced={Boolean(newer)}
        />
      )}
      {dialog === "cancel" && <CancelDialog run={run} onClose={close} onDone={focusHeading} />}
      {dialog === "plan" && configId && latestVersion !== undefined && (
        <StartRunDialog
          target={{
            configId,
            name,
            version: latestVersion,
            ...((run.phase === "planned" || run.phase === "applied") && run.destination_branch
              ? { branch: run.destination_branch, branchFromRunId: run.run_id }
              : {}),
          }}
          onClose={close}
        />
      )}
    </>
  );
}
