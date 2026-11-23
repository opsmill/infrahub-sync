/* PROTOTYPE (spec 027, T074). The Credentials page around one list version, one form version and one delete version. */
import { Button } from "@infrahub/ui";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { type ComponentType, type ReactNode, useRef, useState } from "react";

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

/** What is open on the page. The harness keeps it in the address, so it survives a version change. */
export type Open = { action: "create" } | { action: Action; name: string };

export type Placement = "overlay" | "row";

export function CredentialsPage({
  administrator,
  dataset,
  List,
  Form,
  formPlacement,
  Delete,
  deletePlacement,
  open,
  onOpen,
}: {
  administrator: boolean;
  dataset: Dataset;
  List: ComponentType<ListProps>;
  Form: ComponentType<FormProps>;
  formPlacement: Placement;
  Delete: ComponentType<DeleteProps>;
  deletePlacement: Placement;
  open: Open | undefined;
  onOpen: (open: Open | undefined) => void;
}) {
  const client = useQueryClient();
  // A key of the prototype only, with the sample list in place of the request.
  const queryKey = ["prototype", "credentials", dataset];
  const credentials = useQuery<Credential[]>({
    queryKey,
    queryFn: () => listCredentials(dataset),
    staleTime: Number.POSITIVE_INFINITY,
    retry: false,
  });
  const headingRef = useRef<HTMLHeadingElement>(null);
  const createRef = useRef<HTMLButtonElement>(null);
  const [deleted, setDeleted] = useState<string>();

  /** Reads the list again and returns focus to the control that opened the form. */
  function close(name?: string) {
    onOpen(undefined);
    void client.invalidateQueries({ queryKey });
    requestAnimationFrame(() => {
      const trigger = name
        ? document.querySelector<HTMLElement>(`[data-actions-for="${CSS.escape(name)}"]`)
        : createRef.current;
      (trigger ?? headingRef.current)?.focus();
    });
  }

  const list = credentials.data ?? [];
  const target =
    open && open.action !== "create" ? list.find((c) => c.name === open.name) : undefined;
  // Only an administrator can open a form; another account never views one.
  const canOpen = administrator && credentials.data !== undefined;

  let overlay: ReactNode = null;
  let slot: ListProps["slot"];
  if (canOpen && open?.action === "create") {
    const node = <Form onClose={() => close()} />;
    if (formPlacement === "row") slot = { at: null, node };
    else overlay = node;
  } else if (canOpen && target && open?.action === "value") {
    const node = <Form credential={target} onClose={() => close(target.name)} />;
    if (formPlacement === "row") slot = { at: target.name, node };
    else overlay = node;
  } else if (canOpen && target && open?.action === "delete") {
    const node = (
      <Delete
        credential={target}
        onClose={() => close(target.name)}
        onDeleted={() => {
          setDeleted(target.name);
          close();
        }}
      />
    );
    if (deletePlacement === "row") slot = { at: target.name, node };
    else overlay = node;
  } else if (canOpen && target && open?.action === "describe") {
    overlay = <DescribeDialog credential={target} onClose={() => close(target.name)} />;
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
              onPress={() => onOpen({ action: "create" })}
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
              <Button size="sm" variant="outline" onPress={() => onOpen({ action: "create" })}>
                New credential
              </Button>
            )}
          </EmptyPanel>
        ) : (
          <List
            credentials={list}
            administrator={administrator}
            onAction={(action, credential) => onOpen({ action, name: credential.name })}
            slot={slot}
          />
        )}
      </div>
      {overlay}
    </>
  );
}
