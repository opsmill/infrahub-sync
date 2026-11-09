import { Card } from "@infrahub/ui";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useState } from "react";
import { cn } from "tailwind-variants";

import { configVersionQuery } from "#/api/queries";
import type {
  ConfigurationVersion,
  DeclaredMapping,
  RunReference,
  VersionSummary,
} from "#/api/types";
import { ChevronRightIcon } from "#/components/icons";
import { CopyButton, ErrorPanel, expandChevron, FOCUS, textAction } from "#/components/page";
import { StateLabel, When } from "#/components/run-status";
import { fmt, type ListRunState, refLabel, refState } from "#/domain/run-state";

const RUN_LINK = cn(
  "relative rounded-sm before:absolute before:-inset-x-1 before:-inset-y-3 before:content-[''] hover:underline",
  FOCUS
);

/**
 * The run's state as the Runs tab shows it when the run is on the loaded page. A reference carries
 * no counts, so otherwise the state comes from its phase and outcome.
 */
function refView(
  run: RunReference,
  version: number,
  latestVersion: number | undefined,
  listed: ListRunState | undefined
): Pick<ListRunState, "state" | "label" | "note"> {
  if (listed) return listed;
  const state = refState(run);
  const label = refLabel(run);
  if (state === "review" && latestVersion !== undefined && version < latestVersion)
    return { state: "idle", label, note: `Version ${latestVersion} is newer` };
  return { state, label };
}

function LatestRunRef({
  version,
  latestVersion,
  run,
  runStates,
}: {
  version: number;
  latestVersion?: number;
  run?: RunReference | null;
  runStates: Map<string, ListRunState>;
}) {
  if (!run) return <span className="text-subtle">No run yet</span>;
  const { state, label, note } = refView(run, version, latestVersion, runStates.get(run.run_id));
  return (
    <span className="inline-flex flex-wrap items-center gap-x-1.5">
      <span className="text-subtle">Latest run</span>
      <Link
        to="/runs/$runId"
        params={{ runId: run.run_id }}
        aria-label={`Latest run of version ${version}: ${label}${note ? `, ${note}` : ""}, run ${run.run_id}`}
        className={RUN_LINK}
      >
        <StateLabel state={state} label={label} quiet />
      </Link>
      <When iso={run.finished_at ?? run.started_at} className="text-subtle" />
      {note && (
        <span className="text-subtle">
          <span aria-hidden="true">· </span>
          {note}
        </span>
      )}
    </span>
  );
}

const keyOf = (m: DeclaredMapping) => `${m.name}|${m.mapping}`;

const shape = (m: DeclaredMapping) =>
  JSON.stringify([m.fields ?? [], m.filters ?? [], m.transforms ?? [], m.identifiers ?? []]);

const andList = (names: string[]) =>
  names.length <= 1
    ? (names[0] ?? "")
    : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;

function byKey(v: ConfigurationVersion): Map<string, DeclaredMapping[]> {
  const groups = new Map<string, DeclaredMapping[]>();
  for (const m of v.declared_content.configuration.schema_mapping ?? []) {
    const group = groups.get(keyOf(m));
    if (group) group.push(m);
    else groups.set(keyOf(m), [m]);
  }
  return groups;
}

/**
 * Lines that say how the mappings of `next` differ from those of `prev`. A kind can be mapped more
 * than once from the same source path (with different filters), so mappings that share a key are
 * matched by content first and the rest are paired in declaration order.
 */
function compareMappings(prev: ConfigurationVersion, next: ConfigurationVersion): string[] {
  const before = byKey(prev);
  const after = byKey(next);
  const added = new Set<string>();
  const removed = new Set<string>();
  let changed = 0;
  for (const key of new Set([...before.keys(), ...after.keys()])) {
    const left = (before.get(key) ?? []).map(shape);
    const right = (after.get(key) ?? []).map(shape);
    const unmatched = right.filter((s) => {
      const i = left.indexOf(s);
      if (i === -1) return true;
      left.splice(i, 1);
      return false;
    });
    changed += Math.min(left.length, unmatched.length);
    const name = (after.get(key) ?? before.get(key) ?? [])[0]?.name ?? key;
    if (unmatched.length > left.length) added.add(name);
    if (left.length > unmatched.length) removed.add(name);
  }
  const lines: string[] = [];
  if (added.size) lines.push(`Adds ${andList([...added])}`);
  if (removed.size) lines.push(`Removes ${andList([...removed])}`);
  if (changed) lines.push(`Changes ${fmt(changed)} ${changed === 1 ? "mapping" : "mappings"}`);
  return lines;
}

function Comparison({ configId, version }: { configId: string; version: number }) {
  const prev = useQuery(configVersionQuery(configId, version - 1));
  const next = useQuery(configVersionQuery(configId, version));
  const error = prev.error ?? next.error;
  if (prev.data && next.data) {
    const lines = compareMappings(prev.data, next.data);
    return (
      <div className="mt-1 text-xs">
        <p className="text-subtle">Mappings compared with version {version - 1}</p>
        {lines.length ? (
          <ul className="mt-0.5 flex list-disc flex-col gap-0.5 pl-4 text-foreground-muted">
            {lines.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        ) : (
          <p className="text-foreground-muted">Same mappings as version {version - 1}</p>
        )}
      </div>
    );
  }
  if (error) {
    return (
      <ErrorPanel
        error={error}
        compact
        onRetry={() => {
          void prev.refetch();
          void next.refetch();
        }}
      />
    );
  }
  return (
    <output className="mt-1 text-subtle text-xs">
      Reading versions {version - 1} and {version}…
    </output>
  );
}

function VersionRow({
  configId,
  v,
  latestVersion,
  runStates,
  onStart,
}: {
  configId: string;
  v: VersionSummary;
  latestVersion?: number;
  runStates: Map<string, ListRunState>;
  onStart?: (version: number) => void;
}) {
  const n = v.registry_version;
  const [comparing, setComparing] = useState(false);
  return (
    <li className="grid grid-cols-1 gap-x-8 gap-y-2 px-4 py-3.5 md:grid-cols-[120px_minmax(0,1fr)_auto]">
      <div className="flex items-baseline gap-2">
        <span className="font-medium tabular-nums">Version {n}</span>
        {n === latestVersion && (
          <span className="rounded-md bg-accent-surface px-1.5 font-medium text-accent text-xs leading-5">
            Latest
          </span>
        )}
      </div>
      <div className="flex min-w-0 flex-col gap-1">
        {v.registration_reason ? (
          <p className="text-pretty font-medium text-foreground text-sm">{v.registration_reason}</p>
        ) : (
          <p className="text-sm text-subtle">No reason recorded</p>
        )}
        <p className="flex flex-wrap items-center gap-x-1 text-subtle text-xs">
          <span>
            Registered <When iso={v.created_at} />
            {v.registered_by && ` by ${v.registered_by}`}
          </span>
          <span aria-hidden="true">·</span>
          <span className="inline-flex items-center gap-1">
            checksum
            <span className="font-mono" title={v.package_checksum}>
              {v.package_checksum.slice(0, 12)}
            </span>
            {/* Above the run links below, whose hit areas reach up into this line. */}
            <span className="relative z-10 inline-flex">
              <CopyButton
                value={v.package_checksum}
                label={`Copy the package checksum of version ${n}`}
              />
            </span>
          </span>
        </p>
        <div className="flex flex-wrap gap-x-5 gap-y-1 text-xs">
          <LatestRunRef
            version={n}
            latestVersion={latestVersion}
            run={v.latest_run}
            runStates={runStates}
          />
          {v.latest_applied_run ? (
            <Link
              to="/runs/$runId"
              params={{ runId: v.latest_applied_run.run_id }}
              aria-label={`Last applied run of version ${n}, run ${v.latest_applied_run.run_id}`}
              className={cn(RUN_LINK, "text-foreground-muted")}
            >
              Applied{" "}
              <When iso={v.latest_applied_run.finished_at ?? v.latest_applied_run.started_at} />
            </Link>
          ) : (
            <span className="text-subtle">Never applied</span>
          )}
        </div>
        {comparing && <Comparison configId={configId} version={n} />}
      </div>
      <div className="flex flex-wrap gap-x-3 md:flex-col md:items-end md:justify-self-end">
        <Link
          to="/configs/$configId"
          params={{ configId }}
          search={{ tab: "mapping", version: n === latestVersion ? undefined : n }}
          replace
          resetScroll={false}
          className={textAction()}
        >
          View mapping
          <ChevronRightIcon />
        </Link>
        {n > 1 && (
          <button
            type="button"
            aria-expanded={comparing}
            onClick={() => setComparing((c) => !c)}
            className={textAction()}
          >
            {comparing ? "Hide comparison" : `Compare with version ${n - 1}`}
            <ChevronRightIcon className={expandChevron({ open: comparing })} />
          </button>
        )}
        {onStart && latestVersion !== undefined && n < latestVersion && (
          <button type="button" onClick={() => onStart(n)} className={textAction()}>
            Plan version {n}
            <ChevronRightIcon />
          </button>
        )}
      </div>
    </li>
  );
}

/** Registered versions, newest first, with their latest and last applied runs. */
export function Versions({
  configId,
  versions,
  latestVersion,
  runStates,
  onStart,
}: {
  configId: string;
  versions: VersionSummary[];
  latestVersion?: number;
  /** The states the Runs tab shows, so a run reads the same in both tabs. */
  runStates: Map<string, ListRunState>;
  onStart?: (version: number) => void;
}) {
  return (
    <Card className="overflow-hidden">
      <ol className="divide-y">
        {versions.map((v) => (
          <VersionRow
            key={v.registry_version}
            configId={configId}
            v={v}
            latestVersion={latestVersion}
            runStates={runStates}
            onStart={onStart}
          />
        ))}
      </ol>
    </Card>
  );
}
