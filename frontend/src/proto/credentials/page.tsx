/* PROTOTYPE (spec 027, T074). The Credentials page around one list version, one form version and one delete version. */
import { Button } from "@infrahub/ui";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { type ComponentType, useRef, useState } from "react";

import { credentialsQuery } from "#/api/queries";
import type { Credential } from "#/api/types";
import { Bar, CODE_CHIP, EmptyPanel, ErrorPanel, Notice } from "#/components/page";

import { type Dataset, listCredentials } from "./data";
import {
  type Action,
  type DeleteProps,
  DescribeDialog,
  type FormProps,
  type ListProps,
} from "./shared";

export function CredentialsPage({
  administrator,
  dataset,
  List,
  Form,
  Delete,
}: {
  administrator: boolean;
  dataset: Dataset;
  List: ComponentType<ListProps>;
  Form: ComponentType<FormProps>;
  Delete: ComponentType<DeleteProps>;
}) {
  const client = useQueryClient();
  // The key of the T073 query, with the sample list in place of the request.
  const queryKey = [...credentialsQuery().queryKey, "prototype", dataset];
  const credentials = useQuery<Credential[]>({
    queryKey,
    queryFn: () => listCredentials(dataset),
    staleTime: Number.POSITIVE_INFINITY,
    retry: false,
  });
  const headingRef = useRef<HTMLHeadingElement>(null);
  const createRef = useRef<HTMLButtonElement>(null);
  const [creating, setCreating] = useState(false);
  const [open, setOpen] = useState<{ action: Action; credential: Credential }>();
  const [deleted, setDeleted] = useState<string>();

  /** Reads the list again and returns focus to the control that opened the form. */
  function close(name?: string) {
    setOpen(undefined);
    setCreating(false);
    void client.invalidateQueries({ queryKey });
    requestAnimationFrame(() => {
      const trigger = name
        ? document.querySelector<HTMLElement>(`[data-actions-for="${CSS.escape(name)}"]`)
        : createRef.current;
      (trigger ?? headingRef.current)?.focus();
    });
  }

  const list = credentials.data ?? [];
  const newCredential = (
    <Button ref={createRef} size="sm" variant="primary" onPress={() => setCreating(true)}>
      New credential
    </Button>
  );

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
          {administrator && newCredential}
        </div>
        <p className="max-w-[80ch] text-pretty text-foreground-muted">
          The tokens and passwords that the Sync service reads when a run connects to a source or a
          destination. A configuration names one with{" "}
          <code className={CODE_CHIP}>credential: &lt;name&gt;</code>. No page shows a stored value.
        </p>
      </header>
      <div className="flex flex-col gap-4">
        {!administrator && (
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
        {creating && <Form onClose={() => close()} />}
        {open?.action === "value" && (
          <Form credential={open.credential} onClose={() => close(open.credential.name)} />
        )}
        {open?.action === "delete" && (
          <Delete
            credential={open.credential}
            onClose={() => close(open.credential.name)}
            onDeleted={() => {
              setDeleted(open.credential.name);
              close();
            }}
          />
        )}
        {credentials.isError && !credentials.data ? (
          <ErrorPanel error={credentials.error} onRetry={() => void credentials.refetch()} />
        ) : credentials.isPending ? (
          <div aria-busy="true" className="flex flex-col gap-2">
            <Bar className="h-10 w-full" />
            <Bar className="h-10 w-full" />
            <Bar className="h-10 w-full" />
          </div>
        ) : list.length === 0 ? (
          <EmptyPanel title="No credentials yet">
            <p className="max-w-[60ch] text-pretty text-foreground-muted">
              {administrator
                ? "Create a credential for each source and destination that needs a token or a password. Then name it in the configuration."
                : "A configuration that needs a token or a password names a credential. Ask an administrator to create one."}
            </p>
          </EmptyPanel>
        ) : (
          <List
            credentials={list}
            administrator={administrator}
            onAction={(action, credential) => {
              setCreating(false);
              setOpen({ action, credential });
            }}
          />
        )}
      </div>
      {open?.action === "describe" && (
        <DescribeDialog credential={open.credential} onClose={() => close(open.credential.name)} />
      )}
    </>
  );
}
