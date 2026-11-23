/* PROTOTYPE (spec 027, T074). The three versions of the credential list. */
import { Button, Card, Menu, MenuItem, MenuTrigger, Popover } from "@infrahub/ui";
import { useState } from "react";
import { cn } from "tailwind-variants";

import type { Credential } from "#/api/types";
import { CODE_CHIP, FOCUS_INSET, FOCUS_RAC } from "#/components/classes";
import { CopyButton } from "#/components/page";
import { DateText } from "#/features/accounts/shared";

import { fieldsLabel } from "./data";
import { ACTION_LABEL, type Action, type ListProps } from "./shared";

const ACTIONS: Action[] = ["value", "describe", "delete"];

const NAME = "font-medium font-mono text-[13px] [overflow-wrap:anywhere]";

function Description({ text, className }: { text: string; className?: string }) {
  return text ? (
    <p className={cn("text-pretty text-foreground-muted [overflow-wrap:anywhere]", className)}>
      {text}
    </p>
  ) : (
    <p className={cn("text-subtle", className)}>No description</p>
  );
}

/* ---------- 1. Table: one row per credential, one Actions menu per row ---------- */

const TH = "h-9 px-4 text-left font-medium text-subtle text-xs whitespace-nowrap";
const TD = "px-4 py-3 align-top";

export function TableList({ credentials, administrator, onAction }: ListProps) {
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
          {credentials.map((credential) => (
            <tr key={credential.name}>
              <th scope="row" className={cn(TD, "min-w-44 max-w-72 text-left")}>
                <span className={NAME}>{credential.name}</span>
              </th>
              <td className={cn(TD, "min-w-56 max-w-md")}>
                <Description text={credential.description} className="line-clamp-2" />
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
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

/* ---------- 2. Cards: the whole description in view, each action a button ---------- */

function ActionButtons({
  credential,
  onAction,
}: {
  credential: Credential;
  onAction: ListProps["onAction"];
}) {
  return (
    <div className="flex flex-wrap gap-2" data-actions-for={credential.name} tabIndex={-1}>
      {ACTIONS.map((action) => (
        <Button
          key={action}
          size="xs"
          variant={action === "delete" ? "ghost" : "outline"}
          className={cn(FOCUS_RAC, "pointer-coarse:min-h-11", action === "delete" && "text-danger")}
          aria-label={`${ACTION_LABEL[action]}: ${credential.name}`}
          onPress={() => onAction(action, credential)}
        >
          {ACTION_LABEL[action]}
        </Button>
      ))}
    </div>
  );
}

export function CardList({ credentials, administrator, onAction }: ListProps) {
  return (
    <ul aria-label="Credentials" className="flex flex-col gap-3">
      {credentials.map((credential) => (
        <li key={credential.name}>
          <Card className="flex flex-col gap-3 bg-content bg-none p-4 text-sm">
            <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
              <h2 className={NAME}>{credential.name}</h2>
              <p className="text-subtle text-xs">
                {fieldsLabel(credential.fields)} ·{" "}
                <span className="tabular-nums">revision {credential.revision}</span>
              </p>
            </div>
            <Description text={credential.description} className="max-w-[80ch]" />
            <div className="flex flex-wrap items-end justify-between gap-x-4 gap-y-3">
              <p className="text-subtle text-xs [overflow-wrap:anywhere]">
                Updated by <span className="text-foreground-muted">{credential.updated_by}</span> on{" "}
                <DateText iso={credential.updated_at} />
              </p>
              {administrator && <ActionButtons credential={credential} onAction={onAction} />}
            </div>
          </Card>
        </li>
      ))}
    </ul>
  );
}

/* ---------- 3. Names and details: pick a name, read every fact of that credential ---------- */

export function SplitList({ credentials, administrator, onAction }: ListProps) {
  const [selectedName, setSelectedName] = useState(credentials[0]?.name);
  const selected = credentials.find((c) => c.name === selectedName) ?? credentials[0];
  if (!selected) return null;
  const facts: [string, React.ReactNode][] = [
    ["Fields set", fieldsLabel(selected.fields)],
    ["Revision", <span className="tabular-nums">{selected.revision}</span>],
    ["Updated by", selected.updated_by],
    ["Updated at", <DateText iso={selected.updated_at} />],
    ["Created by", selected.created_by],
    ["Created at", <DateText iso={selected.created_at} />],
  ];
  const line = `credential: ${selected.name}`;
  return (
    <Card className="grid grid-cols-1 bg-content bg-none text-sm md:grid-cols-[minmax(0,18rem)_minmax(0,1fr)]">
      <nav aria-label="Credentials" className="border-b md:border-r md:border-b-0">
        <ul className="flex max-h-72 flex-col overflow-y-auto p-1 md:max-h-[32rem]">
          {credentials.map((credential) => {
            const current = credential.name === selected.name;
            return (
              <li key={credential.name}>
                <button
                  type="button"
                  aria-current={current ? "true" : undefined}
                  onClick={() => setSelectedName(credential.name)}
                  className={cn(
                    "flex min-h-11 w-full touch-manipulation flex-col justify-center gap-0.5 rounded-lg px-3 py-2 text-left transition-colors duration-150 ease-out",
                    current ? "bg-content-muted" : "hover:bg-content-muted/60",
                    FOCUS_INSET
                  )}
                >
                  <span className={NAME}>{credential.name}</span>
                  <span className="text-subtle text-xs">
                    {fieldsLabel(credential.fields)} · revision {credential.revision}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </nav>
      <section aria-labelledby="selected-credential" className="flex min-w-0 flex-col gap-4 p-5">
        <div className="flex flex-col gap-1.5">
          <h2 id="selected-credential" className={cn(NAME, "text-sm")}>
            {selected.name}
          </h2>
          <Description text={selected.description} className="max-w-[70ch]" />
        </div>
        <dl className="grid grid-cols-1 gap-x-8 gap-y-3 text-[13px] sm:grid-cols-2">
          {facts.map(([term, value]) => (
            <div key={term} className="flex min-w-0 flex-col gap-0.5">
              <dt className="text-subtle text-xs">{term}</dt>
              <dd className="min-w-0 [overflow-wrap:anywhere]">{value}</dd>
            </div>
          ))}
        </dl>
        <div className="flex flex-col gap-1">
          <p className="text-subtle text-xs">
            Line that names it in the source or the destination of a configuration
          </p>
          <p className="flex min-w-0 items-center gap-1">
            <code className={cn(CODE_CHIP, "min-w-0 [overflow-wrap:anywhere]")}>{line}</code>
            <CopyButton value={line} label="Copy the line" />
          </p>
        </div>
        {administrator && (
          <div className="border-t pt-4">
            <ActionButtons credential={selected} onAction={onAction} />
          </div>
        )}
      </section>
    </Card>
  );
}
