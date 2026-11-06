import { type RunState, STEP_NAMES, type StepState } from "#/domain/run-state";
import { formatAbsolute, formatRelative, useNow } from "#/lib/time";

import { CODE_CHIP } from "./classes";
import { STATE_TEXT, StateIcon } from "./icons";

/** Sign and colours for counts of changes. Deletes are grey: apply lists them but does not run them. */
export const CHANGE_TONE: Record<
  "create" | "update" | "delete",
  { sign: string; text: string; chip: string }
> = {
  create: { sign: "+", text: "text-diff-added", chip: "bg-diff-added-surface text-diff-added" },
  update: {
    sign: "~",
    text: "text-diff-updated",
    chip: "bg-diff-updated-surface text-diff-updated",
  },
  delete: { sign: "−", text: "text-subtle", chip: "bg-content-strong text-subtle" },
};

export const NODE: Record<StepState, string> = {
  done: "size-2 bg-success-strong",
  failed: "size-3 bg-danger-strong ring-4 ring-danger-surface",
  waiting: "size-3 bg-warning-strong ring-4 ring-warning-surface",
  active:
    "size-3 bg-accent-strong ring-4 ring-accent-surface animate-pulse motion-reduce:animate-none",
  idle: "size-2 border-[1.5px] border-subtle-muted bg-content",
  skipped: "h-0.5 w-2 bg-subtle-muted",
};

/** A done step drawn grey, where colour is kept for the step that failed, waits or runs. */
export const QUIET_DONE = "size-2 bg-subtle-muted/60";

export const STEP_TONE: Record<StepState, string> = {
  done: "text-subtle",
  failed: "text-danger",
  waiting: "text-warning",
  active: "text-accent",
  idle: "text-subtle",
  skipped: "text-subtle",
};

export const STEP_WORD: Record<StepState, string> = {
  done: "done",
  failed: "failed",
  active: "running",
  waiting: "waiting for review",
  idle: "not started",
  skipped: "skipped",
};

const isQuiet = (s: StepState) => s === "done" || s === "idle" || s === "skipped";

/** Plan, Verify and Apply as dots on a line; only the step that needs attention is named. */
export function Stepper({
  steps,
  quiet = false,
  label = true,
  names = STEP_NAMES,
}: {
  steps: StepState[];
  quiet?: boolean;
  /** False removes the row that names the step needing attention. */
  label?: boolean;
  names?: readonly string[];
}) {
  const focus = steps.findIndex((s) => !isQuiet(s));
  return (
    <div className="w-full min-w-0">
      <ol className="flex h-4 items-center" aria-label="Steps of the latest run">
        {steps.map((s, i) => {
          const lineDone = s === "done" && steps[i + 1] !== undefined && steps[i + 1] !== "idle";
          return (
            <li key={names[i]} className="flex flex-1 items-center last:flex-none">
              {/* The fixed box keeps the dot centres in place whatever the size of each dot. */}
              <span className="flex size-3 shrink-0 items-center justify-center">
                <span
                  className={`block shrink-0 rounded-full ${quiet && s === "done" ? QUIET_DONE : NODE[s]}`}
                  title={`${names[i]}: ${STEP_WORD[s]}`}
                />
              </span>
              <span className="sr-only">
                {names[i]}: {STEP_WORD[s]}.
              </span>
              {i < steps.length - 1 && (
                <span
                  aria-hidden="true"
                  className={`mx-1 block h-0.5 flex-1 rounded-full ${lineDone ? (quiet ? "bg-subtle-muted/30" : "bg-success-strong/45") : "bg-border"}`}
                />
              )}
            </li>
          );
        })}
      </ol>
      {label && (
        <div className="relative mt-0.5 h-4" aria-hidden="true">
          {focus >= 0 && (
            <span
              className={`absolute top-0 whitespace-nowrap font-medium text-xs ${STEP_TONE[steps[focus]]}`}
              style={
                focus === 0
                  ? { left: 0 }
                  : focus === steps.length - 1
                    ? { right: 0 }
                    : { left: "50%", transform: "translateX(-50%)" }
              }
            >
              {names[focus]}
            </span>
          )}
        </div>
      )}
    </div>
  );
}

/** A relative time that stays true while the page is open, with the exact time on hover. */
export function When({
  iso,
  prefix = "",
  className = "",
}: {
  iso: string;
  prefix?: string;
  className?: string;
}) {
  const now = useNow();
  return (
    <time
      dateTime={iso}
      title={formatAbsolute(iso)}
      className={`whitespace-nowrap tabular-nums ${className}`}
    >
      {prefix}
      {formatRelative(iso, now)}
    </time>
  );
}

/** A state word with its coloured icon, for tables and headers. Quiet labels recede. */
export function StateLabel({
  state,
  label,
  quiet = false,
  className = "",
}: {
  state: RunState;
  label: string;
  quiet?: boolean;
  className?: string;
}) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 whitespace-nowrap ${quiet ? "font-normal text-foreground-muted" : "font-medium text-foreground"} ${className}`}
    >
      <StateIcon state={state} className={`size-3.5 shrink-0 ${STATE_TEXT[state]}`} />
      {label}
    </span>
  );
}

export function BranchCode({
  branch,
  className = "",
  title,
}: {
  branch: string;
  className?: string;
  title?: string;
}) {
  return (
    <code className={`${CODE_CHIP} ${className}`} title={title}>
      {branch}
    </code>
  );
}
