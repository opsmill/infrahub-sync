import { Button } from "@infrahub/ui";
import { type FormEvent, useId, useState } from "react";

import { fetchClient } from "#/api/client";
import { SyncApiUnavailable } from "#/api/errors";
import { bearer, normalizeToken, setToken } from "#/api/token";
import { Notice } from "#/components/page";
import { Field, INPUT } from "#/features/runs/form";

const REFUSED = "The Sync API refused the token.";
const NO_ANSWER = "The Sync service did not answer. Check that it is running, then try again.";

/**
 * Checks a typed token with `GET /me` and keeps it only when the Sync API accepts it. The answer
 * of `/me` is never shown: the page does not name the token's actor.
 */
async function checkToken(token: string): Promise<"accepted" | "refused" | "no-answer"> {
  try {
    const { response } = await fetchClient.GET("/me", {
      headers: { Authorization: bearer(token) },
    });
    if (response.ok) return "accepted";
    return response.status === 401 ? "refused" : "no-answer";
  } catch (error) {
    // An error answer without a Sync API envelope is thrown by the client middleware.
    return error instanceof SyncApiUnavailable && error.status === 401 ? "refused" : "no-answer";
  }
}

/** Shown instead of every page while this tab holds no Sync API token. */
export function TokenEntry() {
  const id = useId();
  const [value, setValue] = useState("");
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string>();

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const token = normalizeToken(value);
    if (!token || checking) return;
    setChecking(true);
    setError(undefined);
    const result = await checkToken(token);
    setChecking(false);
    if (result === "accepted") setToken(token);
    else setError(result === "refused" ? REFUSED : NO_ANSWER);
  }

  return (
    <div className="min-h-dvh bg-background text-foreground text-sm antialiased">
      <main className="mx-auto max-w-[1200px] px-4 pb-24 outline-none">
        <section className="flex w-full min-w-0 max-w-md flex-col items-start gap-3 pt-8 pb-6">
          <h1 className="text-balance font-semibold text-2xl tracking-tight">
            Enter a Sync API token
          </h1>
          <p className="text-pretty text-foreground-muted">
            The web interface sends this token with every request to the Sync API. It is kept for
            this browser tab only.
          </p>
          <form onSubmit={submit} className="mt-2 flex w-full flex-col gap-4">
            <Field id={id} label="Sync API token">
              <input
                id={id}
                type="password"
                autoComplete="off"
                spellCheck={false}
                className={INPUT}
                value={value}
                onChange={(event) => setValue(event.target.value)}
                disabled={checking}
                aria-describedby={error ? `${id}-error` : undefined}
              />
            </Field>
            {error && (
              <Notice tone="danger" role="alert" id={`${id}-error`}>
                <p>{error}</p>
              </Notice>
            )}
            <div>
              <Button type="submit" variant="primary" size="sm" isDisabled={checking}>
                Continue
              </Button>
            </div>
          </form>
        </section>
      </main>
    </div>
  );
}
