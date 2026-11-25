import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { cn } from "tailwind-variants";

import { type CredentialFinding, credentialsQuery, describeError, meQuery } from "#/api/queries";
import { FOCUS } from "#/components/classes";
import { CheckIcon } from "#/components/icons";
import { CODE_CHIP, Notice } from "#/components/page";

import {
  credentialRefusalTitle,
  type RefusedCredential,
  refusedBlockLabel,
  refusedCredentialResolved,
  refusedCredentialText,
  refusedForMissingCredential,
} from "./package-review";

const ACTION = cn(
  "inline-flex min-h-8 pointer-coarse:min-h-11 items-center gap-1 rounded-sm font-medium text-foreground underline decoration-border-strong underline-offset-4 transition-colors duration-150 ease-out hover:decoration-foreground motion-reduce:transition-none",
  FOCUS
);

/**
 * What the signed-in account can do about one finding. An administrator opens the Credentials page
 * in a new tab, with the form for this credential open, so the package stays open in this tab.
 */
function Remedy({
  finding,
  administrator,
  resolved,
}: {
  finding: RefusedCredential;
  administrator: boolean;
  resolved: boolean;
}) {
  const missing = refusedForMissingCredential(finding);
  if (resolved) {
    return (
      <span className="inline-flex min-h-8 items-center gap-1 text-foreground-muted">
        <CheckIcon className="size-3.5" />
        {missing ? "Exists now" : "Holds the field now"}
      </span>
    );
  }
  if (!administrator) {
    return (
      <span className="text-foreground-muted">
        Ask an administrator to {missing ? "create" : "change the value of"} the credential{" "}
        <code className={CODE_CHIP}>{finding.name}</code>.
      </span>
    );
  }
  return (
    <Link
      to="/credentials"
      search={missing ? { new: finding.name } : { value: finding.name }}
      target="_blank"
      rel="noreferrer"
      className={ACTION}
    >
      <span className="[overflow-wrap:anywhere]">
        {missing ? "Create credential" : "Change the value of"} {finding.name}
      </span>
      <span aria-hidden="true">↗</span>
      <span className="sr-only"> (opens the Credentials page in a new tab)</span>
    </Link>
  );
}

/**
 * The remedy of one finding of the pre-registration check about a stored credential. The check
 * disables Register, so the review offers "Validate again" next to these findings.
 */
export function CheckedCredentialRemedy({ finding }: { finding: RefusedCredential }) {
  const administrator = useQuery(meQuery()).data?.administrator === true;
  const stored = useQuery(credentialsQuery()).data;
  return (
    <Remedy
      finding={finding}
      administrator={administrator}
      resolved={refusedCredentialResolved(finding, stored)}
    />
  );
}

/**
 * A registration that the Sync API refused because a stored credential that the package names does
 * not exist or lacks a field. The notice lists each block with the reason and what the signed-in
 * account can do. It reads the stored credentials again while it is open, so a credential that an
 * administrator creates or changes in another tab is marked here.
 */
export function CredentialRefusal({
  error,
  findings,
}: {
  error: unknown;
  findings: CredentialFinding[];
}) {
  const administrator = useQuery(meQuery()).data?.administrator === true;
  const stored = useQuery(credentialsQuery()).data;
  const open = findings.filter((f) => !refusedCredentialResolved(f, stored)).length;
  const codeLine = describeError(error).codeLine;
  return (
    <Notice tone="danger" role="alert" title={credentialRefusalTitle(findings.length)}>
      <ul className="flex flex-col divide-y">
        {findings.map((finding) => (
          <li
            key={`${finding.block}:${finding.name}:${finding.code}`}
            className="flex flex-wrap items-center justify-between gap-x-4 gap-y-0.5 py-1.5"
          >
            <span className="min-w-0 text-pretty">
              <span className="text-foreground">{refusedBlockLabel(finding.block)}</span>:{" "}
              <code className={CODE_CHIP}>{finding.name}</code> {refusedCredentialText(finding)}
            </span>
            <Remedy
              finding={finding}
              administrator={administrator}
              resolved={refusedCredentialResolved(finding, stored)}
            />
          </li>
        ))}
      </ul>
      <p className="text-pretty">
        {open === 0
          ? "Every credential can be used now. Press Register again."
          : "The package stays open here. Press Register again when each credential can be used."}
      </p>
      {codeLine && <p className="font-mono text-subtle text-xs">{codeLine}</p>}
    </Notice>
  );
}
