import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { $api, fetchClient } from "./client";
import { scheduleQuery } from "./queries";
import type { Schedule } from "./types";

/**
 * One idempotency key per intent. A dialog keeps its key while it is open, so pressing the
 * button again after a lost response retries the same request instead of creating a second
 * run. `renew` starts a new intent.
 */
export function useIdempotencyKey(): [string, () => void] {
  const [key, setKey] = useState(() => crypto.randomUUID());
  return [key, () => setKey(crypto.randomUUID())];
}

/**
 * After any write, every list and the run itself are read again, whatever the answer: a refusal
 * such as `account-not-found` or a conflict means the lists on the page are out of date too.
 */
function useInvalidate() {
  const client = useQueryClient();
  return () => client.invalidateQueries();
}

export function useCreateRun() {
  const invalidate = useInvalidate();
  return $api.useMutation("post", "/runs", { onSettled: invalidate });
}

export function useVerifyRun() {
  const invalidate = useInvalidate();
  return $api.useMutation("post", "/runs/{run_id}/verify", { onSettled: invalidate });
}

export function useApproveRun() {
  const invalidate = useInvalidate();
  return $api.useMutation("post", "/runs/{run_id}/approve", { onSettled: invalidate });
}

export function useApplyRun() {
  const invalidate = useInvalidate();
  return $api.useMutation("post", "/runs/{run_id}/apply", { onSettled: invalidate });
}

export function useResumeRun() {
  const invalidate = useInvalidate();
  return $api.useMutation("post", "/runs/{run_id}/resume", { onSettled: invalidate });
}

export function useCancelRun() {
  const invalidate = useInvalidate();
  return $api.useMutation("post", "/runs/{run_id}/cancel", { onSettled: invalidate });
}

/* ---------- schedules ---------- */

/**
 * After a success, the returned schedule is written into the cache of its page before the
 * invalidation reads it again, so the page shows the state the action produced even when a poll
 * that started before the action answers later.
 */
function useStoreSchedule() {
  const client = useQueryClient();
  return (schedule: Schedule) =>
    client.setQueryData(scheduleQuery(schedule.schedule_id).queryKey, schedule);
}

export function useCreateSchedule() {
  const invalidate = useInvalidate();
  const store = useStoreSchedule();
  return $api.useMutation("post", "/schedules", { onSuccess: store, onSettled: invalidate });
}

export function useChangeSchedule() {
  const invalidate = useInvalidate();
  const store = useStoreSchedule();
  return $api.useMutation("post", "/schedules/{schedule_id}/change", {
    onSuccess: store,
    onSettled: invalidate,
  });
}

export function usePauseSchedule() {
  const invalidate = useInvalidate();
  const store = useStoreSchedule();
  return $api.useMutation("post", "/schedules/{schedule_id}/pause", {
    onSuccess: store,
    onSettled: invalidate,
  });
}

export function useResumeSchedule() {
  const invalidate = useInvalidate();
  const store = useStoreSchedule();
  return $api.useMutation("post", "/schedules/{schedule_id}/resume", {
    onSuccess: store,
    onSettled: invalidate,
  });
}

export function useDeleteSchedule() {
  const invalidate = useInvalidate();
  const store = useStoreSchedule();
  return $api.useMutation("post", "/schedules/{schedule_id}/delete", {
    onSuccess: store,
    onSettled: invalidate,
  });
}

export function useTakeOverSchedule() {
  const invalidate = useInvalidate();
  const store = useStoreSchedule();
  return $api.useMutation("post", "/schedules/{schedule_id}/take-over", {
    onSuccess: store,
    onSettled: invalidate,
  });
}

/* ---------- accounts and tokens ---------- */

/** After a change to an account or a token, the lists of accounts and tokens are read again. */
export function useInvalidateAccounts() {
  return useInvalidate();
}

export function useUpdateAccount() {
  const invalidate = useInvalidate();
  return $api.useMutation("patch", "/accounts/{name}", { onSettled: invalidate });
}

export function useUnlockAccount() {
  const invalidate = useInvalidate();
  return $api.useMutation("post", "/accounts/{name}/unlock", { onSettled: invalidate });
}

export function useDeleteAccount() {
  const invalidate = useInvalidate();
  return $api.useMutation("delete", "/accounts/{name}", { onSettled: invalidate });
}

export function useDeleteMyToken() {
  const invalidate = useInvalidate();
  return $api.useMutation("delete", "/me/tokens/{token_id}", { onSettled: invalidate });
}

export function useDeleteAccountToken() {
  const invalidate = useInvalidate();
  return $api.useMutation("delete", "/accounts/{name}/tokens/{token_id}", {
    onSettled: invalidate,
  });
}

/*
 * The four requests below carry a password or answer with a token value. They call the fetch
 * client directly instead of `useMutation`, because a mutation keeps its body and its answer in
 * the query client, where the developer tools display them. The caller keeps nothing after the
 * answer, and invalidates the lists with `useInvalidateAccounts` after any answer.
 */

/** The answer to a request that carries a secret: its data, or the error to describe. */
export type SecretResult<T> = { ok: true; data: T } | { ok: false; error: unknown };

async function sendSecret<T>(
  request: () => Promise<{ data?: T; error?: unknown; response: Response }>
): Promise<SecretResult<T>> {
  try {
    const { data, error, response } = await request();
    if (response.ok) return { ok: true, data: data as T };
    return { ok: false, error };
  } catch (error) {
    // A network failure, or an answer without a Sync API envelope.
    return { ok: false, error };
  }
}

const optional = (value: string) => value.trim() || null;

/** `PUT /me/password`. The other sessions of the account end; its API tokens keep working. */
export const changeMyPassword = (currentPassword: string, newPassword: string) =>
  sendSecret(() =>
    fetchClient.PUT("/me/password", {
      body: { current_password: currentPassword, new_password: newPassword },
    })
  );

/** `POST /me/tokens`. The answer holds the token value, which the Sync API returns only here. */
export const createMyToken = (name: string, expiresAt: string | null, reason: string) =>
  sendSecret(() =>
    fetchClient.POST("/me/tokens", {
      body: { name: name.trim(), expires_at: expiresAt, reason: optional(reason) },
    })
  );

/** `POST /accounts`, for an administrator. */
export const createAccount = (
  name: string,
  password: string,
  administrator: boolean,
  reason: string
) =>
  sendSecret(() =>
    fetchClient.POST("/accounts", {
      body: { name: name.trim(), password, administrator, reason: optional(reason) },
    })
  );

/** `PUT /accounts/{name}/password`, for an administrator. Every session of the account ends. */
export const setAccountPassword = (name: string, newPassword: string, reason: string) =>
  sendSecret(() =>
    fetchClient.PUT("/accounts/{name}/password", {
      params: { path: { name } },
      body: { new_password: newPassword, reason: optional(reason) },
    })
  );

/* ---------- stored credentials ---------- */

/** After a change to a credential, the list of credentials is read again. */
export function useInvalidateCredentials() {
  return useInvalidate();
}

/** `PATCH /credentials/{name}`, for an administrator. The revision of the credential stays. */
export function useDescribeCredential() {
  const invalidate = useInvalidate();
  return $api.useMutation("patch", "/credentials/{name}", { onSettled: invalidate });
}

/**
 * `DELETE /credentials/{name}`, for an administrator. It cannot be reversed. The Sync API answers
 * `409 credential-in-use` while a configuration or a schedule names the credential;
 * `credentialInUse` reads which ones from that error.
 */
export function useDeleteCredential() {
  const invalidate = useInvalidate();
  return $api.useMutation("delete", "/credentials/{name}", { onSettled: invalidate });
}

/**
 * The fields of a credential value. The Sync API accepts three sets: a token; a username and a
 * password; a username and a token. A field that is left out is not sent.
 */
export interface CredentialFields {
  username?: string;
  password?: string;
  token?: string;
}

/*
 * The two requests below carry a credential value, so they call the fetch client directly, as
 * the requests that carry a password do. The caller clears its form after any answer, and
 * invalidates the list with `useInvalidateCredentials`.
 */

/** `POST /credentials`, for an administrator. The answer holds the names of the fields, no value. */
export const createCredential = (
  name: string,
  description: string,
  fields: CredentialFields,
  idempotencyKey: string
) =>
  sendSecret(() =>
    fetchClient.POST("/credentials", {
      params: { header: { "Idempotency-Key": idempotencyKey } },
      body: { name: name.trim(), description: description.trim(), ...fields },
    })
  );

/**
 * `PUT /credentials/{name}/value`, for an administrator. The fields replace the whole stored set,
 * and the revision of the credential goes up by one.
 */
export const setCredentialValue = (
  name: string,
  fields: CredentialFields,
  idempotencyKey: string
) =>
  sendSecret(() =>
    fetchClient.PUT("/credentials/{name}/value", {
      params: { path: { name }, header: { "Idempotency-Key": idempotencyKey } },
      body: fields,
    })
  );

/** Checks a package that is not registered. It writes nothing, so nothing is read again. */
export function useCheckPackage() {
  return $api.useMutation("post", "/configs/validate");
}

/** After a registration, every configuration read is read again; runs are not affected. */
function useInvalidateConfigs() {
  const client = useQueryClient();
  return () =>
    client.invalidateQueries({
      predicate: (query) =>
        typeof query.queryKey[1] === "string" && query.queryKey[1].startsWith("/configs"),
    });
}

/**
 * Registers a package as the next version of a configuration. The caller passes the
 * `Idempotency-Key` header and keeps it for the whole review, so a retry after a lost answer
 * cannot register a second version.
 */
export function useCreateConfigVersion() {
  const invalidate = useInvalidateConfigs();
  return $api.useMutation("post", "/configs/{config_id}/versions", { onSuccess: invalidate });
}

/**
 * Registers a package as a new configuration with its first version. The caller passes the
 * `Idempotency-Key` header and keeps it for the whole review, so a retry after a lost answer
 * cannot register a second configuration.
 */
export function useRegisterConfig() {
  const invalidate = useInvalidateConfigs();
  return $api.useMutation("post", "/configs", { onSuccess: invalidate });
}
