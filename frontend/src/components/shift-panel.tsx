import {
  type ReactNode,
  type RefObject,
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { cn } from "tailwind-variants";

const CONTENT = 1200;
const EDGE = 12;
const ENTER_MS = 260;
const EXIT_MS = 180;
const ENTER_EASE = "cubic-bezier(0.32, 0.72, 0, 1)";
const EXIT_EASE = "cubic-bezier(0.4, 0, 1, 1)";
const MIN_PANEL = 400;
const MIN_PAGE = 640;

/** An icon button in the header of a shift panel. */
export const PANEL_ICON_BUTTON =
  "relative grid size-8 shrink-0 cursor-pointer place-items-center rounded-lg text-subtle transition-colors duration-150 ease-out hover:bg-highlight hover:text-foreground disabled:cursor-default disabled:opacity-40 disabled:hover:bg-transparent aria-disabled:pointer-events-none aria-disabled:opacity-40 pointer-coarse:size-11 motion-reduce:transition-none";

const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

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
function useEdges(ref: RefObject<HTMLElement | null> | undefined, on: boolean) {
  const [edges, setEdges] = useState<{ top: number; bottom: number } | null>(null);
  useLayoutEffect(() => {
    if (!on || !ref) return;
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
  return on && ref ? edges : null;
}

function readWidth(key: string): number | null {
  try {
    return Number(localStorage.getItem(key)) || null;
  } catch {
    return null;
  }
}

function saveWidth(key: string, width: number | null) {
  try {
    if (width == null) localStorage.removeItem(key);
    else localStorage.setItem(key, String(width));
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

export interface ShiftPanelProps {
  open: boolean;
  /** The panel's accessible name. */
  label: string;
  onClose: () => void;
  /** The id of the element the panel moves left to make room for itself. */
  shiftTargetId: string;
  /** The `localStorage` key that keeps the width a person dragged the panel to. */
  widthKey: string;
  /** An element the panel lines up with, top and bottom. Without it the panel fills the window's height. */
  alignRef?: RefObject<HTMLElement | null>;
  children: ReactNode;
}

/**
 * A panel on the right of the window. The page moves left to make room instead of being covered; on
 * narrow windows the panel covers it. Esc closes the panel. While the panel slides out, it keeps
 * showing what it closed on.
 */
export function ShiftPanel({
  open,
  label,
  onClose,
  shiftTargetId,
  widthKey,
  alignRef,
  children,
}: ShiftPanelProps) {
  const vw = useViewportWidth();
  const [wish, setWish] = useState<number | null>(() => readWidth(widthKey));
  const p = place(vw, wish);
  const resize = useCallback(
    (w: number | null, save?: boolean) => {
      const next = w == null ? null : Math.min(maxWidth(vw), Math.max(MIN_PANEL, Math.round(w)));
      setWish(next);
      if (save) saveWidth(widthKey, next);
    },
    [vw, widthKey]
  );
  usePageShift(shiftTargetId, vw, open ? p.pageRight : null);
  const { mounted, visible } = usePresence(open);
  const edges = useEdges(alignRef, mounted);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (e.key !== "Escape" || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)) return;
      // A list or a dialog that is open closes first.
      if (t.closest('[role="listbox"],[role="dialog"]')) return;
      onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const last = useRef(children);
  if (open) last.current = children;
  if (!mounted) return null;

  return (
    <aside
      aria-label={label}
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
      {open ? children : last.current}
    </aside>
  );
}
