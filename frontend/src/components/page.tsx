import { Button, Card } from "@infrahub/ui";
import { type ReactNode, useEffect, useRef, useState } from "react";

import { describeError } from "#/api/queries";

import { CheckIcon, CopyIcon, SearchIcon, StateIcon, XIcon } from "./icons";

export function SectionHeading({
  id,
  title,
  count,
  children,
  actions,
}: {
  id?: string;
  title: string;
  count?: number;
  children?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 px-1">
      <div className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-0.5">
        <h2 id={id} className="inline-flex items-center gap-2 font-semibold text-sm tracking-tight">
          {title}
          {count !== undefined && (
            <span className="rounded-md bg-content-strong px-1.5 font-medium text-foreground-muted text-xs leading-5 tabular-nums">
              {count}
            </span>
          )}
        </h2>
        {children && <p className="text-subtle text-xs">{children}</p>}
      </div>
      {actions}
    </div>
  );
}

/** A failed request, said in words, with a way to retry it. */
export function ErrorPanel({
  error,
  onRetry,
  compact = false,
}: {
  error: unknown;
  onRetry?: () => void;
  compact?: boolean;
}) {
  const d = describeError(error);
  return (
    <Card>
      <div
        className={`flex flex-col items-center gap-3 px-6 text-center ${compact ? "py-8" : "py-16"}`}
        role="alert"
      >
        <span className="flex size-10 items-center justify-center rounded-xl bg-danger-surface text-danger">
          <StateIcon state="failed" className="size-5" />
        </span>
        <h2 className="text-balance font-semibold text-base">{d.title}</h2>
        <p className="max-w-[60ch] text-pretty text-foreground-muted">
          {d.detail}
          {d.status && <span className="text-subtle"> · HTTP {d.status}</span>}
        </p>
        {onRetry && (
          <Button variant="outline" size="sm" onPress={onRetry}>
            Try again
          </Button>
        )}
      </div>
    </Card>
  );
}

export function EmptyPanel({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <Card>
      <div className="flex flex-col items-center gap-3 px-6 py-14 text-center">
        <div className="flex items-center gap-2 text-subtle-muted" aria-hidden="true">
          <span className="size-2.5 rounded-full border-[1.5px] border-current" />
          <span className="h-0.5 w-8 rounded-full bg-current opacity-40" />
          <span className="size-2.5 rounded-full border-[1.5px] border-current" />
          <span className="h-0.5 w-8 rounded-full bg-current opacity-40" />
          <span className="size-2.5 rounded-full border-[1.5px] border-current" />
        </div>
        <h2 className="text-balance font-semibold text-base">{title}</h2>
        {children}
      </div>
    </Card>
  );
}

export const Bar = ({ className }: { className: string }) => (
  <span className={`block animate-skeleton rounded-md bg-border ${className}`} aria-hidden="true" />
);

/** A copy button that confirms with a check mark; selects the text when the browser refuses. */
export function CopyButton({ value, label = "Copy" }: { value: string; label?: string }) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  useEffect(() => {
    if (state === "idle") return;
    const id = window.setTimeout(() => setState("idle"), 1500);
    return () => window.clearTimeout(id);
  }, [state]);
  return (
    <Button
      size="xxs"
      shape="square"
      variant="ghost"
      aria-label={state === "copied" ? "Copied" : label}
      onPress={async () => {
        try {
          await navigator.clipboard.writeText(value);
          setState("copied");
        } catch {
          setState("failed");
        }
      }}
    >
      {state === "copied" ? (
        <CheckIcon className="size-3.5 text-success" />
      ) : (
        <CopyIcon className="size-3.5" />
      )}
    </Button>
  );
}

export function CopyCommand({ command }: { command: string }) {
  return (
    <div className="flex w-full max-w-md items-center gap-2 rounded-xl border bg-content-muted py-1 pr-1 pl-3 text-left">
      <code className="min-w-0 flex-1 overflow-x-auto whitespace-nowrap font-mono text-[13px]">
        {command}
      </code>
      <CopyButton value={command} label="Copy command" />
    </div>
  );
}

/** A search field: `/` focuses it, Escape clears it, and screen readers hear the match count. */
export function SearchField({
  value,
  onChange,
  count,
  placeholder,
  noun,
}: {
  value: string;
  onChange: (v: string) => void;
  count: number;
  placeholder: string;
  noun: [string, string];
}) {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (e.key !== "/" || e.metaKey || e.ctrlKey || e.altKey) return;
      if (/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) || t.isContentEditable) return;
      e.preventDefault();
      ref.current?.focus();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);
  return (
    <div className="relative w-full sm:w-80">
      <SearchIcon className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-subtle-muted" />
      <label htmlFor="list-search" className="sr-only">
        {placeholder}
      </label>
      <input
        ref={ref}
        id="list-search"
        type="search"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Escape" && value) {
            e.preventDefault();
            onChange("");
          }
        }}
        placeholder={placeholder}
        autoComplete="off"
        spellCheck={false}
        className="flex min-h-10 w-full items-center rounded-xl border border-input-border bg-input py-2 pr-10 pl-9 text-base shadow-input placeholder:text-subtle-muted focus-visible:border-ring focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring-halo md:text-sm [&::-webkit-search-cancel-button]:hidden"
      />
      {value ? (
        <button
          type="button"
          onClick={() => {
            onChange("");
            ref.current?.focus();
          }}
          aria-label="Clear search"
          className="absolute top-1/2 right-1.5 flex size-7 -translate-y-1/2 cursor-pointer items-center justify-center rounded-lg text-subtle before:absolute before:-inset-1.5 hover:bg-highlight focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring-halo"
        >
          <XIcon />
        </button>
      ) : (
        <kbd className="pointer-events-none absolute top-1/2 right-2.5 hidden -translate-y-1/2 rounded-md border border-border-strong bg-content px-1.5 font-mono text-[11px] text-subtle sm:block">
          /
        </kbd>
      )}
      <span className="sr-only" aria-live="polite">
        {value ? `${count} ${count === 1 ? noun[0] : noun[1]}` : ""}
      </span>
    </div>
  );
}

/** Classes for a row or card that a stretched link makes clickable. */
export const CLICKABLE =
  "group relative transition-colors duration-150 ease-out hover:bg-content-muted has-[a[data-stretched]:focus-visible]:ring-2 has-[a[data-stretched]:focus-visible]:ring-ring-halo has-[a[data-stretched]:focus-visible]:ring-inset motion-reduce:transition-none";

/** Classes for the stretched link inside a CLICKABLE row. */
export const STRETCHED =
  "after:absolute after:inset-0 after:rounded-[inherit] after:content-[''] focus-visible:outline-hidden";

/** A quiet action that names the next step; teal when a person must act. */
export const textAction = (primary: boolean) =>
  `relative z-10 -mx-1.5 inline-flex h-8 cursor-pointer items-center gap-1 whitespace-nowrap rounded-md px-1.5 font-medium text-[13px] transition-colors duration-150 ease-out focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring-halo motion-reduce:transition-none ${
    primary
      ? "text-accent hover:bg-accent-surface"
      : "text-foreground-muted hover:bg-highlight group-hover:text-foreground"
  }`;
