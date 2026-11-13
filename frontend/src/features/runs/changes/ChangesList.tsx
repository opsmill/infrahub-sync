import { Card } from "@infrahub/ui";
import {
  type KeyboardEvent,
  type RefObject,
  useDeferredValue,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { cn } from "tailwind-variants";

import { FilterTabs, FOCUS_INSET, SearchField } from "#/components/page";
import { fmt } from "#/domain/run-state";
import { ACTIONS, type Action, fieldText, levelLabel } from "#/features/runs/plan-values";

import { Kbd, Sign } from "./parts";
import { useVirtual } from "./useVirtual";
import {
  type ChangesFilter,
  changedFields,
  kindOrder,
  type OperationView,
  remember,
} from "./views";

type SortKey = "write" | "kind" | "node" | "action";

const ROW_H =
  typeof window !== "undefined" && window.matchMedia("(pointer: coarse)").matches ? 44 : 36;
const GRID =
  "grid grid-cols-[16px_minmax(0,1fr)] gap-x-3 sm:grid-cols-[16px_11rem_minmax(0,1fr)_minmax(0,0.8fr)]";

export const rowId = (v: OperationView) => `change-row-${v.op.operation_id}`;

function changeText(v: OperationView) {
  if (v.op.action === "create") return `${v.fields.length} fields`;
  if (v.op.action === "delete") return "Not in the source";
  const ch = changedFields(v);
  if (ch.length === 1) return `${ch[0].name}: ${fieldText(ch[0])}`;
  if (ch.length > 1) return ch.map((f) => f.name).join(", ");
  return "Changed fields not recorded";
}

export interface ChangesListProps {
  views: OperationView[];
  filter: ChangesFilter;
  onFilterChange: (filter: ChangesFilter) => void;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  /** Called with the rows in view order whenever the filter, the search or the sort change them. */
  onRows: (rows: OperationView[]) => void;
  /** The list fills the height its parent gives it, instead of a fixed height. */
  fill?: boolean;
  /** The card that holds the column headings and the rows, so the details panel can line up with it. */
  cardRef?: RefObject<HTMLDivElement | null>;
}

/** Every operation of the plan in one list: search, a kind menu, action tabs and a virtual list. */
export function ChangesList({
  views,
  filter,
  onFilterChange,
  selectedId,
  onSelect,
  onRows,
  fill,
  cardRef,
}: ChangesListProps) {
  const kinds = useMemo(() => remember(views, "kinds", () => kindOrder(views)), [views]);
  const [query, setQuery] = useState("");
  const needle = useDeferredValue(query).trim().toLowerCase();
  const action: Action | "all" = filter.action ?? "all";
  const kind = filter.kind ?? "all";
  const setAction = (a: Action | "all") =>
    onFilterChange({ kind: filter.kind, action: a === "all" ? undefined : a });
  const setKind = (k: string) =>
    onFilterChange({ kind: k === "all" ? undefined : k, action: filter.action });
  const [sort, setSort] = useState<SortKey>("write");

  const matches = useMemo(
    () => (needle ? views.filter((v) => v.search.includes(needle)) : views),
    [views, needle]
  );
  const counts = useMemo(
    () =>
      remember(matches, `counts:${kind}`, () => {
        const c = { all: 0, create: 0, update: 0, delete: 0 };
        for (const v of matches) {
          if (kind !== "all" && v.op.kind !== kind) continue;
          c[v.op.action] += 1;
          c.all += 1;
        }
        return c;
      }),
    [matches, kind]
  );
  const rows = useMemo(
    () =>
      remember(matches, `rows:${action}:${kind}:${sort}`, () => {
        const order = new Map(kinds.map((k, i) => [k.kind, i]));
        const list = matches.filter(
          (v) =>
            (action === "all" || v.op.action === action) && (kind === "all" || v.op.kind === kind)
        );
        const ai = (a: Action) => ACTIONS.indexOf(a);
        if (sort === "write")
          list.sort((a, b) => (order.get(a.op.kind) ?? 0) - (order.get(b.op.kind) ?? 0));
        if (sort === "kind") list.sort((a, b) => a.op.kind.localeCompare(b.op.kind));
        if (sort === "action") list.sort((a, b) => ai(a.op.action) - ai(b.op.action));
        return list;
      }),
    [matches, action, kind, sort, kinds]
  );

  const index = selectedId ? rows.findIndex((v) => v.op.operation_id === selectedId) : -1;
  const selected = index >= 0 ? rows[index] : undefined;

  const scrollRef = useRef<HTMLDivElement>(null);
  const virtual = useVirtual({ count: rows.length, rowHeight: ROW_H, scrollRef, overscan: 14 });

  const go = (i: number) => {
    if (rows.length === 0) return;
    const n = Math.max(0, Math.min(rows.length - 1, i));
    onSelect(rows[n].op.operation_id);
    virtual.scrollToIndex(n);
  };
  const page = Math.max(1, Math.floor((scrollRef.current?.clientHeight ?? 600) / ROW_H) - 1);
  const onKey = (e: KeyboardEvent) => {
    const at = index < 0 ? virtual.first - 1 : index;
    const map: Record<string, number> = {
      ArrowDown: at + 1,
      j: at + 1,
      ArrowUp: at - 1,
      k: at - 1,
      PageDown: at + page,
      PageUp: at - page,
      Home: 0,
      End: rows.length - 1,
    };
    if (e.key in map && !e.metaKey && !e.ctrlKey && !e.altKey) {
      e.preventDefault();
      go(map[e.key]);
    }
    if (e.key === "Escape") onSelect(null);
  };

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = 0;
  }, [needle, action, kind, sort]);

  useEffect(() => onRows(rows), [rows, onRows]);
  const scrollTo = useRef(virtual.scrollToIndex);
  scrollTo.current = virtual.scrollToIndex;
  useEffect(() => {
    if (index >= 0) scrollTo.current(index);
  }, [index]);

  const sortButton = (key: SortKey, label: string) => (
    <button
      type="button"
      onClick={() => setSort(sort === key ? "write" : key)}
      aria-pressed={sort === key}
      className={cn(
        "-mx-1 inline-flex h-7 cursor-pointer items-center gap-1 rounded-md px-1 text-left hover:text-foreground",
        sort === key && "text-foreground",
        FOCUS_INSET
      )}
    >
      {label}
      <span
        aria-hidden="true"
        className={cn("text-[10px]", sort === key ? "opacity-100" : "opacity-0")}
      >
        ▼
      </span>
    </button>
  );

  const first = Math.min(rows.length, virtual.first + 1);
  const last = Math.min(
    rows.length,
    virtual.first + Math.floor((scrollRef.current?.clientHeight ?? 600) / ROW_H)
  );

  return (
    <div className={cn("flex flex-col gap-3", fill && "min-h-0 flex-1")}>
      {/* One line from 680 px of list width: the search takes what is left, the kind menu a fixed width. */}
      <div className="@container">
        <div className="flex flex-col gap-2 @min-[680px]:flex-row @min-[680px]:items-center">
          <div className="min-w-0 @min-[680px]:max-w-md @min-[680px]:flex-1 [&>div]:w-full">
            <SearchField
              value={query}
              onChange={setQuery}
              placeholder="Search kinds, nodes and values"
              label="Search the plan by kind, node, field name, value or node ID"
              count={rows.length}
              noun={["operation matches", "operations match"]}
            />
          </div>
          <label className="relative flex w-full items-center @min-[680px]:w-40 @min-[680px]:shrink-0">
            <span className="sr-only">Kind</span>
            <select
              value={kind}
              onChange={(e) => setKind(e.target.value)}
              className={cn(
                "h-10 w-full min-w-0 cursor-pointer appearance-none truncate rounded-xl border border-input-border bg-input pr-8 pl-3 font-mono text-base shadow-input md:text-[13px]",
                FOCUS_INSET
              )}
            >
              <option value="all">Every kind</option>
              {kinds.map((k) => (
                <option key={k.kind} value={k.kind}>
                  {k.kind}
                </option>
              ))}
            </select>
            <span
              aria-hidden="true"
              className="pointer-events-none absolute right-3 text-[10px] text-subtle"
            >
              ▼
            </span>
          </label>
          <div className="@min-[680px]:ml-auto @min-[680px]:shrink-0">
            <FilterTabs
              label="Show operations"
              value={action}
              onChange={setAction}
              options={[
                { id: "all", label: "All", count: fmt(counts.all) },
                { id: "create", label: "Create", count: fmt(counts.create) },
                { id: "update", label: "Update", count: fmt(counts.update) },
                { id: "delete", label: "Not deleted", count: fmt(counts.delete) },
              ]}
            />
          </div>
        </div>
      </div>

      <div
        className={cn("grid grid-cols-1 gap-3", fill && "min-h-0 flex-1 grid-rows-[minmax(0,1fr)]")}
      >
        <Card ref={cardRef} className="flex min-h-0 min-w-0 flex-col overflow-clip">
          <div
            className={cn(
              GRID,
              "h-9 items-center border-b bg-content-muted px-4 font-medium text-subtle text-xs"
            )}
          >
            <span>
              <span className="sr-only">Action</span>
            </span>
            <span className="hidden sm:block">{sortButton("kind", "Kind")}</span>
            <span>{sortButton("node", "Node")}</span>
            <span className="hidden sm:block">{sortButton("action", "Change")}</span>
          </div>
          <div
            ref={scrollRef}
            role="listbox"
            tabIndex={0}
            aria-label={`Operations, ${fmt(rows.length)}. Use the arrow keys to move.`}
            aria-activedescendant={selected ? rowId(selected) : undefined}
            onKeyDown={onKey}
            className={cn(
              "relative overflow-auto",
              fill ? "min-h-0 flex-1" : "h-[min(68dvh,640px)] overscroll-contain",
              FOCUS_INSET
            )}
          >
            {rows.length === 0 ? (
              <p className="px-6 py-10 text-center text-foreground-muted">No operations match.</p>
            ) : (
              <div className="relative" style={{ height: virtual.total }}>
                {virtual.items.map(({ index: i, start }) => {
                  const v = rows[i];
                  const on = v.op.operation_id === selectedId;
                  const scope = v.levels.map(levelLabel).join(" › ");
                  return (
                    // biome-ignore lint/a11y/useFocusableInteractive: an option of the listbox, which holds the focus
                    <div
                      key={v.op.operation_id}
                      id={rowId(v)}
                      role="option"
                      aria-selected={on}
                      onClick={() => onSelect(v.op.operation_id)}
                      onKeyDown={() => {}}
                      className={cn(
                        GRID,
                        "absolute inset-x-0 top-0 cursor-pointer items-center border-b px-4 text-[13px]",
                        on
                          ? "bg-accent-surface shadow-[inset_2px_0_0_var(--color-accent)]"
                          : "hover:bg-content-muted"
                      )}
                      style={{ height: ROW_H, transform: `translateY(${start}px)` }}
                    >
                      <Sign action={v.op.action} />
                      <span
                        className="hidden truncate font-mono text-foreground-muted text-xs sm:block"
                        title={v.op.kind}
                      >
                        {v.op.kind}
                      </span>
                      <span className="flex min-w-0 items-baseline gap-1.5" title={v.label}>
                        <span
                          className={cn(
                            "max-w-[70%] shrink-0 truncate font-medium",
                            v.op.action === "delete" ? "text-foreground-muted" : "text-foreground"
                          )}
                        >
                          {v.name}
                        </span>
                        {scope && (
                          <span className="min-w-0 truncate text-subtle text-xs">{scope}</span>
                        )}
                      </span>
                      <span
                        className="hidden min-w-0 truncate text-subtle text-xs sm:block"
                        title={changeText(v)}
                      >
                        {changeText(v)}
                      </span>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
          <div className="flex h-8 items-center justify-between gap-3 border-t bg-content-muted px-4 text-subtle text-xs tabular-nums">
            <span>
              {rows.length > 0
                ? `Rows ${fmt(first)}–${fmt(last)} of ${fmt(rows.length)}`
                : "No rows"}
            </span>
            <span className="hidden items-center gap-1 sm:flex">
              <Kbd>↑</Kbd>
              <Kbd>↓</Kbd> move, <Kbd>PgDn</Kbd> page, <Kbd>Esc</Kbd> close the details
            </span>
          </div>
        </Card>
      </div>
    </div>
  );
}
