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
export type RunLogLine = Schemas["RunLogLine"];

/** A source or destination as a registered package declares it. */
export interface DeclaredAdapter {
  name: string;
  settings?: Record<string, unknown> | null;
}

/** One `schema_mapping` entry; the Sync API sends `null` for keys the package leaves out. */
export interface DeclaredMapping {
  name: string;
  mapping: string;
  identifiers?: string[] | null;
  fields?:
    | { name: string; mapping?: string | null; reference?: string | null; static?: unknown }[]
    | null;
  filters?: { field: string; operation: string; value?: unknown }[] | null;
  transforms?: { field: string; expression: string }[] | null;
}

export interface DeclaredConfiguration {
  name: string;
  source: DeclaredAdapter;
  destination: DeclaredAdapter;
  schema_mapping?: DeclaredMapping[];
  order?: string[] | null;
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
  error: {
    code: string;
    message: string;
    status: number;
    run_id?: string | null;
    reason?: string | null;
  };
}
