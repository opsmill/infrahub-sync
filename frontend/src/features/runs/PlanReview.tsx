import { Button, Card } from "@infrahub/ui";
import {
  type ReactNode,
  type Ref,
  useDeferredValue,
  useEffect,
  useLayoutEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
} from "react";

import type { Plan, PlanLastChange } from "#/api/types";
import { CheckIcon, ChevronRightIcon } from "#/components/icons";
import {
  CopyButton,
  EmptyPanel,
  FOCUS_INSET,
  Notice,
  SearchField,
  textAction,
} from "#/components/page";
import { BranchCode, CHANGE_TONE } from "#/components/run-status";
import { countsSentence, fmt } from "#/domain/run-state";

import { OperationRow, type OperationView, type RowContext } from "./plan-operation";
import {
  ACTION_WORDS,
  ACTIONS,
  type Action,
  fieldsOf,
  fieldText,
  identityLevels,
  levelLabel,
} from "./plan-values";

export { displayValue, identityParts, nodeLabel } from "./plan-values";

/* ---------- the sentence under the Changes heading ---------- */

function listWords(items: string[]): string {
  if (items.length <= 1) return items.join("");
  return `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

/** The sentence under the Changes heading, or undefined for an empty plan. */
export function changesSentence(plan: Plan, phase: string): string | undefined {
  const {
    by_action: by,
    total,
    deletes_not_executed: d,
    delete_operations_computed: deletesComputed,
  } = plan.summary;
  if (total === 0) return undefined;
  const create = by.create ?? 0;
  const update = by.update ?? 0;
  if (phase === "applied") {
    const counts = { create, update, delete: by.delete ?? 0, total, deletesComputed };
    return countsSentence(counts, "done") || undefined;
  }
  const notRead = deletesComputed
    ? ""
    : " Nodes that are not in the source are not listed, because the destination was not read in full.";
  const writes: string[] = [];
  if (create) writes.push(`${fmt(create)} to create`);
  if (update) writes.push(`${fmt(update)} to update`);
  const missing = d === 1 ? "1 node that is" : `${fmt(d)} nodes that are`;
  if (!plan.checksum_ok) {
    const listed = d > 0 ? [...writes, `${missing} not in the source`] : writes;
    return `The plan lists ${listed.length ? listWords(listed) : `${fmt(total)} operations`}. Apply refuses this plan because its checksum does not match.${notRead}`;
  }
  const w = create + update;
  if (w > 0 && d === 0)
    return `Apply writes ${fmt(w)} ${w === 1 ? "operation" : "operations"}: ${writes.join(", ")}.${notRead}`;
  if (w > 0)
    return `Apply writes ${fmt(w)} of ${fmt(total)} operations: ${writes.join(", ")}. It does not delete the ${missing} not in the source.`;
  if (d > 0)
    return "Apply has nothing to write. The plan lists only nodes that are not in the source, and apply does not delete nodes.";
  return undefined;
}

/* ---------- view state ---------- */

const PAGE = 100;

interface ViewState {
  /** Null until the reviewer opens or closes a kind; the defaults apply until then. */
  openKinds: Set<string> | null;
  /**
   * Kinds closed while a search is set. Every kind with a match opens during a search, and a
   * header click then changes only this set, so clearing the search restores `openKinds`.
   */
  searchClosed: Set<string>;
  openOps: Set<string>;
  limits: Record<string, number>;
}

type ViewAction =
  | { type: "toggleKind"; kind: string; current: Set<string>; searching: boolean }
  | { type: "setAllKinds"; kinds: string[]; open: boolean; searching: boolean }
  | { type: "toggleOp"; id: string }
  | { type: "showMore"; kind: string }
  | { type: "resetLimits" }
  | { type: "searchChanged" };

function toggled(set: Set<string>, key: string): Set<string> {
  const next = new Set(set);
  if (next.has(key)) next.delete(key);
  else next.add(key);
  return next;
}

function reduce(state: ViewState, action: ViewAction): ViewState {
  switch (action.type) {
    case "toggleKind":
      return action.searching
        ? { ...state, searchClosed: toggled(state.searchClosed, action.kind) }
        : { ...state, openKinds: toggled(action.current, action.kind) };
    case "setAllKinds": {
      if (action.searching)
        return { ...state, searchClosed: new Set(action.open ? [] : action.kinds) };
      return { ...state, openKinds: new Set(action.open ? action.kinds : []) };
    }
    case "toggleOp":
      return { ...state, openOps: toggled(state.openOps, action.id) };
    case "showMore":
      return {
        ...state,
        limits: { ...state.limits, [action.kind]: (state.limits[action.kind] ?? PAGE) + PAGE },
      };
    case "resetLimits":
      return Object.keys(state.limits).length ? { ...state, limits: {} } : state;
    case "searchChanged":
      return { ...state, limits: {}, searchClosed: new Set() };
  }
}

/* ---------- toolbar ---------- */

type FilterValue = Action | "all";

const FILTER_COUNT: Record<FilterValue, string> = {
  all: "text-foreground-muted",
  create: CHANGE_TONE.create.text,
  update: CHANGE_TONE.update.text,
  delete: CHANGE_TONE.delete.text,
};

function Filter({
  options,
  value,
  onChange,
  counts,
  allRef,
}: {
  options: FilterValue[];
  value: FilterValue;
  onChange: (v: FilterValue) => void;
  counts: Record<FilterValue, number>;
  allRef: Ref<HTMLButtonElement>;
}) {
  return (
    <fieldset
      aria-label="Show operations"
      className="m-0 flex w-full min-w-0 flex-wrap gap-0.5 rounded-xl border bg-content p-0.5 shadow-input sm:ml-auto sm:w-auto sm:flex-nowrap"
    >
      {options.map((o) => {
        const selected = value === o;
        const n = counts[o];
        return (
          <button
            key={o}
            ref={o === "all" ? allRef : undefined}
            type="button"
            aria-pressed={selected}
            onClick={() => onChange(o)}
            className={`inline-flex h-8 flex-1 cursor-pointer items-center justify-center gap-1.5 whitespace-nowrap rounded-[10px] px-1.5 text-sm transition-colors sm:flex-none sm:px-2.5 duration-150 ease-out pointer-coarse:h-11 motion-reduce:transition-none ${FOCUS_INSET} ${
              selected
                ? "bg-content-strong font-medium text-foreground"
                : "text-foreground-muted hover:bg-content-muted hover:text-foreground"
            }`}
          >
            {o === "all" ? "All" : ACTION_WORDS[o].filter}
            <span className="sr-only">, </span>
            <span
              className={`text-xs tabular-nums ${selected ? FILTER_COUNT[o] : n === 0 ? "text-subtle-muted" : "text-subtle"}`}
            >
              {fmt(n)}
            </span>
          </button>
        );
      })}
    </fieldset>
  );
}

function noMatchText(q: string, filter: FilterValue): string {
  if (q) {
    if (filter === "all") return `No operations match “${q}”.`;
    if (filter === "delete") return `No nodes listed as not deleted match “${q}”.`;
    return `No operations to ${filter} match “${q}”.`;
  }
  if (filter === "delete") return "No nodes listed as not deleted in this plan.";
  return `No operations to ${filter} in this plan.`;
}

/* ---------- kinds ---------- */

const KIND_HEADER_BG =
  "bg-content bg-[linear-gradient(var(--color-content-strong),var(--color-content-strong))] hover:bg-[image:var(--highlight),linear-gradient(var(--color-content-strong),var(--color-content-strong))] active:bg-[image:var(--highlight),linear-gradient(var(--color-content-strong),var(--color-content-strong))]";

/** Keyboard focus scrolls a control clear of the root header and the sticky kind header. */
const CLEAR_STICKY = "[&_:is(a,button,[tabindex])]:scroll-mt-12";

type KindCounts = { total: number; byAction: (readonly [Action, number])[] };

function KindGroup({
  kind,
  views,
  planCounts,
  open,
  onToggle,
  limit,
  onShowMore,
  border,
  ctx,
  openOps,
  onToggleOp,
}: {
  kind: string;
  views: OperationView[];
  /** Counts over every operation of the kind, so the chips stay facts about the plan. */
  planCounts: KindCounts;
  open: boolean;
  onToggle: () => void;
  limit: number;
  onShowMore: () => void;
  border: "none" | "thin" | "step";
  ctx: RowContext;
  openOps: Set<string>;
  onToggleOp: (id: string) => void;
}) {
  const sectionRef = useRef<HTMLElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const restoreScroll = useRef(false);
  const rest = views.length - limit;
  const listId = `kind-${kind}`;

  // Closing a kind whose header is stuck removes the rows above the viewport; bring the header back.
  useLayoutEffect(() => {
    if (open || !restoreScroll.current) return;
    restoreScroll.current = false;
    sectionRef.current?.scrollIntoView({ block: "start" });
  }, [open]);

  // The browser does not scroll a control that is partly in view, even when the sticky header covers it.
  useEffect(() => {
    const section = sectionRef.current;
    const heading = headingRef.current;
    if (!section || !heading) return;
    const keepClear = (e: FocusEvent) => {
      const target = e.target;
      if (!(target instanceof HTMLElement) || heading.contains(target)) return;
      if (!target.matches(":focus-visible")) return;
      const covered = heading.getBoundingClientRect().bottom;
      const top = target.getBoundingClientRect().top;
      if (top < covered) window.scrollBy({ top: top - covered - 8 });
    };
    section.addEventListener("focusin", keepClear);
    return () => section.removeEventListener("focusin", keepClear);
  }, []);

  const toggle = () => {
    const section = sectionRef.current?.getBoundingClientRect().top;
    const heading = headingRef.current?.getBoundingClientRect().top;
    restoreScroll.current =
      open && section !== undefined && heading !== undefined && heading - section > 2;
    onToggle();
  };

  return (
    <section
      ref={sectionRef}
      className={
        border === "step"
          ? "border-t-2 border-t-border-strong"
          : border === "thin"
            ? "border-t"
            : ""
      }
    >
      <h3 ref={headingRef} className="sticky top-[49px] z-20">
        <button
          type="button"
          onClick={toggle}
          aria-expanded={open}
          aria-controls={open ? listId : undefined}
          className={`flex min-h-11 w-full cursor-pointer items-center gap-3 px-4 py-2 text-left sm:min-h-0 ${KIND_HEADER_BG} ${open ? "border-b border-b-border-strong" : ""} ${FOCUS_INSET}`}
        >
          <ChevronRightIcon
            className={`size-3.5 shrink-0 text-subtle transition-transform duration-150 ease-out motion-reduce:transition-none ${open ? "rotate-90" : ""}`}
          />
          <span className="flex min-w-0 flex-wrap items-baseline gap-x-2">
            <span className="font-mono font-semibold text-[13px] [overflow-wrap:anywhere]">
              {kind}
            </span>
            {views.length < planCounts.total ? (
              <span className="text-subtle text-xs tabular-nums">
                {fmt(views.length)} of {fmt(planCounts.total)} shown
              </span>
            ) : (
              planCounts.byAction.length > 1 && (
                <span className="text-subtle text-xs tabular-nums">
                  {fmt(planCounts.total)} operations
                </span>
              )
            )}
          </span>
          <span className="ml-auto flex flex-wrap justify-end gap-1.5">
            {planCounts.byAction.map(([a, n]) => (
              <span
                key={a}
                className={`whitespace-nowrap rounded-md px-1.5 font-medium text-xs leading-5 tabular-nums ${CHANGE_TONE[a].chip}`}
              >
                {ACTION_WORDS[a].count(n, ctx.done)}
              </span>
            ))}
          </span>
        </button>
      </h3>
      {open && (
        <>
          <ul id={listId} className={`divide-y ${CLEAR_STICKY}`}>
            {views.slice(0, limit).map((v) => (
              <OperationRow
                key={v.op.operation_id}
                view={v}
                ctx={ctx}
                open={openOps.has(v.op.operation_id)}
                onToggle={() => onToggleOp(v.op.operation_id)}
              />
            ))}
          </ul>
          {rest > 0 && (
            <div className={`border-t px-4 py-1 ${CLEAR_STICKY}`}>
              <button
                type="button"
                onClick={onShowMore}
                className={`${textAction(false)} pointer-coarse:h-11`}
              >
                Show the next {fmt(Math.min(PAGE, rest))} operations ({fmt(rest)} not shown)
              </button>
            </div>
          )}
        </>
      )}
    </section>
  );
}

/* ---------- notices ---------- */

function PlanNotice({ plan }: { plan: Plan }) {
  const notes = plan.verification_notes;
  if (plan.checksum_ok && notes.length === 0) return null;
  const list = notes.length > 0 && (
    <ul className="list-disc pl-4 text-[13px] text-foreground-muted [overflow-wrap:anywhere]">
      {notes.map((n) => (
        <li key={n}>{n}</li>
      ))}
    </ul>
  );
  if (!plan.checksum_ok) {
    return (
      <Notice
        tone="danger"
        role="note"
        titleAs="h3"
        title="The saved plan does not match its checksum"
      >
        <p className="text-[13px]">
          Apply refuses this plan. Plan again to create a new plan, then review and apply that one.
        </p>
        {list}
      </Notice>
    );
  }
  return (
    <Notice tone="warning" role="note" titleAs="h3" title="A check on the saved plan did not pass">
      {list}
    </Notice>
  );
}

function NoDifferences({ plan }: { plan: Plan }) {
  return (
    <EmptyPanel title="No differences found">
      <p className="max-w-[60ch] text-pretty text-foreground-muted">
        The plan found no differences between the source and the destination
        {plan.destination_branch && (
          <>
            {" "}
            on branch <BranchCode branch={plan.destination_branch} />
          </>
        )}
        .
      </p>
      {!plan.summary.delete_operations_computed && (
        <p className="max-w-[60ch] text-pretty text-subtle text-xs">
          Deletes were not listed, because the destination was not read in full.
        </p>
      )}
    </EmptyPanel>
  );
}

/* ---------- the list of operations ---------- */

function buildViews(plan: Plan): OperationView[] {
  let byOperation: Map<string, Map<string, PlanLastChange>> | undefined;
  if (plan.last_changes) {
    byOperation = new Map();
    for (const c of plan.last_changes) {
      const fields = byOperation.get(c.operation_id) ?? new Map<string, PlanLastChange>();
      fields.set(c.field, c);
      byOperation.set(c.operation_id, fields);
    }
  }
  const views = plan.operations.map((op) => {
    const { name, levels } = identityLevels(op.identity);
    const label = [...levels.map(levelLabel), name].join(" › ");
    const fields = fieldsOf(op);
    const search = [
      op.kind,
      label,
      ...fields.flatMap((f) => [f.name, fieldText(f)]),
      op.destination_id ?? "",
    ]
      .join("\n")
      .toLowerCase();
    return {
      op,
      name,
      levels,
      label,
      fields,
      changes: op.action === "update" ? byOperation?.get(op.operation_id) : undefined,
      search,
    };
  });
  return views.sort((a, b) => a.label.localeCompare(b.label, undefined, { numeric: true }));
}

/**
 * Kinds in the order apply writes them: by tier, then by name within a tier. Apply never runs
 * deletes, so a kind with only deletes has no tier (`null`) and comes last.
 */
function kindOrder(views: OperationView[]): { kind: string; tier: number | null }[] {
  const tiers = new Map<string, number | null>();
  for (const v of views) {
    const t = v.op.action === "delete" ? null : v.op.tier;
    const known = tiers.get(v.op.kind);
    if (known === undefined || (t !== null && (known === null || t < known)))
      tiers.set(v.op.kind, t);
  }
  return [...tiers.entries()]
    .map(([kind, tier]) => ({ kind, tier }))
    .sort(
      (a, b) =>
        Number(a.tier === null) - Number(b.tier === null) ||
        (a.tier ?? 0) - (b.tier ?? 0) ||
        a.kind.localeCompare(b.kind)
    );
}

function countsByKind(views: OperationView[]): Map<string, KindCounts> {
  const tally = new Map<string, Record<Action, number>>();
  for (const v of views) {
    const c = tally.get(v.op.kind) ?? { create: 0, update: 0, delete: 0 };
    c[v.op.action] += 1;
    tally.set(v.op.kind, c);
  }
  return new Map(
    [...tally].map(([kind, c]) => [
      kind,
      {
        total: c.create + c.update + c.delete,
        byAction: ACTIONS.map((a) => [a, c[a]] as const).filter(([, n]) => n > 0),
      },
    ])
  );
}

/** The list of operations, with its toolbar, notices and the empty-plan panel. */
export function PlanChanges({ plan, phase }: { plan: Plan; phase: string }) {
  const done = phase === "applied";
  const views = useMemo(() => buildViews(plan), [plan]);
  const kinds = useMemo(() => kindOrder(views), [views]);
  const kindCounts = useMemo(() => countsByKind(views), [views]);
  const presentActions = useMemo(
    () => ACTIONS.filter((a) => views.some((v) => v.op.action === a)),
    [views]
  );
  const defaultOpen = useMemo(
    () =>
      new Set(
        views.length <= 50 || kinds.length <= 3
          ? kinds.map((k) => k.kind)
          : kinds.slice(0, 1).map((k) => k.kind)
      ),
    [views.length, kinds]
  );

  const [state, dispatch] = useReducer(reduce, {
    openKinds: null,
    searchClosed: new Set<string>(),
    openOps: new Set<string>(),
    limits: {},
  });
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<FilterValue>("all");
  const deferredQuery = useDeferredValue(query);
  const needle = deferredQuery.trim().toLowerCase();
  const searchRef = useRef<HTMLInputElement>(null);
  const allRef = useRef<HTMLButtonElement>(null);
  const focusAfterRender = useRef<string | null>(null);

  const matches = useMemo(
    () => (needle ? views.filter((v) => v.search.includes(needle)) : views),
    [views, needle]
  );
  const counts = useMemo(() => {
    const c: Record<FilterValue, number> = { all: matches.length, create: 0, update: 0, delete: 0 };
    for (const v of matches) c[v.op.action] += 1;
    return c;
  }, [matches]);
  const groups = useMemo(() => {
    const byKind = new Map<string, OperationView[]>();
    for (const v of matches) {
      if (filter !== "all" && v.op.action !== filter) continue;
      const list = byKind.get(v.op.kind) ?? [];
      list.push(v);
      byKind.set(v.op.kind, list);
    }
    return kinds.flatMap(({ kind, tier }) => {
      const list = byKind.get(kind);
      if (!list) return [];
      // A thick line marks a new write step only among the rows apply writes.
      const step = list.some((v) => v.op.action !== "delete") ? tier : null;
      return [{ kind, step, views: list }];
    });
  }, [matches, filter, kinds]);
  const shown = groups.reduce((n, g) => n + g.views.length, 0);

  useEffect(() => {
    const id = focusAfterRender.current;
    if (!id) return;
    focusAfterRender.current = null;
    const row = document.getElementById(`op-${id}`);
    row?.querySelector<HTMLElement>("button, [tabindex]")?.focus();
  });

  if (views.length === 0) {
    return (
      <div className="flex flex-col gap-3">
        <PlanNotice plan={plan} />
        <NoDifferences plan={plan} />
      </div>
    );
  }

  const searching = needle !== "";
  const openKinds = state.openKinds ?? defaultOpen;
  const isOpen = (kind: string) =>
    searching ? !state.searchClosed.has(kind) : openKinds.has(kind);
  const allOpen = groups.every((g) => isOpen(g.kind));
  const ctx: RowContext = {
    done,
    recorded: plan.last_changes != null,
    importAccountId: plan.lineage?.import_account.id,
  };

  const changeQuery = (q: string) => {
    setQuery(q);
    dispatch({ type: "searchChanged" });
  };
  const changeFilter = (f: FilterValue) => {
    setFilter(f);
    dispatch({ type: "resetLimits" });
  };
  const showMore = (kind: string, list: OperationView[]) => {
    const limit = state.limits[kind] ?? PAGE;
    focusAfterRender.current = list[limit]?.op.operation_id ?? null;
    dispatch({ type: "showMore", kind });
  };
  const clearNoMatch = () => {
    setQuery("");
    setFilter("all");
    dispatch({ type: "searchChanged" });
    if (searchRef.current) searchRef.current.focus();
    else allRef.current?.focus();
  };

  const withSearch = views.length > 8;
  const withFilter = presentActions.length > 1;
  const withToggle = kinds.length > 1 && groups.length > 0;
  const writtenGroups = groups.filter((g) => g.step !== null).length;
  const deleteOnlyGroups = groups.length - writtenGroups;
  const q = query.trim();
  // "Clear search" would repeat the name of the search field's own clear button.
  const noMatchButton = q && filter !== "all" ? "Clear search and filter" : "Show all operations";

  let previousStep: number | null | undefined;
  return (
    <div className="flex flex-col gap-3">
      <PlanNotice plan={plan} />
      {(withSearch || withFilter) && (
        <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:items-center sm:gap-x-3">
          {withSearch && (
            <SearchField
              value={query}
              onChange={changeQuery}
              placeholder="Search kinds, nodes and values"
              label="Search the plan by kind, node, field name, value or node ID"
              inputRef={searchRef}
              count={shown}
              noun={["operation matches", "operations match"]}
            />
          )}
          {withFilter && (
            <Filter
              options={["all", ...presentActions]}
              value={filter}
              onChange={changeFilter}
              counts={counts}
              allRef={allRef}
            />
          )}
          <span className="sr-only" aria-live="polite">
            {query === "" && filter !== "all"
              ? `${fmt(shown)} ${shown === 1 ? "operation" : "operations"} shown`
              : ""}
          </span>
        </div>
      )}
      {withToggle && (
        <div className="flex items-baseline gap-x-4 px-1">
          {writtenGroups > 1 && (
            <p className="min-w-0 text-pretty text-subtle text-xs">
              {done ? "Apply wrote" : "Apply writes"} these kinds from top to bottom. A thicker line
              separates the steps; kinds within one step are listed by name.
              {deleteOnlyGroups > 0 &&
                ` Kinds with only deletes are listed last, because apply ${done ? "did not run" : "does not run"} deletes.`}
            </p>
          )}
          <button
            type="button"
            onClick={() =>
              dispatch({
                type: "setAllKinds",
                kinds: searching ? groups.map((g) => g.kind) : kinds.map((k) => k.kind),
                open: !allOpen,
                searching,
              })
            }
            className={`${textAction(false)} ml-auto shrink-0 pointer-coarse:h-11`}
          >
            {allOpen ? "Collapse all kinds" : "Expand all kinds"}
          </button>
        </div>
      )}
      <Card className="overflow-clip">
        {groups.length === 0 ? (
          <div className="flex flex-col items-center gap-3 px-6 py-10 text-center">
            <p className="max-w-[60ch] text-pretty text-foreground-muted">
              {noMatchText(q, filter)}
            </p>
            <Button variant="outline" size="sm" onPress={clearNoMatch}>
              {noMatchButton}
            </Button>
          </div>
        ) : (
          groups.map((g, i) => {
            const border: "none" | "thin" | "step" =
              i === 0 ? "none" : g.step !== null && g.step !== previousStep ? "step" : "thin";
            previousStep = g.step;
            const limit = state.limits[g.kind] ?? PAGE;
            return (
              <KindGroup
                key={g.kind}
                kind={g.kind}
                views={g.views}
                planCounts={kindCounts.get(g.kind) ?? { total: g.views.length, byAction: [] }}
                open={isOpen(g.kind)}
                onToggle={() =>
                  dispatch({ type: "toggleKind", kind: g.kind, current: openKinds, searching })
                }
                limit={limit}
                onShowMore={() => showMore(g.kind, g.views)}
                border={border}
                ctx={ctx}
                openOps={state.openOps}
                onToggleOp={(id) => dispatch({ type: "toggleOp", id })}
              />
            );
          })
        )}
      </Card>
    </div>
  );
}

/* ---------- the plan record ---------- */

function Fact({ term, children }: { term: string; children: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <dt className="text-subtle text-xs">{term}</dt>
      {children}
    </div>
  );
}

const Explain = ({ children }: { children: ReactNode }) => (
  <dd className="mt-0.5 text-pretty text-foreground-muted text-xs">{children}</dd>
);

/** The plan record: checksum, schema fingerprint, branch when it differs, lineage. */
export function PlanFacts({ plan, runBranch }: { plan: Plan; runBranch: string | null }) {
  const fingerprint = plan.schema_fingerprint;
  const lineage = plan.lineage;
  return (
    <dl className="grid grid-cols-1 gap-x-8 gap-y-4 text-[13px] sm:grid-cols-2">
      <Fact term="Plan checksum">
        <dd className="flex min-w-0 items-start gap-1.5">
          <span className="min-w-0 font-mono text-xs leading-5 [overflow-wrap:anywhere]">
            {plan.checksum}
          </span>
          <CopyButton value={plan.checksum} label="Copy checksum" />
        </dd>
        <dd className="mt-0.5 text-xs">
          {plan.checksum_ok ? (
            <span className="inline-flex items-start gap-1">
              <CheckIcon className="mt-px size-3.5 shrink-0 text-success" />
              <span className="text-pretty text-foreground-muted">
                Matches the saved plan. Apply runs only the plan with this checksum, and only on the
                branch the plan was created for.
              </span>
            </span>
          ) : (
            <span className="text-danger">Does not match the saved plan.</span>
          )}
        </dd>
      </Fact>
      {fingerprint && (
        <Fact term="Destination schema fingerprint">
          <dd className="flex min-w-0 items-center gap-1.5">
            <span className="font-mono text-xs">
              {fingerprint.slice(0, 12)}…{fingerprint.slice(-8)}
            </span>
            <CopyButton value={fingerprint} label="Copy schema fingerprint" />
          </dd>
          <Explain>
            Apply refuses this plan when the destination schema no longer has this fingerprint.
          </Explain>
        </Fact>
      )}
      {plan.destination_branch && plan.destination_branch !== runBranch && (
        <Fact term="Destination branch">
          <dd>
            <BranchCode branch={plan.destination_branch} />
          </dd>
          <Explain>
            The plan was created for this branch. Apply refuses this plan on any other branch.
          </Explain>
        </Fact>
      )}
      {lineage && (
        <>
          <Fact term="Import account">
            <dd>{lineage.import_account.name}</dd>
            <Explain>Apply refuses this plan when it runs with another account.</Explain>
          </Fact>
          <Fact term="Source of each value">
            <dd>
              {lineage.source.kind === "CoreAccount" &&
              lineage.source.name === lineage.import_account.name ? (
                "The import account"
              ) : (
                <>
                  {lineage.source.name}
                  <span className="text-subtle"> ({lineage.source.kind})</span>
                </>
              )}
            </dd>
          </Fact>
          <Fact term="Owner of created values">
            {lineage.owner ? (
              <>
                <dd>
                  {lineage.owner.name}
                  <span className="text-subtle"> ({lineage.owner.kind})</span>
                </dd>
                <Explain>The owner setting applies only to values this run creates.</Explain>
              </>
            ) : (
              <dd>No owner set</dd>
            )}
          </Fact>
          <Fact term="Protection">
            <dd className="text-pretty">
              {lineage.protect
                ? "On. The import marks each value it creates or changes as protected."
                : "Off. The import sends no protection flag, and values that are already protected stay protected."}
            </dd>
          </Fact>
        </>
      )}
      {!plan.summary.delete_operations_computed && (
        <Fact term="Nodes missing from the source">
          <dd className="text-pretty">
            Not listed. The destination was not read in full, so the plan lists no deletes.
          </dd>
        </Fact>
      )}
    </dl>
  );
}
