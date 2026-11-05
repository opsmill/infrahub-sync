import { Button } from "@infrahub/ui";
import { useSuspenseQuery } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";

import { $api } from "#/api/client";

const versionQuery = $api.queryOptions("get", "/version");

export const Route = createFileRoute("/")({
  loader: ({ context }) => context.queryClient.query({ ...versionQuery, staleTime: "static" }),
  component: Home,
});

function Home() {
  const { data } = useSuspenseQuery(versionQuery);

  return (
    <div className="p-8">
      <h1 className="text-4xl font-bold">Welcome to TanStack Start</h1>
      <p className="mt-4 text-lg">
        Edit <code>src/routes/index.tsx</code> to get started.
      </p>
      <p className="mt-4">Sync API version {data.server_version}</p>
      <Button className="mt-4">Infrahub UI button</Button>
    </div>
  );
}
