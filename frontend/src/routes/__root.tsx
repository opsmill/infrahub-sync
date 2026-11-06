import { Tooltip } from "@infrahub/ui";
import { TanStackDevtools } from "@tanstack/react-devtools";
import { type QueryClient, useQuery } from "@tanstack/react-query";
import { ReactQueryDevtoolsPanel } from "@tanstack/react-query-devtools";
import { createRootRouteWithContext, Link, Outlet, useRouterState } from "@tanstack/react-router";
import { TanStackRouterDevtoolsPanel } from "@tanstack/react-router-devtools";

import { meQuery, statusQuery, versionQuery } from "#/api/queries";
import type { ServiceStatus } from "#/api/types";
import { formatRelative } from "#/lib/time";

import "../styles.css";

export interface RouterContext {
  queryClient: QueryClient;
}

export const Route = createRootRouteWithContext<RouterContext>()({
  component: RootComponent,
});

const NAV =
  "relative inline-flex h-12 items-center px-1 text-foreground-muted text-sm transition-colors duration-150 ease-out hover:text-foreground focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring-halo data-current:text-foreground data-current:after:absolute data-current:after:inset-x-0 data-current:after:bottom-[-1px] data-current:after:h-0.5 data-current:after:rounded-full data-current:after:bg-foreground";

function Mark() {
  return (
    <svg viewBox="0 0 20 20" className="size-5" aria-hidden="true">
      <rect x="1" y="1" width="18" height="18" rx="5" className="fill-cyan-900" />
      <circle cx="6" cy="10" r="1.75" className="fill-white" />
      <circle cx="14" cy="10" r="1.75" className="fill-white" />
      <path d="M7.75 10h4.5" stroke="white" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}

const WORKER: Record<string, { tone: string; label: string }> = {
  ready: { tone: "bg-success-strong", label: "Worker ready" },
  busy: { tone: "bg-warning-strong", label: "Worker busy" },
  "no-live-worker": { tone: "bg-danger-strong", label: "No live worker" },
  unavailable: { tone: "bg-subtle-muted", label: "Worker status unavailable" },
};

function statusMessage(status: ServiceStatus) {
  const w = status.worker;
  const parts = [
    w.live_workers != null &&
      `${w.live_workers} live ${w.live_workers === 1 ? "worker" : "workers"}`,
    w.queue_depth != null && `${w.queue_depth} queued`,
    w.observed_at && `checked ${formatRelative(w.observed_at)}`,
  ].filter(Boolean);
  return parts.length ? parts.join(" · ") : "The Sync API could not read the work pool.";
}

function ServiceIndicator() {
  const status = useQuery(statusQuery());
  const version = useQuery(versionQuery());
  if (status.isPending) return null;
  const w = status.data ? (WORKER[status.data.worker.state] ?? WORKER.unavailable) : null;
  const tone = w?.tone ?? "bg-danger-strong";
  const label = w?.label ?? "Sync API unreachable";
  return (
    <Tooltip
      nonInteractiveTrigger
      placement="bottom"
      message={
        <span className="block max-w-64 leading-5">
          {status.data ? statusMessage(status.data) : "The Sync API did not answer."}
          {version.data && (
            <span className="block text-stone-300">Sync API {version.data.server_version}</span>
          )}
        </span>
      }
    >
      <span className="inline-flex cursor-default items-center gap-2 text-foreground-muted text-xs">
        <span className={`size-2 rounded-full ${tone}`} aria-hidden="true" />
        <span className="max-sm:sr-only">{label}</span>
      </span>
    </Tooltip>
  );
}

function Actor() {
  const me = useQuery(meQuery());
  if (!me.data) return null;
  return (
    <span
      className="hidden items-center gap-2 text-foreground-muted text-xs sm:inline-flex"
      title="The actor your token authenticates as"
    >
      <span className="flex size-6 items-center justify-center rounded-full bg-content-strong font-medium text-[11px] text-foreground uppercase">
        {me.data.actor.slice(0, 2)}
      </span>
      {me.data.actor}
      {me.data.administrator && <span className="text-subtle">admin</span>}
    </span>
  );
}

function RootComponent() {
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const section = pathname.startsWith("/runs") ? "runs" : "configs";
  return (
    <div className="min-h-dvh bg-background text-foreground text-sm antialiased">
      <a
        href="#main"
        className="sr-only z-50 rounded-md bg-content px-3 py-2 focus:not-sr-only focus:fixed focus:top-2 focus:left-2"
      >
        Skip to content
      </a>
      <header className="sticky top-0 z-40 border-b bg-background/85 backdrop-blur-md">
        <div className="mx-auto flex h-12 max-w-[1200px] items-center gap-4 px-4 sm:gap-6">
          <Link
            to="/"
            className="inline-flex items-center gap-2 rounded-md font-semibold tracking-tight focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring-halo"
          >
            <Mark />
            <span className="max-sm:sr-only">Infrahub Sync</span>
          </Link>
          <nav aria-label="Main" className="flex items-center gap-4">
            <Link
              to="/"
              className={NAV}
              data-current={section === "configs" ? "" : undefined}
              activeProps={{ "aria-current": undefined }}
            >
              Configurations
            </Link>
            <Link to="/runs" className={NAV} data-current={section === "runs" ? "" : undefined}>
              Runs
            </Link>
          </nav>
          <div className="ml-auto flex items-center gap-5">
            <ServiceIndicator />
            <Actor />
          </div>
        </div>
      </header>
      <main id="main" className="mx-auto max-w-[1200px] px-4 pb-24">
        <Outlet />
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
