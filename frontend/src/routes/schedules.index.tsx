import { createFileRoute } from "@tanstack/react-router";

import { configsQuery, schedulePagesQuery } from "#/api/queries";
import { SCHEDULE_TABS, type ScheduleTab } from "#/domain/schedule-list";
import { SchedulesPage, type SchedulesSearch } from "#/features/schedules/SchedulesPage";

/**
 * The Sync service's pattern for configuration IDs (`CONFIG_ID_PATTERN` in config_routes.py), as
 * in `routes/runs.index.tsx`.
 */
const CONFIG_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;

export const Route = createFileRoute("/schedules/")({
  validateSearch: (s: Record<string, unknown>): SchedulesSearch => ({
    show:
      SCHEDULE_TABS.includes(s.show as ScheduleTab) && s.show !== "all"
        ? (s.show as Exclude<ScheduleTab, "all">)
        : undefined,
    q: typeof s.q === "string" && s.q.trim() ? s.q : undefined,
    config: typeof s.config === "string" && CONFIG_ID.test(s.config) ? s.config : undefined,
  }),
  loaderDeps: ({ search }) => ({ show: search.show, config: search.config }),
  loader: ({ context: { queryClient }, deps }) => {
    void queryClient.prefetchInfiniteQuery(schedulePagesQuery({ config: deps.config }));
    if (deps.show === "deleted")
      void queryClient.prefetchInfiniteQuery(
        schedulePagesQuery({ state: "deleted", config: deps.config })
      );
    void queryClient.prefetchQuery(configsQuery());
  },
  component: SchedulesRoute,
});

function SchedulesRoute() {
  const { show, q, config } = Route.useSearch();
  return <SchedulesPage show={show} q={q} config={config} />;
}
