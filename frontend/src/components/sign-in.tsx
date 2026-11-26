import { Button } from "@infrahub/ui";
import { type FormEvent, useEffect, useId, useRef, useState } from "react";

import { type SignInResult, signIn } from "#/api/client";
import { Mark } from "#/components/mark";
import { Notice } from "#/components/page";
import { Field, INPUT } from "#/features/runs/form";
import { useDocumentTitle } from "#/lib/title";

/** One message for every refused sign-in, so the page does not tell which part was wrong. */
const MESSAGES: Record<Exclude<SignInResult, "signed-in">, string> = {
  refused: "The account name or the password is not accepted.",
  busy: "The Sync service is checking too many sign-ins. Try again in a moment.",
  "no-answer": "The Sync service did not answer. Check that it is running, then try again.",
};

/**
 * Shown instead of every page while this tab holds no session. A successful sign-in keeps the
 * session token, and the page at the current address opens.
 */
export function SignIn() {
  useDocumentTitle("Sign in");
  const id = useId();
  const nameId = `${id}-name`;
  const passwordId = `${id}-password`;
  const errorId = `${id}-error`;
  const passwordRef = useRef<HTMLInputElement>(null);
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string>();

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!name || !password || pending) return;
    setPending(true);
    setError(undefined);
    const result = await signIn(name, password);
    // The password is not kept after the request, whatever the answer.
    setPassword("");
    setPending(false);
    if (result === "signed-in") return;
    setError(MESSAGES[result]);
  }

  // Only a refusal says the values are wrong; a busy or silent service does not.
  const refused = error === MESSAGES.refused;

  // After a refusal the password field is empty and enabled again; focus returns to it.
  useEffect(() => {
    if (error) passwordRef.current?.focus();
  }, [error]);

  return (
    // Laid out like the Infrahub login page: one centered column, a quarter of the window from the top.
    <div className="min-h-dvh overflow-auto bg-background px-4 py-[max(2rem,25vh)] text-foreground text-sm antialiased">
      <main className="mx-auto flex w-full max-w-sm flex-col items-center gap-6 outline-none">
        <Mark className="size-12" />
        <div className="flex flex-col items-center gap-2 text-center">
          <h1 className="text-balance font-semibold text-xl tracking-tight">
            Sign in to Infrahub Sync
          </h1>
          <p className="text-pretty text-foreground-muted">
            Sign in with the name and the password of your account. The session is kept for this
            browser tab only.
          </p>
        </div>
        <section className="w-full rounded-xl border bg-content p-6 shadow-sm">
          <form onSubmit={submit} className="flex w-full flex-col gap-4">
            <Field id={nameId} label="Account name">
              <input
                id={nameId}
                type="text"
                name="username"
                autoComplete="username"
                autoCapitalize="none"
                spellCheck={false}
                required
                className={INPUT}
                value={name}
                onChange={(event) => setName(event.target.value)}
                disabled={pending}
                aria-describedby={error ? errorId : undefined}
                aria-invalid={refused ? true : undefined}
              />
            </Field>
            <Field id={passwordId} label="Password">
              <input
                ref={passwordRef}
                id={passwordId}
                type="password"
                name="password"
                autoComplete="current-password"
                required
                className={INPUT}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                disabled={pending}
                aria-describedby={error ? errorId : undefined}
                aria-invalid={refused ? true : undefined}
              />
            </Field>
            {error && (
              <Notice tone="danger" role="alert" id={errorId}>
                <p>{error}</p>
              </Notice>
            )}
            <Button type="submit" variant="primary" className="w-full" isDisabled={pending}>
              Sign in
            </Button>
          </form>
        </section>
      </main>
    </div>
  );
}
