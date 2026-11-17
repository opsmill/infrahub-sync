import { useSyncExternalStore } from "react";

/*
 * The Sync API token the person entered. It is kept for this browser tab only: `sessionStorage`
 * is discarded when the tab closes. When the browser blocks storage, the token is kept in memory
 * and is lost on a reload.
 */

const STORAGE_KEY = "infrahub-sync.api-token";

/** The token to keep for a typed value: trimmed, or `undefined` when nothing is left. */
export function normalizeToken(value: string | null | undefined): string | undefined {
  const trimmed = value?.trim();
  return trimmed ? trimmed : undefined;
}

/** The `Authorization` header value that carries a token. */
export function bearer(token: string): string {
  return `Bearer ${token}`;
}

/**
 * True when a 401 answers a request that carried the token held now. A 401 for a request sent
 * with an earlier token must not remove a token entered since.
 */
export function refusesHeldToken(
  status: number,
  sentAuthorization: string | null,
  held: string | undefined
): boolean {
  return status === 401 && held !== undefined && sentAuthorization === bearer(held);
}

const listeners = new Set<() => void>();
const clearedListeners = new Set<() => void>();

function readStored(): string | undefined {
  try {
    return normalizeToken(sessionStorage.getItem(STORAGE_KEY));
  } catch {
    // Storage throws when the browser blocks site data.
    return undefined;
  }
}

let token = readStored();

function update(next: string | undefined) {
  token = next;
  for (const listener of listeners) listener();
}

/** The token held for this tab, if any. */
export function getToken(): string | undefined {
  return token;
}

/** Keeps a token for this tab. An empty value is ignored. */
export function setToken(value: string) {
  const next = normalizeToken(value);
  if (!next) return;
  try {
    sessionStorage.setItem(STORAGE_KEY, next);
  } catch {
    // The token still applies to this page, but it is not kept after a reload.
  }
  update(next);
}

/** Removes the token from this tab and tells the caches that subscribed with `onTokenCleared` to empty. */
export function clearToken() {
  try {
    sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    // Nothing was stored.
  }
  const had = token !== undefined;
  update(undefined);
  if (had) for (const listener of clearedListeners) listener();
}

/** Calls `listener` each time the held token is removed; returns the unsubscribe function. */
export function onTokenCleared(listener: () => void): () => void {
  clearedListeners.add(listener);
  return () => clearedListeners.delete(listener);
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** The token held for this tab, following `setToken` and `clearToken`. */
export function useToken(): string | undefined {
  return useSyncExternalStore(subscribe, getToken);
}
