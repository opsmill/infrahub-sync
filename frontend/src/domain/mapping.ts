import type { DeclaredMapping } from "#/api/types";

type Field = NonNullable<DeclaredMapping["fields"]>[number];
type Filter = NonNullable<DeclaredMapping["filters"]>[number];

const OPERATION_WORDS: Record<string, string> = {
  "==": "equals",
  "!=": "does not equal",
  in: "is one of",
  "not in": "is not one of",
  contains: "contains",
  "not contains": "does not contain",
  is_empty: "is empty",
  is_not_empty: "is not empty",
  regex: "matches the pattern",
};

/** A filter in words, as the package declares it: `rack is empty`, `type.value equals "lag"`. */
export const filterWords = (f: Filter) =>
  `${f.field} ${OPERATION_WORDS[f.operation] ?? f.operation}${f.value != null ? ` ${JSON.stringify(f.value)}` : ""}`;

/** Where a field's value comes from: a source field, a fixed value, or nothing declared. */
export const fieldSource = (f: Field) =>
  f.mapping ?? (f.static != null ? `Fixed value ${JSON.stringify(f.static)}` : "Not declared");

/**
 * The Sync API has no comparison of two versions, so two declared mappings are compared here. This
 * module is the only place that does it: a comparison returned by the service would replace
 * `diffMappings` and leave the pages as they are.
 */
export interface MappingChange {
  status: "added" | "changed" | "removed";
  /** The mapping in the version compared with. */
  was?: DeclaredMapping;
  fieldsAdded: string[];
  fieldsRemoved: string[];
  /** Fields in both versions whose source field, fixed value or peer kind differs. */
  fieldsChanged: string[];
  /** The source model, the identifiers, the filters or the computed fields differ. */
  rulesChanged: boolean;
}

export interface MappingDiff {
  /** The version compared with. */
  against: number;
  /** The changes of the selected version's mappings, by `MappingEntry.key`. */
  byKey: Map<string, MappingChange>;
  /** Keys of the mappings of the version compared with that the selected version does not have. */
  removed: string[];
  counts: { added: number; changed: number; removed: number };
}

export interface MappingEntry {
  /** The kind, with `~2` for its second mapping. It names the mapping in the page's address. */
  key: string;
  mapping: DeclaredMapping;
  /** The filters that tell this mapping from the mappings that share its source model or its kind. */
  conditions: string[];
  change?: MappingChange;
}

const UNCHANGED_FIELDS = {
  fieldsAdded: [],
  fieldsRemoved: [],
  fieldsChanged: [],
  rulesChanged: false,
};

/** A mapping is recognised across versions by its kind and its position among the mappings of that kind. */
function keyed(mappings: DeclaredMapping[]): Map<string, DeclaredMapping> {
  const seen = new Map<string, number>();
  const out = new Map<string, DeclaredMapping>();
  for (const m of mappings) {
    const n = (seen.get(m.name) ?? 0) + 1;
    seen.set(m.name, n);
    out.set(n === 1 ? m.name : `${m.name}~${n}`, m);
  }
  return out;
}

const same = (a: unknown, b: unknown) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);

/** What `next` changed from `base`, the mappings of version `against`. */
export function diffMappings(
  against: number,
  base: DeclaredMapping[],
  next: DeclaredMapping[]
): MappingDiff {
  const before = keyed(base);
  const after = keyed(next);
  const byKey = new Map<string, MappingChange>();
  for (const [key, m] of after) {
    const was = before.get(key);
    if (!was) {
      byKey.set(key, { status: "added", ...UNCHANGED_FIELDS });
    } else if (!same(was, m)) {
      const old = new Map((was.fields ?? []).map((f) => [f.name, f]));
      const now = new Map((m.fields ?? []).map((f) => [f.name, f]));
      byKey.set(key, {
        status: "changed",
        was,
        fieldsAdded: [...now.keys()].filter((n) => !old.has(n)),
        fieldsRemoved: [...old.keys()].filter((n) => !now.has(n)),
        fieldsChanged: [...now.keys()].filter((n) => old.has(n) && !same(old.get(n), now.get(n))),
        rulesChanged:
          was.mapping !== m.mapping ||
          !same(was.identifiers, m.identifiers) ||
          !same(was.filters, m.filters) ||
          !same(was.transforms, m.transforms),
      });
    }
  }
  const removed = [...before.keys()].filter((k) => !after.has(k));
  const count = (status: MappingChange["status"]) =>
    [...byKey.values()].filter((c) => c.status === status).length;
  return {
    against,
    byKey,
    removed,
    counts: { added: count("added"), changed: count("changed"), removed: removed.length },
  };
}

/** The mappings of one version, each with the filters that set it apart and its change, if compared. */
export function mappingEntries(mappings: DeclaredMapping[], diff?: MappingDiff): MappingEntry[] {
  return [...keyed(mappings)].map(([key, mapping]) => {
    const siblings = mappings.filter(
      (o) => o !== mapping && (o.name === mapping.name || o.mapping === mapping.mapping)
    );
    return {
      key,
      mapping,
      conditions: (mapping.filters ?? [])
        .filter((f) =>
          siblings.some((s) => !(s.filters ?? []).some((g) => filterWords(g) === filterWords(f)))
        )
        .map(filterWords),
      change: diff?.byKey.get(key),
    };
  });
}

/** The mappings of the version compared with that the selected version no longer has. */
export function removedEntries(
  base: DeclaredMapping[] | undefined,
  diff?: MappingDiff
): MappingEntry[] {
  if (!base || !diff) return [];
  return mappingEntries(base)
    .filter((e) => diff.removed.includes(e.key))
    .map((e) => ({
      ...e,
      key: `removed:${e.key}`,
      change: { status: "removed", ...UNCHANGED_FIELDS },
    }));
}

/** The mappings of one source model together, at the place of the first one. */
export function groupBySource(entries: MappingEntry[]): MappingEntry[][] {
  const groups = new Map<string, MappingEntry[]>();
  for (const entry of entries) {
    groups.set(entry.mapping.mapping, [...(groups.get(entry.mapping.mapping) ?? []), entry]);
  }
  return [...groups.values()];
}
