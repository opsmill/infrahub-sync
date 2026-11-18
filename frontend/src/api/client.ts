import { QueryClient } from "@tanstack/react-query";
import createFetchClient from "openapi-fetch";
import createQueryHooks from "openapi-react-query";

import { isEnvelope, isTimeout, REQUEST_TIMEOUT_MS, SyncApiUnavailable } from "./errors";
import { bearer, clearToken, getToken, onTokenCleared, refusesHeldToken, setToken } from "./token";
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

// Answers read with a removed token are not shown to the person who signs in next,
// whichever way the token was removed.
onTokenCleared(() => queryClient.clear());

/**
 * Removes the held token when the Sync API refuses the token the request carried. A 401 for an
 * earlier token and a 403 change nothing.
 */
function handleUnauthorized(request: Request, response: Response) {
  if (!refusesHeldToken(response.status, request.headers.get("Authorization"), getToken())) return;
  clearToken();
}

/**
 * Sends a GET outside the typed client, such as a file download, with the held token. An error
 * answer removes a refused token, then throws its envelope, or `SyncApiUnavailable` without one.
 */
export async function fetchWithToken(url: string, signal?: AbortSignal): Promise<Response> {
  const token = getToken();
  const request = new Request(url, {
    headers: token ? { Authorization: bearer(token) } : undefined,
  });
  // The wait for the response headers is limited as for the typed client; reading a long body
  // is not, so the timer stops once the headers arrive.
  const headers = new AbortController();
  const timer = setTimeout(
    () => headers.abort(new DOMException("No answer from the Sync API.", "TimeoutError")),
    REQUEST_TIMEOUT_MS
  );
  let response: Response;
  try {
    response = await fetch(request, {
      signal: signal ? AbortSignal.any([signal, headers.signal]) : headers.signal,
    });
  } finally {
    clearTimeout(timer);
  }
  if (response.ok) return response;
  handleUnauthorized(request, response);
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    body = undefined;
  }
  throw isEnvelope(body) ? body : new SyncApiUnavailable(response.status);
}

fetchClient.use({
  onRequest({ request }) {
    // A request that already names a token keeps it.
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

/** The result of a sign-in, without the password or the session token. */
export type SignInResult = "signed-in" | "refused" | "busy" | "no-answer";

/**
 * Signs in with an account name and a password, and keeps the session token for this tab when the
 * Sync API accepts them. The password is sent once and is not kept.
 */
export async function signIn(name: string, password: string): Promise<SignInResult> {
  try {
    const { data, error, response } = await fetchClient.POST("/auth/sign-in", {
      body: { name, password },
      // The Sync API can still open a session after the tab stops waiting. No one receives the
      // token of that session, so it cannot be used, and the Sync API refuses it after the idle
      // period. A second attempt opens a new session. Without the timeout, a Sync API that does
      // not answer would leave the form waiting with no way to try again.
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
    if (data?.token) {
      setToken(data.token);
      return "signed-in";
    }
    if (response.status === 401) return "refused";
    if (isEnvelope(error) && error.error.code === "sign-in-busy") return "busy";
    return "no-answer";
  } catch {
    // A network failure, a timeout, or an answer without a Sync API envelope.
    return "no-answer";
  }
}

/**
 * Ends the session with the Sync API, then removes the token from this tab whatever the answer:
 * a refused or unanswered sign-out, or an API token (`422 not-a-session`), still signs out here.
 */
export async function signOut(): Promise<void> {
  try {
    if (getToken()) {
      await fetchClient.POST("/auth/sign-out", { signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS) });
    }
  } catch {
    // The token is removed below.
  } finally {
    clearToken();
  }
}
