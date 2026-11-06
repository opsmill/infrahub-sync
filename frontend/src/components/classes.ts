/** Keyboard focus for a control with room around it. Outlines survive forced-colours mode. */
export const FOCUS =
  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring";

/** Keyboard focus drawn inside the control, for controls in a clipped or scrolling container. */
export const FOCUS_INSET =
  "focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring";

/**
 * `FOCUS` for an @infrahub/ui Button, which marks keyboard focus with `data-focus-visible` and
 * draws a pale halo by default.
 */
export const FOCUS_RAC =
  "data-focus-visible:border-transparent data-focus-visible:outline-2 data-focus-visible:outline-offset-2 data-focus-visible:outline-ring data-focus-visible:outline-solid data-focus-visible:ring-0";

/** A breadcrumb link, 32 px high (44 px on a touch screen). */
export const CRUMB = `-mx-1 inline-flex min-h-8 items-center rounded-md px-1 transition-colors duration-150 ease-out hover:text-foreground pointer-coarse:min-h-11 motion-reduce:transition-none ${FOCUS}`;

export const COUNT_BADGE =
  "inline-flex h-5 min-w-5 items-center justify-center rounded-full border border-border bg-content px-1.5 font-medium text-[11px] text-foreground-muted tabular-nums inset-shadow-raised";

/**
 * Moves to the next line as a whole rather than breaking at a hyphen, and wraps inside only when it
 * is wider than its container, so a long ID or command stays inside a phone screen.
 */
export const CODE_CHIP =
  "inline-block max-w-full rounded-md border border-border bg-content px-1 py-px font-mono text-[0.85em] text-foreground-muted [overflow-wrap:break-word]";

/**
 * The facts under a detail page's title, one `li` each. Separators sit in the gap before each item.
 * The list is clipped one pixel row to the left of its items, so the separator of an item that
 * starts a wrapped line is hidden.
 */
export const META_LIST =
  "-ml-1 flex flex-wrap items-center gap-x-5 gap-y-1 overflow-x-clip pl-1 text-[13px] text-foreground-muted [&>li]:relative [&>li]:before:absolute [&>li]:before:top-1/2 [&>li]:before:-left-[10.5px] [&>li]:before:h-3 [&>li]:before:w-px [&>li]:before:-translate-y-1/2 [&>li]:before:bg-border-strong [&>li]:before:content-['']";
