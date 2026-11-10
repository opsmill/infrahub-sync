import { createFileRoute } from "@tanstack/react-router";

import { runQuery } from "#/api/queries";
import { validateRunSearch } from "#/features/runs/ChangesSheet";
import { RunPage } from "#/features/runs/RunPage";

export const Route = createFileRoute("/runs/$runId")({
  validateSearch: validateRunSearch,
  loader: ({ context: { queryClient }, params }) => {
    void queryClient.prefetchQuery(runQuery(params.runId));
  },
  component: RunRoute,
});

function RunRoute() {
  const { runId } = Route.useParams();
  return <RunPage key={runId} runId={runId} />;
}
