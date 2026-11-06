import { type RunState, STEP_NAMES, type StepState } from "#/domain/run-state";
import { formatAbsolute, formatRelative, useNow } from "#/lib/time";

import { STATE_TEXT, StateIcon } from "./icons";

export const NODE: Record<StepState, string> = {
  done: "size-2 bg-success-strong",
  failed: "size-3 bg-danger-strong ring-4 ring-danger-surface",
  waiting: "size-3 bg-warning-strong ring-4 ring-warning-surface",
  active:
    "size-3 bg-accent-strong ring-4 ring-accent-surface animate-pulse motion-reduce:animate-none",
  idle: "size-2 border-[1.5px] border-border-strong bg-content",
  skipped: "h-0.5 w-2 bg-border-strong",
};

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
  waiting: "waiting for a person",
  idle: "not started",
  skipped: "skipped",
};

const isQuiet = (s: StepState) => s === "done" || s === "idle" || s === "skipped";

/** Plan, Verify and Apply as dots on a line; only the step that needs attention is named. */
export function Stepper({ steps, quiet = false }: { steps: StepState[]; quiet?: boolean }) {
  const focus = steps.findIndex((s) => !isQuiet(s));
  return (
    <div className="w-full min-w-0">
      <ol className="flex h-4 items-center" aria-label="Steps of the latest run">
        {steps.map((s, i) => {
          const lineDone = s === "done" && steps[i + 1] !== undefined && steps[i + 1] !== "idle";
          return (
            <li key={STEP_NAMES[i]} className="flex flex-1 items-center last:flex-none">
              <span
                className={`block shrink-0 rounded-full ${quiet && s === "done" ? "size-2 bg-subtle-muted/60" : NODE[s]}`}
                title={`${STEP_NAMES[i]}: ${STEP_WORD[s]}`}
              />
              <span className="sr-only">
                {STEP_NAMES[i]}: {STEP_WORD[s]}.
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
            {STEP_NAMES[focus]}
          </span>
        )}
      </div>
    </div>
  );
}

/** A relative time that stays true while the page is open, with the exact time on hover. */
export function When({ iso, prefix = "" }: { iso: string; prefix?: string }) {
  const now = useNow();
  return (
    <time dateTime={iso} title={formatAbsolute(iso)} className="whitespace-nowrap tabular-nums">
      {prefix}
      {formatRelative(iso, now)}
    </time>
  );
}

/** A coloured state word with its icon, for tables and headers. */
export function StateLabel({
  state,
  label,
  className = "",
}: {
  state: RunState;
  label: string;
  className?: string;
}) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 whitespace-nowrap font-medium ${STATE_TEXT[state]} ${className}`}
    >
      <StateIcon state={state} className="size-3.5 shrink-0" />
      {label}
    </span>
  );
}

export function BranchCode({ branch }: { branch: string }) {
  return (
    <code className="whitespace-nowrap rounded bg-content-strong px-1 py-px font-mono text-[0.85em] text-foreground-muted">
      {branch}
    </code>
  );
}
