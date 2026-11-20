import { createFileRoute } from "@tanstack/react-router";

import { scheduleQuery } from "#/api/queries";
import { SchedulePage } from "#/features/schedules/SchedulePage";

export const Route = createFileRoute("/schedules/$scheduleId")({
  loader: ({ context: { queryClient }, params }) => {
    void queryClient.prefetchQuery(scheduleQuery(params.scheduleId));
  },
  component: ScheduleRoute,
});

function ScheduleRoute() {
  const { scheduleId } = Route.useParams();
  return <SchedulePage key={scheduleId} scheduleId={scheduleId} />;
}
