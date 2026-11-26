import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useEffect, useRef } from "react";
import { cn } from "tailwind-variants";

import { type CredentialFinding, credentialsQuery, describeError, meQuery } from "#/api/queries";
import type { DeclaredContent } from "#/api/types";
import { FOCUS } from "#/components/classes";
import { CheckIcon } from "#/components/icons";
import { CODE_CHIP, Notice } from "#/components/page";

import {
  checkedCredential,
  checkedCredentialState,
  credentialRefusalNextStep,
  credentialRefusalTitle,
  type RefusedCredential,
  refusedBlockLabel,
  refusedCredentialHasRemedy,
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
  // No stored credential answers an auth_method that the adapter does not accept.
  if (!refusedCredentialHasRemedy(finding)) return null;
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
      <p className="text-pretty">{credentialRefusalNextStep(findings, stored)}</p>
      {codeLine && <p className="font-mono text-subtle text-xs">{codeLine}</p>}
    </Notice>
  );
}

/**
 * Runs the check again when a stored credential that one of its findings is about changes.
 *
 * A finding about a stored credential goes out of date when an administrator creates the
 * credential or gives it a new value, in another tab or from the CLI. While such a finding is
 * shown, the stored credentials are read again at the interval of the Credentials page and when
 * the tab gets the focus back. The state at the time of the check is kept; when the revision of a
 * named credential differs from it, or the credential appears or disappears, `onRecheck` is
 * called once. "Validate again" stays for a change that this does not detect.
 */
export function useRecheckOnCredentialChange({
  findings,
  content,
  paused,
  onRecheck,
}: {
  /** The findings of the latest check; a new array for each check. */
  findings: readonly { code: string; location?: string | null }[] | undefined;
  content: DeclaredContent | undefined;
  /** A check is running, or the registration is sent. */
  paused: boolean;
  onRecheck: () => void;
}) {
  const aboutCredentials = (findings ?? []).some((f) => checkedCredential(f, content));
  const stored = useQuery({ ...credentialsQuery(), enabled: aboutCredentials }).data;
  const state = checkedCredentialState(findings ?? [], content, stored);
  const baseline = useRef<{ findings: unknown; state: string }>(undefined);
  const recheck = useRef(onRecheck);
  recheck.current = onRecheck;
  useEffect(() => {
    if (state === undefined || paused) return;
    const previous = baseline.current;
    baseline.current = { findings, state };
    // The same check, and a credential of one of its findings changed since.
    if (previous && previous.findings === findings && previous.state !== state) recheck.current();
  }, [state, findings, paused]);
}
