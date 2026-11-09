import { Button, buttonVariants } from "@infrahub/ui";
import { type ErrorComponentProps, Link, useRouterState } from "@tanstack/react-router";
import { cn } from "tailwind-variants";

import { useDocumentTitle } from "#/lib/title";

import { CODE_CHIP, FOCUS } from "./classes";
import { StateIcon } from "./icons";
import { PageMessage } from "./page";

/** The router's page for an address that matches no route. */
export function NotFoundPage() {
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  useDocumentTitle("Page not found");
  return (
    <PageMessage
      title="No page at this address"
      actions={
        <>
          <Link to="/" className={buttonVariants({ variant: "primary", size: "sm" })}>
            View configurations
          </Link>
          <Link to="/runs" className={buttonVariants({ variant: "outline", size: "sm" })}>
            View runs
          </Link>
        </>
      }
    >
      <p>
        Infrahub Sync has no page at <code className={CODE_CHIP}>{pathname}</code>.
      </p>
    </PageMessage>
  );
}

/** The router's page for an error thrown while a page renders; the header stays usable. */
export function AppErrorPage({ error }: ErrorComponentProps) {
  useDocumentTitle("Error");
  return (
    <PageMessage
      alert
      title="This page could not be shown"
      icon={<StateIcon state="failed" className="size-5 shrink-0 text-danger" />}
      actions={
        <>
          <Button variant="primary" size="sm" onPress={() => window.location.reload()}>
            Reload page
          </Button>
          <Link to="/" className={buttonVariants({ variant: "outline", size: "sm" })}>
            View configurations
          </Link>
        </>
      }
    >
      <p>
        An error in the web app stopped this page. Reload the page to try again. If the error
        happens again, include the error details when you report it.
      </p>
      <details className="w-full max-w-2xl text-subtle text-xs">
        <summary className={cn("w-fit cursor-pointer rounded-sm", FOCUS)}>Error details</summary>
        <pre className="mt-2 overflow-x-auto whitespace-pre-wrap rounded-xl border bg-content-muted px-3 py-2 font-mono [overflow-wrap:anywhere]">
          {error instanceof Error ? error.message : String(error)}
        </pre>
      </details>
    </PageMessage>
  );
}
