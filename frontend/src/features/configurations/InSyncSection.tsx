import { Card } from "@infrahub/ui";
import { Link } from "@tanstack/react-router";
import { useState } from "react";
import { cn } from "tailwind-variants";

import type { Run } from "#/api/types";
import { STATE_TEXT, StateIcon } from "#/components/icons";
import { CLICKABLE, SectionHeading, STRETCHED } from "#/components/page";
import { When } from "#/components/run-status";
import { useNow } from "#/lib/time";
import { useRunPreview } from "./RunPreview";
import { type PreviewHandler, RunsPerDay, RunsPerDayAxis } from "./RunsPerDay";
import { LIFT, SearchHint, SystemsLine } from "./row-parts";
import { type ConfigurationRow, configName, type MatchHint, rowExplanation } from "./rows";

const COLLAPSED_ROWS = 10;
const COLUMNS = "md:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)_minmax(15rem,1.4fr)]";

function Row({
  row,
  hint,
  runs,
  now,
  loadedSince,
  onPreview,
}: {
  row: ConfigurationRow;
  hint?: MatchHint;
  runs: Run[];
  now: number;
  loadedSince?: number;
  onPreview: PreviewHandler;
}) {
  const id = row.summary.config_id;
  const name = configName(row);
  const explanation = rowExplanation(row);
  const lastChange = row.run?.phase === "applied" ? undefined : row.lastApplied?.finished_at;

  return (
    <li
      data-config-id={id}
      className={cn(
        CLICKABLE,
        "grid grid-cols-1 gap-y-2 px-4 py-3 md:items-center md:gap-x-6",
        COLUMNS
      )}
    >
      <div className="min-w-0">
        <Link
          to="/configs/$configId"
          params={{ configId: id }}
          data-stretched=""
          aria-describedby={`reason-${id}`}
          className={cn(
            "font-medium text-foreground [overflow-wrap:anywhere] decoration-subtle-muted underline-offset-2 hover:underline",
            STRETCHED
          )}
        >
          {name}
        </Link>
        <div className="mt-0.5 text-[13px] text-foreground-muted">
          <SystemsLine row={row} />
        </div>
        {hint && <SearchHint hint={hint} />}
      </div>
      <div id={`reason-${id}`} className="min-w-0 text-[13px]">
        <p className="flex items-start gap-1.5 text-foreground-muted">
          {row.state && (
            <StateIcon
              state={row.state}
              className={cn("mt-[3px] size-3.5 shrink-0", STATE_TEXT[row.state])}
            />
          )}
          <span className="text-pretty">{explanation.short}</span>
        </p>
        <p className="flex flex-wrap gap-x-3 pl-5 text-subtle text-xs">
          {explanation.at && (
            <span className="whitespace-nowrap">
              <When iso={explanation.at.iso} prefix={explanation.at.prefix} className={LIFT} />
            </span>
          )}
          {lastChange && (
            <span className="whitespace-nowrap">
              Last change <When iso={lastChange} className={LIFT} />
            </span>
          )}
        </p>
      </div>
      <div className="min-w-0">
        <RunsPerDay
          name={name}
          runs={runs}
          now={now}
          loadedSince={loadedSince}
          onPreview={onPreview}
        />
      </div>
    </li>
  );
}

/**
 * The configurations whose latest run applied its changes or found none, each with a chart of its
 * runs per day. Searching or filtering shows every row; otherwise the list stops after ten.
 */
export function InSyncSection({
  id,
  title,
  items,
  history,
  loadedSince,
  showAll,
}: {
  id: string;
  title: string;
  items: { row: ConfigurationRow; hint?: MatchHint }[];
  /** The runs of each configuration, newest first. */
  history: Map<string, Run[]>;
  loadedSince?: number;
  showAll: boolean;
}) {
  const now = useNow(60_000);
  const preview = useRunPreview();
  const [expanded, setExpanded] = useState(false);
  const shown = showAll || expanded ? items : items.slice(0, COLLAPSED_ROWS);

  return (
    <section aria-labelledby={`group-${id}`} className="flex flex-col gap-2">
      <SectionHeading id={`group-${id}`} title={title} count={items.length} />
      <Card className="overflow-hidden">
        <div
          aria-hidden="true"
          className={cn(
            "hidden border-b px-4 py-2 font-medium text-subtle text-xs md:grid md:gap-x-6",
            COLUMNS
          )}
        >
          <span>Configuration</span>
          <span>Latest run</span>
          <RunsPerDayAxis />
        </div>
        <ul className="divide-y">
          {shown.map(({ row, hint }) => (
            <Row
              key={row.summary.config_id}
              row={row}
              hint={hint}
              runs={history.get(row.summary.config_id) ?? []}
              now={now}
              loadedSince={loadedSince}
              onPreview={preview.show}
            />
          ))}
        </ul>
      </Card>
      {!showAll && items.length > COLLAPSED_ROWS && (
        <button
          type="button"
          aria-expanded={expanded}
          onClick={() => setExpanded((v) => !v)}
          className="self-start rounded-md px-1.5 py-1 font-medium text-[13px] text-foreground-muted transition-colors duration-150 ease-out hover:bg-highlight hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring motion-reduce:transition-none"
        >
          {expanded ? "Show fewer" : `Show ${items.length - COLLAPSED_ROWS} more`}
        </button>
      )}
      {preview.host}
    </section>
  );
}
