/* PROTOTYPE (spec 027, T074). The three versions of the delete confirmation with the in-use listing. */
import { Button } from "@infrahub/ui";
import { useEffect, useRef, useState } from "react";
import { cn } from "tailwind-variants";

import { credentialInUse } from "#/api/queries";
import { announce } from "#/components/announcer";
import { Notice } from "#/components/page";
import { DialogFrame, Field, finePointer, INPUT } from "#/features/runs/form";

import { deleteCredential } from "./data";
import { CredentialError, type DeleteProps, InUseList } from "./shared";

const CANNOT_REVERSE =
  "A deletion cannot be reversed. A run that starts afterwards and names this credential is refused.";

/** Sends the deletion and keeps its refusal. */
function useDeletion({ credential, onDeleted }: DeleteProps) {
  const [error, setError] = useState<unknown>();
  const [pending, setPending] = useState(false);
  async function send() {
    setError(undefined);
    setPending(true);
    const result = await deleteCredential(credential.name);
    setPending(false);
    if (!result.ok) {
      setError(result.error);
      if (credentialInUse(result.error)) {
        announce(`${credential.name} was not deleted: it is in use.`);
      }
      return;
    }
    announce(`Credential ${credential.name} deleted.`);
    onDeleted();
  }
  const inUse = credentialInUse(error);
  return { error: inUse ? undefined : error, inUse, pending, send };
}

function InUseNotice({ name, children }: { name: string; children: React.ReactNode }) {
  return (
    <Notice tone="warning" role="alert" title={`${name} is in use and was not deleted`}>
      <p>
        Register a version of each configuration that names another credential, and change or delete
        each schedule. Then delete the credential.
      </p>
      {children}
    </Notice>
  );
}

/* ---------- 1. Dialog: confirm in a modal; a refusal replaces the question with the listing ---------- */

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
        <InUseNotice name={credential.name}>
          <InUseList inUse={deletion.inUse} />
        </InUseNotice>
      ) : (
        <p className="text-pretty text-foreground-muted text-sm">
          The Sync service refuses the deletion while the latest version of a configuration, or a
          schedule that is not deleted, names the credential, and lists them here.
        </p>
      )}
    </DialogFrame>
  );
}

/* ---------- 2. Type the name: the button works only after the name is typed ---------- */

export function TypedDialog(props: DeleteProps) {
  const { credential, onClose } = props;
  const deletion = useDeletion(props);
  const [typed, setTyped] = useState("");
  const matches = typed.trim() === credential.name;
  const title = `Delete ${credential.name}?`;
  return (
    <DialogFrame
      isOpen
      onOpenChange={(open) => !open && onClose()}
      title={title}
      ariaLabel={title}
      description={CANNOT_REVERSE}
      error={
        deletion.inUse ? (
          <InUseNotice name={credential.name}>
            <InUseList inUse={deletion.inUse} />
          </InUseNotice>
        ) : deletion.error ? (
          <CredentialError error={deletion.error} />
        ) : undefined
      }
      submitLabel="Delete credential"
      submitVariant="danger"
      pendingLabel="Deleting the credential"
      isPending={deletion.pending}
      isDisabled={!matches || Boolean(deletion.inUse)}
      disabledReason={
        deletion.inUse
          ? "The Sync service deletes a credential only when no configuration and no schedule names it."
          : "Type the name of the credential to delete it."
      }
      dismissLabel={deletion.inUse ? "Close" : "Cancel"}
      onSubmit={deletion.send}
    >
      <Field
        id="delete-credential-name"
        label={
          <>
            Type <span className="font-mono [overflow-wrap:anywhere]">{credential.name}</span> to
            confirm
          </>
        }
      >
        <input
          id="delete-credential-name"
          className={cn(INPUT, "font-mono")}
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          autoComplete="off"
          autoCapitalize="none"
          spellCheck={false}
          data-1p-ignore
          data-lpignore="true"
          // biome-ignore lint/a11y/noAutofocus: only with a fine pointer, inside a modal that just opened.
          autoFocus={finePointer()}
        />
      </Field>
    </DialogFrame>
  );
}

/* ---------- 3. On the page: no modal; the listing stays on the page while the person opens the links ---------- */

export function PageConfirm(props: DeleteProps) {
  const { credential, onClose } = props;
  const deletion = useDeletion(props);
  const ref = useRef<HTMLElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    ref.current?.scrollIntoView({ block: "nearest" });
    cancelRef.current?.focus();
  }, []);
  const dismiss = (
    <Button ref={cancelRef} size="sm" variant="ghost" className="max-sm:h-11" onPress={onClose}>
      {deletion.inUse ? "Dismiss" : "Cancel"}
    </Button>
  );
  if (deletion.inUse) {
    return (
      <section ref={ref} aria-label={`${credential.name} is in use`} className="scroll-mt-20">
        <Notice
          tone="warning"
          role="alert"
          title={`${credential.name} is in use and was not deleted`}
          action={dismiss}
        >
          <p>
            Register a version of each configuration that names another credential, and change or
            delete each schedule. Then delete the credential. This message stays until you dismiss
            it.
          </p>
          <InUseList inUse={deletion.inUse} />
        </Notice>
      </section>
    );
  }
  return (
    <section
      ref={ref}
      aria-labelledby="delete-strip-title"
      className="flex scroll-mt-20 flex-col gap-3 rounded-xl border border-danger/30 bg-danger-surface px-4 py-3 text-sm"
    >
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-3">
        <div className="flex min-w-0 flex-1 basis-72 flex-col gap-1">
          <h2 id="delete-strip-title" className="font-medium text-danger [overflow-wrap:anywhere]">
            Delete {credential.name}?
          </h2>
          <p className="text-pretty text-foreground-muted">{CANNOT_REVERSE}</p>
        </div>
        <div className="flex flex-col gap-2 max-sm:w-full sm:flex-row">
          {dismiss}
          <Button
            size="sm"
            variant="danger"
            className="max-sm:h-11"
            isPending={deletion.pending}
            onPress={() => void deletion.send()}
          >
            Delete credential
          </Button>
        </div>
      </div>
      {Boolean(deletion.error) && <CredentialError error={deletion.error} />}
      <span className="sr-only" aria-live="polite">
        {deletion.pending ? "Deleting the credential" : ""}
      </span>
    </section>
  );
}
