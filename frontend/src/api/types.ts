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
export type PackageCheck = Schemas["PackageCheckResource"];
export type ValidationFinding = Schemas["ValidationFindingResource"];
/**
 * The body of `POST /configs` and `POST /configs/{config_id}/versions`. The generated type marks
 * `registered_without_validation` as required because it has a default; the Sync API accepts the
 * body without it.
 */
export type ConfigMutationRequest = Schemas["ConfigMutationRequest"];
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
  /** The name of the account that registered the version, as the Sync API shows it. */
  registered_by?: string | null;
  declared_content: {
    format_version: number;
    configuration: DeclaredConfiguration;
    credentials?: Record<string, { provider: string; identifier: string }>;
  };
}

/** A package as the store keeps it: `PackageCheck.declared_content`, typed as an open object. */
export type DeclaredContent = ConfigurationVersion["declared_content"];

/** `POST /configs/{config_id}/versions`; the OpenAPI document types its response as `unknown`. */
export interface RegisteredVersion {
  version: {
    config_id: string;
    registry_version: number;
    package_checksum: string;
    created_at: string;
    /** True when the version was registered while the destination could not be reached. */
    registered_without_validation: boolean;
  };
  /** False when the package is the same as an existing version, which is returned instead. */
  created: boolean;
}

/** `POST /configs`; the OpenAPI document types its response as `unknown`. */
export interface RegisteredConfiguration {
  configuration: ConfigurationSummary;
  version: RegisteredVersion["version"];
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
