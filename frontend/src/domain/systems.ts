import type { DeclaredAdapter, DeclaredMapping } from "#/api/types";

/** Product names for the adapters in `infrahub_sync/adapters/`. */
const PRODUCT_NAMES: Record<string, string> = {
  aci: "Cisco ACI",
  genericrestapi: "REST API",
  infrahub: "Infrahub",
  ipfabricsync: "IP Fabric",
  nautobot: "Nautobot",
  netbox: "NetBox",
  peeringmanager: "Peering Manager",
  prometheus: "Prometheus",
  slurpitsync: "Slurp’it",
};

export interface System {
  /** The product name, or the adapter name as declared when it is not a known adapter. */
  label: string;
  /** Host and port of `settings.url` or `settings.base_url`; a value that is not a URL is kept as declared. */
  host?: string;
  /** `settings.branch`, declared on Infrahub adapters. */
  branch?: string;
}

function setting(adapter: DeclaredAdapter, key: string): string | undefined {
  const value = adapter.settings?.[key];
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function hostOf(url: string | undefined): string | undefined {
  if (!url) return undefined;
  try {
    return new URL(url).host || url;
  } catch {
    return url;
  }
}

export function describeSystem(adapter: DeclaredAdapter): System {
  const host = hostOf(setting(adapter, "url") ?? setting(adapter, "base_url"));
  const name = adapter.name.toLowerCase();
  // A generic REST source reads one system; its host names that system better than "REST API".
  const label =
    name === "genericrestapi" && host?.includes(".")
      ? host.replace(/^www\./, "")
      : (PRODUCT_NAMES[name] ?? adapter.name);
  return { label, host, branch: setting(adapter, "branch") };
}

/** The destination kinds a mapping writes, once each, in declaration order. */
export function mappedKinds(mapping: DeclaredMapping[] | undefined): string[] {
  return [...new Set((mapping ?? []).map((m) => m.name))];
}
