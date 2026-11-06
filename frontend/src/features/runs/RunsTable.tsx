import { Card } from "@infrahub/ui";
import { Link } from "@tanstack/react-router";

import type { Run } from "#/api/types";
import { CLICKABLE, STRETCHED } from "#/components/page";
import { BranchCode, StateLabel, When } from "#/components/run-status";
import { fmt, phaseLabel, runCounts, runState } from "#/domain/run-state";
import { formatDuration } from "#/lib/time";

const OPERATION: Record<string, string> = {
  plan: "Plan",
  sync: "Sync",
  verify: "Verify",
  apply: "Apply",
};

/** Planned or applied counts as +created ~updated −deleted, coloured, with words for screen readers. */
export function Counts({ run }: { run: Run }) {
  const c = runCounts(run);
  if (!c) return <span className="text-subtle">—</span>;
  if (c.total === 0) return <span className="text-subtle">No changes</span>;
  return (
    <span className="inline-flex gap-2 font-mono text-xs tabular-nums">
      {c.create > 0 && (
        <span className="text-success" title={`${fmt(c.create)} to create`}>
          +{fmt(c.create)}
          <span className="sr-only"> to create</span>
        </span>
      )}
      {c.update > 0 && (
        <span className="text-warning" title={`${fmt(c.update)} to update`}>
          ~{fmt(c.update)}
          <span className="sr-only"> to update</span>
        </span>
      )}
      {c.delete > 0 && (
        <span
          className="text-subtle"
          title={`${fmt(c.delete)} listed for review; apply does not run deletes`}
        >
          −{fmt(c.delete)}
          <span className="sr-only"> deletes listed, not run</span>
        </span>
      )}
    </span>
  );
}

const CELL = "px-4 py-3 align-middle max-md:block max-md:px-0 max-md:py-0.5";

/**
 * Runs, newest first. With `configNames`, a column names each run's configuration; on a
 * configuration's own page that column is left out.
 */
export function RunsTable({
  runs,
  configNames,
  latestVersions,
}: {
  runs: Run[];
  configNames?: Map<string, string>;
  latestVersions?: Map<string, number>;
}) {
  const now = Date.now();
  return (
    <Card className="overflow-hidden">
      <table className="w-full border-collapse text-left">
        <thead className="max-md:sr-only">
          <tr className="border-b text-subtle text-xs">
            <th scope="col" className="px-4 py-2 font-medium">
              Run
            </th>
            {configNames && (
              <th scope="col" className="px-4 py-2 font-medium">
                Configuration
              </th>
            )}
            <th scope="col" className="px-4 py-2 font-medium">
              State
            </th>
            <th scope="col" className="px-4 py-2 font-medium">
              Changes
            </th>
            <th scope="col" className="px-4 py-2 font-medium">
              Branch
            </th>
            <th scope="col" className="px-4 py-2 font-medium">
              Started
            </th>
            <th scope="col" className="px-4 py-2 text-right font-medium">
              Finished
            </th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {runs.map((run) => {
            const latestVersion = run.config_id ? latestVersions?.get(run.config_id) : undefined;
            const state = runState(run);
            return (
              <tr
                key={run.run_id}
                className={`max-md:flex max-md:flex-col max-md:gap-1 max-md:px-4 max-md:py-3 ${CLICKABLE}`}
              >
                <td className={CELL}>
                  <Link
                    to="/runs/$runId"
                    params={{ runId: run.run_id }}
                    data-stretched=""
                    className={`font-medium text-foreground decoration-subtle-muted underline-offset-2 hover:underline ${STRETCHED}`}
                  >
                    {OPERATION[run.operation] ?? run.operation}
                    {run.registry_version != null && (
                      <span className="font-normal text-foreground-muted">
                        {" "}
                        · version {run.registry_version}
                      </span>
                    )}
                  </Link>
                  <div className="whitespace-nowrap font-mono text-[11px] text-subtle">
                    {run.run_id}
                  </div>
                </td>
                {configNames && (
                  <td className={CELL}>
                    <span className="[overflow-wrap:anywhere]">
                      {(run.config_id && configNames.get(run.config_id)) ??
                        run.configuration_reference}
                    </span>
                    {latestVersion !== undefined &&
                      run.registry_version != null &&
                      run.registry_version < latestVersion && (
                        <span className="block text-subtle text-xs">
                          Version {latestVersion} is newer
                        </span>
                      )}
                  </td>
                )}
                <td className={CELL}>
                  <StateLabel state={state} label={phaseLabel(run)} />
                </td>
                <td className={CELL}>
                  <Counts run={run} />
                </td>
                <td className={CELL}>
                  {run.destination_branch ? (
                    <BranchCode branch={run.destination_branch} />
                  ) : (
                    <span className="text-subtle">—</span>
                  )}
                </td>
                <td className={`${CELL} text-foreground-muted`}>
                  <When iso={run.started_at} />
                  {run.actor && <span className="block text-subtle text-xs">by {run.actor}</span>}
                </td>
                <td className={`${CELL} text-foreground-muted md:text-right`}>
                  {run.finished_at ? (
                    <When iso={run.finished_at} />
                  ) : (
                    <span className="text-accent tabular-nums">
                      Running for {formatDuration(run.started_at, now)}
                    </span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </Card>
  );
}
