import { infiniteQueryOptions, queryOptions } from "@tanstack/react-query";

import { cronFields } from "#/domain/cadence";
import { DEFAULT_HISTORY_EVENT_TYPES, untilAfter } from "#/domain/schedule-events";

import { $api, fetchClient } from "./client";
import { isEnvelope, isTimeout, SyncApiUnavailable } from "./errors";
import type {
  ConfigurationSummary,
  ConfigurationVersion,
  Run,
  RunLogLine,
  RunResource,
} from "./types";
import type { components } from "./types.generated";

type EventPage = components["schemas"]["EventPage"];

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

/** `GET /configs?name=`: the configurations with this name, newest first. Names are not unique. */
export const configsNamedQuery = (name: string) =>
  $api.queryOptions("get", "/configs", { params: { query: { name, limit: "1" } } });

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

/**
 * `GET /configs/{config_id}/versions?limit=1`: the newest version of one configuration, with the
 * account that registered it, which `GET /configs/{config_id}/versions/{registry_version}` does
 * not return.
 */
export const newestVersionQuery = (configId: string) =>
  $api.queryOptions("get", "/configs/{config_id}/versions", {
    params: { path: { config_id: configId }, query: { limit: "1" } },
  });

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
 * `GET /runs` 50 runs at a time, newest first, optionally for one configuration and one schedule.
 * With older pages loaded, every page is read again on each refresh, so the interval grows.
 */
export const runPagesQuery = (config?: string, schedule?: string) =>
  infiniteQueryOptions({
    queryKey: ["get", "/runs", "pages", { config, schedule }],
    initialPageParam: null as string | null,
    queryFn: async ({ pageParam, signal }) => {
      const { data, error } = await fetchClient.GET("/runs", {
        params: {
          query: {
            limit: "50",
            ...(pageParam ? { cursor: pageParam } : {}),
            ...(config ? { config_id: config } : {}),
            ...(schedule ? { schedule_id: schedule } : {}),
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

/** The most left-out records a dialog lists; the count of the rest is shown instead. */
export const LEFT_OUT_SHOWN = 20;

/**
 * The first records the saved plan leaves out, for the plan with this checksum. A saved plan never
 * changes, so the answer is kept for the session; a 409 means the run holds another plan.
 */
export const planLeftOutQuery = (runId: string, checksum: string) =>
  $api.queryOptions(
    "get",
    "/runs/{run_id}/plan/left-out",
    {
      params: {
        path: { run_id: runId },
        query: { expected_checksum: checksum, limit: String(LEFT_OUT_SHOWN) },
      },
    },
    { staleTime: Number.POSITIVE_INFINITY }
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

/* ---------- schedules ---------- */

/** The filters of the schedules list. Without `state`, the Sync API leaves deleted schedules out. */
export interface ScheduleFilters {
  state?: string;
  config?: string;
}

/**
 * `GET /schedules` 200 schedules at a time, the largest page the Sync API allows, optionally in one
 * state or for one configuration. With more than one page loaded, every page is read again on each
 * refresh, so the interval grows.
 */
export const schedulePagesQuery = ({ state, config }: ScheduleFilters = {}) =>
  infiniteQueryOptions({
    queryKey: ["get", "/schedules", "pages", { state, config }],
    initialPageParam: null as string | null,
    queryFn: async ({ pageParam, signal }) => {
      const { data, error } = await fetchClient.GET("/schedules", {
        params: {
          query: {
            limit: "200",
            ...(pageParam ? { cursor: pageParam } : {}),
            ...(state ? { state } : {}),
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

export const scheduleQuery = (scheduleId: string) =>
  $api.queryOptions(
    "get",
    "/schedules/{schedule_id}",
    { params: { path: { schedule_id: scheduleId } } },
    { refetchInterval: poll(LIST_REFRESH_MS) }
  );

/** The schedule of one configuration, which has at most one schedule that is not deleted. */
export const configScheduleQuery = (configId: string) =>
  $api.queryOptions(
    "get",
    "/schedules",
    { params: { query: { config_id: configId, limit: "1" } } },
    { refetchInterval: poll(LIST_REFRESH_MS) }
  );

/**
 * The next start times of a cron expression in a time zone, as the scheduler computes them. The
 * values travel in a JSON body so that they never reach an access log. A refusal is shown as it
 * is, so it is not retried; nothing is asked while the expression does not have five fields or the
 * time zone is empty (the form asks for a time zone itself).
 */
export const nextStartsQuery = (cron: string, timezone: string, count: number) =>
  queryOptions({
    queryKey: ["post", "/schedules/next-starts", { cron, timezone, count }] as const,
    queryFn: async ({ signal }) => {
      const { data, error } = await fetchClient.POST("/schedules/next-starts", {
        body: { cron, timezone, count },
        signal,
      });
      if (error) throw error;
      return data;
    },
    staleTime: 60_000,
    retry: false,
    enabled: cronFields(cron) !== null && timezone.trim() !== "",
  });

/** The number of history entries read at a time. */
const EVENT_PAGE_LIMIT = 50;

/**
 * One page of a schedule's history, with the `until` every older page of the list uses; `null`
 * when the first page is empty.
 */
export type ScheduleEventPage = EventPage & { until: string | null };

type ScheduleEventPageParam = { offset: number; until: string | null };

/** One read of a schedule's history, newest first; without `until`, the newest events. */
async function readScheduleEvents(
  scheduleId: string,
  includeAll: boolean,
  { offset, until, signal }: { offset: number; until?: string | null; signal: AbortSignal }
): Promise<EventPage> {
  const { data, error } = await fetchClient.GET("/events", {
    params: {
      query: {
        related: [`schedule:${scheduleId}`],
        order: "desc",
        ...(until ? { until } : {}),
        limit: String(EVENT_PAGE_LIMIT),
        offset: String(offset),
        ...(includeAll ? {} : { event_type: [...DEFAULT_HISTORY_EVENT_TYPES] }),
      },
    },
    signal,
  });
  if (error) throw error;
  return data;
}

/**
 * The history of a schedule, newest first, 50 entries at a time. The first page is read without
 * `until`; every older page uses `until` set to one millisecond after the newest event of the first
 * page (`untilAfter`), so events recorded later do not shift the offsets of older pages. The time
 * comes from the events the Sync API returned, not from the browser's clock. Unless `includeAll`,
 * only the schedule event types are asked for. Events recorded after `until` come from
 * `scheduleNewestEventsQuery`.
 */
export const scheduleEventsQuery = (
  scheduleId: string,
  { includeAll = false }: { includeAll?: boolean } = {}
) =>
  infiniteQueryOptions({
    queryKey: ["get", "/events", "schedule", { scheduleId, includeAll }],
    initialPageParam: { offset: 0, until: null } as ScheduleEventPageParam,
    queryFn: async ({ pageParam, signal }): Promise<ScheduleEventPage> => {
      const data = await readScheduleEvents(scheduleId, includeAll, {
        offset: pageParam.offset,
        until: pageParam.until,
        signal,
      });
      return { ...data, until: pageParam.until ?? untilAfter(data.items[0]) };
    },
    // The offset counts every event the pages returned, repeats included, so no event is skipped.
    getNextPageParam: (last, pages): ScheduleEventPageParam | undefined =>
      last.has_more
        ? {
            offset: pages.reduce((count, page) => count + page.items.length, 0),
            until: pages[0].until,
          }
        : undefined,
  });

/**
 * The newest 50 events of a schedule, read without `until` every 10 seconds (contracts/http-api.md),
 * so the history can add the events recorded after its first page above the list.
 */
export const scheduleNewestEventsQuery = (
  scheduleId: string,
  { includeAll = false }: { includeAll?: boolean } = {}
) =>
  queryOptions({
    queryKey: ["get", "/events", "schedule-newest", { scheduleId, includeAll }],
    queryFn: ({ signal }) => readScheduleEvents(scheduleId, includeAll, { offset: 0, signal }),
    refetchInterval: poll(LIST_REFRESH_MS),
  });

/* ---------- accounts and tokens ---------- */

/** The states `GET /accounts` filters on. */
export type AccountState = "active" | "disabled" | "all";

/**
 * The API tokens of the signed-in account, oldest first. The account lists poll as the other lists
 * do, so a change made by another administrator or in another tab shows up.
 */
export const myTokensQuery = () =>
  $api.queryOptions("get", "/me/tokens", {}, { refetchInterval: poll(LIST_REFRESH_MS) });

/** Every account in a state, by name. Only an administrator can read it. */
export const accountsQuery = (state: AccountState) =>
  $api.queryOptions(
    "get",
    "/accounts",
    { params: { query: { state } } },
    { refetchInterval: poll(LIST_REFRESH_MS) }
  );

/** The API tokens of another account, oldest first. Only an administrator can read them. */
export const accountTokensQuery = (name: string) =>
  $api.queryOptions(
    "get",
    "/accounts/{name}/tokens",
    { params: { path: { name } } },
    { refetchInterval: poll(LIST_REFRESH_MS) }
  );

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

/** A sentence for each failure a page can meet: no answer, a refused session, a missing record or a refusal. */
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
        title: "The Sync API refused the session",
        detail: "The session has ended. Sign in again.",
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

/** The shortest and the longest password the Sync API accepts, in characters. */
export const PASSWORD_MIN = 12;
export const PASSWORD_MAX = 1024;

/** Refusals of an account or token action, in words. The keys are the Sync API error codes. */
const ACCOUNT_ERRORS: Record<string, { title: string; detail?: string }> = {
  forbidden: {
    title: "Only an administrator can manage accounts",
    detail: "Ask an administrator to make this change.",
  },
  "current-password-refused": {
    title: "The current password is not accepted",
    detail:
      "Type the password you signed in with. After repeated failures, the account is refused for a while.",
  },
  "password-too-short": {
    title: "The new password is too short",
    detail: `Use at least ${PASSWORD_MIN} characters.`,
  },
  "password-too-long": {
    title: "The new password is too long",
    detail: `Use at most ${PASSWORD_MAX.toLocaleString("en")} characters.`,
  },
  "password-invalid": {
    title: "The new password contains a character that cannot be stored",
    detail: "Remove the character, then try again.",
  },
  "sign-in-busy": {
    title: "The Sync service is checking too many passwords",
    detail: "Try again in a moment.",
  },
  "environment-managed-account": {
    title: "The deployment's environment defines the admin account",
    detail:
      "The environment of the Sync service defines this account, which is always an active administrator. Change its password or its token there.",
  },
  "account-name-taken": {
    title: "An account with this name exists",
    detail: "Names are compared without regard to letter case. Choose another name.",
  },
  "account-name-invalid": {
    title: "This account name is not allowed",
    detail:
      "Use a name without spaces. The names admin, unknown and unauthenticated are reserved, and a name cannot start with acct_ or contain a token prefix.",
  },
  "account-not-found": {
    title: "No account with this name",
    detail: "Another administrator may have deleted it. The list shows the accounts that exist.",
  },
  "token-name-taken": {
    title: "You already have a token with this name",
    detail: "Choose another name.",
  },
  "token-not-found": {
    title: "This token no longer exists",
    detail: "It may have been deleted already. The list shows the tokens that exist.",
  },
  "expiry-in-the-past": {
    title: "The expiry date is in the past",
    detail: "Choose a date and a time in the future, or leave the expiry empty.",
  },
  "secret-parameter-refused": {
    title: "A value in this form looks like a token or a secret",
    detail: "Change the name or the reason, then try again.",
  },
};

/** `describeError`, with the refusals of the account and token routes said for those pages. */
export function describeAccountError(error: unknown): DescribedError {
  const described = describeError(error);
  const code = described.code;
  if (described.kind !== "envelope" || !code || !Object.hasOwn(ACCOUNT_ERRORS, code)) {
    return described;
  }
  const words = ACCOUNT_ERRORS[code];
  return { ...described, title: words.title, detail: words.detail, notFound: false };
}

/** The refusals whose detail is the Sync API message, because it names the value or the reason. */
const SCHEDULE_MESSAGE_DETAIL = new Set([
  "schedule-cron-invalid",
  "schedule-timezone-invalid",
  "schedule-stopped",
  "request-invalid",
]);

/** Refusals of a schedule read or action, in words. The keys are the Sync API error codes. */
const SCHEDULE_ERRORS: Record<string, { title: string; detail?: string }> = {
  forbidden: {
    title: "Only the owner of this schedule or an administrator can do this",
    detail: "Ask the owner of this schedule, or an administrator, to make this change.",
  },
  "schedule-not-found": {
    title: "Schedule not found",
    detail: "Check the schedule ID in the address. The Schedules page lists every schedule.",
  },
  "schedule-deleted": {
    title: "This schedule is deleted",
    detail: "A deleted schedule accepts no action. Its runs and its history stay available.",
  },
  "schedule-stopped": { title: "This schedule cannot be resumed yet" },
  "schedule-changed": {
    title: "Another change was saved at the same time. Save again.",
    detail: "The values you entered are kept.",
  },
  "schedule-name-in-use": {
    title: "A schedule with this name exists",
    detail: "Choose another name.",
  },
  // Not the Sync API message: it holds the identifier of the existing schedule (FR-007, SC-006).
  "schedule-exists-for-configuration": {
    title: "This configuration already has a schedule",
    detail:
      "A configuration has one schedule that is not deleted. Change or delete that schedule from the Schedules page.",
  },
  "approval-required-for-schedule": {
    title: "This Sync service requires an approved plan before any write",
    detail: "A schedule cannot write to the default branch here. Choose Branch review.",
  },
  "schedule-branch-declared": {
    title: "The configuration version declares its own destination branch",
    detail:
      "A run with branch review would write to that branch without a review. Choose Default branch.",
  },
  "schedule-left-out-not-applicable": {
    title: "Writing when records are left out applies only to the default branch",
    detail:
      "A run with branch review always writes the remaining records, and its proposed change states how many were left out.",
  },
  "schedule-cron-invalid": { title: "The Sync service refused the cron expression" },
  "schedule-timezone-invalid": { title: "The Sync service does not know this time zone" },
  "configuration-version-not-found": {
    title: "No such version of this configuration",
    detail: "Choose a registered version of the configuration.",
  },
  "orchestration-unavailable": {
    title: "The Sync service saved the schedule, but the task manager did not confirm the change",
    detail: "Open the schedule page to check its state.",
  },
  "secret-parameter-refused": {
    title: "A value in this form matches a secret the Sync service holds",
    detail: "Change the value that holds the secret, then try again.",
  },
  "request-invalid": { title: "The Sync service refused the values in this form" },
};

/**
 * `describeError`, with the refusals of the schedule routes said for the schedule pages. For
 * `secret-parameter-refused` the Sync API message is never shown, so no value entered is repeated.
 */
export function describeScheduleError(error: unknown): DescribedError {
  const described = describeError(error);
  const code = described.code;
  if (described.kind !== "envelope" || !code || !Object.hasOwn(SCHEDULE_ERRORS, code)) {
    return described;
  }
  const words = SCHEDULE_ERRORS[code];
  const message = isEnvelope(error) ? capitalize(error.error.message) : undefined;
  return {
    ...described,
    title: words.title,
    detail: SCHEDULE_MESSAGE_DETAIL.has(code) ? message : words.detail,
    notFound: code === "schedule-not-found",
  };
}
