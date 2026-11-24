import { createFileRoute } from "@tanstack/react-router";

import { credentialsQuery, meQuery } from "#/api/queries";
import { CredentialsPage } from "#/features/credentials/CredentialsPage";

export const Route = createFileRoute("/credentials")({
  // Nothing is awaited and nothing is thrown: the page shows its own loading and error states.
  // Every signed-in account can read the credentials; /me says whether it views the actions.
  loader: ({ context: { queryClient } }) => {
    void queryClient.prefetchQuery(meQuery());
    void queryClient.prefetchQuery(credentialsQuery());
  },
  component: CredentialsPage,
});
