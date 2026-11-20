import { Button, Card, Checkbox, Spinner } from "@infrahub/ui";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { type ReactNode, useMemo, useRef } from "react";
import { cn } from "tailwind-variants";

import { describeError, scheduleEventsQuery, scheduleNewestEventsQuery } from "#/api/queries";
import type { SyncEvent } from "#/api/types";
import { Bar, EmptyPanel, ErrorPanel, FOCUS_INSET, SectionHeading } from "#/components/page";
import { When } from "#/components/run-status";
import { eventRunId, eventText, withNewestEvents } from "#/domain/schedule-events";
import { LINK } from "#/features/runs/form";
import { formatAbsolute } from "#/lib/time";

/** Button sizes reach 44 px on touch, like the runs list. */
const TOUCH = "pointer-coarse:h-11";

/**
 * The "History" section of a schedule page (spec 026 User Story 7): the schedule's events, newest
 * first, 50 at a time. The pages share the `until` of the first read; the newest events are read
 * again every 10 seconds without `until` and the ones not listed yet are added above the list
 * (contracts/http-api.md). "Show all events" is kept in the search parameter `events=all`. The
 * section stays for a deleted schedule. An entry names the account as the Sync API returns it.
 */
export function ScheduleHistory({
  scheduleId,
  includeAll,
}: {
  scheduleId: string;
  includeAll: boolean;
}) {
  const navigate = useNavigate();
  const history = useInfiniteQuery(scheduleEventsQuery(scheduleId, { includeAll }));
  const newest = useQuery({
    ...scheduleNewestEventsQuery(scheduleId, { includeAll }),
    // The first page is the newest read; the polling starts once it is shown.
    enabled: history.isSuccess,
  });

  const entries = useMemo(
    () =>
      withNewestEvents(
        newest.data?.items ?? [],
        history.data?.pages.flatMap((page) => page.items) ?? []
      ),
    [newest.data, history.data]
  );
  const listRef = useRef<HTMLOListElement>(null);

  const setIncludeAll = (value: boolean) =>
    navigate({
      to: "/schedules/$scheduleId",
      params: { scheduleId },
      search: { events: value ? "all" : undefined },
      replace: true,
      resetScroll: false,
    });

  const loadOlder = async () => {
    const r = await history.fetchNextPage();
    if (r.isError) return;
    const id = r.data?.pages.at(-1)?.items[0]?.event_id;
    requestAnimationFrame(() => {
      const item = id
        ? listRef.current?.querySelector<HTMLElement>(`li[data-event-id="${CSS.escape(id)}"]`)
        : undefined;
      item?.focus();
    });
  };

  let body: ReactNode;
  if (history.isPending) {
    body = (
      <Card className="flex flex-col gap-3 p-4" aria-hidden="true">
        {[0, 1, 2].map((i) => (
          <div key={i} className="flex flex-col gap-1.5">
            <Bar className="h-4 w-72 max-w-full" />
            <Bar className="h-3 w-40" />
          </div>
        ))}
      </Card>
    );
  } else if (!history.data) {
    body = <ErrorPanel error={history.error} onRetry={() => history.refetch()} />;
  } else if (entries.length === 0) {
    body = <EmptyPanel title="No event recorded yet." />;
  } else {
    body = (
      <>
        <Card className="p-0">
          <ol ref={listRef} aria-label="Events, newest first" className="divide-y">
            {entries.map((event) => (
              <HistoryEntry key={event.event_id} event={event} />
            ))}
          </ol>
        </Card>
        {history.hasNextPage && (
          <div className="flex flex-col items-center gap-2">
            <LoadOlder history={history} onLoad={loadOlder} />
          </div>
        )}
      </>
    );
  }

  return (
    <section aria-labelledby="schedule-history" className="flex flex-col gap-3">
      <SectionHeading
        id="schedule-history"
        title="History"
        actions={
          <Checkbox
            isSelected={includeAll}
            onChange={setIncludeAll}
            className="items-center gap-2 text-foreground text-sm"
          >
            Show all events
          </Checkbox>
        }
      >
        Newest first
      </SectionHeading>
      {body}
    </section>
  );
}

/** One event: its time, what happened, the account, the reason and the run it names. */
function HistoryEntry({ event }: { event: SyncEvent }) {
  const { text, detail } = eventText(event);
  const runId = eventRunId(event);
  return (
    <li
      data-event-id={event.event_id}
      tabIndex={-1}
      className={cn("flex flex-col gap-1 px-4 py-3 text-[13px]", FOCUS_INSET)}
    >
      <p className="text-pretty font-medium text-foreground [overflow-wrap:anywhere]">{text}</p>
      {detail && (
        <p className="text-pretty text-foreground-muted [overflow-wrap:anywhere]">{detail}</p>
      )}
      {event.reason && (
        <p className="text-pretty text-foreground-muted [overflow-wrap:anywhere]">
          Reason: “{event.reason}”
        </p>
      )}
      <p className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-subtle text-xs">
        <span>
          <time dateTime={event.occurred_at}>{formatAbsolute(event.occurred_at)}</time> (
          <When iso={event.occurred_at} />)
        </span>
        <span aria-hidden="true">·</span>
        <span className="[overflow-wrap:anywhere]">
          By <span className="text-foreground">{event.actor}</span>
        </span>
        {runId && (
          <>
            <span aria-hidden="true">·</span>
            <Link to="/runs/$runId" params={{ runId }} className={LINK}>
              View run
            </Link>
          </>
        )}
      </p>
    </li>
  );
}

/** As the runs list's "Load older runs" (`features/runs/RunsPage.tsx`). */
function LoadOlder({
  history,
  onLoad,
}: {
  history: { isFetchingNextPage: boolean; isFetchNextPageError: boolean; error: unknown };
  onLoad: () => void;
}) {
  const d = history.isFetchNextPageError ? describeError(history.error) : undefined;
  return (
    <>
      {d && !history.isFetchingNextPage && (
        <p role="alert" className="max-w-[60ch] text-pretty text-center text-danger text-sm">
          Could not load older events. {d.detail ?? `${d.title}.`}
        </p>
      )}
      <Button
        variant="outline"
        size="sm"
        className={TOUCH}
        isDisabledAndFocusable={history.isFetchingNextPage}
        onPress={onLoad}
      >
        {history.isFetchingNextPage ? (
          <>
            <Spinner aria-hidden="true" className="motion-reduce:animate-none" />
            Loading older events…
          </>
        ) : history.isFetchNextPageError ? (
          "Try again"
        ) : (
          "Load older events"
        )}
      </Button>
    </>
  );
}
