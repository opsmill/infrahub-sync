import { QueryClient } from "@tanstack/react-query";
import createFetchClient from "openapi-fetch";
import createQueryHooks from "openapi-react-query";

import type { paths } from "./types.generated";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 10_000,
    },
  },
});

export const fetchClient = createFetchClient<paths>({ baseUrl: "/api" });

export const $api = createQueryHooks(fetchClient);
