import {
  Card,
  CardContent,
  CardHeader,
  Select,
  SelectItem,
  SelectList,
  SelectTrigger,
} from "@infrahub/ui";
import { useNavigate } from "@tanstack/react-router";
import { type ReactNode, useState } from "react";

import type { ConfigurationVersion, DeclaredAdapter, DeclaredMapping } from "#/api/types";
import { ArrowRightIcon, ChevronRightIcon } from "#/components/icons";
import { FOCUS, FOCUS_INSET, SectionHeading, textAction } from "#/components/page";
import { BranchCode } from "#/components/run-status";
import { fmt } from "#/domain/run-state";
import { describeSystem, mappedKinds } from "#/domain/systems";

/** Words for the operations in `FILTERS_OPERATIONS` (infrahub_sync/__init__.py). */
const OP_WORDS: Record<string, string> = {
  "==": "equals",
  "!=": "does not equal",
  ">": "is greater than",
  "<": "is less than",
  ">=": "is at least",
  "<=": "is at most",
  in: "is one of",
  "not in": "is not one of",
  contains: "contains",
  "not contains": "does not contain",
  is_empty: "is empty",
  is_not_empty: "is not empty",
  regex: "matches the pattern",
  is_ip_within: "is within",
};

type Filter = NonNullable<DeclaredMapping["filters"]>[number];

function Operation({ op }: { op: string }) {
  return Object.hasOwn(OP_WORDS, op) ? OP_WORDS[op] : <code className="font-mono">{op}</code>;
}

function FilterWords({ filter }: { filter: Filter }) {
  return (
    <>
      <code className="font-mono">{filter.field}</code> <Operation op={filter.operation} />
      {filter.value != null && (
        <>
          {" "}
          <code className="font-mono">{JSON.stringify(filter.value)}</code>
        </>
      )}
    </>
  );
}

const filterKey = (f: Filter) => `${f.field}|${f.operation}|${JSON.stringify(f.value)}`;

/** The first filter of `m` that some other mapping of the same kind lacks, so two rows read apart. */
function distinctFilter(m: DeclaredMapping, all: DeclaredMapping[]): Filter | undefined {
  const siblings = all.filter((o) => o !== m && o.name === m.name);
  if (siblings.length === 0) return undefined;
  return (m.filters ?? []).find((f) =>
    siblings.some((s) => !(s.filters ?? []).some((g) => filterKey(g) === filterKey(f)))
  );
}

const plural = (n: number, one: string, many: string) => `${fmt(n)} ${n === 1 ? one : many}`;

/** An identifier or a dotted path that wraps only after its underscores and dots. */
function withBreaks(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  let offset = 0;
  for (const part of text.split(/(?<=[._])/)) {
    offset += part.length;
    out.push(part);
    if (offset < text.length) out.push(<wbr key={`wbr-${offset}`} />);
  }
  return out;
}

const settingText = (adapter: DeclaredAdapter, key: string) => {
  const v = adapter.settings?.[key];
  return typeof v === "string" && v.trim() ? v.trim() : undefined;
};

function Credential({
  adapter,
  version,
}: {
  adapter: DeclaredAdapter;
  version: ConfigurationVersion;
}) {
  const token = adapter.settings?.token as { $credential?: unknown } | undefined;
  const ref = typeof token?.$credential === "string" ? token.$credential : undefined;
  if (!ref) return null;
  const record = version.declared_content.credentials?.[ref];
  return (
    <>
      <dt className="text-subtle max-sm:mt-1.5">Credential</dt>
      <dd className="min-w-0">
        <span className="block font-mono text-xs leading-5">{ref}</span>
        {record && (
          <span className="block text-pretty text-subtle text-xs">
            {record.provider === "env" ? (
              <>
                The Sync worker reads it from the environment variable{" "}
                <code className="font-mono">{withBreaks(record.identifier)}</code> when a run
                starts.
              </>
            ) : (
              <>
                Read from {record.provider} as{" "}
                <code className="font-mono">{withBreaks(record.identifier)}</code>
              </>
            )}
          </span>
        )}
      </dd>
    </>
  );
}

function SystemCard({
  side,
  adapter,
  version,
}: {
  side: "Source" | "Destination";
  adapter: DeclaredAdapter;
  version: ConfigurationVersion;
}) {
  const s = describeSystem(adapter);
  const url = settingText(adapter, "url") ?? settingText(adapter, "base_url");
  const branchNotDeclared =
    side === "Destination" && !s.branch && adapter.name.toLowerCase() === "infrahub";
  return (
    <Card className="min-w-0">
      <CardHeader>
        <p className="font-normal text-subtle text-xs">{side}</p>
        <p className="font-semibold text-sm">{s.label}</p>
      </CardHeader>
      <CardContent className="p-3">
        <dl className="grid grid-cols-1 gap-y-0.5 text-[13px] sm:grid-cols-[auto_minmax(0,1fr)] sm:gap-x-4 sm:gap-y-1">
          <dt className="text-subtle">Adapter</dt>
          <dd className="font-mono text-xs leading-5">{adapter.name}</dd>
          {url && (
            <>
              <dt className="text-subtle max-sm:mt-1.5">Address</dt>
              <dd className="break-words font-mono text-xs leading-5">{url}</dd>
            </>
          )}
          {side === "Destination" && (s.branch || branchNotDeclared) && (
            <>
              <dt className="text-subtle max-sm:mt-1.5">Branch</dt>
              <dd className="min-w-0">
                {s.branch ? (
                  <BranchCode branch={s.branch} />
                ) : (
                  <span className="text-pretty text-foreground-muted">
                    Not declared in this version. Each run records the branch it used.
                  </span>
                )}
              </dd>
            </>
          )}
          <Credential adapter={adapter} version={version} />
        </dl>
      </CardContent>
    </Card>
  );
}

function MappingRow({
  m,
  all,
  source,
  destination,
  open,
  onToggle,
}: {
  m: DeclaredMapping;
  all: DeclaredMapping[];
  source: string;
  destination: string;
  open: boolean;
  onToggle: () => void;
}) {
  const fields = m.fields ?? [];
  const filters = m.filters ?? [];
  const transforms = m.transforms ?? [];
  const distinct = distinctFilter(m, all);
  const where = distinct
    ? `where ${distinct.field} ${Object.hasOwn(OP_WORDS, distinct.operation) ? OP_WORDS[distinct.operation] : distinct.operation}${distinct.value != null ? ` ${JSON.stringify(distinct.value)}` : ""}`
    : "";
  const fromText = `from ${m.mapping}${where ? ` · ${where}` : ""}`;
  const computed = new Set(transforms.map((t) => t.field));
  return (
    <li>
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className={`grid w-full cursor-pointer grid-cols-[16px_minmax(0,1fr)] items-center gap-x-3 gap-y-0.5 px-4 py-2.5 text-left text-[13px] transition-colors duration-150 ease-out hover:bg-content-muted motion-reduce:transition-none md:grid-cols-[16px_minmax(0,16rem)_minmax(0,1fr)_auto] ${FOCUS_INSET}`}
      >
        <ChevronRightIcon
          className={`size-3.5 text-subtle transition-transform duration-150 ease-out motion-reduce:transition-none ${open ? "rotate-90" : ""}`}
        />
        <span className="min-w-0 truncate font-medium font-mono" title={m.name}>
          {m.name}
        </span>
        <span
          className="hidden min-w-0 font-mono text-foreground-muted text-xs md:flex"
          title={fromText}
        >
          <span className="min-w-[4ch] truncate">from {m.mapping}</span>
          {where && <span className="max-w-[75%] shrink-0 truncate">&nbsp;· {where}</span>}
        </span>
        {where && (
          <span className="col-start-2 truncate font-mono text-foreground-muted text-xs md:hidden">
            {where}
          </span>
        )}
        <span className="col-start-2 whitespace-nowrap text-left text-subtle text-xs tabular-nums max-md:whitespace-normal md:col-start-auto md:justify-self-end md:text-right">
          {plural(fields.length, "field", "fields")}
          {filters.length > 0 && ` · ${plural(filters.length, "filter", "filters")}`}
          {transforms.length > 0 &&
            ` · ${plural(transforms.length, "computed field", "computed fields")}`}
        </span>
      </button>
      {open && (
        <div className="border-t bg-content-muted px-4 py-3 text-[13px] md:pl-11">
          <p className="mb-2 font-mono text-foreground-muted text-xs [overflow-wrap:anywhere] md:hidden">
            {fromText}
          </p>
          {m.identifiers?.length ? (
            <p className="mb-2 text-xs">
              <span className="text-subtle">Matched on </span>
              <span className="font-mono">{m.identifiers.join(", ")}</span>
            </p>
          ) : null}
          {fields.length > 0 && (
            <table className="max-w-3xl text-left">
              <thead>
                <tr className="font-medium text-[11px] text-subtle">
                  <th scope="col" className="pr-3 pb-1 font-medium">
                    {source} field
                  </th>
                  <th scope="col" className="w-6 pb-1">
                    <span className="sr-only">to</span>
                  </th>
                  <th scope="col" className="pb-1 font-medium">
                    {destination} attribute or relationship
                  </th>
                </tr>
              </thead>
              <tbody>
                {fields.map((f) => (
                  <tr key={f.name} className="align-top">
                    <td className="py-0.5 pr-3 font-mono text-xs leading-5 break-words">
                      {/* The column fits its longest path up to this width, so short paths sit next to their arrow. */}
                      <div className="max-w-72">
                        {f.mapping ? (
                          <>
                            <span className="text-foreground-muted">{withBreaks(f.mapping)}</span>
                            {computed.has(f.mapping) && (
                              <span className="font-sans text-subtle"> (computed)</span>
                            )}
                          </>
                        ) : f.static != null ? (
                          <>
                            <span className="font-sans text-foreground-muted">Fixed value </span>
                            <code>{JSON.stringify(f.static)}</code>
                          </>
                        ) : (
                          <span className="font-sans text-subtle">Not declared</span>
                        )}
                      </div>
                    </td>
                    <td className="py-0.5">
                      <ArrowRightIcon className="mt-1 size-3 text-subtle-muted" />
                    </td>
                    <td className="py-0.5 font-mono text-foreground text-xs leading-5 break-words">
                      {withBreaks(f.name)}
                      {f.reference && (
                        <code
                          className="ml-1.5 inline-block max-w-full truncate rounded bg-content-strong px-1 py-px align-top text-[0.85em] text-foreground-muted [overflow-wrap:normal]"
                          title={f.reference}
                        >
                          <span className="sr-only">peer kind </span>
                          {f.reference}
                        </code>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {filters.length > 0 && (
            <>
              <h3 className="mt-3 mb-1 text-subtle text-xs">Only source records where</h3>
              <ul className="flex flex-col gap-0.5 text-xs">
                {filters.map((f) => (
                  <li key={filterKey(f)}>
                    <FilterWords filter={f} />
                  </li>
                ))}
              </ul>
            </>
          )}
          {transforms.length > 0 && (
            <>
              <h3 className="mt-3 mb-1 text-subtle text-xs">Computed source fields</h3>
              <div className="flex flex-col gap-2">
                {transforms.map((t) => (
                  <div key={t.field}>
                    <p className="font-mono text-xs">{t.field} =</p>
                    <pre className="mt-0.5 max-w-3xl whitespace-pre-wrap break-words rounded-md border bg-content px-2 py-1 font-mono text-[11px] text-foreground-muted leading-5">
                      {t.expression}
                    </pre>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      )}
    </li>
  );
}

const andList = (names: string[]) =>
  names.length <= 1
    ? (names[0] ?? "")
    : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;

function mappingSentence(version: ConfigurationVersion): string {
  const cfg = version.declared_content.configuration;
  const mapping = cfg.schema_mapping ?? [];
  const kinds = mappedKinds(mapping);
  const counts = new Map<string, number>();
  for (const m of mapping) counts.set(m.name, (counts.get(m.name) ?? 0) + 1);
  const repeated = [...counts].filter(([, n]) => n > 1);
  const parts = [`Version ${version.registry_version}, in declaration order.`];
  if (repeated.length === 1) {
    const [name, n] = repeated[0];
    parts.push(
      `${plural(mapping.length, "mapping", "mappings")} for ${plural(kinds.length, "kind", "kinds")}: ${name} is mapped ${n === 2 ? "twice" : `${fmt(n)} times`}.`
    );
  } else if (repeated.length > 1) {
    parts.push(
      `${plural(mapping.length, "mapping", "mappings")} for ${plural(kinds.length, "kind", "kinds")}: ${andList(repeated.map(([name]) => name))} are mapped more than once.`
    );
  }
  parts.push(
    cfg.order?.length
      ? `Write order: ${cfg.order.join(", ")}.`
      : "Write order is computed from schema references."
  );
  return parts.join(" ");
}

/** The declared source, destination and mappings of one registered version. */
export function Mapping({
  configId,
  version,
  versionNumbers,
  latestVersion,
}: {
  configId: string;
  version: ConfigurationVersion;
  /** Every registered version, newest first; a selector appears when there is more than one. */
  versionNumbers: number[];
  latestVersion?: number;
}) {
  const navigate = useNavigate();
  const cfg = version.declared_content.configuration;
  const mapping = cfg.schema_mapping ?? [];
  const source = describeSystem(cfg.source);
  const destination = describeSystem(cfg.destination);
  const [open, setOpen] = useState<{ version: number; rows: Set<number> }>({
    version: version.registry_version,
    rows: new Set(),
  });
  const rows = open.version === version.registry_version ? open.rows : new Set<number>();
  const allOpen = mapping.length > 0 && rows.size === mapping.length;
  const setRows = (next: Set<number>) => setOpen({ version: version.registry_version, rows: next });
  const toggle = (i: number) => {
    const next = new Set(rows);
    if (!next.delete(i)) next.add(i);
    setRows(next);
  };

  return (
    <div className="flex flex-col gap-6">
      <div className="grid grid-cols-1 items-stretch gap-3 md:grid-cols-[minmax(0,1fr)_40px_minmax(0,1fr)]">
        <SystemCard side="Source" adapter={cfg.source} version={version} />
        <div className="hidden items-center justify-center md:flex" aria-hidden="true">
          <ArrowRightIcon className="size-4 text-subtle-muted" />
        </div>
        <SystemCard side="Destination" adapter={cfg.destination} version={version} />
      </div>
      <section aria-labelledby="mapping" className="flex flex-col gap-2">
        <SectionHeading
          id="mapping"
          title="Mappings"
          actions={
            <div className="flex items-center gap-3">
              {mapping.length > 0 && (
                <button
                  type="button"
                  onClick={() => setRows(allOpen ? new Set() : new Set(mapping.map((_, i) => i)))}
                  className={textAction(false)}
                >
                  {allOpen ? "Collapse all" : "Expand all"}
                </button>
              )}
              {versionNumbers.length > 1 && (
                <Select
                  aria-label="Version"
                  value={version.registry_version}
                  onChange={(key) => {
                    const n = Number(key);
                    void navigate({
                      to: "/configs/$configId",
                      params: { configId },
                      search: (s) => ({ ...s, version: n === latestVersion ? undefined : n }),
                      replace: true,
                      resetScroll: false,
                    });
                  }}
                  className="w-32"
                >
                  <SelectTrigger size="sm" className={FOCUS} />
                  <SelectList items={versionNumbers.map((n) => ({ id: n }))}>
                    {(item) => (
                      <SelectItem id={item.id} textValue={`Version ${item.id}`}>
                        Version {item.id}
                      </SelectItem>
                    )}
                  </SelectList>
                </Select>
              )}
            </div>
          }
        >
          {mappingSentence(version)}
        </SectionHeading>
        <Card className="overflow-hidden">
          <ul className="divide-y">
            {mapping.map((m, i) => (
              <MappingRow
                key={i}
                m={m}
                all={mapping}
                source={source.label}
                destination={destination.label}
                open={rows.has(i)}
                onToggle={() => toggle(i)}
              />
            ))}
          </ul>
        </Card>
      </section>
    </div>
  );
}
