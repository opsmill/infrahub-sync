// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  RouterProvider,
} from "@tanstack/react-router";
import {
  act,
  cleanup,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
} from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { credentialsQuery, meQuery } from "#/api/queries";
import type { DeclaredContent, PackageCheck } from "#/api/types";

import { CredentialRefusal, useRecheckOnCredentialChange } from "./CredentialRefusal";
import { ValidationResult } from "./PackageReview";

const content = {
  configuration: {
    name: "from-aci",
    source: { name: "aci", credential: "apic-account" },
    destination: { name: "infrahub", credential: "infrahub-token" },
  },
} as unknown as DeclaredContent;

const check = {
  destination_read: "read",
  total_findings: 2,
  findings: [
    {
      code: "credential-not-found",
      severity: "error",
      location: "/configuration/source/credential",
      message: "source names the stored credential 'apic-account', which does not exist",
    },
    {
      code: "missing-mapping",
      severity: "error",
      location: "/configuration/schema_mapping",
      message: "no kind is mapped",
    },
  ],
} as unknown as PackageCheck;

/** Renders inside a router with a `/credentials` route, as the signed-in account given. */
async function show(node: ReactNode, { administrator }: { administrator: boolean }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Number.POSITIVE_INFINITY } },
  });
  client.setQueryData(meQuery().queryKey, { administrator } as never);
  client.setQueryData(credentialsQuery().queryKey, []);
  const root = createRootRoute({ component: () => node });
  const router = createRouter({
    routeTree: root.addChildren([
      createRoute({ getParentRoute: () => root, path: "/" }),
      createRoute({ getParentRoute: () => root, path: "/credentials" }),
    ]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router as never} />
    </QueryClientProvider>
  );
  await waitFor(() =>
    expect(document.querySelector('[role="status"], [role="alert"]')).not.toBeNull()
  );
}

const result = (locked: boolean, onRetry = vi.fn()) => (
  <ValidationResult
    check={check}
    content={content}
    kind="findings"
    branch="main"
    validating={false}
    failed={false}
    locked={locked}
    onRetry={onRetry}
  />
);

afterEach(cleanup);

describe("ValidationResult with a finding about a stored credential", () => {
  it("shows an administrator the link that opens the create form for the credential", async () => {
    await show(result(false), { administrator: true });

    const link = screen.getByRole("link", { name: /Create credential apic-account/ });
    expect(link.getAttribute("href")).toBe("/credentials?new=apic-account");
    expect(link.getAttribute("target")).toBe("_blank");
    expect(screen.getAllByRole("link")).toHaveLength(1);
  });

  it("tells another account to ask an administrator", async () => {
    await show(result(false), { administrator: false });

    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.getByText(/Ask an administrator to create the credential/)).toBeTruthy();
  });

  it("runs the check again when Validate again is pressed", async () => {
    const onRetry = vi.fn();
    await show(result(false, onRetry), { administrator: true });

    fireEvent.click(screen.getByRole("button", { name: "Validate again" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("does not offer Validate again once the registration is sent", async () => {
    await show(result(true), { administrator: true });

    expect(screen.queryByRole("button", { name: "Validate again" })).toBeNull();
  });

  it("does not offer Validate again when no finding is about a stored credential", async () => {
    const other = { ...check, total_findings: 1, findings: check.findings.slice(1) };
    await show(
      <ValidationResult
        check={other}
        content={content}
        kind="findings"
        branch="main"
        validating={false}
        failed={false}
        locked={false}
        onRetry={vi.fn()}
      />,
      { administrator: true }
    );

    expect(screen.queryByRole("button", { name: "Validate again" })).toBeNull();
    expect(screen.queryByRole("link")).toBeNull();
  });
});

describe("CredentialRefusal", () => {
  const error = {
    error: {
      code: "configs-validation",
      reason: "credential-method-unknown",
      status: 422,
      message: "refused",
    },
  };

  it("offers a remedy for a missing credential and none for an unknown auth_method", async () => {
    await show(
      <CredentialRefusal
        error={error}
        findings={[
          { block: "source", name: "apic-account", code: "credential-method-unknown", fields: [] },
          {
            block: "destination",
            name: "infrahub-token",
            code: "credential-field-missing",
            fields: ["token"],
          },
        ]}
      />,
      { administrator: true }
    );

    const links = screen.getAllByRole("link");
    expect(links).toHaveLength(1);
    expect(links[0].getAttribute("href")).toBe("/credentials?value=infrahub-token");
    const notice = screen.getByRole("alert").textContent ?? "";
    expect(notice).toContain("does not accept the auth_method that the package sets");
    expect(notice).toContain("lacks a token.");
    expect(notice).toContain("Correct the package, then add it again.");
    expect(notice).not.toContain("Press Register again");
  });
});

describe("useRecheckOnCredentialChange", () => {
  const stored = (revision?: number) =>
    revision === undefined ? [] : [{ name: "apic-account", revision, fields: ["username"] }];

  function watch(initial: { paused: boolean; findings: PackageCheck["findings"] }) {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: Number.POSITIVE_INFINITY } },
    });
    client.setQueryData(credentialsQuery().queryKey, stored() as never);
    const onRecheck = vi.fn();
    const hook = renderHook(
      (props: typeof initial) => useRecheckOnCredentialChange({ ...props, content, onRecheck }),
      {
        initialProps: initial,
        wrapper: ({ children }: { children: ReactNode }) => (
          <QueryClientProvider client={client}>{children}</QueryClientProvider>
        ),
      }
    );
    /** Stores the credential with this revision, and waits until the hook has read it. */
    const store = async (revision?: number) => {
      await act(async () => {
        client.setQueryData(credentialsQuery().queryKey, stored(revision) as never);
        await new Promise((resolve) => setTimeout(resolve, 0));
      });
    };
    return { onRecheck, store, ...hook };
  }

  it("does not run the check for the state that the check found", async () => {
    const { onRecheck } = watch({ paused: false, findings: check.findings });
    expect(onRecheck).not.toHaveBeenCalled();
  });

  it("runs the check once when the credential of a finding is created", async () => {
    const { onRecheck, store } = watch({ paused: false, findings: check.findings });
    await store(1);
    expect(onRecheck).toHaveBeenCalledTimes(1);
  });

  it("runs the check again when the credential gets a new value", async () => {
    const { onRecheck, store } = watch({ paused: false, findings: check.findings });
    await store(1);
    await store(2);
    expect(onRecheck).toHaveBeenCalledTimes(2);
  });

  it("waits while a check runs or the registration is sent", async () => {
    const { onRecheck, store } = watch({ paused: true, findings: check.findings });
    await store(1);
    expect(onRecheck).not.toHaveBeenCalled();
  });

  it("takes the state of a new check as the new starting point", async () => {
    const { onRecheck, store, rerender } = watch({ paused: false, findings: check.findings });
    await store(1);
    rerender({ paused: false, findings: [...check.findings] });
    expect(onRecheck).toHaveBeenCalledTimes(1);
  });

  it("does nothing when no finding is about a stored credential", async () => {
    const { onRecheck, store } = watch({ paused: false, findings: check.findings.slice(1) });
    await store(1);
    expect(onRecheck).not.toHaveBeenCalled();
  });
});
