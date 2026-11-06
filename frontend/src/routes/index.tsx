import { createFileRoute } from "@tanstack/react-router";

import { configsQuery, runsQuery } from "#/api/queries";
import { ConfigurationsPage } from "#/features/configurations/ConfigurationsPage";

export const Route = createFileRoute("/")({
  // Prefetching never throws: the page shows the error itself, next to a way to retry.
  loader: ({ context: { queryClient } }) =>
    Promise.all([
      queryClient.prefetchQuery(configsQuery()),
      queryClient.prefetchQuery(runsQuery()),
    ]),
  component: ConfigurationsPage,
});
