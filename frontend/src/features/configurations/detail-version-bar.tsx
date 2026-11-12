import { Select, SelectItem, SelectList, SelectTrigger } from "@infrahub/ui";
import { useNavigate } from "@tanstack/react-router";
import { cn } from "tailwind-variants";

import { FOCUS } from "#/components/page";
import { CHANGE_TONE, When } from "#/components/run-status";
import type { MappingDiff } from "#/domain/mapping";
import { fmt, plural } from "#/domain/run-state";

import type { ConfigurationDetail } from "./detail";

/** The registration reason shows in the list, not in the closed control. */
const TRIGGER = cn(FOCUS, "tabular-nums [&_[data-reason]]:hidden");

/** One version in a list: its number, and why it was registered when the package says so. */
function VersionOption({ detail, version }: { detail: ConfigurationDetail; version: number }) {
  const reason = detail.summaries.find((v) => v.registry_version === version)?.registration_reason;
  return (
    <span className="flex max-w-80 flex-col">
      <span>
        Version {version}
        {version === detail.latest && <span className="text-subtle"> · latest</span>}
      </span>
      {reason && (
        <span data-reason="" className="text-pretty text-subtle text-xs">
          {reason}
        </span>
      )}
    </span>
  );
}

function VersionSelect({ detail }: { detail: ConfigurationDetail }) {
  const navigate = useNavigate({ from: "/configs/$configId" });
  const { numbers, selected, latest } = detail;
  if (selected === undefined) return null;
  if (numbers.length < 2) {
    return (
      <p className="flex min-h-8 items-center font-medium text-sm tabular-nums">
        Version {selected}
      </p>
    );
  }
  return (
    <Select
      aria-label="Version shown on this page"
      value={selected}
      onChange={(key) => {
        const n = Number(key);
        // A new history entry, so the browser's back button returns to the version before.
        void navigate({
          search: (s) => ({
            version: n === latest ? undefined : n,
            compare: s.compare !== undefined && s.compare < n ? s.compare : undefined,
          }),
          resetScroll: false,
        });
      }}
      className="w-40"
    >
      <SelectTrigger size="sm" className={TRIGGER} />
      <SelectList width="min-trigger" items={numbers.map((n) => ({ id: n }))}>
        {(item) => (
          <SelectItem id={item.id} textValue={`Version ${item.id}`} className="tabular-nums">
            <VersionOption detail={detail} version={item.id} />
          </SelectItem>
        )}
      </SelectList>
    </Select>
  );
}

function CompareSelect({ detail }: { detail: ConfigurationDetail }) {
  const navigate = useNavigate({ from: "/configs/$configId" });
  const { numbers, selected, compareWith } = detail;
  if (selected === undefined || numbers.length < 2) return null;
  const earlier = numbers.filter((n) => n < selected);
  if (earlier.length === 0) {
    return (
      <p className="flex min-h-8 items-center text-subtle text-xs">
        Version {selected} has no earlier version to compare with.
      </p>
    );
  }
  return (
    <div className="flex items-center gap-2">
      <span id="compare-label" className="text-foreground-muted text-xs">
        Compare with
      </span>
      <Select
        aria-labelledby="compare-label"
        value={compareWith ?? 0}
        onChange={(key) => {
          const n = Number(key);
          void navigate({
            search: (s) => ({ ...s, compare: n || undefined, kind: undefined }),
            replace: true,
            resetScroll: false,
          });
        }}
        className="w-40"
      >
        <SelectTrigger size="sm" className={TRIGGER} />
        <SelectList width="min-trigger" items={[{ id: 0 }, ...earlier.map((n) => ({ id: n }))]}>
          {(item) => (
            <SelectItem
              id={item.id}
              textValue={item.id ? `Version ${item.id}` : "No version"}
              className="tabular-nums"
            >
              {item.id ? <VersionOption detail={detail} version={item.id} /> : "No version"}
            </SelectItem>
          )}
        </SelectList>
      </Select>
    </div>
  );
}

const DIFF_PARTS = [
  { key: "added", tone: CHANGE_TONE.create, word: "added" },
  { key: "changed", tone: CHANGE_TONE.update, word: "changed" },
  { key: "removed", tone: CHANGE_TONE.delete, word: "removed" },
] as const;

function diffSentence(diff: MappingDiff): string {
  const { added, changed, removed } = diff.counts;
  const parts = [
    added && `${plural(added, "mapping", "mappings")} added`,
    changed && `${fmt(changed)} changed`,
    removed && `${fmt(removed)} removed`,
  ].filter(Boolean);
  return `Compared with version ${diff.against}: ${parts.join(", ")}`;
}

/** What differs from the version compared with, as +added ~changed −removed in the colours of a run's changes. */
function DiffSummary({ detail }: { detail: ConfigurationDetail }) {
  const { diff, compareWith, base } = detail;
  if (compareWith === undefined) return null;
  if (!diff) {
    return (
      <p className="text-foreground-muted text-xs">
        {base.isError
          ? `Version ${compareWith} could not be read.`
          : `Reading version ${compareWith}.`}
      </p>
    );
  }
  const parts = DIFF_PARTS.filter((part) => diff.counts[part.key] > 0);
  if (parts.length === 0) {
    return <p className="text-foreground-muted text-xs">Same mappings as version {compareWith}</p>;
  }
  return (
    <p className="flex items-center gap-1.5" title={diffSentence(diff)}>
      <span className="sr-only">Compared with version {compareWith}: </span>
      {parts.map((part) => (
        <span
          key={part.key}
          className={cn(
            "rounded-md px-1.5 font-medium font-mono text-xs leading-5 tabular-nums",
            part.tone.chip,
            part.key === "removed" && "inset-ring inset-ring-border"
          )}
        >
          {part.tone.sign}
          {fmt(diff.counts[part.key])}
          <span className="sr-only">
            {" "}
            {diff.counts[part.key] === 1 ? "mapping" : "mappings"} {part.word}
          </span>
        </span>
      ))}
    </p>
  );
}

/** Above the mapping: the version shown, why and when it was registered, and the version to compare with. */
export function VersionBar({ detail }: { detail: ConfigurationDetail }) {
  const { selected, summary } = detail;
  if (selected === undefined) return null;
  return (
    <div className="flex flex-wrap items-start gap-x-3 gap-y-2">
      <VersionSelect detail={detail} />
      <p className="flex min-h-8 min-w-0 flex-1 flex-wrap items-center gap-x-1.5 text-foreground-muted text-xs">
        {summary?.registration_reason && (
          <>
            <span className="text-pretty text-foreground">{summary.registration_reason}</span>
            <span aria-hidden="true">·</span>
          </>
        )}
        {summary && (
          <span>
            Registered <When iso={summary.created_at} />
          </span>
        )}
      </p>
      <div aria-live="polite" className="flex flex-col items-end gap-1.5">
        <CompareSelect detail={detail} />
        <DiffSummary detail={detail} />
      </div>
    </div>
  );
}
