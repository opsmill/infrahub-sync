import { Card } from "@infrahub/ui";
import { Link } from "@tanstack/react-router";
import { cn } from "tailwind-variants";

import { ArrowRightIcon } from "#/components/icons";
import { FOCUS } from "#/components/page";
import { BranchBadge } from "#/components/run-status";
import { plural } from "#/domain/run-state";
import type { System } from "#/domain/systems";

/** The id of the mapping section, which the count of kinds links to. */
export const MAPPING_SECTION_ID = "mapping";

const MONO = "font-mono text-xs [overflow-wrap:anywhere]";

/** The characters kept on one line with the "opens in a new tab" arrow. */
const TAIL = 4;

/** A long address wraps after a dot, a slash, a colon or a hyphen before it wraps inside a word. */
function Breakable({ text }: { text: string }) {
  const parts = text.split(/(?<=[./:-])(?=[^./:-])/);
  let offset = 0;
  return parts.map((part) => {
    offset += part.length;
    return (
      <span key={offset}>
        {part}
        <wbr />
      </span>
    );
  });
}

/** The address when it is an http or https URL, so that it can be opened. */
function openable(address: string | undefined): string | undefined {
  if (!address) return undefined;
  try {
    const url = new URL(address);
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : undefined;
  } catch {
    return undefined;
  }
}

/** The declared address in full. It opens the system in a new tab when it is a web address. */
function Address({ system, adapter }: { system: System; adapter: string }) {
  const href = openable(system.address);
  if (!system.address || !href) {
    return (
      <span className={cn(MONO, "text-foreground-muted")}>
        <Breakable text={system.address ?? adapter} />
      </span>
    );
  }
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className={cn(
        MONO,
        "rounded-sm py-1 text-foreground-muted underline decoration-transparent underline-offset-4 transition-colors duration-150 ease-out hover:text-foreground hover:decoration-subtle motion-reduce:transition-none",
        FOCUS
      )}
    >
      <Breakable text={system.address.slice(0, -TAIL)} />
      <span className="whitespace-nowrap">
        {system.address.slice(-TAIL)}
        <svg viewBox="0 0 16 16" aria-hidden="true" className="ml-1 inline size-3 align-[-1.5px]">
          <path
            d="M6 4h6v6M12 4 4.5 11.5"
            fill="none"
            stroke="currentColor"
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={1.5}
          />
        </svg>
      </span>
      <span className="sr-only"> (opens {system.label} in a new tab)</span>
    </a>
  );
}

/** One system. The source's text is right-aligned, so that both names sit next to the line. */
function SystemCard({
  side,
  system,
  adapter,
}: {
  side: "Source" | "Destination";
  system: System;
  adapter: string;
}) {
  const source = side === "Source";
  return (
    <Card className="relative min-w-0">
      <div
        className={cn(
          "flex h-full flex-col gap-0.5 px-4 py-3.5",
          source && "@2xl:items-end @2xl:text-right"
        )}
      >
        <p className="text-subtle text-xs">{side}</p>
        <p className="font-semibold text-base tracking-tight">{system.label}</p>
        <p>
          <Address system={system} adapter={adapter} />
        </p>
        {system.branch && (
          <p className="mt-1.5 text-foreground-muted text-xs">
            <BranchBadge branch={system.branch} label={`${side} branch`} />
          </p>
        )}
      </div>
      {source && (
        <span
          aria-hidden="true"
          className="absolute top-1/2 right-0 hidden size-2.5 translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-foreground-muted bg-content @2xl:block"
        />
      )}
    </Card>
  );
}

/**
 * What a version of a configuration does, at a glance: the source and the destination, joined by a
 * line of dashes that move towards the destination and carries the count of kinds.
 */
export function Systems({
  configId,
  source,
  destination,
  adapters,
  kinds,
}: {
  configId: string;
  source: System;
  destination: System;
  /** The adapter names, shown when a system declares no address. */
  adapters: { source: string; destination: string };
  kinds: number;
}) {
  const count = (
    <Link
      to="/configs/$configId"
      params={{ configId }}
      search={(s) => s}
      hash={MAPPING_SECTION_ID}
      replace
      className={cn(
        "relative inline-flex h-7 shrink-0 items-center rounded-full border bg-content px-3 font-medium text-foreground-muted text-xs tabular-nums transition-colors duration-150 ease-out hover:bg-content-muted motion-reduce:transition-none",
        FOCUS
      )}
    >
      {plural(kinds, "kind", "kinds")}
    </Link>
  );
  return (
    <section aria-label="Source and destination" className="@container">
      <div className="grid grid-cols-1 items-stretch @2xl:grid-cols-[minmax(0,1fr)_11rem_minmax(0,1fr)]">
        <SystemCard side="Source" system={source} adapter={adapters.source} />
        <span className="relative hidden items-center justify-center text-foreground-muted @2xl:flex">
          <span
            aria-hidden="true"
            className="absolute top-1/2 right-2 left-1.5 h-px -translate-y-1/2 overflow-hidden"
          >
            <span className="flow-dashes block h-px w-[calc(100%+12px)]" />
          </span>
          <ArrowRightIcon className="absolute right-0 size-3.5 translate-x-0.5" />
          {count}
        </span>
        <span className="relative flex h-12 items-center justify-center @2xl:hidden">
          <span aria-hidden="true" className="absolute inset-y-0 left-1/2 w-px bg-border-strong" />
          {count}
          <ArrowRightIcon className="absolute bottom-0 left-1/2 size-3.5 -translate-x-1/2 translate-y-0.5 rotate-90 text-subtle" />
        </span>
        <SystemCard side="Destination" system={destination} adapter={adapters.destination} />
      </div>
    </section>
  );
}
