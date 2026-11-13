import { Fragment, type ReactNode, useState } from "react";
import { cn } from "tailwind-variants";

import type { PlanLastChange } from "#/api/types";
import { ChevronRightIcon, WarningIcon } from "#/components/icons";
import { CopyButton, expandChevron, FOCUS_INSET } from "#/components/page";
import { displayValue, type Field, nodeLabel } from "#/features/runs/plan-values";

import { changedFields, type OperationView, type RowContext } from "./views";

/* ---------- the model: each field with its value before and after ---------- */

type Value = { rel: false; text: string; empty: boolean } | { rel: true; peers: string[] };
const NONE_ATTR: Value = { rel: false, text: "", empty: true };
const NONE_REL: Value = { rel: true, peers: [] };

interface FieldDiff {
  name: string;
  after: Value;
  /** Undefined when the plan does not hold the value the destination has now. */
  before?: Value;
  change?: PlanLastChange;
  /** The current value was last set by an account other than the import account. */
  other: boolean;
}

function toValue(f: Field | undefined, raw: unknown): Value {
  if (f?.rel) {
    const peers = Array.isArray(raw) ? raw : raw == null ? [] : [raw];
    return { rel: true, peers: peers.map((p) => nodeLabel(p as Record<string, unknown>)) };
  }
  const empty = raw === "" || raw == null;
  return { rel: false, text: raw === "" ? "" : displayValue(raw), empty };
}

function diffOf(view: OperationView, ctx: RowContext) {
  const changedNames = new Set(changedFields(view).map((f) => f.name));
  const current = (view.op as { current?: Record<string, unknown> }).current;
  const create = view.op.action === "create";
  const known = create || (view.op.action === "update" && current != null);
  const all = view.fields.map<FieldDiff>((f) => {
    const change = view.changes?.get(f.name);
    const by = change?.updated_by;
    return {
      name: f.name,
      after: toValue(f, f.rel ? f.rel.peers : f.value),
      before: create
        ? f.rel
          ? NONE_REL
          : NONE_ATTR
        : known && changedNames.has(f.name) && current && f.name in current
          ? toValue(f, current[f.name])
          : undefined,
      change,
      other: by != null && ctx.importAccountId != null && by.id !== ctx.importAccountId,
    };
  });
  const changed = create ? all : all.filter((d) => changedNames.has(d.name));
  const same = create ? [] : all.filter((d) => !changedNames.has(d.name));
  return { changed, same, known };
}

type Piece = { text: string; op: "same" | "del" | "add" };

/** Word-level difference of two strings, by longest common subsequence of their tokens. */
function wordDiff(a: string, b: string): Piece[] {
  const split = (s: string) => s.split(/(\s+)/).filter(Boolean);
  const x = split(a);
  const y = split(b);
  const L = Array.from({ length: x.length + 1 }, () => new Array<number>(y.length + 1).fill(0));
  for (let i = x.length - 1; i >= 0; i--)
    for (let j = y.length - 1; j >= 0; j--)
      L[i][j] = x[i] === y[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
  const out: Piece[] = [];
  const push = (text: string, op: Piece["op"]) => {
    const last = out.at(-1);
    if (last?.op === op) last.text += text;
    else out.push({ text, op });
  };
  let i = 0;
  let j = 0;
  while (i < x.length && j < y.length) {
    if (x[i] === y[j]) {
      push(x[i], "same");
      i += 1;
      j += 1;
    } else if (L[i + 1][j] >= L[i][j + 1]) push(x[i++], "del");
    else push(y[j++], "add");
  }
  while (i < x.length) push(x[i++], "del");
  while (j < y.length) push(y[j++], "add");
  return out;
}

function peerSets(before: string[], after: string[]) {
  const b = new Set(before);
  const a = new Set(after);
  return {
    removed: before.filter((p) => !a.has(p)),
    added: after.filter((p) => !b.has(p)),
  };
}

const dateOf = (iso?: string | null) =>
  iso
    ? new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" })
    : "an unknown date";

/** Column headings: the values now and planned, or before and after once the run is applied. */
const words = (done: boolean) => (done ? ["Before", "After"] : ["Now", "Planned"]);

const missingBefore = (done: boolean) =>
  `The plan does not record the value Infrahub ${done ? "held before apply" : "holds now"}, so only the ${done ? "applied" : "planned"} value is shown.`;

const DEL_MARK =
  "rounded-[3px] bg-(--change-remove-surface) text-(--change-remove-text) no-underline [box-decoration-break:clone]";
const INS_MARK =
  "rounded-[3px] bg-(--change-create-surface) text-foreground no-underline [box-decoration-break:clone]";

/* ---------- pieces ---------- */

function Note({ children }: { children: ReactNode }) {
  return (
    <p className="rounded-lg bg-content-muted px-3 py-2 text-foreground-muted text-xs">
      {children}
    </p>
  );
}

function Peers({ peers, tone }: { peers: string[]; tone?: "del" | "add" | "same" }) {
  if (peers.length === 0) return <span className="text-subtle italic">no peers</span>;
  return (
    <span className="flex flex-wrap gap-1">
      {peers.map((p) => (
        <span
          key={p}
          className={cn(
            "inline-flex rounded-md px-1.5 text-xs leading-5",
            tone === "del" &&
              "bg-(--change-remove-surface) text-(--change-remove-text) line-through decoration-1",
            tone === "add" && "bg-(--change-create-surface) font-medium text-foreground",
            (!tone || tone === "same") && "bg-content-strong text-foreground-muted"
          )}
        >
          {tone === "del" && <span className="sr-only">Removed: </span>}
          {tone === "add" && <span className="sr-only">Added: </span>}
          {p}
        </span>
      ))}
    </span>
  );
}

/**
 * One side of a changed field. Before: the removed words in red. After: the added words in green,
 * and all of it when there was no value before.
 */
function ColumnCell({ v, other, side }: { v: Value; other?: Value; side: "before" | "after" }) {
  const before = side === "before";
  if (v.rel) {
    if (!other?.rel) return <Peers peers={v.peers} />;
    const s = before ? peerSets(v.peers, other.peers) : peerSets(other.peers, v.peers);
    const tone = (p: string) =>
      before ? (s.removed.includes(p) ? "del" : "same") : s.added.includes(p) ? "add" : "same";
    return (
      <span className="flex flex-wrap gap-1">
        {v.peers.map((p) => (
          <Peers key={p} peers={[p]} tone={tone(p)} />
        ))}
      </span>
    );
  }
  if (!other || other.rel)
    return <span className={before ? "text-foreground-muted" : "font-medium"}>{v.text}</span>;
  if (other.empty)
    return before ? (
      <del className={cn(DEL_MARK, "text-foreground-muted")}>
        <span className="sr-only">Removed: </span>
        {v.text}
      </del>
    ) : (
      <ins className={cn(INS_MARK, "font-medium")}>
        <span className="sr-only">Added: </span>
        {v.text}
      </ins>
    );
  const pieces = before ? wordDiff(v.text, other.text) : wordDiff(other.text, v.text);
  return (
    <span className={before ? "text-foreground-muted" : "font-medium"}>
      {pieces.map((p, i) =>
        p.op === "same" ? (
          <Fragment key={i}>{p.text}</Fragment>
        ) : p.op === "del" && before ? (
          <del key={i} className={DEL_MARK}>
            {p.text}
          </del>
        ) : p.op === "add" && !before ? (
          <ins key={i} className={INS_MARK}>
            {p.text}
          </ins>
        ) : null
      )}
    </span>
  );
}

const isNothing = (v?: Value) => v != null && (v.rel ? v.peers.length === 0 : v.empty);
const nothingLabel = (v: Value) => (v.rel ? "No peers" : "No value");

/** No value, as a grey dash. A cleared after value removes one: that is a change, so it is green. */
function NoValue({ v, cleared }: { v: Value; cleared: boolean }) {
  return cleared ? (
    <ins className={cn(INS_MARK, "px-1 text-(--change-create-text)")} title={nothingLabel(v)}>
      —<span className="sr-only">{nothingLabel(v)}</span>
    </ins>
  ) : (
    <span className="text-subtle-muted" title={nothingLabel(v)}>
      —<span className="sr-only">{nothingLabel(v)}</span>
    </span>
  );
}

function Side({ d, side }: { d: FieldDiff; side: "before" | "after" }) {
  const v = side === "before" ? d.before : d.after;
  const other = side === "before" ? d.after : d.before;
  if (!v) return <span className="text-subtle text-xs">not in the plan</span>;
  if (isNothing(v))
    return <NoValue v={v} cleared={side === "after" && other != null && !isNothing(other)} />;
  return <ColumnCell v={v} other={other} side={side} />;
}

function SetBy({ d, className }: { d: FieldDiff; className?: string }) {
  const who = d.change?.updated_by?.display_label;
  if (!who) return <span className={cn("text-subtle text-xs", className)}>not recorded</span>;
  return (
    <span
      className={cn(
        "flex min-w-0 flex-col text-xs leading-4",
        d.other ? "text-warning" : "text-subtle",
        className
      )}
    >
      <span className="flex min-w-0 items-center gap-1">
        {d.other && <WarningIcon className="size-3 shrink-0" />}
        <span className="truncate" title={who}>
          {who}
        </span>
      </span>
      <span className="tabular-nums">{dateOf(d.change?.updated_at)}</span>
      {d.other && <span className="sr-only">Not the import account.</span>}
    </span>
  );
}

function SameValue({ d }: { d: FieldDiff }) {
  if (d.after.rel) return <Peers peers={d.after.peers} />;
  return d.after.empty ? <span className="text-subtle italic">empty</span> : d.after.text;
}

function Unchanged({
  same,
  children,
}: {
  same: FieldDiff[];
  children: (d: FieldDiff) => ReactNode;
}) {
  const [open, setOpen] = useState(false);
  if (same.length === 0) return null;
  return (
    <div className="flex flex-col">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className={cn(
          "-mx-1 flex h-8 cursor-pointer items-center gap-1.5 self-start rounded-md px-1 text-subtle text-xs hover:text-foreground pointer-coarse:h-11",
          FOCUS_INSET
        )}
      >
        <ChevronRightIcon className={expandChevron({ open })} />
        {open ? "Hide" : "Show"} {same.length} unchanged {same.length === 1 ? "field" : "fields"}
      </button>
      {open && same.map((d) => <Fragment key={d.name}>{children(d)}</Fragment>)}
    </div>
  );
}

function NodeId({ view }: { view: OperationView }) {
  if (!view.op.destination_id) return null;
  return (
    <p className="flex min-w-0 items-center gap-1.5 text-subtle text-xs">
      Node ID{" "}
      <span className="break-all font-mono text-foreground-muted">{view.op.destination_id}</span>
      <CopyButton value={view.op.destination_id} label="Copy node ID" />
    </p>
  );
}

/* ---------- the Ledger ---------- */

const COLS =
  "grid grid-cols-[6.5rem_minmax(0,1fr)_minmax(0,1fr)] @min-[540px]:grid-cols-[7rem_minmax(0,1fr)_minmax(0,1fr)_8.5rem]";
const CELL = "min-w-0 px-1.5 py-2 text-[13px] leading-5 [overflow-wrap:anywhere]";
const HEAD = "sticky top-0 z-[1] border-b bg-content px-1.5 pt-0.5 pb-1.5 text-subtle text-xs";

/**
 * What one operation changes: each changed field with its value now and the planned value, and
 * who set the value it replaces. A create uses the same layout, with no value before.
 */
export function LedgerDiff({ view, ctx }: { view: OperationView; ctx: RowContext }) {
  if (view.op.action === "delete")
    return (
      <Note>
        This node is in Infrahub but not in the source.{" "}
        {ctx.done ? "Apply did not delete it" : "Apply does not delete it"}, so nothing about it
        changes.
      </Note>
    );
  const { changed, same, known } = diffOf(view, ctx);
  const [now, planned] = words(ctx.done);
  const create = view.op.action === "create";
  return (
    <div className="@container flex flex-col gap-3">
      <p className="text-subtle text-xs">
        {create
          ? `Creates the node with ${changed.length} fields.`
          : `Changes ${changed.length} of ${changed.length + same.length} fields`}
      </p>
      {!known && !create && <Note>{missingBefore(ctx.done)}</Note>}
      <div className={COLS}>
        <span className={cn(HEAD, "col-start-1")}>Field</span>
        <span className={cn(HEAD, "col-start-2")}>{now}</span>
        <span className={cn(HEAD, "col-start-3")}>{planned}</span>
        <span className={cn(HEAD, "col-start-4 hidden @min-[540px]:block")}>Set by</span>
        {changed.map((d) => {
          const tint = d.other && "bg-warning-surface/40";
          const line = create ? "border-b" : "@min-[540px]:border-b";
          const tall = !create && "row-span-2 @min-[540px]:row-span-1";
          return [
            <span
              key={`${d.name}-f`}
              className={cn(
                CELL,
                "col-start-1 border-b font-mono text-foreground-muted text-xs",
                tall,
                tint
              )}
              title={d.name}
            >
              <span className="block truncate">{d.name}</span>
            </span>,
            <span
              key={`${d.name}-b`}
              className={cn(CELL, "col-start-2", line, !create && "pb-1 @min-[540px]:pb-2", tint)}
            >
              <Side d={d} side="before" />
            </span>,
            <span
              key={`${d.name}-a`}
              className={cn(CELL, "col-start-3", line, !create && "pb-1 @min-[540px]:pb-2", tint)}
            >
              <Side d={d} side="after" />
            </span>,
            create ? (
              <span
                key={`${d.name}-s`}
                className={cn(CELL, "col-start-4 hidden border-b @min-[540px]:block")}
              />
            ) : (
              <span
                key={`${d.name}-s`}
                className={cn(
                  CELL,
                  "col-span-2 col-start-2 border-b pt-0 @min-[540px]:col-span-1 @min-[540px]:col-start-4 @min-[540px]:pt-2",
                  tint
                )}
              >
                <span className="text-subtle text-xs @min-[540px]:hidden">Set by </span>
                <SetBy
                  d={d}
                  className="inline-flex flex-row flex-wrap gap-x-1.5 @min-[540px]:flex @min-[540px]:flex-col @min-[540px]:gap-x-0"
                />
              </span>
            ),
          ];
        })}
      </div>
      <Unchanged same={same}>
        {(d) => (
          <div className="grid grid-cols-[6.5rem_minmax(0,1fr)] border-b @min-[540px]:grid-cols-[7rem_minmax(0,1fr)]">
            <span className={cn(CELL, "py-1.5 font-mono text-subtle text-xs")}>
              <span className="block truncate">{d.name}</span>
            </span>
            <span className={cn(CELL, "py-1.5 text-foreground-muted")}>
              <SameValue d={d} />
            </span>
          </div>
        )}
      </Unchanged>
      <NodeId view={view} />
    </div>
  );
}
