import { useInfiniteQuery } from "@tanstack/react-query";
import { useEffect } from "react";

import { type ScheduleFilters, schedulePagesQuery } from "#/api/queries";

/**
 * Every page of a schedule list, read one after the other. Reading stops at a page that fails;
 * `fetchNextPage` reads it again. With `enabled: false`, nothing is read.
 */
export function useAllSchedules(filters: ScheduleFilters, { enabled = true } = {}) {
  const pages = useInfiniteQuery({ ...schedulePagesQuery(filters), enabled });
  const { hasNextPage, isFetchingNextPage, isFetchNextPageError, fetchNextPage } = pages;
  useEffect(() => {
    if (enabled && hasNextPage && !isFetchingNextPage && !isFetchNextPageError)
      void fetchNextPage();
  }, [enabled, hasNextPage, isFetchingNextPage, isFetchNextPageError, fetchNextPage]);
  return pages;
}
