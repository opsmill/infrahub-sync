import { Card, Spinner } from "@infrahub/ui";
import type { ReactNode } from "react";
import { cn } from "tailwind-variants";

import { cronFields } from "#/domain/cadence";
import { startGroups } from "#/domain/schedule-page";
import { startsIn } from "#/domain/start-times";
import { useNow } from "#/lib/time";

import type { CadencePreview } from "./CadenceField";
import { failureText } from "./NextStarts";
import { NextStartGroups, NowMarker, TimelineMessage } from "./ScheduleTimeline";

const NOT_FIVE_FIELDS =
  "A cron expression has five fields: minute, hour, day of month, month, day of week.";

/** What the preview can say about the values entered, before the starts themselves. */
type PreviewState =
  | { kind: "incomplete" | "not-five-fields" | "waiting" }
  | { kind: "refused"; text: string }
  | { kind: "failed"; text: string }
  | { kind: "starts"; starts: string[] };

function previewState(cron: string | null, preview: CadencePreview): PreviewState {
  if (cron === null) return { kind: "incomplete" };
  if (cronFields(cron) === null) return { kind: "not-five-fields" };
  if (preview.refusal)
    return { kind: "refused", text: preview.refusal.detail ?? preview.refusal.title };
  const read = preview.read;
  if (preview.waiting || (read.isPending && read.fetchStatus !== "idle"))
    return { kind: "waiting" };
  if (!read.data) return { kind: "failed", text: failureText(read.error) };
  return { kind: "starts", starts: read.data.starts };
}

function Computing({ as: As, className }: { as: "li" | "span"; className?: string }) {
  return (
    <As className={cn("flex items-center gap-2 text-foreground-muted text-sm", className)}>
      <Spinner aria-hidden="true" className="motion-reduce:animate-none" />
      Computing the next start times…
    </As>
  );
}

/**
 * The next starts of the cadence entered, in the look of the schedule page's timeline: above a
 * "Now" marker, the soonest nearest it, in the viewer's time zone with the schedule's on hover.
 * When there is no start, the timeline says why.
 */
export function CadenceTimeline({
  cron,
  preview,
  count,
}: {
  cron: string | null;
  preview: CadencePreview;
  /** The number of starts asked for. */
  count: number;
}) {
  const now = useNow();
  const state = previewState(cron, preview);
  let body: ReactNode;
  switch (state.kind) {
    case "incomplete":
      body = <TimelineMessage>Complete the cadence to view its next starts.</TimelineMessage>;
      break;
    case "not-five-fields":
      body = <TimelineMessage>{NOT_FIVE_FIELDS}</TimelineMessage>;
      break;
    case "refused":
      body = (
        <TimelineMessage alert>
          <span className="font-medium text-danger">No start. </span>
          {state.text}
        </TimelineMessage>
      );
      break;
    case "waiting":
      body = <Computing as="li" />;
      break;
    case "failed":
      body = <TimelineMessage alert>{state.text}</TimelineMessage>;
      break;
    case "starts":
      body = (
        <>
          {state.starts.length < count && (
            <TimelineMessage>
              {state.starts.length === 0 ? "No start" : "No further start"} in the next 5 years.
            </TimelineMessage>
          )}
          <NextStartGroups groups={startGroups(state.starts, now)} timeZone={preview.timezone} />
        </>
      );
      break;
  }
  return (
    <div className="flex flex-col gap-2">
      <Card className="bg-content bg-none px-3 py-3">
        <ol className="flex flex-col" aria-label="Next starts">
          {body}
          <NowMarker />
        </ol>
      </Card>
      {state.kind === "starts" && state.starts.length > 0 && (
        <p className="text-subtle text-xs">In your time zone. Hover a time for the schedule’s.</p>
      )}
    </div>
  );
}

const startFormat = new Intl.DateTimeFormat(undefined, {
  weekday: "short",
  day: "numeric",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

/** The next start in one line, or why there is none: the folded preview of a narrow screen. */
export function NextStartLine({
  cron,
  preview,
  className,
}: {
  cron: string | null;
  preview: CadencePreview;
  className?: string;
}) {
  const now = useNow();
  const state = previewState(cron, preview);
  let content: ReactNode;
  switch (state.kind) {
    case "incomplete":
      content = "Complete the cadence to view its next start.";
      break;
    case "not-five-fields":
      content = NOT_FIVE_FIELDS;
      break;
    case "refused":
      content = <span className="text-danger">No start. {state.text}</span>;
      break;
    case "waiting":
      return <Computing as="span" className={className} />;
    case "failed":
      content = state.text;
      break;
    case "starts": {
      const first = state.starts[0];
      content = first ? (
        <>
          Next start{" "}
          <span className="text-foreground tabular-nums">
            {startFormat.format(new Date(first))}
          </span>
          , {startsIn(first, now)}.
        </>
      ) : (
        "No start in the next 5 years."
      );
      break;
    }
  }
  return <span className={className}>{content}</span>;
}
