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

const isObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const isWorker = (value: unknown): boolean =>
  isObject(value) &&
  typeof value.name === "string" &&
  typeof value.state === "string" &&
  typeof value.last_heartbeat === "string";

const isDependency = (value: unknown): boolean =>
  isObject(value) && typeof value.name === "string" && typeof value.status === "string";

const isWorkers = (value: unknown): boolean => {
  if (!isObject(value)) return false;
  // The Health page reads the list of workers whatever the state.
  if (!Array.isArray(value.items) || !value.items.every(isWorker)) return false;
  if (value.state === "unknown") return true;
  return (
    value.state === "known" &&
    typeof value.minimum === "number" &&
    typeof value.live_workers === "number" &&
    typeof value.queue_depth === "number"
  );
};

/**
 * True for the body of `GET /health`, which the Sync API also sends with a 503 when a component is
 * down. Only the fields the page reads are checked, so a malformed body counts as no answer
 * instead of breaking the page.
 */
export function isHealthReport(value: unknown): value is HealthReport {
  return (
    isObject(value) &&
    (value.status === "healthy" || value.status === "unhealthy") &&
    Array.isArray(value.dependencies) &&
    value.dependencies.every(isDependency) &&
    typeof value.timestamp === "string" &&
    isWorkers(value.workers)
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
