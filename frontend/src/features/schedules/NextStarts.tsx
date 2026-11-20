import { Spinner } from "@infrahub/ui";
import { cn } from "tailwind-variants";

import { describeScheduleError } from "#/api/queries";
import type { ScheduleNextStarts } from "#/api/types";
import { startsIn, startTimeText, viewerTimeZone } from "#/domain/start-times";
import { formatAbsolute, useNow } from "#/lib/time";

/** Said when the preview has no answer that names a refused value (data-model "Cadence preview"). */
const NO_PREVIEW =
  "The preview is not available. The Sync API checks the expression when you save.";

/**
 * One start time: in the schedule's time zone with its offset, in the viewer's local time when that
 * reads differently, and, with `relative`, how far away it is.
 */
export function StartTime({
  iso,
  timezone,
  relative = false,
  className,
}: {
  iso: string;
  timezone: string;
  relative?: boolean;
  className?: string;
}) {
  const now = useNow();
  const text = startTimeText(iso, timezone, viewerTimeZone());
  return (
    <span className={cn("flex min-w-0 flex-col", className)}>
      <time dateTime={iso} title={formatAbsolute(iso)} className="tabular-nums">
        {text.zoned}
      </time>
      {text.local && (
        <span className="text-subtle text-xs tabular-nums">Your time: {text.local}</span>
      )}
      {relative && <span className="text-subtle text-xs">{startsIn(iso, now)}</span>}
    </span>
  );
}

/** The parts of a `nextStartsQuery` result this list reads. */
export interface NextStartsRead {
  data?: ScheduleNextStarts;
  error: unknown;
  isPending: boolean;
  fetchStatus: "fetching" | "paused" | "idle";
}

/** The refusal's words when the Sync API refused the values; the fixed sentence otherwise. */
function failureText(error: unknown): string {
  const d = describeScheduleError(error);
  return d.kind === "envelope" && !d.retryable ? (d.detail ?? d.title) : NO_PREVIEW;
}

/**
 * The next start times of a cron expression, as `POST /schedules/next-starts` computes them. The
 * first one also says how far away it is. Fewer starts than asked for means the expression has no
 * further start in the five years the Sync API searches.
 */
export function NextStarts({
  read,
  timezone,
  count,
  label = "Next starts",
}: {
  read: NextStartsRead;
  /** The time zone the starts are shown in: the schedule's. */
  timezone: string;
  /** The number of starts asked for. */
  count: number;
  label?: string;
}) {
  if (read.isPending && read.fetchStatus === "idle") return null;
  if (read.isPending) {
    return (
      <output className="flex items-center gap-2 text-foreground-muted text-sm">
        <Spinner aria-hidden="true" className="motion-reduce:animate-none" />
        Computing the next start times…
      </output>
    );
  }
  if (!read.data) {
    return (
      <p role="alert" className="text-pretty text-foreground-muted text-sm">
        {failureText(read.error)}
      </p>
    );
  }
  const { starts } = read.data;
  return (
    <div className="flex flex-col gap-2">
      {starts.length > 0 && (
        <ol aria-label={label} className="flex flex-col gap-2 text-[13px]">
          {starts.map((iso, i) => (
            <li key={iso}>
              <StartTime iso={iso} timezone={timezone} relative={i === 0} />
            </li>
          ))}
        </ol>
      )}
      {starts.length < count && (
        <p className="text-foreground-muted text-sm">No further start in the next 5 years.</p>
      )}
    </div>
  );
}
