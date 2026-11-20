import { cn } from "tailwind-variants";

/** The Infrahub Sync mark, shown in the page header and on the sign-in page. */
export function Mark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 20 20" className={cn("size-5 shrink-0", className)} aria-hidden="true">
      <rect
        x="1"
        y="1"
        width="18"
        height="18"
        rx="5"
        className="fill-cyan-900 dark:fill-cyan-700"
      />
      <circle cx="6" cy="10" r="1.75" className="fill-white" />
      <circle cx="14" cy="10" r="1.75" className="fill-white" />
      <path d="M7.75 10h4.5" stroke="white" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}
