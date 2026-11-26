import { describe, expect, it } from "vitest";

import {
  blockingNamesake,
  CONFIGURATION_NAME_IN_USE,
  checkedCredential,
  checkedCredentialState,
  credentialRefusalNextStep,
  credentialRefusalTitle,
  nameInUseOf,
  otherNamesake,
  refusedBlockLabel,
  refusedCredentialHasRemedy,
  refusedCredentialResolved,
  refusedCredentialText,
  sameNameHint,
  sameNameText,
} from "./package-review";

const envelope = (code: string, status: number, details?: Record<string, unknown> | null) => ({
  error: {
    code,
    status,
    message: "A configuration named 'from-netbox' already exists (cfg-1).",
    ...(details === undefined ? {} : { details }),
  },
});

describe("nameInUseOf", () => {
  it("names the configuration of a configuration-name-in-use refusal", () => {
    const error = envelope(CONFIGURATION_NAME_IN_USE, 409, {
      config_id: "cfg-1",
      name: "from-netbox",
    });
    expect(nameInUseOf(error)).toEqual({ configId: "cfg-1", name: "from-netbox" });
  });

  it("still reports the refusal when details are missing or not strings", () => {
    expect(nameInUseOf(envelope(CONFIGURATION_NAME_IN_USE, 409))).toEqual({});
    expect(nameInUseOf(envelope(CONFIGURATION_NAME_IN_USE, 409, null))).toEqual({});
    expect(
      nameInUseOf(envelope(CONFIGURATION_NAME_IN_USE, 409, { config_id: 7, name: "" }))
    ).toEqual({});
  });

  it.each([
    ["idempotency-conflict", 409],
    ["idempotency-in-progress", 409],
    ["forbidden", 403],
  ])("ignores the refusal %s (HTTP %i)", (code, status) => {
    expect(nameInUseOf(envelope(code, status, { config_id: "cfg-1", name: "x" }))).toBeUndefined();
  });

  it.each([
    null,
    undefined,
    new TypeError("Failed to fetch"),
  ])("ignores an error without an envelope: %s", (error) => {
    expect(nameInUseOf(error)).toBeUndefined();
  });
});

describe("otherNamesake", () => {
  const items = [
    { config_id: "cfg-1", name: "from-netbox" },
    { config_id: "cfg-2", name: "from-netbox" },
  ];

  it("returns any configuration with the name for a new configuration", () => {
    expect(otherNamesake(items, undefined)).toEqual({ configId: "cfg-1", name: "from-netbox" });
  });

  it("skips the configuration the version is added to", () => {
    expect(otherNamesake(items.slice(0, 1), "cfg-1")).toBeUndefined();
    expect(otherNamesake(items, "cfg-1")).toEqual({ configId: "cfg-2", name: "from-netbox" });
  });

  it("returns nothing while the configurations are not read", () => {
    expect(otherNamesake(undefined, undefined)).toBeUndefined();
    expect(otherNamesake([], undefined)).toBeUndefined();
  });
});

describe("blockingNamesake", () => {
  const namesakes = [{ config_id: "cfg-1", name: "from-netbox" }];
  const refusal = nameInUseOf(
    envelope(CONFIGURATION_NAME_IN_USE, 409, { config_id: "cfg-9", name: "from-netbox" })
  );

  it("blocks Register when a configuration listed with the name exists before sending", () => {
    expect(
      blockingNamesake({
        refused: undefined,
        namesakes,
        ownConfigId: undefined,
        checksName: true,
        sent: false,
      })
    ).toEqual({ configId: "cfg-1", name: "from-netbox" });
  });

  it("keeps Retry available when the answer was lost and the listing now has the name", () => {
    // The request was sent, its answer was lost (no refusal), and a refetch lists the
    // configuration that this request itself may have registered.
    expect(
      blockingNamesake({
        refused: nameInUseOf(new TypeError("Failed to fetch")),
        namesakes,
        ownConfigId: undefined,
        checksName: true,
        sent: true,
      })
    ).toBeUndefined();
  });

  it("still blocks on a configuration-name-in-use refusal after sending", () => {
    expect(
      blockingNamesake({
        refused: refusal,
        namesakes: undefined,
        ownConfigId: undefined,
        checksName: true,
        sent: true,
      })
    ).toEqual({ configId: "cfg-9", name: "from-netbox" });
  });

  it("ignores the listing when the package keeps the configuration's name", () => {
    expect(
      blockingNamesake({
        refused: undefined,
        namesakes,
        ownConfigId: "cfg-2",
        checksName: false,
        sent: false,
      })
    ).toBeUndefined();
  });
});

describe("the words of a name in use", () => {
  it("names the configuration when its name is known", () => {
    expect(sameNameText("from-netbox")).toBe("A configuration named from-netbox already exists.");
    expect(sameNameHint("from-netbox")).toBe(
      "Add the package as a new version of from-netbox instead."
    );
  });

  it("never shows a configuration identifier", () => {
    expect(sameNameText(undefined)).not.toContain("cfg-");
    expect(sameNameHint(undefined)).not.toContain("cfg-");
  });
});

describe("a registration refused for its stored credentials", () => {
  const missing = {
    block: "source",
    name: "apic-account",
    code: "credential-not-found",
    fields: ["username", "password"],
  };
  const lacking = {
    block: "destination",
    name: "infrahub-token",
    code: "credential-field-missing",
    fields: ["token"],
  };

  it("counts the stored credentials in the title", () => {
    expect(credentialRefusalTitle(1)).toBe(
      "Not registered: a stored credential that the configuration names cannot be used"
    );
    expect(credentialRefusalTitle(2)).toBe(
      "Not registered: 2 stored credentials that the configuration names cannot be used"
    );
  });

  it.each([
    ["source", "Source"],
    ["destination", "Destination"],
    ["other", "other"],
  ])("labels the block %s as %s", (block, label) => {
    expect(refusedBlockLabel(block)).toBe(label);
  });

  it("names every field the block needs when the credential does not exist", () => {
    expect(refusedCredentialText(missing)).toBe(
      "does not exist. The block needs a username and a password."
    );
  });

  it("names the fields that the credential lacks", () => {
    expect(refusedCredentialText(lacking)).toBe("lacks a token.");
  });

  it("does not leave the sentence open when the refusal names no field", () => {
    expect(refusedCredentialText({ ...lacking, fields: [] })).toBe("lacks a value.");
  });

  it("is not resolved before the stored credentials are read", () => {
    expect(refusedCredentialResolved(missing, undefined)).toBe(false);
  });

  it("is not resolved while the credential does not exist", () => {
    expect(refusedCredentialResolved(missing, [{ name: "other", fields: ["token"] }])).toBe(false);
  });

  it("is not resolved while the credential lacks one of the fields", () => {
    const stored = [{ name: "apic-account", fields: ["username", "token"] }];
    expect(refusedCredentialResolved(missing, stored)).toBe(false);
  });

  it("is resolved once the credential exists with every field", () => {
    const stored = [{ name: "apic-account", fields: ["password", "username"] }];
    expect(refusedCredentialResolved(missing, stored)).toBe(true);
  });
});

describe("checkedCredential", () => {
  const content = {
    configuration: {
      name: "from-netbox",
      source: { name: "netbox", credential: "netbox-token" },
      destination: { name: "infrahub" },
    },
  } as unknown as Parameters<typeof checkedCredential>[1];

  it("names the stored credential of the block that the finding is located at", () => {
    const finding = { code: "credential-not-found", location: "/configuration/source/credential" };
    expect(checkedCredential(finding, content)).toEqual({
      block: "source",
      name: "netbox-token",
      code: "credential-not-found",
      fields: [],
    });
  });

  it.each([
    ["another code", { code: "missing-adapter", location: "/configuration/source/credential" }],
    ["another location", { code: "credential-not-found", location: "/configuration/source" }],
    ["no location", { code: "credential-not-found", location: null }],
    [
      "a block that names no credential",
      { code: "credential-field-missing", location: "/configuration/destination/credential" },
    ],
  ])("returns nothing for %s", (_, finding) => {
    expect(checkedCredential(finding, content)).toBeUndefined();
  });

  it("returns nothing before the package is read", () => {
    const finding = { code: "credential-not-found", location: "/configuration/source/credential" };
    expect(checkedCredential(finding, undefined)).toBeUndefined();
  });

  it("is not resolved by the list when the check does not give the lacking fields", () => {
    const finding = {
      block: "source",
      name: "netbox-token",
      code: "credential-field-missing",
      fields: [],
    };
    const stored = [{ name: "netbox-token", fields: ["username"] }];
    expect(refusedCredentialResolved(finding, stored)).toBe(false);
  });
});

describe("a refusal for an auth_method that the adapter does not accept", () => {
  const unknownMethod = {
    block: "source",
    name: "apic-account",
    code: "credential-method-unknown",
    fields: [],
  };
  const missing = {
    block: "destination",
    name: "infrahub-token",
    code: "credential-not-found",
    fields: ["token"],
  };

  it("does not say that the credential lacks a value", () => {
    expect(refusedCredentialText(unknownMethod)).toBe(
      "cannot be used: the adapter of the block does not accept the auth_method that the package sets."
    );
  });

  it("has no remedy on the Credentials page", () => {
    expect(refusedCredentialHasRemedy(unknownMethod)).toBe(false);
    expect(refusedCredentialHasRemedy(missing)).toBe(true);
  });

  it("is never resolved by a stored credential", () => {
    const stored = [{ name: "apic-account", fields: ["username", "password"] }];
    expect(refusedCredentialResolved(unknownMethod, stored)).toBe(false);
  });

  it("tells the user to correct the package", () => {
    expect(credentialRefusalNextStep([unknownMethod, missing], [])).toContain(
      "Correct the package, then add it again."
    );
  });

  it("tells the user to press Register again when every finding has a remedy", () => {
    expect(credentialRefusalNextStep([missing], [])).toBe(
      "The package stays open here. Press Register again when each credential can be used."
    );
    expect(
      credentialRefusalNextStep([missing], [{ name: "infrahub-token", fields: ["token"] }])
    ).toBe("Every credential can be used now. Press Register again.");
  });
});

describe("checkedCredentialState", () => {
  const content = {
    configuration: {
      name: "from-netbox",
      source: { name: "netbox", credential: "netbox-token" },
      destination: { name: "infrahub", credential: "infrahub-token" },
    },
  } as unknown as Parameters<typeof checkedCredentialState>[1];
  const findings = [
    { code: "credential-not-found", location: "/configuration/source/credential" },
    { code: "credential-field-missing", location: "/configuration/destination/credential" },
    { code: "missing-mapping", location: "/configuration/schema_mapping" },
  ];

  const at = "2026-11-25T10:00:00Z";

  it("gives each named credential with its revision and last change, or absent", () => {
    const stored = [{ name: "infrahub-token", revision: 3, updated_at: at }];
    expect(checkedCredentialState(findings, content, stored)).toBe(
      `infrahub-token:3@${at} netbox-token:absent`
    );
  });

  it("changes when a credential is deleted and created again with the same revision", () => {
    const first = checkedCredentialState(findings, content, [
      { name: "infrahub-token", revision: 1, updated_at: at },
    ]);
    const again = checkedCredentialState(findings, content, [
      { name: "infrahub-token", revision: 1, updated_at: "2026-11-25T10:05:00Z" },
    ]);
    expect(again).not.toBe(first);
  });

  it("changes when a credential is created or gets a new value", () => {
    const before = checkedCredentialState(findings, content, [
      { name: "infrahub-token", revision: 3, updated_at: at },
    ]);
    const created = checkedCredentialState(findings, content, [
      { name: "infrahub-token", revision: 3, updated_at: at },
      { name: "netbox-token", revision: 1, updated_at: at },
    ]);
    const changed = checkedCredentialState(findings, content, [
      { name: "infrahub-token", revision: 4, updated_at: at },
    ]);
    expect(created).not.toBe(before);
    expect(changed).not.toBe(before);
  });

  it("does not change for a credential that no finding is about", () => {
    const one = checkedCredentialState(findings, content, [
      { name: "other", revision: 1, updated_at: at },
    ]);
    const two = checkedCredentialState(findings, content, [
      { name: "other", revision: 2, updated_at: at },
    ]);
    expect(one).toBe(two);
  });

  it("is undefined before the stored credentials are read", () => {
    expect(checkedCredentialState(findings, content, undefined)).toBeUndefined();
  });

  it("is undefined when no finding is about a stored credential", () => {
    expect(checkedCredentialState(findings.slice(2), content, [])).toBeUndefined();
  });
});
