import { Button, buttonVariants } from "@infrahub/ui";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { type ReactNode, useMemo, useRef, useState } from "react";
import { cn } from "tailwind-variants";

import { configsQuery, describeError } from "#/api/queries";
import { PlusIcon } from "#/components/icons";
import {
  EmptyPanel,
  ErrorPanel,
  FilterTabs,
  FOCUS,
  SearchField,
  SectionHeading,
  StaleNotice,
} from "#/components/page";
import {
  byName,
  emptyTabText,
  groupSchedules,
  matchesSearch,
  nextStarts,
  SCHEDULE_TABS,
  type ScheduleTab,
  TAB_LABEL,
  tabShows,
} from "#/domain/schedule-list";
import { useNow } from "#/lib/time";
import { useDocumentTitle } from "#/lib/title";

import { ScheduleFormDialog } from "./ScheduleFormDialog";
import {
  ActiveRow,
  AttentionCard,
  Group,
  NextStartsPanel,
  RestingRow,
  RowsSkeleton,
  SchedulesSkeleton,
  TOUCH,
} from "./ScheduleListParts";
import { useAllSchedules } from "./use-all-schedules";

/** The search parameters of `/schedules`; the "All" tab is the address without `show`. */
export interface SchedulesSearch {
  show?: Exclude<ScheduleTab, "all">;
  q?: string;
  config?: string;
}

/**
 * Whether a list read page by page is still loading. The groups and counts need every page, so
 * the list waits for the last one, unless reading a page failed.
 */
const loading = (q: { isPending: boolean; hasNextPage: boolean; isFetchNextPageError: boolean }) =>
  q.isPending || (q.hasNextPage && !q.isFetchNextPageError);

export function SchedulesPage({ show, q = "", config }: SchedulesSearch) {
  useDocumentTitle("Schedules");
  const tab: ScheduleTab = show ?? "all";
  const navigate = useNavigate();
  const setSearch = (patch: SchedulesSearch) =>
    navigate({
      from: "/schedules/",
      to: "/schedules",
      search: (prev) => ({ ...prev, ...patch }),
      replace: true,
      resetScroll: false,
    });

  const now = useNow();
  const live = useAllSchedules({ config });
  const deleted = useAllSchedules({ state: "deleted", config }, { enabled: tab === "deleted" });
  const configs = useQuery(configsQuery());
  const [creating, setCreating] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);
  const noConfig = configs.data !== undefined && configs.data.items.length === 0;

  const configNames = useMemo(
    () => new Map((configs.data?.items ?? []).map((c) => [c.config_id, c.name ?? c.config_id])),
    [configs.data]
  );
  const configName = (id: string) => configNames.get(id) ?? id;

  const liveItems = useMemo(() => live.data?.pages.flatMap((p) => p.items) ?? [], [live.data]);
  const groups = groupSchedules(liveItems, q, configName);
  const starts = nextStarts(liveItems, now);
  const deletedItems = deleted.data?.pages.flatMap((p) => p.items) ?? [];
  const deletedMatches = deletedItems
    .filter((s) => matchesSearch(s, q, configName(s.config_id)))
    .sort(byName);
  const deletedKnown = Boolean(deleted.data) && !loading(deleted);

  const counts: Record<ScheduleTab, number | undefined> = {
    all: groups.matches.length,
    attention: groups.needsAction.length,
    active: groups.active.length,
    paused: groups.paused.length,
    // Deleted schedules are read only when their tab is chosen.
    deleted: deletedKnown ? deletedMatches.length : undefined,
  };
  const shows = tabShows(tab);
  const search = q.trim();

  // A notice's button disappears once its read succeeds, so focus moves to the search field.
  const focusSearch = () => searchRef.current?.focus();
  const clearSearch = () => {
    setSearch({ q: undefined });
    searchRef.current?.focus();
  };
  const removeConfig = () => {
    setSearch({ config: undefined });
    searchRef.current?.focus();
  };
  const createButton = (variant: "primary" | "outline") => (
    <Button variant={variant} size="sm" className={TOUCH} onPress={() => setCreating(true)}>
      <PlusIcon />
      Create schedule
    </Button>
  );
  const stale = live.isRefetchError
    ? live
    : tab === "deleted" && deleted.isRefetchError
      ? deleted
      : undefined;

  const retryRefresh = async () => {
    const r = await stale?.refetch();
    if (r && !r.isError) focusSearch();
  };

  let main: ReactNode;
  if (tab === "deleted") {
    // The notice stays above the empty state too: a search can match none of the pages read
    // while a later page that failed to load holds the schedule searched for.
    const deletedMain = loading(deleted) ? (
      <div aria-busy="true">
        <output className="sr-only">Loading deleted schedules</output>
        <RowsSkeleton />
      </div>
    ) : !deleted.data ? (
      <ErrorPanel error={deleted.error} onRetry={() => deleted.refetch()} />
    ) : deletedMatches.length === 0 ? (
      <EmptyPanel title={emptyTabText("deleted", search)}>
        {search && (
          <Button variant="outline" size="sm" className={TOUCH} onPress={clearSearch}>
            Clear search
          </Button>
        )}
      </EmptyPanel>
    ) : (
      <Group
        id="schedules-deleted"
        title="Deleted"
        hint="Their runs are kept."
        items={deletedMatches}
      >
        {(s) => <RestingRow key={s.schedule_id} s={s} configName={configName(s.config_id)} />}
      </Group>
    );
    main = (
      <>
        <PartialNotice what="deleted schedules" pages={deleted} onLoaded={focusSearch} />
        {deletedMain}
      </>
    );
  } else if (liveItems.length === 0 && config) {
    main = (
      <EmptyPanel title="No schedule matches these filters.">
        <Button
          variant="outline"
          size="sm"
          className={TOUCH}
          onPress={() => setSearch({ config: undefined, q: undefined })}
        >
          Clear filters
        </Button>
      </EmptyPanel>
    );
  } else if (liveItems.length === 0) {
    main = (
      <EmptyPanel title="No schedule exists.">
        {noConfig && (
          <p className="max-w-[52ch] text-pretty text-foreground-muted">
            Register a configuration first, then create its schedule.
          </p>
        )}
        <div className="flex flex-wrap justify-center gap-2">
          {createButton("outline")}
          {noConfig && (
            <Link
              to="/"
              className={buttonVariants({ variant: "outline", size: "sm", className: TOUCH })}
            >
              View configurations
            </Link>
          )}
        </div>
      </EmptyPanel>
    );
  } else {
    const activeItems =
      shows.active === "all" ? groups.active : shows.active ? groups.activeResting : [];
    const shown =
      (shows.needsAction ? groups.needsAction.length : 0) +
      activeItems.length +
      (shows.paused ? groups.paused.length : 0);
    main =
      shown === 0 ? (
        <EmptyPanel title={emptyTabText(tab, search)}>
          <div className="flex flex-wrap justify-center gap-2">
            {search && (
              <Button variant="outline" size="sm" className={TOUCH} onPress={clearSearch}>
                Clear search
              </Button>
            )}
            {tab !== "all" && (
              <Button
                variant="outline"
                size="sm"
                className={TOUCH}
                onPress={() => setSearch({ show: undefined })}
              >
                Show all schedules
              </Button>
            )}
          </div>
        </EmptyPanel>
      ) : (
        <>
          {shows.needsAction && groups.needsAction.length > 0 && (
            <section aria-labelledby="schedules-attention" className="flex flex-col gap-2">
              <SectionHeading
                id="schedules-attention"
                title="Needs action"
                count={groups.needsAction.length}
              >
                Stopped or failing schedules start no run, or will stop soon.
              </SectionHeading>
              <ul className="flex flex-col gap-2">
                {groups.needsAction.map((s) => (
                  <AttentionCard key={s.schedule_id} s={s} configName={configName(s.config_id)} />
                ))}
              </ul>
            </section>
          )}
          <Group id="schedules-active" title="Active" items={activeItems}>
            {(s) => <ActiveRow key={s.schedule_id} s={s} configName={configName(s.config_id)} />}
          </Group>
          {shows.paused && (
            <Group
              id="schedules-paused"
              title="Paused"
              hint="Start no run until resumed."
              items={groups.paused}
            >
              {(s) => <RestingRow key={s.schedule_id} s={s} configName={configName(s.config_id)} />}
            </Group>
          )}
        </>
      );
  }

  let body: ReactNode;
  if (loading(live)) {
    body = <SchedulesSkeleton />;
  } else if (!live.data) {
    body = <ErrorPanel error={live.error} onRetry={() => live.refetch()} />;
  } else {
    body = (
      <div className="grid grid-cols-1 items-start gap-8 lg:grid-cols-[minmax(0,1fr)_18rem]">
        <div className="flex min-w-0 flex-col gap-6">
          <div className="flex flex-col gap-3">
            <div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
              <FilterTabs<ScheduleTab>
                label="Show"
                value={tab}
                onChange={(id) => setSearch({ show: id === "all" ? undefined : id })}
                options={SCHEDULE_TABS.map((id) => ({
                  id,
                  label: TAB_LABEL[id],
                  count: counts[id],
                }))}
              />
              <SearchField
                value={q}
                onChange={(v) => setSearch({ q: v || undefined })}
                placeholder="Search schedules or configurations"
                label="Search schedules by name or by configuration name"
                count={tab === "deleted" ? deletedMatches.length : groups.matches.length}
                noun={["schedule", "schedules"]}
                inputRef={searchRef}
              />
            </div>
            {config && (
              <ConfigFilter configId={config} name={configName(config)} onRemove={removeConfig} />
            )}
            {configs.error && !configs.data && (
              <output className="block text-subtle text-xs">
                Configuration names could not be loaded, so schedules show configuration IDs.
              </output>
            )}
            <PartialNotice what="schedules" pages={live} onLoaded={focusSearch} />
          </div>
          {main}
        </div>
        {liveItems.length > 0 && (
          <aside aria-label="Next starts" className="flex flex-col gap-6 lg:sticky lg:top-16">
            <NextStartsPanel starts={starts} />
          </aside>
        )}
      </div>
    );
  }

  return (
    <>
      <header className="flex flex-col items-start gap-4 pt-8 pb-6 sm:flex-row sm:justify-between">
        <div className="flex flex-col gap-1.5">
          <h1 className="text-balance font-semibold text-2xl tracking-tight">Schedules</h1>
          <p className="text-pretty text-foreground-muted">Schedules that need you come first.</p>
        </div>
        {createButton("primary")}
      </header>
      {creating && (
        <ScheduleFormDialog target={{ mode: "create" }} onClose={() => setCreating(false)} />
      )}
      {stale && (
        <StaleNotice error={stale.error} updatedAt={stale.dataUpdatedAt} onRetry={retryRefresh} />
      )}
      {body}
    </>
  );
}

/** The configuration filter: it names the configuration, links to it and removes the filter. */
function ConfigFilter({
  configId,
  name,
  onRemove,
}: {
  configId: string;
  name: string;
  onRemove: () => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      <span className="inline-flex min-w-0 items-center gap-1 rounded-md border px-2 py-1">
        <span className="text-subtle">Configuration:</span>
        <Link
          to="/configs/$configId"
          params={{ configId }}
          className={cn(
            "min-w-0 rounded-sm text-accent [overflow-wrap:anywhere] hover:underline",
            FOCUS
          )}
        >
          {name}
        </Link>
      </span>
      <Button variant="outline" size="sm" className={TOUCH} onPress={onRemove}>
        Remove configuration filter
      </Button>
    </div>
  );
}

/**
 * Shown when a page of the list could not be read: the counts and groups cover only the pages
 * read so far, and "Try again" reads the missing page.
 */
function PartialNotice({
  what,
  pages,
  onLoaded,
}: {
  what: string;
  /** Called once the missing page is read: the notice and its button are then gone. */
  onLoaded: () => void;
  pages: {
    data?: { pages: { items: unknown[] }[] };
    isFetchNextPageError: boolean;
    isFetchingNextPage: boolean;
    error: unknown;
    fetchNextPage: () => Promise<{ isError: boolean }>;
  };
}) {
  if (!pages.isFetchNextPageError || pages.isFetchingNextPage) return null;
  const d = describeError(pages.error);
  const retry = async () => {
    const r = await pages.fetchNextPage();
    if (!r.isError) onLoaded();
  };
  return (
    <div role="alert" className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
      <p className="max-w-[70ch] text-pretty text-danger">
        Not all {what} could be loaded, so the counts and lists are incomplete.{" "}
        {d.detail ?? `${d.title}.`}
      </p>
      <Button variant="outline" size="sm" className={TOUCH} onPress={retry}>
        Try again
      </Button>
    </div>
  );
}
