import { Button } from "@infrahub/ui";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { fetchClient } from "#/api/client";
import { configsQuery } from "#/api/queries";
import type { Run } from "#/api/types";
import { Bar, EmptyPanel, ErrorPanel, SearchField } from "#/components/page";
import { phaseLabel, type RunState, runState } from "#/domain/run-state";

import { RunsTable } from "./RunsTable";

const PAGE = 50;

type Filter = "all" | "attention" | "running" | "applied";
const FILTERS: { id: Filter; label: string; test: (r: Run) => boolean }[] = [
  { id: "all", label: "All", test: () => true },
  {
    id: "attention",
    label: "Failed or waiting",
    test: (r) => (["failed", "review"] as RunState[]).includes(runState(r)),
  },
  { id: "running", label: "Running", test: (r) => runState(r) === "running" },
  { id: "applied", label: "Applied", test: (r) => r.phase === "applied" },
];

export function RunsPage() {
  const runs = useInfiniteQuery({
    queryKey: ["get", "/runs", "pages"],
    initialPageParam: null as string | null,
    queryFn: async ({ pageParam, signal }) => {
      const { data, error } = await fetchClient.GET("/runs", {
        params: { query: { limit: String(PAGE), ...(pageParam ? { cursor: pageParam } : {}) } },
        signal,
      });
      if (error) throw error;
      return data;
    },
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    refetchInterval: 10_000,
  });
  const configs = useQuery(configsQuery());
  const [filter, setFilter] = useState<Filter>("all");
  const [query, setQuery] = useState("");

  const all = useMemo(() => runs.data?.pages.flatMap((p) => p.items) ?? [], [runs.data]);
  const names = useMemo(
    () => new Map((configs.data?.items ?? []).map((c) => [c.config_id, c.name ?? c.config_id])),
    [configs.data]
  );
  const latestVersions = useMemo(
    () => new Map((configs.data?.items ?? []).map((c) => [c.config_id, c.latest_version ?? 1])),
    [configs.data]
  );
  const shown = useMemo(() => {
    const test = FILTERS.find((f) => f.id === filter)?.test ?? (() => true);
    const needle = query.trim().toLowerCase();
    return all.filter(
      (r) =>
        test(r) &&
        (!needle ||
          [
            r.run_id,
            r.config_id ? names.get(r.config_id) : r.configuration_reference,
            r.destination_branch,
            r.actor,
            phaseLabel(r),
          ].some((v) => v?.toLowerCase().includes(needle)))
    );
  }, [all, filter, query, names]);

  return (
    <>
      <header className="flex flex-col gap-1.5 pt-8 pb-6">
        <h1 className="text-balance font-semibold text-2xl tracking-tight">Runs</h1>
        <p className="text-foreground-muted">
          Every plan, verification, apply and sync, newest first.
        </p>
      </header>
      {runs.isPending ? (
        <div className="flex flex-col gap-2" aria-busy="true">
          <output className="sr-only">Loading runs</output>
          {Array.from({ length: 6 }, (_, i) => (
            <Bar key={i} className="h-12 w-full" />
          ))}
        </div>
      ) : runs.error ? (
        <ErrorPanel error={runs.error} onRetry={() => runs.refetch()} />
      ) : all.length === 0 ? (
        <EmptyPanel title="No runs yet">
          <p className="max-w-[52ch] text-pretty text-foreground-muted">
            Open a configuration and start a plan. Its run appears here.
          </p>
        </EmptyPanel>
      ) : (
        <div className="flex flex-col gap-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <SearchField
              value={query}
              onChange={setQuery}
              count={shown.length}
              placeholder="Search by configuration, branch or person"
              noun={["run matches", "runs match"]}
            />
            <fieldset
              aria-label="Show runs"
              className="m-0 flex min-w-0 max-w-full gap-0.5 overflow-x-auto rounded-xl border-0 bg-content-strong p-0.5"
            >
              {FILTERS.map((f) => (
                <button
                  key={f.id}
                  type="button"
                  aria-pressed={filter === f.id}
                  onClick={() => setFilter(f.id)}
                  className={`inline-flex h-8 shrink-0 cursor-pointer items-center gap-1.5 whitespace-nowrap rounded-[10px] px-2.5 text-sm transition-colors duration-150 ease-out focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring-halo motion-reduce:transition-none ${
                    filter === f.id
                      ? "bg-content text-foreground shadow-input"
                      : "text-foreground-muted hover:text-foreground"
                  }`}
                >
                  {f.label}
                  <span className="text-subtle text-xs tabular-nums">
                    {all.filter(f.test).length}
                  </span>
                </button>
              ))}
            </fieldset>
          </div>
          {shown.length === 0 ? (
            <EmptyPanel title="No runs match">
              <p className="text-foreground-muted">Change the search or the filter.</p>
            </EmptyPanel>
          ) : (
            <RunsTable runs={shown} configNames={names} latestVersions={latestVersions} />
          )}
          {runs.hasNextPage && (
            <div className="flex justify-center">
              <Button
                variant="outline"
                size="sm"
                isDisabled={runs.isFetchingNextPage}
                onPress={() => runs.fetchNextPage()}
              >
                {runs.isFetchingNextPage ? "Loading…" : "Load older runs"}
              </Button>
            </div>
          )}
        </div>
      )}
    </>
  );
}
