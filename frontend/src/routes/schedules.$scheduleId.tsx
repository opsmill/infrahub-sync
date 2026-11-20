import { createFileRoute } from "@tanstack/react-router";

import { scheduleQuery } from "#/api/queries";
import { SchedulePage } from "#/features/schedules/SchedulePage";

export interface ScheduleSearch {
  /** `all` shows every event of the schedule's history, not only the schedule's own types. */
  events?: "all";
}

export const Route = createFileRoute("/schedules/$scheduleId")({
  validateSearch: (s: Record<string, unknown>): ScheduleSearch => ({
    events: s.events === "all" ? "all" : undefined,
  }),
  loader: ({ context: { queryClient }, params }) => {
    void queryClient.prefetchQuery(scheduleQuery(params.scheduleId));
  },
  component: ScheduleRoute,
});

function ScheduleRoute() {
  const { scheduleId } = Route.useParams();
  const { events } = Route.useSearch();
  return <SchedulePage key={scheduleId} scheduleId={scheduleId} showAllEvents={events === "all"} />;
}
