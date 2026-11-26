/*
 * The body of the Health page: one tile per component beside the workers, as three figures, one
 * square per worker, the table of live workers and a folded table of stale workers.
 *
 * Chosen on 2026-11-24 from three prototypes. Rejected: a one-column list with a row per check,
 * which fills the screen when the Sync API is healthy and gives one row per stale worker; and a
 * page that names only the problems, where the components that are up need a click.
 * Two refinements followed: the stale workers are folded under the live ones, and the page draws
 * a report that the last check could not refresh in grey, under an amber band (HealthPage).
 */
import { cn } from "tailwind-variants";

import { StateIcon } from "#/components/icons";
import { FOCUS } from "#/components/page";
import { plural } from "#/domain/run-state";

import {
  type BoardProps,
  componentLabel,
  count,
  ErrorCode,
  enoughWorkers,
  errorText,
  NO_WORKER_LISTED,
  staleCount,
  WORKERS_UNKNOWN,
  WorkerTable,
  workerKeys,
} from "./parts";

function Figure({
  term,
  value,
  tone,
}: {
  term: string;
  value: string;
  tone?: "danger" | "success";
}) {
  return (
    <div className="flex min-w-0 flex-col justify-between gap-1">
      <dt className="text-subtle text-xs">{term}</dt>
      <dd
        className={cn(
          "font-semibold text-3xl tabular-nums leading-none tracking-tight",
          tone === "danger" && "text-danger",
          tone === "success" && "text-success",
          !tone && "text-foreground"
        )}
      >
        {value}
      </dd>
    </div>
  );
}

export function Board({ report }: BoardProps) {
  const w = report.workers;
  const enough = enoughWorkers(w);
  const healthy = report.status === "healthy";
  const stale = staleCount(w);
  const liveItems = w.items.filter((i) => i.state === "live");
  const keys = workerKeys(w.items);
  return (
    <div className="flex flex-col gap-4">
      <p
        className={cn(
          "flex items-center gap-2 rounded-xl border px-4 py-3 font-medium",
          healthy
            ? "border-success/30 bg-success/10 text-success"
            : "border-danger/30 bg-danger-surface/40 text-danger"
        )}
      >
        <StateIcon state={healthy ? "insync" : "failed"} className="size-5 shrink-0" />
        {healthy ? "Healthy" : "Unhealthy"}
      </p>

      <div className="grid items-start gap-4 lg:grid-cols-2">
        <section aria-labelledby="board-components" className="flex flex-col gap-3">
          <h2 id="board-components" className="font-semibold text-sm tracking-tight">
            Components
          </h2>
          <ul className="grid grid-cols-2 gap-3">
            {report.dependencies.map((d) => {
              const up = d.status === "up";
              return (
                <li
                  key={d.name}
                  className={cn(
                    "flex min-h-24 flex-col gap-2 rounded-xl border p-4",
                    !up && "border-danger/40 bg-danger-surface/40"
                  )}
                >
                  <span className="text-foreground-muted text-xs">{componentLabel(d.name)}</span>
                  <span
                    className={cn(
                      "flex items-center gap-2 font-semibold text-2xl leading-none tracking-tight",
                      up ? "text-success" : "text-danger"
                    )}
                  >
                    <StateIcon state={up ? "insync" : "failed"} className="size-5 shrink-0" />
                    {up ? "Up" : "Down"}
                  </span>
                  {!up && (
                    <span className="mt-auto flex flex-col items-start gap-1 text-pretty text-[13px] text-foreground-muted">
                      {errorText(d)}
                      <ErrorCode code={d.error} />
                    </span>
                  )}
                </li>
              );
            })}
          </ul>
        </section>

        <section aria-labelledby="board-workers" className="flex flex-col gap-3">
          <h2 id="board-workers" className="font-semibold text-sm tracking-tight">
            Workers
          </h2>
          <div
            className={cn(
              "flex flex-col gap-5 rounded-xl border p-4",
              enough === false && "border-danger/40"
            )}
          >
            {w.state !== "known" ? (
              <p className="text-pretty text-foreground-muted">{WORKERS_UNKNOWN}</p>
            ) : (
              <>
                <dl className="grid grid-cols-3 gap-4">
                  <Figure
                    term="Online"
                    value={count(w.live_workers)}
                    tone={enough === null ? undefined : enough ? "success" : "danger"}
                  />
                  <Figure term="Required" value={count(w.minimum)} />
                  <Figure term="Executions waiting" value={count(w.queue_depth)} />
                </dl>
                {w.items.length === 0 ? (
                  <p className="text-foreground-muted">{NO_WORKER_LISTED}</p>
                ) : (
                  <div className="flex flex-col gap-2">
                    {/* The line under the squares gives the same counts in words. */}
                    <div aria-hidden="true" className="flex flex-wrap gap-1">
                      {w.items.map((item, i) => (
                        <span
                          key={keys[i]}
                          className={cn(
                            "block size-4 rounded-[4px]",
                            item.state === "live"
                              ? "bg-success-strong"
                              : "border-[1.5px] border-subtle-muted"
                          )}
                        />
                      ))}
                    </div>
                    <p className="flex flex-wrap gap-x-4 gap-y-1 text-subtle text-xs">
                      <span className="inline-flex items-center gap-1.5">
                        <span
                          aria-hidden="true"
                          className="block size-2.5 rounded-[3px] bg-success-strong"
                        />
                        {plural(liveItems.length, "live", "live")}
                      </span>
                      <span className="inline-flex items-center gap-1.5">
                        <span
                          aria-hidden="true"
                          className="block size-2.5 rounded-[3px] border-[1.5px] border-subtle-muted"
                        />
                        {plural(stale, "stale", "stale")} in the last 24 hours
                      </span>
                    </p>
                  </div>
                )}
              </>
            )}
          </div>
          {w.state === "known" && liveItems.length > 0 && (
            <WorkerTable workers={liveItems} label="Live workers" />
          )}
          {w.state === "known" && stale > 0 && (
            <details className="group">
              <summary
                className={cn(
                  "flex min-h-11 cursor-pointer list-none items-center gap-2 rounded-xl px-4 text-foreground-muted transition-colors duration-150 ease-out hover:text-foreground motion-reduce:transition-none [&::-webkit-details-marker]:hidden",
                  FOCUS
                )}
              >
                <svg
                  viewBox="0 0 16 16"
                  aria-hidden="true"
                  className="size-3.5 shrink-0 text-subtle transition-transform duration-150 ease-out group-open:rotate-90 motion-reduce:transition-none"
                >
                  <path
                    d="M6 4l4 4-4 4"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.5"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
                {plural(stale, "stale worker", "stale workers")} in the last 24 hours
              </summary>
              <div className="pt-1">
                <WorkerTable
                  workers={w.items.filter((i) => i.state === "stale")}
                  label="Stale workers"
                />
              </div>
            </details>
          )}
        </section>
      </div>
    </div>
  );
}
