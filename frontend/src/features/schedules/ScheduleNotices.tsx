import type { ReactNode } from "react";

import type { Schedule } from "#/api/types";
import { Notice } from "#/components/page";
import { type PanelOffer, stateView } from "#/domain/schedules";

const NOT_HANDED_OVER =
  "This schedule starts no run until the Sync API hands it to the task manager. Restarting the Sync API or changing the schedule hands it over again.";

/**
 * The notices of a schedule page: the state panel (stop reason and what to do), the task-manager
 * warning when `prefect_synced` is false, and the newer version notice. The page passes the
 * buttons it allows the signed-in account, keyed by the step of the panel they perform, and the
 * button that changes the version.
 */
export function ScheduleNotices({
  schedule,
  offers = {},
  changeVersion,
}: {
  schedule: Schedule;
  offers?: Partial<Record<PanelOffer, ReactNode>>;
  changeVersion?: ReactNode;
}) {
  const panel = stateView(schedule).panel;
  const offered = panel?.offers.flatMap((o) => (offers[o] ? [[o, offers[o]] as const] : [])) ?? [];
  const stopped = schedule.state === "stopped";
  const newer = schedule.newer_version;
  const live = schedule.state !== "deleted";

  if (!panel && schedule.prefect_synced && (newer == null || !live)) return null;
  return (
    <div className="flex flex-col gap-3">
      {panel && (
        <Notice
          tone={stopped ? "warning" : "neutral"}
          title={panel.text}
          role={stopped ? "alert" : "status"}
          action={
            offered.length > 0 ? (
              <div className="flex flex-wrap items-center gap-2">
                {offered.map(([key, node]) => (
                  <span key={key}>{node}</span>
                ))}
              </div>
            ) : undefined
          }
        >
          {panel.action && <p>{panel.action}</p>}
          {stopped && schedule.state_reason === "repeated-failures" && (
            <p className="tabular-nums">Failed runs in a row: {schedule.consecutive_failures}</p>
          )}
        </Notice>
      )}
      {!schedule.prefect_synced && live && (
        <Notice tone="warning" title={NOT_HANDED_OVER} role="status" />
      )}
      {newer != null && live && (
        <Notice
          tone="neutral"
          title={`Version ${newer} of this configuration is registered`}
          action={changeVersion}
        >
          <p>This schedule starts runs of version {schedule.registry_version}.</p>
        </Notice>
      )}
    </div>
  );
}
