import { useRouter, useRouterState } from "@tanstack/react-router";
import { type ReactNode, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal, flushSync } from "react-dom";
import { cn } from "tailwind-variants";

import type { Plan, Run } from "#/api/types";
import { XIcon } from "#/components/icons";
import { FOCUS } from "#/components/page";
import { fmt } from "#/domain/run-state";

import { type ChangeCounts, changesStateLine, changesTitle, SplitWords } from "./ChangesDock";
import { ChangesReview } from "./changes/ChangesReview";
import { prepareViews } from "./changes/views";
import { changesSentence, PlanFacts } from "./PlanReview";
import { DownloadProgress, usePlanDownload } from "./plan-download";
import type { Action } from "./plan-values";

export interface ChangesFilter {
  kind?: string;
  action?: Action;
}

const ACTIONS = new Set(["create", "update", "delete"]);

export interface RunSearch {
  /** The changes sheet is open. */
  changes?: true;
  kind?: string;
  action?: Action;
}

/** The run page's search parameters: whether the changes sheet is open, and its filter. */
export function validateRunSearch(search: {
  changes?: unknown;
  kind?: unknown;
  action?: unknown;
}): RunSearch {
  return {
    changes: search.changes === true || search.changes === "true" ? true : undefined,
    kind: typeof search.kind === "string" && search.kind ? search.kind : undefined,
    action:
      typeof search.action === "string" && ACTIONS.has(search.action)
        ? (search.action as Action)
        : undefined,
  };
}

const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const canMorph = () =>
  typeof document !== "undefined" &&
  typeof document.startViewTransition === "function" &&
  !reducedMotion();

/**
 * Whether the changes sheet is open, from the address (`?changes=true&action=…&kind=…`), and how to
 * open and close it. With view transitions the page switches in a synchronous update inside the
 * transition (the router would render before the browser's "before" picture) and the address
 * follows once the transition has finished.
 */
export function useChangesSheet() {
  const router = useRouter();
  const search = useRouterState({ select: (s) => s.location.search }) as RunSearch;
  const [override, setOverride] = useState<{ open: boolean; filter?: ChangesFilter } | null>(null);
  const openedHere = useRef(false);
  const morph = canMorph();

  const navigate = (next: Record<string, unknown>, replace = false) =>
    router.navigate({
      to: ".",
      search: (prev: Record<string, unknown>) => ({ ...prev, ...next }),
      replace,
      resetScroll: false,
    } as never);

  const open = (filter: ChangesFilter = {}) => {
    const next = { changes: true, kind: filter.kind, action: filter.action };
    openedHere.current = true;
    if (!morph) return navigate(next);
    document
      .startViewTransition({
        update: () => flushSync(() => setOverride({ open: true, filter })),
        types: ["changes-open"],
      })
      .finished.finally(async () => {
        await navigate(next);
        setOverride(null);
      });
  };

  const leave = async () => {
    if (openedHere.current) {
      openedHere.current = false;
      router.history.back();
    } else {
      await navigate({ changes: undefined, kind: undefined, action: undefined }, true);
    }
  };

  const close = () => {
    if (!morph) return void leave();
    document
      .startViewTransition({
        update: () => flushSync(() => setOverride({ open: false })),
        types: ["changes-close"],
      })
      .finished.finally(async () => {
        await leave();
        setOverride(null);
      });
  };

  const isOpen = override ? override.open : search.changes === true;
  const filter: ChangesFilter = override?.filter ?? { kind: search.kind, action: search.action };
  const setFilter = (f: ChangesFilter) => void navigate({ kind: f.kind, action: f.action }, true);
  return { isOpen, filter, setFilter, open, close, morph };
}

/* ---------- the sheet ---------- */

const COLUMN_ID = "changes-sheet-column";
const ENTER_MS = 380;
const EXIT_MS = 240;
const ENTER_EASE = "cubic-bezier(0.32, 0.72, 0, 1)";
const EXIT_EASE = "cubic-bezier(0.32, 0, 0.67, 0)";
/** How much the run page shrinks behind the sheet when it slides in. */
const BEHIND = 0.97;

const typing = (t: EventTarget | null) =>
  t instanceof HTMLElement && (/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) || t.isContentEditable);

/** The prepared rows of a plan, built in small steps so that 150,000 operations never freeze the page. */
const prepared = new WeakMap<Plan, ReturnType<typeof prepareViews>>();
function usePreparedViews(plan: Plan | undefined, hold: boolean) {
  const [state, setState] = useState<{
    views?: Awaited<ReturnType<typeof prepareViews>>;
    done: number;
  }>({ done: 0 });
  useEffect(() => {
    if (!plan || hold) return;
    let live = true;
    let p = prepared.get(plan);
    if (!p) {
      p = prepareViews(plan, { onProgress: (done) => live && setState((s) => ({ ...s, done })) });
      prepared.set(plan, p);
    }
    p.then((views) => live && setState({ views, done: views.length }));
    return () => {
      live = false;
    };
  }, [plan, hold]);
  return state;
}

/**
 * The plan's changes in a sheet over the run page. It grows out of the dock (`morph`) or slides up;
 * Esc, the close button or dragging its top down returns to the run where it was.
 */
export function ChangesSheet({
  run,
  counts,
  branch,
  filter,
  onFilterChange,
  close,
  morph,
  replaced = false,
}: {
  run: Run;
  counts: ChangeCounts;
  branch?: string | null;
  filter: ChangesFilter;
  onFilterChange: (f: ChangesFilter) => void;
  close: () => void;
  morph: boolean;
  /** A newer plan replaced this one, so it no longer waits for review. */
  replaced?: boolean;
}) {
  const still = reducedMotion();
  const [phase, setPhase] = useState<"enter" | "open" | "leave">(still || morph ? "open" : "enter");
  const [drag, setDrag] = useState<number | null>(null);
  // The list mounts once the sheet has finished moving: setting it up would freeze the animation.
  const [settled, setSettled] = useState(still);
  const sheet = useRef<HTMLDivElement>(null);
  const start = useRef<{ y: number; t: number } | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const titleId = useId();
  const opener = useRef<Element | null>(document.activeElement);
  const download = usePlanDownload(run, { start: true });
  const views = usePreparedViews(download.plan, !settled);

  useEffect(() => {
    if (settled) return;
    const id = window.setTimeout(() => setSettled(true), morph ? 440 : ENTER_MS + 40);
    return () => window.clearTimeout(id);
  }, [settled, morph]);

  useEffect(() => {
    const main = document.getElementById("main");
    const root = document.documentElement;
    const overflow = root.style.overflow;
    const gutter = root.style.scrollbarGutter;
    // Locking the scroll removes the page's scrollbar; keeping its space stops the page shifting.
    if (root.scrollHeight > root.clientHeight) root.style.scrollbarGutter = "stable";
    root.style.overflow = "hidden";
    main?.setAttribute("inert", "");
    const id = requestAnimationFrame(() => requestAnimationFrame(() => setPhase("open")));
    heading.current?.focus({ preventScroll: true });
    return () => {
      cancelAnimationFrame(id);
      root.style.overflow = overflow;
      root.style.scrollbarGutter = gutter;
      main?.removeAttribute("inert");
      (opener.current as HTMLElement | null)?.focus?.({ preventScroll: true });
    };
  }, []);

  const height = sheet.current?.offsetHeight ?? window.innerHeight;
  const progress = drag != null ? Math.max(0, 1 - drag / height) : phase === "open" ? 1 : 0;
  const timing =
    drag != null
      ? "none"
      : phase === "leave"
        ? `${EXIT_MS}ms ${EXIT_EASE}`
        : `${ENTER_MS}ms ${ENTER_EASE}`;

  // When the sheet slides, the run page shrinks a little behind it. In the morph it stays still:
  // scaling it would show the page twice in the crossfade.
  useLayoutEffect(() => {
    const shell = document.getElementById("main")?.parentElement;
    if (!shell || still || morph) return;
    shell.style.transformOrigin = `50% ${window.scrollY + window.innerHeight / 2}px`;
    shell.style.transition = timing === "none" ? "none" : `transform ${timing}`;
    shell.style.transform = `scale(${1 - (1 - BEHIND) * progress})`;
  });
  useEffect(
    () => () => {
      const shell = document.getElementById("main")?.parentElement;
      if (shell) shell.style.transform = shell.style.transition = shell.style.transformOrigin = "";
    },
    []
  );

  const leave = () => {
    if (still || morph) return close();
    setDrag(null);
    setPhase("leave");
    window.setTimeout(close, EXIT_MS);
  };

  const grab = {
    onPointerDown: (e: React.PointerEvent) => {
      if ((e.target as Element).closest("button") || e.button !== 0 || still) return;
      start.current = { y: e.clientY, t: performance.now() };
      (e.currentTarget as Element).setPointerCapture(e.pointerId);
    },
    onPointerMove: (e: React.PointerEvent) => {
      if (!start.current) return;
      const dy = e.clientY - start.current.y;
      // Upwards the sheet resists: it is already as high as it goes.
      setDrag(dy > 0 ? dy : dy / 6);
    },
    onPointerUp: (e: React.PointerEvent) => {
      if (!start.current) return;
      const dy = e.clientY - start.current.y;
      const speed = dy / Math.max(1, performance.now() - start.current.t);
      start.current = null;
      if (dy > height * 0.25 || (dy > 24 && speed > 0.5)) leave();
      else setDrag(null);
    },
    onPointerCancel: () => {
      start.current = null;
      setDrag(null);
    },
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || typing(e.target)) return;
      // The details panel takes Escape first.
      if (document.querySelector('aside[aria-label="Operation details"]:not([inert])')) return;
      leave();
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  });

  const line = changesStateLine(run, counts, branch, replaced);
  const plan = download.plan;

  let content: ReactNode;
  if (download.status === "error" || (download.status !== "ready" && !plan)) {
    content = (
      <Placeholder>
        <DownloadProgress state={download} retry={download.start} />
      </Placeholder>
    );
  } else if (!plan || !views.views || !settled) {
    content = (
      <Placeholder outline sentence={plan ? changesSentence(plan, run.phase) : undefined}>
        {plan && settled && (
          <p className="text-foreground-muted text-sm tabular-nums">
            Preparing {fmt(views.done)} of {fmt(plan.operations.length)} operations
          </p>
        )}
      </Placeholder>
    );
  } else {
    content = (
      <div className="flex min-h-0 flex-1 flex-col gap-3">
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
          <p className="max-w-[80ch] text-pretty text-foreground-muted text-sm">
            {changesSentence(plan, run.phase)}
          </p>
          <details className="group text-sm">
            <summary className="cursor-pointer text-accent hover:underline">Plan record</summary>
            <div className="absolute inset-x-4 z-10 mt-2 rounded-xl border bg-card p-4 shadow-card sm:left-auto sm:w-[36rem]">
              <PlanFacts plan={plan} runBranch={run.destination_branch ?? null} />
            </div>
          </details>
        </div>
        <ChangesReview
          views={views.views}
          plan={plan}
          done={run.phase === "applied"}
          filter={filter}
          onFilterChange={onFilterChange}
          shiftTargetId={COLUMN_ID}
        />
      </div>
    );
  }

  return createPortal(
    <div data-changes-sheet="">
      <div
        aria-hidden="true"
        onClick={leave}
        className="fixed inset-0 z-[44] bg-black/35"
        style={{ opacity: progress, transition: timing === "none" ? "none" : `opacity ${timing}` }}
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        ref={sheet}
        className="fixed inset-x-0 top-3 bottom-0 z-[45] flex flex-col overflow-hidden rounded-t-2xl border-t bg-background shadow-[0_-8px_32px_-12px_rgb(0_0_0/0.25)]"
        style={{
          // No transform once open and still: a transform makes the fixed details panel inside the
          // sheet position itself from the sheet instead of the window.
          transform:
            drag != null ? `translateY(${drag}px)` : phase === "open" ? "none" : "translateY(100%)",
          transition: timing === "none" ? "none" : `transform ${timing}`,
          viewTransitionName: morph ? "changes-surface" : undefined,
        }}
      >
        <div className="relative touch-none select-none border-b" {...grab}>
          <span
            aria-hidden="true"
            className="absolute top-1.5 left-1/2 h-1 w-9 -translate-x-1/2 rounded-full bg-border-strong"
          />
          <div className="mx-auto flex h-14 max-w-[1200px] cursor-grab items-center gap-3 px-4 pt-1.5 active:cursor-grabbing">
            <span className="flex min-w-0 flex-col">
              <h2
                id={titleId}
                ref={heading}
                tabIndex={-1}
                className="w-fit font-semibold text-[15px] tabular-nums leading-5 outline-none"
                style={{ viewTransitionName: morph ? "changes-title" : undefined }}
              >
                {changesTitle(counts.total)}
              </h2>
              <span className="flex min-w-0 gap-1.5 text-subtle text-xs">
                {line && (
                  <span
                    className="w-fit shrink-0"
                    style={{ viewTransitionName: morph ? "changes-state" : undefined }}
                  >
                    {line}
                  </span>
                )}
                <span className="truncate font-mono">{run.run_id}</span>
              </span>
            </span>
            <SplitWords counts={counts} onPage morph={morph} className="mx-6 hidden md:flex" />
            <span className="ml-auto hidden items-center gap-1.5 text-subtle text-xs sm:flex">
              <kbd className="rounded-md border border-border-strong bg-content px-1 font-mono text-[11px] text-subtle">
                Esc
              </kbd>
              to return to the run
            </span>
            <button
              type="button"
              onClick={leave}
              aria-label="Close the changes"
              className={cn(
                "relative grid size-8 cursor-pointer place-items-center rounded-md text-foreground-muted transition-colors duration-150 ease-out before:absolute before:-inset-1.5 hover:bg-content-muted hover:text-foreground motion-reduce:transition-none",
                FOCUS
              )}
            >
              <XIcon className="size-4" />
            </button>
          </div>
        </div>
        <div className="min-h-0 flex-1">
          <div
            id={COLUMN_ID}
            className="mx-auto flex h-full max-w-[1200px] flex-col px-4 pt-4 pb-3"
          >
            {content}
          </div>
        </div>
      </div>
    </div>,
    document.body
  );
}

/** The sheet's body before the list: the download, or an outline of the list while it is set up. */
function Placeholder({
  children,
  outline,
  sentence,
}: {
  children?: ReactNode;
  outline?: boolean;
  sentence?: string;
}) {
  if (!outline)
    return (
      <div className="flex min-h-0 flex-1 flex-col items-start gap-3 rounded-2xl border bg-card p-6 shadow-card">
        {children}
      </div>
    );
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      <p className="max-w-[80ch] text-pretty text-foreground-muted text-sm">{sentence ?? " "}</p>
      <div aria-hidden="true" className="h-10" />
      <div className="flex min-h-0 flex-1 flex-col rounded-2xl border bg-card shadow-card">
        <div className="h-9 rounded-t-2xl border-b bg-content-muted" />
        <div className="p-6">{children}</div>
      </div>
    </div>
  );
}
