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

/**
 * What to do when Register is disabled because the destination schema could not be read for a
 * reason the package does not cause; `undefined` when the package is the thing to fix.
 */
export function readFailureHint(destinationRead: string): string | undefined {
  switch (destinationRead) {
    case "nobranch":
      return "The validation branch does not exist in Infrahub. Change the validation branch to validate again.";
    case "credentials":
      return "The Sync API cannot read a token from the stored credential the destination names. Check on the Credentials page that it exists and holds a token, then validate again.";
    case "rejected":
      return "Infrahub refused the destination token. Check the value of the stored credential the destination names, then validate again.";
    default:
      return undefined;
  }
}

/** The warning findings a check returned, which the review step lists without blocking. */
export const warningFindings = (check: PackageCheck) =>
  check.findings.filter((f) => f.severity === "warning");

/** One block of a package and the stored credential it names. */
export interface BlockCredential {
  block: "Source" | "Destination";
  name: string;
}

/**
 * The stored credential that each block of the configuration names, source first, without a
 * value: a package names a stored credential with `credential: <name>` and never holds a value.
 */
export function blockCredentials(content: DeclaredContent): BlockCredential[] {
  const { source, destination } = content.configuration;
  const named: BlockCredential[] = [];
  if (source.credential) named.push({ block: "Source", name: source.credential });
  if (destination.credential) named.push({ block: "Destination", name: destination.credential });
  return named;
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

/** The refusal of a package whose name another configuration has: names are unique. */
export const sameNameText = (name: string | undefined) =>
  name
    ? `A configuration named ${name} already exists.`
    : "Another configuration already has the name of this package.";

/** Why Register stays disabled while another configuration has the name of the package. */
export const sameNameHint = (name: string | undefined) =>
  name
    ? `Add the package as a new version of ${name} instead.`
    : "Add the package as a new version of the configuration that has its name instead.";

/** The Sync API error code of a registration refused because another configuration has the name. */
export const CONFIGURATION_NAME_IN_USE = "configuration-name-in-use";

/**
 * The configuration that already has the name of the package. `configId` is used only as the
 * target of "add a new version"; the review step never shows it.
 */
export interface NameInUse {
  configId?: string;
  name?: string;
}

/**
 * The configuration named by a `configuration-name-in-use` refusal, from `details`; nothing for
 * any other error, including the other 409 refusals (`idempotency-conflict`).
 */
export function nameInUseOf(error: unknown): NameInUse | undefined {
  if (!isEnvelope(error) || error.error.code !== CONFIGURATION_NAME_IN_USE) return undefined;
  const details = error.error.details;
  const configId = details?.config_id;
  const name = details?.name;
  return {
    ...(typeof configId === "string" && configId ? { configId } : {}),
    ...(typeof name === "string" && name ? { name } : {}),
  };
}

/**
 * The configuration, other than `ownConfigId`, that has the name of the package, among the
 * configurations listed for that name. For a new configuration `ownConfigId` is undefined, so
 * any listed configuration has the name.
 */
export function otherNamesake(
  items: readonly { config_id: string; name?: string | null }[] | undefined,
  ownConfigId: string | undefined
): NameInUse | undefined {
  const other = items?.find((item) => item.config_id !== ownConfigId);
  if (!other) return undefined;
  return { configId: other.config_id, ...(other.name ? { name: other.name } : {}) };
}

/**
 * The configuration whose name blocks Register. A `configuration-name-in-use` refusal always
 * blocks it. The configurations listed for the name block it only before the request is sent:
 * once it is sent, a configuration listed with that name can be the one the request itself
 * registered before its answer was lost, and Retry with the same Idempotency-Key returns it.
 */
export function blockingNamesake(options: {
  refused: NameInUse | undefined;
  namesakes: readonly { config_id: string; name?: string | null }[] | undefined;
  ownConfigId: string | undefined;
  checksName: boolean;
  sent: boolean;
}): NameInUse | undefined {
  if (options.refused) return options.refused;
  if (!options.checksName || options.sent) return undefined;
  return otherNamesake(options.namesakes, options.ownConfigId);
}

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
