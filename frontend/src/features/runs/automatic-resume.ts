import type { AutomaticResume, Resume, Run } from "#/api/types";

/**
 * The name the Sync API shows for the reserved actor of the automatic resume (spec 030). An
 * automatic resume record names no actor, so its "resumed … by" line repeats the name here, as
 * `infrahub-sync runs show` does. The resume entries show the actor the Sync API returns.
 */
export const AUTOMATIC_RESUME_NAME = "Sync (automatic resume)";

/** What a record that has not ended is doing, in the words of `infrahub-sync runs show`. */
const STATE_TEXT: Record<string, string> = {
  "waiting-check": "waiting for a live worker to run the check",
  checking: "checking",
  "waiting-resume": "waiting for a live worker to resume",
  resuming: "resuming",
};

export interface AutomaticResumeLine {
  label: string;
  text: string;
}

export interface AutomaticResumeBlock {
  /** "Automatic resume 1: started", "Automatic resume 2: not started" or the state of a record that has not ended. */
  title: string;
  /** Whether Sync did not resume the run: the record ended without a resume. */
  notStarted: boolean;
  lines: AutomaticResumeLine[];
}

const startText = (startAfter?: number | null) =>
  startAfter == null ? "from the first operation" : `after operation ${startAfter}`;

/**
 * The text of one automatic resume record, line for line as `infrahub-sync runs show` prints it
 * (spec 030 contracts/cli-and-web.md). `number` counts the records from 1, oldest first; `next` is
 * the advice of `automaticResumeNext`, when the record gets one.
 */
export function automaticResumeBlock(
  row: AutomaticResume,
  number: number,
  next?: string
): AutomaticResumeBlock {
  const ended = row.state === "ended";
  const heading = ended
    ? row.decision === "resume"
      ? "started"
      : "not started"
    : (STATE_TEXT[row.state] ?? row.state);
  const lines: AutomaticResumeLine[] = [{ label: "cause", text: row.cause_text }];
  // The check passed once the record waits for the resume, or once the resume started.
  if (row.state === "waiting-resume" || row.state === "resuming" || row.decision === "resume") {
    lines.push({
      label: "check",
      text: `passed, 0 differs, ${row.operations_not_checked ?? 0} not checked`,
    });
  }
  if (row.decision === "resume") {
    lines.push({
      label: "resumed",
      text: `${startText(row.start_after)} by ${AUTOMATIC_RESUME_NAME}`,
    });
  }
  if (row.reason_text != null) lines.push({ label: "reason", text: row.reason_text });
  if (next !== undefined) lines.push({ label: "next", text: next });
  return {
    title: `Automatic resume ${number}: ${heading}`,
    notStarted: ended && row.decision !== "resume",
    lines,
  };
}

/**
 * What the operator can do after Sync did not resume the run, in the words of the `next:` line of
 * `infrahub-sync runs show` (spec 030 contracts/cli-and-web.md). Only the latest record gets it,
 * only when it ended without a resume, and only while the run still has an interrupted write to
 * resolve. A run that `runs resume` refuses (`not-resumable`) gets no `runs resume` command.
 */
export function automaticResumeNext(
  row: AutomaticResume,
  latest: boolean,
  run: Pick<Run, "run_id" | "reconciliation_required">
): string | undefined {
  if (
    !latest ||
    row.state !== "ended" ||
    row.decision !== "no-resume" ||
    !run.reconciliation_required
  ) {
    return undefined;
  }
  if (row.reason === "not-resumable") {
    return "create a new plan; `infrahub-sync runs resume` cannot resume this run";
  }
  return `run \`infrahub-sync verify ${run.run_id}\`, then a new plan or \`infrahub-sync runs resume ${run.run_id} --expected-checksum CHECKSUM\``;
}

/** Whether Sync is still deciding or carrying out an automatic resume of the run. */
export const automaticResumePending = (rows: readonly AutomaticResume[] | undefined) =>
  (rows ?? []).some((row) => row.state !== "ended");

export interface ResumeEntry {
  key: string;
  title: string;
  /** The account that asked for the resume, as the Sync API names it. */
  actor: string;
  requestedAt: string;
  start: string;
  outcome: string;
}

/** One entry per resume of the run, oldest first, with the name the Sync API returns as the actor. */
export function resumeEntries(resumes: readonly Resume[] | undefined): ResumeEntry[] {
  return (resumes ?? []).map((resume, i) => ({
    key: `${resume.requested_at}-${i}`,
    title: `Resume ${i + 1}`,
    actor: resume.actor,
    requestedAt: resume.requested_at,
    start: `Started ${startText(resume.start_after)}`,
    outcome: `${resume.outcome == null ? "Not finished" : (OUTCOME_TEXT[resume.outcome] ?? resume.outcome)}${
      resume.operations_written == null
        ? ""
        : `, wrote ${resume.operations_written} ${
            resume.operations_written === 1 ? "operation" : "operations"
          }`
    }`,
  }));
}

/** A resume execution's outcome, in the words the timeline uses for any execution. */
const OUTCOME_TEXT: Record<string, string> = {
  succeeded: "Succeeded",
  failed: "Failed",
  ambiguous: "Interrupted: result unknown",
  cancelled: "Cancelled",
  abandoned: "Never picked up",
};
