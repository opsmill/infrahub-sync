import { Button, Card } from "@infrahub/ui";
import { useQuery } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { type FormEvent, useId, useRef, useState } from "react";

import {
  changeMyPassword,
  createMyToken,
  useDeleteMyToken,
  useInvalidateAccounts,
} from "#/api/actions";
import { meQuery, myTokensQuery } from "#/api/queries";
import type { components } from "#/api/types.generated";
import { announce } from "#/components/announcer";
import { Bar, EmptyPanel, ErrorPanel, Notice, SectionHeading } from "#/components/page";
import {
  AccountError,
  DateText,
  FormProblem,
  NewPasswordFields,
  newPasswordProblem,
  SecretValue,
} from "#/features/accounts/shared";
import { DialogFrame, Field, finePointer, INPUT, ReasonField } from "#/features/runs/form";
import { useDocumentTitle } from "#/lib/title";

type Token = components["schemas"]["TokenResource"];

export const Route = createFileRoute("/account")({
  component: AccountPage,
});

/** The account the deployment's environment defines. The name is reserved for it. */
const ENVIRONMENT_ACCOUNT = "admin";

function AccountPage() {
  useDocumentTitle("My account");
  const me = useQuery(meQuery());
  if (me.isError && !me.data) {
    return <ErrorPanel error={me.error} level="page" onRetry={() => void me.refetch()} />;
  }
  if (!me.data) {
    return (
      <div aria-busy="true" className="flex flex-col gap-3 pt-8">
        <Bar className="h-7 w-48" />
        <Bar className="h-4 w-80" />
      </div>
    );
  }
  const name = me.data.actor;
  const environmentManaged = name === ENVIRONMENT_ACCOUNT;
  return (
    <>
      <header className="flex flex-col gap-1.5 pt-8 pb-6">
        <h1 className="text-balance font-semibold text-2xl tracking-tight">My account</h1>
        <p className="text-pretty text-foreground-muted">
          Signed in as <span className="text-foreground">{name}</span>,{" "}
          {me.data.administrator ? "an administrator" : "not an administrator"}. Change your
          password and manage the API tokens that scripts use to call the Sync API as you.
        </p>
      </header>
      <div className="flex max-w-3xl flex-col gap-8">
        {environmentManaged ? (
          <Notice title="The deployment's environment defines this account" titleAs="h2">
            <p>
              The password of <code>admin</code> is set by{" "}
              <code>INFRAHUB_SYNC_ADMIN_ACCOUNT_PASSWORD</code> and its token by{" "}
              <code>INFRAHUB_SYNC_ADMIN_ACCOUNT_TOKEN</code>, in the environment of the Sync
              service. To change them, change those variables and restart the Sync service.
            </p>
          </Notice>
        ) : (
          <PasswordSection name={name} />
        )}
        <TokensSection readOnly={environmentManaged} />
      </div>
    </>
  );
}

/* ---------- password ---------- */

function PasswordSection({ name }: { name: string }) {
  const id = useId();
  const headingId = `${id}-heading`;
  const problemId = `${id}-problem`;
  const errorId = `${id}-error`;
  const currentRef = useRef<HTMLInputElement>(null);
  const [current, setCurrent] = useState("");
  const [password, setPassword] = useState("");
  const [repeat, setRepeat] = useState("");
  const [problem, setProblem] = useState<string>();
  const [error, setError] = useState<unknown>();
  const [done, setDone] = useState(false);
  const [pending, setPending] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    setDone(false);
    setError(undefined);
    const refused = current ? newPasswordProblem(password, repeat) : "Type your current password.";
    setProblem(refused);
    if (refused) return;
    setPending(true);
    const result = await changeMyPassword(current, password);
    // The passwords are not kept after the request, whatever the answer.
    setCurrent("");
    setPassword("");
    setRepeat("");
    setPending(false);
    if (result.ok) {
      setDone(true);
      announce("Password changed. Your other sessions ended; your API tokens still work.");
    } else {
      setError(result.error);
      currentRef.current?.focus();
    }
  }

  const edit = (set: (value: string) => void) => (value: string) => {
    set(value);
    setProblem(undefined);
    setDone(false);
  };

  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-3">
      <SectionHeading id={headingId} title="Password">
        Changing it ends your other sessions. Your API tokens keep working.
      </SectionHeading>
      <Card>
        <form onSubmit={submit} className="flex max-w-md flex-col gap-4 p-5">
          {/* Lets a password manager save the new password under the right account. */}
          <input type="text" name="username" autoComplete="username" value={name} readOnly hidden />
          <fieldset disabled={pending} className="m-0 flex min-w-0 flex-col gap-4 border-0 p-0">
            <Field id={`${id}-current`} label="Current password">
              <input
                ref={currentRef}
                id={`${id}-current`}
                type="password"
                autoComplete="current-password"
                className={INPUT}
                value={current}
                onChange={(e) => edit(setCurrent)(e.target.value)}
                aria-describedby={problem ? problemId : error ? errorId : undefined}
              />
            </Field>
            <NewPasswordFields
              idPrefix={id}
              password={password}
              repeat={repeat}
              onPassword={edit(setPassword)}
              onRepeat={edit(setRepeat)}
              problemId={problem ? problemId : undefined}
            />
          </fieldset>
          {problem && <FormProblem id={problemId}>{problem}</FormProblem>}
          <AccountError error={error} id={errorId} />
          {done && (
            <Notice title="Password changed">
              <p>
                Your other sessions ended, and your API tokens still work. This session stays signed
                in.
              </p>
            </Notice>
          )}
          <div>
            <Button type="submit" variant="primary" size="sm" isPending={pending}>
              Change password
            </Button>
          </div>
        </form>
      </Card>
    </section>
  );
}

/* ---------- tokens ---------- */

const TH = "h-9 px-4 text-left font-medium text-subtle text-xs";
const TD = "px-4 py-3 align-top";

function TokensSection({ readOnly }: { readOnly: boolean }) {
  const tokens = useQuery(myTokensQuery());
  const headingRef = useRef<HTMLHeadingElement>(null);
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState<Token>();
  const [created, setCreated] = useState<{ name: string; value: string }>();
  const list = tokens.data ?? [];

  return (
    <section aria-labelledby="tokens-heading" className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2
          ref={headingRef}
          id="tokens-heading"
          tabIndex={-1}
          className="font-semibold text-sm tracking-tight outline-none"
        >
          API tokens
        </h2>
        {!readOnly && (
          <Button size="sm" variant="primary" onPress={() => setCreating(true)}>
            Create a token
          </Button>
        )}
      </div>
      <p className="text-pretty text-foreground-muted text-sm">
        A script sends a token in the <code>Authorization</code> header and acts as you. The list
        shows each token's name and dates, never its value.
      </p>
      {created && (
        <SecretValue
          label={`Value of the token ${created.name}`}
          value={created.value}
          onDismiss={() => {
            setCreated(undefined);
            headingRef.current?.focus();
          }}
        >
          <p>Copy it now and keep it where your script reads its secrets.</p>
        </SecretValue>
      )}
      {tokens.isError && !tokens.data ? (
        <ErrorPanel error={tokens.error} compact onRetry={() => void tokens.refetch()} />
      ) : tokens.isPending ? (
        <div aria-busy="true" className="flex flex-col gap-2">
          <Bar className="h-9 w-full" />
          <Bar className="h-9 w-full" />
        </div>
      ) : list.length === 0 ? (
        <EmptyPanel title="No API tokens">
          <p className="text-foreground-muted">
            {readOnly
              ? "The environment of the Sync service sets the token of this account."
              : "Create a token for each script, so you can delete one without stopping the others."}
          </p>
        </EmptyPanel>
      ) : (
        <Card className="overflow-x-auto bg-content bg-none">
          <table aria-labelledby="tokens-heading" className="w-full text-sm">
            <thead className="bg-content-muted">
              <tr>
                <th scope="col" className={TH}>
                  Name
                </th>
                <th scope="col" className={TH}>
                  Created
                </th>
                <th scope="col" className={TH}>
                  Expires
                </th>
                <th scope="col" className={TH}>
                  Last used
                </th>
                {!readOnly && (
                  <th scope="col" className={TH}>
                    <span className="sr-only">Actions</span>
                  </th>
                )}
              </tr>
            </thead>
            <tbody className="divide-y">
              {list.map((token) => (
                <tr key={token.token_id}>
                  <td className={`${TD} font-medium [overflow-wrap:anywhere]`}>{token.name}</td>
                  <td className={TD}>
                    <DateText iso={token.created_at} />
                  </td>
                  <td className={TD}>
                    <DateText iso={token.expires_at} empty="no expiry" />
                  </td>
                  <td className={TD}>
                    <DateText iso={token.last_used_at} />
                  </td>
                  {!readOnly && (
                    <td className={`${TD} text-right`}>
                      <Button
                        size="xs"
                        variant="outline"
                        aria-label={`Delete the token ${token.name}`}
                        onPress={() => setDeleting(token)}
                      >
                        Delete
                      </Button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
      {creating && (
        <CreateTokenDialog
          onClose={() => setCreating(false)}
          onCreated={(name, value) => {
            setCreating(false);
            setCreated({ name, value });
          }}
        />
      )}
      {deleting && (
        <DeleteTokenDialog
          token={deleting}
          onClose={() => setDeleting(undefined)}
          onDeleted={() => {
            setDeleting(undefined);
            headingRef.current?.focus();
          }}
        />
      )}
    </section>
  );
}

/**
 * The expiry the Sync API receives, as the CLI computes it: a date alone is the last second of
 * that day in UTC; a date and a time are read in this browser's time zone.
 */
function expiryOf(date: string, time: string): { iso: string | null } | { problem: string } {
  if (!date) return time ? { problem: "Choose a date for the expiry time." } : { iso: null };
  const moment = time ? new Date(`${date}T${time}`) : new Date(`${date}T23:59:59Z`);
  if (Number.isNaN(moment.getTime())) return { problem: "The expiry date is not a valid date." };
  if (moment.getTime() <= Date.now()) {
    return { problem: "The expiry is in the past. Choose a later date, or leave it empty." };
  }
  return { iso: moment.toISOString() };
}

function CreateTokenDialog({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (name: string, value: string) => void;
}) {
  const invalidate = useInvalidateAccounts();
  const [name, setName] = useState("");
  const [date, setDate] = useState("");
  const [time, setTime] = useState("");
  const [reason, setReason] = useState("");
  const [problem, setProblem] = useState<string>();
  const [error, setError] = useState<unknown>();
  const [pending, setPending] = useState(false);

  async function submit() {
    setError(undefined);
    const expiry = expiryOf(date, time);
    const refused = !name.trim()
      ? "Give the token a name."
      : "problem" in expiry
        ? expiry.problem
        : undefined;
    setProblem(refused);
    if (refused || "problem" in expiry) return;
    setPending(true);
    const result = await createMyToken(name, expiry.iso, reason);
    setPending(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    void invalidate();
    announce(`Token ${result.data.name} created. Its value is shown once.`);
    onCreated(result.data.name, result.data.token);
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
      title="Create an API token"
      ariaLabel="Create an API token"
      description="The value is shown once, after you create the token."
      error={
        problem ? (
          <FormProblem id="token-problem">{problem}</FormProblem>
        ) : error ? (
          <AccountError error={error} />
        ) : undefined
      }
      submitLabel="Create token"
      pendingLabel="Creating the token"
      isPending={pending}
      onSubmit={submit}
    >
      <Field id="token-name" label="Name" hint="Up to 64 characters, such as the script's name.">
        <input
          id="token-name"
          className={INPUT}
          value={name}
          maxLength={64}
          onChange={(e) => edit(setName)(e.target.value)}
          autoComplete="off"
          spellCheck={false}
          aria-describedby="token-name-hint"
          // biome-ignore lint/a11y/noAutofocus: only with a fine pointer, inside a modal that just opened.
          autoFocus={finePointer()}
        />
      </Field>
      <div className="flex flex-col gap-4 sm:flex-row">
        <Field
          id="token-expiry-date"
          label="Expiry date"
          hint="Optional. A date alone means the end of that day, 23:59:59 UTC."
        >
          <input
            id="token-expiry-date"
            type="date"
            className={INPUT}
            value={date}
            onChange={(e) => edit(setDate)(e.target.value)}
            aria-describedby="token-expiry-date-hint"
          />
        </Field>
        <Field
          id="token-expiry-time"
          label="Expiry time"
          hint="Optional. In this browser's time zone."
        >
          <input
            id="token-expiry-time"
            type="time"
            className={INPUT}
            value={time}
            onChange={(e) => edit(setTime)(e.target.value)}
            aria-describedby="token-expiry-time-hint"
          />
        </Field>
      </div>
      <ReasonField id="token-reason" value={reason} onChange={edit(setReason)} />
    </DialogFrame>
  );
}

function DeleteTokenDialog({
  token,
  onClose,
  onDeleted,
}: {
  token: Token;
  onClose: () => void;
  onDeleted: () => void;
}) {
  const remove = useDeleteMyToken();
  const [reason, setReason] = useState("");
  return (
    <DialogFrame
      isOpen
      onOpenChange={(open) => !open && onClose()}
      title={`Delete the token ${token.name}`}
      ariaLabel={`Delete the token ${token.name}`}
      description="A script that sends this token is refused from its next request. You cannot undo this; create a new token instead."
      error={remove.error ? <AccountError error={remove.error} /> : undefined}
      submitLabel="Delete token"
      submitVariant="danger"
      submitOnEnter={false}
      pendingLabel="Deleting the token"
      isPending={remove.isPending}
      onSubmit={() =>
        remove.mutate(
          {
            params: {
              path: { token_id: token.token_id },
              query: { reason: reason.trim() || undefined },
            },
          },
          {
            onSuccess: () => {
              announce(`Token ${token.name} deleted.`);
              onDeleted();
            },
          }
        )
      }
    >
      <ReasonField
        id="token-delete-reason"
        value={reason}
        onChange={(v) => {
          setReason(v);
          remove.reset();
        }}
        autoFocus={finePointer()}
      />
    </DialogFrame>
  );
}
