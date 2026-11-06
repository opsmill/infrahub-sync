import { Button, Card } from "@infrahub/ui";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { type ReactNode, useState } from "react";

import { configQuery, configVersionQuery, runsQuery, versionsQuery } from "#/api/queries";
import type {
  ConfigurationVersion,
  DeclaredAdapter,
  DeclaredMapping,
  Run,
  RunReference,
  VersionSummary,
} from "#/api/types";
import {
  ArrowRightIcon,
  ChevronRightIcon,
  PlusIcon,
  STATE_TEXT,
  StateIcon,
} from "#/components/icons";
import {
  Bar,
  CopyButton,
  EmptyPanel,
  ErrorPanel,
  SectionHeading,
  textAction,
} from "#/components/page";
import { BranchCode, StateLabel, Stepper, When } from "#/components/run-status";
import { explain, refLabel, refState, runState, runSteps } from "#/domain/run-state";
import { describeSystem, mappedKinds } from "#/domain/systems";
import { RunsTable } from "#/features/runs/RunsTable";
import { StartRunDialog, type StartRunTarget } from "#/features/runs/StartRunDialog";

import { KindsCount } from "./ConfigurationsPage";

export type ConfigTab = "runs" | "versions" | "mapping";

/* ---------- latest run ---------- */

function LatestRun({
  run,
  latestVersion,
  createdAt,
  lastApplied,
  onStart,
}: {
  run?: Run;
  latestVersion: number;
  createdAt: string;
  lastApplied?: Run;
  onStart: () => void;
}) {
  const state = runState(run, { latestVersion });
  const e = explain(run, { latestVersion, createdAt, lastApplied });
  return (
    <Card>
      <div className="grid grid-cols-1 items-center gap-x-8 gap-y-3 p-4 md:grid-cols-[140px_minmax(0,1fr)_auto]">
        <Stepper
          steps={runSteps(run, { latestVersion })}
          quiet={state === "insync" || state === "idle"}
        />
        <div className="flex min-w-0 flex-col gap-0.5">
          <p className={`flex items-start gap-1.5 font-medium ${STATE_TEXT[state]}`}>
            <StateIcon state={state} className="mt-[3px] size-3.5 shrink-0" />
            <span className="text-pretty">{e.short}</span>
          </p>
          <p className="pl-5 text-subtle text-xs [overflow-wrap:anywhere]">
            {e.note}
            {e.note && e.at && <span aria-hidden="true"> · </span>}
            {e.at && <When iso={e.at.iso} prefix={e.at.prefix} />}
            {lastApplied?.finished_at && run?.run_id !== lastApplied.run_id && (
              <>
                <span aria-hidden="true"> · </span>last applied{" "}
                <When iso={lastApplied.finished_at} />
              </>
            )}
          </p>
        </div>
        <div className="md:justify-self-end">
          {e.action?.target === "run" && run && (
            <Link
              to="/runs/$runId"
              params={{ runId: run.run_id }}
              className={textAction(e.action.primary)}
            >
              {e.action.label}
              <ChevronRightIcon />
            </Link>
          )}
          {e.action?.target === "plan" && (
            <button type="button" onClick={onStart} className={textAction(false)}>
              {e.action.label}
              <ChevronRightIcon />
            </button>
          )}
          {!e.action && run && (
            <Link to="/runs/$runId" params={{ runId: run.run_id }} className={textAction(false)}>
              View run
              <ChevronRightIcon />
            </Link>
          )}
        </div>
      </div>
    </Card>
  );
}

/* ---------- versions ---------- */

function RunRef({ label, ref }: { label: string; ref?: RunReference | null }) {
  if (!ref) {
    return (
      <span className="text-subtle">
        {label}: <span>none</span>
      </span>
    );
  }
  return (
    <span className="inline-flex flex-wrap items-center gap-x-1.5">
      <span className="text-subtle">{label}:</span>
      <Link to="/runs/$runId" params={{ runId: ref.run_id }} className="rounded-sm hover:underline">
        <StateLabel state={refState(ref)} label={refLabel(ref)} className="font-normal" />
      </Link>
      <span className="text-subtle">
        <When iso={ref.finished_at ?? ref.started_at} />
      </span>
    </span>
  );
}

function Versions({
  versions,
  onStart,
}: {
  versions: VersionSummary[];
  onStart: (version: number) => void;
}) {
  return (
    <Card className="overflow-hidden">
      <ol className="divide-y">
        {versions.map((v, i) => (
          <li
            key={v.registry_version}
            className="grid grid-cols-1 gap-x-8 gap-y-2 px-4 py-3.5 md:grid-cols-[120px_minmax(0,1fr)_auto]"
          >
            <div className="flex items-baseline gap-2">
              <span className="font-medium tabular-nums">Version {v.registry_version}</span>
              {i === 0 && (
                <span className="rounded-md bg-accent-surface px-1.5 font-medium text-accent text-xs leading-5">
                  Latest
                </span>
              )}
            </div>
            <div className="flex min-w-0 flex-col gap-1 text-[13px]">
              <p className="text-pretty">
                {v.registration_reason ?? <span className="text-subtle">No reason recorded</span>}
              </p>
              <p className="text-subtle text-xs">
                Registered <When iso={v.created_at} />
                {v.registered_by && <> by {v.registered_by}</>}
                <span aria-hidden="true"> · </span>
                <span className="font-mono" title={v.package_checksum}>
                  {v.package_checksum.slice(0, 12)}
                </span>
              </p>
              <div className="flex flex-wrap gap-x-5 gap-y-1 text-xs">
                <RunRef label="Latest run" ref={v.latest_run} />
                <RunRef label="Last applied" ref={v.latest_applied_run} />
              </div>
            </div>
            <div className="md:justify-self-end">
              <button
                type="button"
                onClick={() => onStart(v.registry_version)}
                className={textAction(false)}
              >
                Plan this version
                <ChevronRightIcon />
              </button>
            </div>
          </li>
        ))}
      </ol>
    </Card>
  );
}

/* ---------- mapping ---------- */

function credentialOf(adapter: DeclaredAdapter, version: ConfigurationVersion): string | undefined {
  const token = adapter.settings?.token as { $credential?: string } | undefined;
  const ref = token?.$credential;
  if (!ref) return undefined;
  const c = version.declared_content.credentials?.[ref];
  return c ? `${ref} (${c.provider} ${c.identifier})` : ref;
}

function SystemCard({
  side,
  adapter,
  version,
}: {
  side: string;
  adapter: DeclaredAdapter;
  version: ConfigurationVersion;
}) {
  const s = describeSystem(adapter);
  const url = (adapter.settings?.url ?? adapter.settings?.base_url) as string | undefined;
  const credential = credentialOf(adapter, version);
  return (
    <Card className="p-4">
      <p className="text-subtle text-xs">{side}</p>
      <p className="font-medium text-base">{s.label}</p>
      <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-[13px]">
        <dt className="text-subtle">Adapter</dt>
        <dd className="font-mono text-xs leading-5">{adapter.name}</dd>
        {url && (
          <>
            <dt className="text-subtle">Address</dt>
            <dd className="font-mono text-xs leading-5 [overflow-wrap:anywhere]">{url}</dd>
          </>
        )}
        {s.branch && (
          <>
            <dt className="text-subtle">Branch</dt>
            <dd>
              <BranchCode branch={s.branch} />
            </dd>
          </>
        )}
        {credential && (
          <>
            <dt className="text-subtle">Credential</dt>
            <dd className="font-mono text-xs leading-5 [overflow-wrap:anywhere]">{credential}</dd>
          </>
        )}
      </dl>
    </Card>
  );
}

function MappingRow({ m }: { m: DeclaredMapping }) {
  const [open, setOpen] = useState(false);
  const fields = m.fields ?? [];
  return (
    <li>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="grid w-full cursor-pointer grid-cols-[16px_minmax(0,1fr)_auto] items-center gap-x-3 px-4 py-2.5 text-left text-[13px] transition-colors duration-150 ease-out hover:bg-content-muted focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring-halo focus-visible:ring-inset md:grid-cols-[16px_minmax(0,1fr)_minmax(0,1fr)_auto]"
      >
        <ChevronRightIcon
          className={`size-3.5 text-subtle transition-transform duration-150 ease-out motion-reduce:transition-none ${open ? "rotate-90" : ""}`}
        />
        <span className="font-medium font-mono">{m.name}</span>
        <span
          className="hidden min-w-0 justify-self-start truncate text-left font-mono text-foreground-muted text-xs md:block"
          title={m.mapping}
        >
          from {m.mapping}
        </span>
        <span className="text-subtle text-xs tabular-nums">
          {fields.length} {fields.length === 1 ? "field" : "fields"}
          {m.filters?.length
            ? ` · ${m.filters.length} ${m.filters.length === 1 ? "filter" : "filters"}`
            : ""}
          {m.transforms?.length
            ? ` · ${m.transforms.length} ${m.transforms.length === 1 ? "transform" : "transforms"}`
            : ""}
        </span>
      </button>
      {open && (
        <div className="border-t bg-content-muted px-4 py-3 pl-11 text-[13px]">
          <p className="mb-2 font-mono text-foreground-muted text-xs md:hidden">from {m.mapping}</p>
          {m.identifiers?.length ? (
            <p className="mb-2 text-xs">
              <span className="text-subtle">Matched on </span>
              <span className="font-mono">{m.identifiers.join(", ")}</span>
            </p>
          ) : null}
          <table className="w-full text-left">
            <thead className="sr-only">
              <tr>
                <th scope="col">Destination field</th>
                <th scope="col">Source</th>
              </tr>
            </thead>
            <tbody>
              {fields.map((f) => (
                <tr key={f.name} className="align-top">
                  <td className="w-1/3 py-0.5 pr-4 font-mono text-xs leading-5">{f.name}</td>
                  <td className="py-0.5 font-mono text-foreground-muted text-xs leading-5 [overflow-wrap:anywhere]">
                    {f.mapping ??
                      (f.static !== undefined ? `static ${JSON.stringify(f.static)}` : "")}
                    {f.reference && <span className="text-subtle"> → {f.reference}</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </li>
  );
}

function Mapping({ version }: { version: ConfigurationVersion }) {
  const cfg = version.declared_content.configuration;
  const mapping = cfg.schema_mapping ?? [];
  return (
    <div className="flex flex-col gap-6">
      <div className="grid grid-cols-1 items-stretch gap-3 md:grid-cols-[1fr_auto_1fr]">
        <SystemCard side="Source" adapter={cfg.source} version={version} />
        <div className="hidden items-center justify-center md:flex" aria-hidden="true">
          <ArrowRightIcon className="size-5 text-subtle-muted" />
        </div>
        <SystemCard side="Destination" adapter={cfg.destination} version={version} />
      </div>
      <section aria-labelledby="mapping" className="flex flex-col gap-2">
        <SectionHeading id="mapping" title="Mapped kinds" count={mapping.length}>
          Version {version.registry_version}, in declaration order
        </SectionHeading>
        <Card className="overflow-hidden">
          <ul className="divide-y">
            {mapping.map((m, i) => (
              <MappingRow key={`${m.name}-${i}`} m={m} />
            ))}
          </ul>
        </Card>
      </section>
    </div>
  );
}

/* ---------- page ---------- */

function Tabs({
  tab,
  counts,
  configId,
}: {
  tab: ConfigTab;
  counts: Record<ConfigTab, ReactNode>;
  configId: string;
}) {
  const items: [ConfigTab, string][] = [
    ["runs", "Runs"],
    ["versions", "Versions"],
    ["mapping", "Mapping"],
  ];
  return (
    <nav aria-label="Configuration sections" className="flex gap-5 border-b">
      {items.map(([id, label]) => (
        <Link
          key={id}
          to="/configs/$configId"
          params={{ configId }}
          search={{ tab: id }}
          replace
          aria-current={tab === id ? "page" : undefined}
          className={`relative inline-flex h-10 items-center gap-1.5 text-sm transition-colors duration-150 ease-out focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring-halo ${
            tab === id
              ? "text-foreground after:absolute after:inset-x-0 after:bottom-[-1px] after:h-0.5 after:rounded-full after:bg-foreground"
              : "text-foreground-muted hover:text-foreground"
          }`}
        >
          {label}
          <span className="text-subtle text-xs tabular-nums">{counts[id]}</span>
        </Link>
      ))}
    </nav>
  );
}

export function ConfigurationPage({ configId, tab }: { configId: string; tab: ConfigTab }) {
  const config = useQuery(configQuery(configId));
  const versions = useQuery(versionsQuery(configId));
  const runs = useQuery(runsQuery({ config_id: configId }));
  const latestVersion = config.data?.latest_version ?? 1;
  const version = useQuery({
    ...configVersionQuery(configId, latestVersion),
    enabled: Boolean(config.data),
  });
  const [starting, setStarting] = useState<StartRunTarget | null>(null);

  if (config.isPending) {
    return (
      <div className="flex flex-col gap-4 pt-6" aria-busy="true">
        <output className="sr-only">Loading the configuration</output>
        <Bar className="h-4 w-48" />
        <Bar className="h-8 w-96 max-w-full" />
        <Bar className="h-4 w-64" />
        <Bar className="mt-4 h-20 w-full" />
      </div>
    );
  }
  if (!config.data) {
    return (
      <div className="pt-8">
        <ErrorPanel error={config.error} onRetry={() => config.refetch()} />
      </div>
    );
  }

  const name = config.data.name ?? configId;
  const runItems = runs.data?.items ?? [];
  const latestRun = runItems[0];
  const lastApplied = runItems.find((r) => r.phase === "applied");
  const cfg = version.data?.declared_content.configuration;
  const source = cfg ? describeSystem(cfg.source) : undefined;
  const destination = cfg ? describeSystem(cfg.destination) : undefined;
  const branch = latestRun?.destination_branch ?? destination?.branch;
  const start = (v = latestVersion) =>
    setStarting({ configId, name, version: v, branch: branch ?? undefined });

  return (
    <>
      <header className="flex flex-col gap-4 pt-6 pb-6">
        <nav aria-label="Breadcrumb" className="text-foreground-muted text-sm">
          <ol className="flex flex-wrap items-center gap-1.5">
            <li>
              <Link to="/" className="rounded-sm hover:text-foreground hover:underline">
                Configurations
              </Link>
            </li>
            <li aria-hidden="true" className="text-border-strong">
              /
            </li>
            <li aria-current="page" className="[overflow-wrap:anywhere]">
              {name}
            </li>
          </ol>
        </nav>
        <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-4">
          <div className="flex min-w-0 flex-col gap-2">
            <h1 className="text-balance font-semibold text-2xl tracking-tight [overflow-wrap:anywhere]">
              {name}
            </h1>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-foreground-muted">
              {source && destination ? (
                <span className="inline-flex items-center gap-1.5">
                  {source.label}
                  <ArrowRightIcon />
                  <span className="sr-only">to</span>
                  {destination.label}
                </span>
              ) : (
                <Bar className="h-4 w-32" />
              )}
              <span className="tabular-nums">Version {latestVersion}</span>
              {cfg && <KindsCount kinds={mappedKinds(cfg.schema_mapping)} />}
              <span className="inline-flex items-center gap-1 font-mono text-xs">
                {configId}
                <CopyButton value={configId} label="Copy configuration ID" />
              </span>
            </div>
          </div>
          <Button variant="primary" size="sm" onPress={() => start()}>
            <PlusIcon />
            Start run
          </Button>
        </div>
      </header>

      <div className="flex flex-col gap-8">
        <section aria-labelledby="latest" className="flex flex-col gap-2">
          <SectionHeading id="latest" title="Latest run" />
          {runs.isPending ? (
            <Bar className="h-[72px] w-full" />
          ) : runs.error ? (
            <ErrorPanel error={runs.error} onRetry={() => runs.refetch()} compact />
          ) : (
            <LatestRun
              run={latestRun}
              latestVersion={latestVersion}
              createdAt={config.data.created_at}
              lastApplied={lastApplied}
              onStart={() => start()}
            />
          )}
        </section>

        <div className="flex flex-col gap-4">
          <Tabs
            tab={tab}
            configId={configId}
            counts={{
              runs: runs.data ? `${runItems.length}${runs.data.next_cursor ? "+" : ""}` : "",
              versions: versions.data?.items.length ?? "",
              mapping: cfg ? (cfg.schema_mapping ?? []).length : "",
            }}
          />
          {tab === "runs" &&
            (runs.error ? (
              <ErrorPanel error={runs.error} onRetry={() => runs.refetch()} compact />
            ) : runs.isPending ? (
              <Bar className="h-40 w-full" />
            ) : runItems.length === 0 ? (
              <EmptyPanel title="No runs yet">
                <p className="max-w-[52ch] text-pretty text-foreground-muted">
                  A plan reads both systems and lists what would change. Nothing is written until
                  you apply it.
                </p>
                <Button variant="outline" size="sm" onPress={() => start()}>
                  Create the first plan
                </Button>
              </EmptyPanel>
            ) : (
              <RunsTable runs={runItems} />
            ))}
          {tab === "versions" &&
            (versions.error ? (
              <ErrorPanel error={versions.error} onRetry={() => versions.refetch()} compact />
            ) : versions.data ? (
              <Versions versions={versions.data.items} onStart={start} />
            ) : (
              <Bar className="h-40 w-full" />
            ))}
          {tab === "mapping" &&
            (version.error ? (
              <ErrorPanel error={version.error} onRetry={() => version.refetch()} compact />
            ) : version.data ? (
              <Mapping version={version.data} />
            ) : (
              <Bar className="h-40 w-full" />
            ))}
        </div>
      </div>
      {starting && <StartRunDialog target={starting} onClose={() => setStarting(null)} />}
    </>
  );
}
