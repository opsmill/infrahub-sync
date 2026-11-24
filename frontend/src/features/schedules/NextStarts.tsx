import { describeScheduleError } from "#/api/queries";
import type { ScheduleNextStarts } from "#/api/types";

/** Said when the preview has no answer that names a refused value (data-model "Cadence preview"). */
const NO_PREVIEW =
  "The preview is not available. The Sync API checks the expression when you save.";

/** The parts of a `nextStartsQuery` result that the timelines read. */
export interface NextStartsRead {
  data?: ScheduleNextStarts;
  error: unknown;
  isPending: boolean;
  fetchStatus: "fetching" | "paused" | "idle";
}

/** The refusal's words when the Sync API refused the values; the fixed sentence otherwise. */
export function failureText(error: unknown): string {
  const d = describeScheduleError(error);
  return d.kind === "envelope" && !d.retryable ? (d.detail ?? d.title) : NO_PREVIEW;
}
