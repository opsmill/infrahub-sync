import { createFileRoute } from "@tanstack/react-router";

import { configQuery, runsQuery, versionsQuery } from "#/api/queries";
import { type ConfigTab, ConfigurationPage } from "#/features/configurations/ConfigurationPage";

const TABS: ConfigTab[] = ["runs", "versions", "mapping"];

export const Route = createFileRoute("/configs/$configId")({
  validateSearch: (search: Record<string, unknown>): { tab?: ConfigTab } =>
    TABS.includes(search.tab as ConfigTab) ? { tab: search.tab as ConfigTab } : {},
  loader: ({ context: { queryClient }, params: { configId } }) =>
    Promise.all([
      queryClient.prefetchQuery(configQuery(configId)),
      queryClient.prefetchQuery(versionsQuery(configId)),
      queryClient.prefetchQuery(runsQuery({ config_id: configId })),
    ]),
  component: ConfigurationRoute,
});

function ConfigurationRoute() {
  const { configId } = Route.useParams();
  const { tab = "runs" } = Route.useSearch();
  return <ConfigurationPage key={configId} configId={configId} tab={tab} />;
}
