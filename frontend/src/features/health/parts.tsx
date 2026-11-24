import { cn } from "tailwind-variants";

import type { HealthReport } from "#/api/types";
import { CODE_CHIP } from "#/components/page";
import { When } from "#/components/run-status";
import { type Worker, workerKeys } from "#/domain/health";
import { fmt } from "#/domain/run-state";

export {
  componentLabel,
  enoughWorkers,
  errorText,
  NO_WORKER_LISTED,
  staleCount,
  WORKERS_UNKNOWN,
  workerKeys,
} from "#/domain/health";

export interface BoardProps {
  report: HealthReport;
}

function WorkerStateWord({ worker }: { worker: Worker }) {
  const isLive = worker.state === "live";
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 whitespace-nowrap",
        isLive ? "text-success" : "text-subtle"
      )}
    >
      <span
        aria-hidden="true"
        className={cn(
          "block size-2 shrink-0 rounded-full",
          isLive ? "bg-success-strong" : "border-[1.5px] border-subtle-muted"
        )}
      />
      {isLive ? "Live" : "Stale"}
    </span>
  );
}

/** The error category of the report, as the Sync API names it. */
export const ErrorCode = ({ code }: { code: string }) => <code className={CODE_CHIP}>{code}</code>;

/** A table of workers, newest heartbeat first, as the Sync API orders them. */
export function WorkerTable({ workers, label }: { workers: Worker[]; label: string }) {
  const keys = workerKeys(workers);
  return (
    <div className="overflow-x-auto rounded-xl border">
      <table aria-label={label} className="w-full border-collapse text-left text-[13px]">
        <thead>
          <tr className="border-b text-subtle text-xs">
            <th scope="col" className="px-4 py-2 font-medium">
              Name
            </th>
            <th scope="col" className="px-4 py-2 font-medium">
              State
            </th>
            <th scope="col" className="whitespace-nowrap px-4 py-2 text-right font-medium">
              Last heartbeat
            </th>
          </tr>
        </thead>
        <tbody>
          {workers.map((w, i) => (
            <tr key={keys[i]} className="border-b last:border-b-0">
              <td
                className={cn(
                  "min-w-36 px-4 py-2 font-mono text-xs [overflow-wrap:anywhere]",
                  w.state === "live" ? "text-foreground" : "text-foreground-muted"
                )}
              >
                {w.name}
              </td>
              <td className="px-4 py-2">
                <WorkerStateWord worker={w} />
              </td>
              <td className="px-4 py-2 text-right text-foreground-muted">
                <When iso={w.last_heartbeat} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** A count from the report; a dash when the Sync API could not read it. */
export const count = (n: number | null) => (n == null ? "–" : fmt(n));
