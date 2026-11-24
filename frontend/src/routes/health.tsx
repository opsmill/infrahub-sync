import { createFileRoute } from "@tanstack/react-router";

import { healthQuery } from "#/api/queries";
import { HealthPage } from "#/features/health/HealthPage";

export const Route = createFileRoute("/health")({
  loader: ({ context: { queryClient } }) => {
    void queryClient.prefetchQuery(healthQuery());
  },
  component: HealthPage,
});
