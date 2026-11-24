import { type ReactNode, useState } from "react";

import {
  useDeleteSchedule,
  useIdempotencyKey,
  usePauseSchedule,
  useResumeSchedule,
  useTakeOverSchedule,
} from "#/api/actions";
import { describeScheduleError } from "#/api/queries";
import type { Schedule } from "#/api/types";
import { announce } from "#/components/announcer";
import { BranchBadge } from "#/components/run-status";
import {
  DialogFrame,
  finePointer,
  MutationError,
  ReasonField,
  resendHint,
} from "#/features/runs/form";

interface DialogProps {
  schedule: Schedule;
  onClose: () => void;
}

/** What a schedule action dialog needs from its mutation. */
interface ActionRequest {
  /** Sends the request with the retry key and the reason, and calls `onSuccess` on a 2xx answer. */
  send: (key: string, reason: string | null, onSuccess: () => void) => void;
  isPending: boolean;
  error: unknown;
  reset: () => void;
}

/**
 * A confirmation dialog for one schedule action, with an optional reason. The retry key stays the
 * same while the reason is unchanged, so pressing the button again after a lost answer resends the
 * same request (FR-021); a new reason is a new request.
 */
function ScheduleActionDialog({
  id,
  title,
  description,
  submitLabel,
  pendingLabel,
  done,
  danger = false,
  submitOnEnter = !danger,
  request,
  onClose,
}: {
  id: string;
  title: string;
  description: ReactNode;
  submitLabel: string;
  pendingLabel: string;
  /** Read to screen readers after a success. */
  done: string;
  danger?: boolean;
  /** Enter submits the dialog; off for a danger action and for a confirmation that must be read. */
  submitOnEnter?: boolean;
  request: ActionRequest;
  onClose: () => void;
}) {
  const [key, renew] = useIdempotencyKey();
  const [reason, setReason] = useState("");
  return (
    <DialogFrame
      isOpen
      onOpenChange={(open) => !open && onClose()}
      title={title}
      ariaLabel={title}
      description={description}
      error={
        request.error ? (
          <MutationError
            error={request.error}
            describe={describeScheduleError}
            retryHint={resendHint(submitLabel)}
          />
        ) : undefined
      }
      submitLabel={submitLabel}
      submitVariant={danger ? "danger" : "primary"}
      pendingLabel={pendingLabel}
      isPending={request.isPending}
      dismissLabel={danger ? "Keep schedule" : "Cancel"}
      autoFocusDismiss={danger}
      submitOnEnter={submitOnEnter}
      onSubmit={() =>
        request.send(key, reason.trim() || null, () => {
          onClose();
          announce(done);
        })
      }
    >
      <ReasonField
        id={id}
        value={reason}
        onChange={(v) => {
          setReason(v);
          renew();
          request.reset();
        }}
        autoFocus={!danger && finePointer()}
      />
    </DialogFrame>
  );
}

const path = (schedule: Schedule, key: string) => ({
  path: { schedule_id: schedule.schedule_id },
  header: { "Idempotency-Key": key },
});

export function PauseScheduleDialog({ schedule, onClose }: DialogProps) {
  const pause = usePauseSchedule();
  return (
    <ScheduleActionDialog
      id="pause-schedule-reason"
      title="Pause this schedule"
      description="The schedule starts no run until it is resumed."
      submitLabel="Pause schedule"
      pendingLabel="Pausing the schedule"
      done="Schedule paused"
      request={{
        send: (key, reason, onSuccess) =>
          pause.mutate({ params: path(schedule, key), body: { reason } }, { onSuccess }),
        isPending: pause.isPending,
        error: pause.error,
        reset: pause.reset,
      }}
      onClose={onClose}
    />
  );
}

export function ResumeScheduleDialog({ schedule, onClose }: DialogProps) {
  const resume = useResumeSchedule();
  return (
    <ScheduleActionDialog
      id="resume-schedule-reason"
      title="Resume this schedule"
      description="The schedule starts runs again from its next start."
      submitLabel="Resume schedule"
      pendingLabel="Resuming the schedule"
      done="Schedule resumed"
      request={{
        send: (key, reason, onSuccess) =>
          resume.mutate({ params: path(schedule, key), body: { reason } }, { onSuccess }),
        isPending: resume.isPending,
        error: resume.error,
        reset: resume.reset,
      }}
      onClose={onClose}
    />
  );
}

export function DeleteScheduleDialog({ schedule, onClose }: DialogProps) {
  const remove = useDeleteSchedule();
  const branch = schedule.open_branch?.branch;
  return (
    <ScheduleActionDialog
      id="delete-schedule-reason"
      title="Delete this schedule"
      description={
        <>
          <p>
            A run in progress is not cancelled. An open review branch and its proposed change stay
            in Infrahub.
          </p>
          {branch && (
            <p className="mt-1">
              Open review branch: <BranchBadge branch={branch} />
            </p>
          )}
        </>
      }
      submitLabel="Delete schedule"
      pendingLabel="Deleting the schedule"
      done="Schedule deleted"
      danger
      request={{
        send: (key, reason, onSuccess) =>
          remove.mutate({ params: path(schedule, key), body: { reason } }, { onSuccess }),
        isPending: remove.isPending,
        error: remove.error,
        reset: remove.reset,
      }}
      onClose={onClose}
    />
  );
}

/**
 * Take over a schedule (FR-014, FR-016). The answer is the schedule after the take-over, which the
 * page shows: it can be stopped with `approval-required` when the schedule writes to the default
 * branch and the Sync API requires approval.
 */
export function TakeOverScheduleDialog({ schedule, onClose }: DialogProps) {
  const takeOver = useTakeOverSchedule();
  return (
    <ScheduleActionDialog
      id="take-over-schedule-reason"
      title="Take over this schedule"
      description="You become the owner of this schedule. Its next runs record you as the account that started them."
      submitLabel="Take over schedule"
      pendingLabel="Taking over the schedule"
      done="You own this schedule"
      submitOnEnter={false}
      request={{
        send: (key, reason, onSuccess) =>
          takeOver.mutate({ params: path(schedule, key), body: { reason } }, { onSuccess }),
        isPending: takeOver.isPending,
        error: takeOver.error,
        reset: takeOver.reset,
      }}
      onClose={onClose}
    />
  );
}
