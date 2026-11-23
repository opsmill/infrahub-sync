/* PROTOTYPE (spec 027, T074). The three versions of the "Credentials used" section of the run details. */
import { Card } from "@infrahub/ui";
import { cn } from "tailwind-variants";

import type { CredentialUsed } from "#/api/types";
import { SectionHeading } from "#/components/page";

export interface UsedProps {
  used: CredentialUsed[];
}

const NAME = "font-mono text-[13px] [overflow-wrap:anywhere]";
const capitalize = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);

function Section({ count, children }: { count: number; children: React.ReactNode }) {
  return (
    <section aria-labelledby="credentials-used" className="flex flex-col gap-3">
      <SectionHeading id="credentials-used" title="Credentials used" count={count || undefined}>
        The stored credentials that each stage of this run read, with the revision it read.
      </SectionHeading>
      {count === 0 ? (
        <Card className="p-4 text-foreground-muted text-sm">
          No stage of this run read a stored credential.
        </Card>
      ) : (
        children
      )}
    </section>
  );
}

/* ---------- 1. Table: one row per read, in the order the Sync API returns them ---------- */

const TH = "h-9 px-4 text-left font-medium text-subtle text-xs";
const TD = "px-4 py-2.5 align-top";

export function UsedTable({ used }: UsedProps) {
  return (
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
  );
}

/* ---------- 2. By stage: one group per stage, as the timeline of the run orders them ---------- */

export function UsedByStage({ used }: UsedProps) {
  const stages = [...new Set(used.map((use) => use.stage))];
  return (
    <Section count={used.length}>
      <Card className="p-4">
        <dl className="grid grid-cols-1 gap-x-8 gap-y-4 text-[13px] sm:grid-cols-2 lg:grid-cols-3">
          {stages.map((stage) => (
            <div key={stage} className="flex min-w-0 flex-col gap-1">
              <dt className="text-subtle text-xs">{capitalize(stage)}</dt>
              {used
                .filter((use) => use.stage === stage)
                .map((use) => (
                  <dd key={use.block} className="flex min-w-0 flex-col">
                    <span className={NAME}>{use.name}</span>
                    <span className="text-subtle text-xs">
                      {capitalize(use.block)} ·{" "}
                      <span className="tabular-nums">revision {use.revision}</span>
                    </span>
                  </dd>
                ))}
            </div>
          ))}
        </dl>
      </Card>
    </Section>
  );
}

/* ---------- 3. By credential: one line per credential; says when two stages read different revisions ---------- */

export function UsedByCredential({ used }: UsedProps) {
  const keys = [...new Set(used.map((use) => `${use.block}\n${use.name}`))];
  return (
    <Section count={keys.length}>
      <Card className="bg-content bg-none">
        <ul className="divide-y text-sm">
          {keys.map((key) => {
            const [block, name] = key.split("\n");
            const reads = used.filter((use) => use.block === block && use.name === name);
            const revisions = [...new Set(reads.map((use) => use.revision))];
            return (
              <li key={key} className="flex flex-col gap-1 px-4 py-3">
                <p className="flex flex-wrap items-baseline gap-x-2">
                  <span className="text-subtle text-xs">{capitalize(block)}</span>
                  <span className={NAME}>{name}</span>
                </p>
                {revisions.length === 1 ? (
                  <p className="text-foreground-muted text-xs">
                    <span className="tabular-nums">Revision {revisions[0]}</span>, read by{" "}
                    {reads.map((use) => use.stage).join(", ")}
                  </p>
                ) : (
                  <>
                    <p className="text-foreground-muted text-xs tabular-nums">
                      {reads.map((use) => `${use.stage}: revision ${use.revision}`).join(" · ")}
                    </p>
                    <p className="text-warning text-xs">
                      The value changed during this run: the stages read different revisions.
                    </p>
                  </>
                )}
              </li>
            );
          })}
        </ul>
      </Card>
    </Section>
  );
}
