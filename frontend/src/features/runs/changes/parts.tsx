import type { ReactNode } from "react";
import { cn } from "tailwind-variants";

import { CHANGE_TONE } from "#/components/run-status";
import type { Action } from "#/features/runs/plan-values";

export function Sign({ action, className }: { action: Action; className?: string }) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        "grid size-4 shrink-0 place-items-center rounded-[5px] font-mono font-semibold text-[11px] leading-none",
        CHANGE_TONE[action].chip,
        className
      )}
    >
      {CHANGE_TONE[action].sign}
    </span>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="rounded-md border border-border-strong bg-content px-1 font-mono text-[11px] text-subtle">
      {children}
    </kbd>
  );
}

export const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;
