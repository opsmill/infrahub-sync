import { createFileRoute } from "@tanstack/react-router";

import { RunsPage } from "#/features/runs/RunsPage";

export const Route = createFileRoute("/runs/")({
  component: RunsPage,
});
