/*
 * PROTOTYPE (spec 027, T074). The harness: the picker of the emil-prototype skill, the screen
 * choice and the sample switches. Remove src/proto/credentials and src/routes/proto.credentials.tsx
 * once the maintainer has picked one version per screen.
 */
import "./picker.css";

import { Card } from "@infrahub/ui";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { type ComponentType, useEffect, useLayoutEffect, useRef, useState } from "react";

import { SectionHeading } from "#/components/page";

import { type Dataset, type Refusal, resetSample, setRefusal, USED, type UsedSample } from "./data";
import { ConfirmDialog, PageConfirm, TypedDialog } from "./deletes";
import { DialogForm, PanelForm, StepsForm } from "./forms";
import { CardList, SplitList, TableList } from "./lists";
import { CredentialsPage } from "./page";
import type { DeleteProps, FormProps, ListProps } from "./shared";
import { UsedByCredential, UsedByStage, type UsedProps, UsedTable } from "./used";

export type Screen = "list" | "form" | "delete" | "used";

export interface ProtoSearch {
  screen?: Screen;
  v?: number;
  account?: "other";
  list?: Exclude<Dataset, "sample">;
  answer?: Exclude<Refusal, "none">;
  run?: Exclude<UsedSample, "plan-and-apply">;
}

const SCREENS: { id: Screen; label: string; versions: string[] }[] = [
  {
    id: "list",
    label: "Credentials page: list",
    versions: ["Table", "Cards", "Names and details"],
  },
  {
    id: "form",
    label: "Create and change-value forms",
    versions: ["Dialog", "On the page", "Two steps"],
  },
  {
    id: "delete",
    label: "Delete confirmation",
    versions: ["Dialog", "Type the name", "On the page"],
  },
  {
    id: "used",
    label: "Run details: Credentials used",
    versions: ["Table", "By stage", "By credential"],
  },
];

const LISTS: ComponentType<ListProps>[] = [TableList, CardList, SplitList];
const FORMS: ComponentType<FormProps>[] = [DialogForm, PanelForm, StepsForm];
const DELETES: ComponentType<DeleteProps>[] = [ConfirmDialog, TypedDialog, PageConfirm];
const USES: ComponentType<UsedProps>[] = [UsedTable, UsedByStage, UsedByCredential];

/** The picker: its markup and behavior follow PICKER.md of the skill. */
function Picker({
  names,
  current,
  onChange,
}: {
  names: string[];
  current: number;
  onChange: (index: number) => void;
}) {
  const navRef = useRef<HTMLElement>(null);
  const highlightRef = useRef<HTMLSpanElement>(null);
  const itemRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const [ready, setReady] = useState(false);

  useLayoutEffect(() => {
    const move = () => {
      const item = itemRefs.current[current];
      const highlight = highlightRef.current;
      if (!item || !highlight) return;
      highlight.style.width = `${item.offsetWidth}px`;
      highlight.style.transform = `translateX(${item.offsetLeft}px)`;
    };
    move();
    window.addEventListener("resize", move);
    return () => window.removeEventListener("resize", move);
  }, [current, names]);

  useEffect(() => {
    const frame = requestAnimationFrame(() => requestAnimationFrame(() => setReady(true)));
    return () => cancelAnimationFrame(frame);
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      if (target && (/^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName) || target.isContentEditable))
        return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      // A dialog or a menu owns the keys while it is open.
      if (document.querySelector("[role=dialog],[role=menu]")) return;
      const num = Number.parseInt(e.key, 10);
      if (num >= 1 && num <= names.length) onChange(num - 1);
      else if (e.key === "ArrowRight") onChange((current + 1) % names.length);
      else if (e.key === "ArrowLeft") onChange((current - 1 + names.length) % names.length);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [current, names, onChange]);

  return (
    <nav
      ref={navRef}
      className="proto-picker"
      aria-label="Prototype variants"
      data-ready={ready ? "" : undefined}
    >
      <span ref={highlightRef} className="proto-picker-highlight" aria-hidden="true" />
      {names.map((name, index) => (
        <button
          key={name}
          ref={(el) => {
            itemRefs.current[index] = el;
          }}
          type="button"
          className="proto-picker-item"
          data-active={index === current ? "" : undefined}
          aria-current={index === current ? "true" : undefined}
          onClick={() => onChange(index)}
        >
          {name}
        </button>
      ))}
    </nav>
  );
}

const SELECT =
  "h-9 pointer-coarse:h-11 rounded-lg border border-input-border bg-input px-2 text-base md:text-sm";

function Switch<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: [T, string][];
  onChange: (value: T) => void;
}) {
  return (
    <label className="flex flex-col gap-1 text-subtle text-xs">
      {label}
      <select
        className={SELECT}
        value={value}
        onChange={(e) => onChange(e.target.value as T)}
        style={{ color: "var(--color-foreground)" }}
      >
        {options.map(([id, text]) => (
          <option key={id} value={id}>
            {text}
          </option>
        ))}
      </select>
    </label>
  );
}

/** The run details around the section, so it is judged between its neighbors. */
function RunDetailsSample({
  Used,
  sample,
}: {
  Used: ComponentType<UsedProps>;
  sample: UsedSample;
}) {
  const facts = [
    ["Run ID", "run_01JD3M8Q2K7T5W9X4B6C0A1E2F"],
    ["Configuration", "netbox-to-infrahub · version 7"],
    ["Source", "NetBox"],
    ["Destination", "Infrahub"],
    ["Started by", "alice"],
    ["Plan approved by", "bob"],
  ];
  return (
    <div className="flex flex-col gap-8 pt-8">
      <h1 className="font-semibold text-2xl tracking-tight">Run of netbox-to-infrahub</h1>
      <section aria-labelledby="details" className="flex flex-col gap-3">
        <SectionHeading id="details" title="Details" />
        <Card className="p-4">
          <dl className="grid grid-cols-1 gap-x-8 gap-y-3 text-[13px] sm:grid-cols-2 lg:grid-cols-3">
            {facts.map(([term, value]) => (
              <div key={term} className="flex min-w-0 flex-col gap-0.5">
                <dt className="text-subtle text-xs">{term}</dt>
                <dd className="min-w-0 [overflow-wrap:anywhere]">{value}</dd>
              </div>
            ))}
          </dl>
        </Card>
      </section>
      <Used used={USED[sample]} />
      <section aria-labelledby="files" className="flex flex-col gap-3">
        <SectionHeading id="files" title="Files" count={2} />
        <Card className="p-4 text-foreground-muted text-sm">plan.json · results.json</Card>
      </section>
    </div>
  );
}

export function CredentialsPrototype({
  search,
  onSearch,
}: {
  search: ProtoSearch;
  onSearch: (next: ProtoSearch) => void;
}) {
  // A query client of its own: the sample list never mixes with answers of the Sync API.
  const [client] = useState(() => new QueryClient());
  const screen = SCREENS.find((s) => s.id === search.screen) ?? SCREENS[0];
  const version = Math.min(Math.max((search.v ?? 1) - 1, 0), screen.versions.length - 1);
  const administrator = search.account !== "other";
  const dataset: Dataset = search.list ?? "sample";
  const refusal: Refusal = search.answer ?? "none";
  const run: UsedSample = search.run ?? "plan-and-apply";
  setRefusal(refusal);

  const pick = (index: number) => onSearch({ ...search, v: index === 0 ? undefined : index + 1 });
  const at = (kind: Screen) => (screen.id === kind ? version : 0);

  return (
    <QueryClientProvider client={client}>
      <aside
        aria-label="Prototype controls"
        className="mt-4 flex flex-wrap items-end gap-3 rounded-xl border border-dashed px-4 py-3"
      >
        <p className="basis-full text-subtle text-xs">
          Prototype of spec 027. Sample data only: no request reaches the Sync API. These controls
          and the picker at the bottom are not part of the design. Keys 1 to 3, or the arrow keys,
          change the version.
        </p>
        <Switch
          label="Screen"
          value={screen.id}
          options={SCREENS.map((s) => [s.id, s.label])}
          onChange={(id) =>
            onSearch({ ...search, screen: id === "list" ? undefined : id, v: undefined })
          }
        />
        {screen.id === "used" ? (
          <Switch
            label="Run"
            value={run}
            options={[
              ["plan-and-apply", "Plan and apply, same revisions"],
              ["revision-changed", "Value changed between stages, long name"],
              ["none", "No stored credential"],
            ]}
            onChange={(id) =>
              onSearch({ ...search, run: id === "plan-and-apply" ? undefined : id })
            }
          />
        ) : (
          <>
            <Switch
              label="Signed-in account"
              value={administrator ? "administrator" : "other"}
              options={[
                ["administrator", "Administrator"],
                ["other", "Not an administrator"],
              ]}
              onChange={(id) =>
                onSearch({ ...search, account: id === "other" ? "other" : undefined })
              }
            />
            <Switch
              label="List"
              value={dataset}
              options={[
                ["sample", "Five credentials"],
                ["empty", "No credential"],
                ["loading", "Loading"],
                ["error", "The Sync API does not answer"],
              ]}
              onChange={(id) => onSearch({ ...search, list: id === "sample" ? undefined : id })}
            />
            <Switch
              label="Answer to the next change"
              value={refusal}
              options={[
                ["none", "As the sample data decides"],
                ["key", "503 credential-key-not-configured"],
                ["fields", "422 credential-fields-invalid"],
                ["forbidden", "403 forbidden"],
                ["no-answer", "No answer"],
              ]}
              onChange={(id) => onSearch({ ...search, answer: id === "none" ? undefined : id })}
            />
            <button
              type="button"
              className="h-9 pointer-coarse:h-11 rounded-lg border px-3 text-sm"
              onClick={() => {
                resetSample();
                void client.invalidateQueries();
              }}
            >
              Restore the sample data
            </button>
          </>
        )}
      </aside>
      <div key={`${screen.id}-${version}`} className="pb-28">
        {screen.id === "used" ? (
          <RunDetailsSample Used={USES[version]} sample={run} />
        ) : (
          <CredentialsPage
            administrator={administrator}
            dataset={dataset}
            List={LISTS[at("list")]}
            Form={FORMS[at("form")]}
            Delete={DELETES[at("delete")]}
          />
        )}
      </div>
      <Picker names={screen.versions} current={version} onChange={pick} />
    </QueryClientProvider>
  );
}
