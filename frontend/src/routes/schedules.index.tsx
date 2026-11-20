import { createFileRoute } from "@tanstack/react-router";

import { configsQuery, schedulePagesQuery } from "#/api/queries";
import {
  SCHEDULE_STATES,
  type ScheduleStateFilter,
  SchedulesPage,
  type SchedulesSearch,
} from "#/features/schedules/SchedulesPage";

/**
 * The Sync service's pattern for configuration IDs (`CONFIG_ID_PATTERN` in config_routes.py), as
 * in `routes/runs.index.tsx`.
 */
const CONFIG_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;

export const Route = createFileRoute("/schedules/")({
  validateSearch: (s: Record<string, unknown>): SchedulesSearch => ({
    state: SCHEDULE_STATES.includes(s.state as ScheduleStateFilter)
      ? (s.state as ScheduleStateFilter)
      : undefined,
    config: typeof s.config === "string" && CONFIG_ID.test(s.config) ? s.config : undefined,
  }),
  loaderDeps: ({ search }) => ({ state: search.state, config: search.config }),
  loader: ({ context: { queryClient }, deps }) => {
    void queryClient.prefetchInfiniteQuery(schedulePagesQuery(deps));
    void queryClient.prefetchQuery(configsQuery());
  },
  component: SchedulesRoute,
});

function SchedulesRoute() {
  const { state, config } = Route.useSearch();
  return <SchedulesPage state={state} config={config} />;
}
