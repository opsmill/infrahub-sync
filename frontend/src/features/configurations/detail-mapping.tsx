import { Card } from "@infrahub/ui";
import { Link } from "@tanstack/react-router";
import { type ReactNode, useState } from "react";
import { cn } from "tailwind-variants";

import { ArrowRightIcon, ChevronRightIcon, XIcon } from "#/components/icons";
import { FOCUS_INSET } from "#/components/page";
import { PANEL_ICON_BUTTON, ShiftPanel } from "#/components/shift-panel";
import {
  fieldSource,
  filterWords,
  groupBySource,
  type MappingChange,
  type MappingDiff,
  type MappingEntry,
} from "#/domain/mapping";
import { fmt, plural } from "#/domain/run-state";

/** The rows shown before "Show more". Whole source models are shown, so the count can be higher. */
const ROWS_SHOWN = 8;

const HOVER =
  "transition-colors duration-150 ease-out hover:bg-content-muted aria-expanded:bg-content-strong motion-reduce:transition-none";
const ROW =
  "grid min-h-9 grid-cols-1 items-center gap-y-0.5 rounded-lg px-2.5 py-1.5 text-[13px] @2xl:grid-cols-[minmax(0,1fr)_11rem_minmax(0,1fr)] @2xl:py-0 pointer-coarse:min-h-11";

const CHANGE_WORD = { added: "New", changed: "Changed", removed: "Removed" };

/** How a mapping or a field differs from the version compared with. */
function ChangeBadge({ status }: { status: MappingChange["status"] }) {
  return (
    <span
      className={cn(
        "shrink-0 rounded-md px-1.5 font-medium font-sans text-[11px] leading-5",
        status === "added" && "bg-(--change-create-surface) text-(--change-create-text)",
        status === "changed" && "bg-(--change-update-surface) text-(--change-update-text)",
        status === "removed" && "bg-content-strong text-foreground-muted"
      )}
    >
      {CHANGE_WORD[status]}
    </span>
  );
}

const isRemoved = (entry: MappingEntry) => entry.change?.status === "removed";

/** A row that opens the mapping in the panel. A removed mapping is text only. */
function Row({
  configId,
  entry,
  open,
  children,
}: {
  configId: string;
  entry: MappingEntry;
  open?: string;
  children: ReactNode;
}) {
  if (isRemoved(entry)) return <div className={cn(ROW, "text-subtle")}>{children}</div>;
  const selected = entry.key === open;
  return (
    <Link
      to="/configs/$configId"
      params={{ configId }}
      search={(s) => ({ ...s, kind: selected ? undefined : entry.key })}
      replace
      resetScroll={false}
      aria-expanded={selected}
      className={cn(ROW, HOVER, FOCUS_INSET)}
    >
      {children}
    </Link>
  );
}

/** A source model is written once and branches to the kinds it fills. */
function Groups({
  configId,
  entries,
  open,
}: {
  configId: string;
  entries: MappingEntry[];
  open?: string;
}) {
  return (
    <ul className="p-1.5">
      {groupBySource(entries).flatMap((group) =>
        group.map((entry, i) => (
          <li key={entry.key}>
            <Row configId={configId} entry={entry} open={open}>
              <span
                className={cn(
                  "order-2 truncate font-mono text-foreground-muted text-xs @2xl:order-none @2xl:pr-4 @2xl:text-right",
                  i > 0 && "@2xl:invisible"
                )}
              >
                <span className="font-sans text-subtle @2xl:hidden">from </span>
                {entry.mapping.mapping}
              </span>
              <span aria-hidden="true" className="relative hidden h-9 items-center @2xl:flex">
                {group.length > 1 && (
                  <span
                    className={cn(
                      "absolute left-10 w-px bg-border-strong",
                      i === 0 ? "top-1/2 -bottom-px" : "-top-px",
                      i > 0 && (i === group.length - 1 ? "bottom-1/2" : "-bottom-px")
                    )}
                  />
                )}
                <span className={cn("h-px flex-1 bg-border-strong", i > 0 && "ml-10")} />
                <ArrowRightIcon className="mr-3 -ml-2 size-3.5 text-subtle" />
              </span>
              <span className="flex min-w-0 items-center gap-2 @2xl:pl-4">
                <span
                  className={cn(
                    "min-w-0 shrink-0 truncate font-medium font-mono",
                    isRemoved(entry) && "font-normal line-through"
                  )}
                >
                  {entry.mapping.name}
                </span>
                {entry.change && <ChangeBadge status={entry.change.status} />}
                <span className="min-w-0 flex-1 truncate font-mono text-subtle text-xs">
                  {entry.conditions.join(" and ")}
                </span>
                <span className="ml-auto shrink-0 whitespace-nowrap text-subtle text-xs tabular-nums">
                  {plural((entry.mapping.fields ?? []).length, "field", "fields")}
                </span>
              </span>
            </Row>
          </li>
        ))
      )}
    </ul>
  );
}

const EXPAND = cn(
  HOVER,
  FOCUS_INSET,
  "flex min-h-10 w-full cursor-pointer items-center justify-center gap-1.5 rounded-b-[inherit] border-t font-medium text-[13px] text-foreground-muted hover:text-foreground pointer-coarse:min-h-11"
);

/**
 * The mappings of one version: each source model next to the kinds it fills. While the version is
 * compared with an earlier one, the mappings that differ come first and the others are folded.
 */
export function MappingList({
  configId,
  entries,
  removed,
  diff,
  open,
}: {
  configId: string;
  entries: MappingEntry[];
  /** The mappings of the version compared with that this version no longer has. */
  removed: MappingEntry[];
  diff?: MappingDiff;
  /** The key of the mapping open in the panel. */
  open?: string;
}) {
  const [all, setAll] = useState(false);
  const expand = (label: string) => (
    <button type="button" aria-expanded={all} onClick={() => setAll((v) => !v)} className={EXPAND}>
      {label}
      <ChevronRightIcon className={cn("size-3.5", all ? "-rotate-90" : "rotate-90")} />
    </button>
  );

  if (diff) {
    // The mappings that differ stay first, in the same order, whether the others are shown or not.
    const differing = [...entries, ...removed].filter((e) => e.change);
    const unchanged = entries.filter((e) => !e.change);
    return (
      <Card className="@container overflow-hidden">
        {differing.length > 0 ? (
          <Groups configId={configId} entries={differing} open={open} />
        ) : (
          <p className="px-4 py-5 text-center text-foreground-muted text-sm">
            The two versions declare the same mappings.
          </p>
        )}
        {all && unchanged.length > 0 && (
          <>
            <h3 className="border-t px-4 pt-2.5 font-medium text-subtle text-xs">
              Same in version {diff.against}
            </h3>
            <Groups configId={configId} entries={unchanged} open={open} />
          </>
        )}
        {unchanged.length > 0 &&
          expand(
            all
              ? "Show less"
              : `Show ${plural(unchanged.length, "unchanged mapping", "unchanged mappings")}`
          )}
      </Card>
    );
  }

  // Whole source models are shown, so that "Show more" adds rows below and moves none.
  const groups = groupBySource(entries);
  let rows = 0;
  const first = groups.filter((group) => {
    const shown = rows < ROWS_SHOWN;
    rows += group.length;
    return shown;
  });
  return (
    <Card className="@container overflow-hidden">
      <Groups configId={configId} entries={all ? entries : first.flat()} open={open} />
      {first.length < groups.length && expand(all ? "Show less" : "Show more")}
    </Card>
  );
}

const BADGE_AFTER = "ml-2 inline-block align-middle";

/** The fields, filters and computed fields of one mapping, with what differs when compared. */
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
  const fields = mapping.fields ?? [];
  const filters = mapping.filters ?? [];
  const transforms = mapping.transforms ?? [];
  const added = new Set(change?.fieldsAdded);
  const changed = new Set(change?.fieldsChanged);
  const before = new Map((change?.was?.fields ?? []).map((f) => [f.name, f]));
  const removed = (change?.fieldsRemoved ?? []).flatMap((name) => before.get(name) ?? []);
  return (
    <div className="flex flex-col gap-3 text-[13px]">
      <p className="font-mono text-foreground-muted text-xs [overflow-wrap:anywhere]">
        from {mapping.mapping}
        {mapping.identifiers?.length ? (
          <span className="font-sans text-subtle"> · matched on </span>
        ) : null}
        {mapping.identifiers?.join(", ")}
      </p>
      {change && against !== undefined && (
        <p className="text-pretty text-foreground-muted text-xs">
          {change.status === "added"
            ? `Version ${against} does not have this mapping.`
            : `Compared with version ${against}: ${
                [
                  change.fieldsAdded.length &&
                    `${plural(change.fieldsAdded.length, "field", "fields")} added`,
                  change.fieldsChanged.length &&
                    `${plural(change.fieldsChanged.length, "field", "fields")} changed`,
                  change.fieldsRemoved.length &&
                    `${plural(change.fieldsRemoved.length, "field", "fields")} removed`,
                  change.rulesChanged &&
                    "the source model, identifiers, filters or computed fields differ",
                ]
                  .filter(Boolean)
                  .join(", ") || "the order of the fields differs"
              }.`}
        </p>
      )}
      {(fields.length > 0 || removed.length > 0) && (
        <table className="max-w-3xl text-left">
          <thead>
            <tr className="text-[11px] text-subtle">
              <th scope="col" className="pr-3 pb-1 font-medium">
                {sourceLabel} field
              </th>
              <th scope="col" className="w-6 pb-1">
                <span className="sr-only">to</span>
              </th>
              <th scope="col" className="pb-1 font-medium">
                {destinationLabel} attribute or relationship
              </th>
            </tr>
          </thead>
          <tbody>
            {fields.map((f) => {
              const was = changed.has(f.name) ? before.get(f.name) : undefined;
              return (
                <tr key={f.name} className="align-top">
                  <td className="py-0.5 pr-3 font-mono text-foreground-muted text-xs leading-5 [overflow-wrap:anywhere]">
                    {fieldSource(f)}
                    {was && fieldSource(was) !== fieldSource(f) && (
                      <span className="ml-1.5 font-sans text-[11px] text-subtle">
                        was <span className="font-mono line-through">{fieldSource(was)}</span>
                      </span>
                    )}
                  </td>
                  <td className="py-0.5">
                    <ArrowRightIcon className="mt-1 size-3 text-subtle-muted" />
                  </td>
                  <td className="py-0.5 font-mono text-xs leading-5 [overflow-wrap:anywhere]">
                    {f.name}
                    {f.reference && (
                      <code className="ml-1.5 rounded bg-content-strong px-1 py-px text-[0.85em] text-foreground-muted">
                        <span className="sr-only">peer kind </span>
                        {f.reference}
                      </code>
                    )}
                    {added.has(f.name) && (
                      <span className={BADGE_AFTER}>
                        <ChangeBadge status="added" />
                      </span>
                    )}
                    {was && (
                      <span className={BADGE_AFTER}>
                        <ChangeBadge status="changed" />
                      </span>
                    )}
                  </td>
                </tr>
              );
            })}
            {removed.map((f) => (
              <tr key={f.name} className="align-top text-subtle">
                <td className="py-0.5 pr-3 font-mono text-xs leading-5 line-through [overflow-wrap:anywhere]">
                  {fieldSource(f)}
                </td>
                <td className="py-0.5">
                  <ArrowRightIcon className="mt-1 size-3 text-subtle-muted" />
                </td>
                <td className="py-0.5 font-mono text-xs leading-5 [overflow-wrap:anywhere]">
                  <span className="line-through">{f.name}</span>
                  <span className={BADGE_AFTER}>
                    <ChangeBadge status="removed" />
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {filters.length > 0 && (
        <div>
          <h3 className="mb-1 text-subtle text-xs">Only source records where</h3>
          <ul className="flex flex-col gap-0.5 font-mono text-xs">
            {filters.map((f) => (
              <li key={filterWords(f)}>{filterWords(f)}</li>
            ))}
          </ul>
        </div>
      )}
      {transforms.length > 0 && (
        <div>
          <h3 className="mb-1 text-subtle text-xs">Computed source fields</h3>
          <div className="flex flex-col gap-2">
            {transforms.map((t) => (
              <div key={t.field}>
                <p className="font-mono text-xs">{t.field} =</p>
                <pre className="mt-0.5 max-w-3xl whitespace-pre-wrap break-words rounded-md border bg-content px-2 py-1 font-mono text-[11px] text-foreground-muted leading-5">
                  {t.expression}
                </pre>
              </div>
            ))}
          </div>
        </div>
      )}
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
