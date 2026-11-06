import { queryOptions } from "@tanstack/react-query";

import { $api, fetchClient } from "./client";
import type {
  ApiErrorBody,
  ConfigurationSummary,
  ConfigurationVersion,
  Run,
  RunResource,
} from "./types";

/** Lists are re-read on this interval, so a run started elsewhere appears without a reload. */
const LIST_REFRESH_MS = 10_000;
/** A run in progress is re-read on this interval. */
const RUN_REFRESH_MS = 2_000;

/** The largest page the Sync API serves. */
export const PAGE_LIMIT = 200;

export const statusQuery = () =>
  $api.queryOptions("get", "/status", {}, { refetchInterval: 15_000, retry: false });

export const meQuery = () => $api.queryOptions("get", "/me", {}, { staleTime: 5 * 60_000 });

export const versionQuery = () => $api.queryOptions("get", "/version", {}, { staleTime: "static" });

export const configsQuery = () =>
  $api.queryOptions(
    "get",
    "/configs",
    { params: { query: { limit: String(PAGE_LIMIT) } } },
    { refetchInterval: LIST_REFRESH_MS }
  );

/** `GET /configs/{config_id}`; the OpenAPI document types its response as `any`. */
export const configQuery = (configId: string) =>
  queryOptions({
    queryKey: ["get", "/configs/{config_id}", configId],
    queryFn: async ({ signal }) => {
      const { data, error } = await fetchClient.GET("/configs/{config_id}", {
        params: { path: { config_id: configId } },
        signal,
      });
      if (error) throw error;
      return data as ConfigurationSummary;
    },
  });

export const versionsQuery = (configId: string) =>
  $api.queryOptions(
    "get",
    "/configs/{config_id}/versions",
    { params: { path: { config_id: configId }, query: { limit: String(PAGE_LIMIT) } } },
    { refetchInterval: LIST_REFRESH_MS }
  );

/** A registered version never changes, so it is read once. */
export const configVersionQuery = (configId: string, version: number) =>
  queryOptions({
    queryKey: ["get", "/configs/{config_id}/versions/{registry_version}", configId, version],
    queryFn: async ({ signal }) => {
      const { data, error } = await fetchClient.GET(
        "/configs/{config_id}/versions/{registry_version}",
        {
          params: { path: { config_id: configId, registry_version: String(version) } },
          signal,
        }
      );
      if (error) throw error;
      return data as ConfigurationVersion;
    },
    staleTime: "static",
  });

export const runsQuery = (filters: { config_id?: string } = {}) =>
  $api.queryOptions(
    "get",
    "/runs",
    { params: { query: { ...filters, limit: String(PAGE_LIMIT) } } },
    { refetchInterval: LIST_REFRESH_MS }
  );

/** True while an execution of the run (plan, verify, apply or sync) has not finished. */
export function isInProgress(resource: RunResource | undefined): boolean {
  if (!resource) return false;
  if (resource.run.phase === "accepted") return true;
  return resource.orchestration.some((o) => o.terminal_at == null);
}

export const runQuery = (runId: string) =>
  $api.queryOptions(
    "get",
    "/runs/{run_id}",
    { params: { path: { run_id: runId } } },
    {
      refetchInterval: (query) =>
        isInProgress(query.state.data as RunResource | undefined) ? RUN_REFRESH_MS : false,
    }
  );

export const planQuery = (runId: string) =>
  $api.queryOptions(
    "get",
    "/runs/{run_id}/plan",
    { params: { path: { run_id: runId } } },
    { retry: false }
  );

export const artifactsQuery = (runId: string) =>
  $api.queryOptions("get", "/runs/{run_id}/artifacts", { params: { path: { run_id: runId } } });

/** The newest run of each configuration, from one page of `GET /runs`. */
export function latestRunByConfig(runs: Run[]): Map<string, Run> {
  const latest = new Map<string, Run>();
  for (const run of runs) {
    if (run.config_id && !latest.has(run.config_id)) latest.set(run.config_id, run);
  }
  return latest;
}

/* ---------- errors ---------- */

export interface DescribedError {
  title: string;
  detail: string;
  status?: number;
  code?: string;
}

function isEnvelope(value: unknown): value is ApiErrorBody {
  return (
    typeof value === "object" &&
    value !== null &&
    "error" in value &&
    typeof value.error === "object"
  );
}

/** A sentence for each failure a page can meet: the API is down, the token is refused, or the API refused. */
export function describeError(error: unknown): DescribedError {
  if (isEnvelope(error)) {
    const { code, message, status } = error.error;
    if (status === 401) {
      return {
        title: "The Sync API refused the token",
        detail:
          "Set INFRAHUB_SYNC_API_TOKEN to a bearer token the Sync API accepts, then restart the development server.",
        status,
        code,
      };
    }
    return {
      title: message.charAt(0).toUpperCase() + message.slice(1),
      detail: code,
      status,
      code,
    };
  }
  if (error instanceof TypeError || typeof error === "string") {
    return {
      title: "The Sync API did not answer",
      detail:
        "Check that the Sync service is running and that INFRAHUB_SYNC_API_URL points to it, then try again.",
    };
  }
  return { title: "Something went wrong", detail: String(error) };
}
