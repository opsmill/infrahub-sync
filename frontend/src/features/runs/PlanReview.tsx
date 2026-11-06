import { Card } from "@infrahub/ui";
import { useMemo, useState } from "react";

import type { Plan, PlanLastChange, PlanOperation } from "#/api/types";
import { ChevronRightIcon } from "#/components/icons";
import { CopyButton, SearchField } from "#/components/page";
import { fmt } from "#/domain/run-state";
import { formatAbsolute, formatRelative } from "#/lib/time";

type Action = PlanOperation["action"];
const ACTIONS: Action[] = ["create", "update", "delete"];

const ACTION_STYLE: Record<Action, { sign: string; text: string; chip: string; word: string }> = {
  create: {
    sign: "+",
    text: "text-success",
    chip: "bg-success-surface text-success",
    word: "Create",
  },
  update: {
    sign: "~",
    text: "text-warning",
    chip: "bg-warning-surface text-warning",
    word: "Update",
  },
  delete: { sign: "−", text: "text-danger", chip: "bg-danger-surface text-danger", word: "Delete" },
};

/* ---------- values ---------- */

type Json = Record<string, unknown>;
const isObject = (v: unknown): v is Json =>
  typeof v === "object" && v !== null && !Array.isArray(v);

/**
 * A node named by its identifiers, as a plan records a peer or a key:
 * `{name, site: {identity: {name}, peer_kind}}` reads "name (site)".
 */
function nodeLabel(identity: Json): string {
  const own: string[] = [];
  const parents: string[] = [];
  for (const [key, value] of Object.entries(identity)) {
    if (key === "peer_kind") continue;
    if (isObject(value) && isObject(value.identity)) parents.push(nodeLabel(value.identity));
    else if (isObject(value)) parents.push(nodeLabel(value));
    else own.push(displayValue(value));
  }
  const name = own.join(" · ") || parents.shift() || "—";
  return parents.length ? `${name} (${parents.join(", ")})` : name;
}

/** A planned value in words: attribute values as written, related nodes by their identifiers. */
export function displayValue(value: unknown): string {
  if (value === null || value === undefined) return "empty";
  if (typeof value === "string") return value === "" ? "empty" : value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) return value.length ? value.map(displayValue).join(", ") : "none";
  if (isObject(value)) {
    if ("value" in value) return displayValue(value.value);
    if (isObject(value.identity)) return nodeLabel(value.identity);
    return nodeLabel(value);
  }
  return String(value);
}

/** The object an operation names: its own name, then the nodes that scope it. */
export function identityLabel(identity: Json): { name: string; scope?: string } {
  const label = nodeLabel(identity);
  const at = label.indexOf(" (");
  return at > 0 && label.endsWith(")")
    ? { name: label.slice(0, at), scope: label.slice(at + 2, -1) }
    : { name: label };
}

interface Field {
  name: string;
  value: unknown;
  relationship: boolean;
}

function fieldsOf(op: PlanOperation): Field[] {
  const fields: Field[] = Object.entries(op.payload ?? {}).map(([name, value]) => ({
    name,
    value,
    relationship: false,
  }));
  for (const rel of op.relationships ?? []) {
    if (typeof rel.field !== "string") continue;
    const peers = Array.isArray(rel.peers) ? rel.peers : [];
    fields.push({
      name: rel.field,
      value: rel.cardinality === "one" ? (peers[0] ?? null) : peers,
      relationship: true,
    });
  }
  return fields;
}

/* ---------- one operation ---------- */

function LastChange({ change }: { change: PlanLastChange }) {
  const who = change.updated_by?.display_label ?? change.updated_by?.id;
  if (!who && !change.updated_at)
    return <span className="text-subtle">last change not recorded</span>;
  return (
    <span
      className="text-subtle"
      title={change.updated_at ? formatAbsolute(change.updated_at) : undefined}
    >
      last changed{who && <> by {who}</>}
      {change.updated_at && <> {formatRelative(change.updated_at)}</>}
    </span>
  );
}

function FieldList({
  fields,
  op,
  changes,
}: {
  fields: Field[];
  op: PlanOperation;
  changes?: Map<string, PlanLastChange>;
}) {
  return (
    <dl className="grid grid-cols-[minmax(110px,auto)_1fr] gap-x-6 gap-y-1">
      {fields.map((f) => {
        const change = changes?.get(f.name);
        return (
          <div key={f.name} className="contents">
            <dt className="font-mono text-foreground-muted text-xs leading-5">{f.name}</dt>
            <dd className="min-w-0 leading-5 [overflow-wrap:anywhere]">
              <span className={change ? `font-medium ${ACTION_STYLE[op.action].text}` : ""}>
                {displayValue(f.value)}
              </span>
              {change && (
                <span className="ml-2 text-xs">
                  <LastChange change={change} />
                </span>
              )}
            </dd>
          </div>
        );
      })}
    </dl>
  );
}

function OperationRow({
  op,
  lastChanges,
}: {
  op: PlanOperation;
  lastChanges: Map<string, Map<string, PlanLastChange>> | null;
}) {
  const [open, setOpen] = useState(false);
  const style = ACTION_STYLE[op.action];
  const fields = fieldsOf(op);
  const id = identityLabel(op.identity);
  // An update's payload carries every field; the plan's last-change records name the ones it changes.
  const changes = op.action === "update" ? lastChanges?.get(op.operation_id) : undefined;
  const changed = changes ? fields.filter((f) => changes.has(f.name)) : [];
  const unchanged = changes ? fields.filter((f) => !changes.has(f.name)) : fields;
  const hasDetail = fields.length > 0 || Boolean(op.destination_id);
  const summary =
    op.action === "update" && changes
      ? changed.length
        ? `changes ${changed.map((f) => f.name).join(", ")}`
        : "no field change recorded"
      : op.action === "create"
        ? `${fields.length} ${fields.length === 1 ? "field" : "fields"}`
        : op.action === "delete"
          ? "listed for review only"
          : "";
  return (
    <li className="text-[13px]">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={hasDetail ? open : undefined}
        disabled={!hasDetail}
        className="grid w-full cursor-pointer grid-cols-[12px_minmax(0,1fr)_auto_14px] items-center gap-3 px-4 py-2 text-left transition-colors duration-150 ease-out hover:bg-content-muted focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring-halo focus-visible:ring-inset disabled:cursor-default disabled:hover:bg-transparent"
      >
        <span className={`text-center font-mono font-semibold ${style.text}`} aria-hidden="true">
          {style.sign}
        </span>
        <span className="min-w-0 truncate">
          <span className="sr-only">{style.word} </span>
          <span className="font-medium">{id.name}</span>
          {id.scope && <span className="text-subtle"> · {id.scope}</span>}
        </span>
        <span className="max-w-[40ch] truncate text-subtle text-xs">{summary}</span>
        {hasDetail ? (
          <ChevronRightIcon
            className={`size-3.5 text-subtle transition-transform duration-150 ease-out motion-reduce:transition-none ${open ? "rotate-90" : ""}`}
          />
        ) : (
          <span />
        )}
      </button>
      {open && (
        <div className="flex flex-col gap-2.5 border-t bg-content-muted px-4 py-3 pl-11">
          {changed.length > 0 && <FieldList fields={changed} op={op} changes={changes} />}
          {unchanged.length > 0 && (
            <div className="flex flex-col gap-1">
              {changed.length > 0 && (
                <p className="text-subtle text-xs">Sent unchanged with the update</p>
              )}
              <div className={changed.length > 0 ? "opacity-70" : ""}>
                <FieldList fields={unchanged} op={op} />
              </div>
            </div>
          )}
          {op.destination_id && (
            <p className="text-subtle text-xs">
              Updates the destination node <span className="font-mono">{op.destination_id}</span>
            </p>
          )}
        </div>
      )}
    </li>
  );
}

/* ---------- one kind ---------- */

const PAGE = 25;

function KindGroup({
  kind,
  ops,
  lastChanges,
  defaultOpen,
}: {
  kind: string;
  ops: PlanOperation[];
  lastChanges: Map<string, Map<string, PlanLastChange>> | null;
  defaultOpen: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const [limit, setLimit] = useState(PAGE);
  const counts = ACTIONS.map((a) => [a, ops.filter((o) => o.action === a).length] as const).filter(
    ([, n]) => n > 0
  );
  return (
    <section className="border-b last:border-b-0">
      <h3>
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          className="flex w-full cursor-pointer items-center gap-3 px-4 py-2.5 text-left transition-colors duration-150 ease-out hover:bg-content-muted focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring-halo focus-visible:ring-inset"
        >
          <ChevronRightIcon
            className={`size-3.5 shrink-0 text-subtle transition-transform duration-150 ease-out motion-reduce:transition-none ${open ? "rotate-90" : ""}`}
          />
          <span className="font-medium font-mono text-[13px]">{kind}</span>
          <span className="ml-auto flex gap-2 font-mono text-xs tabular-nums">
            {counts.map(([a, n]) => (
              <span key={a} className={ACTION_STYLE[a].text}>
                {ACTION_STYLE[a].sign}
                {fmt(n)}
              </span>
            ))}
          </span>
        </button>
      </h3>
      {open && (
        <>
          <ul className="divide-y border-t">
            {ops.slice(0, limit).map((op) => (
              <OperationRow key={op.operation_id} op={op} lastChanges={lastChanges} />
            ))}
          </ul>
          {ops.length > limit && (
            <div className="border-t px-4 py-2">
              <button
                type="button"
                onClick={() => setLimit((l) => l + 200)}
                className="cursor-pointer rounded-md font-medium text-[13px] text-accent hover:underline focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring-halo"
              >
                Show {fmt(Math.min(200, ops.length - limit))} more of {fmt(ops.length - limit)}
              </button>
            </div>
          )}
        </>
      )}
    </section>
  );
}

/* ---------- plan ---------- */

function Filter({
  value,
  onChange,
  counts,
}: {
  value: Action | "all";
  onChange: (v: Action | "all") => void;
  counts: Record<Action | "all", number>;
}) {
  const options: (Action | "all")[] = ["all", ...ACTIONS.filter((a) => counts[a] > 0)];
  return (
    <fieldset
      aria-label="Show operations"
      className="m-0 flex min-w-0 gap-0.5 rounded-xl border-0 bg-content-strong p-0.5"
    >
      {options.map((o) => (
        <button
          key={o}
          type="button"
          aria-pressed={value === o}
          onClick={() => onChange(o)}
          className={`inline-flex h-8 cursor-pointer items-center gap-1.5 whitespace-nowrap rounded-[10px] px-2.5 text-sm transition-colors duration-150 ease-out focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring-halo motion-reduce:transition-none ${
            value === o
              ? "bg-content text-foreground shadow-input"
              : "text-foreground-muted hover:text-foreground"
          }`}
        >
          {o === "all" ? "All" : ACTION_STYLE[o].word}
          <span className="text-subtle text-xs tabular-nums">{fmt(counts[o])}</span>
        </button>
      ))}
    </fieldset>
  );
}

export function PlanChanges({ plan }: { plan: Plan }) {
  const [action, setAction] = useState<Action | "all">("all");
  const [query, setQuery] = useState("");
  const lastChanges = useMemo(() => {
    if (!plan.last_changes) return null;
    const byOperation = new Map<string, Map<string, PlanLastChange>>();
    for (const c of plan.last_changes) {
      const fields = byOperation.get(c.operation_id) ?? new Map<string, PlanLastChange>();
      fields.set(c.field, c);
      byOperation.set(c.operation_id, fields);
    }
    return byOperation;
  }, [plan.last_changes]);
  const counts = useMemo(() => {
    const c = { all: plan.operations.length, create: 0, update: 0, delete: 0 };
    for (const op of plan.operations) c[op.action] += 1;
    return c;
  }, [plan.operations]);
  const byKind = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const groups = new Map<string, PlanOperation[]>();
    for (const op of plan.operations) {
      if (action !== "all" && op.action !== action) continue;
      if (needle && !`${op.kind} ${nodeLabel(op.identity)}`.toLowerCase().includes(needle))
        continue;
      const list = groups.get(op.kind) ?? [];
      list.push(op);
      groups.set(op.kind, list);
    }
    return [...groups.entries()].sort(
      (a, b) => a[1][0].tier - b[1][0].tier || a[0].localeCompare(b[0])
    );
  }, [plan.operations, action, query]);
  const shownCount = byKind.reduce((n, [, ops]) => n + ops.length, 0);

  if (plan.operations.length === 0) {
    return (
      <Card>
        <p className="px-6 py-10 text-center text-foreground-muted">
          The plan found no differences between the source and the destination.
        </p>
      </Card>
    );
  }
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <SearchField
          value={query}
          onChange={setQuery}
          count={shownCount}
          placeholder="Search kinds and objects"
          noun={["operation matches", "operations match"]}
        />
        <Filter value={action} onChange={setAction} counts={counts} />
      </div>
      <Card className="overflow-hidden">
        {byKind.length === 0 ? (
          <p className="px-6 py-10 text-center text-foreground-muted">No operations match.</p>
        ) : (
          byKind.map(([kind, ops]) => (
            <KindGroup
              key={kind}
              kind={kind}
              ops={ops}
              lastChanges={lastChanges}
              defaultOpen={byKind.length <= 3 || Boolean(query)}
            />
          ))
        )}
      </Card>
      <p className="px-1 text-subtle text-xs">
        Kinds are listed in write order: a kind is written after the kinds it references.
      </p>
    </div>
  );
}

/** Checksum, schema fingerprint and lineage: what the apply will be bound to. */
export function PlanFacts({ plan }: { plan: Plan }) {
  return (
    <dl className="grid grid-cols-1 gap-x-8 gap-y-3 text-[13px] sm:grid-cols-2">
      <div className="flex min-w-0 flex-col gap-0.5">
        <dt className="text-subtle text-xs">Plan checksum</dt>
        <dd className="flex min-w-0 items-center gap-1">
          <span className="truncate font-mono text-xs" title={plan.checksum}>
            {plan.checksum}
          </span>
          <CopyButton value={plan.checksum} label="Copy checksum" />
        </dd>
      </div>
      {plan.schema_fingerprint && (
        <div className="flex min-w-0 flex-col gap-0.5">
          <dt className="text-subtle text-xs">Destination schema fingerprint</dt>
          <dd className="truncate font-mono text-xs" title={plan.schema_fingerprint}>
            {plan.schema_fingerprint}
          </dd>
        </div>
      )}
      {plan.lineage && (
        <div className="flex min-w-0 flex-col gap-0.5">
          <dt className="text-subtle text-xs">Writes as</dt>
          <dd>
            {plan.lineage.import_account.name}
            <span className="text-subtle">
              {" "}
              · source {plan.lineage.source.kind} {plan.lineage.source.name}
              {plan.lineage.owner &&
                ` · owner ${plan.lineage.owner.kind} ${plan.lineage.owner.name}`}
              {plan.lineage.protect && " · protected"}
            </span>
          </dd>
        </div>
      )}
      <div className="flex min-w-0 flex-col gap-0.5">
        <dt className="text-subtle text-xs">Deletes</dt>
        <dd>
          {!plan.summary.delete_operations_computed
            ? "Not computed for this plan"
            : plan.summary.deletes_not_executed > 0
              ? `${fmt(plan.summary.deletes_not_executed)} listed for review; apply does not run deletes`
              : "None"}
        </dd>
      </div>
    </dl>
  );
}
