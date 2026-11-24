import type { HealthReport } from "#/api/types";

export type Dependency = HealthReport["dependencies"][number];
export type Workers = HealthReport["workers"];
export type Worker = Workers["items"][number];

const COMPONENT_LABEL: Record<string, string> = {
  database: "Database",
  object_store: "Object store",
  task_manager: "Task manager",
  task_manager_db: "Task manager database",
};

/** A component the page has no name for is shown under the name the Sync API gives it. */
export const componentLabel = (name: string): string =>
  Object.hasOwn(COMPONENT_LABEL, name) ? COMPONENT_LABEL[name] : name;

const ERROR_TEXT: Record<string, string> = {
  timeout: "Did not answer within the time limit.",
  connection_refused: "Refused the connection.",
  connection_closed: "Closed the connection before it answered.",
  name_resolution_failed: "Its host name could not be resolved.",
  unavailable: "Answered that it is not available.",
  unknown_error: "The check failed for a reason the Sync API could not name.",
};

/** Why the health check of a component that is down failed, from the error category of the report. */
export const errorText = (dependency: Dependency): string =>
  Object.hasOwn(ERROR_TEXT, dependency.error) ? ERROR_TEXT[dependency.error] : "The check failed.";

export const WORKERS_UNKNOWN =
  "The Sync service could not read the state of its workers, so the number of workers is not known.";

export const NO_WORKER_LISTED = "No worker sent a heartbeat in the last 24 hours.";

/** Whether the report counts enough live workers. Null when the workers could not be read. */
export function enoughWorkers(workers: Workers): boolean | null {
  if (workers.state === "unknown" || workers.live_workers == null) return null;
  return workers.live_workers >= workers.minimum;
}

export const staleCount = (workers: Workers): number =>
  workers.items.filter((item) => item.state === "stale").length;

/** Two workers can share the name "other", so the name alone is not a key. */
export const workerKey = (worker: Worker, index: number): string =>
  `${worker.name}-${worker.last_heartbeat}-${index}`;
