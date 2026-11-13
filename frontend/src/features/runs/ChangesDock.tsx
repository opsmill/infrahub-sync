import { useEffect } from "react";
import { createPortal } from "react-dom";
import { cn } from "tailwind-variants";

import type { Run } from "#/api/types";
import { FOCUS } from "#/components/page";
import { fmt } from "#/domain/run-state";

import type { Action } from "./plan-values";

/** A plan's counts, from the run record: the dock and the header never download the plan. */
export interface ChangeCounts {
  create: number;
  update: number;
  delete: number;
  total: number;
  /** False when the plan did not compute deletes. */
  deletesComputed?: boolean;
}

/** What a run can show of its changes. */
export type ChangesState =
  | { kind: "list"; counts: ChangeCounts }
  | { kind: "pending" }
  | { kind: "none" }
  | { kind: "unavailable"; counts: ChangeCounts };

const ACTIONS: Action[] = ["create", "update", "delete"];
const MARK: Record<Action, string> = {
  create: "bg-(--change-create)",
  update: "bg-(--change-update)",
  delete: "bg-(--change-delete)",
};
const NOUN: Record<Action, [string, string]> = {
  create: ["create", "creates"],
  update: ["update", "updates"],
  delete: ["not deleted", "not deleted"],
};
const compact = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });
const parts = (c: ChangeCounts) => ACTIONS.filter((a) => c[a] > 0);
const share = (c: ChangeCounts, a: Action) => Math.round((c[a] / c.total) * 100);
const noun = (c: ChangeCounts, a: Action) => NOUN[a][c[a] === 1 ? 0 : 1];

/** A plan that lists only deletes: apply never deletes, so it has nothing to write. */
export const isDeleteOnly = (c: ChangeCounts) => c.total > 0 && c.create + c.update === 0;

/**
 * A run writes once: only a plan waiting for review has a decision ahead; the others are history,
 * and so is a plan that a newer plan replaced.
 */
const awaitsReview = (run: Run, c: ChangeCounts, replaced: boolean) =>
  !replaced && run.phase === "planned" && run.outcome === "planned" && !isDeleteOnly(c);

/** Where the plan's changes stand, in the words of the run page. */
export function changesStateLine(
  run: Run,
  c: ChangeCounts,
  branch?: string | null,
  replaced = false
): string {
  if (isDeleteOnly(c)) return "Nothing to apply";
  if (replaced) return "Replaced by a newer plan";
  const to = branch ? ` to ${branch}` : "";
  if (run.phase === "planned" && run.outcome === "planned") return `Waiting for review${to}`;
  if (run.phase === "applied") return `Applied${to}`;
  return branch ? `To ${branch}` : "";
}

export const changesTitle = (total: number) =>
  `${fmt(total)} ${total === 1 ? "change" : "changes"}`;

/** The split of the plan by action, with its legend in words. */
export function SplitWords({
  counts,
  onPage,
  morph,
  className,
}: {
  counts: ChangeCounts;
  /** Drawn on the page's own surface (the sheet's header) instead of the dark dock. */
  onPage?: boolean;
  morph?: boolean;
  className?: string;
}) {
  return (
    <div className={cn("flex min-w-80 flex-col gap-1.5", className)}>
      <span
        className={cn(
          "flex h-2 gap-[3px] rounded-full p-px",
          onPage ? "bg-content-strong" : "bg-(--dock-faint)"
        )}
        style={{ viewTransitionName: morph ? "changes-bar" : undefined }}
      >
        {parts(counts).map((a) => (
          <span
            key={a}
            title={`${fmt(counts[a])} ${noun(counts, a)}, ${share(counts, a)}%`}
            className={cn("h-full min-w-1 rounded-full", MARK[a])}
            style={{ flexGrow: counts[a] }}
          />
        ))}
      </span>
      <span
        className={cn(
          "flex justify-between gap-x-4 text-[11px] tabular-nums",
          onPage ? "text-subtle" : "text-(--dock-muted)"
        )}
      >
        {parts(counts).map((a) => (
          <span key={a} className="inline-flex items-center gap-1.5">
            <span aria-hidden="true" className={cn("size-2 rounded-full", MARK[a])} />
            <span className={onPage ? "text-foreground" : "text-(--dock-fg)"}>
              {compact.format(counts[a])}
            </span>
            {noun(counts, a)}
          </span>
        ))}
        {counts.deletesComputed === false && counts.delete === 0 && (
          <span>deletes not computed</span>
        )}
      </span>
    </div>
  );
}

/** C opens the changes from anywhere on the run page, except while typing. */
export function useChangesShortcut(open: () => void, enabled: boolean) {
  useEffect(() => {
    if (!enabled) return;
    const onKey = (e: KeyboardEvent) => {
      const t = e.target;
      if (e.key !== "c" || e.metaKey || e.ctrlKey || e.altKey || e.repeat) return;
      if (
        t instanceof HTMLElement &&
        (/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) || t.isContentEditable)
      )
        return;
      if (document.querySelector("[role=dialog]")) return;
      e.preventDefault();
      open();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  });
}

/**
 * The plan's changes, floating at the bottom of the window on every scroll position. Dark in both
 * themes; it grows into the changes sheet through a view transition (`morph`).
 */
export function ChangesDock({
  run,
  state,
  branch,
  onOpen,
  morph,
  replaced = false,
}: {
  run: Run;
  state: ChangesState;
  branch?: string | null;
  onOpen: () => void;
  morph?: boolean;
  /** A newer plan replaced this one, so it no longer waits for review. */
  replaced?: boolean;
}) {
  let body: React.ReactNode;
  if (state.kind === "pending" || state.kind === "none") {
    body = (
      <div className="flex items-center gap-3 py-3 pr-5 pl-4">
        {state.kind === "pending" ? (
          <span
            aria-hidden="true"
            className="size-3.5 animate-spin rounded-full border-2 border-(--dock-faint) border-t-(--dock-fg) motion-reduce:animate-none"
          />
        ) : (
          <span aria-hidden="true" className="size-2 rounded-full bg-(--change-create)" />
        )}
        <span className="flex flex-col">
          <span className="font-medium text-sm">
            {state.kind === "pending" ? "Planning" : "No differences"}
          </span>
          <span className="text-(--dock-muted) text-xs">
            {state.kind === "pending"
              ? "The changes appear here when the plan is saved."
              : "The plan found nothing to create, update or delete."}
          </span>
        </span>
      </div>
    );
  } else {
    const c = state.counts;
    const line = changesStateLine(run, c, branch, replaced);
    body = (
      <div className="flex items-center gap-5 p-1.5 pl-4">
        <span className="flex min-w-0 flex-col">
          <span
            className="w-fit font-medium text-sm tabular-nums tracking-[-0.005em]"
            style={{ viewTransitionName: morph ? "changes-title" : undefined }}
          >
            {changesTitle(c.total)}
          </span>
          {line && (
            <span
              className="w-fit max-w-full truncate text-(--dock-muted) text-xs"
              style={{ viewTransitionName: morph ? "changes-state" : undefined }}
            >
              {line}
            </span>
          )}
        </span>
        <SplitWords counts={c} morph={morph} className="hidden md:flex" />
        {state.kind === "unavailable" ? (
          <span className="max-w-36 py-1.5 pr-2.5 text-right text-(--dock-muted) text-xs leading-4">
            The saved plan is no longer available
          </span>
        ) : (
          <button
            type="button"
            onClick={onOpen}
            aria-keyshortcuts="C"
            className={cn(
              "inline-flex h-9 shrink-0 cursor-pointer items-center gap-2 rounded-xl bg-white/10 px-3.5 font-medium text-sm shadow-[inset_0_0_0_1px_rgb(255_255_255/0.1)] transition-[background-color,scale] duration-150 ease-out hover:bg-white/16 active:scale-[0.97] motion-reduce:transition-none",
              FOCUS
            )}
          >
            {awaitsReview(run, c, replaced) ? "Review" : "View changes"}
            <kbd className="rounded-[5px] border border-white/22 px-1 font-mono text-[11px] text-white/60 leading-4">
              C
            </kbd>
          </button>
        )}
      </div>
    );
  }
  return createPortal(
    <div className="pointer-events-none fixed inset-x-0 bottom-4 z-30 flex justify-center px-4 pb-[env(safe-area-inset-bottom)]">
      <section
        aria-label="Changes"
        className="changes-dock pointer-events-auto relative max-w-full rounded-[18px]"
        style={{ viewTransitionName: morph ? "changes-surface" : undefined }}
      >
        {body}
      </section>
    </div>,
    document.body
  );
}

const BLOCKS = 5;

/** Five squares split like the plan, as GitHub draws the size of a pull request. */
function blocks(c: ChangeCounts): string[] {
  const out: string[] = [];
  for (const a of ACTIONS) {
    const k = c[a] > 0 ? Math.max(1, Math.round((c[a] / c.total) * BLOCKS)) : 0;
    for (let i = 0; i < k && out.length < BLOCKS; i++) out.push(MARK[a]);
  }
  while (out.length < BLOCKS) out.push("bg-content-strong");
  return out;
}

/** The plan's counts in the run header's details line; they open the changes. */
export function ChangeCount({ counts, onOpen }: { counts: ChangeCounts; onOpen: () => void }) {
  const sign: Record<Action, string> = { create: "+", update: "~", delete: "−" };
  return (
    <button
      type="button"
      onClick={onOpen}
      aria-keyshortcuts="C"
      aria-label={`View the ${changesTitle(counts.total)}: ${parts(counts)
        .map((a) => `${fmt(counts[a])} ${noun(counts, a)}`)
        .join(", ")}`}
      className={cn(
        "group inline-flex cursor-pointer items-center gap-2 rounded-sm tabular-nums",
        FOCUS
      )}
    >
      <span className="inline-flex items-baseline gap-1.5 decoration-foreground-muted underline-offset-4 group-hover:underline">
        {parts(counts).map((a) => (
          <span key={a} className={a === "delete" ? "text-foreground-muted" : "font-medium"}>
            <span
              className={
                a === "create"
                  ? "text-(--change-create)"
                  : a === "update"
                    ? "text-(--change-update)"
                    : "text-(--change-delete)"
              }
            >
              {sign[a]}
            </span>
            {fmt(counts[a])}
          </span>
        ))}
      </span>
      <span aria-hidden="true" className="flex gap-0.5">
        {blocks(counts).map((bg, i) => (
          <span key={i} className={cn("size-2 rounded-[2px]", bg)} />
        ))}
      </span>
    </button>
  );
}
