/* PROTOTYPE (spec 027, T074). The three versions of the "Credentials used" part of the run details. */
import { Card } from "@infrahub/ui";
import type { ReactNode } from "react";
import { cn } from "tailwind-variants";

import type { CredentialUsed } from "#/api/types";
import { SectionHeading } from "#/components/page";

/** Each version renders the "Details" section too, because version 2 places the credentials inside it. */
export interface UsedProps {
  used: CredentialUsed[];
  /** The facts of the "Details" section of the sample run. */
  facts: [string, string][];
}

const NAME = "font-mono text-[13px] [overflow-wrap:anywhere]";
const capitalize = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);
const NONE = "No stage of this run read a stored credential.";

function FactList({ facts }: { facts: [string, string][] }) {
  return (
    <dl className="grid grid-cols-1 gap-x-8 gap-y-3 text-[13px] sm:grid-cols-2 lg:grid-cols-3">
      {facts.map(([term, value]) => (
        <div key={term} className="flex min-w-0 flex-col gap-0.5">
          <dt className="text-subtle text-xs">{term}</dt>
          <dd className="min-w-0 [overflow-wrap:anywhere]">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function Details({ facts, children }: { facts: [string, string][]; children?: ReactNode }) {
  return (
    <section aria-labelledby="details" className="flex flex-col gap-3">
      <SectionHeading id="details" title="Details" />
      <Card className="flex flex-col gap-4 p-4">
        <FactList facts={facts} />
        {children}
      </Card>
    </section>
  );
}

function Section({ count, children }: { count: number; children: ReactNode }) {
  return (
    <section aria-labelledby="credentials-used" className="flex flex-col gap-3">
      <SectionHeading id="credentials-used" title="Credentials used" count={count || undefined}>
        The stored credentials that each stage of this run read, with the revision it read.
      </SectionHeading>
      {count === 0 ? <Card className="p-4 text-foreground-muted text-sm">{NONE}</Card> : children}
    </section>
  );
}

/* ---------- 1. Table: one row per read, the four columns of the contract. Kept from the first round. ---------- */

const TH = "h-9 px-4 text-left font-medium text-subtle text-xs";
const TD = "px-4 py-2.5 align-top";

export function UsedTable({ used, facts }: UsedProps) {
  return (
    <>
      <Details facts={facts} />
      <Section count={used.length}>
        <Card className="overflow-x-auto bg-content bg-none">
          <table aria-labelledby="credentials-used" className="w-full text-sm">
            <thead className="bg-content-muted">
              <tr>
                <th scope="col" className={TH}>
                  Stage
                </th>
                <th scope="col" className={TH}>
                  Block
                </th>
                <th scope="col" className={TH}>
                  Name
                </th>
                <th scope="col" className={cn(TH, "text-right")}>
                  Revision
                </th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {used.map((use) => (
                <tr key={`${use.stage}-${use.block}`}>
                  <td className={TD}>{capitalize(use.stage)}</td>
                  <td className={TD}>{capitalize(use.block)}</td>
                  <td className={cn(TD, NAME)}>{use.name}</td>
                  <td className={cn(TD, "text-right tabular-nums")}>{use.revision}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      </Section>
    </>
  );
}

/* ---------- 2. In the details: one line per block, next to the source and the destination ---------- */

interface Line {
  block: string;
  name: string;
  /** Each revision read, with the stages that read it, in the order of the run. */
  reads: { revision: number; stages: string[] }[];
}

function linesOf(used: CredentialUsed[]): Line[] {
  const lines: Line[] = [];
  for (const use of used) {
    let line = lines.find((l) => l.block === use.block && l.name === use.name);
    if (!line) {
      line = { block: use.block, name: use.name, reads: [] };
      lines.push(line);
    }
    const read = line.reads.find((r) => r.revision === use.revision);
    if (read) read.stages.push(use.stage);
    else line.reads.push({ revision: use.revision, stages: [use.stage] });
  }
  return lines;
}

export function UsedInDetails({ used, facts }: UsedProps) {
  const lines = linesOf(used);
  return (
    <Details facts={facts}>
      <div className="flex flex-col gap-2 border-t pt-4">
        <h3 className="font-medium text-subtle text-xs">Credentials used</h3>
        {lines.length === 0 ? (
          <p className="text-[13px] text-foreground-muted">{NONE}</p>
        ) : (
          <dl className="grid grid-cols-1 gap-x-6 gap-y-2.5 text-[13px] sm:grid-cols-[8rem_minmax(0,1fr)]">
            {lines.map((line) => (
              <div key={`${line.block}-${line.name}`} className="contents">
                <dt className="text-subtle max-sm:text-xs">{capitalize(line.block)}</dt>
                <dd className="flex min-w-0 flex-col gap-0.5">
                  <span className={NAME}>{line.name}</span>
                  <span className="text-foreground-muted">
                    {line.reads.map((read, index) => (
                      <span key={read.revision}>
                        {index > 0 && "; "}
                        <span className="tabular-nums">revision {read.revision}</span>, read by{" "}
                        {read.stages.join(", ")}
                      </span>
                    ))}
                  </span>
                  {line.reads.length > 1 && (
                    <span className="text-amber-700 dark:text-amber-400">
                      The value changed during the run.
                    </span>
                  )}
                </dd>
              </div>
            ))}
          </dl>
        )}
      </div>
    </Details>
  );
}

/* ---------- 3. Stage grid: one row per block and credential, one column per stage, the revision in each cell ---------- */

export function UsedGrid({ used, facts }: UsedProps) {
  const stages = [...new Set(used.map((use) => use.stage))];
  const lines = linesOf(used);
  const changed = lines.some((line) => line.reads.length > 1);
  return (
    <>
      <Details facts={facts} />
      <Section count={used.length}>
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
              {lines.map((line) => {
                const first = line.reads[0]?.revision;
                return (
                  <tr key={`${line.block}-${line.name}`}>
                    <th scope="row" className={cn(TD, "text-left font-normal")}>
                      <span className="block text-subtle text-xs">{capitalize(line.block)}</span>
                      <span className={NAME}>{line.name}</span>
                    </th>
                    {stages.map((stage) => {
                      const read = used.find(
                        (u) => u.stage === stage && u.block === line.block && u.name === line.name
                      );
                      const differs = read && read.revision !== first;
                      return (
                        <td key={stage} className={cn(TD, "text-right tabular-nums")}>
                          {read ? (
                            <span
                              className={cn(
                                "inline-flex items-center gap-1.5",
                                differs && "font-medium text-amber-700 dark:text-amber-400"
                              )}
                            >
                              {differs && (
                                <span
                                  aria-hidden="true"
                                  className="size-1.5 rounded-full bg-current"
                                />
                              )}
                              {read.revision}
                              {differs && <span className="sr-only"> (changed)</span>}
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
                );
              })}
            </tbody>
          </table>
        </Card>
        <p className="text-pretty text-subtle text-xs">
          Each cell is the revision that the stage read. A dash: the stage did not read this
          credential.
          {changed &&
            " A marked revision differs from the one the first stage read: the value changed during the run."}
        </p>
      </Section>
    </>
  );
}
