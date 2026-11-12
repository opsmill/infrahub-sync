import { createFileRoute } from "@tanstack/react-router";

import { configQuery, runsQuery, versionsQuery } from "#/api/queries";
import { ConfigurationPage } from "#/features/configurations/ConfigurationPage";

const positive = (value: unknown) => {
  const n = Number(value);
  return Number.isInteger(n) && n > 0 ? n : undefined;
};

export const Route = createFileRoute("/configs/$configId")({
  validateSearch: (
    search: Record<string, unknown>
  ): {
    /** The version shown; the latest when absent. */
    version?: number;
    /** An earlier version the mapping is compared with. */
    compare?: number;
    /** The mapping open in the panel: its kind, with `~2` for the second mapping of a kind. */
    kind?: string;
  } => {
    const version = positive(search.version);
    const compare = positive(search.compare);
    return {
      ...(version !== undefined ? { version } : {}),
      ...(compare !== undefined ? { compare } : {}),
      ...(typeof search.kind === "string" && search.kind ? { kind: search.kind } : {}),
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
  const { version, compare, kind } = Route.useSearch();
  return (
    <ConfigurationPage
      key={configId}
      configId={configId}
      version={version}
      compare={compare}
      kind={kind}
    />
  );
}
