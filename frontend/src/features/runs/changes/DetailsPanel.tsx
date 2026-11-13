import { type RefObject, useCallback } from "react";
import { cn } from "tailwind-variants";

import { ChevronRightIcon, XIcon } from "#/components/icons";
import { FOCUS_INSET } from "#/components/page";
import { CHANGE_TONE } from "#/components/run-status";
import { PANEL_ICON_BUTTON, ShiftPanel } from "#/components/shift-panel";
import { fmt } from "#/domain/run-state";
import { type Action, levelLabel } from "#/features/runs/plan-values";

import { LedgerDiff } from "./LedgerDiff";
import { Kbd, Sign } from "./parts";
import type { OperationView, RowContext } from "./views";

const ACTION_WORD: Record<Action, string> = {
  create: "Create",
  update: "Update",
  delete: "Not deleted",
};

export interface DetailsPanelProps {
  /** The selected operation; undefined closes the panel. */
  view: OperationView | undefined;
  /** The list's rows in view order, for the position and the previous and next buttons. */
  rows: OperationView[];
  onSelect: (id: string | null) => void;
  ctx: RowContext;
  /** The list's card: the panel lines up with its top and bottom. */
  alignRef: RefObject<HTMLElement | null>;
  /** The id of the element the panel moves left to make room for itself. */
  shiftTargetId: string;
}

/**
 * The details of the selected operation, in a panel on the right. The page moves left to make room
 * instead of being covered; on narrow windows the panel covers it. Esc closes the panel.
 */
export function DetailsPanel({
  view,
  rows,
  onSelect,
  ctx,
  alignRef,
  shiftTargetId,
}: DetailsPanelProps) {
  const at = view ? rows.findIndex((v) => v.op.operation_id === view.op.operation_id) : -1;
  const go = (i: number) => rows[i] && onSelect(rows[i].op.operation_id);
  const close = useCallback(() => onSelect(null), [onSelect]);

  return (
    <ShiftPanel
      open={view != null}
      label="Operation details"
      onClose={close}
      shiftTargetId={shiftTargetId}
      widthKey="changes-panel-width"
      alignRef={alignRef}
    >
      {view && <Details view={view} at={at} total={rows.length} go={go} close={close} ctx={ctx} />}
    </ShiftPanel>
  );
}

function Details({
  view: current,
  at,
  total,
  go,
  close,
  ctx,
}: {
  view: OperationView;
  at: number;
  total: number;
  go: (i: number) => void;
  close: () => void;
  ctx: RowContext;
}) {
  return (
    <>
      <div className="flex flex-col gap-1 border-b px-4 pt-2 pb-3">
        <div className="flex items-center gap-2">
          <Sign action={current.op.action} />
          <span className={cn("font-medium text-xs", CHANGE_TONE[current.op.action].text)}>
            {ACTION_WORD[current.op.action]}
          </span>
          <span className="min-w-0 truncate font-mono text-subtle text-xs">{current.op.kind}</span>
          <span className="ml-auto flex shrink-0 items-center">
            {at >= 0 && (
              <>
                <span className="mr-1 text-subtle text-xs tabular-nums">
                  {fmt(at + 1)} of {fmt(total)}
                </span>
                <button
                  type="button"
                  aria-label="Previous operation"
                  disabled={at <= 0}
                  onClick={() => go(at - 1)}
                  className={cn(PANEL_ICON_BUTTON, FOCUS_INSET)}
                >
                  <ChevronRightIcon className="size-3.5 -rotate-90" />
                </button>
                <button
                  type="button"
                  aria-label="Next operation"
                  disabled={at >= total - 1}
                  onClick={() => go(at + 1)}
                  className={cn(PANEL_ICON_BUTTON, FOCUS_INSET)}
                >
                  <ChevronRightIcon className="size-3.5 rotate-90" />
                </button>
              </>
            )}
            <button
              type="button"
              aria-label="Close the details"
              onClick={close}
              className={cn(PANEL_ICON_BUTTON, FOCUS_INSET)}
            >
              <XIcon />
            </button>
          </span>
        </div>
        <h2 className="font-semibold text-base tracking-tight [overflow-wrap:anywhere]">
          {current.name}
        </h2>
        {current.levels.length > 0 && (
          <p className="text-foreground-muted text-xs [overflow-wrap:anywhere]">
            in {current.levels.map(levelLabel).join(" › ")}
          </p>
        )}
      </div>
      <div className="min-h-0 flex-1 overflow-auto overscroll-contain px-4 py-3">
        <LedgerDiff view={current} ctx={ctx} />
      </div>
      <p className="hidden items-center gap-1 border-t bg-content-muted px-4 py-2 text-subtle text-xs sm:flex">
        <Kbd>↑</Kbd>
        <Kbd>↓</Kbd> in the list move this panel, <Kbd>Esc</Kbd> closes it
      </p>
    </>
  );
}
