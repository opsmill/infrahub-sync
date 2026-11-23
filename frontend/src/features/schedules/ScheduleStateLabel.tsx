import type { Schedule } from "#/api/types";
import { StateLabel } from "#/components/run-status";
import type { RunState } from "#/domain/run-state";
import { stateView } from "#/domain/schedules";

/** The icon of a schedule state: a stopped schedule needs attention, a paused or deleted one rests. */
const STATE_ICON: Record<string, RunState> = {
  active: "insync",
  paused: "idle",
  stopped: "failed",
  deleted: "idle",
};

/** The state badge of a schedule, with the words of `stateView`. */
export function ScheduleStateLabel({
  schedule,
  className,
}: {
  schedule: Schedule;
  className?: string;
}) {
  return (
    <StateLabel
      state={STATE_ICON[schedule.state] ?? "idle"}
      label={stateView(schedule).label}
      quiet={schedule.state === "deleted"}
      className={className}
    />
  );
}
