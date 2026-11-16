import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { planLeftOutQuery } from "#/api/queries";
import type { Plan } from "#/api/types";
import { Notice } from "#/components/page";
import { fmt } from "#/domain/run-state";

import { type Json, nodeLabel } from "./plan-values";

type Entry = Record<string, unknown>;

const records = (n: number) => `${fmt(n)} ${n === 1 ? "record" : "records"}`;

function recordLabel(record: unknown): string {
  if (!record || typeof record !== "object") return "an unknown record";
  const r = record as Entry;
  const kind = typeof r.kind === "string" ? `${r.kind} ` : "";
  const identity = r.identity;
  if (identity && typeof identity === "object" && Object.keys(identity).length > 0)
    return `${kind}${nodeLabel(identity as Json)}`;
  return `${kind}with source ID ${String(r.source_id ?? "unknown")}`;
}

/** Why the plan leaves the record out, in words. */
function entryReason(entry: Entry): string {
  if (entry.cause === "depends_on_left_out")
    return `depends on ${recordLabel(entry.depends_on)}, which is left out too`;
  const types = Array.isArray(entry.problem_types) ? entry.problem_types.map(String) : [];
  return types.length > 0 ? `has a problem: ${types.join(", ").replaceAll("_", " ")}` : "";
}

/**
 * Whether the write must confirm the records the plan leaves out, and that confirmation. The Sync
 * service refuses an apply or a resume of such a plan unless the request confirms them.
 */
export function useLeftOutConfirmation(plan: Plan) {
  const [confirmed, setConfirmed] = useState(false);
  const required = plan.summary.left_out > 0;
  return { required, confirmed: required && confirmed, setConfirmed };
}

/**
 * The records the plan leaves out, and the box that confirms they were reviewed. The write does
 * not create or update them.
 */
export function LeftOutReview({
  runId,
  plan,
  confirmed,
  onConfirmedChange,
  verb,
}: {
  runId: string;
  plan: Plan;
  confirmed: boolean;
  onConfirmedChange: (value: boolean) => void;
  /** "Apply" or "Resume": the action that skips the records. */
  verb: string;
}) {
  const summary = plan.summary;
  const page = useQuery(planLeftOutQuery(runId, plan.checksum));
  const entries = (page.data?.entries ?? []) as Entry[];
  const total = page.data?.total ?? summary.left_out;
  const rest = total - entries.length;
  const id = `left-out-confirm-${runId}`;

  return (
    <Notice tone="warning" role="note" title={`This plan leaves out ${records(summary.left_out)}`}>
      <p className="text-pretty">
        {verb} does not create or update them.{" "}
        {summary.left_out_with_problem > 0 &&
          `${records(summary.left_out_with_problem)} with a problem`}
        {summary.left_out_with_problem > 0 && summary.left_out_dependent > 0 && ", "}
        {summary.left_out_dependent > 0 &&
          `${records(summary.left_out_dependent)} depending on a record that is left out`}
        {(summary.left_out_with_problem > 0 || summary.left_out_dependent > 0) && "."}
      </p>
      {page.isPending ? (
        <p className="text-subtle text-xs">Loading the records that are left out.</p>
      ) : page.isError ? (
        <p className="text-subtle text-xs">
          The list of records could not be read. The count above comes from the saved plan.
        </p>
      ) : (
        <ul className="mt-1 flex max-h-40 list-disc flex-col gap-0.5 overflow-y-auto pl-4 text-xs">
          {entries.map((entry, index) => (
            <li key={index} className="[overflow-wrap:anywhere]">
              {recordLabel(entry.record)}
              {entryReason(entry) && <span className="text-subtle"> {entryReason(entry)}</span>}
            </li>
          ))}
          {rest > 0 && <li className="text-subtle">and {records(rest)} more</li>}
        </ul>
      )}
      <label htmlFor={id} className="mt-2 flex items-start gap-2 font-medium text-sm">
        <input
          id={id}
          type="checkbox"
          className="mt-0.5 size-4 shrink-0"
          checked={confirmed}
          onChange={(e) => onConfirmedChange(e.target.checked)}
        />
        <span>
          I reviewed the {records(summary.left_out)} this plan leaves out. {verb} without them.
        </span>
      </label>
    </Notice>
  );
}

/** The reason a write stays disabled until the left-out records are confirmed, if it does. */
export const leftOutDisabledReason = (required: boolean, confirmed: boolean) =>
  required && !confirmed
    ? "Confirm that you reviewed the records this plan leaves out."
    : undefined;
