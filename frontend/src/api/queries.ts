import { infiniteQueryOptions, queryOptions } from "@tanstack/react-query";

import { $api, fetchClient } from "./client";
import { isEnvelope, isTimeout, SyncApiUnavailable } from "./errors";
import type {
  ConfigurationSummary,
  ConfigurationVersion,
  Run,
  RunLogLine,
  RunResource,
} from "./types";

/** Lists are re-read on this interval, so a run started elsewhere appears without a reload. */
export const LIST_REFRESH_MS = 10_000;
/** A run in progress is re-read on this interval. */
const RUN_REFRESH_MS = 2_000;

/** The largest page the Sync API serves. */
const PAGE_LIMIT = 200;

type PolledQuery = { state: { data?: unknown; status: string } };

/**
 * True when a query has no data and its last read failed. Polling must stop there: a refetch of a
 * query without data puts it back in the pending state, so the page would swap its error message
 * for its skeleton on every interval. "Try again" still reads it again.
 */
export const failedFirstRead = (query: PolledQuery): boolean =>
  query.state.data === undefined && query.state.status === "error";

const poll =
  (ms: number) =>
  (query: PolledQuery): number | false =>
    failedFirstRead(query) ? false : ms;

/**
 * The status check keeps polling after a failure, so the header turns back to green when the Sync
 * API answers again. It uses the shared retry rule: a timeout or a refusal is not retried.
 */
export const statusQuery = () =>
  $api.queryOptions("get", "/status", {}, { refetchInterval: 15_000, retryDelay: 1_000 });

export const meQuery = () => $api.queryOptions("get", "/me", {}, { staleTime: 5 * 60_000 });

export const versionQuery = () => $api.queryOptions("get", "/version", {}, { staleTime: "static" });

export const configsQuery = () =>
  $api.queryOptions(
    "get",
    "/configs",
    { params: { query: { limit: String(PAGE_LIMIT) } } },
    { refetchInterval: poll(LIST_REFRESH_MS) }
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
    refetchInterval: poll(LIST_REFRESH_MS),
  });

export const versionsQuery = (configId: string) =>
  $api.queryOptions(
    "get",
    "/configs/{config_id}/versions",
    { params: { path: { config_id: configId }, query: { limit: String(PAGE_LIMIT) } } },
    { refetchInterval: poll(LIST_REFRESH_MS) }
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

export const runsQuery = (filters: { config_id?: string; registry_version?: number } = {}) =>
  $api.queryOptions(
    "get",
    "/runs",
    {
      params: {
        query: {
          ...(filters.config_id !== undefined && { config_id: filters.config_id }),
          ...(filters.registry_version !== undefined && {
            registry_version: String(filters.registry_version),
          }),
          limit: String(PAGE_LIMIT),
        },
      },
    },
    { refetchInterval: poll(LIST_REFRESH_MS) }
  );

/** The newest run of one configuration, for when the newest page of `GET /runs` does not include it. */
export const latestRunQuery = (configId: string) =>
  $api.queryOptions(
    "get",
    "/runs",
    { params: { query: { config_id: configId, limit: "1" } } },
    { refetchInterval: poll(LIST_REFRESH_MS) }
  );

/**
 * `GET /runs` 50 runs at a time, newest first, optionally for one configuration. With older pages
 * loaded, every page is read again on each refresh, so the interval grows.
 */
export const runPagesQuery = (config?: string) =>
  infiniteQueryOptions({
    queryKey: ["get", "/runs", "pages", { config }],
    initialPageParam: null as string | null,
    queryFn: async ({ pageParam, signal }) => {
      const { data, error } = await fetchClient.GET("/runs", {
        params: {
          query: {
            limit: "50",
            ...(pageParam ? { cursor: pageParam } : {}),
            ...(config ? { config_id: config } : {}),
          },
        },
        signal,
      });
      if (error) throw error;
      return data;
    },
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    refetchInterval: (query) =>
      failedFirstRead(query)
        ? false
        : (query.state.data?.pages.length ?? 1) > 1
          ? 30_000
          : LIST_REFRESH_MS,
  });

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

/** A step in progress asks for the new lines of its log on this interval. */
export const LOG_REFRESH_MS = 2_000;
/** The most lines the Sync API returns in one page of a log. */
const LOG_PAGE_LIMIT = 1000;

export interface RunLog {
  /** The Sync API no longer holds the lines of this stage (410). */
  expired: boolean;
  /** Oldest first. */
  lines: RunLogLine[];
}

/**
 * The lines Sync wrote during one stage of a run. The first read takes every page; a later read
 * asks only for the lines after those already held, so a step in progress is cheap to follow.
 * A log is never stale by itself: the caller reads it again while its step runs.
 */
export const runLogQuery = (runId: string, stage: string) =>
  queryOptions({
    queryKey: ["get", "/runs/{run_id}/logs", { runId, stage }] as const,
    queryFn: async ({ client, queryKey, signal }): Promise<RunLog> => {
      const lines = [...(client.getQueryData<RunLog>(queryKey)?.lines ?? [])];
      for (;;) {
        const { data, error, response } = await fetchClient.GET("/runs/{run_id}/logs", {
          params: {
            path: { run_id: runId },
            query: { stage, limit: String(LOG_PAGE_LIMIT), offset: String(lines.length) },
          },
          signal,
        });
        if (response.status === 410) return { expired: true, lines: [] };
        if (error) throw error;
        lines.push(...data.items);
        if (!data.has_more) return { expired: false, lines };
      }
    },
    staleTime: Number.POSITIVE_INFINITY,
  });

/** The newest run of each configuration, from one page of `GET /runs`. */
export function latestRunByConfig(runs: Run[]): Map<string, Run> {
  const latest = new Map<string, Run>();
  for (const run of runs) {
    if (run.config_id && !latest.has(run.config_id)) latest.set(run.config_id, run);
  }
  return latest;
}

/* ---------- errors ---------- */

/** What a page says about a failed request. */
export interface DescribedError {
  title: string;
  detail?: string;
  /** The HTTP status of a Sync API error envelope. */
  status?: number;
  code?: string;
  reason?: string;
  runId?: string;
  /** `[code, reason, status && `HTTP ${status}`]` joined with " · ", or the raw value for an unknown error. */
  codeLine?: string;
  retryable: boolean;
  notFound: boolean;
  /** "envelope": the Sync API answered with an error. "no-answer": no answer arrived (network, proxy, time limit). */
  kind: "envelope" | "no-answer" | "unknown";
}

/** Refusals of a run action, in words. The keys are the Sync API error codes. */
const ACTION_ERRORS: Record<string, { title: string; detail?: string }> = {
  "confirmation-required": {
    title: "The write was not confirmed",
    detail: "Select the confirmation, then press the button again.",
  },
  "checksum-conflict": {
    title: "The saved plan no longer matches the checksum you reviewed",
    detail: "Reload the run page and review the plan again before you apply it.",
  },
  "run-execution-conflict": {
    title: "This run cannot start another stage",
    detail:
      "It has already applied, or another stage is queued or running. Plan again to create a new run.",
  },
  forbidden: {
    title: "Only the actor that started this run, or an administrator, can do this",
    detail: "Ask the actor that started this run, or an administrator, to continue.",
  },
  "artifact-expired": {
    title: "The saved plan has expired",
    detail: "Plan again to create a new plan.",
  },
  "plan-unavailable": {
    title: "The Sync service cannot read the saved plan",
    detail: "Try again. If it fails again, plan again.",
  },
  "no-active-execution": {
    title: "No stage of this run is running yet",
    detail: "There is nothing to cancel. Try again in a few seconds.",
  },
  "execution-terminal": {
    title: "This stage already finished",
    detail: "There is nothing left to cancel.",
  },
  "cancellation-ambiguous": {
    title: "The write may already have reached the destination",
    detail: "Inspect the destination branch, then plan again.",
  },
  "idempotency-conflict": {
    title: "This request was already sent with other values",
    detail: "Close the dialog and open it again.",
  },
  "orchestration-unavailable": {
    title: "The Sync service recorded the run, but Prefect did not confirm that it received it",
    detail:
      "Press the button again without changing the form. The same request is sent and no second run is created.",
  },
  "secret-parameter-refused": {
    title: "A value in this form matches a secret the Sync service holds",
    detail: "Change the branch name or the reason, then try again.",
  },
  "configuration-version-invalid": {
    title: "The registered package for this version cannot be read",
    detail: "Register a new version of the configuration.",
  },
  "request-invalid": { title: "The Sync service refused the values in this form" },
};

const capitalize = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);

/** A 4xx answer will not change on retry, except a request timeout (408) or a rate limit (429). */
const retryableStatus = (status: number) =>
  !(status >= 400 && status < 500 && status !== 408 && status !== 429);

function noAnswerDetail(): string {
  return import.meta.env.DEV
    ? "Check that the Sync service is running and that INFRAHUB_SYNC_API_URL points to it, then try again."
    : "Check that the Sync service is running, then try again.";
}

/** A sentence for each failure a page can meet: no answer, a refused token, a missing record or a refusal. */
export function describeError(error: unknown): DescribedError {
  if (isEnvelope(error)) {
    const { code, message, status } = error.error;
    const reason = error.error.reason || undefined;
    const base = {
      status,
      code,
      reason,
      runId: error.error.run_id ?? undefined,
      codeLine: [code, reason !== code && reason, status && `HTTP ${status}`]
        .filter(Boolean)
        .join(" · "),
      kind: "envelope" as const,
    };
    const notFound = (title: string, detail: string): DescribedError => ({
      ...base,
      title,
      detail,
      retryable: false,
      notFound: true,
    });
    if (status === 404 && reason === "configuration-not-found") {
      return notFound(
        "No configuration with this ID",
        "Check the configuration ID in the address. The Configurations page lists every registered configuration."
      );
    }
    if (
      status === 404 &&
      (reason === "configuration-version-not-found" || code === "configuration-version-not-found")
    ) {
      return notFound(
        "No such version of this configuration",
        "The Sync API has no version with this number for this configuration. The Versions tab lists the registered versions."
      );
    }
    if (status === 404 && code === "run-not-found") {
      return notFound(
        "No run with this ID",
        "Check the run ID in the address. The Runs page lists every run."
      );
    }
    if (status === 401) {
      return {
        ...base,
        title: "The Sync API refused the token",
        detail: import.meta.env.DEV
          ? "Set INFRAHUB_SYNC_API_TOKEN to a bearer token the Sync API accepts, then restart the development server."
          : "The Sync API did not accept the token sent with requests from this page.",
        retryable: false,
        notFound: false,
      };
    }
    const action = Object.hasOwn(ACTION_ERRORS, code) ? ACTION_ERRORS[code] : undefined;
    return {
      ...base,
      title: action?.title ?? capitalize(message),
      detail: code === "request-invalid" ? capitalize(message) : action?.detail,
      retryable: retryableStatus(status),
      notFound: false,
    };
  }
  if (error instanceof SyncApiUnavailable) {
    return {
      title: "The Sync API did not answer",
      detail: noAnswerDetail(),
      codeLine: `HTTP ${error.status} from the web server`,
      retryable: true,
      notFound: false,
      kind: "no-answer",
    };
  }
  if (isTimeout(error)) {
    return {
      title: "The Sync API did not answer within 20 seconds",
      detail: "Check that the Sync service is running, then try again.",
      retryable: true,
      notFound: false,
      kind: "no-answer",
    };
  }
  if (error instanceof TypeError || typeof error === "string") {
    return {
      title: "The Sync API did not answer",
      detail: noAnswerDetail(),
      retryable: true,
      notFound: false,
      kind: "no-answer",
    };
  }
  return {
    title: "The page could not read the answer from the Sync API",
    codeLine: String(error),
    retryable: true,
    notFound: false,
    kind: "unknown",
  };
}
