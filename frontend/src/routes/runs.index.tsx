import { createFileRoute } from "@tanstack/react-router";

import { configsQuery, runPagesQuery } from "#/api/queries";
import { FILTER_IDS, type FilterId, RunsPage, type RunsSearch } from "#/features/runs/RunsPage";

/** The Sync service's pattern for configuration IDs (`CONFIG_ID_PATTERN` in config_routes.py). */
const CONFIG_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;

export const Route = createFileRoute("/runs/")({
  validateSearch: (s: Record<string, unknown>): RunsSearch => ({
    q: typeof s.q === "string" && s.q.trim() ? s.q : undefined,
    show:
      FILTER_IDS.includes(s.show as FilterId) && s.show !== "all"
        ? (s.show as FilterId)
        : undefined,
    config: typeof s.config === "string" && CONFIG_ID.test(s.config) ? s.config : undefined,
  }),
  loaderDeps: ({ search }) => ({ config: search.config }),
  loader: ({ context: { queryClient }, deps }) => {
    void queryClient.prefetchInfiniteQuery(runPagesQuery(deps.config));
    void queryClient.prefetchQuery(configsQuery());
  },
  component: RunsRoute,
});

function RunsRoute() {
  const { q, show, config } = Route.useSearch();
  return <RunsPage q={q} show={show} config={config} />;
}
