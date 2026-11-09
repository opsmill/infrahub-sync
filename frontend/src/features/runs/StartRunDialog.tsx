import { Checkbox } from "@infrahub/ui";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { type ReactNode, useEffect, useRef, useState } from "react";
import { cn } from "tailwind-variants";

import { useCreateRun, useIdempotencyKey } from "#/api/actions";
import { configQuery, configVersionQuery, runsQuery, statusQuery } from "#/api/queries";
import type { Run } from "#/api/types";
import { StateIcon } from "#/components/icons";
import { Notice } from "#/components/page";
import { BranchCode, When } from "#/components/run-status";
import { executableCount, fmt, NO_WORKER_SENTENCE, queueSentence } from "#/domain/run-state";
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

export interface StartRunTarget {
  configId: string;
  name: string;
  /** A registered version. Callers never invent one. */
  version: number;
  /** Prefill for the branch field, from branchPrefill() or the run itself. */
  branch?: string;
  /** The run the prefill came from; the hint names it. */
  branchFromRunId?: string;
}

const OPTIONS = [
  {
    value: "plan",
    title: "Plan for review",
    hint: "Writes nothing. You review the plan, then apply it.",
  },
  {
    value: "sync",
    title: "Sync without review",
    hint: "Creates and updates nodes in the destination without a review, and does not delete nodes. You can view the saved plan afterwards.",
  },
] as const;

/** The first fact about this configuration's runs or the worker that a person should read before starting a run. */
function runNotice(
  runs: { items: Run[] } | undefined,
  worker: { state: string; queue_depth?: number | null } | undefined,
  onLeave: () => void
): ReactNode {
  if (worker?.state === "no-live-worker") {
    return (
      <Notice tone="danger" role="note">
        <p>{NO_WORKER_SENTENCE}</p>
      </Notice>
    );
  }
  const runLink = (run: Run) => (
    <Link to="/runs/$runId" params={{ runId: run.run_id }} className={LINK} onClick={onLeave}>
      {run.run_id}
    </Link>
  );
  if (runs) {
    const running = runs.items.find((r) => r.phase === "accepted");
    if (running) {
      return (
        <Notice
          role="note"
          icon={<StateIcon state="running" className="mt-0.5 size-4 shrink-0 text-accent" />}
        >
          <p>Run {runLink(running)} of this configuration is still in progress.</p>
        </Notice>
      );
    }
    // Only the newest run of a configuration asks for action; an older plan does not.
    const newest = runs.items[0];
    if (newest?.phase === "planned" && newest.outcome === "planned") {
      const written = executableCount(newest);
      return (
        <Notice
          role="note"
          icon={<StateIcon state="review" className="mt-0.5 size-4 shrink-0 text-warning" />}
        >
          <p>
            A plan of this configuration waits for review: {runLink(newest)}
            {newest.finished_at && (
              <>
                , planned <When iso={newest.finished_at} />
              </>
            )}
            {written === 0 ? (
              ". It lists only deletes, which apply does not run."
            ) : (
              <>
                {written !== undefined &&
                  `, with ${fmt(written)} ${written === 1 ? "change" : "changes"} to write`}
                .
              </>
            )}
          </p>
        </Notice>
      );
    }
  }
  if (worker?.state === "unavailable") {
    return (
      <Notice role="note">
        <p>The Sync service cannot report whether a worker is connected.</p>
      </Notice>
    );
  }
  if (worker?.state === "busy" && (worker.queue_depth ?? 0) > 0) {
    return (
      <Notice role="note">
        <p>{queueSentence(worker.queue_depth ?? 0)}</p>
      </Notice>
    );
  }
  return undefined;
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

  const n = target.version;
  const version = useQuery(configVersionQuery(target.configId, n));
  const latest = useQuery(configQuery(target.configId)).data?.latest_version;
  const status = useQuery(statusQuery());
  const runs = useQuery(runsQuery({ config_id: target.configId }));

  const destination = version.data
    ? describeSystem(version.data.declared_content.configuration.destination)
    : undefined;
  const declared = destination?.branch;
  const typed = branch.trim() || "main";
  const effective = declared ?? typed;
  const isPending = create.isPending;
  const submitLabel = operation === "plan" ? "Create plan" : "Sync now";

  /** Any change to the request makes it a new request, and the answer to the last one no longer applies. */
  const edited = () => {
    renewKey();
    create.reset();
  };

  // When the version turns out to set the branch, the field the person was in is replaced by text.
  const branchFocused = useRef(false);
  const declaredRef = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    if (declared && branchFocused.current) {
      branchFocused.current = false;
      declaredRef.current?.focus();
    }
  }, [declared]);

  const submit = () =>
    create.mutate(
      {
        params: { header: { "Idempotency-Key": key } },
        body: {
          operation,
          config_id: target.configId,
          registry_version: n,
          branch: declared ? null : branch.trim() || null,
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

  const branchHint = version.isPending ? (
    <>Loading the branch that version {n} sets.</>
  ) : version.isError ? (
    <>
      The branch that version {n} sets could not be read. If it sets one, the run writes to that
      branch, whatever you type here.
    </>
  ) : (
    <>
      Leave empty to write to main.
      {target.branchFromRunId && branch === target.branch && (
        <>
          {" "}
          Filled in from run <code className="font-mono">{target.branchFromRunId}</code>.
        </>
      )}
    </>
  );

  return (
    <DialogFrame
      isOpen
      onOpenChange={(open) => !open && onClose()}
      title="Start a run"
      ariaLabel={`Start a run of ${target.name}`}
      description={
        <>
          <p className="font-medium text-foreground [overflow-wrap:anywhere]">{target.name}</p>
          {latest == null ? (
            <p>Version {n}.</p>
          ) : n === latest ? (
            <p>Version {n}, the latest.</p>
          ) : (
            <p className="text-warning">
              Version {n}. Version {latest} is the latest.
            </p>
          )}
        </>
      }
      notice={runNotice(runs.data, status.data?.worker, onClose)}
      error={
        create.error ? (
          <MutationError
            error={create.error}
            retryHint={resendHint(submitLabel, {
              sameRun: true,
              check: (
                <Link
                  to="/configs/$configId"
                  params={{ configId: target.configId }}
                  className={LINK}
                  onClick={onClose}
                >
                  view the runs of this configuration
                </Link>
              ),
            })}
          />
        ) : undefined
      }
      submitLabel={submitLabel}
      submitVariant={operation === "plan" ? "primary" : "warning"}
      pendingLabel={operation === "plan" ? "Creating the plan" : "Starting the sync"}
      isPending={isPending}
      isDisabled={operation === "sync" && !confirmed}
      disabledReason="Select the confirmation to continue."
      onSubmit={submit}
    >
      {declared ? (
        <div className="flex flex-col gap-1.5">
          <p className="font-medium text-foreground text-sm">Destination branch</p>
          <p
            ref={declaredRef}
            tabIndex={-1}
            className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm outline-hidden"
          >
            <BranchCode branch={declared} />
            <span className="text-subtle text-xs">
              Set by version {n}. Every run of this version writes to it.
            </span>
          </p>
        </div>
      ) : (
        <Field id="run-branch" label="Destination branch" hint={branchHint}>
          <input
            id="run-branch"
            className={cn(INPUT, "font-mono")}
            value={branch}
            readOnly={version.isPending}
            onChange={(e) => {
              setBranch(e.target.value);
              setConfirmed(false);
              edited();
            }}
            onFocus={() => {
              branchFocused.current = true;
            }}
            onBlur={() => {
              branchFocused.current = false;
            }}
            autoComplete="off"
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            aria-describedby="run-branch-hint"
          />
        </Field>
      )}
      <fieldset className="m-0 flex min-w-0 flex-col gap-2 border-0 p-0">
        <legend className="mb-2 font-medium text-foreground text-sm">What the run does</legend>
        {OPTIONS.map((option) => {
          const card = (
            <label
              key={option.value}
              className="group flex cursor-pointer items-start gap-3 rounded-xl border px-4 py-2.5 transition-[border-color,background-color,box-shadow] duration-150 ease-out hover:bg-content-muted has-checked:border-ring has-checked:bg-content has-focus-visible:border-ring has-checked:hover:bg-content has-focus-visible:ring-2 has-focus-visible:ring-ring-halo"
            >
              <input
                type="radio"
                name="operation"
                value={option.value}
                checked={operation === option.value}
                onChange={() => {
                  setOperation(option.value);
                  setConfirmed(false);
                  edited();
                }}
                // biome-ignore lint/a11y/noAutofocus: only with a fine pointer, inside a modal that just opened.
                autoFocus={option.value === "plan" && finePointer()}
                className="sr-only"
              />
              <span
                aria-hidden="true"
                className="mt-0.5 grid size-4 shrink-0 place-items-center rounded-full border border-border-strong bg-input transition-colors duration-150 group-has-checked:border-cyan-800 group-has-checked:bg-linear-to-b group-has-checked:from-cyan-800 group-has-checked:to-cyan-600"
              >
                <span className="size-1.5 rounded-full bg-white opacity-0 group-has-checked:opacity-100" />
              </span>
              <span className="flex min-w-0 flex-col gap-0.5">
                <span className="font-medium text-foreground text-sm">{option.title}</span>
                <span className="text-pretty text-[13px] text-foreground-muted leading-snug">
                  {option.hint}
                </span>
                {option.value === "sync" && destination && (
                  <span className="text-subtle text-xs">
                    Writes to {destination.label}
                    {destination.host && (
                      <>
                        {" "}
                        at{" "}
                        <span className="inline-block max-w-full font-mono [overflow-wrap:anywhere]">
                          {destination.host}
                        </span>
                      </>
                    )}
                    .
                  </span>
                )}
              </span>
            </label>
          );
          if (option.value === "plan") return card;
          return (
            <div key={option.value} className="flex flex-col">
              {card}
              <div
                className={cn(
                  "grid transition-[grid-template-rows] duration-200 ease-out motion-reduce:transition-none",
                  operation === "sync" ? "grid-rows-[1fr]" : "grid-rows-[0fr]"
                )}
              >
                <div className="min-h-0 overflow-hidden" inert={operation !== "sync"}>
                  <div className="pt-2">
                    <div className="rounded-xl border border-warning-border bg-warning-surface px-4 py-2.5">
                      <Checkbox
                        isSelected={confirmed}
                        isDisabled={isPending || version.isPending}
                        onChange={(v) => {
                          setConfirmed(v);
                          edited();
                        }}
                        className="items-start gap-2.5 text-foreground"
                      >
                        <span className="min-w-0 text-pretty">
                          {version.isPending ? (
                            "I confirm this run writes to the destination branch without a review."
                          ) : (
                            <>
                              I confirm this run writes to{" "}
                              <BranchCode branch={version.isError ? typed : effective} /> without a
                              review
                              {version.isError ? `, unless version ${n} sets another branch` : ""}.
                            </>
                          )}
                        </span>
                      </Checkbox>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          );
        })}
      </fieldset>
      <ReasonField
        id="run-reason"
        value={reason}
        onChange={(v) => {
          setReason(v);
          edited();
        }}
      />
    </DialogFrame>
  );
}
