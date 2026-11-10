import {
  type RefObject,
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { cn } from "tailwind-variants";

import { ChevronRightIcon, XIcon } from "#/components/icons";
import { FOCUS_INSET } from "#/components/page";
import { CHANGE_TONE } from "#/components/run-status";
import { fmt } from "#/domain/run-state";
import { type Action, levelLabel } from "#/features/runs/plan-values";

import { LedgerDiff } from "./LedgerDiff";
import { Kbd, reducedMotion, Sign } from "./parts";
import type { OperationView, RowContext } from "./views";

const CONTENT = 1200;
const EDGE = 12;
const ENTER_MS = 260;
const EXIT_MS = 180;
const ENTER_EASE = "cubic-bezier(0.32, 0.72, 0, 1)";
const EXIT_EASE = "cubic-bezier(0.4, 0, 1, 1)";
const MIN_PANEL = 400;
const MIN_PAGE = 640;
const WIDTH_KEY = "changes-panel-width";

const ACTION_WORD: Record<Action, string> = {
  create: "Create",
  update: "Update",
  delete: "Not deleted",
};

const subscribe = (cb: () => void) => {
  window.addEventListener("resize", cb);
  return () => window.removeEventListener("resize", cb);
};
/**
 * The width the page lays out in. The body's width, not the root's: while the sheet locks the
 * scroll, the root reports the reserved scrollbar space as its own, the body does not.
 */
const useViewportWidth = () => useSyncExternalStore(subscribe, () => document.body.clientWidth);

interface Placement {
  /** Distance from the right edge of the window. */
  right: number;
  width: number;
  /** The panel covers part of the page instead of sitting beside it. */
  overlay: boolean;
  /** Margin the page needs on its right so the panel sits beside it; null leaves the page centred. */
  pageRight: number | null;
}

const defaultWidth = (vw: number) => Math.min(720, Math.max(560, Math.round(vw * 0.4)));
const maxWidth = (vw: number) => Math.max(MIN_PANEL, vw - EDGE - MIN_PAGE);

/** Beside the page in its right margin when there is room, else beside the page moved left, else over it. */
function place(vw: number, wish: number | null): Placement {
  const gutter = (vw - CONTENT) / 2;
  if (vw < 768) return { right: 0, width: vw, overlay: true, pageRight: null };
  const width = Math.min(maxWidth(vw), Math.max(MIN_PANEL, wish ?? defaultWidth(vw)));
  if (gutter - EDGE >= width)
    return { right: gutter - width, width, overlay: false, pageRight: null };
  if (vw - (EDGE + width) >= MIN_PAGE)
    return { right: EDGE, width, overlay: false, pageRight: EDGE + width };
  return { right: 0, width: Math.min(width, vw), overlay: true, pageRight: null };
}

/**
 * Moves the element with the given id left so the panel sits beside it. The margins are written in
 * pixels even when the element is centred, because a transition cannot start from `auto`; they only
 * transition when the panel opens or closes, so resizing the window does not lag.
 */
function usePageShift(targetId: string, vw: number, pageRight: number | null) {
  const previous = useRef(pageRight);
  useLayoutEffect(() => {
    const target = document.getElementById(targetId);
    if (!target) return;
    const centred = Math.max(0, (vw - CONTENT) / 2);
    const moved = (previous.current == null) !== (pageRight == null);
    previous.current = pageRight;
    const [ms, ease] = pageRight == null ? [EXIT_MS, EXIT_EASE] : [ENTER_MS, ENTER_EASE];
    target.style.transition =
      moved && !reducedMotion() ? `margin-left ${ms}ms ${ease}, margin-right ${ms}ms ${ease}` : "";
    target.style.marginLeft = `${pageRight == null ? centred : Math.max(0, vw - CONTENT - pageRight)}px`;
    target.style.marginRight = `${pageRight ?? centred}px`;
  }, [targetId, vw, pageRight]);
  useEffect(
    () => () => {
      const target = document.getElementById(targetId);
      if (target) target.style.marginLeft = target.style.marginRight = target.style.transition = "";
    },
    [targetId]
  );
}

/** Keeps the panel mounted while it slides out, and mounts it one frame early so it can slide in. */
function usePresence(open: boolean) {
  const [mounted, setMounted] = useState(open);
  const [visible, setVisible] = useState(open);
  useEffect(() => {
    if (reducedMotion()) {
      setMounted(open);
      setVisible(open);
      return;
    }
    if (open) {
      setMounted(true);
      let second = 0;
      const first = requestAnimationFrame(() => {
        second = requestAnimationFrame(() => setVisible(true));
      });
      return () => {
        cancelAnimationFrame(first);
        cancelAnimationFrame(second);
      };
    }
    setVisible(false);
    const t = window.setTimeout(() => setMounted(false), EXIT_MS);
    return () => window.clearTimeout(t);
  }, [open]);
  return { mounted, visible };
}

/** The top and bottom of an element in the window, kept current while anything scrolls. */
function useEdges(ref: RefObject<HTMLElement | null>, on: boolean) {
  const [edges, setEdges] = useState<{ top: number; bottom: number } | null>(null);
  useLayoutEffect(() => {
    if (!on) return;
    let frame = 0;
    const measure = () => {
      frame = 0;
      const r = ref.current?.getBoundingClientRect();
      if (!r) return;
      const top = Math.max(61, Math.round(r.top));
      const bottom = Math.max(12, Math.round(window.innerHeight - r.bottom));
      setEdges((e) => (e && e.top === top && e.bottom === bottom ? e : { top, bottom }));
    };
    const later = () => {
      if (!frame) frame = requestAnimationFrame(measure);
    };
    measure();
    window.addEventListener("scroll", later, { capture: true, passive: true });
    window.addEventListener("resize", later);
    const ro = new ResizeObserver(later);
    if (ref.current) ro.observe(ref.current);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("scroll", later, { capture: true });
      window.removeEventListener("resize", later);
      ro.disconnect();
    };
  }, [ref, on]);
  return on ? edges : null;
}

function readWidth(): number | null {
  try {
    return Number(localStorage.getItem(WIDTH_KEY)) || null;
  } catch {
    return null;
  }
}

function saveWidth(width: number | null) {
  try {
    if (width == null) localStorage.removeItem(WIDTH_KEY);
    else localStorage.setItem(WIDTH_KEY, String(width));
  } catch {}
}

/** Drag the panel's left edge, or focus it and press the arrow keys, to change the panel's width. */
function ResizeHandle({
  width,
  max,
  onChange,
}: {
  width: number;
  max: number;
  onChange: (w: number | null, save?: boolean) => void;
}) {
  const start = useRef<{ x: number; w: number } | null>(null);
  const [dragging, setDragging] = useState(false);
  return (
    // biome-ignore lint/a11y/useSemanticElements: a window splitter is a focusable separator
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label="Panel width. Drag, or press the left and right arrow keys."
      aria-valuenow={Math.round(width)}
      aria-valuemin={MIN_PANEL}
      aria-valuemax={Math.round(max)}
      tabIndex={0}
      onPointerDown={(e) => {
        e.currentTarget.setPointerCapture(e.pointerId);
        start.current = { x: e.clientX, w: width };
        setDragging(true);
      }}
      onPointerMove={(e) => {
        if (start.current) onChange(start.current.w + start.current.x - e.clientX);
      }}
      onPointerUp={() => {
        start.current = null;
        setDragging(false);
        onChange(width, true);
      }}
      onDoubleClick={() => onChange(null, true)}
      onKeyDown={(e) => {
        const step = e.shiftKey ? 64 : 16;
        if (e.key === "ArrowLeft") onChange(width + step, true);
        else if (e.key === "ArrowRight") onChange(width - step, true);
        else return;
        e.preventDefault();
      }}
      className={cn(
        "group absolute inset-y-0 left-0 z-10 w-2.5 cursor-col-resize touch-none outline-none",
        "after:absolute after:inset-y-3 after:left-0.5 after:w-0.5 after:rounded-full after:transition-colors after:duration-150",
        dragging
          ? "after:bg-accent"
          : "after:bg-transparent hover:after:bg-border-strong focus-visible:after:bg-accent"
      )}
    />
  );
}

const ICON_BUTTON =
  "relative grid size-8 shrink-0 cursor-pointer place-items-center rounded-lg text-subtle transition-colors duration-150 ease-out hover:bg-highlight hover:text-foreground disabled:cursor-default disabled:opacity-40 disabled:hover:bg-transparent pointer-coarse:size-11 motion-reduce:transition-none";

export interface DetailsPanelProps {
  /** The selected operation; undefined closes the panel. */
  view: OperationView | undefined;
  /** The list's rows in view order, for the position and the previous and next buttons. */
  rows: OperationView[];
  onSelect: (id: string | null) => void;
  ctx: RowContext;
  /** The list's card: the panel lines up with its top and bottom. */
  alignRef: RefObject<HTMLElement | null>;
  /** The id of the element the panel moves left to make room for itself. */
  shiftTargetId: string;
}

/**
 * The details of the selected operation, in a panel on the right. The page moves left to make room
 * instead of being covered; on narrow windows the panel covers it. Esc closes the panel.
 */
export function DetailsPanel({
  view,
  rows,
  onSelect,
  ctx,
  alignRef,
  shiftTargetId,
}: DetailsPanelProps) {
  const vw = useViewportWidth();
  const open = view != null;
  const [wish, setWish] = useState<number | null>(readWidth);
  const p = place(vw, wish);
  const resize = useCallback(
    (w: number | null, save?: boolean) => {
      const next = w == null ? null : Math.min(maxWidth(vw), Math.max(MIN_PANEL, Math.round(w)));
      setWish(next);
      if (save) saveWidth(next);
    },
    [vw]
  );
  usePageShift(shiftTargetId, vw, open ? p.pageRight : null);
  const { mounted, visible } = usePresence(open);
  const edges = useEdges(alignRef, mounted);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (e.key !== "Escape" || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)) return;
      onSelect(null);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onSelect]);

  // While the panel slides out, it keeps showing the operation it closed on.
  const last = useRef(view);
  if (view) last.current = view;
  const current = view ?? last.current;
  if (!mounted || !current) return null;

  const at = rows.findIndex((v) => v.op.operation_id === current.op.operation_id);
  const go = (i: number) => rows[i] && onSelect(rows[i].op.operation_id);

  return (
    <aside
      aria-label="Operation details"
      inert={!open}
      style={{
        right: p.right,
        width: p.width,
        transitionDuration: `${visible ? ENTER_MS : EXIT_MS}ms`,
        transitionTimingFunction: visible ? ENTER_EASE : EXIT_EASE,
        ...(edges && !p.overlay ? edges : {}),
      }}
      className={cn(
        "fixed z-[46] flex flex-col overflow-hidden bg-content transition-[translate,opacity] motion-reduce:transition-none",
        p.overlay
          ? "top-[49px] bottom-0 border-l shadow-[-8px_0_32px_-8px_rgb(0_0_0/0.18)]"
          : cn(
              !edges && "top-[61px] bottom-3",
              "rounded-xl border shadow-[0_1px_2px_rgb(0_0_0/0.04),0_8px_24px_-12px_rgb(0_0_0/0.12)]"
            ),
        visible
          ? "translate-x-0 opacity-100"
          : p.overlay
            ? "translate-x-full"
            : "translate-x-4 opacity-0"
      )}
    >
      {!p.overlay && <ResizeHandle width={p.width} max={maxWidth(vw)} onChange={resize} />}
      <div className="flex flex-col gap-1 border-b px-4 pt-2 pb-3">
        <div className="flex items-center gap-2">
          <Sign action={current.op.action} />
          <span className={cn("font-medium text-xs", CHANGE_TONE[current.op.action].text)}>
            {ACTION_WORD[current.op.action]}
          </span>
          <span className="min-w-0 truncate font-mono text-subtle text-xs">{current.op.kind}</span>
          <span className="ml-auto flex shrink-0 items-center">
            {at >= 0 && (
              <>
                <span className="mr-1 text-subtle text-xs tabular-nums">
                  {fmt(at + 1)} of {fmt(rows.length)}
                </span>
                <button
                  type="button"
                  aria-label="Previous operation"
                  disabled={at <= 0}
                  onClick={() => go(at - 1)}
                  className={cn(ICON_BUTTON, FOCUS_INSET)}
                >
                  <ChevronRightIcon className="size-3.5 -rotate-90" />
                </button>
                <button
                  type="button"
                  aria-label="Next operation"
                  disabled={at >= rows.length - 1}
                  onClick={() => go(at + 1)}
                  className={cn(ICON_BUTTON, FOCUS_INSET)}
                >
                  <ChevronRightIcon className="size-3.5 rotate-90" />
                </button>
              </>
            )}
            <button
              type="button"
              aria-label="Close the details"
              onClick={() => onSelect(null)}
              className={cn(ICON_BUTTON, FOCUS_INSET)}
            >
              <XIcon />
            </button>
          </span>
        </div>
        <h2 className="font-semibold text-base tracking-tight [overflow-wrap:anywhere]">
          {current.name}
        </h2>
        {current.levels.length > 0 && (
          <p className="text-foreground-muted text-xs [overflow-wrap:anywhere]">
            in {current.levels.map(levelLabel).join(" › ")}
          </p>
        )}
      </div>
      <div className="min-h-0 flex-1 overflow-auto overscroll-contain px-4 py-3">
        <LedgerDiff view={current} ctx={ctx} />
      </div>
      <p className="hidden items-center gap-1 border-t bg-content-muted px-4 py-2 text-subtle text-xs sm:flex">
        <Kbd>↑</Kbd>
        <Kbd>↓</Kbd> in the list move this panel, <Kbd>Esc</Kbd> closes it
      </p>
    </aside>
  );
}
