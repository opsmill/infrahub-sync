import { createFileRoute } from "@tanstack/react-router";

import { configsQuery, configVersionQuery, runsQuery } from "#/api/queries";
import type { RunState } from "#/domain/run-state";
import { ConfigurationsPage } from "#/features/configurations/ConfigurationsPage";

const STATES: readonly RunState[] = ["failed", "review", "running", "idle", "insync"];

interface HomeSearch {
  q?: string;
  state?: RunState;
}

const isState = (value: unknown): value is RunState => STATES.includes(value as RunState);

export const Route = createFileRoute("/")({
  validateSearch: (search: Record<string, unknown>): HomeSearch => ({
    ...(typeof search.q === "string" && search.q.trim() ? { q: search.q } : {}),
    ...(isState(search.state) ? { state: search.state } : {}),
  }),
  // Nothing is awaited: the page shows its skeleton while the requests run.
  loader: ({ context: { queryClient } }) => {
    void queryClient
      .fetchQuery(configsQuery())
      .then((configs) => {
        for (const c of configs.items) {
          if (c.latest_version != null)
            void queryClient.prefetchQuery(configVersionQuery(c.config_id, c.latest_version));
        }
      })
      .catch(() => undefined);
    void queryClient.prefetchQuery(runsQuery());
  },
  component: HomeRoute,
});

function HomeRoute() {
  const { q = "", state } = Route.useSearch();
  const navigate = Route.useNavigate();
  return (
    <ConfigurationsPage
      query={q}
      state={state ?? "all"}
      onQueryChange={(v) =>
        navigate({
          search: (prev) => ({ ...prev, q: v || undefined }),
          replace: true,
          resetScroll: false,
        })
      }
      onStateChange={(s) =>
        navigate({
          search: (prev) => ({ ...prev, state: s === "all" ? undefined : s }),
          replace: true,
          resetScroll: false,
        })
      }
    />
  );
}
