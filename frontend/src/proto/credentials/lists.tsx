/* PROTOTYPE (spec 027, T074). The three versions of the credential list. */
import { Button, Card, Menu, MenuItem, MenuTrigger, Popover } from "@infrahub/ui";
import { Fragment, type ReactNode, useId, useMemo, useState } from "react";
import { cn } from "tailwind-variants";

import type { Credential } from "#/api/types";
import { CODE_CHIP, FOCUS, FOCUS_INSET, FOCUS_RAC } from "#/components/classes";
import { CopyButton, FilterTabs, SearchField } from "#/components/page";
import { DateText } from "#/features/accounts/shared";
import { formatRelative } from "#/lib/time";

import { FIELD_SETS, type FieldSetId, fieldSetOf, fieldsLabel } from "./data";
import { ACTION_LABEL, type Action, type ListProps } from "./shared";

const ACTIONS: Action[] = ["value", "describe", "delete"];

const NAME = "font-medium font-mono text-[13px] [overflow-wrap:anywhere]";

/** A description cut after two lines, with a button that shows the rest when there is more. */
function ClampedText({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  if (!text) return <p className="text-subtle">No description</p>;
  const long = text.length > 110;
  return (
    <div className="flex flex-col items-start gap-1">
      <p
        id={id}
        className={cn(
          "text-pretty text-foreground-muted [overflow-wrap:anywhere]",
          long && !open && "line-clamp-2"
        )}
      >
        {text}
      </p>
      {long && (
        <button
          type="button"
          aria-expanded={open}
          aria-controls={id}
          onClick={() => setOpen(!open)}
          className={cn(
            "relative rounded-sm font-medium text-accent text-xs underline-offset-2 before:absolute before:-inset-x-1 before:-inset-y-2 hover:underline",
            FOCUS
          )}
        >
          {open ? "Show less" : "Show all"}
        </button>
      )}
    </div>
  );
}

/** The "Actions" menu of one credential, as on the Accounts page. */
function ActionsMenu({
  credential,
  onAction,
}: {
  credential: Credential;
  onAction: ListProps["onAction"];
}) {
  return (
    <MenuTrigger>
      <Button
        size="xs"
        variant="outline"
        className={cn(FOCUS_RAC, "pointer-coarse:min-h-11")}
        aria-label={`Actions for ${credential.name}`}
        data-actions-for={credential.name}
      >
        Actions
      </Button>
      <Popover placement="bottom end" className="bg-content">
        <Menu
          aria-label={`Actions for ${credential.name}`}
          onAction={(key) => onAction(key as Action, credential)}
          className="min-w-44"
        >
          {ACTIONS.map((action) => (
            <MenuItem
              key={action}
              id={action}
              textValue={ACTION_LABEL[action]}
              className={cn(
                "pointer-coarse:min-h-11",
                action === "delete" ? "text-danger" : "text-foreground"
              )}
            >
              {ACTION_LABEL[action]}
            </MenuItem>
          ))}
        </Menu>
      </Popover>
    </MenuTrigger>
  );
}

/* ---------- 1. Table: the six columns of the contract, as on the Accounts page ---------- */

const TH = "h-9 px-4 text-left font-medium text-subtle text-xs whitespace-nowrap";
const TD = "px-4 py-3 align-top";

export function TableList({ credentials, administrator, onAction, slot }: ListProps) {
  const columns = administrator ? 7 : 6;
  const slotRow = (
    <tr>
      <td colSpan={columns} className="bg-content-muted/50 p-0">
        {/* Stays in view when the table scrolls sideways on a narrow window. */}
        <div className="sticky left-0 max-w-[calc(100vw-2rem)]">{slot?.node}</div>
      </td>
    </tr>
  );
  return (
    <Card className="relative overflow-x-auto bg-content bg-none">
      <table aria-label="Credentials" className="w-full text-sm">
        <thead className="bg-content-muted">
          <tr>
            <th scope="col" className={TH}>
              Name
            </th>
            <th scope="col" className={TH}>
              Description
            </th>
            <th scope="col" className={TH}>
              Fields set
            </th>
            <th scope="col" className={cn(TH, "text-right")}>
              Revision
            </th>
            <th scope="col" className={TH}>
              Updated by
            </th>
            <th scope="col" className={TH}>
              Updated at
            </th>
            {administrator && (
              <th scope="col" className={TH}>
                <span className="sr-only">Actions</span>
              </th>
            )}
          </tr>
        </thead>
        <tbody className="divide-y">
          {slot && slot.at === null && slotRow}
          {credentials.map((credential) =>
            slot && slot.at === credential.name ? (
              <Fragment key={credential.name}>{slotRow}</Fragment>
            ) : (
              <tr key={credential.name}>
                <th scope="row" className={cn(TD, "min-w-44 max-w-72 text-left")}>
                  <span className={NAME}>{credential.name}</span>
                </th>
                <td className={cn(TD, "min-w-56 max-w-md")}>
                  <ClampedText text={credential.description} />
                </td>
                <td className={cn(TD, "whitespace-nowrap")}>{fieldsLabel(credential.fields)}</td>
                <td className={cn(TD, "text-right tabular-nums")}>{credential.revision}</td>
                <td className={cn(TD, "min-w-32 max-w-48 [overflow-wrap:anywhere]")}>
                  {credential.updated_by}
                </td>
                <td className={cn(TD, "whitespace-nowrap")}>
                  <DateText iso={credential.updated_at} />
                </td>
                {administrator && (
                  <td className={cn(TD, "text-right")}>
                    <ActionsMenu credential={credential} onAction={onAction} />
                  </td>
                )}
              </tr>
            )
          )}
        </tbody>
      </table>
    </Card>
  );
}

/* ---------- 2. Rows that open: one line per credential, the rest under the line ---------- */

function Facts({ credential }: { credential: Credential }) {
  const facts: [string, ReactNode][] = [
    ["Fields set", fieldsLabel(credential.fields)],
    [
      "Revision",
      <span key="r" className="tabular-nums">
        {credential.revision}
      </span>,
    ],
    ["Updated by", credential.updated_by],
    ["Updated at", <DateText key="u" iso={credential.updated_at} />],
    ["Created by", credential.created_by],
    ["Created at", <DateText key="c" iso={credential.created_at} />],
  ];
  return (
    <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-[13px] sm:grid-cols-3">
      {facts.map(([term, value]) => (
        <div key={term} className="flex min-w-0 flex-col gap-0.5">
          <dt className="text-subtle text-xs">{term}</dt>
          <dd className="min-w-0 [overflow-wrap:anywhere]">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function RowsList({ credentials, administrator, onAction }: ListProps) {
  const [open, setOpen] = useState<Set<string>>(() => new Set());
  const toggle = (name: string) =>
    setOpen((current) => {
      const next = new Set(current);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  return (
    <Card className="bg-content bg-none p-0">
      <ul aria-label="Credentials" className="divide-y">
        {credentials.map((credential) => {
          const isOpen = open.has(credential.name);
          const panelId = `credential-panel-${credential.name}`;
          return (
            <li key={credential.name}>
              <h2>
                <button
                  type="button"
                  aria-expanded={isOpen}
                  aria-controls={panelId}
                  onClick={() => toggle(credential.name)}
                  className={cn(
                    "grid min-h-12 w-full grid-cols-[auto_minmax(0,1fr)] items-center gap-x-3 gap-y-0.5 px-4 py-2.5 text-left transition-colors duration-150 ease-out hover:bg-content-muted/60 motion-reduce:transition-none sm:grid-cols-[auto_minmax(0,16rem)_minmax(0,1fr)_auto]",
                    FOCUS_INSET
                  )}
                >
                  <svg
                    aria-hidden="true"
                    viewBox="0 0 16 16"
                    className={cn(
                      "size-4 text-subtle transition-transform duration-150 ease-out motion-reduce:transition-none",
                      isOpen && "rotate-90"
                    )}
                  >
                    <path
                      d="M6 4l4 4-4 4"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="1.5"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                  </svg>
                  <span className={cn(NAME, "min-w-0")}>{credential.name}</span>
                  <span
                    className={cn(
                      "col-start-2 min-w-0 truncate text-[13px] sm:col-start-auto",
                      credential.description ? "text-foreground-muted" : "text-subtle"
                    )}
                  >
                    {credential.description || "No description"}
                  </span>
                  <span className="col-start-2 whitespace-nowrap text-subtle text-xs sm:col-start-auto">
                    {fieldsLabel(credential.fields)} ·{" "}
                    <span className="tabular-nums">revision {credential.revision}</span>
                  </span>
                </button>
              </h2>
              <div
                id={panelId}
                hidden={!isOpen}
                className="flex flex-col gap-4 px-4 pt-1 pb-4 pl-11 text-sm"
              >
                {credential.description && (
                  <p className="max-w-[75ch] text-pretty text-foreground-muted [overflow-wrap:anywhere]">
                    {credential.description}
                  </p>
                )}
                <Facts credential={credential} />
                <div className="flex flex-wrap items-center gap-2">
                  <code className={CODE_CHIP}>credential: {credential.name}</code>
                  <CopyButton value={`credential: ${credential.name}`} label="Copy" />
                </div>
                {administrator && (
                  <div
                    className="flex flex-wrap gap-2"
                    data-actions-for={credential.name}
                    tabIndex={-1}
                  >
                    {ACTIONS.map((action) => (
                      <Button
                        key={action}
                        size="xs"
                        variant={action === "delete" ? "ghost" : "outline"}
                        className={cn(
                          FOCUS_RAC,
                          "pointer-coarse:min-h-11",
                          action === "delete" && "text-danger"
                        )}
                        aria-label={`${ACTION_LABEL[action]}: ${credential.name}`}
                        onPress={() => onAction(action, credential)}
                      >
                        {ACTION_LABEL[action]}
                      </Button>
                    ))}
                  </div>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </Card>
  );
}

/* ---------- 3. Find: a search field, the fields set as a filter, two orders ---------- */

type Order = "name" | "changed";

export function FindList({ credentials, administrator, onAction }: ListProps) {
  const [query, setQuery] = useState("");
  const [set, setSet] = useState<"all" | FieldSetId>("all");
  const [order, setOrder] = useState<Order>("name");
  const now = Date.now();

  const counts = useMemo(() => {
    const c: Record<string, number> = { all: credentials.length };
    for (const s of FIELD_SETS) c[s.id] = 0;
    for (const credential of credentials) c[fieldSetOf(credential.fields)] += 1;
    return c;
  }, [credentials]);

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    const found = credentials.filter(
      (credential) =>
        (set === "all" || fieldSetOf(credential.fields) === set) &&
        (!q ||
          credential.name.includes(q) ||
          credential.description.toLowerCase().includes(q) ||
          credential.updated_by.toLowerCase().includes(q))
    );
    return order === "name"
      ? found
      : [...found].sort((a, b) => b.updated_at.localeCompare(a.updated_at));
  }, [credentials, query, set, order]);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <div className="min-w-0 flex-[1_1_18rem]">
          <SearchField
            value={query}
            onChange={setQuery}
            label="Find a credential"
            placeholder="Find by name, description or account"
            count={shown.length}
            noun={["credential", "credentials"]}
          />
        </div>
        <FilterTabs
          label="Order"
          value={order}
          onChange={setOrder}
          options={[
            { id: "name", label: "By name" },
            { id: "changed", label: "Last changed first" },
          ]}
        />
      </div>
      <FilterTabs
        label="Fields set"
        value={set}
        onChange={setSet}
        options={[
          { id: "all", label: "All", count: counts.all },
          ...FIELD_SETS.map((s) => ({ id: s.id, label: s.label, count: counts[s.id] })),
        ]}
      />
      {shown.length === 0 ? (
        <Card className="flex flex-col items-start gap-2 bg-content bg-none px-4 py-6 text-sm">
          <p className="font-medium">No credential matches</p>
          <p className="text-foreground-muted">
            Nothing in the {credentials.length} credentials matches this search and this fields set.
          </p>
          <Button
            size="xs"
            variant="outline"
            className={FOCUS_RAC}
            onPress={() => {
              setQuery("");
              setSet("all");
            }}
          >
            Clear the search
          </Button>
        </Card>
      ) : (
        <Card className="bg-content bg-none p-0">
          <ul aria-label="Credentials" className="divide-y">
            {shown.map((credential) => (
              <li
                key={credential.name}
                className="grid grid-cols-[minmax(0,1fr)_auto] items-start gap-x-4 gap-y-1 px-4 py-3 text-sm"
              >
                <div className="flex min-w-0 flex-col gap-0.5">
                  <span className={NAME}>{credential.name}</span>
                  <p
                    className={cn(
                      "line-clamp-1 text-[13px] [overflow-wrap:anywhere]",
                      credential.description ? "text-foreground-muted" : "text-subtle"
                    )}
                    title={credential.description || undefined}
                  >
                    {credential.description || "No description"}
                  </p>
                  <p className="text-subtle text-xs">
                    {fieldsLabel(credential.fields)} ·{" "}
                    <span className="tabular-nums">revision {credential.revision}</span> · changed{" "}
                    <time dateTime={credential.updated_at} title={credential.updated_at}>
                      {formatRelative(credential.updated_at, now)}
                    </time>{" "}
                    by <span className="[overflow-wrap:anywhere]">{credential.updated_by}</span>
                  </p>
                </div>
                {administrator && <ActionsMenu credential={credential} onAction={onAction} />}
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}
