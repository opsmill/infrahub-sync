import { Button } from "@infrahub/ui";
import { type FormEvent, useEffect, useId, useRef, useState } from "react";

import { type SignInResult, signIn } from "#/api/client";
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
    <div className="min-h-dvh bg-background text-foreground text-sm antialiased">
      <main className="mx-auto max-w-[1200px] px-4 pb-24 outline-none">
        <section className="flex w-full min-w-0 max-w-md flex-col items-start gap-3 pt-8 pb-6">
          <h1 className="text-balance font-semibold text-2xl tracking-tight">
            Sign in to Infrahub Sync
          </h1>
          <p className="text-pretty text-foreground-muted">
            Sign in with the name and the password of your account. The session is kept for this
            browser tab only.
          </p>
          <form onSubmit={submit} className="mt-2 flex w-full flex-col gap-4">
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
            <div>
              <Button type="submit" variant="primary" size="sm" isDisabled={pending}>
                Sign in
              </Button>
            </div>
          </form>
        </section>
      </main>
    </div>
  );
}
