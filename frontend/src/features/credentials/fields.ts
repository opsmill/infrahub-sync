import type { Credential } from "#/api/types";

/** The three field sets of a credential value, in the order the form offers them. */
export const FIELD_SETS = [
  { id: "token", label: "Token", fields: ["token"] },
  { id: "username-password", label: "Username and password", fields: ["username", "password"] },
  { id: "username-token", label: "Username and token", fields: ["username", "token"] },
] as const;

export type FieldSetId = (typeof FIELD_SETS)[number]["id"];

export const fieldSet = (id: FieldSetId) =>
  FIELD_SETS.find((set) => set.id === id) ?? FIELD_SETS[0];

/** The field set a stored credential holds, from the names of its fields. */
export function fieldSetOf(fields: readonly string[]): FieldSetId {
  if (fields.includes("password")) return "username-password";
  return fields.includes("username") ? "username-token" : "token";
}

/** "Token", "Username and password" or "Username and token". */
export const fieldsLabel = (fields: readonly string[]) => fieldSet(fieldSetOf(fields)).label;

export type CredentialOrder = "name" | "changed";

/**
 * The credentials that match a search over the name, the description and the account that last
 * changed the value, and a field set, in the chosen order. The Sync API sorts by name. `keep` is a
 * credential that stays in the result whatever the search, because a form is open in its row.
 */
export function findCredentials(
  credentials: Credential[],
  search: { query: string; set: "all" | FieldSetId; order: CredentialOrder; keep?: string }
): Credential[] {
  const q = search.query.trim().toLowerCase();
  const found = credentials.filter(
    (credential) =>
      credential.name === search.keep ||
      ((search.set === "all" || fieldSetOf(credential.fields) === search.set) &&
        (!q ||
          credential.name.includes(q) ||
          credential.description.toLowerCase().includes(q) ||
          credential.updated_by.toLowerCase().includes(q)))
  );
  return search.order === "name"
    ? found
    : [...found].sort((a, b) => Date.parse(b.updated_at) - Date.parse(a.updated_at));
}

/** The number of credentials in each field set, and in all of them. */
export function countBySet(credentials: Credential[]): Record<"all" | FieldSetId, number> {
  const counts = { all: credentials.length, token: 0, "username-password": 0, "username-token": 0 };
  for (const credential of credentials) counts[fieldSetOf(credential.fields)] += 1;
  return counts;
}
