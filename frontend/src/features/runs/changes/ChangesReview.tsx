import { useCallback, useMemo, useRef, useState } from "react";

import type { Plan } from "#/api/types";

import { ChangesList } from "./ChangesList";
import { DetailsPanel } from "./DetailsPanel";
import { type ChangesFilter, type OperationView, type RowContext, remember } from "./views";

export type { ChangesFilter } from "./views";

export interface ChangesReviewProps {
  /** The plan's operations, from `prepareViews(plan)`. */
  views: OperationView[];
  plan: Plan;
  /** True on an applied run: the copy is written in the past tense. */
  done: boolean;
  filter: ChangesFilter;
  onFilterChange: (filter: ChangesFilter) => void;
  /** The id of the element the details panel moves left to make room for itself. */
  shiftTargetId: string;
}

/**
 * The review of a plan's operations: the search and filters, the list filling its parent's height,
 * and the details of the selected operation in a panel on the right.
 */
export function ChangesReview({
  views,
  plan,
  done,
  filter,
  onFilterChange,
  shiftTargetId,
}: ChangesReviewProps) {
  const cardRef = useRef<HTMLDivElement>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [rows, setRows] = useState<OperationView[]>([]);
  const byId = useMemo(
    () => remember(views, "byId", () => new Map(views.map((v) => [v.op.operation_id, v]))),
    [views]
  );
  const ctx = useMemo<RowContext>(
    () => ({
      done,
      recorded: plan.last_changes != null,
      importAccountId: plan.lineage?.import_account.id,
    }),
    [done, plan]
  );
  const select = useCallback((id: string | null) => setSelectedId(id), []);

  return (
    <>
      <ChangesList
        views={views}
        filter={filter}
        onFilterChange={onFilterChange}
        selectedId={selectedId}
        onSelect={select}
        onRows={setRows}
        fill
        cardRef={cardRef}
      />
      <DetailsPanel
        view={selectedId ? byId.get(selectedId) : undefined}
        rows={rows}
        onSelect={select}
        ctx={ctx}
        alignRef={cardRef}
        shiftTargetId={shiftTargetId}
      />
    </>
  );
}
