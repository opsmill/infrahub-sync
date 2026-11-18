import { Button, Card, Checkbox, Menu, MenuItem, MenuTrigger, Modal, Popover } from "@infrahub/ui";
import { useQuery } from "@tanstack/react-query";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { cn } from "tailwind-variants";

import {
  createAccount,
  setAccountPassword,
  useDeleteAccount,
  useDeleteAccountToken,
  useInvalidateAccounts,
  useUnlockAccount,
  useUpdateAccount,
} from "#/api/actions";
import {
  type AccountState,
  accountsQuery,
  accountTokensQuery,
  describeAccountError,
  meQuery,
} from "#/api/queries";
import { clearToken } from "#/api/token";
import type { components } from "#/api/types.generated";
import { announce } from "#/components/announcer";
import { FOCUS_RAC } from "#/components/classes";
import { CheckIcon } from "#/components/icons";
import { Bar, EmptyPanel, ErrorPanel, FilterTabs, Notice, PageMessage } from "#/components/page";
import {
  AccountError,
  DateText,
  FormProblem,
  NewPasswordFields,
  newPasswordProblem,
} from "#/features/accounts/shared";
import { DialogFrame, Field, finePointer, INPUT, LINK, ReasonField } from "#/features/runs/form";
import { useNow } from "#/lib/time";
import { useDocumentTitle } from "#/lib/title";

type Account = components["schemas"]["AccountResource"];
type Token = components["schemas"]["TokenResource"];

interface AccountsSearch {
  state?: Exclude<AccountState, "all">;
}

export const Route = createFileRoute("/accounts")({
  validateSearch: (s: Record<string, unknown>): AccountsSearch => ({
    state: s.state === "active" || s.state === "disabled" ? s.state : undefined,
  }),
  component: AccountsRoute,
});

const FILTERS: { id: AccountState; label: string }[] = [
  { id: "all", label: "All" },
  { id: "active", label: "Active" },
  { id: "disabled", label: "Disabled" },
];

/** What a dialog on this page does, and to which account. */
type Action = "level" | "password" | "unlock" | "disable" | "enable" | "tokens" | "delete";

/** True while the Sync API refuses sign-ins for the account after repeated failures. */
const isRefused = (account: Account, now: number) =>
  Boolean(account.sign_in_refused_until && Date.parse(account.sign_in_refused_until) > now);

/**
 * The actions the page offers on an account: only those the Sync API accepts from this
 * administrator. `admin` is defined by the environment, so only unlock is offered for it. One's
 * own account offers no level change (the safe reading of FR-011) and no password set by an
 * administrator (the My account page changes it with the current password).
 */
function actionsFor(account: Account, self: boolean, now: number): Action[] {
  const refused = isRefused(account, now);
  if (account.environment_managed) return refused ? ["unlock"] : [];
  const actions: Action[] = [];
  if (!self) actions.push("level", "password");
  if (refused) actions.push("unlock");
  actions.push(account.disabled ? "enable" : "disable", "tokens", "delete");
  return actions;
}

function actionLabel(action: Action, account: Account): string {
  switch (action) {
    case "level":
      return account.administrator ? "Remove administrator rights" : "Make administrator";
    case "password":
      return "Set password";
    case "unlock":
      return "Allow sign-in again";
    case "disable":
      return "Disable";
    case "enable":
      return "Enable";
    case "tokens":
      return "View API tokens";
    case "delete":
      return "Delete";
  }
}

function AccountsRoute() {
  const { state } = Route.useSearch();
  return <AccountsPage state={state ?? "all"} />;
}

function AccountsPage({ state }: { state: AccountState }) {
  useDocumentTitle("Accounts");
  const me = useQuery(meQuery());
  const administrator = me.data?.administrator === true;
  const accounts = useQuery({ ...accountsQuery(state), enabled: administrator });
  const navigate = useNavigate({ from: "/accounts" });
  const now = useNow();
  const headingRef = useRef<HTMLHeadingElement>(null);
  const createRef = useRef<HTMLButtonElement>(null);
  const [creating, setCreating] = useState(false);
  const [open, setOpen] = useState<{ action: Action; account: Account }>();
  const [deleted, setDeleted] = useState<{ name: string; placeholder: string }>();

  if (me.isError && !me.data) {
    return <ErrorPanel error={me.error} level="page" onRetry={() => void me.refetch()} />;
  }
  if (!me.data) return <Skeleton />;
  // The level read with GET /me is cached; a 403 for the list means the same as a level "no".
  if (!administrator || describeAccountError(accounts.error).code === "forbidden") {
    return <AdministratorsOnly />;
  }
  const self = me.data.actor;

  /**
   * Returns focus to the control that opened a dialog: the Actions button of the account, or
   * `fallback`. The heading takes it when that control is gone, as after a deletion.
   */
  function closeDialog(name?: string, fallback?: HTMLElement | null) {
    setOpen(undefined);
    setCreating(false);
    requestAnimationFrame(() => {
      const trigger = name
        ? document.querySelector<HTMLElement>(`[data-actions-for="${CSS.escape(name)}"]`)
        : fallback;
      (trigger ?? headingRef.current)?.focus();
    });
  }

  const list = accounts.data ?? [];

  return (
    <>
      <header className="flex flex-col gap-1.5 pt-8 pb-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h1
            ref={headingRef}
            tabIndex={-1}
            className="text-balance font-semibold text-2xl tracking-tight outline-none"
          >
            Accounts
          </h1>
          <Button ref={createRef} size="sm" variant="primary" onPress={() => setCreating(true)}>
            Create an account
          </Button>
        </div>
        <p className="text-pretty text-foreground-muted">
          The people who sign in to the Sync service, their level and their state. Only an
          administrator sees this page. To change your own password or tokens, use{" "}
          <Link to="/account" className={LINK}>
            My account
          </Link>
          .
        </p>
      </header>
      <div className="flex flex-col gap-4">
        {deleted && (
          <Notice
            title={`Account ${deleted.name} deleted`}
            action={
              <Button size="xs" variant="ghost" onPress={() => setDeleted(undefined)}>
                Dismiss
              </Button>
            }
          >
            <p>
              Runs, approvals, configuration versions and audit events that named it now show{" "}
              <span className="font-medium text-foreground">{deleted.placeholder}</span>.
            </p>
          </Notice>
        )}
        <div>
          <FilterTabs
            label="Accounts to show"
            options={FILTERS}
            value={state}
            onChange={(next) =>
              void navigate({ search: { state: next === "all" ? undefined : next } })
            }
          />
        </div>
        {accounts.isError && !accounts.data ? (
          <ErrorPanel error={accounts.error} onRetry={() => void accounts.refetch()} />
        ) : accounts.isPending ? (
          <div aria-busy="true" className="flex flex-col gap-2">
            <Bar className="h-10 w-full" />
            <Bar className="h-10 w-full" />
            <Bar className="h-10 w-full" />
          </div>
        ) : list.length === 0 ? (
          <EmptyPanel title={state === "disabled" ? "No disabled accounts" : "No accounts"} />
        ) : (
          <AccountsTable
            accounts={list}
            self={self}
            now={now}
            onAction={(action, account) => setOpen({ action, account })}
          />
        )}
      </div>
      {creating && (
        <CreateAccountDialog onClose={() => closeDialog(undefined, createRef.current)} />
      )}
      {open?.action === "level" && (
        <LevelDialog account={open.account} onClose={() => closeDialog(open.account.name)} />
      )}
      {open?.action === "password" && (
        <PasswordDialog account={open.account} onClose={() => closeDialog(open.account.name)} />
      )}
      {open?.action === "unlock" && (
        <UnlockDialog account={open.account} onClose={() => closeDialog(open.account.name)} />
      )}
      {(open?.action === "disable" || open?.action === "enable") && (
        <StateDialog
          account={open.account}
          self={open.account.name === self}
          onClose={() => closeDialog(open.account.name)}
        />
      )}
      {open?.action === "tokens" && (
        <TokensDialog account={open.account} onClose={() => closeDialog(open.account.name)} />
      )}
      {open?.action === "delete" && (
        <DeleteDialog
          account={open.account}
          self={open.account.name === self}
          onClose={() => closeDialog(open.account.name)}
          onDeleted={(placeholder) => {
            setDeleted({ name: open.account.name, placeholder });
            closeDialog();
          }}
        />
      )}
    </>
  );
}

function Skeleton() {
  return (
    <div aria-busy="true" className="flex flex-col gap-3 pt-8">
      <Bar className="h-7 w-40" />
      <Bar className="h-4 w-96 max-w-full" />
    </div>
  );
}

function AdministratorsOnly() {
  return (
    <PageMessage title="Only an administrator can manage accounts">
      <p>
        Your account can change its own password and manage its own API tokens on the{" "}
        <Link to="/account" className={LINK}>
          My account
        </Link>{" "}
        page. Ask an administrator to create, change or delete other accounts.
      </p>
    </PageMessage>
  );
}

const TH = "h-9 px-4 text-left font-medium text-subtle text-xs";
const TD = "px-4 py-3 align-top";

function AccountsTable({
  accounts,
  self,
  now,
  onAction,
}: {
  accounts: Account[];
  self: string;
  now: number;
  onAction: (action: Action, account: Account) => void;
}) {
  return (
    <Card className="relative overflow-x-auto bg-content bg-none">
      <table aria-label="Accounts" className="w-full text-sm">
        <thead className="bg-content-muted">
          <tr>
            <th scope="col" className={TH}>
              Name
            </th>
            <th scope="col" className={TH}>
              Level
            </th>
            <th scope="col" className={TH}>
              State
            </th>
            <th scope="col" className={TH}>
              Created
            </th>
            <th scope="col" className={TH}>
              <span className="sr-only">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {accounts.map((account) => {
            const isSelf = account.name === self;
            const actions = actionsFor(account, isSelf, now);
            const refused = isRefused(account, now);
            return (
              <tr key={account.name} data-account={account.name}>
                <td className={TD}>
                  <span className="font-medium [overflow-wrap:anywhere]">{account.name}</span>
                  {isSelf && <span className="text-subtle"> (you)</span>}
                  {account.environment_managed && (
                    <p className="text-subtle text-xs">Defined by the deployment's environment</p>
                  )}
                </td>
                <td className={TD}>
                  {account.administrator ? "Administrator" : "Not an administrator"}
                </td>
                <td className={TD}>
                  {account.disabled ? "Disabled" : "Active"}
                  {refused && account.sign_in_refused_until && (
                    <p className="text-warning text-xs">
                      Sign-in refused until <DateText iso={account.sign_in_refused_until} />
                    </p>
                  )}
                </td>
                <td className={TD}>
                  <DateText iso={account.created_at} />
                </td>
                <td className={cn(TD, "text-right")}>
                  {actions.length > 0 && (
                    <MenuTrigger>
                      <Button
                        size="xs"
                        variant="outline"
                        className={FOCUS_RAC}
                        aria-label={`Actions for ${account.name}`}
                        data-actions-for={account.name}
                      >
                        Actions
                      </Button>
                      <Popover placement="bottom end" className="bg-content">
                        <Menu
                          aria-label={`Actions for ${account.name}`}
                          onAction={(key) => onAction(key as Action, account)}
                          className="min-w-52"
                        >
                          {actions.map((action) => (
                            <MenuItem
                              key={action}
                              id={action}
                              textValue={actionLabel(action, account)}
                              className={cn(
                                "pointer-coarse:min-h-11",
                                action === "delete" ? "text-danger" : "text-foreground"
                              )}
                            >
                              {actionLabel(action, account)}
                            </MenuItem>
                          ))}
                        </Menu>
                      </Popover>
                    </MenuTrigger>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </Card>
  );
}

/* ---------- dialogs ---------- */

function CreateAccountDialog({ onClose }: { onClose: () => void }) {
  const invalidate = useInvalidateAccounts();
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [repeat, setRepeat] = useState("");
  const [administrator, setAdministrator] = useState(false);
  const [reason, setReason] = useState("");
  const [problem, setProblem] = useState<string>();
  /** The field the problem is about, linked to the message while it is shown. */
  const [problemField, setProblemField] = useState<"name" | "password">();
  const [error, setError] = useState<unknown>();
  const [pending, setPending] = useState(false);

  async function submit() {
    setError(undefined);
    const nameMissing = !name.trim();
    const refused = nameMissing ? "Give the account a name." : newPasswordProblem(password, repeat);
    setProblem(refused);
    setProblemField(refused ? (nameMissing ? "name" : "password") : undefined);
    if (refused) return;
    setPending(true);
    const result = await createAccount(name, password, administrator, reason);
    // The passwords are not kept after the request, whatever the answer.
    setPassword("");
    setRepeat("");
    setPending(false);
    // The lists are read again whatever the answer: a refusal can mean they are out of date.
    void invalidate();
    if (!result.ok) {
      setError(result.error);
      return;
    }
    announce(`Account ${result.data.name} created.`);
    onClose();
  }

  const edit = (set: (value: string) => void) => (value: string) => {
    set(value);
    setProblem(undefined);
    setError(undefined);
  };

  return (
    <DialogFrame
      isOpen
      onOpenChange={(open) => !open && onClose()}
      title="Create an account"
      ariaLabel="Create an account"
      description="Give the person the name and the initial password. They can change the password on the My account page."
      error={
        problem ? (
          <FormProblem id="account-problem">{problem}</FormProblem>
        ) : error ? (
          <AccountError error={error} />
        ) : undefined
      }
      submitLabel="Create account"
      pendingLabel="Creating the account"
      isPending={pending}
      onSubmit={submit}
    >
      <Field id="account-name" label="Name" hint="Up to 64 characters, without spaces.">
        <input
          id="account-name"
          className={INPUT}
          value={name}
          maxLength={64}
          onChange={(e) => edit(setName)(e.target.value)}
          autoComplete="off"
          autoCapitalize="none"
          spellCheck={false}
          aria-describedby={
            problem && problemField === "name"
              ? "account-name-hint account-problem"
              : "account-name-hint"
          }
          aria-invalid={problem && problemField === "name" ? true : undefined}
          // biome-ignore lint/a11y/noAutofocus: only with a fine pointer, inside a modal that just opened.
          autoFocus={finePointer()}
        />
      </Field>
      <NewPasswordFields
        idPrefix="account-password"
        label="Initial password"
        password={password}
        repeat={repeat}
        onPassword={edit(setPassword)}
        onRepeat={edit(setRepeat)}
        problemId={problem && problemField === "password" ? "account-problem" : undefined}
      />
      <Checkbox
        isSelected={administrator}
        onChange={(value) => {
          setAdministrator(value);
          setError(undefined);
        }}
        className="items-start gap-2.5 text-foreground"
      >
        <span className="min-w-0 text-pretty">
          Administrator: can manage accounts and register configurations
        </span>
      </Checkbox>
      <ReasonField id="account-reason" value={reason} onChange={edit(setReason)} />
    </DialogFrame>
  );
}

function LevelDialog({ account, onClose }: { account: Account; onClose: () => void }) {
  const update = useUpdateAccount();
  const [reason, setReason] = useState("");
  const raise = !account.administrator;
  const title = raise
    ? `Make ${account.name} an administrator`
    : `Remove administrator rights from ${account.name}`;
  return (
    <DialogFrame
      isOpen
      onOpenChange={(open) => !open && onClose()}
      title={title}
      ariaLabel={title}
      description={
        raise
          ? `From its next request, ${account.name} can manage accounts, register configurations and act on every run.`
          : `From its next request, ${account.name} can no longer manage accounts or register configurations.`
      }
      error={update.error ? <AccountError error={update.error} /> : undefined}
      submitLabel={raise ? "Make administrator" : "Remove administrator rights"}
      submitVariant={raise ? "warning" : "primary"}
      pendingLabel="Changing the level"
      isPending={update.isPending}
      onSubmit={() =>
        update.mutate(
          {
            params: { path: { name: account.name } },
            body: { administrator: raise, reason: reason.trim() || null },
          },
          {
            onSuccess: () => {
              announce(
                raise
                  ? `${account.name} is now an administrator.`
                  : `${account.name} is no longer an administrator.`
              );
              onClose();
            },
          }
        )
      }
    >
      <ReasonField
        id="level-reason"
        value={reason}
        onChange={(v) => {
          setReason(v);
          update.reset();
        }}
        autoFocus={finePointer()}
      />
    </DialogFrame>
  );
}

function PasswordDialog({ account, onClose }: { account: Account; onClose: () => void }) {
  const invalidate = useInvalidateAccounts();
  const [password, setPassword] = useState("");
  const [repeat, setRepeat] = useState("");
  const [reason, setReason] = useState("");
  const [problem, setProblem] = useState<string>();
  const [error, setError] = useState<unknown>();
  const [pending, setPending] = useState(false);

  async function submit() {
    setError(undefined);
    const refused = newPasswordProblem(password, repeat);
    setProblem(refused);
    if (refused) return;
    setPending(true);
    const result = await setAccountPassword(account.name, password, reason);
    // The passwords are not kept after the request, whatever the answer.
    setPassword("");
    setRepeat("");
    setPending(false);
    // The lists are read again whatever the answer: a refusal can mean they are out of date.
    void invalidate();
    if (!result.ok) {
      setError(result.error);
      return;
    }
    announce(`Password of ${account.name} set. Its sessions ended.`);
    onClose();
  }

  const edit = (set: (value: string) => void) => (value: string) => {
    set(value);
    setProblem(undefined);
    setError(undefined);
  };

  return (
    <DialogFrame
      isOpen
      onOpenChange={(open) => !open && onClose()}
      title={`Set the password of ${account.name}`}
      ariaLabel={`Set the password of ${account.name}`}
      description={`Every session of ${account.name} ends, and a refusal after repeated failed sign-ins ends too. Give the new password to the person, who can change it on the My account page.`}
      error={
        problem ? (
          <FormProblem id="password-problem">{problem}</FormProblem>
        ) : error ? (
          <AccountError error={error} />
        ) : undefined
      }
      submitLabel="Set password"
      pendingLabel="Setting the password"
      isPending={pending}
      onSubmit={submit}
    >
      <input type="text" name="username" autoComplete="off" value={account.name} readOnly hidden />
      <NewPasswordFields
        idPrefix="set-password"
        password={password}
        repeat={repeat}
        onPassword={edit(setPassword)}
        onRepeat={edit(setRepeat)}
        problemId={problem ? "password-problem" : undefined}
        autoFocus={finePointer()}
      />
      <ReasonField id="password-reason" value={reason} onChange={edit(setReason)} />
    </DialogFrame>
  );
}

function UnlockDialog({ account, onClose }: { account: Account; onClose: () => void }) {
  const unlock = useUnlockAccount();
  const [reason, setReason] = useState("");
  const title = `Allow sign-in again for ${account.name}`;
  return (
    <DialogFrame
      isOpen
      onOpenChange={(open) => !open && onClose()}
      title={title}
      ariaLabel={title}
      description={
        <>
          The Sync service refuses sign-ins for {account.name} after repeated failures
          {account.sign_in_refused_until && (
            <>
              , until <DateText iso={account.sign_in_refused_until} />
            </>
          )}
          . This ends the refusal now. It does not change the password.
        </>
      }
      error={unlock.error ? <AccountError error={unlock.error} /> : undefined}
      submitLabel="Allow sign-in"
      pendingLabel="Ending the refusal"
      isPending={unlock.isPending}
      onSubmit={() =>
        unlock.mutate(
          {
            params: { path: { name: account.name } },
            body: { reason: reason.trim() || null },
          },
          {
            onSuccess: () => {
              announce(`${account.name} can sign in again.`);
              onClose();
            },
          }
        )
      }
    >
      <ReasonField
        id="unlock-reason"
        value={reason}
        onChange={(v) => {
          setReason(v);
          unlock.reset();
        }}
        autoFocus={finePointer()}
      />
    </DialogFrame>
  );
}

/** Disables an active account or enables a disabled one. */
function StateDialog({
  account,
  self,
  onClose,
}: {
  account: Account;
  self: boolean;
  onClose: () => void;
}) {
  const update = useUpdateAccount();
  const [reason, setReason] = useState("");
  const disable = !account.disabled;
  const title = disable ? `Disable ${account.name}` : `Enable ${account.name}`;
  return (
    <DialogFrame
      isOpen
      onOpenChange={(open) => !open && onClose()}
      title={title}
      ariaLabel={title}
      description={
        disable ? (
          <>
            <p>
              {account.name} can no longer sign in, and its sessions and API tokens are deleted. Its
              name stays on past records. You can enable the account again, but the deleted tokens
              do not come back.
            </p>
            {self && (
              <p className="mt-1 font-medium text-foreground">
                This is your account: you will be signed out.
              </p>
            )}
          </>
        ) : (
          `${account.name} can sign in again with its password. API tokens deleted when the account was disabled do not come back.`
        )
      }
      error={update.error ? <AccountError error={update.error} /> : undefined}
      submitLabel={disable ? (self ? "Disable and sign out" : "Disable account") : "Enable account"}
      submitVariant={disable ? "danger" : "primary"}
      submitOnEnter={!disable}
      pendingLabel={disable ? "Disabling the account" : "Enabling the account"}
      isPending={update.isPending}
      onSubmit={() =>
        update.mutate(
          {
            params: { path: { name: account.name } },
            body: { disabled: disable, reason: reason.trim() || null },
          },
          {
            onSuccess: () => {
              if (self && disable) {
                // The session was deleted with the account's sessions: the sign-in form follows.
                clearToken();
                return;
              }
              announce(disable ? `${account.name} disabled.` : `${account.name} enabled.`);
              onClose();
            },
          }
        )
      }
    >
      <ReasonField
        id="state-reason"
        value={reason}
        onChange={(v) => {
          setReason(v);
          update.reset();
        }}
        autoFocus={finePointer()}
      />
    </DialogFrame>
  );
}

function DeleteDialog({
  account,
  self,
  onClose,
  onDeleted,
}: {
  account: Account;
  self: boolean;
  onClose: () => void;
  onDeleted: (placeholder: string) => void;
}) {
  const remove = useDeleteAccount();
  const [typed, setTyped] = useState("");
  const [reason, setReason] = useState("");
  const matches = typed === account.name;
  const title = `Delete ${account.name}`;
  return (
    <DialogFrame
      isOpen
      onOpenChange={(open) => !open && onClose()}
      title={title}
      ariaLabel={title}
      description={
        <>
          <p>
            Deleting cannot be reversed. The account, its password, its sessions and its API tokens
            are removed, and past runs, approvals and audit events show a placeholder instead of the
            name. The name can be used for a new account.
          </p>
          <p className="mt-1">
            To stop sign-ins and keep the account, disable it instead: disabling can be reversed.
          </p>
          {self && (
            <p className="mt-1 font-medium text-foreground">
              This is your account: you will be signed out.
            </p>
          )}
        </>
      }
      error={remove.error ? <AccountError error={remove.error} /> : undefined}
      submitLabel={self ? "Delete and sign out" : "Delete account"}
      submitVariant="danger"
      submitOnEnter={false}
      isDisabled={!matches}
      disabledReason={`Type ${account.name} to continue.`}
      pendingLabel="Deleting the account"
      isPending={remove.isPending}
      onSubmit={() =>
        remove.mutate(
          {
            params: {
              path: { name: account.name },
              query: { reason: reason.trim() || undefined },
            },
          },
          {
            onSuccess: (data) => {
              if (self) {
                clearToken();
                return;
              }
              announce(`Account ${account.name} deleted. Records now show ${data.placeholder}.`);
              onDeleted(data.placeholder);
            },
          }
        )
      }
    >
      <Field id="delete-confirm" label={`Type ${account.name} to confirm`}>
        <div className="relative">
          <input
            id="delete-confirm"
            className={cn(
              INPUT,
              "font-mono",
              matches && "border-success pr-9 focus-visible:border-success"
            )}
            value={typed}
            onChange={(e) => {
              setTyped(e.target.value);
              remove.reset();
            }}
            autoComplete="off"
            spellCheck={false}
            autoCapitalize="none"
            autoCorrect="off"
            data-1p-ignore=""
            data-lpignore="true"
            // biome-ignore lint/a11y/noAutofocus: only with a fine pointer, inside a modal that just opened.
            autoFocus={finePointer()}
          />
          {matches && (
            <CheckIcon className="pointer-events-none absolute inset-y-0 right-3 my-auto size-4 text-success" />
          )}
        </div>
      </Field>
      <ReasonField
        id="delete-reason"
        value={reason}
        onChange={(v) => {
          setReason(v);
          remove.reset();
        }}
      />
    </DialogFrame>
  );
}

/** The API tokens of another account, with a delete action on each. */
function TokensDialog({ account, onClose }: { account: Account; onClose: () => void }) {
  const tokens = useQuery(accountTokensQuery(account.name));
  const remove = useDeleteAccountToken();
  const [confirming, setConfirming] = useState<Token>();
  const titleId = "account-tokens-title";
  const headingRef = useRef<HTMLHeadingElement>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);
  const list = tokens.data ?? [];

  // The confirmation replaces the row's Delete button, so focus moves to its "Delete token".
  const confirmingId = confirming?.token_id;
  useEffect(() => {
    if (confirmingId) confirmRef.current?.focus();
  }, [confirmingId]);

  /** Ends the confirmation and moves focus to the row's Delete button, or to the heading. */
  function endConfirmation(tokenId?: string) {
    setConfirming(undefined);
    requestAnimationFrame(() => {
      const button = tokenId
        ? document.querySelector<HTMLElement>(`[data-delete-token="${CSS.escape(tokenId)}"]`)
        : null;
      (button ?? headingRef.current)?.focus();
    });
  }

  return (
    <Modal
      isOpen
      onOpenChange={(open) => !open && !remove.isPending && onClose()}
      isDismissable={!remove.isPending}
      aria-label={`API tokens of ${account.name}`}
    >
      <div className="flex w-[min(560px,calc(90vw-10px))] flex-col gap-4 p-5">
        <div className="flex flex-col gap-1.5">
          <h2
            ref={headingRef}
            id={titleId}
            tabIndex={-1}
            className="font-semibold text-base outline-none"
          >
            API tokens of {account.name}
          </h2>
          <p className="text-pretty text-foreground-muted text-sm">
            A script that sends a deleted token is refused from its next request. Values are never
            shown.
          </p>
        </div>
        {tokens.isError && !tokens.data ? (
          <div className="flex flex-col items-start gap-2">
            <AccountError error={tokens.error} />
            <Button size="sm" variant="outline" onPress={() => void tokens.refetch()}>
              Try again
            </Button>
          </div>
        ) : tokens.isPending ? (
          <div aria-busy="true" className="flex flex-col gap-2">
            <Bar className="h-9 w-full" />
          </div>
        ) : list.length === 0 ? (
          <p className="text-foreground-muted">{account.name} has no API tokens.</p>
        ) : (
          <ul aria-labelledby={titleId} className="flex flex-col divide-y rounded-xl border">
            {list.map((token) => (
              <li key={token.token_id} className="flex flex-col gap-2 px-4 py-3">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="font-medium [overflow-wrap:anywhere]">{token.name}</p>
                    <p className="text-subtle text-xs">
                      Created <DateText iso={token.created_at} />. Expires{" "}
                      <DateText iso={token.expires_at} empty="never" />. Last used{" "}
                      <DateText iso={token.last_used_at} />.
                    </p>
                  </div>
                  {confirming?.token_id !== token.token_id && (
                    <Button
                      size="xs"
                      variant="outline"
                      aria-label={`Delete the token ${token.name}`}
                      data-delete-token={token.token_id}
                      onPress={() => {
                        remove.reset();
                        setConfirming(token);
                      }}
                    >
                      Delete
                    </Button>
                  )}
                </div>
                {confirming?.token_id === token.token_id && (
                  <div className="flex flex-wrap items-center gap-2 rounded-lg bg-danger-surface px-3 py-2">
                    <p className="min-w-0 flex-1 text-sm">
                      Delete {token.name}? You cannot undo this.
                    </p>
                    <Button
                      ref={confirmRef}
                      size="xs"
                      variant="danger"
                      isPending={remove.isPending}
                      onPress={() =>
                        remove.mutate(
                          {
                            params: {
                              path: { name: account.name, token_id: token.token_id },
                            },
                          },
                          {
                            onSuccess: () => {
                              announce(`Token ${token.name} of ${account.name} deleted.`);
                              endConfirmation();
                            },
                            onError: (error) => {
                              // The token or the account is gone: the list read again drops the
                              // row, so the confirmation ends and focus moves to the heading.
                              const code = describeAccountError(error).code;
                              if (code === "token-not-found" || code === "account-not-found") {
                                endConfirmation();
                              }
                            },
                          }
                        )
                      }
                    >
                      Delete token
                    </Button>
                    <Button
                      size="xs"
                      variant="ghost"
                      isDisabled={remove.isPending}
                      onPress={() => {
                        remove.reset();
                        endConfirmation(token.token_id);
                      }}
                    >
                      Keep
                    </Button>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
        <AccountError error={remove.error} />
        <div className="flex justify-end border-t pt-3">
          <Button size="sm" variant="ghost" isDisabled={remove.isPending} onPress={onClose}>
            Close
          </Button>
        </div>
      </div>
    </Modal>
  );
}
