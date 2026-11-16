import { QueryClient } from "@tanstack/react-query";
import createFetchClient from "openapi-fetch";
import createQueryHooks from "openapi-react-query";

import { isEnvelope, isTimeout, REQUEST_TIMEOUT_MS, SyncApiUnavailable } from "./errors";
import { bearer, clearToken, getToken, refusesHeldToken } from "./token";
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

/**
 * Removes the held token and empties the query cache when the Sync API refuses the token the
 * request carried. A 401 for an earlier token and a 403 change nothing. Requests sent with a raw
 * `fetch` pass their request and answer here too.
 */
export function handleUnauthorized(request: Request, response: Response) {
  if (!refusesHeldToken(response.status, request.headers.get("Authorization"), getToken())) return;
  clearToken();
  queryClient.clear();
}

fetchClient.use({
  onRequest({ request }) {
    // A request that already names a token, such as the check of a typed token, keeps it.
    const token = getToken();
    if (token && !request.headers.has("Authorization")) {
      request.headers.set("Authorization", bearer(token));
    }
    return request;
  },
  async onResponse({ request, response }) {
    if (response.ok) return;
    handleUnauthorized(request, response);
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
