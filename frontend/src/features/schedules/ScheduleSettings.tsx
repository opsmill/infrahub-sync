import { Card } from "@infrahub/ui";
import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";
import { cn } from "tailwind-variants";

import type { Schedule } from "#/api/types";
import { CODE_CHIP, SectionHeading } from "#/components/page";
import { BranchBadge, When } from "#/components/run-status";
import { cadenceWords, formFromCron } from "#/domain/cadence";
import { lateStartLabel, overlapPolicyLabel, writeTargetLabel } from "#/domain/schedules";
import { LINK } from "#/features/runs/form";

/** A setting: its label, and its value under it, so the column can stay narrow. */
function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5 px-4 py-2.5">
      <dt className="text-subtle text-xs">{label}</dt>
      <dd className="min-w-0 text-[13px] text-foreground [overflow-wrap:anywhere]">{children}</dd>
    </div>
  );
}

/**
 * The settings of a schedule, each value under its label: version, cadence, time zone, write
 * target, records left out with who set it and when, overlap policy and late start. Accounts are
 * shown as the Sync API names them.
 */
export function ScheduleSettings({ schedule: s }: { schedule: Schedule }) {
  const words = cadenceWords(formFromCron(s.cron));
  return (
    <section aria-labelledby="schedule-settings" className="flex flex-col gap-2">
      <SectionHeading id="schedule-settings" title="Settings" />
      <Card className="overflow-clip bg-content bg-none p-0">
        <dl className="divide-y">
          <Row label="Version">
            <span className="tabular-nums">Version {s.registry_version}</span>
            {s.newer_version != null && s.state !== "deleted" && (
              <span className="block text-accent tabular-nums">
                Version {s.newer_version} is registered
              </span>
            )}
          </Row>
          <Row label="Starts">
            <span className="flex flex-col items-start gap-1">
              {words && <span>{words}</span>}
              <code className={cn(CODE_CHIP, "max-w-full [overflow-wrap:anywhere]")}>{s.cron}</code>
            </span>
          </Row>
          <Row label="Time zone">{s.timezone}</Row>
          <Row label="Writes to">{writeTargetLabel(s.write_target)}</Row>
          <Row label="Records left out">
            {s.write_when_left_out ? "Written" : "Not written"}
            {(s.left_out_set_by || s.left_out_set_at) && (
              <span className="block text-foreground-muted text-xs">
                Set
                {s.left_out_set_by && (
                  <>
                    {" "}
                    by <span className="text-foreground">{s.left_out_set_by}</span>
                  </>
                )}
                {s.left_out_set_at && (
                  <>
                    {" "}
                    <When iso={s.left_out_set_at} />
                  </>
                )}
              </span>
            )}
          </Row>
          <Row label="When a run is in progress">{overlapPolicyLabel(s.overlap_policy)}</Row>
          <Row label="Start late by at most">{lateStartLabel(s.start_late_by_at_most_seconds)}</Row>
        </dl>
      </Card>
    </section>
  );
}

/** The open review branch, its proposed change and the run that opened it; nothing when none is open. */
export function ScheduleReviewBranch({ schedule }: { schedule: Schedule }) {
  const b = schedule.open_branch;
  if (!b) return null;
  return (
    <section aria-labelledby="schedule-review-branch" className="flex flex-col gap-2">
      <SectionHeading id="schedule-review-branch" title="Review branch" />
      <Card className="flex flex-col gap-2 bg-content bg-none px-4 py-3 text-[13px]">
        <BranchBadge branch={b.branch} truncate title={b.branch} className="self-start" />
        {b.proposed_change_id && (
          <p className="flex flex-col gap-0.5 text-foreground-muted text-xs">
            Proposed change
            <code className={cn(CODE_CHIP, "self-start [overflow-wrap:anywhere]")}>
              {b.proposed_change_id}
            </code>
          </p>
        )}
        <Link to="/runs/$runId" params={{ runId: b.run_id }} className={LINK}>
          View the run that opened it
        </Link>
      </Card>
    </section>
  );
}
