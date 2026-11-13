import { type RefObject, useCallback, useLayoutEffect, useState } from "react";

/**
 * A virtual list of rows of one height inside its own scroll box: only the rows in view, plus
 * `overscan` on each side, are rendered.
 */
export function useVirtual({
  count,
  rowHeight,
  scrollRef,
  overscan = 10,
}: {
  count: number;
  rowHeight: number;
  scrollRef: RefObject<HTMLElement | null>;
  overscan?: number;
}) {
  const [view, setView] = useState({ top: 0, height: 800 });

  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const update = () => setView({ top: el.scrollTop, height: el.clientHeight });
    update();
    el.addEventListener("scroll", update, { passive: true });
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => {
      el.removeEventListener("scroll", update);
      ro.disconnect();
    };
  }, [scrollRef]);

  const first = Math.min(count, Math.floor(view.top / rowHeight));
  const start = Math.max(0, first - overscan);
  const end = Math.min(count, Math.ceil((view.top + view.height) / rowHeight) + overscan);

  const scrollToIndex = useCallback(
    (i: number) => {
      const el = scrollRef.current;
      if (!el) return;
      const top = i * rowHeight;
      const bottom = top + rowHeight;
      const pad = 40;
      if (top < el.scrollTop + pad) el.scrollTop = Math.max(0, top - pad);
      else if (bottom > el.scrollTop + el.clientHeight) el.scrollTop = bottom - el.clientHeight;
    },
    [rowHeight, scrollRef]
  );

  const items: { index: number; start: number }[] = [];
  for (let i = start; i < end; i++) items.push({ index: i, start: i * rowHeight });
  return { items, total: count * rowHeight, scrollToIndex, first };
}
