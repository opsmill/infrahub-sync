import type { Plan, PlanLastChange, PlanOperation } from "#/api/types";
import {
  type Action,
  type Field,
  fieldsOf,
  fieldText,
  identityLevels,
  levelLabel,
  type ScopeEntry,
} from "#/features/runs/plan-values";

/** One operation with everything its row and details read, computed once per plan. */
export interface OperationView {
  op: PlanOperation;
  name: string;
  levels: ScopeEntry[][];
  label: string;
  fields: Field[];
  /** Last-change records of an update, by field name; undefined when the plan has none for it. */
  changes?: Map<string, PlanLastChange>;
  search: string;
}

export interface RowContext {
  /** True on an applied run: the copy is written in the past tense. */
  done: boolean;
  /** False when the plan holds no last-change records at all (`last_changes` is null). */
  recorded: boolean;
  importAccountId?: string;
}

/** What the list shows: every operation, or those of one kind or one action. */
export interface ChangesFilter {
  kind?: string;
  action?: Action;
}

function lastChangesByOperation(plan: Plan) {
  if (!plan.last_changes) return undefined;
  const byOperation = new Map<string, Map<string, PlanLastChange>>();
  for (const c of plan.last_changes) {
    const fields = byOperation.get(c.operation_id) ?? new Map<string, PlanLastChange>();
    fields.set(c.field, c);
    byOperation.set(c.operation_id, fields);
  }
  return byOperation;
}

function viewOf(
  op: Plan["operations"][number],
  byOperation: ReturnType<typeof lastChangesByOperation>
): OperationView {
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
}

const byLabel = new Intl.Collator(undefined, { numeric: true });

const CHUNK = 5000;

const yieldToBrowser = (): Promise<void> => {
  const scheduler = (globalThis as { scheduler?: { yield?: () => Promise<void> } }).scheduler;
  if (typeof scheduler?.yield === "function") return scheduler.yield();
  return new Promise((resolve) => setTimeout(resolve, 0));
};

const prepared = new WeakMap<Plan, OperationView[]>();

/**
 * The plan's operations as views, sorted by their label. The work runs in chunks that hand the
 * browser back control in between, so a plan of 150,000 operations does not freeze the page. The
 * result is kept for the plan, so preparing the same plan again returns at once.
 */
export async function prepareViews(
  plan: Plan,
  {
    onProgress,
    signal,
  }: { onProgress?: (done: number, total: number) => void; signal?: AbortSignal } = {}
): Promise<OperationView[]> {
  const known = prepared.get(plan);
  if (known) return known;
  const byOperation = lastChangesByOperation(plan);
  const ops = plan.operations;
  const views: OperationView[] = new Array(ops.length);
  for (let start = 0; start < ops.length; start += CHUNK) {
    signal?.throwIfAborted();
    const end = Math.min(ops.length, start + CHUNK);
    for (let i = start; i < end; i++) views[i] = viewOf(ops[i], byOperation);
    onProgress?.(end, ops.length);
    if (end < ops.length) await yieldToBrowser();
  }
  signal?.throwIfAborted();
  views.sort((a, b) => byLabel.compare(a.label, b.label));
  prepared.set(plan, views);
  return views;
}

/** Each kind once, in write order: lower tiers first, kinds with only deletes last. */
export function kindOrder(views: OperationView[]): { kind: string; tier: number | null }[] {
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

/** The fields of an update that the plan records as changed. */
export const changedFields = (v: OperationView) =>
  v.changes ? v.fields.filter((f) => v.changes?.has(f.name)) : [];

/**
 * Results computed from a prepared list of views, kept between mounts. A prepared list never
 * changes, so opening the changes a second time does not redo the work.
 */
const remembered = new WeakMap<object, Map<string, unknown>>();
export function remember<T>(owner: object, key: string, compute: () => T): T {
  let m = remembered.get(owner);
  if (!m) {
    m = new Map();
    remembered.set(owner, m);
  }
  if (!m.has(key)) m.set(key, compute());
  return m.get(key) as T;
}
