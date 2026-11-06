import { Button, Modal } from "@infrahub/ui";
import type { ReactNode } from "react";

import { describeError } from "#/api/queries";

export const INPUT =
  "w-full rounded-xl border border-input-border bg-input px-3 py-2 text-base shadow-input placeholder:text-subtle-muted focus-visible:border-ring focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring-halo md:text-sm";

export function Field({
  id,
  label,
  hint,
  children,
}: {
  id: string;
  label: string;
  hint?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="font-medium text-sm">
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

/** The API's refusal, next to the button that caused it. */
export function MutationError({ error }: { error: unknown }) {
  if (!error) return null;
  const d = describeError(error);
  return (
    <p role="alert" className="rounded-xl bg-danger-surface px-3 py-2 text-danger text-sm">
      <span className="font-medium">{d.title}</span>
      {d.code && <span className="ml-1 font-mono text-xs opacity-80">{d.code}</span>}
    </p>
  );
}

/** A modal with a title, a body and a footer of actions; Enter in a field submits. */
export function DialogFrame({
  isOpen,
  onOpenChange,
  title,
  description,
  children,
  submitLabel,
  submitVariant = "primary",
  isPending,
  isDisabled,
  onSubmit,
}: {
  isOpen: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: ReactNode;
  children: ReactNode;
  submitLabel: string;
  submitVariant?: "primary" | "danger" | "warning";
  isPending: boolean;
  isDisabled?: boolean;
  onSubmit: () => void;
}) {
  return (
    <Modal isOpen={isOpen} onOpenChange={onOpenChange} aria-label={title}>
      <form
        className="flex w-[min(520px,calc(100vw-48px))] flex-col"
        onSubmit={(e) => {
          e.preventDefault();
          if (!isPending && !isDisabled) onSubmit();
        }}
      >
        <div className="flex flex-col gap-1 px-5 pt-5 pb-4">
          <h2 className="text-balance font-semibold text-base tracking-tight">{title}</h2>
          {description && (
            <div className="text-pretty text-foreground-muted text-sm">{description}</div>
          )}
        </div>
        <div className="flex flex-col gap-4 px-5 pb-5">{children}</div>
        <div className="flex justify-end gap-2 border-t bg-content-muted px-5 py-3">
          <Button variant="ghost" size="sm" onPress={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            type="submit"
            variant={submitVariant}
            size="sm"
            isDisabled={isDisabled || isPending}
          >
            {isPending ? "Sending…" : submitLabel}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
