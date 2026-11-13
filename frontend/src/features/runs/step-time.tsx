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

/** "01:13:36", for times inside one step. */
export const clockSeconds = (iso: string) =>
  new Date(iso).toLocaleTimeString("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });

/** ["01:13:36", "042"]: the time of a log line, whose neighbours often share its second. */
export const clockMillis = (iso: string): [string, string] => [
  clockSeconds(iso),
  String(new Date(iso).getMilliseconds()).padStart(3, "0"),
];

/** How long a review lasted, or has lasted so far, counted every second while it still waits. */
export function WaitTime({ wait }: { wait: Wait }) {
  const now = useNow(1000);
  return <span className="tabular-nums">{formatSpan(wait.from, wait.to ?? now)}</span>;
}

/** The dot of a step: done steps stay grey, so colour marks only the step that failed, waits or runs. */
export const stepNode = (s: RunStep) => (s.state === "done" ? QUIET_DONE : NODE[s.state]);
