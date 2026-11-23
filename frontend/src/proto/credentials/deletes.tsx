/* PROTOTYPE (spec 027, T074). The three versions of the delete confirmation with the in-use listing. */
import { Button, Sheet } from "@infrahub/ui";
import { type ReactNode, useEffect, useRef, useState } from "react";
import { cn } from "tailwind-variants";

import { type CredentialInUse, credentialInUse } from "#/api/queries";
import { announce } from "#/components/announcer";
import { FOCUS_RAC } from "#/components/classes";
import { Notice } from "#/components/page";
import { DateText } from "#/features/accounts/shared";
import { DialogFrame, LINK } from "#/features/runs/form";

import { deleteCredential, fieldsLabel } from "./data";
import { CredentialError, configHref, type DeleteProps, InUseList } from "./shared";

const CANNOT_REVERSE = "A deletion cannot be reversed.";
const REFUSED_WHEN =
  "The Sync service refuses the deletion while the latest version of a configuration, or a schedule that is not deleted, names the credential, and then lists them.";

/** Sends the deletion and keeps its refusal. */
function useDeletion({ credential, onDeleted }: DeleteProps) {
  const [error, setError] = useState<unknown>();
  const [pending, setPending] = useState(false);
  const [refusedAt, setRefusedAt] = useState<string>();
  async function send() {
    setError(undefined);
    setPending(true);
    const result = await deleteCredential(credential.name);
    setPending(false);
    if (!result.ok) {
      setError(result.error);
      setRefusedAt(new Date().toISOString());
      if (credentialInUse(result.error)) {
        announce(`${credential.name} was not deleted: it is in use.`);
      }
      return;
    }
    announce(`Credential ${credential.name} deleted.`);
    onDeleted();
  }
  const inUse = credentialInUse(error);
  return { error: inUse ? undefined : error, inUse, pending, refusedAt, send };
}

/* ---------- 1. Dialog: confirm in a modal; a refusal replaces the question with the listing. Kept from the first round. ---------- */

export function ConfirmDialog(props: DeleteProps) {
  const { credential, onClose } = props;
  const deletion = useDeletion(props);
  const title = `Delete ${credential.name}?`;
  return (
    <DialogFrame
      isOpen
      onOpenChange={(open) => !open && onClose()}
      title={deletion.inUse ? `${credential.name} is in use` : title}
      ariaLabel={title}
      description={deletion.inUse ? undefined : CANNOT_REVERSE}
      error={deletion.error ? <CredentialError error={deletion.error} /> : undefined}
      submitLabel="Delete credential"
      submitVariant="danger"
      pendingLabel="Deleting the credential"
      isPending={deletion.pending}
      isDisabled={Boolean(deletion.inUse)}
      disabledReason="The Sync service deletes a credential only when no configuration and no schedule names it."
      dismissLabel={deletion.inUse ? "Close" : "Cancel"}
      autoFocusDismiss
      submitOnEnter={false}
      onSubmit={deletion.send}
    >
      {deletion.inUse ? (
        <Notice tone="warning" role="alert" title={`${credential.name} was not deleted`}>
          <p>
            Register a version of each configuration that names another credential, and change or
            delete each schedule. Then delete the credential.
          </p>
          <InUseList inUse={deletion.inUse} />
        </Notice>
      ) : (
        <p className="text-pretty text-foreground-muted text-sm">{REFUSED_WHEN}</p>
      )}
    </DialogFrame>
  );
}

/* ---------- 2. In the row: the row turns into the question, then into the refusal, until dismissed ---------- */

export function RowDelete(props: DeleteProps) {
  const { credential, onClose } = props;
  const deletion = useDeletion(props);
  const ref = useRef<HTMLElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    cancelRef.current?.focus();
  }, []);
  useEffect(() => {
    if (deletion.inUse) ref.current?.focus();
  }, [deletion.inUse]);

  return (
    <section
      ref={ref}
      tabIndex={-1}
      aria-labelledby="credential-delete-row-title"
      className={cn(
        "flex flex-col gap-3 border-l-2 px-4 py-4 text-sm outline-none",
        deletion.inUse ? "border-amber-600" : "border-danger"
      )}
      onKeyDown={(e) => {
        if (e.key === "Escape" && !deletion.pending) {
          e.preventDefault();
          onClose();
        }
      }}
    >
      <div className="flex flex-col gap-1">
        <h2 id="credential-delete-row-title" className="text-balance font-semibold text-sm">
          {deletion.inUse ? (
            <>
              <span className="font-mono [overflow-wrap:anywhere]">{credential.name}</span> is in
              use and was not deleted
            </>
          ) : (
            <>
              Delete <span className="font-mono [overflow-wrap:anywhere]">{credential.name}</span>?
            </>
          )}
        </h2>
        <p className="max-w-[90ch] text-pretty text-foreground-muted">
          {deletion.inUse
            ? "Register a version of each configuration below that names another credential, and change or delete each schedule. This listing stays here until you dismiss it."
            : `${CANNOT_REVERSE} ${REFUSED_WHEN}`}
        </p>
      </div>
      {deletion.inUse && (
        <div className="rounded-lg border bg-content px-3 py-2.5">
          <InUseList inUse={deletion.inUse} />
        </div>
      )}
      {deletion.error ? <CredentialError error={deletion.error} /> : null}
      <div className="flex flex-col gap-2 sm:flex-row sm:justify-end">
        <Button
          ref={cancelRef}
          variant="ghost"
          size="sm"
          className={cn(FOCUS_RAC, "max-sm:h-11")}
          isDisabled={deletion.pending}
          onPress={onClose}
        >
          {deletion.inUse ? "Dismiss" : "Cancel"}
        </Button>
        <Button
          variant={deletion.inUse ? "outline" : "danger"}
          size="sm"
          className={cn(FOCUS_RAC, "max-sm:h-11")}
          isPending={deletion.pending}
          onPress={deletion.send}
        >
          {deletion.inUse ? "Try again" : "Delete credential"}
        </Button>
      </div>
      <span className="sr-only" aria-live="polite">
        {deletion.pending ? "Deleting the credential" : ""}
      </span>
    </section>
  );
}

/* ---------- 3. Guided sheet: the facts of the credential first; a refusal becomes a list of steps ---------- */

function Steps({ inUse }: { inUse: CredentialInUse }) {
  const steps: [string, ReactNode][] = [
    ...inUse.configurations.map((configId): [string, ReactNode] => [
      `config-${configId}`,
      <>
        Register a version of{" "}
        <a href={configHref(configId)} className={cn(LINK, "font-mono [overflow-wrap:anywhere]")}>
          {configId}
        </a>{" "}
        that names another credential.
      </>,
    ]),
    ...inUse.schedules.map((schedule): [string, ReactNode] => [
      `schedule-${schedule}`,
      <>
        Change or delete the schedule{" "}
        <span className="font-mono [overflow-wrap:anywhere]">{schedule}</span>.
      </>,
    ]),
  ];
  return (
    <ol className="flex flex-col gap-2 text-sm">
      {steps.map(([key, step], index) => (
        <li key={key} className="grid grid-cols-[1.5rem_minmax(0,1fr)] items-baseline gap-2">
          <span className="text-right text-subtle text-xs tabular-nums">{index + 1}.</span>
          <span className="text-pretty">{step}</span>
        </li>
      ))}
      <li className="grid grid-cols-[1.5rem_minmax(0,1fr)] items-baseline gap-2">
        <span className="text-right text-subtle text-xs tabular-nums">{steps.length + 1}.</span>
        <span className="text-pretty">Come back here and press “Try again”.</span>
      </li>
    </ol>
  );
}

export function SheetDelete(props: DeleteProps) {
  const { credential, onClose } = props;
  const deletion = useDeletion(props);
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
  ];
  return (
    <Sheet
      isOpen
      onOpenChange={(open) => !open && !deletion.pending && onClose()}
      aria-label={`Delete ${credential.name}`}
      style={{ width: "min(26rem, calc(100vw - 16px))" }}
    >
      <div className="flex min-h-full flex-col gap-4 px-2 pt-2 text-sm">
        <div className="flex flex-col gap-1.5">
          <h2 className="text-balance font-semibold text-base">
            {deletion.inUse ? "Before you can delete it" : "Delete this credential?"}
          </h2>
          <p className="font-mono text-[13px] [overflow-wrap:anywhere]">{credential.name}</p>
        </div>
        {deletion.inUse ? (
          <>
            <p className="text-pretty text-foreground-muted">
              The Sync service refused the deletion
              {deletion.refusedAt && (
                <>
                  {" "}
                  at <DateText iso={deletion.refusedAt} />
                </>
              )}{" "}
              because these name the credential. This list is not updated while the sheet is open.
            </p>
            <Steps inUse={deletion.inUse} />
          </>
        ) : (
          <>
            {credential.description && (
              <p className="text-pretty text-foreground-muted [overflow-wrap:anywhere]">
                {credential.description}
              </p>
            )}
            <dl className="grid grid-cols-2 gap-x-4 gap-y-3 rounded-xl border px-3.5 py-3 text-[13px]">
              {facts.map(([term, value]) => (
                <div key={term} className="flex min-w-0 flex-col gap-0.5">
                  <dt className="text-subtle text-xs">{term}</dt>
                  <dd className="min-w-0 [overflow-wrap:anywhere]">{value}</dd>
                </div>
              ))}
            </dl>
            <p className="text-pretty text-foreground-muted">
              {CANNOT_REVERSE} {REFUSED_WHEN}
            </p>
          </>
        )}
        {deletion.error ? <CredentialError error={deletion.error} /> : null}
        <div className="sticky bottom-0 mt-auto flex flex-col gap-2 border-t bg-secondary pt-3 pb-1 sm:flex-row sm:justify-end">
          <Button
            variant="ghost"
            size="sm"
            className="max-sm:h-11"
            isDisabled={deletion.pending}
            autoFocus
            onPress={onClose}
          >
            {deletion.inUse ? "Close" : "Cancel"}
          </Button>
          <Button
            variant={deletion.inUse ? "outline" : "danger"}
            size="sm"
            className="max-sm:h-11"
            isPending={deletion.pending}
            onPress={deletion.send}
          >
            {deletion.inUse ? "Try again" : "Delete credential"}
          </Button>
        </div>
        <span className="sr-only" aria-live="polite">
          {deletion.pending ? "Deleting the credential" : ""}
        </span>
      </div>
    </Sheet>
  );
}
