import type { components } from "./types.generated";

type Schemas = components["schemas"];

export type Run = Schemas["PublicRunResource"];
export type RunResource = Schemas["RunResource"];
export type Orchestration = Schemas["OrchestrationSummary"];
export type ConfigurationSummary = Schemas["ConfigurationSummaryResource"];
export type VersionSummary = Schemas["ConfigurationVersionSummaryResource"];
export type RunReference = Schemas["RunReference"];
export type Plan = Schemas["PlanResource"];
export type PlanOperation = Schemas["PlanOperationResource"];
export type PlanLastChange = Schemas["PlanLastChangeResource"];
export type Artifact = Schemas["ArtifactReferenceResource"];
export type ServiceStatus = Schemas["ServiceStatusResource"];
export type Principal = Schemas["PrincipalResource"];

/** A source or destination as a registered package declares it. */
export interface DeclaredAdapter {
  name: string;
  settings?: Record<string, unknown> | null;
}

export interface DeclaredMapping {
  name: string;
  mapping: string;
  identifiers?: string[];
  fields?: { name: string; mapping?: string; reference?: string; static?: unknown }[];
  filters?: unknown[];
  transforms?: unknown[];
}

export interface DeclaredConfiguration {
  name: string;
  source: DeclaredAdapter;
  destination: DeclaredAdapter;
  schema_mapping?: DeclaredMapping[];
  order?: string[];
}

/** `GET /configs/{config_id}/versions/{registry_version}`; the OpenAPI document types it as `any`. */
export interface ConfigurationVersion {
  config_id: string;
  registry_version: number;
  package_checksum: string;
  created_at: string;
  declared_content: {
    format_version: number;
    configuration: DeclaredConfiguration;
    credentials?: Record<string, { provider: string; identifier: string }>;
  };
}

/** The error envelope every Sync API error uses. */
export interface ApiErrorBody {
  error: { code: string; message: string; status: number; run_id?: string | null };
}
