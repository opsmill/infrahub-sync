import type { SelectOption } from "./form-parts";

/** The fields of a configuration summary that name a configuration choice. */
export interface ConfigChoiceSource {
  config_id: string;
  name?: string | null;
  latest_version?: number | null;
}

/** A configuration and one of its registered versions, as the schedule form sends them. */
export interface ConfigVersion {
  configId: string;
  version: number;
}

/** The key of one configuration version in the choice; `parseConfigVersionKey` reads it back. */
export const configVersionKey = (configId: string, version: number) => `${configId}@${version}`;

export function parseConfigVersionKey(key: string): ConfigVersion | undefined {
  const at = key.lastIndexOf("@");
  const version = Number(key.slice(at + 1));
  if (at <= 0 || !Number.isInteger(version) || version < 1) return undefined;
  return { configId: key.slice(0, at), version };
}

/**
 * The choices of the schedule form, one per registered version: "<name>, version <n>", the latest
 * marked, newest first within each configuration. The Sync API numbers versions 1, 2, 3 and never
 * removes one, so the latest version number gives every version without reading each list. The
 * name is the configuration's current name. The identifier is a second line only for a name that
 * two configurations share; a configuration without a name is known only by its identifier.
 */
export function configVersionChoices(configs: ConfigChoiceSource[]): SelectOption<string>[] {
  const counts = new Map<string, number>();
  for (const c of configs) {
    if (c.name) counts.set(c.name, (counts.get(c.name) ?? 0) + 1);
  }
  return configs.flatMap((c) => {
    const name = c.name || c.config_id;
    const detail = c.name && (counts.get(c.name) ?? 0) > 1 ? c.config_id : undefined;
    const latest = c.latest_version ?? 0;
    return Array.from({ length: latest }, (_, i) => latest - i).map((n) => ({
      id: configVersionKey(c.config_id, n),
      label: n === latest ? `${name}, version ${n}, the latest` : `${name}, version ${n}`,
      detail,
    }));
  });
}
