import { Button, Card } from "@infrahub/ui";
import {
  type ReactNode,
  type Ref,
  type RefCallback,
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";

import { describeError } from "#/api/queries";
import { formatAbsolute } from "#/lib/time";

import { announce } from "./announcer";
import { CODE_CHIP, COUNT_BADGE, FOCUS, FOCUS_INSET } from "./classes";
import { CheckIcon, CopyIcon, SearchIcon, StateIcon, WarningIcon, XIcon } from "./icons";

export { CODE_CHIP, COUNT_BADGE, CRUMB, FOCUS, FOCUS_INSET, META_LIST } from "./classes";

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
    <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
      <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-0.5">
        <h2 id={id} className="inline-flex items-center gap-2 font-semibold text-sm tracking-tight">
          {title}
          {count !== undefined && <span className={COUNT_BADGE}>{count}</span>}
        </h2>
        {children && <p className="text-subtle text-xs">{children}</p>}
      </div>
      {actions}
    </div>
  );
}

/** The layout of every page-level message: its h1 sits where a page title sits. */
export function PageMessage({
  title,
  icon,
  children,
  code,
  actions,
  alert = false,
}: {
  title: string;
  icon?: ReactNode;
  children?: ReactNode;
  code?: string;
  actions?: ReactNode;
  alert?: boolean;
}) {
  return (
    <section
      role={alert ? "alert" : undefined}
      className="flex w-full min-w-0 max-w-2xl flex-col items-start gap-3 pt-8 pb-6"
    >
      <h1 className="flex items-center gap-2 text-balance font-semibold text-2xl tracking-tight [overflow-wrap:anywhere]">
        {icon}
        {title}
      </h1>
      {children && (
        <div className="flex min-w-0 max-w-[min(60ch,100%)] flex-col gap-2 text-pretty text-foreground-muted">
          {children}
        </div>
      )}
      {code && <p className="font-mono text-subtle text-xs">{code}</p>}
      {actions && <div className="mt-2 flex flex-wrap gap-2">{actions}</div>}
    </section>
  );
}

/** A failed request, said in words, with a way to retry it when a retry can help. */
export function ErrorPanel({
  error,
  onRetry,
  compact = false,
  level = "section",
  action,
  children,
}: {
  error: unknown;
  onRetry?: () => void;
  compact?: boolean;
  level?: "page" | "section";
  action?: ReactNode;
  children?: ReactNode;
}) {
  const d = describeError(error);
  const retry =
    onRetry && d.retryable ? (
      <Button variant="outline" size="sm" onPress={onRetry}>
        Try again
      </Button>
    ) : null;

  if (level === "page") {
    return (
      <PageMessage
        title={d.title}
        alert={!d.notFound}
        icon={
          d.notFound ? undefined : (
            <StateIcon state="failed" className="size-5 shrink-0 text-danger" />
          )
        }
        code={d.codeLine}
        actions={
          action || retry ? (
            <>
              {action}
              {retry}
            </>
          ) : undefined
        }
      >
        {d.detail && <p>{d.detail}</p>}
        {children}
      </PageMessage>
    );
  }

  return (
    <Card>
      <div
        className={`flex flex-col items-center gap-3 px-6 text-center ${compact ? "py-8" : "py-16"}`}
        role={d.notFound ? undefined : "alert"}
      >
        {d.notFound ? (
          <StateIcon state="idle" className="size-5 text-subtle-muted" />
        ) : (
          <span className="flex size-10 items-center justify-center rounded-xl bg-danger-surface text-danger">
            <StateIcon state="failed" className="size-5" />
          </span>
        )}
        <h2 className="text-balance font-semibold text-base">{d.title}</h2>
        {d.detail && <p className="max-w-[60ch] text-pretty text-foreground-muted">{d.detail}</p>}
        {children}
        {d.codeLine && <p className="font-mono text-subtle text-xs">{d.codeLine}</p>}
        {(action || retry) && (
          <div className="mt-1 flex flex-wrap justify-center gap-2">
            {action}
            {retry}
          </div>
        )}
      </div>
    </Card>
  );
}

const NOTICE_TONE = {
  neutral: {
    box: "border-border bg-content-muted",
    title: "text-foreground",
    icon: <StateIcon state="idle" className="mt-0.5 size-4 shrink-0 text-subtle" />,
  },
  warning: {
    box: "border-warning-border bg-warning-surface",
    title: "text-foreground",
    icon: <WarningIcon className="mt-0.5 size-4 shrink-0 text-warning" />,
  },
  danger: {
    box: "border-danger/30 bg-danger-surface",
    title: "text-danger",
    icon: <StateIcon state="failed" className="mt-0.5 size-4 shrink-0 text-danger" />,
  },
} as const;

/** A sentence a person reads before acting on the page: a caution, a failure or a fact about the data. */
export function Notice({
  tone = "neutral",
  icon,
  title,
  titleAs: Title = "p",
  titleId,
  children,
  action,
  role = "status",
  id,
  className = "",
}: {
  tone?: "neutral" | "warning" | "danger";
  icon?: ReactNode;
  title?: ReactNode;
  titleAs?: "h2" | "h3" | "p";
  titleId?: string;
  children?: ReactNode;
  action?: ReactNode;
  role?: "status" | "alert" | "note";
  id?: string;
  className?: string;
}) {
  const t = NOTICE_TONE[tone];
  return (
    <div
      id={id}
      role={role}
      className={`flex flex-wrap items-start gap-x-3 gap-y-1 rounded-xl border px-4 py-2.5 text-sm ${t.box} ${className}`}
    >
      {icon ?? t.icon}
      <div className="flex min-w-0 flex-1 flex-col gap-1 text-pretty text-foreground-muted">
        {title && (
          <Title id={titleId} className={`font-medium text-sm ${t.title}`}>
            {title}
          </Title>
        )}
        {children}
      </div>
      {action && <div className="ml-auto shrink-0">{action}</div>}
    </div>
  );
}

/** Shown above data that a background refresh failed to update. */
export function StaleNotice({
  error,
  updatedAt,
  onRetry,
}: {
  error: unknown;
  updatedAt: number;
  onRetry: () => void;
}) {
  const d = describeError(error);
  const iso = new Date(updatedAt).toISOString();
  useEffect(() => {
    announce("The last refresh failed. This page shows older data from the Sync API.");
  }, []);
  return (
    <Notice
      tone="neutral"
      role="note"
      className="mb-4"
      icon={<StateIcon state="failed" className="mt-0.5 size-4 shrink-0 text-danger" />}
      action={
        d.retryable ? (
          <Button size="xs" variant="ghost" className="-my-1" onPress={onRetry}>
            Try again
          </Button>
        ) : undefined
      }
    >
      <p>
        The last refresh failed. {d.title.replace(/\.$/, "")}. This page shows what the Sync API
        returned at <time dateTime={iso}>{formatAbsolute(iso)}</time>.
      </p>
      {!d.retryable && d.detail && <p>{d.detail}</p>}
    </Notice>
  );
}

/** Text from the Sync API with `backticks` around commands, shown as code. */
export function InlineCode({ text }: { text: string }) {
  const parts = text.split("`");
  if (parts.length % 2 === 0) return <>{text}</>;
  return (
    <>
      {parts.map((part, i) =>
        i % 2 === 1 ? (
          <code key={i} className={CODE_CHIP}>
            {part}
          </code>
        ) : (
          part
        )
      )}
    </>
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
  <span
    className={`block animate-skeleton rounded-md bg-border motion-reduce:animate-none ${className}`}
    aria-hidden="true"
  />
);

function copyWithSelection(value: string): boolean {
  const area = document.createElement("textarea");
  area.value = value;
  area.readOnly = true;
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.append(area);
  area.select();
  const copied = document.execCommand("copy");
  area.remove();
  return copied;
}

/** A copy button that confirms with a check mark, or shows a cross when the browser refuses. */
export function CopyButton({ value, label = "Copy" }: { value: string; label?: string }) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  useEffect(() => {
    if (state === "idle") return;
    const id = window.setTimeout(() => setState("idle"), 1500);
    return () => window.clearTimeout(id);
  }, [state]);
  const copy = async () => {
    try {
      if (!navigator.clipboard) throw new Error("No Clipboard API");
      await navigator.clipboard.writeText(value);
      setState("copied");
    } catch {
      // The Clipboard API needs a secure context and permission; the selection copy needs neither.
      setState(copyWithSelection(value) ? "copied" : "failed");
    }
  };
  return (
    <>
      <Button
        size="xxs"
        shape="square"
        variant="ghost"
        className="relative before:absolute before:-inset-2.5"
        aria-label={state === "copied" ? "Copied" : state === "failed" ? "Could not copy" : label}
        onPress={copy}
      >
        {state === "copied" ? (
          <CheckIcon className="size-3.5 text-success" />
        ) : state === "failed" ? (
          <XIcon className="size-3.5 text-danger" />
        ) : (
          <CopyIcon className="size-3.5" />
        )}
      </Button>
      <span className="sr-only" aria-live="polite">
        {state === "copied"
          ? "Copied"
          : state === "failed"
            ? "Could not copy. Select the text and copy it."
            : ""}
      </span>
    </>
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

type Overflow = "none" | "start" | "end" | "both";

// The fade marks the side where options are scrolled out of view; the scrollbar is hidden.
const FADE: Record<Overflow, string> = {
  none: "",
  end: "[mask-image:linear-gradient(to_right,#000_calc(100%-2rem),transparent)]",
  start: "[mask-image:linear-gradient(to_left,#000_calc(100%-2rem),transparent)]",
  both: "[mask-image:linear-gradient(to_right,transparent,#000_2rem,#000_calc(100%-2rem),transparent)]",
};

function overflowOf(el: HTMLElement): Overflow {
  const max = el.scrollWidth - el.clientWidth;
  if (max <= 1) return "none";
  const start = el.scrollLeft > 1;
  const end = el.scrollLeft < max - 1;
  return start && end ? "both" : start ? "start" : end ? "end" : "none";
}

/** Buttons that switch between views of one list; the pressed button is the current view. */
export function FilterTabs<T extends string>({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: { id: T; label: string; count?: number | string }[];
  value: T;
  onChange: (id: T) => void;
}) {
  const scroller = useRef<HTMLDivElement>(null);
  const [overflow, setOverflow] = useState<Overflow>("none");

  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const update = () => setOverflow(overflowOf(el));
    update();
    el.addEventListener("scroll", update, { passive: true });
    const observer = new ResizeObserver(update);
    observer.observe(el);
    for (const child of el.children) observer.observe(child);
    return () => {
      el.removeEventListener("scroll", update);
      observer.disconnect();
    };
  }, [options]);

  useEffect(() => {
    const el = scroller.current;
    const pressed = el?.querySelector<HTMLElement>('[aria-pressed="true"]');
    if (!el || !pressed) return;
    // Scrolls the list only, never the page, so the pressed option is in view.
    const left = pressed.offsetLeft;
    if (left < el.scrollLeft) el.scrollLeft = left;
    else if (left + pressed.offsetWidth > el.scrollLeft + el.clientWidth)
      el.scrollLeft = left + pressed.offsetWidth - el.clientWidth;
  }, [value]);

  return (
    <fieldset
      aria-label={label}
      className="m-0 flex min-w-0 max-w-full overflow-hidden rounded-xl border border-border bg-content p-0.5 shadow-input"
    >
      <div
        ref={scroller}
        className={`relative flex min-w-0 gap-0.5 overflow-x-auto [scrollbar-width:none] ${FADE[overflow]}`}
      >
        {options.map((o) => {
          const selected = o.id === value;
          return (
            <button
              key={o.id}
              type="button"
              aria-pressed={selected}
              onClick={() => onChange(o.id)}
              className={`inline-flex h-8 shrink-0 cursor-pointer items-center gap-1.5 whitespace-nowrap rounded-[10px] px-2.5 text-sm transition-colors duration-150 ease-out pointer-coarse:h-11 motion-reduce:transition-none ${FOCUS_INSET} ${
                selected
                  ? "bg-content-strong text-foreground"
                  : "text-foreground-muted hover:bg-content-muted hover:text-foreground"
              }`}
            >
              {o.label}
              {o.count !== undefined && (
                <>
                  <span className="sr-only">, </span>
                  <span
                    className={`text-xs tabular-nums ${o.count === 0 && !selected ? "text-subtle-muted" : "text-subtle"}`}
                  >
                    {o.count}
                  </span>
                </>
              )}
            </button>
          );
        })}
      </div>
    </fieldset>
  );
}

function assignRef<T>(ref: Ref<T> | undefined, value: T | null) {
  if (typeof ref === "function") ref(value);
  else if (ref) ref.current = value;
}

/** A search field: `/` focuses it, Escape clears it, and screen readers hear the match count. */
export function SearchField({
  value,
  onChange,
  placeholder,
  label,
  count,
  noun,
  shortcut = true,
  inputRef,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder: string;
  /** The field's accessible name; the placeholder is used when it is left out. */
  label?: string;
  /** With `noun`, screen readers hear the number of matches after typing stops. */
  count?: number;
  noun?: [string, string];
  /** When true, `/` anywhere on the page focuses the field. */
  shortcut?: boolean;
  inputRef?: Ref<HTMLInputElement>;
}) {
  const id = useId();
  const ref = useRef<HTMLInputElement | null>(null);
  const setRef: RefCallback<HTMLInputElement> = useCallback(
    (el) => {
      ref.current = el;
      assignRef(inputRef, el);
    },
    [inputRef]
  );

  useEffect(() => {
    if (!shortcut) return;
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (e.key !== "/" || e.metaKey || e.ctrlKey || e.altKey) return;
      if (/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) || t.isContentEditable) return;
      e.preventDefault();
      ref.current?.focus();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [shortcut]);

  const [announcement, setAnnouncement] = useState("");
  const counted = count !== undefined && noun !== undefined;
  const message = counted && value ? `${count} ${count === 1 ? noun[0] : noun[1]}` : "";
  useEffect(() => {
    // Announcing on every keystroke interrupts the reader; wait until typing pauses.
    const t = window.setTimeout(() => setAnnouncement(message), 500);
    return () => window.clearTimeout(t);
  }, [message]);

  return (
    <div className="relative w-full sm:w-80">
      <SearchIcon className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-subtle-muted" />
      <label htmlFor={id} className="sr-only">
        {label ?? placeholder}
      </label>
      <input
        ref={setRef}
        id={id}
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
        className="flex min-h-10 w-full items-center rounded-xl border border-input-border bg-input py-2 pr-10 pl-9 text-base shadow-input transition-[border-color,box-shadow] duration-150 ease-out placeholder:text-subtle-muted focus-visible:border-ring focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring-halo motion-reduce:transition-none md:text-sm [&::-webkit-search-cancel-button]:hidden"
      />
      {value ? (
        <button
          type="button"
          onClick={() => {
            onChange("");
            ref.current?.focus();
          }}
          aria-label="Clear search"
          className={`absolute top-1/2 right-1.5 flex size-7 -translate-y-1/2 cursor-pointer items-center justify-center rounded-lg text-subtle before:absolute before:-inset-1.5 hover:bg-highlight hover:text-foreground ${FOCUS}`}
        >
          <XIcon />
        </button>
      ) : (
        shortcut && (
          <kbd className="pointer-events-none absolute top-1/2 right-2.5 hidden -translate-y-1/2 rounded-md border border-border-strong bg-content px-1.5 font-mono text-[11px] text-subtle sm:block">
            /
          </kbd>
        )
      )}
      {counted && (
        <span className="sr-only" aria-live="polite">
          {announcement}
        </span>
      )}
    </div>
  );
}

/** Classes for a row or card that a stretched link makes clickable. */
export const CLICKABLE =
  "group relative transition-colors duration-150 ease-out hover:bg-[color-mix(in_oklch,var(--foreground)_4%,transparent)] has-[a[data-stretched]:focus-visible]:outline-2 has-[a[data-stretched]:focus-visible]:-outline-offset-2 has-[a[data-stretched]:focus-visible]:outline-ring motion-reduce:transition-none";

/** Classes for the stretched link inside a CLICKABLE row. */
export const STRETCHED =
  "after:absolute after:inset-0 after:rounded-[inherit] after:content-[''] focus-visible:outline-hidden";

/** A quiet action that names the next step; teal when a person must act. */
export const textAction = (primary: boolean) =>
  `relative z-10 -mx-1.5 inline-flex h-8 cursor-pointer items-center gap-1 whitespace-nowrap rounded-md px-1.5 font-medium text-[13px] transition-colors duration-150 ease-out motion-reduce:transition-none ${FOCUS} ${
    primary
      ? "text-accent hover:bg-accent-surface"
      : "text-foreground-muted hover:bg-highlight group-hover:text-foreground"
  }`;
