import type { ReactNode } from "react";

import type { RunState } from "#/domain/run-state";

const stroke = {
  fill: "none",
  stroke: "currentColor",
  strokeLinecap: "round",
  strokeLinejoin: "round",
  strokeWidth: 1.5,
} as const;

function Icon({ className = "size-4", children }: { className?: string; children: ReactNode }) {
  return (
    <svg viewBox="0 0 16 16" className={className} aria-hidden="true">
      {children}
    </svg>
  );
}

export const ArrowRightIcon = ({ className = "size-3.5 shrink-0 text-subtle-muted" }) => (
  <Icon className={className}>
    <path d="M3 8h9.5M9 4.5 12.5 8 9 11.5" {...stroke} />
  </Icon>
);

export const ChevronRightIcon = ({ className = "size-3.5" }) => (
  <Icon className={className}>
    <path d="M6 3.5 10.5 8 6 12.5" {...stroke} />
  </Icon>
);

export const PlusIcon = ({ className = "size-3.5" }) => (
  <Icon className={className}>
    <path d="M8 3.5v9M3.5 8h9" {...stroke} />
  </Icon>
);

export const CheckIcon = ({ className = "size-3.5" }) => (
  <Icon className={className}>
    <path d="M3.5 8.5l3 3 6-7" {...stroke} />
  </Icon>
);

export const CopyIcon = ({ className = "size-3.5" }) => (
  <Icon className={className}>
    <rect x="5.5" y="5.5" width="8" height="8" rx="1.5" {...stroke} />
    <path
      d="M10.5 3.5v-.25A1.25 1.25 0 0 0 9.25 2h-5A1.25 1.25 0 0 0 3 3.25v5A1.25 1.25 0 0 0 4.25 9.5h.25"
      {...stroke}
    />
  </Icon>
);

export const SearchIcon = ({ className = "size-4" }) => (
  <Icon className={className}>
    <circle cx="7" cy="7" r="4.5" {...stroke} />
    <path d="M10.5 10.5 13.5 13.5" {...stroke} />
  </Icon>
);

export const XIcon = ({ className = "size-3.5" }) => (
  <Icon className={className}>
    <path d="M4.5 4.5l7 7M11.5 4.5l-7 7" {...stroke} />
  </Icon>
);

export const DownloadIcon = ({ className = "size-3.5" }) => (
  <Icon className={className}>
    <path d="M8 2.75v7.5M4.75 7 8 10.25 11.25 7M3 13.25h10" {...stroke} />
  </Icon>
);

export const BranchIcon = ({ className = "size-3.5" }) => (
  <Icon className={className}>
    <circle cx="4.5" cy="3.75" r="1.5" {...stroke} />
    <circle cx="4.5" cy="12.25" r="1.5" {...stroke} />
    <circle cx="11.5" cy="5.25" r="1.5" {...stroke} />
    <path d="M4.5 5.25v5.5M11.5 6.75c0 2.5-3 2.25-6.25 4.25" {...stroke} />
  </Icon>
);

export const PlayIcon = ({ className = "size-3.5" }) => (
  <Icon className={className}>
    <path d="M5.5 3.75v8.5L12 8 5.5 3.75Z" {...stroke} />
  </Icon>
);

/** A caution a person reads before acting; not a run state. */
export const WarningIcon = ({ className = "size-4" }) => (
  <Icon className={className}>
    <path
      d="M7.13 2.5a1 1 0 0 1 1.74 0l5.2 9.25a1 1 0 0 1-.87 1.5H2.8a1 1 0 0 1-.87-1.5l5.2-9.25Z"
      {...stroke}
    />
    <path d="M8 6.25v2.75" {...stroke} />
    <circle cx="8" cy="11" r="0.75" fill="currentColor" />
  </Icon>
);

export const STATE_TEXT: Record<RunState, string> = {
  failed: "text-danger",
  review: "text-warning",
  running: "text-accent",
  idle: "text-subtle",
  insync: "text-success",
};

export function StateIcon({
  state,
  className = "size-3.5",
}: {
  state: RunState;
  className?: string;
}) {
  if (state === "running") {
    return (
      <svg
        viewBox="0 0 16 16"
        className={`${className} animate-spin motion-reduce:animate-none`}
        aria-hidden="true"
      >
        <circle
          cx="8"
          cy="8"
          r="6"
          fill="none"
          stroke="currentColor"
          strokeOpacity="0.25"
          strokeWidth="2"
        />
        <path d="M8 2a6 6 0 0 1 6 6" {...stroke} strokeWidth="2" />
      </svg>
    );
  }
  const marks: Record<Exclude<RunState, "running">, ReactNode> = {
    failed: <path d="M5.75 5.75l4.5 4.5M10.25 5.75l-4.5 4.5" {...stroke} />,
    review: <path d="M8 4.75V8.4l2.25 1.35" {...stroke} />,
    insync: <path d="M5 8.3l2 2 4-4.6" {...stroke} />,
    idle: <circle cx="8" cy="8" r="1.75" fill="currentColor" />,
  };
  return (
    <Icon className={className}>
      <circle
        cx="8"
        cy="8"
        r="6.4"
        {...stroke}
        strokeLinecap="butt"
        strokeDasharray={state === "idle" ? "2.2 2" : undefined}
      />
      {marks[state]}
    </Icon>
  );
}
