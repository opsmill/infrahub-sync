import type { ApiErrorBody } from "./types";

/** A GET request the Sync API has not answered after this time is aborted. */
export const REQUEST_TIMEOUT_MS = 20_000;

/** True for the error envelope every Sync API error uses. */
export function isEnvelope(value: unknown): value is ApiErrorBody {
  return (
    typeof value === "object" &&
    value !== null &&
    "error" in value &&
    typeof value.error === "object" &&
    value.error !== null
  );
}

/** An error answer without a Sync API envelope, such as a proxy page when the Sync service is down. */
export class SyncApiUnavailable extends Error {
  constructor(readonly status: number) {
    super(`HTTP ${status}`);
    this.name = "SyncApiUnavailable";
  }
}

/** True when a GET request reached `REQUEST_TIMEOUT_MS` without an answer. */
export const isTimeout = (error: unknown): boolean =>
  error instanceof DOMException && error.name === "TimeoutError";
