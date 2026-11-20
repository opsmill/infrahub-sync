import { Button, buttonVariants, Card } from "@infrahub/ui";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { cn } from "tailwind-variants";

import {
  configsQuery,
  describeScheduleError,
  meQuery,
  nextStartsQuery,
  scheduleQuery,
  scheduleRunsQuery,
} from "#/api/queries";
import { Bar, CRUMB, ErrorPanel, META_LIST, PageMessage, StaleNotice } from "#/components/page";
import { When } from "#/components/run-status";
import { cronFields } from "#/domain/cadence";
import {
  headerActions,
  type StepDialog,
  scheduleStatus,
  statusNotes,
  TIMELINE_RUNS,
} from "#/domain/schedule-page";
import type { ActionAccess, ScheduleAction } from "#/domain/schedules";
import { scheduleActions } from "#/domain/schedules";
import { LINK } from "#/features/runs/form";
import { formatAbsolute, useNow } from "#/lib/time";
import { useDocumentTitle } from "#/lib/title";

import {
  DeleteScheduleDialog,
  PauseScheduleDialog,
  ResumeScheduleDialog,
  TakeOverScheduleDialog,
} from "./ScheduleActionDialogs";
import { ScheduleFormDialog } from "./ScheduleFormDialog";
import { ScheduleHistory } from "./ScheduleHistory";
import { TOUCH } from "./ScheduleListParts";
import { ScheduleReviewBranch, ScheduleSettings } from "./ScheduleSettings";
import { ScheduleStateLabel } from "./ScheduleStateLabel";
import { ScheduleStatusCard } from "./ScheduleStatusCard";
import { ScheduleTimeline } from "./ScheduleTimeline";

/** The number of start times the timeline asks for. */
const NEXT_STARTS = 5;

/** The three columns from `lg`; below it, status, timeline, history, then settings. */
const COLUMNS =
  "grid grid-cols-1 items-start gap-8 lg:grid-cols-[15rem_minmax(0,1fr)_16rem] lg:grid-rows-[auto_1fr] lg:gap-x-6 lg:[grid-template-areas:'left_status_right'_'left_history_right']";

function Breadcrumb() {
  return (
    <nav aria-label="Breadcrumb" className="text-foreground-muted text-sm">
      <Link to="/schedules" className={CRUMB}>
        Schedules
      </Link>
    </nav>
  );
}

/** The loaded page's shape, so nothing moves when the schedule arrives. */
function ScheduleSkeleton() {
  return (
    <div aria-busy="true">
      <output className="sr-only">Loading the schedule</output>
      <div aria-hidden="true">
        <div className="flex flex-col gap-4 pt-6 pb-6">
          <div className="flex h-5 items-center">
            <Bar className="h-4 w-24" />
          </div>
          <Bar className="h-8 w-[24rem] max-w-full max-sm:h-7" />
          <div className="flex h-5 items-center">
            <Bar className="h-4 w-96 max-w-full" />
          </div>
        </div>
        <div className={COLUMNS}>
          <Card className="flex flex-col gap-3 bg-content bg-none p-4 lg:[grid-area:status]">
            <Bar className="h-4 w-80 max-w-full" />
            <Bar className="h-3.5 w-64 max-w-full" />
          </Card>
          <Card className="flex flex-col gap-3 bg-content bg-none p-4 lg:[grid-area:right]">
            {Array.from({ length: 6 }, (_, i) => (
              <Bar key={i} className="h-3.5 w-40 max-w-full" />
            ))}
          </Card>
          <Card className="flex flex-col gap-4 bg-content bg-none p-4 lg:[grid-area:history]">
            {[0, 1, 2].map((i) => (
              <div key={i} className="flex flex-col gap-1.5">
                <Bar className="h-4 w-72 max-w-full" />
                <Bar className="h-3 w-40" />
              </div>
            ))}
          </Card>
          <Card className="flex flex-col gap-4 bg-content bg-none p-4 lg:[grid-area:left]">
            {Array.from({ length: 5 }, (_, i) => (
              <div key={i} className="flex flex-col gap-1.5">
                <Bar className="h-3 w-20" />
                <Bar className="h-4 w-32" />
              </div>
            ))}
          </Card>
        </div>
      </div>
    </div>
  );
}

/** The dialog open on the page; "change-version" is the change form with the newest version filled in. */
type PageDialog = ScheduleAction | "change-version";

const ACTION_LABEL: Record<ScheduleAction, string> = {
  change: "Change",
  pause: "Pause",
  resume: "Resume",
  "take-over": "Take over",
  delete: "Delete",
};

/**
 * The header buttons, quiet, without the action the status card offers. When an action is shown
 * but not available, its reason is shown once in place of the buttons, as on the run page.
 */
function HeaderActions({
  actions,
  access,
  onOpen,
}: {
  actions: ScheduleAction[];
  access: Record<ScheduleAction, ActionAccess>;
  onOpen: (action: ScheduleAction) => void;
}) {
  if (actions.length === 0) return null;
  const refused = actions.find((a) => !access[a].available);
  if (refused) {
    return (
      <p className="max-w-[40ch] text-foreground-muted text-xs">
        {access[refused].unavailableReason}
      </p>
    );
  }
  return (
    <div className="-mx-1.5 flex shrink-0 flex-wrap items-center gap-1">
      {actions.map((a) => (
        <Button
          key={a}
          variant="ghost"
          size="sm"
          className={cn(TOUCH, a === "delete" && "text-danger")}
          onPress={() => onOpen(a)}
        >
          {ACTION_LABEL[a]}
        </Button>
      ))}
    </div>
  );
}

/**
 * The page of one schedule (design-schedule-page.md, contracts/web-pages.md): a header with the
 * actions; a status card with the situation and its next step; the history; the settings and the
 * open review branch; and a timeline of the next starts and the last runs. The schedule is read
 * again every 10 seconds. The schedule identifier, the owner and the account that set "write when
 * records are left out" are shown only as the strings the Sync API returns.
 */
export function SchedulePage({
  scheduleId,
  showAllEvents = false,
}: {
  scheduleId: string;
  showAllEvents?: boolean;
}) {
  const q = useQuery(scheduleQuery(scheduleId));
  const schedule = q.data;
  // The configuration name comes from the configurations list the interface already reads.
  const configs = useQuery(configsQuery());
  const me = useQuery(meQuery());
  const runs = useQuery(scheduleRunsQuery(scheduleId, TIMELINE_RUNS));
  const now = useNow();
  const [dialog, setDialog] = useState<PageDialog | null>(null);
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
  const access = scheduleActions(me.data, schedule);
  const status = scheduleStatus(schedule, access, now);
  const notes = statusNotes(schedule, access, status);
  const closeDialog = () => setDialog(null);
  const openStep = (step: StepDialog) => setDialog(step);

  return (
    <>
      <header className="flex flex-col gap-4 pt-6 pb-6">
        <Breadcrumb />
        <div className="flex flex-col gap-x-6 gap-y-3 md:flex-row md:items-start md:justify-between">
          <div className="flex min-w-0 flex-col gap-2">
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
          <HeaderActions
            actions={headerActions(access, status)}
            access={access}
            onOpen={setDialog}
          />
        </div>
      </header>
      {q.isRefetchError && (
        <StaleNotice error={q.error} updatedAt={q.dataUpdatedAt} onRetry={() => q.refetch()} />
      )}
      <div className={COLUMNS}>
        <div className="min-w-0 lg:[grid-area:status]">
          <ScheduleStatusCard
            status={status}
            notes={notes}
            scheduleId={schedule.schedule_id}
            onOpen={openStep}
            title={
              status.icon === "active" && schedule.next_start_at
                ? `Your time: ${formatAbsolute(schedule.next_start_at)}`
                : undefined
            }
          />
        </div>
        <aside
          aria-label="Next starts and last runs"
          className="min-w-0 lg:sticky lg:top-16 lg:[grid-area:right]"
        >
          <ScheduleTimeline
            schedule={schedule}
            starts={starts}
            runs={runs}
            count={NEXT_STARTS}
            runCount={TIMELINE_RUNS}
          />
        </aside>
        <div className="min-w-0 lg:[grid-area:history]">
          <ScheduleHistory scheduleId={scheduleId} includeAll={showAllEvents} />
        </div>
        <aside
          aria-label="Settings and review branch"
          className="flex min-w-0 flex-col gap-6 lg:[grid-area:left]"
        >
          <ScheduleSettings schedule={schedule} />
          <ScheduleReviewBranch schedule={schedule} />
          <p className="text-subtle text-xs">
            Created <When iso={schedule.created_at} />, updated <When iso={schedule.updated_at} />.
          </p>
        </aside>
      </div>
      {(dialog === "change" || dialog === "change-version") && (
        <ScheduleFormDialog
          target={{
            mode: "change",
            schedule,
            configName,
            newestVersion: dialog === "change-version",
          }}
          onClose={closeDialog}
        />
      )}
      {dialog === "pause" && <PauseScheduleDialog schedule={schedule} onClose={closeDialog} />}
      {dialog === "resume" && <ResumeScheduleDialog schedule={schedule} onClose={closeDialog} />}
      {dialog === "take-over" && (
        <TakeOverScheduleDialog schedule={schedule} onClose={closeDialog} />
      )}
      {dialog === "delete" && <DeleteScheduleDialog schedule={schedule} onClose={closeDialog} />}
    </>
  );
}
