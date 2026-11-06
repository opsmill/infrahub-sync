import { createFileRoute } from "@tanstack/react-router";

import { runQuery } from "#/api/queries";
import { RunPage } from "#/features/runs/RunPage";

export const Route = createFileRoute("/runs/$runId")({
  loader: ({ context: { queryClient }, params }) =>
    queryClient.prefetchQuery(runQuery(params.runId)),
  component: RunRoute,
});

function RunRoute() {
  const { runId } = Route.useParams();
  return <RunPage key={runId} runId={runId} />;
}
