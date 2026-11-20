import { isEnvelope, isTimeout, SyncApiUnavailable } from "#/api/errors";
import type { DeclaredContent, PackageCheck } from "#/api/types";
import { plural } from "#/domain/run-state";

/** The largest findings page the Sync API serves; the review step asks for all of it. */
export const REVIEW_FINDINGS_LIMIT = 256;

/** The longest reason the Sync API keeps. */
export const REASON_MAX_LENGTH = 512;

/** The finding that comes with every failed read of the destination schema. */
const READ_FAILED_CODE = "destination-schema-read-failed";

/** `destination_read` values that mean the destination did not answer at all. */
const UNREACHABLE_READS = new Set(["timeout", "unreachable"]);

export const MESSAGES = {
  moreThanOneFile: "Drop one package file.",
  unreadableFile: "The file could not be read. Choose it again.",
  invalidPackage: "This file is not a YAML or JSON configuration package.",
  v2Configuration:
    "This is a v2 configuration. Convert it with `infrahub-sync configs convert`, then drop the package here. Converting a v2 configuration in the web interface is planned for a later release.",
  tooLarge: "The file is larger than the Sync API accepts.",
  administratorRequired: "Registering needs an administrator token",
  checkFailed: "Validation did not complete.",
  unreachable: "Could not validate: the destination cannot be reached",
  requestFailed: "The request did not complete. Retry.",
} as const;

/** The package as the store would keep it. The OpenAPI document types it as an open object. */
export const declaredContent = (check: PackageCheck): DeclaredContent =>
  check.declared_content as unknown as DeclaredContent;

/** The branch the review step validates against first: the one the package declares, else `main`. */
export function defaultBranch(content: DeclaredContent): string {
  const branch = content.configuration.destination.settings?.branch;
  return typeof branch === "string" && branch.trim() ? branch.trim() : "main";
}

/**
 * What a check allows. Only an `error` finding blocks registration; a `warning` finding is listed
 * and does not block (FR-011).
 * - `valid`: the destination was read and there is no error finding; Register is enabled.
 * - `unreachable`: the destination did not answer and no other error finding was reported;
 *   Register without validation is enabled (FR-020).
 * - `findings`: at least one error finding, or more findings than the page holds; Register is
 *   disabled (FR-011).
 * - `unchecked`: the check ran without a branch, so the destination was not read.
 */
export type ResultKind = "valid" | "findings" | "unreachable" | "unchecked";

/** True when the page does not hold every finding, so an error finding may be on a later page. */
const pageIncomplete = (check: PackageCheck) => check.total_findings > check.findings.length;

export function resultKind(check: PackageCheck): ResultKind {
  if (UNREACHABLE_READS.has(check.destination_read)) {
    const onlyReadFailed =
      !pageIncomplete(check) &&
      check.findings.every((f) => f.code === READ_FAILED_CODE || f.severity !== "error");
    return onlyReadFailed ? "unreachable" : "findings";
  }
  if (pageIncomplete(check) || check.findings.some((f) => f.severity === "error")) {
    return "findings";
  }
  return check.destination_read === "read" ? "valid" : "unchecked";
}

/** The warning findings a check returned, which the review step lists without blocking. */
export const warningFindings = (check: PackageCheck) =>
  check.findings.filter((f) => f.severity === "warning");

/** Every `$credential` reference name in declared settings, depth first. */
function* referenceNames(value: unknown): Generator<string> {
  if (Array.isArray(value)) {
    for (const child of value) yield* referenceNames(child);
    return;
  }
  if (value === null || typeof value !== "object") return;
  const mapping = value as Record<string, unknown>;
  const keys = Object.keys(mapping);
  const reference = mapping.$credential;
  if (keys.length === 1 && keys[0] === "$credential" && typeof reference === "string") {
    yield reference;
    return;
  }
  for (const child of Object.values(mapping)) yield* referenceNames(child);
}

/**
 * The variable names the configuration's credential references point to, without their values.
 * Like the worker check of the Sync API (`referenced_credential_variables`), only a declared
 * reference with the `env` provider that a setting uses names a variable.
 */
export function credentialVariables(content: DeclaredContent): string[] {
  const declared = content.credentials ?? {};
  const names = new Set<string>();
  for (const name of referenceNames(content.configuration)) {
    const reference = declared[name];
    if (reference?.provider === "env") names.add(reference.identifier);
  }
  return [...names].sort();
}

/** The rename a new version makes, or nothing when the name stays the same. */
export function renameText(current: string | null | undefined, next: string | null | undefined) {
  if (!current || !next || current === next) return undefined;
  return `This version renames the configuration from ${current} to ${next}.`;
}

/** The sentence under a findings page that does not hold every finding. */
export function moreFindingsText(check: PackageCheck): string | undefined {
  const more = check.total_findings - check.findings.length;
  if (more <= 0) return undefined;
  return `${plural(more, "more finding", "more findings")}. Run \`infrahub-sync configs check\` to list them all.`;
}

export const reviewTitle = (name: string, nextVersion: number) =>
  `Add version ${nextVersion} to ${name}`;

export const NEW_CONFIGURATION_TITLE = "Register a new configuration";

/** The warning for a new configuration whose name is in use. Names are not unique (FR-013). */
export const sameNameText = (name: string) => `A configuration named ${name} already exists.`;

export const validatingText = (branch: string) => `Validating against branch ${branch}…`;

export const validText = (branch: string) => `Valid against branch ${branch}`;

export const unchangedText = (version: number) =>
  `This package is the same as version ${version}. No new version was registered.`;

/** Before Register: the package has the checksum of the newest version, so Register adds nothing. */
export const sameAsNewestText = (version: number) =>
  `This package is the same as version ${version}. Registering it would add no version.`;

/** Names the account that registered the version when the Sync API returns it (AGENTS.md, "Sync frontend"). */
export const registeredMeanwhileText = (version: number, registeredBy?: string | null) =>
  registeredBy
    ? `Version ${version} was registered meanwhile by ${registeredBy}.`
    : `Version ${version} was registered meanwhile.`;

/** True for a request the review step aborted itself, such as a check replaced by a newer one. */
export const isAbort = (error: unknown): boolean =>
  error instanceof DOMException && error.name === "AbortError";

/**
 * The message for a check the Sync API refused, keyed by reason, or by HTTP status for an answer
 * without the Sync API's envelope. Nothing when the check did not complete: no answer, a server
 * error or a time limit, which the review step reports as "Validation did not complete."
 */
export function refusalMessage(error: unknown): string | undefined {
  if (error instanceof SyncApiUnavailable) {
    return error.status === 413 ? MESSAGES.tooLarge : undefined;
  }
  if (!isEnvelope(error)) return undefined;
  const { status, reason, message } = error.error;
  if (status >= 500 || status === 408 || status === 429) return undefined;
  if (reason === "invalid_package") return MESSAGES.invalidPackage;
  if (reason === "v2_configuration") return MESSAGES.v2Configuration;
  if (status === 413) return MESSAGES.tooLarge;
  if (isAdministratorRefusal(error)) return `${MESSAGES.administratorRequired}.`;
  return message.charAt(0).toUpperCase() + message.slice(1);
}

/** True when the Sync API refused the request because the token is not an administrator's. */
export const isAdministratorRefusal = (error: unknown): boolean =>
  isEnvelope(error) && error.error.status === 403;

/** True when a write got no answer, so the same request with the same key can be sent again. */
export function isNoAnswer(error: unknown): boolean {
  if (isTimeout(error) || error instanceof TypeError) return true;
  if (error instanceof SyncApiUnavailable) return error.status >= 500 || error.status === 408;
  return isEnvelope(error) && error.error.status >= 500;
}
