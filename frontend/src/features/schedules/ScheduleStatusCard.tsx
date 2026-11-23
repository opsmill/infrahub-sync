import { Button, buttonVariants } from "@infrahub/ui";
import { Link } from "@tanstack/react-router";
import { cn } from "tailwind-variants";

import { StateIcon, WarningIcon } from "#/components/icons";
import type {
  ScheduleStatus,
  StatusIcon,
  StatusNote,
  StatusStep,
  StatusTone,
  StepDialog,
} from "#/domain/schedule-page";

import { TOUCH } from "./ScheduleListParts";

/** The tints of the schedules list's cards: red for stopped, amber for failing. */
const TONE_BOX: Record<StatusTone, string> = {
  neutral: "border bg-content",
  warning: "border border-warning-border bg-warning-surface",
  danger: "border border-danger/30 bg-danger-surface",
};

const ICON_CLASS = "mt-0.5 size-4 shrink-0";

function ToneIcon({ icon }: { icon: StatusIcon }) {
  switch (icon) {
    case "stopped":
      return <StateIcon state="failed" className={cn(ICON_CLASS, "text-danger")} />;
    case "failing":
      return <WarningIcon className={cn(ICON_CLASS, "text-warning")} />;
    case "resting":
      return <StateIcon state="idle" className={cn(ICON_CLASS, "text-subtle")} />;
    default:
      return <StateIcon state="insync" className={cn(ICON_CLASS, "text-success")} />;
  }
}

/** A step of the card: a button that opens a dialog of the page, or a link to the schedule's runs. */
function Step({
  step,
  primary,
  scheduleId,
  onOpen,
}: {
  step: StatusStep;
  primary: boolean;
  scheduleId: string;
  onOpen: (dialog: StepDialog) => void;
}) {
  const variant = primary ? "primary" : "outline";
  if ("runs" in step) {
    return (
      <Link
        to="/runs"
        search={{ schedule: scheduleId }}
        className={cn(buttonVariants({ variant, size: "sm" }), "px-3", TOUCH)}
      >
        {step.label}
      </Link>
    );
  }
  return (
    <Button variant={variant} size="sm" className={TOUCH} onPress={() => onOpen(step.dialog)}>
      {step.label}
    </Button>
  );
}

function Note({
  note,
  scheduleId,
  onOpen,
}: {
  note: StatusNote;
  scheduleId: string;
  onOpen: (dialog: StepDialog) => void;
}) {
  return (
    <div className="flex flex-col gap-2 border-t pt-3 xl:flex-row xl:items-center xl:justify-between xl:gap-6">
      <div className="flex min-w-0 items-start gap-3">
        {note.kind === "not-handed-over" ? (
          <WarningIcon className={cn(ICON_CLASS, "text-warning")} />
        ) : (
          <StateIcon state="idle" className={cn(ICON_CLASS, "text-subtle")} />
        )}
        <div className="flex min-w-0 flex-col gap-0.5 text-sm">
          <p className="max-w-[70ch] text-pretty font-medium text-foreground">{note.text}</p>
          {note.detail && <p className="text-pretty text-foreground-muted">{note.detail}</p>}
        </div>
      </div>
      {note.step && (
        <div className="shrink-0 pl-7 xl:pl-0">
          <Step step={note.step} primary={false} scheduleId={scheduleId} onOpen={onOpen} />
        </div>
      )}
    </div>
  );
}

/**
 * The status card of a schedule page (design-schedule-page.md): the situation in one sentence, its
 * next step as the main button, then the facts that also need a person: the schedule is not handed
 * to the task manager, or a newer version of the configuration is registered.
 */
export function ScheduleStatusCard({
  status,
  notes,
  scheduleId,
  onOpen,
  title,
}: {
  status: ScheduleStatus;
  notes: StatusNote[];
  scheduleId: string;
  onOpen: (dialog: StepDialog) => void;
  /** Shown on hover of the sentence: the next start in the viewer's time. */
  title?: string;
}) {
  return (
    <section
      aria-labelledby="schedule-status"
      className={cn("flex flex-col gap-3 rounded-xl px-4 py-3.5", TONE_BOX[status.tone])}
    >
      <div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between xl:gap-6">
        <div className="flex min-w-0 items-start gap-3">
          <ToneIcon icon={status.icon} />
          <div className="flex min-w-0 flex-col gap-1">
            <h2
              id="schedule-status"
              title={title}
              className="max-w-[65ch] text-pretty font-medium text-[15px] text-foreground leading-snug"
            >
              {status.text}
            </h2>
            {status.details.map((d) => (
              <p
                key={d}
                className="max-w-[70ch] text-pretty text-foreground-muted text-sm [overflow-wrap:anywhere]"
              >
                {d}
              </p>
            ))}
          </div>
        </div>
        {(status.step || status.second) && (
          <div className="flex shrink-0 flex-wrap items-center gap-2 pl-7 xl:pl-0">
            {status.second && (
              <Step step={status.second} primary={false} scheduleId={scheduleId} onOpen={onOpen} />
            )}
            {status.step && (
              <Step step={status.step} primary scheduleId={scheduleId} onOpen={onOpen} />
            )}
          </div>
        )}
      </div>
      {notes.map((n) => (
        <Note key={n.kind} note={n} scheduleId={scheduleId} onOpen={onOpen} />
      ))}
    </section>
  );
}
