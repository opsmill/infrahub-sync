import {
  Button,
  buttonVariants,
  Label,
  Select,
  SelectItem,
  SelectList,
  SelectTrigger,
  Spinner,
} from "@infrahub/ui";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { type ReactNode, useMemo, useRef, useState } from "react";

import { configsQuery, describeError, schedulePagesQuery } from "#/api/queries";
import { PlusIcon } from "#/components/icons";
import { Bar, EmptyPanel, ErrorPanel, SectionHeading, StaleNotice } from "#/components/page";
import { fmt } from "#/domain/run-state";
import { useDocumentTitle } from "#/lib/title";

import { ScheduleFormDialog } from "./ScheduleFormDialog";
import { SchedulesTable, SchedulesTableSkeleton } from "./SchedulesTable";

export const SCHEDULE_STATES = ["active", "paused", "stopped", "deleted"] as const;
export type ScheduleStateFilter = (typeof SCHEDULE_STATES)[number];

export interface SchedulesSearch {
  state?: ScheduleStateFilter;
  config?: string;
}

/** The select's key for "every state except deleted", which sends no `state` to the Sync API. */
const NOT_DELETED = "__not_deleted__";
const ALL_CONFIGS = "__all__";

const STATE_OPTIONS: { id: ScheduleStateFilter | typeof NOT_DELETED; label: string }[] = [
  { id: NOT_DELETED, label: "Not deleted" },
  { id: "active", label: "Active" },
  { id: "paused", label: "Paused" },
  { id: "stopped", label: "Stopped" },
  { id: "deleted", label: "Deleted" },
];

const LIST_TITLE: Record<ScheduleStateFilter | typeof NOT_DELETED, string> = {
  [NOT_DELETED]: "Schedules that are not deleted",
  active: "Active schedules",
  paused: "Paused schedules",
  stopped: "Stopped schedules",
  deleted: "Deleted schedules",
};

/** Button sizes reach 44 px on touch, like the runs list. */
const TOUCH = "pointer-coarse:h-11";

const plural = (n: number, one: string, many: string) => `${fmt(n)} ${n === 1 ? one : many}`;

export function SchedulesPage({ state, config }: SchedulesSearch) {
  useDocumentTitle("Schedules");
  const navigate = useNavigate();
  const setSearch = (patch: SchedulesSearch) =>
    navigate({
      to: "/schedules",
      search: { state, config, ...patch },
      replace: true,
      resetScroll: false,
    });

  const schedules = useInfiniteQuery(schedulePagesQuery({ state, config }));
  const configs = useQuery(configsQuery());
  const [creating, setCreating] = useState(false);
  const noConfig = configs.data !== undefined && configs.data.items.length === 0;
  const createButton = (variant: "primary" | "outline") => (
    <Button variant={variant} size="sm" className={TOUCH} onPress={() => setCreating(true)}>
      <PlusIcon />
      Create schedule
    </Button>
  );

  const listed = useMemo(
    () => schedules.data?.pages.flatMap((p) => p.items) ?? [],
    [schedules.data]
  );
  const configNames = useMemo(
    () => new Map((configs.data?.items ?? []).map((c) => [c.config_id, c.name ?? c.config_id])),
    [configs.data]
  );
  const summaryRef = useRef<HTMLParagraphElement>(null);
  const more = schedules.hasNextPage;
  const filtered = Boolean(state || config);
  const clearFilters = () => setSearch({ state: undefined, config: undefined });

  const loadOlder = async () => {
    const r = await schedules.fetchNextPage();
    if (r.isError) return;
    const id = r.data?.pages.at(-1)?.items[0]?.schedule_id;
    requestAnimationFrame(() => {
      const link = id
        ? [
            ...document.querySelectorAll<HTMLElement>(`a[data-schedule-id="${CSS.escape(id)}"]`),
          ].find((a) => a.offsetParent !== null)
        : undefined;
      (link ?? summaryRef.current)?.focus();
    });
  };
  const retryRefresh = async () => {
    const r = await schedules.refetch();
    if (!r.isError) summaryRef.current?.focus();
  };

  let body: ReactNode;
  if (schedules.isPending) {
    body = (
      <>
        <div className="flex h-4 items-center" aria-hidden="true">
          <Bar className="h-3 w-24" />
        </div>
        <SchedulesTableSkeleton />
      </>
    );
  } else if (!schedules.data) {
    body = <ErrorPanel error={schedules.error} onRetry={() => schedules.refetch()} />;
  } else if (listed.length === 0 && filtered) {
    body = (
      <EmptyPanel title="No schedule matches these filters.">
        <Button variant="outline" size="sm" className={TOUCH} onPress={clearFilters}>
          Clear filters
        </Button>
      </EmptyPanel>
    );
  } else if (listed.length === 0) {
    body = (
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
    const titleKey = state ?? NOT_DELETED;
    body = (
      <section aria-labelledby="schedules-list" className="flex flex-col gap-3">
        <SectionHeading
          id="schedules-list"
          title={LIST_TITLE[titleKey]}
          count={more ? undefined : listed.length}
        />
        <div className="flex flex-col gap-1">
          <p
            ref={summaryRef}
            tabIndex={-1}
            aria-live="polite"
            className="text-subtle text-xs tabular-nums outline-none"
          >
            {more
              ? plural(listed.length, "loaded schedule", "loaded schedules")
              : plural(listed.length, "schedule", "schedules")}
            {more && " · Older schedules are not loaded."}
          </p>
          {configs.error && !configs.data && (
            <output className="block text-subtle text-xs">
              Configuration names could not be loaded, so rows show configuration IDs.
            </output>
          )}
        </div>
        <SchedulesTable schedules={listed} label="Schedules" configNames={configNames} />
        {more ? (
          <div className="flex flex-col items-center gap-2">
            <LoadOlder schedules={schedules} onLoad={loadOlder} />
          </div>
        ) : (
          schedules.data.pages.length > 1 && (
            <p className="text-center text-subtle text-xs">
              All {fmt(listed.length)} schedules are loaded.
            </p>
          )
        )}
      </section>
    );
  }

  return (
    <>
      <header className="flex flex-col items-start gap-4 pt-8 pb-6 sm:flex-row sm:justify-between">
        <div className="flex flex-col gap-1.5">
          <h1 className="text-balance font-semibold text-2xl tracking-tight">Schedules</h1>
          <p className="text-pretty text-foreground-muted">
            The schedules that start runs of a configuration, newest first.
          </p>
        </div>
        {createButton("primary")}
      </header>
      {creating && (
        <ScheduleFormDialog target={{ mode: "create" }} onClose={() => setCreating(false)} />
      )}
      {schedules.isRefetchError && (
        <StaleNotice
          error={schedules.error}
          updatedAt={schedules.dataUpdatedAt}
          onRetry={retryRefresh}
        />
      )}
      <div className="flex flex-col gap-4">
        <div className="flex flex-wrap items-end gap-3">
          <StateSelect value={state} onChange={(s) => setSearch({ state: s })} />
          <ConfigSelect
            value={config}
            configs={configs.data?.items ?? []}
            onChange={(id) => setSearch({ config: id })}
          />
        </div>
        {body}
      </div>
    </>
  );
}

function StateSelect({
  value,
  onChange,
}: {
  value?: ScheduleStateFilter;
  onChange: (state: ScheduleStateFilter | undefined) => void;
}) {
  return (
    <Select
      className="flex w-full flex-col gap-1.5 sm:w-48"
      selectedKey={value ?? NOT_DELETED}
      onSelectionChange={(k) =>
        onChange(k === NOT_DELETED || k == null ? undefined : (k as ScheduleStateFilter))
      }
    >
      <Label className="text-subtle text-xs">State</Label>
      <SelectTrigger size="md" />
      <SelectList className="bg-content">
        {STATE_OPTIONS.map((o) => (
          <SelectItem key={o.id} id={o.id} textValue={o.label}>
            {o.label}
          </SelectItem>
        ))}
      </SelectList>
    </Select>
  );
}

/** Copied from the runs list (`features/runs/RunsPage.tsx`). */
function ConfigSelect({
  value,
  configs,
  onChange,
}: {
  value?: string;
  configs: { config_id: string; name?: string | null }[];
  onChange: (id: string | undefined) => void;
}) {
  const options = configs.map((c) => ({ id: c.config_id, name: c.name ?? c.config_id }));
  if (value && !options.some((o) => o.id === value)) options.push({ id: value, name: value });
  return (
    <Select
      className="flex w-full flex-col gap-1.5 sm:w-64"
      selectedKey={value ?? ALL_CONFIGS}
      onSelectionChange={(k) => onChange(k === ALL_CONFIGS || k == null ? undefined : String(k))}
    >
      <Label className="text-subtle text-xs">Configuration</Label>
      <SelectTrigger size="md" />
      <SelectList
        width="min-trigger"
        className="max-h-80 max-w-[min(32rem,calc(100vw-2rem))] bg-content"
      >
        <SelectItem id={ALL_CONFIGS} textValue="All configurations">
          All configurations
        </SelectItem>
        {options.map((o) => (
          <SelectItem key={o.id} id={o.id} textValue={o.name}>
            <span className="flex min-w-0 flex-col">
              <span className="truncate">{o.name}</span>
              {o.name !== o.id && (
                <span className="truncate font-mono text-subtle text-xs in-[button]:hidden">
                  {o.id}
                </span>
              )}
            </span>
          </SelectItem>
        ))}
      </SelectList>
    </Select>
  );
}

/** Copied from the runs list (`features/runs/RunsPage.tsx`). */
function LoadOlder({
  schedules,
  onLoad,
}: {
  schedules: { isFetchingNextPage: boolean; isFetchNextPageError: boolean; error: unknown };
  onLoad: () => void;
}) {
  const d = schedules.isFetchNextPageError ? describeError(schedules.error) : undefined;
  return (
    <>
      {d && !schedules.isFetchingNextPage && (
        <p role="alert" className="max-w-[60ch] text-pretty text-center text-danger text-sm">
          Could not load older schedules. {d.detail ?? `${d.title}.`}
        </p>
      )}
      <Button
        variant="outline"
        size="sm"
        className={TOUCH}
        isDisabledAndFocusable={schedules.isFetchingNextPage}
        onPress={onLoad}
      >
        {schedules.isFetchingNextPage ? (
          <>
            <Spinner aria-hidden="true" className="motion-reduce:animate-none" />
            Loading older schedules…
          </>
        ) : schedules.isFetchNextPageError ? (
          "Try again"
        ) : (
          "Load older schedules"
        )}
      </Button>
    </>
  );
}
