import { Button } from "@infrahub/ui";
import { type ReactNode, useEffect, useRef, useState } from "react";

import { describeAccountError, PASSWORD_MAX, PASSWORD_MIN } from "#/api/queries";
import { Notice } from "#/components/page";
import { Field, INPUT } from "#/features/runs/form";
import { formatAbsolute } from "#/lib/time";

/** A date from the Sync API in the page's date format, or "never" when there is none. */
export function DateText({ iso, empty = "never" }: { iso?: string | null; empty?: string }) {
  if (!iso) return <span className="text-subtle">{empty}</span>;
  return <time dateTime={iso}>{formatAbsolute(iso)}</time>;
}

/** The refusal of an account or token request, next to the button that sent it. Never raw JSON. */
export function AccountError({ error, id }: { error: unknown; id?: string }) {
  if (!error) return null;
  const d = describeAccountError(error);
  return (
    <Notice tone="danger" role="alert" title={d.title} id={id}>
      {d.detail && <p className="text-pretty text-foreground-muted">{d.detail}</p>}
      {d.codeLine && <p className="font-mono text-subtle text-xs">{d.codeLine}</p>}
    </Notice>
  );
}

/** The number of characters in a password, as the Sync API counts them (code points). */
const lengthOf = (password: string) => [...password].length;

/**
 * Why a new password cannot be sent, checked before any request: its length, and that both
 * fields hold the same value. `undefined` when it can be sent.
 */
export function newPasswordProblem(password: string, repeat: string): string | undefined {
  const length = lengthOf(password);
  if (length < PASSWORD_MIN) {
    return `The new password has ${length} ${length === 1 ? "character" : "characters"}. Use at least ${PASSWORD_MIN}.`;
  }
  if (length > PASSWORD_MAX) {
    return `The new password has ${length.toLocaleString("en")} characters. Use at most ${PASSWORD_MAX.toLocaleString("en")}.`;
  }
  if (password !== repeat) return "The two new passwords are not the same. Type them again.";
  return undefined;
}

export const PASSWORD_RULE = `From ${PASSWORD_MIN} to ${PASSWORD_MAX.toLocaleString("en")} characters.`;

/** A new password asked twice, with the length rule under the first field. */
export function NewPasswordFields({
  idPrefix,
  password,
  repeat,
  onPassword,
  onRepeat,
  label = "New password",
  problemId,
  autoFocus,
}: {
  idPrefix: string;
  password: string;
  repeat: string;
  onPassword: (value: string) => void;
  onRepeat: (value: string) => void;
  label?: string;
  /** The id of the message that explains why the password was not sent, if one is shown. */
  problemId?: string;
  autoFocus?: boolean;
}) {
  const first = `${idPrefix}-new`;
  const second = `${idPrefix}-repeat`;
  return (
    <>
      <Field id={first} label={label} hint={PASSWORD_RULE}>
        <input
          id={first}
          type="password"
          autoComplete="new-password"
          className={INPUT}
          value={password}
          onChange={(e) => onPassword(e.target.value)}
          aria-describedby={[`${first}-hint`, problemId].filter(Boolean).join(" ")}
          aria-invalid={problemId ? true : undefined}
          // biome-ignore lint/a11y/noAutofocus: only when the caller asks, inside a modal that just opened.
          autoFocus={autoFocus}
        />
      </Field>
      <Field id={second} label={`Type the ${label.toLowerCase()} again`}>
        <input
          id={second}
          type="password"
          autoComplete="new-password"
          className={INPUT}
          value={repeat}
          onChange={(e) => onRepeat(e.target.value)}
          aria-describedby={problemId}
          aria-invalid={problemId ? true : undefined}
        />
      </Field>
    </>
  );
}

/** A message about a value the page refused before sending it. */
export function FormProblem({ id, children }: { id: string; children: string }) {
  return (
    <Notice tone="danger" role="alert" id={id}>
      <p>{children}</p>
    </Notice>
  );
}

/** A copy of the clipboard text, for a browser without the Clipboard API. */
function copyWithSelection(field: HTMLInputElement): boolean {
  field.focus();
  field.select();
  try {
    return document.execCommand("copy");
  } catch {
    return false;
  }
}

/**
 * A secret value displayed once, in a read-only field with a "Copy" button. The value lives only
 * in the state of the page that shows it, and goes when the person dismisses it or leaves.
 */
export function SecretValue({
  label,
  value,
  onDismiss,
  children,
}: {
  label: string;
  value: string;
  onDismiss: () => void;
  children?: ReactNode;
}) {
  const fieldRef = useRef<HTMLInputElement>(null);
  const [copy, setCopy] = useState<"idle" | "copied" | "failed">("idle");
  const id = "secret-value";

  // The dialog that created the value returns focus to its button when it closes; the field
  // takes it on the next frame, so the value is selected and read first.
  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      fieldRef.current?.focus();
      fieldRef.current?.select();
    });
    return () => cancelAnimationFrame(frame);
  }, []);

  async function press() {
    try {
      if (!navigator.clipboard) throw new Error("No Clipboard API");
      await navigator.clipboard.writeText(value);
      setCopy("copied");
    } catch {
      // The Clipboard API needs a secure context and a permission; the selection copy needs neither.
      setCopy(fieldRef.current && copyWithSelection(fieldRef.current) ? "copied" : "failed");
    }
  }

  return (
    <Notice tone="warning" role="status" title={label} titleAs="h2">
      {children}
      <p className="font-medium text-foreground">This value is not shown again.</p>
      <div className="mt-1 flex flex-col gap-2 sm:flex-row sm:items-center">
        <label htmlFor={id} className="sr-only">
          {label}
        </label>
        <input
          ref={fieldRef}
          id={id}
          readOnly
          value={value}
          className={`${INPUT} font-mono sm:flex-1`}
          autoComplete="off"
          spellCheck={false}
          data-1p-ignore=""
          data-lpignore="true"
        />
        <div className="flex gap-2">
          <Button size="sm" variant="primary" onPress={press}>
            Copy
          </Button>
          <Button size="sm" variant="outline" onPress={onDismiss}>
            Dismiss
          </Button>
        </div>
      </div>
      <p aria-live="polite" className="text-xs">
        {copy === "copied"
          ? "Copied."
          : copy === "failed"
            ? "This browser did not copy the value. Select the text in the field and copy it."
            : ""}
      </p>
    </Notice>
  );
}
