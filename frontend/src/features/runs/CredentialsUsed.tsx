import { Card } from "@infrahub/ui";
import { Link } from "@tanstack/react-router";
import { cn } from "tailwind-variants";

import type { CredentialUsed } from "#/api/types";
import { SectionHeading } from "#/components/page";

import { LINK } from "./form";

const TH = "h-9 px-4 text-left font-medium text-subtle text-xs";
const TD = "px-4 py-2.5 align-top";

const capitalize = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);

/** One row of the grid: a block and the stored credential it named. */
export interface CredentialLine {
  block: string;
  name: string;
  /** The revision the first stage read. */
  first: number;
  /** The revisions each stage read, in the order of the run. A stage that ran twice has two. */
  byStage: Map<string, number[]>;
}

/**
 * The reads of a run as a grid: the stages in the order they first read a credential, and one line
 * per block and credential, in the order of their first read. The Sync API lists one entry for each
 * stage execution and block, oldest first, so a stage that ran twice, such as a second resume,
 * reads twice.
 */
export function credentialGrid(used: CredentialUsed[]): {
  stages: string[];
  lines: CredentialLine[];
} {
  const stages: string[] = [];
  const lines: CredentialLine[] = [];
  for (const use of used) {
    if (!stages.includes(use.stage)) stages.push(use.stage);
    let line = lines.find((l) => l.block === use.block && l.name === use.name);
    if (!line) {
      line = { block: use.block, name: use.name, first: use.revision, byStage: new Map() };
      lines.push(line);
    }
    line.byStage.set(use.stage, [...(line.byStage.get(use.stage) ?? []), use.revision]);
  }
  return { stages, lines };
}

function Revision({ revision, changed }: { revision: number; changed: boolean }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5",
        changed && "font-medium text-amber-700 dark:text-amber-400"
      )}
    >
      {changed && <span aria-hidden="true" className="size-1.5 rounded-full bg-current" />}
      {revision}
      {changed && <span className="sr-only"> (changed)</span>}
    </span>
  );
}

/**
 * "Credentials used": the name and the revision of the stored credential that each stage of the
 * run read for each block, never a value. A revision that differs from the one the first stage read
 * is marked: the value changed during the run.
 */
export function CredentialsUsed({ used }: { used: CredentialUsed[] }) {
  const { stages, lines } = credentialGrid(used);
  const changed = lines.some((line) =>
    [...line.byStage.values()].some((revisions) => revisions.some((r) => r !== line.first))
  );
  return (
    <section aria-labelledby="credentials-used" className="flex flex-col gap-3">
      <SectionHeading
        id="credentials-used"
        title="Credentials used"
        count={used.length || undefined}
      >
        The stored credentials that each stage of this run read, with the revision it read.
      </SectionHeading>
      {used.length === 0 ? (
        <Card className="p-4 text-foreground-muted text-sm">
          No stage of this run read a stored credential.
        </Card>
      ) : (
        <>
          <Card className="overflow-x-auto bg-content bg-none">
            <table aria-labelledby="credentials-used" className="w-full text-sm">
              <thead className="bg-content-muted">
                <tr>
                  <th scope="col" className={TH}>
                    Block and credential
                  </th>
                  {stages.map((stage) => (
                    <th key={stage} scope="col" className={cn(TH, "w-24 text-right")}>
                      {capitalize(stage)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y">
                {lines.map((line) => (
                  <tr key={`${line.block}-${line.name}`}>
                    <th scope="row" className={cn(TD, "text-left font-normal")}>
                      <span className="block text-subtle text-xs">{capitalize(line.block)}</span>
                      <Link
                        to="/credentials"
                        className={cn(LINK, "font-mono text-[13px] [overflow-wrap:anywhere]")}
                      >
                        {line.name}
                      </Link>
                    </th>
                    {stages.map((stage) => {
                      const revisions = line.byStage.get(stage);
                      return (
                        <td key={stage} className={cn(TD, "text-right tabular-nums")}>
                          {revisions ? (
                            <span className="inline-flex flex-wrap justify-end gap-x-2">
                              {revisions.map((revision, index) => (
                                <Revision
                                  // A stage that ran twice can read the same revision twice.
                                  key={index}
                                  revision={revision}
                                  changed={revision !== line.first}
                                />
                              ))}
                            </span>
                          ) : (
                            <span className="text-subtle">
                              <span aria-hidden="true">–</span>
                              <span className="sr-only">not read</span>
                            </span>
                          )}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
          <p className="text-pretty text-subtle text-xs">
            Each cell is the revision that the stage read; a stage that ran more than once shows
            each read. A dash: the stage did not read this credential.
            {changed &&
              " A marked revision differs from the one the first stage read: the value changed during the run."}
          </p>
        </>
      )}
    </section>
  );
}
