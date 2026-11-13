import { Link } from "@tanstack/react-router";
import { Fragment, type ReactNode } from "react";
import { cn } from "tailwind-variants";

import { ArrowRightIcon, ChevronRightIcon, XIcon } from "#/components/icons";
import { FOCUS_INSET } from "#/components/page";
import { PANEL_ICON_BUTTON, ShiftPanel } from "#/components/shift-panel";
import {
  fieldSource,
  filterOperation,
  filterWords,
  type MappingChange,
  type MappingEntry,
  type MappingField,
} from "#/domain/mapping";
import { fmt, plural } from "#/domain/run-state";

import { ChangeBadge } from "./detail-mapping";

const CODE = "font-mono text-xs";

/** A name that wraps after a dot or an underscore, or before a capital, before it wraps inside a word. */
function Name({ text }: { text: string }) {
  return (
    <>
      {text.split(/(?<=[._])|(?<=[a-z])(?=[A-Z])/).map((part, i) => (
        <Fragment key={i}>
          {i > 0 && <wbr />}
          {part}
        </Fragment>
      ))}
    </>
  );
}

/** Where a field's value comes from: a source field in the code face, or a fixed value. */
function FieldSource({ field }: { field: MappingField }) {
  if (field.mapping != null)
    return (
      <span className={CODE}>
        <Name text={field.mapping} />
      </span>
    );
  if (field.static != null)
    return (
      <span>
        <span className="text-foreground-muted">Fixed value </span>
        <span className={CODE}>{JSON.stringify(field.static)}</span>
      </span>
    );
  return <span className="text-subtle">{fieldSource(field)}</span>;
}

function PeerKind({ name }: { name: string }) {
  return (
    <code className="mt-0.5 ml-1.5 inline-block max-w-full rounded bg-content-strong px-1 py-px align-top font-mono text-[11px] text-foreground-muted leading-4">
      <span className="sr-only">peer kind </span>
      <Name text={name} />
    </code>
  );
}

/** An outline with a header: what the mapping declares about one of the two systems. */
function Frame({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="min-w-0 overflow-hidden rounded-lg border">
      <h3 className="border-b bg-content-muted px-3 py-1.5 font-medium text-foreground-muted text-xs">
        {title}
      </h3>
      <div className="px-3 py-0.5">{children}</div>
    </section>
  );
}

/** Label and value rows, as the run page's Started step writes them. */
function Facts({ rows }: { rows: { label: string; value: ReactNode }[] }) {
  return (
    <dl className="flex flex-col">
      {rows.map((r) => (
        <div key={r.label} className="flex items-baseline gap-3 border-b py-1.5 last:border-b-0">
          <dt className="w-[5.5rem] shrink-0 text-subtle text-xs">{r.label}</dt>
          <dd className="min-w-0 flex-1 text-[13px] leading-5 [overflow-wrap:anywhere]">
            {r.value}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function Filters({ filters }: { filters: NonNullable<MappingEntry["mapping"]["filters"]> }) {
  return (
    <ul className="flex flex-col gap-0.5">
      {filters.map((f) => (
        <li key={filterWords(f)}>
          <span className={CODE}>{f.field}</span>{" "}
          <span className="text-foreground-muted">{filterOperation(f)}</span>
          {f.value != null && <span className={CODE}> {JSON.stringify(f.value)}</span>}
        </li>
      ))}
    </ul>
  );
}

function ComputedFields({
  transforms,
}: {
  transforms: NonNullable<MappingEntry["mapping"]["transforms"]>;
}) {
  return (
    <div className="flex flex-col gap-2.5">
      {transforms.map((t) => (
        <div key={t.field} className="flex flex-col gap-1">
          <p>
            <span className={CODE}>
              <Name text={t.field} />
            </span>{" "}
            <span className="text-subtle text-xs">=</span>
          </p>
          <pre className="whitespace-pre-wrap rounded-md border bg-content-muted px-2 py-1 font-mono text-[11px] text-foreground-muted leading-5 [overflow-wrap:anywhere]">
            {t.expression}
          </pre>
        </div>
      ))}
    </div>
  );
}

/** How the mapping differs from the version compared with, in one sentence. */
function comparison(change: MappingChange, against: number): string {
  if (change.status === "added") return `Version ${against} does not have this mapping.`;
  const parts = [
    change.fieldsAdded.length && `${plural(change.fieldsAdded.length, "field", "fields")} added`,
    change.fieldsChanged.length &&
      `${plural(change.fieldsChanged.length, "field", "fields")} changed`,
    change.fieldsRemoved.length &&
      `${plural(change.fieldsRemoved.length, "field", "fields")} removed`,
    change.rulesChanged && "the source model, identifiers, filters or computed fields differ",
  ].filter(Boolean);
  return `Compared with version ${against}: ${parts.join(", ") || "the order of the fields differs"}.`;
}

interface FieldRow {
  field: MappingField;
  status?: MappingChange["status"];
  /** The field in the version compared with, when its source differs. */
  was?: MappingField;
}

/** The declared fields in their order, then the fields that only the version compared with has. */
function fieldRows({ mapping, change }: MappingEntry): FieldRow[] {
  const added = new Set(change?.fieldsAdded);
  const changed = new Set(change?.fieldsChanged);
  const before = new Map((change?.was?.fields ?? []).map((f) => [f.name, f]));
  const declared = (mapping.fields ?? []).map<FieldRow>((field) => {
    const old = changed.has(field.name) ? before.get(field.name) : undefined;
    return {
      field,
      status: added.has(field.name) ? "added" : old ? "changed" : undefined,
      was: old && fieldSource(old) !== fieldSource(field) ? old : undefined,
    };
  });
  const removed = (change?.fieldsRemoved ?? []).flatMap<FieldRow>((name) => {
    const field = before.get(name);
    return field ? [{ field, status: "removed" }] : [];
  });
  return [...declared, ...removed];
}

const COLUMN_TITLE =
  "border-b pb-1.5 align-bottom font-medium font-mono text-foreground-muted text-xs";
const CELL = "border-b py-1.5 [overflow-wrap:anywhere] group-last:border-b-0";
/* The badges have a column of their own only where the table is wide enough for it. */
const BADGE_COLUMN = "hidden w-[4.75rem] @min-[24rem]:table-column";
const BADGE_CELL = "hidden @min-[24rem]:table-cell";

/** Each field of the source model next to the field of the kind it fills. */
function FieldTable({ entry }: { entry: MappingEntry }) {
  const rows = fieldRows(entry);
  const compared = rows.some((r) => r.status);
  if (rows.length === 0)
    return <p className="py-1.5 text-foreground-muted">This mapping declares no field.</p>;
  return (
    <div className="@container">
      <table className="w-full table-fixed text-left">
        <colgroup>
          <col className="w-[42%]" />
          <col className="w-5" />
          <col />
          {compared && <col className={BADGE_COLUMN} />}
        </colgroup>
        <thead>
          <tr>
            <th scope="col" className={cn(COLUMN_TITLE, "pr-3 [overflow-wrap:anywhere]")}>
              <Name text={entry.mapping.mapping} />
              <span className="sr-only"> field</span>
            </th>
            <th scope="col" className={COLUMN_TITLE}>
              <ArrowRightIcon className="mb-0.5 size-3 text-subtle-muted" />
              <span className="sr-only">to</span>
            </th>
            <th scope="col" className={cn(COLUMN_TITLE, "pr-3 [overflow-wrap:anywhere]")}>
              <Name text={entry.mapping.name} />
              <span className="sr-only"> field</span>
            </th>
            {compared && (
              <th scope="col" className={cn(COLUMN_TITLE, BADGE_CELL)}>
                <span className="sr-only">Compared</span>
              </th>
            )}
          </tr>
        </thead>
        <tbody>
          {rows.map(({ field, status, was }) => {
            const gone = status === "removed";
            const strike = gone && "line-through decoration-1";
            return (
              <tr key={field.name} className={cn("group align-top", gone && "text-subtle")}>
                <td className={cn(CELL, "pr-3", !gone && "text-foreground-muted")}>
                  <span className={cn(strike)}>
                    <FieldSource field={field} />
                  </span>
                  {was && (
                    <span className="block text-subtle text-xs">
                      was{" "}
                      <span className="line-through decoration-1">
                        <FieldSource field={was} />
                      </span>
                    </span>
                  )}
                </td>
                <td className={CELL}>
                  <ArrowRightIcon className="mt-1 size-3 text-subtle-muted" />
                </td>
                <td className={cn(CELL, "pr-3")}>
                  <span className={cn(CODE, "inline-block max-w-full align-top leading-5", strike)}>
                    <Name text={field.name} />
                  </span>
                  {field.reference && <PeerKind name={field.reference} />}
                  {status && (
                    <span className="mt-0.5 block @min-[24rem]:hidden">
                      <ChangeBadge status={status} />
                    </span>
                  )}
                </td>
                {compared && (
                  <td className={cn(CELL, BADGE_CELL, "text-right")}>
                    {status && <ChangeBadge status={status} />}
                  </td>
                )}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/**
 * One mapping: what it reads from the source system, then what it fills in the destination, with
 * what differs when the version is compared with an earlier one.
 */
function MappingDetail({
  entry,
  sourceLabel,
  destinationLabel,
  against,
}: {
  entry: MappingEntry;
  sourceLabel: string;
  destinationLabel: string;
  against?: number;
}) {
  const { mapping, change } = entry;
  return (
    <div className="flex flex-col gap-3 text-[13px] leading-5">
      {change && against !== undefined && (
        <p className="text-pretty text-foreground-muted text-xs">{comparison(change, against)}</p>
      )}
      <Frame title={`From ${sourceLabel}`}>
        <Facts
          rows={[
            {
              label: "Model",
              value: (
                <span className={CODE}>
                  <Name text={mapping.mapping} />
                </span>
              ),
            },
            ...(mapping.filters?.length
              ? [{ label: "Only where", value: <Filters filters={mapping.filters} /> }]
              : []),
            ...(mapping.transforms?.length
              ? [{ label: "Computed", value: <ComputedFields transforms={mapping.transforms} /> }]
              : []),
          ]}
        />
      </Frame>
      <Frame title={`Into ${destinationLabel}`}>
        <Facts
          rows={[
            {
              label: "Identifiers",
              value: mapping.identifiers?.length ? (
                <span className={CODE}>{mapping.identifiers.join(", ")}</span>
              ) : (
                <span className="text-subtle">No identifier declared</span>
              ),
            },
          ]}
        />
        <h4 className="mt-1 mb-1.5 flex items-center gap-2 border-t pt-2.5 font-medium">
          Fields
          <span className="rounded-full bg-content-strong px-1.5 font-normal text-[11px] text-foreground-muted leading-4 tabular-nums">
            {fmt((mapping.fields ?? []).length)}
          </span>
        </h4>
        <FieldTable entry={entry} />
      </Frame>
    </div>
  );
}

/** A link that opens another mapping in the panel, drawn as an icon button. */
function Step({
  configId,
  entry,
  label,
  up = false,
}: {
  configId: string;
  entry?: MappingEntry;
  label: string;
  up?: boolean;
}) {
  return (
    <Link
      to="/configs/$configId"
      params={{ configId }}
      search={(s) => ({ ...s, kind: entry?.key ?? s.kind })}
      replace
      resetScroll={false}
      aria-label={label}
      aria-disabled={!entry}
      tabIndex={entry ? undefined : -1}
      className={cn(PANEL_ICON_BUTTON, FOCUS_INSET)}
    >
      <ChevronRightIcon className={cn("size-3.5", up ? "-rotate-90" : "rotate-90")} />
    </Link>
  );
}

/**
 * The mapping named by `open`, in a panel on the right of the page. The previous and next buttons
 * follow the order of the declaration.
 */
export function MappingPanel({
  configId,
  version,
  entries,
  open,
  against,
  sourceLabel,
  destinationLabel,
  shiftTargetId,
  onClose,
}: {
  configId: string;
  version?: number;
  entries: MappingEntry[];
  /** The key of the mapping to show; the panel is closed when no mapping has it. */
  open?: string;
  /** The version the mapping is compared with. */
  against?: number;
  sourceLabel: string;
  destinationLabel: string;
  /** The id of the element the panel moves left to make room for itself. */
  shiftTargetId: string;
  onClose: () => void;
}) {
  const at = entries.findIndex((e) => e.key === open);
  const entry = entries[at];
  return (
    <ShiftPanel
      open={entry !== undefined}
      label={`Mapping of ${entry?.mapping.name}`}
      onClose={onClose}
      shiftTargetId={shiftTargetId}
      widthKey="mapping-panel-width"
    >
      {entry && (
        <>
          <div className="flex flex-col gap-1 border-b px-4 pt-2 pb-3">
            <div className="flex items-center gap-2">
              <span className="text-subtle text-xs">Version {version}</span>
              <span className="ml-auto flex shrink-0 items-center">
                <span className="mr-1 text-subtle text-xs tabular-nums">
                  {fmt(at + 1)} of {fmt(entries.length)}
                </span>
                <Step configId={configId} entry={entries[at - 1]} label="Previous mapping" up />
                <Step configId={configId} entry={entries[at + 1]} label="Next mapping" />
                <button
                  type="button"
                  aria-label="Close the panel"
                  onClick={onClose}
                  className={cn(PANEL_ICON_BUTTON, FOCUS_INSET)}
                >
                  <XIcon />
                </button>
              </span>
            </div>
            <h2 className="flex min-w-0 items-center gap-2 font-medium font-mono text-base">
              <span className="truncate">{entry.mapping.name}</span>
              {entry.change && <ChangeBadge status={entry.change.status} />}
            </h2>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 py-3">
            <MappingDetail
              entry={entry}
              sourceLabel={sourceLabel}
              destinationLabel={destinationLabel}
              against={against}
            />
          </div>
        </>
      )}
    </ShiftPanel>
  );
}
