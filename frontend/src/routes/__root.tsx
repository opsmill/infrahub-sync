import { Button, Popover, PopoverDialog, PopoverTrigger } from "@infrahub/ui";
import { TanStackDevtools } from "@tanstack/react-devtools";
import { type QueryClient, useQuery } from "@tanstack/react-query";
import { ReactQueryDevtoolsPanel } from "@tanstack/react-query-devtools";
import {
  createLink,
  createRootRouteWithContext,
  Link,
  Outlet,
  useRouterState,
} from "@tanstack/react-router";
import { TanStackRouterDevtoolsPanel } from "@tanstack/react-router-devtools";
import { type ComponentPropsWithRef, type ReactNode, useEffect, useRef, useState } from "react";
import { cn } from "tailwind-variants";
import { signOut } from "#/api/client";
import { describeError, meQuery, statusQuery, versionQuery } from "#/api/queries";
import { announce, LiveRegion } from "#/components/announcer";
import { FOCUS, FOCUS_RAC } from "#/components/classes";
import { StateIcon } from "#/components/icons";
import { Notice } from "#/components/page";
import { When } from "#/components/run-status";
import { ThemeMenu } from "#/components/theme-menu";
import { fmt, NO_WORKER_SENTENCE, queueSentence, type RunState } from "#/domain/run-state";

import "../styles.css";

export interface RouterContext {
  queryClient: QueryClient;
}

export const Route = createRootRouteWithContext<RouterContext>()({
  component: RootComponent,
});

const NAV = cn(
  "relative inline-flex h-8 shrink-0 items-center whitespace-nowrap rounded-md px-2 text-foreground-muted text-sm transition-colors duration-150 ease-out hover:bg-highlight hover:text-foreground motion-reduce:transition-none before:absolute before:inset-x-0 before:-inset-y-2",
  FOCUS,
  "data-current:text-foreground data-current:after:absolute data-current:after:inset-x-2 data-current:after:bottom-[-9px] data-current:after:h-0.5 data-current:after:rounded-full data-current:after:bg-foreground"
);

function Mark() {
  return (
    <svg viewBox="0 0 20 20" className="size-5 shrink-0" aria-hidden="true">
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

interface ServiceState {
  icon: RunState;
  color: string;
  label: string;
  sentence?: ReactNode;
}

interface QueryResult {
  error: unknown;
  isError: boolean;
  errorUpdatedAt: number;
  dataUpdatedAt: number;
}

/**
 * Whether a query's last finished request failed, and its error. A refetch of a query without data
 * puts it back in "pending" and clears its error, so the error is kept until a newer answer.
 */
function useLastFailure(query: QueryResult): { failed: boolean; error: unknown } {
  const [kept, setKept] = useState<unknown>();
  useEffect(() => {
    if (query.isError) setKept(query.error);
  }, [query.isError, query.error]);
  const failed = query.errorUpdatedAt > query.dataUpdatedAt;
  return { failed, error: failed ? (query.isError ? query.error : kept) : undefined };
}

function useServiceState(): ServiceState & { showDetail: boolean } {
  const status = useQuery(statusQuery());
  const me = useQuery(meQuery());
  const statusFailure = useLastFailure(status);
  const meFailure = useLastFailure(me);
  // React Query keeps the last data after a failed refresh, so the failure is checked first.
  if (statusFailure.failed) {
    const d = describeError(statusFailure.error);
    if (d.kind === "envelope") {
      return {
        icon: "failed",
        color: "text-danger",
        label: "Sync API returned an error",
        sentence: (
          <>
            <span>{d.title}.</span>
            {d.codeLine && (
              <span className="block font-mono text-subtle text-xs">{d.codeLine}</span>
            )}
          </>
        ),
        showDetail: false,
      };
    }
    const last = status.data
      ? (status.data.worker.observed_at ?? new Date(status.dataUpdatedAt).toISOString())
      : undefined;
    return {
      icon: "failed",
      color: "text-danger",
      label: "Sync API not answering",
      sentence: (
        <>
          The Sync API did not answer the last status check.
          {last && (
            <>
              {" "}
              Last answer <When iso={last} />.
            </>
          )}
        </>
      ),
      showDetail: false,
    };
  }
  // `GET /status` needs no session, so only `GET /me` shows that the Sync API refuses the session.
  if (meFailure.failed && describeError(meFailure.error).status === 401) {
    return {
      icon: "failed",
      color: "text-danger",
      label: "Sync API refused the session",
      sentence:
        "The Sync API answered, but it did not accept the session sent with requests from this page.",
      showDetail: false,
    };
  }
  if (!status.data) {
    return {
      icon: "idle",
      color: "text-subtle-muted",
      label: "Checking the Sync API",
      showDetail: false,
    };
  }
  const w = status.data.worker;
  const showDetail = w.detail_available;
  switch (w.state) {
    case "ready":
      return { icon: "insync", color: "text-success", label: "Worker ready", showDetail };
    case "busy":
      return {
        icon: "review",
        color: "text-accent",
        label:
          w.queue_depth != null
            ? `${fmt(w.queue_depth)} ${w.queue_depth === 1 ? "execution" : "executions"} waiting for a worker`
            : "Executions waiting for a worker",
        showDetail,
      };
    case "no-live-worker":
      // The notice on the page explains this state, so the menu does not repeat it.
      return { icon: "failed", color: "text-danger", label: "No worker online", showDetail };
    default:
      return {
        icon: "idle",
        color: "text-subtle",
        label: "Worker status not known",
        sentence:
          "The Sync service could not read the state of its workers, so the number of workers is not known.",
        showDetail,
      };
  }
}

function Row({
  term,
  children,
  className,
}: {
  term: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <>
      <dt className="text-subtle">{term}</dt>
      <dd className={cn("min-w-0 text-foreground tabular-nums", className)}>{children}</dd>
    </>
  );
}

const DL = "col-span-2 grid grid-cols-subgrid gap-y-1.5";

/** The Sync service's state in the header; opens the details. */
function StatusMenu() {
  const s = useServiceState();
  const status = useQuery(statusQuery());
  const version = useQuery(versionQuery());
  const me = useQuery(meQuery());
  const w = status.data?.worker;
  const workerRows = s.showDetail && w;

  return (
    <PopoverTrigger>
      <Button
        variant="ghost"
        size="sm"
        className={cn(
          "min-w-0 max-w-full shrink gap-2 px-2 max-sm:size-8 max-sm:px-0 max-sm:before:absolute max-sm:before:-inset-2",
          FOCUS_RAC
        )}
        aria-label={`${s.label}. Open Sync service details`}
      >
        <StateIcon state={s.icon} className={cn("size-3.5 shrink-0", s.color)} />
        <span className="min-w-0 truncate font-normal text-foreground-muted text-xs max-sm:sr-only">
          {s.label}
        </span>
      </Button>
      <Popover placement="bottom end" className="bg-content">
        <PopoverDialog
          aria-label="Sync service"
          className="w-80 max-w-[calc(100vw-2rem)] p-4 text-sm"
        >
          <p className="flex items-center gap-2 font-medium text-foreground">
            <StateIcon state={s.icon} className={cn("size-4 shrink-0", s.color)} />
            {s.label}
          </p>
          {s.sentence && <p className="mt-1.5 text-pretty text-foreground-muted">{s.sentence}</p>}
          {(workerRows || version.data || me.data) && (
            <div className="mt-3 grid grid-cols-[minmax(0,auto)_minmax(0,1fr)] gap-x-4">
              {(workerRows || version.data) && (
                <dl className={DL}>
                  {workerRows && w.live_workers != null && (
                    <Row term="Workers online">{fmt(w.live_workers)}</Row>
                  )}
                  {workerRows && w.queue_depth != null && (
                    <Row term="Executions waiting for a worker">{fmt(w.queue_depth)}</Row>
                  )}
                  {workerRows && w.observed_at && (
                    <Row term="Checked">
                      <When iso={w.observed_at} />
                    </Row>
                  )}
                  {version.data && (
                    <Row
                      term="Sync API version"
                      className="flex flex-wrap items-center gap-x-1.5 gap-y-1"
                    >
                      <span className="font-mono text-xs">{version.data.server_version}</span>
                      {version.data.stability === "unstable" && (
                        <span className="rounded-md border border-border px-1.5 text-[11px] text-subtle leading-[18px]">
                          unstable
                        </span>
                      )}
                    </Row>
                  )}
                </dl>
              )}
              {me.data && (
                <>
                  {(workerRows || version.data) && (
                    <hr className="col-span-2 my-3 border-0 border-border border-t" />
                  )}
                  <dl className={DL}>
                    <Row term="Administrator rights">{me.data.administrator ? "Yes" : "No"}</Row>
                  </dl>
                </>
              )}
            </div>
          )}
        </PopoverDialog>
      </Popover>
    </PopoverTrigger>
  );
}

/**
 * The signed-in account, from `GET /me`, and the action that signs out. Signing out removes the
 * session token, which also empties the query cache and the downloaded plans.
 */
function AccountControls() {
  const me = useQuery(meQuery());
  const [pending, setPending] = useState(false);
  const name = me.data?.actor;

  async function press() {
    setPending(true);
    await signOut();
  }

  return (
    <div className="flex min-w-0 items-center gap-1">
      {name && (
        <span
          className="min-w-0 max-w-24 truncate text-foreground-muted text-xs sm:max-w-48"
          title={`Signed in as ${name}`}
        >
          <span className="sr-only">Signed in as </span>
          {name}
        </span>
      )}
      <Button
        variant="ghost"
        size="sm"
        className={cn("shrink-0", FOCUS_RAC)}
        onPress={press}
        isDisabled={pending}
      >
        Sign out
      </Button>
    </div>
  );
}

/**
 * Shown on every page while no worker is online, because new runs then wait or are abandoned. It
 * sits at the end of the page and sticks to the bottom of the window, so the page content does
 * not move when it appears.
 */
function WorkerNotice() {
  const status = useQuery(statusQuery());
  const shown = Boolean(
    status.data && !status.isError && status.data.worker.state === "no-live-worker"
  );
  useEffect(() => {
    if (shown) announce(NO_WORKER_SENTENCE);
  }, [shown]);
  if (!shown || !status.data) return null;
  const queued = status.data.worker.queue_depth;
  return (
    <div className="sticky bottom-[max(1rem,env(safe-area-inset-bottom))] z-30 mt-8 rounded-xl bg-content shadow-lg">
      <Notice tone="danger" role="note">
        <p>
          {NO_WORKER_SENTENCE}
          {queued != null && queued > 0 && ` ${queueSentence(queued)}`}
        </p>
      </Notice>
    </div>
  );
}

/** An anchor without `aria-current` and `data-status`, which TanStack's `Link` always writes on an active link. */
function PlainAnchor({
  "aria-current": _current,
  "data-status": _status,
  ...props
}: ComponentPropsWithRef<"a"> & { "data-status"?: string }) {
  return <a {...props} />;
}

const PlainLink = createLink(PlainAnchor);

function sectionOf(pathname: string): "configs" | "runs" | "accounts" | "account" | undefined {
  if (pathname === "/" || pathname.startsWith("/configs/")) return "configs";
  if (pathname === "/runs" || pathname.startsWith("/runs/")) return "runs";
  if (pathname === "/accounts") return "accounts";
  if (pathname === "/account") return "account";
  return undefined;
}

/** The title is read once it has not changed for this long. */
const TITLE_SETTLE_MS = 300;
/** After this time the title is read even while the page still loads, and later changes are not. */
const TITLE_WATCH_MS = 3_000;

function RootComponent() {
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const section = sectionOf(pathname);
  // Only an administrator is offered the Accounts page; the Sync API refuses it to the others.
  const administrator = useQuery(meQuery()).data?.administrator === true;

  const mainRef = useRef<HTMLElement>(null);
  const lastPath = useRef(pathname);
  useEffect(() => {
    if (lastPath.current === pathname) return;
    lastPath.current = pathname;
    mainRef.current?.focus({ preventScroll: true });
    // A page names itself in its title once its data loads, and marks its skeleton with aria-busy
    // until then, so the title is read when it has settled and the page is no longer loading.
    const started = Date.now();
    let said = "";
    let settle: number | undefined;
    const schedule = () => {
      window.clearTimeout(settle);
      settle = window.setTimeout(() => {
        const loading = mainRef.current?.querySelector('[aria-busy="true"]');
        if (loading && Date.now() - started < TITLE_WATCH_MS) return schedule();
        if (document.title === said) return;
        said = document.title;
        announce(said);
      }, TITLE_SETTLE_MS);
    };
    const observer = new MutationObserver(schedule);
    observer.observe(document.head, { childList: true, subtree: true, characterData: true });
    const stop = window.setTimeout(() => observer.disconnect(), TITLE_WATCH_MS);
    schedule();
    return () => {
      observer.disconnect();
      window.clearTimeout(settle);
      window.clearTimeout(stop);
    };
  }, [pathname]);

  return (
    <div className="min-h-dvh bg-background text-foreground text-sm antialiased">
      <a
        href="#main"
        className={cn(
          "sr-only font-medium focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:rounded-lg focus:border focus:bg-content focus:px-3 focus:py-2 focus:shadow-md",
          FOCUS
        )}
      >
        Skip to content
      </a>
      <header className="sticky top-0 z-40 border-b bg-background">
        <div className="mx-auto flex h-12 max-w-[1200px] items-center gap-3 px-4 sm:gap-6">
          <PlainLink
            to="/"
            aria-label="Infrahub Sync, configurations"
            className={cn(
              "relative -ml-1 inline-flex h-8 shrink-0 items-center gap-2 whitespace-nowrap rounded-md px-1 font-semibold tracking-tight before:absolute before:-inset-2",
              FOCUS
            )}
          >
            <Mark />
            <span className="max-sm:hidden">Infrahub Sync</span>
          </PlainLink>
          <nav
            aria-label="Main"
            className="flex shrink-0 items-center gap-1 max-sm:min-w-0 max-sm:shrink max-sm:self-stretch max-sm:overflow-x-auto max-sm:[scrollbar-width:none]"
          >
            {/* TanStack sets aria-current="page" on an exact match; a page inside the section gets "true". */}
            <Link
              to="/"
              activeOptions={{ exact: true, includeSearch: false }}
              className={NAV}
              data-current={section === "configs" ? "" : undefined}
              aria-current={section === "configs" ? "true" : undefined}
            >
              Configurations
            </Link>
            <Link
              to="/runs"
              activeOptions={{ exact: true, includeSearch: false }}
              className={NAV}
              data-current={section === "runs" ? "" : undefined}
              aria-current={section === "runs" ? "true" : undefined}
            >
              Runs
            </Link>
            {administrator && (
              <Link
                to="/accounts"
                activeOptions={{ exact: true, includeSearch: false }}
                className={NAV}
                data-current={section === "accounts" ? "" : undefined}
                aria-current={section === "accounts" ? "true" : undefined}
              >
                Accounts
              </Link>
            )}
            <Link
              to="/account"
              activeOptions={{ exact: true, includeSearch: false }}
              className={NAV}
              data-current={section === "account" ? "" : undefined}
              aria-current={section === "account" ? "true" : undefined}
            >
              My account
            </Link>
          </nav>
          <div className="ml-auto flex min-w-0 items-center gap-2 max-sm:shrink-0">
            <StatusMenu />
            <AccountControls />
            <ThemeMenu />
          </div>
        </div>
      </header>
      <LiveRegion />
      <main
        ref={mainRef}
        id="main"
        tabIndex={-1}
        className="mx-auto max-w-[1200px] px-4 pb-24 outline-none"
      >
        <Outlet />
        <WorkerNotice />
      </main>
      {import.meta.env.DEV && (
        <TanStackDevtools
          config={{ position: "bottom-right" }}
          plugins={[
            { name: "TanStack Router", render: <TanStackRouterDevtoolsPanel /> },
            { name: "TanStack Query", render: <ReactQueryDevtoolsPanel /> },
          ]}
        />
      )}
    </div>
  );
}
