/*
 * PROTOTYPE (spec 027, T074). Throwaway: only frontend/proto/credentials.html loads this directory,
 * and the production build does not include that page. It sends no request: the credentials below
 * are sample data held in the memory of the tab, and each "request" is answered here with the body
 * the Sync API would return.
 */
import type { CredentialFields, SecretResult } from "#/api/actions";
import type { ApiErrorBody, Credential, CredentialUsed } from "#/api/types";

/** The three field sets of contracts/web-interface.md, in the order the form offers them. */
export const FIELD_SETS = [
  {
    id: "token",
    label: "Token",
    fields: ["token"],
    hint: "One API token. NetBox, Nautobot and Infrahub read a token.",
  },
  {
    id: "username-password",
    label: "Username and password",
    fields: ["username", "password"],
    hint: "For a system that signs in with a username and a password.",
  },
  {
    id: "username-token",
    label: "Username and token",
    fields: ["username", "token"],
    hint: "For a system that reads a token together with the username it belongs to.",
  },
] as const;

export type FieldSetId = (typeof FIELD_SETS)[number]["id"];

export const fieldSet = (id: FieldSetId) =>
  FIELD_SETS.find((set) => set.id === id) ?? FIELD_SETS[0];

/** The field set a stored credential holds, from the names of its fields. */
export function fieldSetOf(fields: string[]): FieldSetId {
  if (fields.includes("password")) return "username-password";
  return fields.includes("username") ? "username-token" : "token";
}

/** "Token", "Username and password" or "Username and token", for the "Fields set" column. */
export const fieldsLabel = (fields: string[]) => fieldSet(fieldSetOf(fields)).label;

const LONG_DESCRIPTION =
  "Read-only service account of the ACI fabric in the EMEA production data centers. The network automation team owns it and rotates the password on the first working day of each quarter; the rotation is recorded in the change calendar. The account reads tenants, bridge domains, endpoint groups and contracts, and it cannot write. Before you change this value, tell the on-call engineer, because the nightly schedule and two configurations read it. Ask in the network automation channel when unsure.";

const SAMPLE: Credential[] = [
  {
    name: "aci-fabric-emea-production-read-only-service-account-ams-26-q4",
    description: LONG_DESCRIPTION,
    fields: ["username", "password"],
    revision: 12,
    created_by: "alice",
    created_at: "2026-02-03T08:15:00Z",
    updated_by: "maximilian.von-hohenberg-automation",
    updated_at: "2026-11-02T07:02:00Z",
  },
  {
    name: "infrahub-main",
    description: "Infrahub production, token of the sync service account",
    fields: ["token"],
    revision: 4,
    created_by: "alice",
    created_at: "2026-06-11T13:40:00Z",
    updated_by: "alice",
    updated_at: "2026-11-19T16:21:00Z",
  },
  {
    name: "nautobot-staging",
    description: "",
    fields: ["token"],
    revision: 1,
    created_by: "bob",
    created_at: "2026-11-20T10:00:00Z",
    updated_by: "bob",
    updated_at: "2026-11-20T10:00:00Z",
  },
  {
    name: "netbox-lab",
    description: "NetBox lab, read-only token",
    fields: ["token"],
    revision: 3,
    created_by: "alice",
    created_at: "2026-09-01T09:30:00Z",
    updated_by: "bob",
    updated_at: "2026-11-21T09:00:00Z",
  },
  {
    name: "peeringmanager-api",
    description: "Peering Manager, token of the account sync-reader",
    fields: ["username", "token"],
    revision: 2,
    created_by: "Deleted account 3f9a1c2e",
    created_at: "2026-04-22T11:05:00Z",
    updated_by: "Deleted account 3f9a1c2e",
    updated_at: "2026-07-30T15:48:00Z",
  },
];

/** The configurations and schedules that name a credential, as a `credential-in-use` refusal lists them. */
const IN_USE: Record<string, { configurations: string[]; schedules: string[] }> = {
  "netbox-lab": {
    configurations: ["netbox-to-infrahub", "netbox-lab-devices-and-interfaces"],
    schedules: ["nightly-netbox"],
  },
  "infrahub-main": {
    configurations: ["netbox-to-infrahub", "netbox-lab-devices-and-interfaces", "aci-to-infrahub"],
    schedules: ["nightly-netbox", "aci-every-6-hours"],
  },
  "aci-fabric-emea-production-read-only-service-account-ams-26-q4": {
    configurations: ["aci-to-infrahub"],
    schedules: [],
  },
};

/** What the sample Sync API answers to the next write. The harness sets it. */
export type Refusal = "none" | "key" | "fields" | "forbidden" | "no-answer";
/** Which sample list the page reads. */
export type Dataset = "sample" | "empty" | "loading" | "error";

/** The account the sample writes are recorded for. */
export const SIGNED_IN = "alice";

const state: { items: Credential[]; refusal: Refusal } = { items: [...SAMPLE], refusal: "none" };

export function setRefusal(refusal: Refusal) {
  state.refusal = refusal;
}

export function resetSample() {
  state.items = [...SAMPLE];
}

const envelope = (
  status: number,
  code: string,
  message: string,
  details?: Record<string, unknown>
): ApiErrorBody => ({ error: { code, message, status, details } });

const wait = () => new Promise((resolve) => setTimeout(resolve, 450));

/** The answer the next write gets before the Sync API looks at the credential, if any. */
function refused(): unknown {
  switch (state.refusal) {
    case "key":
      return envelope(
        503,
        "credential-key-not-configured",
        "INFRAHUB_SYNC_ENCRYPTION_KEY is not set, so the Sync API cannot store a credential value; the operator of the Sync API sets it"
      );
    case "fields":
      return envelope(
        422,
        "credential-fields-invalid",
        "a credential holds a token, a username and a password, or a username and a token; each value holds 1 to 4096 characters and no control character"
      );
    case "forbidden":
      return envelope(403, "forbidden", "the caller is not allowed to do this");
    case "no-answer":
      return new TypeError("Failed to fetch");
    default:
      return undefined;
  }
}

const notFound = (name: string) =>
  envelope(404, "credential-not-found", `no stored credential has the name '${name}'`);

const byName = (a: Credential, b: Credential) => a.name.localeCompare(b.name);

/** `GET /credentials`, every page. */
export async function listCredentials(dataset: Dataset): Promise<Credential[]> {
  await wait();
  if (dataset === "loading") return new Promise(() => undefined);
  if (dataset === "error") throw new TypeError("Failed to fetch");
  return dataset === "empty" ? [] : [...state.items].sort(byName);
}

/** `POST /credentials`. Only the names of the fields are kept; the values are dropped here. */
export async function createCredential(
  name: string,
  description: string,
  fields: CredentialFields
): Promise<SecretResult<Credential>> {
  await wait();
  const error = refused();
  if (error) return { ok: false, error };
  if (state.items.some((item) => item.name === name)) {
    return {
      ok: false,
      error: envelope(
        409,
        "credential-exists",
        `a stored credential already has the name '${name}'; change its value instead`
      ),
    };
  }
  const now = new Date().toISOString();
  const created: Credential = {
    name,
    description,
    fields: Object.keys(fields).sort(),
    revision: 1,
    created_by: SIGNED_IN,
    created_at: now,
    updated_by: SIGNED_IN,
    updated_at: now,
  };
  state.items = [...state.items, created];
  return { ok: true, data: created };
}

function change(
  name: string,
  update: (current: Credential) => Partial<Credential>
): SecretResult<Credential> {
  const error = refused();
  if (error) return { ok: false, error };
  const current = state.items.find((item) => item.name === name);
  if (!current) return { ok: false, error: notFound(name) };
  const next = {
    ...current,
    ...update(current),
    updated_by: SIGNED_IN,
    updated_at: new Date().toISOString(),
  };
  state.items = state.items.map((item) => (item.name === name ? next : item));
  return { ok: true, data: next };
}

/** `PUT /credentials/{name}/value`: the whole new set of fields; the revision goes up by one. */
export async function setCredentialValue(
  name: string,
  fields: CredentialFields
): Promise<SecretResult<Credential>> {
  await wait();
  return change(name, (current) => ({
    fields: Object.keys(fields).sort(),
    revision: current.revision + 1,
  }));
}

/** `PATCH /credentials/{name}`: the revision stays. */
export async function describeCredential(
  name: string,
  description: string
): Promise<SecretResult<Credential>> {
  await wait();
  return change(name, () => ({ description }));
}

/** `DELETE /credentials/{name}`: refused with `credential-in-use` while the sample data names it. */
export async function deleteCredential(name: string): Promise<SecretResult<null>> {
  await wait();
  const error = refused();
  if (error) return { ok: false, error };
  if (!state.items.some((item) => item.name === name)) return { ok: false, error: notFound(name) };
  if (Object.hasOwn(IN_USE, name)) {
    return {
      ok: false,
      error: envelope(
        409,
        "credential-in-use",
        `the stored credential '${name}' is in use: the latest version of a configuration, or a schedule that is not deleted, names it; register a version that names another credential, or change or delete the schedule`,
        IN_USE[name]
      ),
    };
  }
  state.items = state.items.filter((item) => item.name !== name);
  return { ok: true, data: null };
}

/* ---------- the "Credentials used" section of the run details ---------- */

export type UsedSample = "plan-and-apply" | "revision-changed" | "none";

export const USED: Record<UsedSample, CredentialUsed[]> = {
  /** A plan and an apply that read the same revisions. */
  "plan-and-apply": [
    { stage: "plan", block: "source", name: "netbox-lab", revision: 3 },
    { stage: "plan", block: "destination", name: "infrahub-main", revision: 4 },
    { stage: "apply", block: "destination", name: "infrahub-main", revision: 4 },
  ],
  /** The value of a credential changed between the plan and the apply, and a verify ran too. */
  "revision-changed": [
    {
      stage: "plan",
      block: "source",
      name: "aci-fabric-emea-production-read-only-service-account-ams-26-q4",
      revision: 11,
    },
    { stage: "plan", block: "destination", name: "infrahub-main", revision: 3 },
    { stage: "verify", block: "destination", name: "infrahub-main", revision: 4 },
    { stage: "apply", block: "destination", name: "infrahub-main", revision: 4 },
  ],
  /** A run whose source and destination name no stored credential. */
  none: [],
};
