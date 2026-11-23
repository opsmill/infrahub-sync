/*
 * PROTOTYPE (spec 027, T074). The harness: a copy of the page frame, the picker of the emil-prototype
 * skill, the screen choice and the sample switches. It reads its state from the address and sends no
 * request. Remove frontend/proto/ and src/proto/credentials once the maintainer has picked one
 * version per screen.
 */
import "./picker.css";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  type ComponentType,
  type ReactNode,
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";
import { cn } from "tailwind-variants";

import { LiveRegion } from "#/components/announcer";
import { FOCUS } from "#/components/classes";
import { Mark } from "#/components/mark";
import { ThemeMenu } from "#/components/theme-menu";

import { type Dataset, type Refusal, resetSample, setRefusal, USED, type UsedSample } from "./data";
import { ConfirmDialog, RowDelete, SheetDelete } from "./deletes";
import { DialogForm, RowForm, SheetForm } from "./forms";
import { FindList, RowsList, TableList } from "./lists";
import { CredentialsPage, type Open, type Placement } from "./page";
import type { Action, DeleteProps, FormProps, ListProps } from "./shared";
import { UsedGrid, UsedInDetails, type UsedProps, UsedTable } from "./used";

type Screen = "list" | "form" | "delete" | "used";

const SCREENS: { id: Screen; label: string; versions: string[] }[] = [
  { id: "list", label: "Credentials page: list", versions: ["Table", "Rows that open", "Find"] },
  {
    id: "form",
    label: "Create and change-value forms",
    versions: ["Dialog", "Side sheet", "In the row"],
  },
  {
    id: "delete",
    label: "Delete confirmation",
    versions: ["Dialog", "In the row", "Guided sheet"],
  },
  {
    id: "used",
    label: "Run details: Credentials used",
    versions: ["Table", "In the details", "Stage grid"],
  },
];

const LISTS: ComponentType<ListProps>[] = [TableList, RowsList, FindList];
const FORMS: [ComponentType<FormProps>, Placement][] = [
  [DialogForm, "overlay"],
  [SheetForm, "overlay"],
  [RowForm, "row"],
];
const DELETES: [ComponentType<DeleteProps>, Placement][] = [
  [ConfirmDialog, "overlay"],
  [RowDelete, "row"],
  [SheetDelete, "overlay"],
];
const USES: ComponentType<UsedProps>[] = [UsedTable, UsedInDetails, UsedGrid];

const LONG_NAME = "aci-fabric-emea-production-read-only-service-account-ams-26-q4";

/** The open states the "Open" switch offers, as they are written in the address. */
const OPEN_CHOICES: [string, string][] = [
  ["none", "Nothing"],
  ["create", "New credential"],
  ["value:netbox-lab", "Change value: netbox-lab"],
  [`value:${LONG_NAME}`, "Change value: the 62-character name"],
  ["delete:netbox-lab", "Delete: netbox-lab (in use)"],
  [`delete:${LONG_NAME}`, "Delete: the 62-character name (in use)"],
  ["delete:nautobot-staging", "Delete: nautobot-staging (not in use)"],
  ["describe:infrahub-main", "Edit description: infrahub-main"],
];

/** The default open state of each screen, so each version shows its own form or confirmation. */
const DEFAULT_OPEN: Record<Screen, string> = {
  list: "none",
  form: "create",
  delete: "delete:netbox-lab",
  used: "none",
};

function parseOpen(text: string): Open | undefined {
  if (text === "create") return { action: "create" };
  const [action, ...rest] = text.split(":");
  const name = rest.join(":");
  if (name && (action === "value" || action === "describe" || action === "delete")) {
    return { action: action as Action, name };
  }
  return undefined;
}

const writeOpen = (open: Open | undefined) =>
  !open ? "none" : open.action === "create" ? "create" : `${open.action}:${open.name}`;

/* ---------- the address: the whole state of the harness ---------- */

type Params = Record<string, string>;

function readParams(): Params {
  return Object.fromEntries(new URLSearchParams(window.location.search));
}

function useParams(): [Params, (next: Params) => void] {
  const [params, setParams] = useState<Params>(readParams);
  const update = useCallback((next: Params) => {
    const url = new URL(window.location.href);
    url.search = new URLSearchParams(
      Object.entries(next).filter(([, value]) => value !== "")
    ).toString();
    window.history.replaceState(null, "", url);
    setParams(next);
  }, []);
  return [params, update];
}

const oneOf = <T extends string>(value: string | undefined, allowed: readonly T[], fallback: T) =>
  allowed.includes(value as T) ? (value as T) : fallback;

/* ---------- the picker: markup and behavior from PICKER.md of the skill ---------- */

function Picker({
  names,
  current,
  onChange,
}: {
  names: string[];
  current: number;
  onChange: (index: number) => void;
}) {
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
    let inner = 0;
    const outer = requestAnimationFrame(() => {
      inner = requestAnimationFrame(() => setReady(true));
    });
    return () => {
      cancelAnimationFrame(outer);
      cancelAnimationFrame(inner);
    };
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      if (target && (/^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName) || target.isContentEditable))
        return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      // An open menu uses the arrow keys itself.
      if (document.querySelector("[role=menu]")) return;
      const num = Number.parseInt(e.key, 10);
      if (num >= 1 && num <= names.length) onChange(num - 1);
      else if (e.key === "ArrowRight") onChange((current + 1) % names.length);
      else if (e.key === "ArrowLeft") onChange((current - 1 + names.length) % names.length);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [current, names, onChange]);

  const nav = (
    <nav
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
  const root = document.getElementById("proto-picker-root");
  return root ? createPortal(nav, root) : nav;
}

/* ---------- a copy of the page frame, with no request ---------- */

const NAV = cn(
  "relative inline-flex h-8 shrink-0 items-center whitespace-nowrap rounded-md px-2 text-foreground-muted text-sm transition-colors duration-150 ease-out hover:bg-highlight hover:text-foreground motion-reduce:transition-none before:absolute before:inset-x-0 before:-inset-y-2",
  FOCUS,
  "data-current:text-foreground data-current:after:absolute data-current:after:inset-x-2 data-current:after:bottom-[-9px] data-current:after:h-0.5 data-current:after:rounded-full data-current:after:bg-foreground",
  "max-sm:h-12 max-sm:px-1.5 max-sm:data-current:after:inset-x-1.5 max-sm:data-current:after:bottom-[-1px]"
);

function Frame({
  section,
  administrator,
  children,
}: {
  section: "credentials" | "runs";
  administrator: boolean;
  children: ReactNode;
}) {
  // The links lead to the real pages, which need a signed-in session and a Sync API.
  const links: [string, string, boolean][] = [
    ["Configurations", "/", true],
    ["Runs", "/runs", true],
    ["Accounts", "/accounts", administrator],
    ["Credentials", "/proto/credentials", true],
  ];
  return (
    <div className="min-h-dvh bg-background text-foreground text-sm antialiased">
      <header className="sticky top-0 z-40 border-b bg-background">
        <div className="mx-auto flex h-12 max-w-[1200px] items-center gap-3 px-4 max-sm:h-auto max-sm:flex-wrap max-sm:gap-y-0 max-sm:pt-2 sm:gap-6">
          <span className="relative -ml-1 inline-flex h-8 shrink-0 items-center gap-2 whitespace-nowrap rounded-md px-1 font-semibold tracking-tight">
            <Mark />
            <span className="max-sm:hidden">Infrahub Sync</span>
          </span>
          <nav
            aria-label="Main"
            className="flex shrink-0 items-center gap-1 max-sm:order-last max-sm:-ml-1.5 max-sm:w-[calc(100%+0.375rem)] max-sm:flex-wrap max-sm:gap-x-1 max-sm:gap-y-0"
          >
            {links
              .filter(([, , shown]) => shown)
              .map(([label, href]) => {
                const current =
                  (label === "Credentials" && section === "credentials") ||
                  (label === "Runs" && section === "runs");
                return (
                  <a
                    key={label}
                    href={href}
                    className={NAV}
                    data-current={current ? "" : undefined}
                    aria-current={current ? "page" : undefined}
                    onClick={(e) => label === "Credentials" && e.preventDefault()}
                  >
                    {label}
                  </a>
                );
              })}
          </nav>
          <div className="ml-auto flex min-w-0 items-center gap-2 max-sm:shrink-0">
            <span className="inline-flex h-8 items-center rounded-md px-2 text-foreground-muted">
              {administrator ? "alice" : "carol"}
            </span>
            <ThemeMenu />
          </div>
        </div>
      </header>
      <LiveRegion />
      <main id="main" tabIndex={-1} className="mx-auto max-w-[1200px] px-4 pb-24 outline-none">
        {children}
      </main>
    </div>
  );
}

/* ---------- the controls of the prototype ---------- */

const SELECT =
  "h-9 pointer-coarse:h-11 rounded-lg border border-input-border bg-input px-2 text-base text-foreground md:text-sm";

function Switch({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: [string, string][];
  onChange: (value: string) => void;
}) {
  return (
    <label className="flex min-w-0 flex-col gap-1 text-subtle text-xs">
      {label}
      <select className={SELECT} value={value} onChange={(e) => onChange(e.target.value)}>
        {options.map(([id, text]) => (
          <option key={id} value={id}>
            {text}
          </option>
        ))}
      </select>
    </label>
  );
}

const RUN_FACTS: [string, string][] = [
  ["Run ID", "run_01JD3M8Q2K7T5W9X4B6C0A1E2F"],
  ["Configuration", "netbox-to-infrahub · version 7"],
  ["Source", "NetBox"],
  ["Destination", "Infrahub"],
  ["Started by", "alice"],
  ["Plan approved by", "bob"],
];

function RunDetailsSample({
  Used,
  sample,
}: {
  Used: ComponentType<UsedProps>;
  sample: UsedSample;
}) {
  return (
    <div className="flex flex-col gap-8 pt-8">
      <h1 className="text-balance font-semibold text-2xl tracking-tight">
        Run of netbox-to-infrahub
      </h1>
      <Used used={USED[sample]} facts={RUN_FACTS} />
      <section aria-labelledby="files" className="flex flex-col gap-3">
        <h2 id="files" className="font-semibold text-base">
          Files
        </h2>
        <p className="rounded-xl border px-4 py-3 text-foreground-muted">
          plan.json · results.json
        </p>
      </section>
    </div>
  );
}

export function CredentialsPrototype() {
  // A query client of its own, whose only queries read the sample data.
  const [client] = useState(() => new QueryClient());
  const [params, setParams] = useParams();
  const screen = SCREENS.find((s) => s.id === params.screen) ?? SCREENS[0];
  const version = Math.min(
    Math.max((Number.parseInt(params.v ?? "1", 10) || 1) - 1, 0),
    screen.versions.length - 1
  );
  const administrator = params.account !== "other";
  const dataset = oneOf<Dataset>(params.list, ["sample", "empty", "loading", "error"], "sample");
  const refusal = oneOf<Refusal>(
    params.answer,
    ["none", "key", "fields", "forbidden", "no-answer"],
    "none"
  );
  const run = oneOf<UsedSample>(
    params.run,
    ["plan-and-apply", "revision-changed", "none"],
    "plan-and-apply"
  );
  const openText = params.open ?? DEFAULT_OPEN[screen.id];
  setRefusal(refusal);

  const set = (patch: Params) => setParams({ ...params, ...patch });

  /*
   * A press on the picker counts, for a dialog or a sheet, as a press outside it, which closes it.
   * While the press is on the picker, a close is ignored, so the next version opens the same form.
   */
  const pickerPress = useRef(false);
  useEffect(() => {
    const down = (e: PointerEvent) => {
      pickerPress.current = Boolean((e.target as HTMLElement | null)?.closest(".proto-picker"));
    };
    const up = () => window.setTimeout(() => (pickerPress.current = false), 0);
    window.addEventListener("pointerdown", down, true);
    window.addEventListener("click", up, true);
    return () => {
      window.removeEventListener("pointerdown", down, true);
      window.removeEventListener("click", up, true);
    };
  }, []);

  const at = (kind: Screen) => (screen.id === kind ? version : 0);
  const [Form, formPlacement] = FORMS[at("form")];
  const [Delete, deletePlacement] = DELETES[at("delete")];

  return (
    <QueryClientProvider client={client}>
      <Frame section={screen.id === "used" ? "runs" : "credentials"} administrator={administrator}>
        <aside
          aria-label="Prototype controls"
          className="mt-4 flex flex-wrap items-end gap-3 rounded-xl border border-dashed px-4 py-3"
        >
          <p className="basis-full text-pretty text-subtle text-xs">
            Prototype of spec 027, sample data only: no request leaves this tab. These controls and
            the dark picker at the bottom are not part of the design. Keys 1 to 3 and the arrow keys
            change the version.
          </p>
          <Switch
            label="Screen"
            value={screen.id}
            options={SCREENS.map((s) => [s.id, s.label])}
            onChange={(id) => setParams({ ...params, screen: id, v: "", open: "" })}
          />
          {screen.id === "used" ? (
            <Switch
              label="Run"
              value={run}
              options={[
                ["plan-and-apply", "Plan and apply, same revisions"],
                ["revision-changed", "Value changed during the run, 62-character name"],
                ["none", "No stored credential"],
              ]}
              onChange={(id) => set({ run: id })}
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
                onChange={(id) => set({ account: id === "other" ? "other" : "" })}
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
                onChange={(id) => set({ list: id })}
              />
              <Switch
                label="Open"
                value={OPEN_CHOICES.some(([id]) => id === openText) ? openText : "none"}
                options={OPEN_CHOICES}
                onChange={(id) => set({ open: id })}
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
                onChange={(id) => set({ answer: id })}
              />
              <button
                type="button"
                className={cn(
                  "h-9 pointer-coarse:h-11 rounded-lg border px-3 text-sm transition-colors duration-150 ease-out hover:bg-content-muted",
                  FOCUS
                )}
                onClick={() => {
                  resetSample();
                  void client.invalidateQueries();
                }}
              >
                Restore the sample data
              </button>
              <p className="basis-full text-pretty text-subtle text-xs">
                Creating a credential named netbox-lab answers 409 credential-exists. Deleting
                netbox-lab, infrahub-main or the 62-character name answers 409 credential-in-use.
              </p>
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
              Form={Form}
              formPlacement={formPlacement}
              Delete={Delete}
              deletePlacement={deletePlacement}
              open={parseOpen(openText)}
              onOpen={(next) => {
                if (!next && pickerPress.current) return;
                set({ open: writeOpen(next) });
              }}
            />
          )}
        </div>
      </Frame>
      <Picker
        names={screen.versions}
        current={version}
        onChange={(index) => set({ v: index === 0 ? "" : String(index + 1) })}
      />
    </QueryClientProvider>
  );
}
