import { buttonVariants, Card } from "@infrahub/ui";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { type ReactNode, useEffect } from "react";
import { cn } from "tailwind-variants";

import {
  configsQuery,
  describeScheduleError,
  nextStartsQuery,
  runQuery,
  scheduleQuery,
} from "#/api/queries";
import type { Schedule } from "#/api/types";
import {
  Bar,
  CODE_CHIP,
  CRUMB,
  ErrorPanel,
  META_LIST,
  PageMessage,
  SectionHeading,
  StaleNotice,
} from "#/components/page";
import { BranchBadge, StateLabel, When } from "#/components/run-status";
import { cadenceWords, cronFields, formFromCron } from "#/domain/cadence";
import { phaseLabel, runState } from "#/domain/run-state";
import { lateStartLabel, overlapPolicyLabel, writeTargetLabel } from "#/domain/schedules";
import { LINK } from "#/features/runs/form";
import { formatAbsolute } from "#/lib/time";
import { useDocumentTitle } from "#/lib/title";

import { NextStarts, StartTime } from "./NextStarts";
import { ScheduleNotices } from "./ScheduleNotices";
import { ScheduleStateLabel } from "./SchedulesTable";

/** The number of start times the "Next starts" section asks for. */
const NEXT_STARTS = 5;

function Breadcrumb() {
  return (
    <nav aria-label="Breadcrumb" className="text-foreground-muted text-sm">
      <Link to="/schedules" className={CRUMB}>
        Schedules
      </Link>
    </nav>
  );
}

function ScheduleSkeleton() {
  return (
    <div aria-busy="true">
      <output className="sr-only">Loading the schedule</output>
      <div className="flex flex-col gap-4 pt-6 pb-6">
        <div className="flex h-8 items-center pointer-coarse:h-11">
          <Bar className="h-4 w-24" />
        </div>
        <Bar className="h-8 w-[24rem] max-w-full max-sm:h-7" />
        <div className="flex h-5 items-center">
          <Bar className="h-4 w-96 max-w-full" />
        </div>
      </div>
      <div className="flex flex-col gap-8">
        {[3, 6].map((n) => (
          <Card key={n} className="grid grid-cols-1 gap-3 p-4 sm:grid-cols-2 lg:grid-cols-3">
            {Array.from({ length: n }, (_, i) => (
              <div key={i} className="flex flex-col gap-1.5">
                <Bar className="h-3 w-24" />
                <Bar className="h-4 w-40 max-w-full" />
              </div>
            ))}
          </Card>
        ))}
      </div>
    </div>
  );
}

/** A section of label and value pairs, as the run page's "Details". */
function Details({ id, title, rows }: { id: string; title: string; rows: [string, ReactNode][] }) {
  return (
    <section aria-labelledby={id} className="flex flex-col gap-3">
      <SectionHeading id={id} title={title} />
      <Card className="p-4">
        <dl className="grid grid-cols-1 gap-x-8 gap-y-3 text-[13px] sm:grid-cols-2 lg:grid-cols-3">
          {rows.map(([k, v]) => (
            <div key={k} className="flex min-w-0 flex-col gap-0.5">
              <dt className="text-subtle text-xs">{k}</dt>
              <dd className="flex min-h-6 min-w-0 flex-wrap items-center gap-1 [overflow-wrap:anywhere]">
                {v}
              </dd>
            </div>
          ))}
        </dl>
      </Card>
    </section>
  );
}

const None = ({ text = "None" }: { text?: string }) => (
  <span className="text-foreground-muted">{text}</span>
);

/** An absolute time with its relative time, for the record and the left-out setting. */
function At({ iso }: { iso: string }) {
  return (
    <span>
      <time dateTime={iso}>{formatAbsolute(iso)}</time>{" "}
      <span className="text-subtle">
        (<When iso={iso} />)
      </span>
    </span>
  );
}

/** The last run of the schedule with its state, read from the run itself. */
function LastRun({ runId }: { runId: string }) {
  const run = useQuery(runQuery(runId));
  const data = run.data?.run;
  return (
    <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5">
      {data ? (
        <StateLabel state={runState(data)} label={phaseLabel(data)} />
      ) : run.isPending ? (
        <Bar className="h-3.5 w-20" />
      ) : (
        <span className="text-foreground-muted">State not available</span>
      )}
      <Link to="/runs/$runId" params={{ runId }} className={LINK}>
        View run
      </Link>
      {data && (
        <span className="text-subtle text-xs">
          Started <When iso={data.started_at} />
        </span>
      )}
    </span>
  );
}

function leftOutText(schedule: Schedule): ReactNode {
  const value = schedule.write_when_left_out ? "On" : "Off";
  if (!schedule.left_out_set_by && !schedule.left_out_set_at) return value;
  return (
    <span>
      {value}
      {schedule.left_out_set_by && (
        <>
          , set by <span className="text-foreground">{schedule.left_out_set_by}</span>
        </>
      )}
      {schedule.left_out_set_at && (
        <>
          {" "}
          on <At iso={schedule.left_out_set_at} />
        </>
      )}
    </span>
  );
}

function Cadence({ cron }: { cron: string }) {
  const words = cadenceWords(formFromCron(cron));
  return (
    <span className="flex min-w-0 flex-col items-start gap-0.5">
      <code className={cn(CODE_CHIP, "[overflow-wrap:anywhere]")}>{cron}</code>
      {words && <span className="text-foreground-muted text-xs">{words}</span>}
    </span>
  );
}

/**
 * The page of one schedule (contracts/web-pages.md): header, notices, next start and last run,
 * settings, next starts, review branch, history and record. The schedule is read again every 10
 * seconds. The schedule identifier, the owner and the account that set "write when records are
 * left out" are shown only as the strings the Sync API returns.
 */
export function SchedulePage({ scheduleId }: { scheduleId: string }) {
  const q = useQuery(scheduleQuery(scheduleId));
  const schedule = q.data;
  // The configuration name comes from the configurations list the interface already reads.
  const configs = useQuery(configsQuery());
  const cron = schedule?.cron ?? "";
  const timezone = schedule?.timezone ?? "UTC";
  const active = schedule?.state === "active";
  const starts = useQuery({
    ...nextStartsQuery(cron, timezone, NEXT_STARTS),
    enabled: active && cronFields(cron) !== null,
  });

  // The preview is kept for a minute and its key does not move with time, so it is read again
  // when the schedule's next start moves and no longer matches the first start listed.
  const nextStartAt = schedule?.next_start_at;
  const firstStart = starts.data?.starts[0];
  const refetchStarts = starts.refetch;
  useEffect(() => {
    if (!active || !nextStartAt || !firstStart) return;
    if (Date.parse(nextStartAt) !== Date.parse(firstStart)) void refetchStarts();
  }, [active, nextStartAt, firstStart, refetchStarts]);

  const notFound = !schedule && q.isError && describeScheduleError(q.error).notFound;
  useDocumentTitle(
    ...(schedule
      ? [schedule.name, "Schedules"]
      : notFound
        ? ["Schedule not found", "Schedules"]
        : ["Schedules"])
  );

  if (q.isPending) return <ScheduleSkeleton />;
  if (!schedule) {
    if (notFound) {
      return (
        <PageMessage
          title="Schedule not found"
          actions={
            <Link to="/schedules" className={buttonVariants({ variant: "outline", size: "sm" })}>
              View schedules
            </Link>
          }
        >
          <p>The Sync API has no schedule with the identifier in this address.</p>
        </PageMessage>
      );
    }
    return (
      <>
        <nav aria-label="Breadcrumb" className="pt-6 text-foreground-muted text-sm">
          <Link to="/schedules" className={CRUMB}>
            Schedules
          </Link>
        </nav>
        <ErrorPanel level="page" error={q.error} onRetry={() => q.refetch()} />
      </>
    );
  }

  const configName =
    configs.data?.items.find((c) => c.config_id === schedule.config_id)?.name ?? schedule.config_id;
  const branch = schedule.open_branch;

  return (
    <>
      <header className="flex flex-col gap-4 pt-6 pb-6">
        <Breadcrumb />
        <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-4">
          <div className="flex min-w-0 flex-col gap-2">
            <p className="font-medium text-subtle text-xs">Schedule</p>
            <h1 className="hyphens-none text-balance font-semibold text-2xl tracking-tight [overflow-wrap:anywhere] max-sm:text-xl">
              {schedule.name}
            </h1>
            <ul className={META_LIST}>
              <li className="flex min-w-0 max-w-full">
                <output aria-live="polite" aria-atomic="true">
                  <ScheduleStateLabel schedule={schedule} />
                </output>
              </li>
              <li className="flex min-w-0 max-w-full">
                <span>
                  <Link
                    to="/configs/$configId"
                    params={{ configId: schedule.config_id }}
                    className={cn(LINK, "[overflow-wrap:anywhere]")}
                  >
                    {configName}
                  </Link>{" "}
                  <span className="tabular-nums">version {schedule.registry_version}</span>
                </span>
              </li>
              <li className="flex min-w-0 max-w-full">
                <span className="[overflow-wrap:anywhere]">
                  Owner: <span className="text-foreground">{schedule.owner}</span>
                </span>
              </li>
            </ul>
          </div>
          {/* The actions of the signed-in account are added here (spec 026, T032 and T038). */}
        </div>
      </header>
      {q.isRefetchError && (
        <StaleNotice error={q.error} updatedAt={q.dataUpdatedAt} onRetry={() => q.refetch()} />
      )}
      <div className="flex flex-col gap-8">
        <ScheduleNotices schedule={schedule} />
        <Details
          id="schedule-next-start"
          title="Next start and last run"
          rows={[
            [
              "Next start",
              schedule.next_start_at ? (
                <StartTime iso={schedule.next_start_at} timezone={schedule.timezone} relative />
              ) : (
                <None />
              ),
            ],
            ...(schedule.waiting_start_at
              ? [
                  [
                    "Waiting start",
                    <StartTime iso={schedule.waiting_start_at} timezone={schedule.timezone} />,
                  ] as [string, ReactNode],
                ]
              : []),
            [
              "Last run",
              schedule.last_run_id ? <LastRun runId={schedule.last_run_id} /> : <None />,
            ],
            [
              "Failed runs in a row",
              <span className="tabular-nums">{schedule.consecutive_failures}</span>,
            ],
          ]}
        />
        <Details
          id="schedule-settings"
          title="Settings"
          rows={[
            ["Cron expression", <Cadence cron={schedule.cron} />],
            ["Time zone", schedule.timezone],
            ["Write target", writeTargetLabel(schedule.write_target)],
            ["Write when records are left out", leftOutText(schedule)],
            ["When a run is in progress", overlapPolicyLabel(schedule.overlap_policy)],
            ["Start late by at most", lateStartLabel(schedule.start_late_by_at_most_seconds)],
          ]}
        />
        {active && (
          <section aria-labelledby="schedule-next-starts" className="flex flex-col gap-3">
            <SectionHeading id="schedule-next-starts" title="Next starts">
              In the schedule's time zone, {schedule.timezone}
            </SectionHeading>
            <Card className="p-4">
              <NextStarts read={starts} timezone={schedule.timezone} count={NEXT_STARTS} />
            </Card>
          </section>
        )}
        <Details
          id="schedule-review-branch"
          title="Review branch"
          rows={
            branch
              ? [
                  ["Open branch", <BranchBadge branch={branch.branch} />],
                  [
                    "Created by run",
                    <Link to="/runs/$runId" params={{ runId: branch.run_id }} className={LINK}>
                      View run
                    </Link>,
                  ],
                  [
                    "Proposed change",
                    branch.proposed_change_id ? (
                      <code className={CODE_CHIP}>{branch.proposed_change_id}</code>
                    ) : (
                      <None />
                    ),
                  ],
                ]
              : [["Open branch", <None text="No review branch is open" />]]
          }
        />
        {/* The "History" section is added here (spec 026, T030). */}
        <Details
          id="schedule-record"
          title="Record"
          rows={[
            ["Created", <At iso={schedule.created_at} />],
            ["Updated", <At iso={schedule.updated_at} />],
          ]}
        />
      </div>
    </>
  );
}
