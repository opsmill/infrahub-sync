import { Button, Modal } from "@infrahub/ui";
import { Link } from "@tanstack/react-router";
import { type ReactNode, useId, useLayoutEffect, useRef } from "react";

import { describeError } from "#/api/queries";
import { FOCUS, Notice } from "#/components/page";

export const INPUT =
  "w-full rounded-xl border border-input-border bg-input px-3 py-2 text-base shadow-input transition-[border-color,box-shadow] duration-150 ease-out placeholder:text-subtle-muted focus-visible:border-ring focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring-halo disabled:cursor-not-allowed disabled:opacity-60 md:text-sm";

/** The design-system amber fill gives white text about 3:1; amber-700 and darker reach 5:1. */
const WARNING_FILL = "border-amber-700 from-amber-700 to-amber-800";

export const LINK = `rounded-sm font-medium text-accent underline-offset-2 hover:underline ${FOCUS}`;

/** Autofocus only with a mouse or trackpad, so a phone keyboard does not cover the dialog on open. */
export const finePointer = () =>
  typeof window !== "undefined" && window.matchMedia("(pointer: fine)").matches;

export function Field({
  id,
  label,
  hint,
  children,
}: {
  id: string;
  label: ReactNode;
  hint?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="font-medium text-foreground text-sm">
        {label}
      </label>
      {children}
      {hint && (
        <p id={`${id}-hint`} className="text-pretty text-subtle text-xs">
          {hint}
        </p>
      )}
    </div>
  );
}

export function ReasonField({
  id,
  value,
  onChange,
  autoFocus,
}: {
  id: string;
  value: string;
  onChange: (v: string) => void;
  autoFocus?: boolean;
}) {
  return (
    <Field id={id} label="Reason" hint="Optional. Stored in the Sync service's audit log.">
      <input
        id={id}
        className={INPUT}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        autoComplete="off"
        // biome-ignore lint/a11y/noAutofocus: only with a fine pointer, inside a modal that just opened.
        autoFocus={autoFocus}
        aria-describedby={`${id}-hint`}
      />
    </Field>
  );
}

/** The Sync API's refusal, next to the button that sent the request. */
export function MutationError({
  error,
  currentRunId,
  retryHint,
}: {
  error: unknown;
  currentRunId?: string;
  retryHint?: ReactNode;
}) {
  if (!error) return null;
  const d = describeError(error);
  const detail =
    d.kind === "no-answer" && retryHint
      ? retryHint
      : d.detail && d.detail !== d.code
        ? d.detail
        : undefined;
  return (
    <Notice tone="danger" role="alert" title={d.title}>
      {detail && <p className="text-pretty text-foreground-muted">{detail}</p>}
      {d.codeLine && <p className="font-mono text-subtle text-xs">{d.codeLine}</p>}
      {d.runId && d.runId !== currentRunId && (
        <p>
          <Link to="/runs/$runId" params={{ runId: d.runId }} className={LINK}>
            View run {d.runId}
          </Link>
        </p>
      )}
    </Notice>
  );
}

/** What to do after a request that got no answer; the idempotency key makes a resend safe. */
export function resendHint(
  submitLabel: string,
  { check = "reload this page", sameRun = false }: { check?: ReactNode; sameRun?: boolean } = {}
) {
  return (
    <>
      The Sync service may have received the request. To retry the same request, press {submitLabel}{" "}
      again without changing the form{sameRun ? "; no second run is created" : ""}. To check first,{" "}
      {check}.
    </>
  );
}

const TOP = "max(1rem, 8vh)";
/** Anchors the dialog by its top edge, so it grows downward and the control under the pointer stays put. */
const ANCHOR_TOP = {
  top: TOP,
  translate: "-50% 0",
  maxHeight: `calc(var(--visual-viewport-height) - ${TOP} - 1rem)`,
};

/**
 * A modal with a fixed header and footer and a scrolling body. Enter in a field submits. While
 * the request is in flight the dialog cannot be closed or edited, so its answer stays in view.
 */
export function DialogFrame({
  isOpen,
  onOpenChange,
  title,
  ariaLabel,
  description,
  notice,
  error,
  children,
  submitLabel,
  submitVariant = "primary",
  pendingLabel,
  isPending,
  isDisabled = false,
  disabledReason,
  dismissLabel = "Cancel",
  autoFocusDismiss,
  submitOnEnter = true,
  onSubmit,
}: {
  isOpen: boolean;
  onOpenChange: (open: boolean) => void;
  title: ReactNode;
  ariaLabel: string;
  description?: ReactNode;
  notice?: ReactNode;
  error?: ReactNode;
  children: ReactNode;
  submitLabel: string;
  submitVariant?: "primary" | "danger" | "warning";
  /** Read by screen readers while the request is in flight; the button shows a spinner. */
  pendingLabel: string;
  isPending: boolean;
  isDisabled?: boolean;
  disabledReason?: ReactNode;
  dismissLabel?: string;
  autoFocusDismiss?: boolean;
  /** False when a write cannot be undone by the person, so only a press on the button sends it. */
  submitOnEnter?: boolean;
  onSubmit: () => void;
}) {
  const id = useId();
  const titleId = `${id}-title`;
  const descId = `${id}-desc`;
  const noticeId = `${id}-notice`;
  const reasonId = `${id}-reason`;
  const showReason = isDisabled && Boolean(disabledReason);
  // The vendor Modal does not forward isKeyboardDismissDisabled, so Escape is refused here.
  const close = (open: boolean) => {
    if (!open && isPending) return;
    onOpenChange(open);
  };
  const describedBy = [description && descId, notice && noticeId].filter(Boolean).join(" ");
  const formRef = useRef<HTMLFormElement>(null);
  const submitRef = useRef<HTMLButtonElement>(null);
  // The vendor Modal forwards only aria-label to the dialog element.
  useLayoutEffect(() => {
    const dialog = formRef.current?.closest("[role=dialog]");
    if (!dialog) return;
    if (describedBy) dialog.setAttribute("aria-describedby", describedBy);
    else dialog.removeAttribute("aria-describedby");
  }, [describedBy]);
  return (
    <Modal
      isOpen={isOpen}
      onOpenChange={close}
      isDismissable={!isPending}
      aria-label={ariaLabel}
      style={ANCHOR_TOP}
    >
      <form
        ref={formRef}
        // The vendor modal is 90vw wide with 4px padding and a 1px border on each side.
        className="flex min-h-0 w-[min(520px,calc(90vw-10px))] flex-1 flex-col"
        aria-labelledby={titleId}
        onKeyDown={(e) => {
          if (!submitOnEnter && e.key === "Enter" && e.target instanceof HTMLInputElement) {
            e.preventDefault();
          }
        }}
        onSubmit={(e) => {
          e.preventDefault();
          if (isPending || isDisabled) return;
          // The body turns inert during the request; focus waits on the pending button instead.
          submitRef.current?.focus();
          onSubmit();
        }}
      >
        <div className="flex shrink-0 flex-col gap-1.5 px-5 pt-5 pb-4">
          <h2 id={titleId} className="text-balance font-semibold text-base">
            {title}
          </h2>
          {description && (
            <div id={descId} className="text-pretty text-foreground-muted text-sm">
              {description}
            </div>
          )}
        </div>
        <div
          className="flex min-h-0 flex-col gap-4 overflow-y-auto overscroll-contain px-5 pb-5"
          inert={isPending}
        >
          {notice && <div id={noticeId}>{notice}</div>}
          <fieldset disabled={isPending} className="m-0 flex min-w-0 flex-col gap-4 border-0 p-0">
            {children}
          </fieldset>
        </div>
        {error && <div className="shrink-0 px-5 pb-3">{error}</div>}
        <div className="flex shrink-0 flex-col gap-2 border-t bg-content-muted px-5 pt-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] sm:flex-row sm:items-center sm:gap-3">
          {showReason && (
            <p id={reasonId} className="min-w-0 text-pretty text-subtle text-xs">
              {disabledReason}
            </p>
          )}
          <div className="flex flex-col gap-2 sm:ml-auto sm:flex-row">
            <Button
              variant="ghost"
              size="sm"
              className="max-sm:h-11 max-sm:w-full"
              isDisabled={isPending}
              autoFocus={autoFocusDismiss}
              onPress={() => close(false)}
            >
              {dismissLabel}
            </Button>
            <Button
              ref={submitRef}
              type="submit"
              variant={isDisabled ? "outline" : submitVariant}
              size="sm"
              className={`max-sm:h-11 max-sm:w-full ${!isDisabled && submitVariant === "warning" ? WARNING_FILL : ""}`}
              isPending={isPending}
              isDisabledAndFocusable={isDisabled}
              aria-describedby={showReason ? reasonId : undefined}
            >
              {submitLabel}
            </Button>
          </div>
        </div>
        <span className="sr-only" aria-live="polite">
          {isPending ? pendingLabel : ""}
        </span>
      </form>
    </Modal>
  );
}
