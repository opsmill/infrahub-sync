import { Link } from "@tanstack/react-router";
import { type ReactNode, useId, useState } from "react";
import { cn } from "tailwind-variants";

import type { Orchestration, Plan, Run } from "#/api/types";
import { ArrowRightIcon } from "#/components/icons";
import { CopyButton, FOCUS, InlineCode, Notice } from "#/components/page";
import { BranchBadge, When } from "#/components/run-status";
import { executableCount, type FailureDetail, fmt, phaseLabel } from "#/domain/run-state";
import type { System } from "#/domain/systems";
import { useNow } from "#/lib/time";

import { identityParts } from "./plan-values";

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
        {f.recoveryAction ? (
          // The recorded instruction says the same in command-line words, so it is left out.
          <p>
            {f.recoveryAction === "compatible_version" &&
              "This version of the Sync service does not apply the format of the saved plan. "}
            To continue here, create a new plan, review it, then apply that plan.
            {f.recoveryAction === "compatible_version" &&
              " The saved plan can still be applied with the version of the Sync service that saved it."}
          </p>
        ) : (
          f.nextAction && (
            <>
              <p>
                <span className={LABEL}>Command-line instruction recorded by the Sync service</span>
                <InlineCode text={f.nextAction} />
              </p>
              <p>To continue here, create a new plan, review it, then apply that plan.</p>
            </>
          )
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
                <BranchBadge branch={run.destination_branch} /> in{" "}
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

export type NoticeTone = "neutral" | "warning";

/* ---------- notices about other runs: the sheets tucked under the run summary ---------- */

const CHIP: Record<NoticeTone, string> = { neutral: "bg-accent", warning: "bg-warning-strong" };
const TITLE: Record<NoticeTone, string> = { neutral: "text-accent", warning: "text-warning" };

const glyph = {
  fill: "none",
  stroke: "currentColor",
  strokeLinecap: "round",
  strokeWidth: 1.75,
} as const;

/** The tone's mark: a white glyph on a solid circle. */
function ToneMark({ tone }: { tone: NoticeTone }) {
  return (
    <span
      className={cn(
        "mt-px grid size-[18px] shrink-0 place-items-center rounded-full text-content shadow-[0_1px_1px_rgb(0_0_0/0.12)]",
        CHIP[tone]
      )}
    >
      <svg viewBox="0 0 16 16" aria-hidden="true" className="size-3">
        {tone === "neutral" ? (
          <>
            <path d="M8 7v4.5" {...glyph} />
            <circle cx="8" cy="4.4" r="1" fill="currentColor" />
          </>
        ) : (
          <>
            <path d="M8 3.5v5" {...glyph} />
            <circle cx="8" cy="11.9" r="1" fill="currentColor" />
          </>
        )}
      </svg>
    </span>
  );
}

/** A small button, its hit area grown to 40px; its ring takes the sheet's colour. */
const OPEN_RUN = cn(
  "relative inline-flex h-7 shrink-0 items-center gap-1 rounded-lg bg-content/80 px-2.5 font-medium text-[13px] text-foreground",
  "shadow-[0_0_0_1px_color-mix(in_oklab,var(--notice-tone)_26%,transparent),0_1px_2px_rgb(0_0_0/0.06)] hover:bg-content",
  "before:absolute before:inset-x-0 before:-inset-y-1.5 before:content-['']",
  "transition-[background-color,scale] duration-150 ease-out active:scale-[0.97] motion-reduce:transition-none",
  FOCUS
);

const WHY = cn(
  "relative ml-1 rounded-sm font-medium text-foreground underline decoration-current/30 underline-offset-2 sm:hidden",
  "before:absolute before:-inset-x-1 before:-inset-y-3 before:content-['']",
  FOCUS
);

/** The other run's ID, as text: "Open run" is the row's one link. */
function RunId({ id }: { id: string }) {
  return (
    <span className="font-mono text-[13px] text-foreground [overflow-wrap:anywhere]">{id}</span>
  );
}

/** One notice in the sheet tucked under the run summary: about another run, which it opens. */
function SheetNotice({
  tone,
  id,
  title,
  other,
  more,
  children,
}: {
  tone: NoticeTone;
  id: string;
  title: string;
  other: Run;
  /** The explanation that a phone shows behind "Why?". */
  more?: string;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const moreId = useId();
  return (
    <div role="note" id={id} className="flex flex-wrap items-start gap-x-2.5 gap-y-2 pt-3">
      <ToneMark tone={tone} />
      <div className="flex min-w-0 flex-1 basis-64 flex-col gap-1">
        <p className="text-pretty text-foreground-muted text-sm">
          <span className={cn("font-medium [overflow-wrap:anywhere]", TITLE[tone])}>{title}.</span>{" "}
          {children}
          {more && <span className="max-sm:hidden"> {more}</span>}
          {more && (
            <button
              type="button"
              aria-expanded={open}
              aria-controls={moreId}
              onClick={() => setOpen((o) => !o)}
              className={WHY}
            >
              {open ? "Less" : "Why?"}
            </button>
          )}
        </p>
        {more && open && (
          <p id={moreId} className="text-pretty text-foreground-muted text-sm sm:hidden">
            {more}
          </p>
        )}
      </div>
      <Link to="/runs/$runId" params={{ runId: other.run_id }} className={OPEN_RUN}>
        Open run
        <ArrowRightIcon className="size-3.5 shrink-0 text-subtle" />
      </Link>
    </div>
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
    <SheetNotice
      tone="warning"
      id="stale-plan"
      title={`Another run wrote to ${run.destination_branch ?? "the destination"} after this plan was made`}
      other={other}
      more={
        deleteOnly
          ? undefined
          : "An apply writes the operations saved in this plan. It checks the destination schema but not the destination's data, so it does not detect what that run changed."
      }
    >
      Run <RunId id={other.run_id} /> ended{" "}
      {other.finished_at && (
        <>
          <When iso={other.finished_at} />{" "}
        </>
      )}
      with the state “{phaseLabel(other)}”. Create a new plan to compare against the current
      destination.
    </SheetNotice>
  );
}

export function NewerPlanNotice({ other }: { other: Run }) {
  return (
    <SheetNotice
      tone="neutral"
      id="newer-plan"
      title="A newer plan is waiting for review"
      other={other}
    >
      Run <RunId id={other.run_id} />{" "}
      {other.registry_version != null
        ? `planned version ${other.registry_version},`
        : "was planned"}{" "}
      <When iso={other.started_at} />.
    </SheetNotice>
  );
}

/** A run of the same configuration started after this one, so this run no longer asks for action. */
export function NewerRunNotice({ other }: { other: Run }) {
  return (
    <SheetNotice
      tone="neutral"
      id="newer-run"
      title="A newer run of this configuration exists"
      other={other}
    >
      Run <RunId id={other.run_id} /> started <When iso={other.started_at} /> and has the state “
      {phaseLabel(other)}”.
    </SheetNotice>
  );
}

/* ---------- the run's own outcome: a box under its step in the timeline ---------- */

const timeOfDay = (iso: string) =>
  new Date(iso).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });

function WaitedMinutes({ from }: { from: string }) {
  const now = useNow(1000);
  return <>{Math.max(1, Math.floor((now - new Date(from).getTime()) / 60_000))} min</>;
}

function Until({ iso }: { iso: string }) {
  const now = useNow(1000);
  const minutes = Math.max(1, Math.ceil((new Date(iso).getTime() - now) / 60_000));
  return (
    <>
      <time dateTime={iso}>{timeOfDay(iso)}</time> (in {minutes} min)
    </>
  );
}

const writes = (purpose?: string) => purpose === "apply" || purpose === "sync";

/**
 * Why an abandoned run read and wrote nothing: no worker picked up a step, or a cancellation came
 * first. Once a newer run exists, it gives no advice.
 */
export function AbandonedNotice({
  run,
  orchestration,
  newerRun,
}: {
  run: Run;
  orchestration: Orchestration[];
  newerRun: boolean;
}) {
  const o = orchestration.at(-1);
  if (!o) {
    return (
      <Notice
        tone="warning"
        role="note"
        id="abandoned"
        titleAs="h2"
        title="No execution was recorded for this run"
      >
        <Body>
          <p>Nothing was read or written.{newerRun ? "" : " Plan again."}</p>
        </Body>
      </Notice>
    );
  }
  if (o.cancellation_requested_at) {
    return (
      <Notice
        tone="neutral"
        role="note"
        id="abandoned"
        titleAs="h2"
        title={`Cancelled before a worker picked up the ${o.purpose}`}
      >
        <Body>
          <p>
            Cancellation was requested <When iso={o.cancellation_requested_at} />. Nothing was read
            or written.
          </p>
        </Body>
      </Notice>
    );
  }
  const earlierRan = orchestration.some((e) => e !== o && e.terminal_outcome === "succeeded");
  return (
    <Notice
      tone="warning"
      role="note"
      id="abandoned"
      titleAs="h2"
      title={`No worker picked up the ${o.purpose}`}
    >
      <Body>
        <p>
          The Sync service stopped waiting{" "}
          <When iso={o.terminal_at ?? run.finished_at ?? run.started_at} />. Nothing was read or
          written{earlierRan ? ` by the ${o.purpose}` : ""}.
          {newerRun ? "" : " Check that a worker is running, then plan again."}
        </p>
      </Body>
    </Notice>
  );
}

/** When a cancelled run was cancelled, and whether the cancelled step could write. */
export function CancelledNotice({ orchestration }: { orchestration: Orchestration[] }) {
  const o = [...orchestration].reverse().find((e) => e.cancellation_requested_at);
  return (
    <Notice tone="neutral" role="note" id="cancelled" titleAs="h2" title="This run was cancelled">
      <Body>
        <p>
          {o?.cancellation_requested_at ? (
            <>
              Cancellation was requested <When iso={o.cancellation_requested_at} />
              {o.claimed_at
                ? `, after a worker started the ${o.purpose}.`
                : `, before a worker picked up the ${o.purpose}.`}
              {!writes(o.purpose) && ` A ${o.purpose} does not write to the destination.`}
            </>
          ) : (
            "The Sync service did not record when cancellation was requested."
          )}
        </p>
      </Body>
    </Notice>
  );
}

/** True when an execution in flight has a notice: someone asked to cancel it, or no worker picked it up. */
export const hasInFlightNotice = (open: Orchestration) =>
  Boolean(open.cancellation_requested_at) || (Boolean(open.stalled_at) && !open.claimed_at);

/** An execution in flight that someone asked to cancel, or that no worker has picked up. */
export function InFlightNotice({ open }: { open: Orchestration }) {
  if (!hasInFlightNotice(open)) return null;
  if (open.cancellation_requested_at) {
    const deadline = open.cancellation_recovery_deadline_at;
    return (
      <Notice
        tone="neutral"
        role="note"
        id="cancelling"
        titleAs="h2"
        title="Cancellation requested"
      >
        <Body>
          <p>
            {deadline && (
              <>
                If no worker confirms it by <Until iso={deadline} />, the Sync service ends the{" "}
                {open.purpose}.{" "}
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
  return (
    <Notice
      tone="warning"
      role="note"
      id="stalled"
      titleAs="h2"
      title={`No worker has picked up this ${open.purpose}`}
    >
      <Body>
        <p>
          {open.submitted_at && (
            <>
              The {open.purpose} has waited <WaitedMinutes from={open.submitted_at} />, since{" "}
              {timeOfDay(open.submitted_at)}.{" "}
            </>
          )}
          Check that a worker is running.
        </p>
      </Body>
    </Notice>
  );
}
