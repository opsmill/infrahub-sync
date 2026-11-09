import { Fragment, type ReactNode } from "react";
import { cn } from "tailwind-variants";

import type { PlanLastChange, PlanOperation } from "#/api/types";
import { ChevronRightIcon } from "#/components/icons";
import { CopyButton, expandChevron, FOCUS_INSET } from "#/components/page";
import { CHANGE_TONE, When } from "#/components/run-status";
import { formatAbsolute } from "#/lib/time";

import {
  ACTION_WORDS,
  displayValue,
  type Field,
  fieldText,
  isEmptyValue,
  levelLabel,
  nodeLabel,
  type ScopeEntry,
} from "./plan-values";

/** One operation with everything its row and panel read, computed once per plan. */
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

const ROW =
  "grid w-full min-h-11 grid-cols-[16px_minmax(0,1fr)_14px] items-start gap-x-3 gap-y-0.5 px-4 py-2.5 text-left sm:min-h-0 sm:grid-cols-[16px_minmax(0,1fr)_fit-content(40%)_14px] sm:items-center sm:py-2";

function Sign({ action }: { action: PlanOperation["action"] }) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        "col-start-1 row-start-1 mt-0.5 grid size-4 place-items-center rounded-[5px] font-mono font-semibold text-[11px] leading-none sm:mt-0",
        CHANGE_TONE[action].chip
      )}
    >
      {CHANGE_TONE[action].sign}
    </span>
  );
}

function NameCell({ view, muted }: { view: OperationView; muted?: boolean }) {
  const { name, levels } = view;
  return (
    <span
      className="col-start-2 row-start-1 flex min-w-0 flex-col sm:flex-row sm:items-baseline sm:gap-1.5"
      title={view.label}
    >
      <span
        className={cn(
          "min-w-0 font-medium text-[13px] [overflow-wrap:anywhere] sm:max-w-[70%] sm:shrink-0 sm:truncate",
          muted ? "text-foreground-muted" : "text-foreground"
        )}
      >
        <span className="sr-only">{ACTION_WORDS[view.op.action].word} </span>
        {name}
      </span>
      {levels.length > 0 && (
        <span
          className="min-w-0 truncate text-subtle text-xs sm:text-[13px]"
          title={levels
            .flat()
            .map((e) => `${e.field}: ${e.label}${e.kind ? ` (${e.kind})` : ""}`)
            .join("; ")}
        >
          <span className="sr-only">in </span>
          {levels.map((level, i) => (
            <Fragment key={level.map((e) => e.field).join()}>
              {i > 0 && (
                <>
                  <span aria-hidden="true" className="px-1 text-subtle-muted">
                    ›
                  </span>
                  <span className="sr-only">, </span>
                </>
              )}
              {levelLabel(level)}
            </Fragment>
          ))}
        </span>
      )}
    </span>
  );
}

const changedFields = (view: OperationView) =>
  view.changes ? view.fields.filter((f) => view.changes?.has(f.name)) : [];

function listNames(names: string[]): string {
  if (names.length === 2) return `${names[0]} and ${names[1]}`;
  return `${names[0]}, ${names[1]} and ${names.length - 2} more`;
}

function Summary({ view, done }: { view: OperationView; done: boolean }) {
  const { op, fields } = view;
  const changed = changedFields(view);
  const cell =
    "col-start-2 row-start-2 min-w-0 truncate text-subtle text-xs sm:col-start-3 sm:row-start-1 sm:max-w-[48ch]";
  if (op.action === "create") {
    return (
      <span className={cell}>
        {fields.length} {fields.length === 1 ? "field" : "fields"}
      </span>
    );
  }
  if (changed.length === 1) {
    const f = changed[0];
    const value = fieldText(f);
    return (
      <span className={cell} title={`${f.name}: ${value}`}>
        <span className="font-mono">{f.name}:</span> <span className="sr-only">new value </span>
        <span className="font-medium text-foreground">{value}</span>
      </span>
    );
  }
  if (changed.length > 1) {
    return (
      <span className={cell}>
        {done ? "Changed" : "Changes"} {listNames(changed.map((f) => f.name))}
      </span>
    );
  }
  return <span className={cell}>Changed fields not shown</span>;
}

function LastChangeLine({
  change,
  many,
  importAccountId,
  done,
}: {
  change: PlanLastChange;
  many: boolean;
  importAccountId?: string;
  done: boolean;
}) {
  const who = change.updated_by?.display_label ?? change.updated_by?.id;
  const at = change.updated_at;
  const other =
    importAccountId !== undefined &&
    Boolean(change.updated_by?.id) &&
    change.updated_by?.id !== importAccountId;
  const replaces = done ? "Replaced" : "Replaces";
  let text: ReactNode;
  if (who && at)
    text = (
      <>
        {replaces} a value that {who} changed on {formatAbsolute(at)} (<When iso={at} />
        ).
      </>
    );
  else if (who)
    text = (
      <>
        {replaces} a value that {who} changed. The time is not recorded.
      </>
    );
  else if (at)
    text = (
      <>
        {replaces} a value changed on {formatAbsolute(at)} (<When iso={at} />
        ). The account is not recorded.
      </>
    );
  // The API does not say why both are missing: no value, or metadata the plan did not read.
  else text = "Who last changed this field, and when, is not recorded.";
  return (
    <span className={cn("mt-0.5 block text-xs", other ? "text-warning" : "text-subtle")}>
      {text}
      {change.field_type === "relationship" &&
        many &&
        " For a relationship with several peers, this is the most recent peer change."}
      {other && " This account is not the import account."}
    </span>
  );
}

function FieldValue({ field, changed }: { field: Field; changed: boolean }) {
  const newValue = changed && <span className="sr-only">New value: </span>;
  if (field.rel) {
    const { peers, peerKind } = field.rel;
    return (
      // The negative margin hangs the chip padding outside the column, so chip text lines up with plain values.
      <span className={cn("flex flex-wrap items-center gap-1", peers.length > 0 && "-ml-1.5")}>
        {newValue}
        {peers.length === 0 ? (
          <span className="text-subtle">no peers</span>
        ) : (
          peers.map((peer, i) => (
            <span
              key={i}
              className={cn(
                "inline-flex items-center rounded-md px-1.5 text-xs leading-5",
                changed
                  ? "bg-diff-updated-surface font-medium text-foreground"
                  : "bg-content-strong text-foreground-muted"
              )}
            >
              {nodeLabel(peer)}
            </span>
          ))
        )}
        {peerKind && (
          <span
            className={cn(
              "basis-full font-mono text-[11px] text-subtle-muted sm:basis-auto",
              peers.length > 0 && "pl-1.5"
            )}
          >
            {peerKind}
          </span>
        )}
      </span>
    );
  }
  const empty = isEmptyValue(field.value);
  const text = displayValue(field.value);
  if (!changed)
    return <span className={empty ? "text-subtle" : "text-foreground-muted"}>{text}</span>;
  return (
    <>
      {newValue}
      <span
        className={cn(
          "-ml-1 rounded-sm bg-diff-updated-surface px-1 font-medium [box-decoration-break:clone]",
          empty ? "text-subtle" : "text-foreground"
        )}
      >
        {text}
      </span>
    </>
  );
}

function FieldList({
  fields,
  changes,
  ctx,
}: {
  fields: Field[];
  changes?: Map<string, PlanLastChange>;
  ctx: RowContext;
}) {
  return (
    <dl className="grid grid-cols-1 gap-y-3 sm:grid-cols-[10rem_minmax(0,1fr)] sm:gap-x-6 sm:gap-y-1">
      {fields.map((f) => {
        const change = changes?.get(f.name);
        return (
          <div key={f.name} className="flex min-w-0 flex-col gap-0.5 sm:contents">
            <dt
              className="truncate font-mono text-foreground-muted text-xs leading-5"
              title={f.name}
            >
              {f.name}
            </dt>
            <dd className="min-w-0 text-[13px] leading-5 [overflow-wrap:anywhere]">
              <FieldValue field={f} changed={Boolean(change)} />
              {change && (
                <LastChangeLine
                  change={change}
                  many={f.rel?.many ?? false}
                  importAccountId={ctx.importAccountId}
                  done={ctx.done}
                />
              )}
            </dd>
          </div>
        );
      })}
    </dl>
  );
}

function Group({
  title,
  divided,
  children,
}: {
  title: string;
  divided?: boolean;
  children: ReactNode;
}) {
  return (
    <div className={cn("flex flex-col gap-2", divided && "border-t pt-3")}>
      <h4 className="font-medium text-foreground text-xs">{title}</h4>
      {children}
    </div>
  );
}

function Panel({ view, ctx, id }: { view: OperationView; ctx: RowContext; id: string }) {
  const { op, fields, changes } = view;
  const changed = changedFields(view);
  const unchanged = changes ? fields.filter((f) => !changes.has(f.name)) : fields;
  const sends = ctx.done ? "sent" : "sends";
  return (
    <div id={id} className="flex flex-col gap-4 border-t bg-content-muted px-4 py-3 sm:pl-11">
      {op.action === "create" && fields.length > 0 && <FieldList fields={fields} ctx={ctx} />}
      {op.action === "update" && changed.length > 0 && (
        <>
          <Group title="Changed by this update">
            <FieldList fields={changed} changes={changes} ctx={ctx} />
            <p className="text-subtle text-xs">
              The plan does not store the values these fields have now. The last-change records were
              read when the plan was created.
            </p>
          </Group>
          {unchanged.length > 0 && (
            <Group title="Sent unchanged with the update" divided>
              <FieldList fields={unchanged} ctx={ctx} />
            </Group>
          )}
        </>
      )}
      {op.action === "update" && changed.length === 0 && fields.length > 0 && (
        <>
          <p className="max-w-[72ch] text-pretty text-subtle text-xs">
            {ctx.recorded
              ? `The plan records no changed field for this update, for example when the update clears a relationship. Apply ${sends} every field below.`
              : `The changed fields of this update are not shown. Apply ${sends} every field below.`}
          </p>
          <Group title="Fields the update sends">
            <FieldList fields={fields} ctx={ctx} />
          </Group>
        </>
      )}
      {op.destination_id && (
        <dl className="grid grid-cols-1 gap-y-0.5 sm:grid-cols-[10rem_minmax(0,1fr)] sm:gap-x-6 sm:gap-y-1">
          <dt className="text-subtle text-xs leading-5">Destination node ID</dt>
          <dd className="flex min-w-0 items-center gap-1.5 leading-5">
            <span className="break-all font-mono text-xs">{op.destination_id}</span>
            <CopyButton value={op.destination_id} label="Copy node ID" />
          </dd>
        </dl>
      )}
    </div>
  );
}

function DeleteNote({ done }: { done: boolean }) {
  return (
    <span className="col-start-2 row-start-2 min-w-0 whitespace-normal text-subtle text-xs sm:col-start-3 sm:row-start-1 sm:truncate">
      {done
        ? "Not in the source. Apply did not delete it."
        : "Not in the source. Apply does not delete it."}
    </span>
  );
}

export function OperationRow({
  view,
  ctx,
  open,
  onToggle,
}: {
  view: OperationView;
  ctx: RowContext;
  open: boolean;
  onToggle: () => void;
}) {
  const { op } = view;
  const rowId = `op-${op.operation_id}`;
  const panelId = `panel-${op.operation_id}`;
  const expandable = view.fields.length > 0 || Boolean(op.destination_id);

  if (op.action === "delete" && !expandable) {
    return (
      <li id={rowId} className="scroll-mt-12">
        {/* tabIndex lets "Show the next…" move focus here; the row has no action of its own. */}
        <div tabIndex={-1} className={cn(ROW, FOCUS_INSET)}>
          <Sign action="delete" />
          <NameCell view={view} muted />
          <DeleteNote done={ctx.done} />
        </div>
      </li>
    );
  }

  return (
    <li id={rowId} className="scroll-mt-12">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        className={cn(
          ROW,
          "cursor-pointer transition-colors duration-150 ease-out hover:bg-content-muted active:bg-content-strong motion-reduce:transition-none",
          FOCUS_INSET
        )}
      >
        <Sign action={op.action} />
        <NameCell view={view} muted={op.action === "delete"} />
        {op.action === "delete" ? (
          <DeleteNote done={ctx.done} />
        ) : (
          <Summary view={view} done={ctx.done} />
        )}
        <ChevronRightIcon
          className={expandChevron({
            open,
            className: "col-start-3 row-start-1 self-center text-subtle sm:col-start-4",
          })}
        />
      </button>
      {open && <Panel view={view} ctx={ctx} id={panelId} />}
    </li>
  );
}
