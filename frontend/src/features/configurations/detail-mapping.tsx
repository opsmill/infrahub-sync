import { Card } from "@infrahub/ui";
import { Link } from "@tanstack/react-router";
import { type ReactNode, useState } from "react";
import { cn } from "tailwind-variants";

import { ArrowRightIcon, ChevronRightIcon } from "#/components/icons";
import { FOCUS_INSET } from "#/components/page";
import {
  groupBySource,
  type MappingChange,
  type MappingDiff,
  type MappingEntry,
} from "#/domain/mapping";
import { plural } from "#/domain/run-state";

/** The rows shown before "Show more". Whole source models are shown, so the count can be higher. */
const ROWS_SHOWN = 8;

const HOVER =
  "transition-colors duration-150 ease-out hover:bg-content-muted aria-expanded:bg-content-strong motion-reduce:transition-none";
const ROW =
  "grid min-h-9 grid-cols-1 items-center gap-y-0.5 rounded-lg px-2.5 py-1.5 text-[13px] @2xl:grid-cols-[minmax(0,1fr)_11rem_minmax(0,1fr)] @2xl:py-0 pointer-coarse:min-h-11";

const CHANGE_WORD = { added: "New", changed: "Changed", removed: "Removed" };

/** How a mapping or a field differs from the version compared with. */
export function ChangeBadge({ status }: { status: MappingChange["status"] }) {
  return (
    <span
      className={cn(
        "shrink-0 whitespace-nowrap rounded-md px-1.5 font-medium font-sans text-[11px] leading-5",
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
