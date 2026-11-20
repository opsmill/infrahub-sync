import type { SelectOption } from "./form-parts";

/** The fields of a configuration summary that name a configuration choice. */
export interface ConfigChoiceSource {
  config_id: string;
  name?: string | null;
  latest_version?: number | null;
}

/**
 * The configuration choices of the schedule form: "<name>, version <n>" from the latest registered
 * version. The identifier is a second line only for a name that two configurations share, so the
 * person can tell them apart; a configuration without a name is known only by its identifier.
 */
export function configChoices(configs: ConfigChoiceSource[]): SelectOption<string>[] {
  const counts = new Map<string, number>();
  for (const c of configs) {
    if (c.name) counts.set(c.name, (counts.get(c.name) ?? 0) + 1);
  }
  return configs.map((c) => {
    const name = c.name || c.config_id;
    const label = c.latest_version != null ? `${name}, version ${c.latest_version}` : name;
    const shared = c.name ? (counts.get(c.name) ?? 0) > 1 : false;
    return { id: c.config_id, label, detail: shared ? c.config_id : undefined };
  });
}
