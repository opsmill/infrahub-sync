import { Button } from "@infrahub/ui";
import { Link } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { cn } from "tailwind-variants";

import { useDeleteCredential } from "#/api/actions";
import { type CredentialInUse, credentialInUse } from "#/api/queries";
import type { Credential } from "#/api/types";
import { announce } from "#/components/announcer";
import { FOCUS_RAC } from "#/components/classes";
import { LINK } from "#/features/runs/form";

import { CredentialError } from "./CredentialForm";

/**
 * The configurations and schedules that name a credential, from a `409 credential-in-use`
 * refusal. The web interface has no page for one schedule, so a schedule is named, not linked.
 */
function InUseList({ inUse }: { inUse: CredentialInUse }) {
  return (
    <div className="flex flex-col gap-2 text-sm">
      {inUse.configurations.length > 0 && (
        <div>
          <p className="text-subtle text-xs">Configurations whose latest version names it</p>
          <ul className="mt-1 flex flex-col">
            {inUse.configurations.map((configId) => (
              <li key={configId} className="flex min-h-6 pointer-coarse:min-h-11 items-center">
                <Link
                  to="/configs/$configId"
                  params={{ configId }}
                  target="_blank"
                  className={cn(LINK, "[overflow-wrap:anywhere]")}
                >
                  {configId}
                </Link>
              </li>
            ))}
          </ul>
        </div>
      )}
      {inUse.schedules.length > 0 && (
        <div>
          <p className="text-subtle text-xs">Schedules that name it</p>
          <ul className="mt-1 flex flex-col">
            {inUse.schedules.map((schedule) => (
              <li
                key={schedule}
                className="flex min-h-6 items-center text-foreground [overflow-wrap:anywhere]"
              >
                {schedule}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

/**
 * "Delete", in place of the row: first the question, then, when the Sync API refuses because a
 * configuration or a schedule names the credential, the listing of them. The listing stays until
 * the person dismisses it, and "Try again" sends the deletion again. Escape cancels.
 */
export function CredentialRowDelete({
  credential,
  onClose,
  onDeleted,
}: {
  credential: Credential;
  onClose: () => void;
  onDeleted: () => void;
}) {
  const remove = useDeleteCredential();
  // Kept in state, so the listing stays in view while "Try again" is pending.
  const [inUse, setInUse] = useState<CredentialInUse>();
  const error = credentialInUse(remove.error) ? undefined : remove.error;
  const refused = inUse !== undefined;
  const ref = useRef<HTMLElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    cancelRef.current?.focus();
    ref.current?.scrollIntoView({ block: "nearest" });
  }, []);
  useEffect(() => {
    if (refused) ref.current?.focus();
  }, [refused]);

  function send() {
    remove.mutate(
      {
        // Each press is a new intent: after a refusal the person may have changed the
        // configurations, so "Try again" must not replay the stored refusal.
        params: {
          path: { name: credential.name },
          header: { "Idempotency-Key": crypto.randomUUID() },
        },
      },
      {
        onSuccess: () => {
          announce(`Credential ${credential.name} deleted.`);
          onDeleted();
        },
        onError: (failure) => {
          const listing = credentialInUse(failure);
          if (listing) {
            setInUse(listing);
            announce(`${credential.name} was not deleted: it is in use.`);
          }
        },
      }
    );
  }

  return (
    <section
      ref={ref}
      tabIndex={-1}
      aria-labelledby="credential-delete-title"
      className={cn(
        "flex scroll-mt-20 flex-col gap-3 border-l-2 px-4 py-4 text-sm outline-none",
        inUse ? "border-amber-600" : "border-danger"
      )}
      onKeyDown={(e) => {
        if (e.key === "Escape" && !remove.isPending) {
          e.preventDefault();
          onClose();
        }
      }}
    >
      <div className="flex flex-col gap-1">
        <h2 id="credential-delete-title" className="text-balance font-semibold text-sm">
          {inUse ? (
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
          {inUse
            ? "Register a version of each configuration below that names another credential, and change or delete each schedule. This listing stays here until you dismiss it."
            : "A deletion cannot be reversed. The Sync service refuses the deletion while the latest version of a configuration, or a schedule that is not deleted, names the credential, and then lists them."}
        </p>
      </div>
      {inUse && (
        <div className="rounded-lg border bg-content px-3 py-2.5">
          <InUseList inUse={inUse} />
        </div>
      )}
      <CredentialError error={error} />
      <div className="flex flex-col gap-2 sm:flex-row sm:justify-end">
        <Button
          ref={cancelRef}
          variant="ghost"
          size="sm"
          className={cn(FOCUS_RAC, "max-sm:h-11")}
          isDisabled={remove.isPending}
          onPress={onClose}
        >
          {inUse ? "Dismiss" : "Cancel"}
        </Button>
        <Button
          variant={inUse ? "outline" : "danger"}
          size="sm"
          className={cn(FOCUS_RAC, "max-sm:h-11")}
          isPending={remove.isPending}
          onPress={send}
        >
          {inUse ? "Try again" : "Delete credential"}
        </Button>
      </div>
      <span className="sr-only" aria-live="polite">
        {remove.isPending ? "Deleting the credential" : ""}
      </span>
    </section>
  );
}
