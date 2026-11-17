import { useEffect, useSyncExternalStore } from "react";

/**
 * The bearer token a person pasted into the web interface and the Sync API accepted. It stays in
 * this browser, for this address, until the person selects Forget token.
 */
export const TOKEN_KEY = "infrahub-sync.api-token";

/** Dispatched on this tab when the stored token changes; other tabs receive a `storage` event. */
const CHANGED = "infrahub-sync:token-changed";

/** The stored token, or null when none is stored or the browser blocks storage. */
export function readToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY) || null;
  } catch {
    return null;
  }
}

/** Applies a change to storage and tells this tab; false when the browser blocks storage. */
function changeToken(update: () => void): boolean {
  try {
    update();
  } catch {
    return false;
  }
  window.dispatchEvent(new Event(CHANGED));
  return true;
}

/**
 * Stores the token, which the caller has checked with the Sync API. Returns false when the browser
 * blocks storage and so keeps nothing.
 */
export const writeToken = (token: string): boolean =>
  changeToken(() => localStorage.setItem(TOKEN_KEY, token));

/** Deletes the stored token at once. Returns false when the browser blocks storage. */
export const deleteToken = (): boolean => changeToken(() => localStorage.removeItem(TOKEN_KEY));

/** True for a `storage` event that changed the stored token in another tab, or cleared all storage. */
const isTokenStorageEvent = (event: StorageEvent): boolean =>
  event.key === TOKEN_KEY || event.key === null;

/** Listens for token changes in other tabs; returns the function that stops listening. */
function subscribeToOtherTabs(onChange: () => void): () => void {
  const onStorage = (event: StorageEvent) => {
    if (isTokenStorageEvent(event)) onChange();
  };
  window.addEventListener("storage", onStorage);
  return () => window.removeEventListener("storage", onStorage);
}

function subscribe(onChange: () => void): () => void {
  const stopOtherTabs = subscribeToOtherTabs(onChange);
  window.addEventListener(CHANGED, onChange);
  return () => {
    window.removeEventListener(CHANGED, onChange);
    stopOtherTabs();
  };
}

/** Whether a token is stored, updated when this tab or another one changes it. */
export function useHasStoredToken(): boolean {
  return useSyncExternalStore(subscribe, () => readToken() !== null);
}

/** Calls `onChange` when another tab stores or forgets a token. */
export function useOnTokenChangedInAnotherTab(onChange: () => void): void {
  useEffect(() => subscribeToOtherTabs(onChange), [onChange]);
}
