/*
 * PROTOTYPE (spec 027, T074). A throwaway route for the maintainer to pick one version per screen.
 * Nothing links to it. Remove this file and src/proto/credentials after the choice.
 */
import { createFileRoute, useNavigate } from "@tanstack/react-router";

import { CredentialsPrototype, type ProtoSearch } from "#/proto/credentials/harness";

const oneOf = <T extends string>(value: unknown, allowed: readonly T[]): T | undefined =>
  allowed.includes(value as T) ? (value as T) : undefined;

export const Route = createFileRoute("/proto/credentials")({
  validateSearch: (s: Record<string, unknown>): ProtoSearch => ({
    screen: oneOf(s.screen, ["form", "delete", "used"] as const),
    v: s.v === 2 || s.v === 3 ? s.v : undefined,
    account: oneOf(s.account, ["other"] as const),
    list: oneOf(s.list, ["empty", "loading", "error"] as const),
    answer: oneOf(s.answer, ["key", "fields", "forbidden", "no-answer"] as const),
    run: oneOf(s.run, ["revision-changed", "none"] as const),
  }),
  component: PrototypeRoute,
});

function PrototypeRoute() {
  const search = Route.useSearch();
  const navigate = useNavigate({ from: "/proto/credentials" });
  return (
    <CredentialsPrototype
      search={search}
      onSearch={(next) => void navigate({ search: next, replace: true })}
    />
  );
}
