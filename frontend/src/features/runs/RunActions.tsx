import { Button } from "@infrahub/ui";
import { useState } from "react";

import { useApplyRun, useCancelRun, useIdempotencyKey, useVerifyRun } from "#/api/actions";
import type { Plan, Run } from "#/api/types";
import { PlayIcon } from "#/components/icons";
import { countsSentence, fmt, runCounts } from "#/domain/run-state";

import { DialogFrame, Field, INPUT, MutationError } from "./form";

function ReasonField({
  id,
  value,
  onChange,
  placeholder,
}: {
  id: string;
  value: string;
  onChange: (v: string) => void;
  placeholder: string;
}) {
  return (
    <Field id={id} label="Reason" hint="Optional. Recorded in the audit history.">
      <input
        id={id}
        className={INPUT}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        aria-describedby={`${id}-hint`}
      />
    </Field>
  );
}

export function VerifyDialog({ run, onClose }: { run: Run; onClose: () => void }) {
  const verify = useVerifyRun();
  const [key, renew] = useIdempotencyKey();
  const [reason, setReason] = useState("");
  return (
    <DialogFrame
      isOpen
      onOpenChange={(open) => !open && onClose()}
      title="Verify the plan"
      description="The worker reads the destination again and checks that the saved plan still matches it. Nothing is written."
      submitLabel="Verify"
      isPending={verify.isPending}
      onSubmit={() =>
        verify.mutate(
          {
            params: { path: { run_id: run.run_id }, header: { "Idempotency-Key": key } },
            body: { reason: reason.trim() || null },
          },
          { onSuccess: onClose }
        )
      }
    >
      <ReasonField
        id="verify-reason"
        value={reason}
        onChange={(v) => {
          setReason(v);
          renew();
        }}
        placeholder="Check before the change window"
      />
      <MutationError error={verify.error} />
    </DialogFrame>
  );
}

export function ApplyDialog({ run, plan, onClose }: { run: Run; plan: Plan; onClose: () => void }) {
  const apply = useApplyRun();
  const [key, renew] = useIdempotencyKey();
  const [reason, setReason] = useState("");
  const [typed, setTyped] = useState("");
  const counts = runCounts(run);
  const executable = (counts?.total ?? 0) - plan.summary.deletes_not_executed;
  const branch = run.destination_branch ?? plan.destination_branch ?? "the declared branch";
  const confirmWord = branch;
  return (
    <DialogFrame
      isOpen
      onOpenChange={(open) => !open && onClose()}
      title={`Apply ${fmt(executable)} ${executable === 1 ? "change" : "changes"} to ${branch}`}
      description={
        <>
          The worker checks the plan against the destination again before its first write, and
          refuses if anything changed since the review.
        </>
      }
      submitLabel="Apply changes"
      submitVariant="warning"
      isPending={apply.isPending}
      isDisabled={typed.trim() !== confirmWord}
      onSubmit={() =>
        apply.mutate(
          {
            params: { path: { run_id: run.run_id }, header: { "Idempotency-Key": key } },
            // The apply must name the branch the plan was made for, or the worker refuses it.
            body: {
              expected_checksum: plan.checksum,
              confirm_writes: true,
              branch: run.destination_branch ?? plan.destination_branch ?? null,
              reason: reason.trim() || null,
            },
          },
          { onSuccess: onClose }
        )
      }
    >
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 rounded-xl bg-content-muted px-3 py-2.5 text-sm">
        <dt className="text-subtle">Changes</dt>
        <dd>{counts ? countsSentence(counts, "plan") : "—"}</dd>
        {plan.summary.deletes_not_executed > 0 && (
          <>
            <dt className="text-subtle">Not applied</dt>
            <dd>
              {fmt(plan.summary.deletes_not_executed)}{" "}
              {plan.summary.deletes_not_executed === 1 ? "delete" : "deletes"}: the service never
              runs deletes
            </dd>
          </>
        )}
        <dt className="text-subtle">Checksum</dt>
        <dd className="font-mono text-xs [overflow-wrap:anywhere]">{plan.checksum}</dd>
      </dl>
      <ReasonField
        id="apply-reason"
        value={reason}
        onChange={(v) => {
          setReason(v);
          renew();
        }}
        placeholder="Approved in the weekly network review"
      />
      <Field id="apply-confirm" label={`Type ${confirmWord} to confirm`}>
        <input
          id="apply-confirm"
          className={`${INPUT} font-mono`}
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          autoComplete="off"
          spellCheck={false}
        />
      </Field>
      <p className="text-pretty text-subtle text-xs">
        A run admits one write. If the apply fails, create and review a new plan to try again.
      </p>
      <MutationError error={apply.error} />
    </DialogFrame>
  );
}

export function CancelDialog({
  run,
  writing,
  onClose,
}: {
  run: Run;
  writing: boolean;
  onClose: () => void;
}) {
  const cancel = useCancelRun();
  const [key, renew] = useIdempotencyKey();
  const [reason, setReason] = useState("");
  return (
    <DialogFrame
      isOpen
      onOpenChange={(open) => !open && onClose()}
      title="Cancel this run"
      description={
        writing ? (
          <>
            This run may already be writing. Cancelling a write can leave its outcome unknown: the
            run then ends <span className="font-medium">interrupted</span> and the destination needs
            inspecting.
          </>
        ) : (
          "The service asks the worker to stop the current execution."
        )
      }
      submitLabel="Cancel run"
      submitVariant="danger"
      isPending={cancel.isPending}
      onSubmit={() =>
        cancel.mutate(
          {
            params: { path: { run_id: run.run_id }, header: { "Idempotency-Key": key } },
            body: { reason: reason.trim() || null },
          },
          { onSuccess: onClose }
        )
      }
    >
      <ReasonField
        id="cancel-reason"
        value={reason}
        onChange={(v) => {
          setReason(v);
          renew();
        }}
        placeholder="Started on the wrong branch"
      />
      <MutationError error={cancel.error} />
    </DialogFrame>
  );
}

export function ApplyButton({ label, onPress }: { label: string; onPress: () => void }) {
  return (
    <Button variant="primary" size="sm" onPress={onPress}>
      <PlayIcon />
      {label}
    </Button>
  );
}
