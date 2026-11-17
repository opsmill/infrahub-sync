import { Button } from "@infrahub/ui";
import { type FormEvent, useId, useState } from "react";
import { cn } from "tailwind-variants";

import { fetchClient } from "#/api/client";
import { deleteToken, writeToken } from "#/api/token";
import { FOCUS } from "#/components/classes";
import { PageMessage } from "#/components/page";
import { useDocumentTitle } from "#/lib/title";

export const ANONYMOUS_ACTOR_SETTING = "INFRAHUB_SYNC_SERVICE_ANONYMOUS_ACTOR";
export const ANONYMOUS_ACCESS_DOCS =
  "https://docs.infrahub.app/sync/compose-deployment#open-the-web-interface";

type Check =
  | "idle"
  | "checking"
  | "refused"
  | "failed"
  | "unreachable"
  | "not-stored"
  | "not-forgotten";

const BLOCKED_STORAGE = "This browser blocks site data for this address, so";

/** The message each state of the check shows below the field. */
const CHECK_MESSAGES: Partial<Record<Check, string>> = {
  refused:
    "The Sync API refused this token. Check that it is a token from INFRAHUB_SYNC_SERVICE_BEARER_TOKENS.",
  failed: "The Sync API could not check this token because of an error on its side. Try again.",
  unreachable: "The Sync API did not answer. Try again.",
  "not-stored": `${BLOCKED_STORAGE} it cannot keep the token. Allow site data for this address, then try again.`,
  "not-forgotten": `${BLOCKED_STORAGE} it could not forget the token.`,
};

/**
 * Shown in place of the page when the Sync API refuses this browser's requests. It explains the
 * refusal and checks a pasted token with the Sync API before it stores the token.
 */
export function TokenGate({
  storedTokenRefused,
  onChange,
}: {
  /** True when the refused requests carried a stored token. */
  storedTokenRefused: boolean;
  /** Called after a token is stored or forgotten, so that the page reloads its data. */
  onChange: () => void;
}) {
  const fieldId = useId();
  const messageId = useId();
  const [token, setToken] = useState("");
  const [check, setCheck] = useState<Check>("idle");
  useDocumentTitle(storedTokenRefused ? "Token refused" : "Token required");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const candidate = token.trim();
    if (!candidate) return;
    setCheck("checking");
    try {
      const { response } = await fetchClient.GET("/me", {
        headers: { Authorization: `Bearer ${candidate}` },
      });
      if (!response.ok) {
        setCheck(response.status === 401 ? "refused" : "failed");
        return;
      }
    } catch {
      setCheck("unreachable");
      return;
    }
    if (!writeToken(candidate)) {
      setCheck("not-stored");
      return;
    }
    setToken("");
    setCheck("idle");
    onChange();
  }

  function forget() {
    if (!deleteToken()) {
      setCheck("not-forgotten");
      return;
    }
    onChange();
  }

  const message = CHECK_MESSAGES[check];

  return (
    <PageMessage
      title={storedTokenRefused ? "The Sync API refused the stored token" : "A token is required"}
      alert={storedTokenRefused}
    >
      {storedTokenRefused ? (
        <p>
          This browser holds a token that the Sync API no longer accepts. Paste a new token to
          replace it, or forget it.
        </p>
      ) : (
        <p>The Sync API refuses requests without a token from this address.</p>
      )}
      <p>
        An operator can let requests without a token act as one actor with{" "}
        <code className="font-mono text-xs">{ANONYMOUS_ACTOR_SETTING}</code>. See{" "}
        <a
          href={ANONYMOUS_ACCESS_DOCS}
          target="_blank"
          rel="noreferrer"
          className={cn("font-medium text-accent underline underline-offset-2", FOCUS)}
        >
          Open the web interface
        </a>
        .
      </p>
      <form onSubmit={submit} className="mt-2 flex w-full flex-col gap-2" noValidate>
        <label htmlFor={fieldId} className="font-medium text-foreground">
          {storedTokenRefused ? "New bearer token" : "Bearer token"}
        </label>
        <input
          id={fieldId}
          type="password"
          value={token}
          onChange={(e) => {
            setToken(e.target.value);
            if (check !== "checking") setCheck("idle");
          }}
          autoComplete="off"
          spellCheck={false}
          aria-describedby={message ? messageId : undefined}
          aria-invalid={check === "refused" || undefined}
          className="flex min-h-10 w-full max-w-md items-center rounded-xl border border-input-border bg-input px-3 py-2 text-base shadow-input transition-[border-color,box-shadow] duration-150 ease-out focus-visible:border-ring focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring-halo motion-reduce:transition-none md:text-sm"
        />
        {message && (
          <p id={messageId} role="alert" className="text-danger">
            {message}
          </p>
        )}
        <p className="text-subtle text-xs">
          This browser keeps the token for this address until you select Forget token.
        </p>
        <div className="mt-1 flex flex-wrap gap-2">
          <Button
            type="submit"
            variant="primary"
            size="sm"
            isDisabled={check === "checking" || !token.trim()}
          >
            {storedTokenRefused ? "Replace" : "Use token"}
          </Button>
          {storedTokenRefused && (
            <Button type="button" variant="outline" size="sm" onPress={forget}>
              Forget token
            </Button>
          )}
        </div>
      </form>
    </PageMessage>
  );
}
