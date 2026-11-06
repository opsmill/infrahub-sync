import { createFileRoute } from "@tanstack/react-router";

import { configQuery, runsQuery, versionsQuery } from "#/api/queries";
import { type ConfigTab, ConfigurationPage } from "#/features/configurations/ConfigurationPage";

const TABS: ConfigTab[] = ["runs", "versions", "mapping"];

export const Route = createFileRoute("/configs/$configId")({
  validateSearch: (search: Record<string, unknown>): { tab?: ConfigTab; version?: number } => {
    const version = Number(search.version);
    return {
      ...(TABS.includes(search.tab as ConfigTab) ? { tab: search.tab as ConfigTab } : {}),
      ...(Number.isInteger(version) && version > 0 ? { version } : {}),
    };
  },
  loader: ({ context: { queryClient }, params: { configId } }) => {
    void queryClient.prefetchQuery(configQuery(configId));
    void queryClient.prefetchQuery(versionsQuery(configId));
    void queryClient.prefetchQuery(runsQuery({ config_id: configId }));
  },
  component: ConfigurationRoute,
});

function ConfigurationRoute() {
  const { configId } = Route.useParams();
  const { tab = "runs", version } = Route.useSearch();
  return <ConfigurationPage key={configId} configId={configId} tab={tab} version={version} />;
}
