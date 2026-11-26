import { describe, expect, it } from "vitest";

import type { Credential } from "#/api/types";

import {
  changedSinceCreation,
  countBySet,
  FIELD_SETS,
  fieldSetOf,
  fieldsLabel,
  findCredentials,
} from "./fields";

function credential(over: Partial<Credential> = {}): Credential {
  return {
    name: "netbox-token",
    description: "",
    fields: ["token"],
    revision: 1,
    created_by: "admin",
    created_at: "2026-11-20T10:00:00Z",
    updated_by: "admin",
    updated_at: "2026-11-20T10:00:00Z",
    ...over,
  };
}

describe("the field sets of a credential value", () => {
  // The Sync API, the CLI and the shared client accept exactly these three sets.
  it("offers a token, a username and a password, and a username and a token", () => {
    expect(FIELD_SETS.map((set) => [...set.fields])).toEqual([
      ["token"],
      ["username", "password"],
      ["username", "token"],
    ]);
  });

  it.each([
    [["token"], "token", "Token"],
    [["password", "username"], "username-password", "Username and password"],
    [["token", "username"], "username-token", "Username and token"],
  ] as const)("reads the set of %j as %s", (fields, id, label) => {
    expect(fieldSetOf(fields)).toBe(id);
    expect(fieldsLabel(fields)).toBe(label);
  });
});

describe("the search of the Credentials page", () => {
  const credentials = [
    credential({ name: "aci-account", fields: ["username", "password"], updated_by: "alice" }),
    credential({
      name: "infrahub-token",
      description: "Lab Infrahub",
      updated_at: "2026-11-22T10:00:00Z",
    }),
    credential({ name: "netbox-token", updated_at: "2026-11-21T10:00:00Z" }),
  ];
  const names = (found: Credential[]) => found.map((item) => item.name);

  it("keeps the order of the Sync API, by name, without a search", () => {
    expect(names(findCredentials(credentials, { query: "", set: "all", order: "name" }))).toEqual([
      "aci-account",
      "infrahub-token",
      "netbox-token",
    ]);
  });

  it.each([
    ["netbox", ["netbox-token"]],
    ["  LAB ", ["infrahub-token"]],
    ["alice", ["aci-account"]],
    ["nothing", []],
  ])("finds %j in the name, the description or the account", (query, expected) => {
    expect(names(findCredentials(credentials, { query, set: "all", order: "name" }))).toEqual(
      expected
    );
  });

  it("filters by the field set", () => {
    expect(
      names(findCredentials(credentials, { query: "", set: "username-password", order: "name" }))
    ).toEqual(["aci-account"]);
  });

  it("keeps the credential whose form is open, whatever the search", () => {
    expect(
      names(
        findCredentials(credentials, {
          query: "netbox",
          set: "all",
          order: "name",
          keep: "aci-account",
        })
      )
    ).toEqual(["aci-account", "netbox-token"]);
  });

  it("orders by the last change, newest first", () => {
    expect(
      names(findCredentials(credentials, { query: "", set: "all", order: "changed" }))
    ).toEqual(["infrahub-token", "netbox-token", "aci-account"]);
  });

  it("counts the credentials of each field set", () => {
    expect(countBySet(credentials)).toEqual({
      all: 3,
      token: 2,
      "username-password": 1,
      "username-token": 0,
    });
  });
});

describe("changedSinceCreation", () => {
  const created = { revision: 1, created_at: "2026-11-01T10:00:00Z", created_by: "admin" };

  it("is false while the last change is the creation", () => {
    expect(
      changedSinceCreation({ ...created, updated_at: created.created_at, updated_by: "admin" })
    ).toBe(false);
  });

  it("is true after a change of the description, which leaves the revision at 1", () => {
    expect(
      changedSinceCreation({ ...created, updated_at: "2026-11-02T09:00:00Z", updated_by: "alice" })
    ).toBe(true);
  });

  it("is true after a change of the value by the same account at the same time", () => {
    expect(
      changedSinceCreation({
        ...created,
        revision: 2,
        updated_at: created.created_at,
        updated_by: "admin",
      })
    ).toBe(true);
  });

  it("is true when another account changed it at the same time", () => {
    expect(
      changedSinceCreation({ ...created, updated_at: created.created_at, updated_by: "alice" })
    ).toBe(true);
  });
});
