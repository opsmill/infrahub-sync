import { Button, Card, Menu, MenuItem, MenuTrigger, Popover } from "@infrahub/ui";
import { useQuery } from "@tanstack/react-query";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { type ReactNode, useMemo, useRef, useState } from "react";
import { cn } from "tailwind-variants";

import { credentialsQuery, meQuery } from "#/api/queries";
import type { Credential } from "#/api/types";
import { CODE_CHIP, FOCUS_RAC } from "#/components/classes";
import { Bar, EmptyPanel, ErrorPanel, FilterTabs, Notice, SearchField } from "#/components/page";
import { formatRelative, useNow } from "#/lib/time";
import { useDocumentTitle } from "#/lib/title";

import { CredentialRowDelete } from "./CredentialDelete";
import { CredentialRowForm, DescribeCredentialDialog } from "./CredentialForm";
import {
  type CredentialOrder,
  changedSinceCreation,
  countBySet,
  FIELD_SETS,
  type FieldSetId,
  fieldsLabel,
  findCredentials,
} from "./fields";

type Action = "value" | "describe" | "delete";

const ACTION_LABEL: Record<Action, string> = {
  value: "Change value",
  describe: "Edit description",
  delete: "Delete",
};

const ACTIONS: Action[] = ["value", "describe", "delete"];

/** What is open on the page: the create form above the first row, or an action on one row. */
type Open = { action: "create" } | { action: Action; name: string };

/** The "Actions" menu of one credential, as on the Accounts page. */
function ActionsMenu({
  credential,
  onAction,
}: {
  credential: Credential;
  onAction: (action: Action, credential: Credential) => void;
}) {
  return (
    <MenuTrigger>
      <Button
        size="xs"
        variant="outline"
        className={cn(FOCUS_RAC, "pointer-coarse:min-h-11")}
        aria-label={`Actions for ${credential.name}`}
        data-credential-actions={credential.name}
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

function CredentialRow({
  credential,
  now,
  actions,
}: {
  credential: Credential;
  now: number;
  actions: ReactNode;
}) {
  const changed = changedSinceCreation(credential);
  return (
    <li className="grid grid-cols-[minmax(0,1fr)_auto] items-start gap-x-4 gap-y-1 px-4 py-3 text-sm">
      <div className="flex min-w-0 flex-col gap-0.5">
        <span className="font-medium font-mono text-[13px] [overflow-wrap:anywhere]">
          {credential.name}
        </span>
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
          <span className="tabular-nums">revision {credential.revision}</span> ·{" "}
          {changed ? "changed" : "created"}{" "}
          <time dateTime={credential.updated_at} title={credential.updated_at}>
            {formatRelative(credential.updated_at, now)}
          </time>{" "}
          by <span className="[overflow-wrap:anywhere]">{credential.updated_by}</span>
          {changed && (
            <>
              {" "}
              · created{" "}
              <time dateTime={credential.created_at} title={credential.created_at}>
                {formatRelative(credential.created_at, now)}
              </time>{" "}
              by <span className="[overflow-wrap:anywhere]">{credential.created_by}</span>
            </>
          )}
        </p>
      </div>
      {actions}
    </li>
  );
}

/**
 * The list: a search over the name, the description and the account that last changed the value,
 * the field set as a filter, and two orders. A form or a confirmation takes the place of the row it
 * acts on (`slot.at`), or opens above the first row (`slot.at` null).
 */
function CredentialList({
  credentials,
  administrator,
  onAction,
  slot,
}: {
  credentials: Credential[];
  administrator: boolean;
  onAction: (action: Action, credential: Credential) => void;
  slot?: { at: string | null; node: ReactNode };
}) {
  const [query, setQuery] = useState("");
  const [set, setSet] = useState<"all" | FieldSetId>("all");
  const [order, setOrder] = useState<CredentialOrder>("name");
  const now = useNow();
  const counts = useMemo(() => countBySet(credentials), [credentials]);
  const keep = slot?.at ?? undefined;
  const shown = useMemo(
    () => findCredentials(credentials, { query, set, order, keep }),
    [credentials, query, set, order, keep]
  );
  const slotItem = slot && <li className="bg-content-muted/50">{slot.node}</li>;

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
      {(shown.length > 0 || slot) && (
        <Card className="bg-content bg-none p-0">
          <ul aria-label="Credentials" className="divide-y">
            {slot?.at === null && slotItem}
            {shown.map((credential) =>
              slot?.at === credential.name ? (
                <li key={credential.name} className="bg-content-muted/50">
                  {slot.node}
                </li>
              ) : (
                <CredentialRow
                  key={credential.name}
                  credential={credential}
                  now={now}
                  actions={
                    administrator && <ActionsMenu credential={credential} onAction={onAction} />
                  }
                />
              )
            )}
          </ul>
        </Card>
      )}
      {shown.length === 0 && credentials.length > 0 && (
        <Card className="flex flex-col items-start gap-2 bg-content bg-none px-4 py-6 text-sm">
          <p className="font-medium">No credential matches</p>
          <p className="text-foreground-muted">
            None of the {credentials.length} credentials matches this search and this field set.
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
      )}
    </div>
  );
}

/**
 * The Credentials page. Every signed-in account views the list; only an administrator views the
 * actions, which open in place of the row they act on.
 */
export function CredentialsPage() {
  useDocumentTitle("Credentials");
  const me = useQuery(meQuery());
  const credentials = useQuery(credentialsQuery());
  const administrator = me.data?.administrator === true;
  const headingRef = useRef<HTMLHeadingElement>(null);
  const createRef = useRef<HTMLButtonElement>(null);
  // A link from a refused registration opens the form for the credential it names.
  const search = useSearch({ from: "/credentials" });
  const navigate = useNavigate();
  const [open, setOpen] = useState<Open | undefined>(() =>
    search.new
      ? { action: "create" }
      : search.value
        ? { action: "value", name: search.value }
        : undefined
  );
  const [deleted, setDeleted] = useState<string>();

  /** Closes what is open and returns focus to the control that opened it. */
  function close(name?: string) {
    setOpen(undefined);
    // The name from the link serves one form: the next "New credential" starts empty.
    if (search.new || search.value)
      void navigate({ to: "/credentials", search: {}, replace: true });
    requestAnimationFrame(() => {
      const trigger = name
        ? document.querySelector<HTMLElement>(`[data-credential-actions="${CSS.escape(name)}"]`)
        : createRef.current;
      (trigger ?? headingRef.current)?.focus();
    });
  }

  const list = credentials.data ?? [];
  const target =
    open && open.action !== "create" ? list.find((c) => c.name === open.name) : undefined;
  // Only an administrator opens a form; another account never views one.
  const canOpen = administrator && credentials.data !== undefined;

  let slot: { at: string | null; node: ReactNode } | undefined;
  let dialog: ReactNode = null;
  if (canOpen && open?.action === "create") {
    slot = {
      at: null,
      node: <CredentialRowForm initialName={search.new} onClose={() => close()} />,
    };
  } else if (canOpen && target && open?.action === "value") {
    slot = {
      at: target.name,
      node: <CredentialRowForm credential={target} onClose={() => close(target.name)} />,
    };
  } else if (canOpen && target && open?.action === "delete") {
    slot = {
      at: target.name,
      node: (
        <CredentialRowDelete
          credential={target}
          onClose={() => close(target.name)}
          onDeleted={() => {
            setDeleted(target.name);
            close();
          }}
        />
      ),
    };
  } else if (canOpen && target && open?.action === "describe") {
    dialog = <DescribeCredentialDialog credential={target} onClose={() => close(target.name)} />;
  }

  return (
    <>
      <header className="flex flex-col gap-1.5 pt-8 pb-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h1
            ref={headingRef}
            tabIndex={-1}
            className="text-balance font-semibold text-2xl tracking-tight outline-none"
          >
            Credentials
          </h1>
          {administrator && (
            <Button
              ref={createRef}
              size="sm"
              variant="primary"
              onPress={() => {
                setDeleted(undefined);
                setOpen({ action: "create" });
              }}
            >
              New credential
            </Button>
          )}
        </div>
        <p className="max-w-[80ch] text-pretty text-foreground-muted">
          The tokens and passwords that the Sync service reads when a run connects to a source or a
          destination. A configuration names one with{" "}
          <code className={CODE_CHIP}>credential: &lt;name&gt;</code>. No page shows a stored value.
        </p>
      </header>
      <div className="flex flex-col gap-4">
        {me.data && !administrator && (
          <Notice role="note">
            <p>Ask an administrator to create or change credentials.</p>
          </Notice>
        )}
        {deleted && (
          <Notice
            title={`Credential ${deleted} deleted`}
            action={
              <Button size="xs" variant="ghost" onPress={() => setDeleted(undefined)}>
                Dismiss
              </Button>
            }
          />
        )}
        {credentials.isError && !credentials.data ? (
          <ErrorPanel error={credentials.error} onRetry={() => void credentials.refetch()} />
        ) : credentials.isPending ? (
          <div aria-busy="true" className="flex flex-col gap-2">
            <span className="sr-only">Loading the credentials</span>
            <Bar className="h-12 w-full" />
            <Bar className="h-12 w-full" />
            <Bar className="h-12 w-full" />
          </div>
        ) : list.length === 0 && !slot ? (
          <EmptyPanel title="No credentials yet">
            <p className="max-w-[60ch] text-pretty text-foreground-muted">
              {administrator
                ? "Create a credential for each source and destination that needs a token or a password. Then name it in the configuration."
                : "A configuration that needs a token or a password names a credential. Ask an administrator to create one."}
            </p>
            {administrator && (
              <Button size="sm" variant="outline" onPress={() => setOpen({ action: "create" })}>
                New credential
              </Button>
            )}
          </EmptyPanel>
        ) : (
          <CredentialList
            credentials={list}
            administrator={administrator}
            onAction={(action, credential) => {
              setDeleted(undefined);
              setOpen({ action, name: credential.name });
            }}
            slot={slot}
          />
        )}
      </div>
      {dialog}
    </>
  );
}
