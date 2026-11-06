import type { PlanOperation } from "#/api/types";
import { fmt } from "#/domain/run-state";

export type Action = PlanOperation["action"];
export const ACTIONS: Action[] = ["create", "update", "delete"];

/** Words for each action: the row's screen-reader word, the filter label and the count in a kind header. */
export const ACTION_WORDS: Record<
  Action,
  { word: string; filter: string; count: (n: number, done: boolean) => string }
> = {
  create: {
    word: "Create",
    filter: "Create",
    count: (n, done) => `${fmt(n)} ${done ? "created" : "to create"}`,
  },
  update: {
    word: "Update",
    filter: "Update",
    count: (n, done) => `${fmt(n)} ${done ? "updated" : "to update"}`,
  },
  delete: {
    word: "Not deleted",
    filter: "Delete, not applied",
    count: (n) => `${fmt(n)} ${n === 1 ? "delete" : "deletes"} listed, not applied`,
  },
};

export type Json = Record<string, unknown>;
const isObject = (v: unknown): v is Json =>
  typeof v === "object" && v !== null && !Array.isArray(v);

/** A peer that scopes a node: `field` is the identity key, `kind` the peer's kind. */
export interface ScopeEntry {
  field: string;
  kind?: string;
  label: string;
}

/**
 * A node's own identifiers and the peers that scope it. `levels` runs outermost first;
 * peers on one level are siblings (a location and a tenant), each level nests the next.
 */
export function identityLevels(identity: Json): {
  name: string;
  own: string[];
  levels: ScopeEntry[][];
} {
  const own: string[] = [];
  const direct: ScopeEntry[] = [];
  const parents: ScopeEntry[][][] = [];
  for (const [field, value] of Object.entries(identity)) {
    if (field === "peer_kind") continue;
    if (!isObject(value)) {
      own.push(displayValue(value));
      continue;
    }
    const peer = identityLevels(isObject(value.identity) ? value.identity : value);
    direct.push({
      field,
      kind: typeof value.peer_kind === "string" ? value.peer_kind : undefined,
      label: peer.name,
    });
    parents.push(peer.levels);
  }
  // Sibling peers can nest to different depths; align their parents from the inside out.
  const depth = Math.max(0, ...parents.map((l) => l.length));
  const levels: ScopeEntry[][] = [];
  for (let d = depth; d >= 1; d--) levels.push(parents.flatMap((l) => l[l.length - d] ?? []));
  if (direct.length > 0) levels.push(direct);
  const name =
    own.join(" · ") ||
    levels
      .pop()
      ?.map((e) => e.label)
      .join(", ") ||
    "—";
  return { name, own, levels };
}

export function identityParts(identity: Json): {
  name: string;
  own: string[];
  scope: ScopeEntry[];
} {
  const { name, own, levels } = identityLevels(identity);
  return { name, own, scope: levels.flat() };
}

export const levelLabel = (level: ScopeEntry[]) => level.map((e) => e.label).join(", ");

/** A node in plain text, scope first: "dm-syracuse › 11:DM-Syracuse:Comms closet › dmi01-syracuse-rtr01". */
export function nodeLabel(identity: Json): string {
  const { name, levels } = identityLevels(identity);
  return [...levels.map(levelLabel), name].join(" › ");
}

/** A planned value in words: attribute values as written, peers by their identifiers. */
export function displayValue(value: unknown): string {
  if (value === null || value === undefined) return "no value";
  if (typeof value === "string") return value === "" ? "empty text" : value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) return value.length ? value.map(displayValue).join(", ") : "empty list";
  if (isObject(value)) {
    if ("value" in value) return displayValue(value.value);
    return nodeLabel(isObject(value.identity) ? value.identity : value);
  }
  return String(value);
}

export const isEmptyValue = (value: unknown) =>
  value === null ||
  value === undefined ||
  value === "" ||
  (Array.isArray(value) && value.length === 0);

export interface Field {
  name: string;
  value: unknown;
  rel?: { peerKind?: string; many: boolean; peers: Json[] };
}

export function fieldsOf(op: PlanOperation): Field[] {
  const fields: Field[] = Object.entries(op.payload ?? {}).map(([name, value]) => ({
    name,
    value,
  }));
  for (const rel of op.relationships ?? []) {
    if (typeof rel.field !== "string") continue;
    const peers = Array.isArray(rel.peers) ? rel.peers.filter(isObject) : [];
    const many = rel.cardinality === "many";
    fields.push({
      name: rel.field,
      value: many ? peers : (peers[0] ?? null),
      rel: {
        peerKind: typeof rel.peer_kind === "string" ? rel.peer_kind : undefined,
        many,
        peers,
      },
    });
  }
  return fields;
}

/** A field's value as one line of text, as the row summary and the search read it. */
export const fieldText = (f: Field) =>
  f.rel
    ? f.rel.peers.length
      ? f.rel.peers.map(nodeLabel).join(", ")
      : "no peers"
    : displayValue(f.value);
