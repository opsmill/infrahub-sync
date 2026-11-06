import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";

import type { Orchestration, Plan, Run } from "#/api/types";
import { CopyButton, FOCUS, InlineCode, Notice } from "#/components/page";
import { BranchCode, When } from "#/components/run-status";
import { executableCount, type FailureDetail, fmt, phaseLabel } from "#/domain/run-state";
import type { System } from "#/domain/systems";
import { formatAbsolute } from "#/lib/time";

import { identityParts } from "./PlanReview";

const RUN_LINK = `rounded-sm font-mono text-[13px] text-accent hover:underline ${FOCUS}`;
const LABEL = "block text-subtle text-xs";
const CONNECT_ERRORS = new Set([
  "ConnectError",
  "ConnectTimeout",
  "ReadTimeout",
  "ConnectionError",
]);

const capitalise = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

/** The body column of a notice: paragraphs kept to a readable width. */
function Body({ children }: { children: ReactNode }) {
  return <div className="flex max-w-[70ch] flex-col gap-3 pt-1">{children}</div>;
}

/**
 * The execution that ended without a result, and whether it could have written. Only an apply or a
 * sync writes, so the Sync service sets `reconciliation_required` for those two purposes alone.
 */
function interruption(run: Run, orchestration: Orchestration[]) {
  const o = [...orchestration].reverse().find((e) => e.terminal_outcome === "ambiguous");
  if (!o && !run.reconciliation_required && run.outcome !== "ambiguous") return undefined;
  const purpose = o?.purpose;
  const mayHaveWritten =
    run.reconciliation_required ||
    purpose === undefined ||
    purpose === "apply" ||
    purpose === "sync";
  return { purpose, mayHaveWritten };
}

function failureTitle(f: FailureDetail, cut: ReturnType<typeof interruption>): string {
  if (cut?.mayHaveWritten) return "The write may have reached the destination";
  if (cut?.purpose) return `The ${cut.purpose} was interrupted`;
  if (f.known) return f.sentence;
  return `Why the ${f.stage.toLowerCase()} failed`;
}

const changesWord = (n: number) => (n === 1 ? "change" : "changes");

function FailedOn({ id, plan }: { id: string; plan?: Plan }) {
  const op = plan?.operations.find((o) => o.operation_id === id);
  const idLine = (
    <span className="block select-all font-mono text-subtle text-xs [overflow-wrap:anywhere]">
      {id}
    </span>
  );
  if (!op) return idLine;
  return (
    <>
      <span className="[overflow-wrap:anywhere]">
        {capitalise(op.action)} {op.kind} {identityParts(op.identity).name}
      </span>
      {idLine}
    </>
  );
}

/** Why the run failed, in words, where the full message is, and which systems the run reads and writes. */
export function FailureNotice({
  run,
  f,
  orchestration,
  plan,
  source,
  destination,
}: {
  run: Run;
  f: FailureDetail;
  orchestration: Orchestration[];
  plan?: Plan;
  source?: System;
  destination?: System;
}) {
  const failedExec = [...orchestration]
    .reverse()
    .find((o) => o.terminal_outcome === "failed" || o.terminal_outcome === "ambiguous");
  const cut = interruption(run, orchestration);
  const stage = cut?.purpose ?? f.stage;
  const connect = [f.causeType, f.errorType].some((t) => t !== undefined && CONNECT_ERRORS.has(t));
  const summary = (run.summary ?? {}) as Record<string, unknown>;
  const skippedDeletes =
    typeof summary.skipped_delete_count === "number" ? summary.skipped_delete_count : undefined;
  const executable = executableCount(run);
  const at = (host?: string) =>
    host && (
      <>
        {" "}
        at <span className="font-mono text-xs [overflow-wrap:anywhere]">{host}</span>
      </>
    );

  return (
    <Notice
      tone="danger"
      role="note"
      id="failure"
      titleAs="h2"
      titleId="failure-title"
      title={failureTitle(f, cut)}
    >
      <Body>
        {f.cause && (
          <p>
            <span className={LABEL}>Cause recorded by the Sync service</span>
            <InlineCode text={f.cause} />
          </p>
        )}
        {f.nextAction && (
          <>
            <p>To continue here, create a new plan, review it, then apply that plan.</p>
            <p>
              <span className={LABEL}>Command-line instruction recorded by the Sync service</span>
              <InlineCode text={f.nextAction} />
            </p>
          </>
        )}
        {cut && !cut.mayHaveWritten && (
          <p>
            The worker stopped before it reported a result. A {cut.purpose} does not write to the
            destination. Create a new plan.
          </p>
        )}
        {!f.cause && !f.nextAction && !cut && (
          <p>
            The Sync service records only the error type for this failure. The full message is in
            the state message of{" "}
            {failedExec ? "this Prefect flow run" : "the failed Prefect flow run"} and in the
            worker's log.
            {failedExec && (
              <span className="mt-1 flex min-w-0 items-center gap-1">
                <code className="min-w-0 font-mono text-foreground text-xs [overflow-wrap:anywhere]">
                  {failedExec.flow_run_id}
                </code>
                <CopyButton value={failedExec.flow_run_id} label="Copy flow run ID" />
              </span>
            )}
          </p>
        )}
        {connect && source && destination && (
          <p>
            This configuration reads {source.label}
            {at(source.host)} and writes to {destination.label}
            {at(destination.host)}. The Sync service did not record which of them failed.
          </p>
        )}
        {cut?.mayHaveWritten && (
          <p>
            The Sync service could not confirm what was written. Inspect{" "}
            {run.destination_branch ? (
              <>
                <BranchCode branch={run.destination_branch} /> in{" "}
              </>
            ) : null}
            {destination?.label ?? "the destination"}, then create a new plan. The new plan proposes
            only what is still missing or different.
          </p>
        )}
        <dl className="grid grid-cols-[auto_minmax(0,1fr)] items-baseline gap-x-4 gap-y-1 text-[13px]">
          <dt className="text-subtle">Stage</dt>
          <dd className="text-foreground">{capitalise(stage)}</dd>
          {(f.errorType || f.causeType) && (
            <>
              <dt className="text-subtle">Error</dt>
              <dd className="font-mono text-foreground text-xs leading-5 [overflow-wrap:anywhere]">
                {f.errorType}
                {f.errorType && f.causeType && <span className="text-subtle"> caused by </span>}
                {f.causeType}
              </dd>
            </>
          )}
          {f.failedOperation && (
            <>
              <dt className="text-subtle">Failed on</dt>
              <dd className="text-foreground">
                <FailedOn id={f.failedOperation} plan={plan} />
              </dd>
            </>
          )}
          {f.appliedCount !== undefined && (
            <>
              <dt className="text-subtle">Written before the failure</dt>
              <dd className="text-foreground tabular-nums">
                {executable !== undefined
                  ? `${fmt(f.appliedCount)} of ${fmt(executable)} ${changesWord(executable)}`
                  : `${fmt(f.appliedCount)} ${changesWord(f.appliedCount)}`}
              </dd>
            </>
          )}
          {skippedDeletes !== undefined && (
            <>
              <dt className="text-subtle">Deletes</dt>
              <dd className="text-foreground tabular-nums">
                {fmt(skippedDeletes)} listed, not applied
              </dd>
            </>
          )}
        </dl>
      </Body>
    </Notice>
  );
}

/** An abandoned execution was never picked up by a worker, so it read and wrote nothing. */
export function AbandonedNotice({
  run,
  orchestration,
}: {
  run: Run;
  orchestration: Orchestration[];
}) {
  const o = orchestration.at(-1);
  const ended = o?.terminal_at ?? run.finished_at;
  const earlierRan = orchestration.some((e) => e !== o && e.terminal_outcome === "succeeded");
  const nothing =
    earlierRan && o
      ? `Nothing was read or written by the ${o.purpose}.`
      : "Nothing was read or written.";
  return (
    <Notice
      tone="neutral"
      role="note"
      id="abandoned"
      titleAs="h2"
      titleId="abandoned-title"
      title="Why this run was abandoned"
    >
      <Body>
        {!o ? (
          <p>The Sync service recorded no execution for this run.</p>
        ) : o.cancellation_requested_at ? (
          <p>
            Cancellation was requested <When iso={o.cancellation_requested_at} />, before a worker
            picked up the {o.purpose}. {nothing}
          </p>
        ) : (
          <p>
            No worker picked up the {o.purpose} within the admission time, so the Sync service
            stopped waiting
            {ended && (
              <>
                {" "}
                <When iso={ended} />
              </>
            )}
            . {nothing} Check that a worker is running, then plan again.
          </p>
        )}
      </Body>
    </Notice>
  );
}

/** An execution in flight that someone asked to cancel, or that no worker has picked up. */
export function InFlightNotice({ open }: { open: Orchestration }) {
  if (open.cancellation_requested_at) {
    const deadline = open.cancellation_recovery_deadline_at;
    return (
      <Notice
        tone="neutral"
        role="note"
        id="cancelling"
        titleAs="h2"
        titleId="cancelling-title"
        title="Cancellation requested"
      >
        <Body>
          <p>
            {deadline && (
              <>
                If no worker confirms the cancellation by{" "}
                <time dateTime={deadline}>{formatAbsolute(deadline)}</time>, the Sync service ends
                the {open.purpose}.{" "}
              </>
            )}
            {open.claimed_at
              ? "A worker already started it, so its outcome is then unknown."
              : "No worker has picked it up, so nothing has been read or written."}
          </p>
        </Body>
      </Notice>
    );
  }
  if (!open.stalled_at || open.claimed_at) return null;
  return (
    <Notice
      tone="warning"
      role="note"
      id="stalled"
      titleAs="h2"
      titleId="stalled-title"
      title={`No worker has picked up this ${open.purpose}`}
    >
      <Body>
        <p>
          The {open.purpose} has waited
          {open.submitted_at && (
            <>
              {" "}
              since <When iso={open.submitted_at} />
            </>
          )}
          . Check that a worker is running.
        </p>
      </Body>
    </Notice>
  );
}

function RunLink({ runId }: { runId: string }) {
  return (
    <Link to="/runs/$runId" params={{ runId }} className={RUN_LINK}>
      {runId}
    </Link>
  );
}

/** Another run wrote to the same branch after this plan was made; apply does not detect what it changed. */
export function LaterWriteNotice({
  run,
  other,
  deleteOnly,
}: {
  run: Run;
  other: Run;
  /** A plan of deletes alone offers no apply, so the notice does not describe one. */
  deleteOnly: boolean;
}) {
  return (
    <Notice
      tone="warning"
      role="note"
      id="stale-plan"
      titleAs="h2"
      titleId="stale-plan-title"
      title={`Another run wrote to ${run.destination_branch ?? "the destination"} after this plan was made`}
    >
      <Body>
        <p>
          Run <RunLink runId={other.run_id} /> ended{" "}
          {other.finished_at && (
            <>
              <When iso={other.finished_at} />{" "}
            </>
          )}
          with the state “{phaseLabel(other)}”.{" "}
          {!deleteOnly &&
            "An apply writes the operations saved in this plan. It checks the destination schema but not the destination's data, so it does not detect what that run changed. "}
          Create a new plan to compare against the current destination.
        </p>
      </Body>
    </Notice>
  );
}

export function NewerPlanNotice({ other }: { other: Run }) {
  return (
    <Notice
      tone="neutral"
      role="note"
      id="newer-plan"
      titleAs="h2"
      titleId="newer-plan-title"
      title="A newer plan is waiting for review"
    >
      <Body>
        <p>
          Run <RunLink runId={other.run_id} />{" "}
          {other.registry_version != null
            ? `planned version ${other.registry_version}`
            : "was planned"}{" "}
          <When iso={other.started_at} />.
        </p>
      </Body>
    </Notice>
  );
}

/** A run of the same configuration started after this one, so this run no longer asks for action. */
export function NewerRunNotice({ other }: { other: Run }) {
  return (
    <Notice
      tone="neutral"
      role="note"
      id="newer-run"
      titleAs="h2"
      titleId="newer-run-title"
      title="A newer run of this configuration exists"
    >
      <Body>
        <p>
          Run <RunLink runId={other.run_id} /> started <When iso={other.started_at} /> and has the
          state “{phaseLabel(other)}”.
        </p>
      </Body>
    </Notice>
  );
}
