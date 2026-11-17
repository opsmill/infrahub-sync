import { QueryClient } from "@tanstack/react-query";
import createFetchClient from "openapi-fetch";
import createQueryHooks from "openapi-react-query";

import { isEnvelope, isTimeout, REQUEST_TIMEOUT_MS, SyncApiUnavailable } from "./errors";
import { readToken } from "./token";
import type { paths } from "./types.generated";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 10_000,
      // A refusal does not change on retry, and a timeout has already waited 20 s.
      retry: (failureCount, error) => {
        if (isTimeout(error)) return false;
        const status = isEnvelope(error) ? error.error.status : undefined;
        if (status !== undefined && status < 500 && status !== 408 && status !== 429) return false;
        return failureCount < 1;
      },
    },
  },
});

// Only GET requests time out: aborting a write in the browser does not stop it in the Sync service.
export const fetchClient = createFetchClient<paths>({
  baseUrl: "/api",
  fetch: (request) =>
    globalThis.fetch(
      request,
      request.method === "GET"
        ? { signal: AbortSignal.any([request.signal, AbortSignal.timeout(REQUEST_TIMEOUT_MS)]) }
        : undefined
    ),
});

// A stored token goes with every request. A request that names its own token, such as the token
// page checking a pasted one, keeps it.
fetchClient.use({
  onRequest({ request }) {
    const token = readToken();
    if (token && !request.headers.has("Authorization")) {
      request.headers.set("Authorization", `Bearer ${token}`);
    }
    return request;
  },
});

fetchClient.use({
  async onResponse({ response }) {
    if (response.ok) return;
    const text = await response.clone().text();
    let body: unknown;
    try {
      body = JSON.parse(text);
    } catch {
      body = undefined;
    }
    if (!isEnvelope(body)) throw new SyncApiUnavailable(response.status);
  },
});

export const $api = createQueryHooks(fetchClient);
