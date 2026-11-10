import type { ReactNode } from "react";

import type { Plan } from "#/api/types";
import { CheckIcon } from "#/components/icons";
import { CopyButton } from "#/components/page";
import { BranchCode } from "#/components/run-status";
import { countsSentence, fmt } from "#/domain/run-state";

/* ---------- the sentence under the Changes heading ---------- */

function listWords(items: string[]): string {
  if (items.length <= 1) return items.join("");
  return `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

/** The sentence under the Changes heading, or undefined for an empty plan. */
export function changesSentence(plan: Plan, phase: string): string | undefined {
  const {
    by_action: by,
    total,
    deletes_not_executed: d,
    delete_operations_computed: deletesComputed,
  } = plan.summary;
  if (total === 0) return undefined;
  const create = by.create ?? 0;
  const update = by.update ?? 0;
  if (phase === "applied") {
    const counts = { create, update, delete: by.delete ?? 0, total, deletesComputed };
    return countsSentence(counts, "done") || undefined;
  }
  const notRead = deletesComputed
    ? ""
    : " Nodes that are not in the source are not listed, because the destination was not read in full.";
  const writes: string[] = [];
  if (create) writes.push(`${fmt(create)} to create`);
  if (update) writes.push(`${fmt(update)} to update`);
  const missing = d === 1 ? "1 node that is" : `${fmt(d)} nodes that are`;
  if (!plan.checksum_ok) {
    const listed = d > 0 ? [...writes, `${missing} not in the source`] : writes;
    return `The plan lists ${listed.length ? listWords(listed) : `${fmt(total)} operations`}. Apply refuses this plan because its checksum does not match.${notRead}`;
  }
  const w = create + update;
  if (w > 0 && d === 0)
    return `Apply writes ${fmt(w)} ${w === 1 ? "operation" : "operations"}: ${writes.join(", ")}.${notRead}`;
  if (w > 0)
    return `Apply writes ${fmt(w)} of ${fmt(total)} operations: ${writes.join(", ")}. It does not delete the ${missing} not in the source.`;
  if (d > 0)
    return "Apply has nothing to write. The plan lists only nodes that are not in the source, and apply does not delete nodes.";
  return undefined;
}

/* ---------- view state ---------- */

function Fact({ term, children }: { term: string; children: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <dt className="text-subtle text-xs">{term}</dt>
      {children}
    </div>
  );
}

const Explain = ({ children }: { children: ReactNode }) => (
  <dd className="mt-0.5 text-pretty text-foreground-muted text-xs">{children}</dd>
);

/** The plan record: checksum, schema fingerprint, branch when it differs, lineage. */
export function PlanFacts({ plan, runBranch }: { plan: Plan; runBranch: string | null }) {
  const fingerprint = plan.schema_fingerprint;
  const lineage = plan.lineage;
  return (
    <dl className="grid grid-cols-1 gap-x-8 gap-y-4 text-[13px] sm:grid-cols-2">
      <Fact term="Plan checksum">
        <dd className="flex min-w-0 items-start gap-1.5">
          <span className="min-w-0 font-mono text-xs leading-5 [overflow-wrap:anywhere]">
            {plan.checksum}
          </span>
          <CopyButton value={plan.checksum} label="Copy checksum" />
        </dd>
        <dd className="mt-0.5 text-xs">
          {plan.checksum_ok ? (
            <span className="inline-flex items-start gap-1">
              <CheckIcon className="mt-px size-3.5 shrink-0 text-success" />
              <span className="text-pretty text-foreground-muted">
                Matches the saved plan. Apply runs only the plan with this checksum, and only on the
                branch the plan was created for.
              </span>
            </span>
          ) : (
            <span className="text-danger">Does not match the saved plan.</span>
          )}
        </dd>
      </Fact>
      {fingerprint && (
        <Fact term="Destination schema fingerprint">
          <dd className="flex min-w-0 items-center gap-1.5">
            <span className="font-mono text-xs">
              {fingerprint.slice(0, 12)}…{fingerprint.slice(-8)}
            </span>
            <CopyButton value={fingerprint} label="Copy schema fingerprint" />
          </dd>
          <Explain>
            Apply refuses this plan when the destination schema no longer has this fingerprint.
          </Explain>
        </Fact>
      )}
      {plan.destination_branch && plan.destination_branch !== runBranch && (
        <Fact term="Destination branch">
          <dd>
            <BranchCode branch={plan.destination_branch} />
          </dd>
          <Explain>
            The plan was created for this branch. Apply refuses this plan on any other branch.
          </Explain>
        </Fact>
      )}
      {lineage && (
        <>
          <Fact term="Import account">
            <dd>{lineage.import_account.name}</dd>
            <Explain>Apply refuses this plan when it runs with another account.</Explain>
          </Fact>
          <Fact term="Source of each value">
            <dd>
              {lineage.source.kind === "CoreAccount" &&
              lineage.source.name === lineage.import_account.name ? (
                "The import account"
              ) : (
                <>
                  {lineage.source.name}
                  <span className="text-subtle"> ({lineage.source.kind})</span>
                </>
              )}
            </dd>
          </Fact>
          <Fact term="Owner of created values">
            {lineage.owner ? (
              <>
                <dd>
                  {lineage.owner.name}
                  <span className="text-subtle"> ({lineage.owner.kind})</span>
                </dd>
                <Explain>The owner setting applies only to values this run creates.</Explain>
              </>
            ) : (
              <dd>No owner set</dd>
            )}
          </Fact>
          <Fact term="Protection">
            <dd className="text-pretty">
              {lineage.protect
                ? "On. The import marks each value it creates or changes as protected."
                : "Off. The import sends no protection flag, and values that are already protected stay protected."}
            </dd>
          </Fact>
        </>
      )}
      {!plan.summary.delete_operations_computed && (
        <Fact term="Nodes missing from the source">
          <dd className="text-pretty">
            Not listed. The destination was not read in full, so the plan lists no deletes.
          </dd>
        </Fact>
      )}
    </dl>
  );
}
