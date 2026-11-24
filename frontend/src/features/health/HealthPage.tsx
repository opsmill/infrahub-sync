import { Button } from "@infrahub/ui";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { describeError, healthQuery } from "#/api/queries";
import { announce } from "#/components/announcer";
import { WarningIcon } from "#/components/icons";
import { Bar, ErrorPanel } from "#/components/page";
import { When } from "#/components/run-status";
import { formatAbsolute } from "#/lib/time";
import { useDocumentTitle } from "#/lib/title";

import { Board } from "./Board";

const TOUCH = "pointer-coarse:min-h-11";

/**
 * Shown above a report that the last check could not refresh. The report under it is drawn grey,
 * so its colours are not read as the current state.
 */
function OlderReport({
  error,
  timestamp,
  onRetry,
}: {
  error: unknown;
  timestamp: string;
  onRetry: () => void;
}) {
  const title = describeError(error).title.replace(/\.$/, "");
  useEffect(() => {
    announce("The last check failed. This page shows an older report from the Sync API.");
  }, []);
  return (
    <div
      role="note"
      className="mb-4 flex flex-wrap items-center gap-x-4 gap-y-3 rounded-xl border border-warning-border bg-warning-surface px-4 py-4"
    >
      <WarningIcon className="size-6 shrink-0 text-warning" />
      <div className="flex min-w-0 flex-1 basis-64 flex-col gap-0.5">
        <p className="font-semibold text-base text-foreground">
          <When iso={timestamp} prefix="Last updated " />
        </p>
        <p className="text-pretty text-foreground-muted">
          {title}. The report below is the one the Sync API returned at{" "}
          <time dateTime={timestamp}>{formatAbsolute(timestamp)}</time>.
        </p>
      </div>
      <Button variant="outline" size="sm" className={TOUCH} onPress={onRetry}>
        Try again
      </Button>
    </div>
  );
}

/** The components of the Sync API and its workers, from `GET /health`, which the page frame polls. */
export function HealthPage() {
  useDocumentTitle("Health");
  const health = useQuery(healthQuery());
  const report = health.data;
  // React Query keeps the last report after a failed refresh, so the failure is checked first. A
  // refetch of a query without data clears its error, so the error is kept until a newer answer.
  const failed = health.errorUpdatedAt > health.dataUpdatedAt;
  const [kept, setKept] = useState<unknown>();
  useEffect(() => {
    if (health.isError) setKept(health.error);
  }, [health.isError, health.error]);
  const error = health.isError ? health.error : kept;
  const check = () => void health.refetch();

  return (
    <>
      <header className="flex flex-col items-start gap-4 pt-8 pb-6 sm:flex-row sm:justify-between">
        <div className="flex flex-col gap-1.5">
          <h1 className="text-balance font-semibold text-2xl tracking-tight">Health</h1>
          <p className="text-pretty text-foreground-muted">
            The components of the Sync API and its workers, checked every 15 seconds.
          </p>
        </div>
        {(report || !failed) && (
          <div className="flex items-center gap-3">
            <span className="text-subtle text-xs">
              {report ? <When iso={report.timestamp} prefix="Checked " /> : "Checking"}
            </span>
            <Button
              variant="outline"
              size="sm"
              className={TOUCH}
              isDisabled={!report}
              onPress={check}
            >
              Check again
            </Button>
          </div>
        )}
      </header>
      {!report && !failed && (
        <div aria-busy="true" className="grid gap-4 lg:grid-cols-2">
          <span className="sr-only">Loading the health report</span>
          <Bar className="h-12 w-full rounded-xl lg:col-span-2" />
          <Bar className="h-52 w-full rounded-xl" />
          <Bar className="h-52 w-full rounded-xl" />
        </div>
      )}
      {!report && failed && <ErrorPanel error={error} onRetry={check} />}
      {report && failed && (
        <OlderReport error={error} timestamp={report.timestamp} onRetry={check} />
      )}
      {report && (
        <div className={failed ? "opacity-60 grayscale" : undefined}>
          <Board report={report} />
        </div>
      )}
    </>
  );
}
