import { NODE, QUIET_DONE } from "#/components/run-status";
import type { RunStep, Wait } from "#/domain/run-steps";
import { formatSpan, useNow } from "#/lib/time";

/** "6 Nov, 01:13". */
export const clock = (iso: string) =>
  new Date(iso).toLocaleString("en-GB", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });

/** How long a review lasted, or has lasted so far, counted every second while it still waits. */
export function WaitTime({ wait }: { wait: Wait }) {
  const now = useNow(1000);
  return <span className="tabular-nums">{formatSpan(wait.from, wait.to ?? now)}</span>;
}

/** The dot of a step: done steps stay grey, so colour marks only the step that failed, waits or runs. */
export const stepNode = (s: RunStep) => (s.state === "done" ? QUIET_DONE : NODE[s.state]);
