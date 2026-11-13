import { Tooltip } from "@infrahub/ui";
import { cn } from "tailwind-variants";

import { ArrowRightIcon } from "#/components/icons";
import { Bar } from "#/components/page";
import { BranchBadge } from "#/components/run-status";
import type { System } from "#/domain/systems";

import { type ConfigurationRow, defaultBranch, type MatchHint } from "./rows";

// Tooltip triggers sit above the stretched row link for a mouse; on a touch screen a tap on them
// opens the configuration, because touch does not open tooltips.
export const LIFT = "pointer-fine:relative pointer-fine:z-10";

function HostTip({ system }: { system: System }) {
  if (!system.host) return <span>{system.label}</span>;
  return (
    <Tooltip
      nonInteractiveTrigger
      placement="top"
      message={<span className="font-mono">{system.host}</span>}
    >
      <span className={cn(LIFT, "cursor-default")}>{system.label}</span>
    </Tooltip>
  );
}

export function SystemsLine({ row }: { row: ConfigurationRow }) {
  if (row.latestVersion === undefined)
    return <span className="text-subtle">Latest version not known</span>;
  if (row.versionError)
    return <span className="text-subtle">Could not read the configuration package</span>;
  if (!row.source || !row.destination) return <Bar className="my-0.5 h-3.5 w-36" />;
  const branch = defaultBranch(row);
  const fromRun = Boolean(row.run?.destination_branch);
  return (
    <span className="inline-flex max-w-full flex-wrap items-center gap-x-1.5">
      <HostTip system={row.source} />
      <ArrowRightIcon />
      <span className="sr-only">to</span>
      <HostTip system={row.destination} />
      {branch && branch !== "main" && (
        <Tooltip
          nonInteractiveTrigger
          placement="top"
          message={fromRun ? "Branch of the latest run" : "Branch declared in the configuration"}
        >
          <span className={cn(LIFT, "inline-flex min-w-0 max-w-full cursor-default")}>
            <BranchBadge branch={branch} label={fromRun ? "latest run on branch" : "on branch"} />
          </span>
        </Tooltip>
      )}
      <span className="inline-flex items-center gap-1.5 whitespace-nowrap">
        <span aria-hidden="true" className="text-subtle">
          ·
        </span>
        <KindsCount kinds={row.kinds} />
      </span>
    </span>
  );
}

const HINT_WORDS: Record<MatchHint["field"], string> = {
  id: "Matches ID ",
  branch: "Matches branch ",
  host: "Matches host ",
  kind: "Maps the kind ",
};

export function SearchHint({ hint }: { hint: MatchHint }) {
  return (
    <p className="mt-0.5 text-subtle text-xs [overflow-wrap:anywhere]">
      {HINT_WORDS[hint.field]}
      <code className="font-mono">{hint.value}</code>
    </p>
  );
}

function KindsCount({ kinds }: { kinds: string[] }) {
  const label = `${kinds.length} ${kinds.length === 1 ? "kind" : "kinds"}`;
  if (kinds.length === 0) return <span>{label}</span>;
  const shown = kinds.slice(0, 14);
  return (
    <Tooltip
      nonInteractiveTrigger
      placement="top"
      message={
        <span className="block max-w-72 font-mono text-[11px] leading-5">
          {shown.join(", ")}
          {kinds.length > shown.length && ` and ${kinds.length - shown.length} more`}
        </span>
      }
    >
      <span
        className={cn(
          LIFT,
          "cursor-default tabular-nums underline decoration-subtle-muted decoration-dotted underline-offset-4"
        )}
      >
        {label}
      </span>
    </Tooltip>
  );
}
