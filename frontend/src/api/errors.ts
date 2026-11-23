import type { ApiErrorBody, HealthReport } from "./types";

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

/**
 * True for the body of `GET /health`, which the Sync API also sends with a 503 when a component is
 * down. Only the fields the page reads are checked.
 */
export function isHealthReport(value: unknown): value is HealthReport {
  return (
    typeof value === "object" &&
    value !== null &&
    "status" in value &&
    "workers" in value &&
    "timestamp" in value &&
    typeof value.workers === "object" &&
    value.workers !== null
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
