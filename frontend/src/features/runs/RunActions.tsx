import { Button } from "@infrahub/ui";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { type ReactNode, useState } from "react";

import { useApplyRun, useCancelRun, useIdempotencyKey, useVerifyRun } from "#/api/actions";
import { configQuery, configVersionQuery, runQuery, runsQuery } from "#/api/queries";
import type { Plan, Run } from "#/api/types";
import { CheckIcon, PlayIcon, StateIcon } from "#/components/icons";
import { CopyButton, Notice } from "#/components/page";
import { BranchCode, CHANGE_TONE, When } from "#/components/run-status";
import { fmt, isVerified, laterWrite, newerPlan } from "#/domain/run-state";
import { describeSystem } from "#/domain/systems";

import {
  DialogFrame,
  Field,
  finePointer,
  INPUT,
  LINK,
  MutationError,
  ReasonField,
  resendHint,
} from "./form";

const changes = (n: number) => `${fmt(n)} ${n === 1 ? "change" : "changes"}`;

function RunLink({ run, onLeave }: { run: Run; onLeave: () => void }) {
  return (
    <Link to="/runs/$runId" params={{ runId: run.run_id }} className={LINK} onClick={onLeave}>
      {run.run_id}
    </Link>
  );
}

export function VerifyDialog({
  run,
  onClose,
  onDone,
}: {
  run: Run;
  onClose: () => void;
  onDone?: () => void;
}) {
  const verify = useVerifyRun();
  const [key, renew] = useIdempotencyKey();
  const [reason, setReason] = useState("");
  const submitLabel = "Verify plan";
  return (
    <DialogFrame
      isOpen
      onOpenChange={(open) => !open && onClose()}
      title="Verify the plan"
      ariaLabel="Verify the plan"
      description={
        <>
          <p>
            Checks that the saved plan is intact: its checksum, the copy of the source data it was
            made from and{" "}
            {run.registry_version == null
              ? "the configuration version it was made from"
              : `configuration version ${run.registry_version}`}
            . Reads neither the source nor the destination, and writes nothing.
          </p>
          <p className="mt-1 text-subtle text-xs">
            Optional. Apply runs the same checks before its first write.
          </p>
        </>
      }
      error={
        verify.error ? (
          <MutationError
            error={verify.error}
            currentRunId={run.run_id}
            retryHint={resendHint(submitLabel)}
          />
        ) : undefined
      }
      submitLabel={submitLabel}
      pendingLabel="Requesting verification"
      isPending={verify.isPending}
      onSubmit={() =>
        verify.mutate(
          {
            params: { path: { run_id: run.run_id }, header: { "Idempotency-Key": key } },
            body: { reason: reason.trim() || null },
          },
          {
            onSuccess: () => {
              onClose();
              onDone?.();
            },
          }
        )
      }
    >
      <ReasonField
        id="verify-reason"
        value={reason}
        onChange={(v) => {
          setReason(v);
          renew();
          verify.reset();
        }}
        autoFocus={finePointer()}
      />
    </DialogFrame>
  );
}

/** `terminal_at` of the latest verification that completed, from the run's executions. */
function verifiedAt(
  orchestration: { purpose: string; terminal_state: string | null; terminal_at: string | null }[]
) {
  return orchestration
    .filter((o) => o.purpose === "verify" && o.terminal_state === "completed" && o.terminal_at)
    .map((o) => o.terminal_at as string)
    .sort()
    .at(-1);
}

function ChangeLine({ tone, children }: { tone: keyof typeof CHANGE_TONE; children: ReactNode }) {
  return (
    <li className="flex items-baseline gap-2">
      <span aria-hidden="true" className={`w-3 shrink-0 font-mono ${CHANGE_TONE[tone].text}`}>
        {CHANGE_TONE[tone].sign}
      </span>
      <span className="min-w-0 text-pretty">{children}</span>
    </li>
  );
}

export function ApplyDialog({
  run,
  plan,
  onClose,
  onDone,
}: {
  run: Run;
  plan: Plan;
  onClose: () => void;
  onDone?: () => void;
}) {
  const apply = useApplyRun();
  const [key, renew] = useIdempotencyKey();
  const [reason, setReason] = useState("");
  const [typed, setTyped] = useState("");

  const configId = run.config_id ?? "";
  const n = run.registry_version;
  const version = useQuery({
    ...configVersionQuery(configId, n ?? 0),
    enabled: Boolean(run.config_id) && n != null,
  });
  const latest = useQuery({ ...configQuery(configId), enabled: Boolean(run.config_id) }).data
    ?.latest_version;
  const siblings = useQuery({
    ...runsQuery({ config_id: configId }),
    enabled: Boolean(run.config_id),
  });
  const resource = useQuery(runQuery(run.run_id)).data;

  const by = plan.summary.by_action;
  const create = by.create ?? 0;
  const update = by.update ?? 0;
  const written = create + update;
  const notRun = plan.summary.deletes_not_executed;
  const checksumRefused = plan.checksum_ok === false;

  const destination = version.data
    ? describeSystem(version.data.declared_content.configuration.destination)
    : undefined;
  const requested = run.destination_branch ?? plan.destination_branch ?? null;
  const branch = requested ?? (destination ? (destination.branch ?? "main") : undefined);
  const versionKnown = Boolean(run.config_id) && n != null;
  const branchLoading = branch === undefined && version.isPending && version.fetchStatus !== "idle";
  const matches = branch !== undefined && typed.trim() === branch;
  const submitLabel = `Apply ${changes(written)}`;

  let notice: ReactNode;
  const later = siblings.data && laterWrite(run, siblings.data.items);
  const newer = siblings.data && newerPlan(run, siblings.data.items);
  if (checksumRefused) {
    notice = (
      <Notice tone="danger" role="alert">
        <p>
          The saved plan does not match its checksum. The Sync service refuses to apply it. Plan
          again.
        </p>
        {plan.verification_notes.length > 0 && (
          <ul className="mt-1 list-disc pl-4 text-xs">
            {plan.verification_notes.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        )}
      </Notice>
    );
  } else if (later) {
    notice = (
      <Notice tone="warning" role="note">
        <p>
          Run <RunLink run={later} onLeave={onClose} />{" "}
          {later.phase === "applied" && later.outcome === "applied" ? "wrote" : "may have written"}{" "}
          to {branch ? <BranchCode branch={branch} /> : "the destination branch"}{" "}
          {later.finished_at && <When iso={later.finished_at} />}, after this plan was made. Apply
          writes the operations saved in this plan and does not detect what that run changed.
        </p>
      </Notice>
    );
  } else if (newer) {
    notice = (
      <Notice role="note">
        <p>
          A newer plan of this configuration waits for review:{" "}
          <RunLink run={newer} onLeave={onClose} />.
        </p>
      </Notice>
    );
  } else if (siblings.data && n != null && latest != null && n < latest) {
    notice = (
      <Notice role="note">
        <p>
          This plan was made from version {n}. Version {latest} is the latest, and this plan does
          not use its changes.
        </p>
      </Notice>
    );
  }

  const verified = isVerified(run);
  const at = resource && verifiedAt(resource.orchestration);

  const confirmHint = branchLoading ? (
    <>Loading the branch that version {n} sets.</>
  ) : branch !== undefined ? undefined : versionKnown ? (
    "The branch of this plan could not be read. Reload the page."
  ) : (
    "The run does not record its configuration version, so the branch it writes to is not known."
  );

  return (
    <DialogFrame
      isOpen
      onOpenChange={(open) => !open && onClose()}
      title={
        <>
          Apply {changes(written)}
          {branch && (
            <>
              {" "}
              to{" "}
              <span className="font-normal">
                <BranchCode branch={branch} />
              </span>
            </>
          )}
        </>
      }
      ariaLabel={`Apply ${changes(written)}${branch ? ` to ${branch}` : ""}`}
      description="Before its first write, the worker checks the plan against the checksum below and checks that the destination schema still matches the schema the plan was made with."
      notice={notice}
      error={
        apply.error ? (
          <MutationError
            error={apply.error}
            currentRunId={run.run_id}
            retryHint={resendHint(submitLabel)}
          />
        ) : undefined
      }
      submitLabel={submitLabel}
      submitVariant="warning"
      pendingLabel="Requesting the apply"
      isPending={apply.isPending}
      isDisabled={checksumRefused || !matches}
      disabledReason={
        checksumRefused ? (
          "The Sync service refuses to apply this plan."
        ) : branch ? (
          <>
            Type <BranchCode branch={branch} /> to continue.
          </>
        ) : undefined
      }
      onSubmit={() =>
        apply.mutate(
          {
            params: { path: { run_id: run.run_id }, header: { "Idempotency-Key": key } },
            // The apply must name the branch the plan was made for, or the worker refuses it.
            body: {
              expected_checksum: plan.checksum,
              confirm_writes: true,
              branch: requested,
              reason: reason.trim() || null,
            },
          },
          {
            onSuccess: () => {
              onClose();
              onDone?.();
            },
          }
        )
      }
    >
      <div className="flex flex-col gap-2.5 rounded-xl bg-content-muted px-4 py-2.5 text-sm">
        <dl className="grid grid-cols-1 sm:grid-cols-[auto_minmax(0,1fr)] sm:gap-x-4 sm:gap-y-1.5 [&>dd]:max-sm:mb-2 [&>dd:last-child]:max-sm:mb-0">
          {destination && (
            <>
              <dt className="text-subtle">Into</dt>
              <dd className="min-w-0 [overflow-wrap:anywhere]">
                {destination.label}
                {destination.host && (
                  <>
                    {" "}
                    at{" "}
                    <span className="inline-block max-w-full font-mono text-[13px]">
                      {destination.host}
                    </span>
                  </>
                )}
              </dd>
            </>
          )}
          {plan.lineage && (
            <>
              <dt className="text-subtle">Writes as</dt>
              <dd className="min-w-0 [overflow-wrap:anywhere]">
                {plan.lineage.import_account.name}
              </dd>
            </>
          )}
          {plan.lineage?.owner && (
            <>
              <dt className="text-subtle">Owner</dt>
              <dd className="min-w-0 text-pretty">
                {plan.lineage.owner.name}. The owner setting applies only to values this run
                creates.
              </dd>
            </>
          )}
          <dt className="text-subtle">Verified</dt>
          <dd className="flex min-w-0 items-start gap-1.5">
            {verified ? (
              <>
                <StateIcon state="insync" className="mt-0.5 size-3.5 shrink-0 text-success" />
                <span className="text-pretty">
                  {at ? (
                    <>
                      Yes, <When iso={at} />.
                    </>
                  ) : (
                    "Yes."
                  )}{" "}
                  The saved plan passed its checks.
                </span>
              </>
            ) : (
              <>
                <StateIcon state="idle" className="mt-0.5 size-3.5 shrink-0 text-subtle" />
                <span className="text-pretty">
                  No. Apply runs the same checks before its first write.
                </span>
              </>
            )}
          </dd>
          <dt className="text-subtle">Plan checksum</dt>
          <dd className="flex min-w-0 items-center gap-1.5">
            <code
              className="truncate font-mono text-foreground-muted text-xs"
              title={plan.checksum}
            >
              {`${plan.checksum.slice(0, 12)}…${plan.checksum.slice(-8)}`}
            </code>
            <CopyButton value={plan.checksum} label="Copy plan checksum" />
          </dd>
        </dl>
        <div className="border-t pt-2.5">
          <ul className="flex flex-col gap-1 tabular-nums">
            {create > 0 && <ChangeLine tone="create">{fmt(create)} to create</ChangeLine>}
            {update > 0 && <ChangeLine tone="update">{fmt(update)} to update</ChangeLine>}
            {plan.summary.delete_operations_computed === false ? (
              <ChangeLine tone="delete">
                <span className="text-subtle">
                  Deletes were not listed, because the destination was not read in full.
                </span>
              </ChangeLine>
            ) : (
              notRun > 0 && (
                <ChangeLine tone="delete">
                  <span className="text-subtle">
                    {fmt(notRun)} {notRun === 1 ? "delete" : "deletes"} listed, not applied. Apply
                    does not run deletes.
                  </span>
                </ChangeLine>
              )
            )}
          </ul>
        </div>
        <p className="text-pretty border-t pt-2.5 text-subtle text-xs">
          You can apply this plan once. If the apply stops, the changes written before it stopped
          stay written. To try again, plan again and review the new plan.
        </p>
      </div>
      <Field
        id="apply-confirm"
        label={
          branch ? (
            <>
              Type <BranchCode branch={branch} /> to confirm
            </>
          ) : (
            "Type the destination branch to confirm"
          )
        }
        hint={confirmHint}
      >
        <div className="relative">
          <input
            id="apply-confirm"
            className={`${INPUT} font-mono ${matches ? "border-success pr-9 focus-visible:border-success" : ""}`}
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            disabled={branch === undefined || checksumRefused}
            autoComplete="off"
            spellCheck={false}
            autoCapitalize="none"
            autoCorrect="off"
            enterKeyHint="go"
            data-1p-ignore=""
            data-lpignore="true"
            // biome-ignore lint/a11y/noAutofocus: only with a fine pointer, inside a modal that just opened.
            autoFocus={finePointer()}
            aria-describedby={confirmHint ? "apply-confirm-hint" : undefined}
          />
          {matches && (
            <CheckIcon className="pointer-events-none absolute inset-y-0 right-3 my-auto size-4 text-success" />
          )}
        </div>
        <span className="sr-only" aria-live="polite">
          {matches ? `The text matches ${branch}. ${submitLabel} is available.` : ""}
        </span>
      </Field>
      <ReasonField
        id="apply-reason"
        value={reason}
        onChange={(v) => {
          setReason(v);
          renew();
          apply.reset();
        }}
      />
    </DialogFrame>
  );
}

const STAGE_NOUN: Record<string, string> = {
  plan: "plan",
  verify: "verification",
  apply: "apply",
  sync: "sync",
};

export function CancelDialog({
  run,
  onClose,
  onDone,
}: {
  run: Run;
  onClose: () => void;
  onDone?: () => void;
}) {
  const cancel = useCancelRun();
  const [key, renew] = useIdempotencyKey();
  const [reason, setReason] = useState("");
  const resource = useQuery(runQuery(run.run_id)).data;
  const entry = resource?.orchestration.find((o) => o.terminal_at == null);
  const nothing = resource !== undefined && entry === undefined;
  const noun = entry ? (STAGE_NOUN[entry.purpose] ?? entry.purpose) : undefined;
  const writing = entry?.purpose === "apply" || entry?.purpose === "sync";
  const requestedAt =
    entry?.cancellation_requested_at && !entry.cancellation_acknowledged_at
      ? entry.cancellation_requested_at
      : undefined;
  const submitLabel = requestedAt
    ? "Request cancellation again"
    : noun
      ? `Cancel ${noun}`
      : "Cancel run";

  return (
    <DialogFrame
      isOpen
      onOpenChange={(open) => !open && onClose()}
      title={noun ? `Cancel the ${noun} in progress` : "Cancel this run"}
      ariaLabel={noun ? `Cancel the ${noun} in progress` : "Cancel this run"}
      description={
        nothing ? (
          "This run has no stage in progress, so there is nothing to cancel."
        ) : (
          <>
            The Sync service asks Prefect to stop the {noun ?? "stage in progress"}.
            {noun && !writing && ` The ${noun} writes nothing to the destination.`}
            {requestedAt && (
              <>
                {" "}
                Cancellation was requested <When iso={requestedAt} />; the worker has not confirmed
                it yet.
              </>
            )}
          </>
        )
      }
      notice={
        writing ? (
          <Notice tone="warning" role="note">
            <p>
              This {noun} may already be writing to{" "}
              {run.destination_branch ? (
                <BranchCode branch={run.destination_branch} />
              ) : (
                "the destination branch"
              )}
              . If you cancel it, the run can end interrupted with an unknown outcome, because the
              worker may already have changed the destination branch. Inspect the branch before you
              plan again.
            </p>
          </Notice>
        ) : undefined
      }
      error={
        cancel.error ? (
          <MutationError
            error={cancel.error}
            currentRunId={run.run_id}
            retryHint={resendHint(submitLabel)}
          />
        ) : undefined
      }
      submitLabel={submitLabel}
      submitVariant="danger"
      pendingLabel="Requesting cancellation"
      isPending={cancel.isPending}
      isDisabled={nothing}
      disabledReason="Nothing is running."
      dismissLabel={nothing ? "Close" : "Keep running"}
      autoFocusDismiss
      submitOnEnter={false}
      onSubmit={() =>
        cancel.mutate(
          {
            params: { path: { run_id: run.run_id }, header: { "Idempotency-Key": key } },
            body: { reason: reason.trim() || null },
          },
          {
            onSuccess: () => {
              onClose();
              onDone?.();
            },
          }
        )
      }
    >
      <ReasonField
        id="cancel-reason"
        value={reason}
        onChange={(v) => {
          setReason(v);
          renew();
          cancel.reset();
        }}
      />
    </DialogFrame>
  );
}

export function ApplyButton({
  label,
  onPress,
  isPending,
  variant = "primary",
}: {
  label: string;
  onPress: () => void;
  isPending?: boolean;
  variant?: "primary" | "outline";
}) {
  return (
    <Button variant={variant} size="sm" isPending={isPending} onPress={onPress}>
      <PlayIcon />
      {label}
    </Button>
  );
}
