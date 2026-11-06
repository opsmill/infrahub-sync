import { useNavigate } from "@tanstack/react-router";
import { useState } from "react";

import { useCreateRun, useIdempotencyKey } from "#/api/actions";

import { DialogFrame, Field, INPUT, MutationError } from "./form";

export interface StartRunTarget {
  configId: string;
  name: string;
  version: number;
  /** The branch the previous run wrote to, or the package's declared branch. */
  branch?: string;
}

/** Start a plan for review, or a sync that plans and applies without review. */
export function StartRunDialog({
  target,
  onClose,
}: {
  target: StartRunTarget;
  onClose: () => void;
}) {
  const navigate = useNavigate();
  const create = useCreateRun();
  const [key, renewKey] = useIdempotencyKey();
  const [operation, setOperation] = useState<"plan" | "sync">("plan");
  const [branch, setBranch] = useState(target.branch ?? "");
  const [reason, setReason] = useState("");
  const [confirmed, setConfirmed] = useState(false);

  const submit = () =>
    create.mutate(
      {
        params: { header: { "Idempotency-Key": key } },
        body: {
          operation,
          config_id: target.configId,
          registry_version: target.version,
          branch: branch.trim() || null,
          confirm_writes: operation === "sync" && confirmed,
          reason: reason.trim() || null,
        },
      },
      {
        onSuccess: (data) => {
          onClose();
          navigate({ to: "/runs/$runId", params: { runId: data.run.run_id } });
        },
      }
    );

  return (
    <DialogFrame
      isOpen
      onOpenChange={(open) => !open && onClose()}
      title={`Start a run of ${target.name}`}
      description={
        <>
          Version {target.version}. The worker reads the source and the destination, then records
          what would change.
        </>
      }
      submitLabel={operation === "plan" ? "Create plan" : "Sync now"}
      submitVariant={operation === "plan" ? "primary" : "warning"}
      isPending={create.isPending}
      isDisabled={operation === "sync" && !confirmed}
      onSubmit={submit}
    >
      <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
        <legend className="mb-1.5 font-medium text-sm">What the run does</legend>
        {(
          [
            ["plan", "Plan for review", "Writes nothing. You review the changes, then apply them."],
            [
              "sync",
              "Sync without review",
              "Plans and applies in one run. Writes to the destination.",
            ],
          ] as const
        ).map(([value, label, hint]) => (
          <label
            key={value}
            className={`flex cursor-pointer items-start gap-3 rounded-xl border px-3 py-2.5 transition-colors duration-150 ease-out ${
              operation === value ? "border-ring bg-content" : "hover:bg-content-muted"
            }`}
          >
            <input
              type="radio"
              name="operation"
              value={value}
              checked={operation === value}
              onChange={() => {
                setOperation(value);
                renewKey();
              }}
              className="mt-1 accent-cyan-800"
            />
            <span className="flex flex-col">
              <span className="font-medium">{label}</span>
              <span className="text-foreground-muted text-xs">{hint}</span>
            </span>
          </label>
        ))}
      </fieldset>
      <Field
        id="run-branch"
        label="Destination branch"
        hint="Leave empty to use the branch the package declares."
      >
        <input
          id="run-branch"
          className={`${INPUT} font-mono`}
          value={branch}
          onChange={(e) => {
            setBranch(e.target.value);
            renewKey();
          }}
          placeholder="main"
          autoComplete="off"
          spellCheck={false}
          aria-describedby="run-branch-hint"
        />
      </Field>
      <Field id="run-reason" label="Reason" hint="Optional. Recorded in the audit history.">
        <input
          id="run-reason"
          className={INPUT}
          value={reason}
          onChange={(e) => {
            setReason(e.target.value);
            renewKey();
          }}
          placeholder="Review the weekly NetBox changes"
          aria-describedby="run-reason-hint"
        />
      </Field>
      {operation === "sync" && (
        <label className="flex items-start gap-2.5 rounded-xl bg-warning-surface px-3 py-2.5 text-sm">
          <input
            type="checkbox"
            checked={confirmed}
            onChange={(e) => {
              setConfirmed(e.target.checked);
              renewKey();
            }}
            className="mt-0.5 accent-amber-700"
          />
          <span>
            I confirm this run writes to{" "}
            <span className="font-mono">{branch.trim() || "the declared branch"}</span> without a
            review.
          </span>
        </label>
      )}
      <MutationError error={create.error} />
    </DialogFrame>
  );
}
