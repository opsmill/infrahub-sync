import { createFileRoute } from "@tanstack/react-router";

import { credentialsQuery, meQuery } from "#/api/queries";
import { CredentialsPage } from "#/features/credentials/CredentialsPage";

/** The form that the page opens when it loads: a new credential with this name, or a new value. */
export interface CredentialsSearch {
  /** Opens "New credential" with this name filled in. */
  new?: string;
  /** Opens "Change value" for the credential with this name. */
  value?: string;
}

const text = (value: unknown) => (typeof value === "string" && value ? value : undefined);

export const Route = createFileRoute("/credentials")({
  validateSearch: (search: Record<string, unknown>): CredentialsSearch => ({
    new: text(search.new),
    value: text(search.value),
  }),
  // Nothing is awaited and nothing is thrown: the page shows its own loading and error states.
  // Every signed-in account can read the credentials; /me says whether it views the actions.
  loader: ({ context: { queryClient } }) => {
    void queryClient.prefetchQuery(meQuery());
    void queryClient.prefetchQuery(credentialsQuery());
  },
  component: CredentialsPage,
});
