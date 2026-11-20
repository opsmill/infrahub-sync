import { Button } from "@infrahub/ui";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { cn } from "tailwind-variants";

import {
  useCheckPackage,
  useCreateConfigVersion,
  useIdempotencyKey,
  useRegisterConfig,
} from "#/api/actions";
import {
  configQuery,
  configsNamedQuery,
  configVersionQuery,
  meQuery,
  newestVersionQuery,
} from "#/api/queries";
import type {
  ConfigMutationRequest,
  PackageCheck,
  RegisteredConfiguration,
  RegisteredVersion,
  ValidationFinding,
  VersionSummary,
} from "#/api/types";
import { CheckIcon } from "#/components/icons";
import { CODE_CHIP, InlineCode, Notice } from "#/components/page";
import { diffMappings, mappingEntries, removedEntries } from "#/domain/mapping";
import { plural } from "#/domain/run-state";
import { describeSystem } from "#/domain/systems";
import { DialogFrame, Field, INPUT, MutationError, ReasonField } from "#/features/runs/form";

import { MappingList } from "./detail-mapping";
import { MappingDetail } from "./detail-mapping-panel";
import { PackageDropArea } from "./PackageDropArea";
import {
  credentialVariables,
  declaredContent,
  defaultBranch,
  isAbort,
  isAdministratorRefusal,
  isNoAnswer,
  MESSAGES,
  moreFindingsText,
  type NameInUse,
  NEW_CONFIGURATION_TITLE,
  nameInUseOf,
  otherNamesake,
  REASON_MAX_LENGTH,
  REVIEW_FINDINGS_LIMIT,
  readFailureHint,
  refusalMessage,
  registeredMeanwhileText,
  renameText,
  resultKind,
  reviewTitle,
  sameAsNewestText,
  sameNameHint,
  sameNameText,
  unchangedText,
  validatingText,
  validText,
  warningFindings,
} from "./package-review";

/**
 * Where the reviewed package goes.
 * - `version`: the next version of a registered configuration, compared with its newest version.
 * - `configuration`: a new configuration with version 1, with nothing to compare.
 */
export type ReviewTarget = { kind: "version"; configId: string } | { kind: "configuration" };

/** A branch typed in the field is checked once typing stops for this long. */
const BRANCH_CHECK_DELAY_MS = 600;

/**
 * The request body. `document` is the text of the dropped file that the review step checked, so
 * the Sync API stores what it checked: a package parsed and serialized again by the browser can
 * have different numbers (`30.0` becomes `30`) and so a different checksum (spec D-12).
 * `registered_without_validation` is sent only as `true`, when Register runs while the
 * destination cannot be reached (FR-021); otherwise it is left out. It is part of the locked
 * body, so a retry sends the same request with the same idempotency key.
 */
type Body = {
  document: string;
  reason: string | null;
  registered_without_validation?: true;
};
type Outcome =
  | { kind: "unchanged"; version: number }
  /** `registeredBy` is the account name the Sync API returns for the version, when it has one. */
  | { kind: "meanwhile"; version: number; registeredBy?: string | null }
  | { kind: "read-failed"; error: unknown };

/**
 * The review step for one dropped package file: what it declares, how its mapping differs from
 * the newest version (for a new version), whether another configuration has its name, and the
 * result of validating it against a destination branch. Register is enabled only for a valid
 * package whose name no other configuration has, or, as "Register without validation", when the
 * destination cannot be reached.
 */
export function PackageReview({
  target,
  document,
  onClose,
  onRefused,
  onAddAsVersion,
}: {
  target: ReviewTarget;
  /** The text of the dropped file; the Sync API parses it. */
  document: string;
  onClose: () => void;
  /** A first check the Sync API refused: the file is not a package it accepts. */
  onRefused: (message: string) => void;
  /** Review the same file as a new version of the configuration that has its name. */
  onAddAsVersion: (configId: string) => void;
}) {
  const configId = target.kind === "version" ? target.configId : undefined;
  const isNew = configId === undefined;
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  // One key for the whole review: every attempt sends the same request with it (FR-017).
  const [key] = useIdempotencyKey();
  const check = useCheckPackage();
  const createVersion = useCreateConfigVersion();
  const registerConfig = useRegisterConfig();
  const create = isNew ? registerConfig : createVersion;
  const config = useQuery({ ...configQuery(configId ?? ""), enabled: !isNew });

  /** The branch of the latest check; `null` for the first check, which reads the package only. */
  const [checkedBranch, setCheckedBranch] = useState<string | null>(null);
  const [lastCheck, setLastCheck] = useState<PackageCheck>();
  const [branch, setBranch] = useState("");
  const [reason, setReason] = useState("");
  /**
   * The newest version when the review opened, or when a newer one was found; `null` for none,
   * which is always the case for a new configuration.
   */
  const [compared, setCompared] = useState<number | null | undefined>(isNew ? null : undefined);
  /** The request of the first attempt; every retry sends it unchanged. */
  const [sent, setSent] = useState<Body>();
  const [preparing, setPreparing] = useState(false);
  const [outcome, setOutcome] = useState<Outcome>();
  const controller = useRef<AbortController>(undefined);

  const runCheck = (b: string | null) => {
    controller.current?.abort();
    const c = new AbortController();
    controller.current = c;
    setCheckedBranch(b);
    check.mutate(
      {
        params: {
          query: { limit: String(REVIEW_FINDINGS_LIMIT), ...(b === null ? {} : { branch: b }) },
        },
        body: { document },
        signal: c.signal,
      },
      {
        onSuccess: (data) => {
          setLastCheck(data);
          // The first check gives the package's own branch; the second validates against it.
          if (b === null) {
            const declared = defaultBranch(declaredContent(data));
            setBranch(declared);
            runCheck(declared);
          }
        },
        onError: (error) => {
          const message = b === null && !isAbort(error) ? refusalMessage(error) : undefined;
          if (message) onRefused(message);
        },
      }
    );
  };
  const runCheckRef = useRef(runCheck);
  runCheckRef.current = runCheck;

  useEffect(() => {
    runCheckRef.current(null);
    return () => controller.current?.abort();
  }, []);

  useEffect(() => {
    if (compared === undefined && config.data) setCompared(config.data.latest_version ?? null);
  }, [compared, config.data]);

  const effectiveBranch = branch.trim() || "main";
  useEffect(() => {
    if (checkedBranch === null || sent || effectiveBranch === checkedBranch) return;
    const timer = setTimeout(() => runCheckRef.current(effectiveBranch), BRANCH_CHECK_DELAY_MS);
    return () => clearTimeout(timer);
  }, [effectiveBranch, checkedBranch, sent]);

  const base = useQuery({
    ...configVersionQuery(configId ?? "", compared ?? 0),
    enabled: !isNew && typeof compared === "number",
  });

  const content = lastCheck ? declaredContent(lastCheck) : undefined;
  // Configuration names are unique: the Sync API refuses a new configuration, or a version that
  // renames its configuration, when another configuration has the name of the package. The
  // configurations with that name are read first, so Register is disabled before it is refused;
  // the refusal itself covers a configuration registered with that name meanwhile.
  const packageName = content?.configuration.name ?? "";
  const renames = !isNew && config.data !== undefined && config.data.name !== packageName;
  const namesake = useQuery({
    ...configsNamedQuery(packageName),
    enabled: !!packageName && (isNew || renames),
  });
  const refusedName = nameInUseOf(create.error);
  const nameInUse: NameInUse | undefined =
    refusedName ?? (isNew || renames ? otherNamesake(namesake.data?.items, configId) : undefined);
  const nextMappings = content?.configuration.schema_mapping ?? [];
  const baseMappings = base.data?.declared_content.configuration.schema_mapping;
  const diff =
    typeof compared === "number" && baseMappings && content
      ? diffMappings(compared, baseMappings, nextMappings)
      : undefined;

  const checkError = check.isError && !isAbort(check.error) ? check.error : undefined;
  const refusal = checkError ? refusalMessage(checkError) : undefined;
  const checkFailed = checkError !== undefined && refusal === undefined;
  const kind = check.data ? resultKind(check.data) : undefined;
  const validating =
    !checkError &&
    (check.isPending ||
      check.isIdle ||
      kind === "unchecked" ||
      (checkedBranch !== null && effectiveBranch !== checkedBranch));
  const comparisonReady = compared === null || (compared !== undefined && base.data !== undefined);
  /** The configuration or its newest version could not be read, so nothing can be compared. */
  const comparisonFailed = (!isNew && config.isError) || base.isError;

  /** The package is the newest version already, so registering it would add no version. */
  const sameAsNewest =
    !isNew &&
    sent === undefined &&
    lastCheck !== undefined &&
    base.data !== undefined &&
    lastCheck.package_checksum === base.data.package_checksum;
  const unchanged = outcome?.kind === "unchanged" || sameAsNewest;
  const noAnswer = create.isError && isNoAnswer(create.error);
  const registrable =
    sent !== undefined || (!validating && (kind === "valid" || kind === "unreachable"));
  const isDisabled = unchanged || !registrable || !comparisonReady || nameInUse !== undefined;
  const submitLabel =
    sent && noAnswer
      ? "Retry"
      : kind === "unreachable"
        ? "Register without validation"
        : "Register";

  const open = (id: string) => {
    onClose();
    void navigate({ to: "/configs/$configId", params: { configId: id }, search: {} });
  };

  const bodyFor = (): Body => ({
    document,
    reason: reason.trim() || null,
    ...(kind === "unreachable" ? { registered_without_validation: true } : {}),
  });

  const send = (sentBody: Body) => {
    // The mark is left out unless it is true, and `package` is not sent, so the body matches the
    // generated type only by a cast; the Sync API reads a missing mark as false.
    const body = sentBody as ConfigMutationRequest;
    if (configId === undefined) {
      registerConfig.mutate(
        { params: { header: { "Idempotency-Key": key } }, body },
        { onSuccess: (data) => open((data as RegisteredConfiguration).configuration.config_id) }
      );
      return;
    }
    createVersion.mutate(
      {
        params: { path: { config_id: configId }, header: { "Idempotency-Key": key } },
        body,
      },
      {
        onSuccess: (data) => {
          const registered = data as RegisteredVersion;
          if (!registered.created) {
            setOutcome({ kind: "unchanged", version: registered.version.registry_version });
            return;
          }
          open(configId);
        },
      }
    );
  };

  const register = async () => {
    if (sent) {
      send(sent);
      return;
    }
    if (!lastCheck) return;
    setOutcome(undefined);
    if (configId === undefined) {
      // A new configuration has no newest version to read again.
      const body = bodyFor();
      setSent(body);
      send(body);
      return;
    }
    // The newest version is read again, not taken from the cache, so a version registered
    // elsewhere since the review opened is found (FR-018), with the account that registered it
    // (FR-019).
    setPreparing(true);
    let newest: VersionSummary | undefined;
    try {
      const page = await queryClient.fetchQuery({ ...newestVersionQuery(configId), staleTime: 0 });
      newest = page.items.at(0);
    } catch (error) {
      setOutcome({ kind: "read-failed", error });
      return;
    } finally {
      setPreparing(false);
    }
    const latest = newest?.registry_version ?? null;
    if (latest !== compared) {
      // Only the comparison is reloaded: the check does not depend on the newest version.
      if (newest !== undefined) {
        setOutcome({
          kind: "meanwhile",
          version: newest.registry_version,
          registeredBy: newest.registered_by,
        });
      }
      setCompared(latest);
      return;
    }
    const body = bodyFor();
    setSent(body);
    send(body);
  };

  const close = () => {
    controller.current?.abort();
    onClose();
  };

  const name = config.data?.name ?? configId;
  const configuration = content?.configuration;
  const rename = isNew ? undefined : renameText(config.data?.name, configuration?.name);
  const title = isNew
    ? NEW_CONFIGURATION_TITLE
    : compared === undefined
      ? `Add a version to ${name}`
      : reviewTitle(name ?? "", (compared ?? 0) + 1);
  const credentials = content ? credentialVariables(content) : [];
  const sourceLabel = configuration ? describeSystem(configuration.source).label : "Source";
  const destinationLabel = configuration
    ? describeSystem(configuration.destination).label
    : "Destination";
  const entries = mappingEntries(nextMappings, diff);
  const removed = removedEntries(baseMappings, diff);

  // The unreachable warning stays next to Register whatever else the footer reports (FR-020).
  const unreachableWarning =
    kind === "unreachable" && !validating ? (
      <Notice tone="warning" role="note" title={MESSAGES.unreachable}>
        {check.data?.findings
          .filter((f) => f.severity === "error")
          .map((f) => (
            <p key={`${f.location}:${f.message}`}>{f.message}</p>
          ))}
      </Notice>
    ) : undefined;
  const nameInUseId = nameInUse?.configId;
  const nameNotice = nameInUse ? (
    <Notice
      tone="danger"
      role="alert"
      title={sameNameText(nameInUse.name)}
      action={
        nameInUseId === undefined ? undefined : (
          <Button
            size="xs"
            variant="ghost"
            className="-my-1"
            onPress={() => onAddAsVersion(nameInUseId)}
          >
            Add as a new version
          </Button>
        )
      }
    >
      <p>
        Configuration names are unique. Did you mean to add this package as a new version of{" "}
        {nameInUse.name ?? "that configuration"}?
      </p>
    </Notice>
  ) : undefined;
  const outcomeNotice = refusedName ? undefined : create.isError ? (
    noAnswer ? (
      <Notice tone="danger" role="alert" title={MESSAGES.requestFailed}>
        <p>
          Retrying sends the same request. No second {isNew ? "configuration" : "version"} is
          registered.
        </p>
      </Notice>
    ) : isAdministratorRefusal(create.error) ? (
      <Notice tone="danger" role="alert" title={`${MESSAGES.administratorRequired}.`} />
    ) : (
      <MutationError error={create.error} />
    )
  ) : outcome?.kind === "unchanged" ? (
    <Notice role="status" title={unchangedText(outcome.version)} />
  ) : sameAsNewest && typeof compared === "number" ? (
    <Notice role="status" title={sameAsNewestText(compared)} />
  ) : outcome?.kind === "meanwhile" ? (
    <Notice
      tone="warning"
      role="alert"
      title={registeredMeanwhileText(outcome.version, outcome.registeredBy)}
    >
      <p>The comparison below is with version {outcome.version} now.</p>
    </Notice>
  ) : outcome?.kind === "read-failed" ? (
    <MutationError error={outcome.error} />
  ) : undefined;
  const footer =
    nameNotice || outcomeNotice || unreachableWarning ? (
      <div className="flex flex-col gap-2">
        {nameNotice}
        {outcomeNotice}
        {unreachableWarning}
      </div>
    ) : undefined;

  return (
    <DialogFrame
      isOpen
      onOpenChange={(open) => !open && close()}
      title={title}
      ariaLabel={isNew ? NEW_CONFIGURATION_TITLE : `Add a version to ${name}`}
      description={
        configuration && (
          <>
            <p className="font-medium text-foreground [overflow-wrap:anywhere]">
              {configuration.name}
            </p>
            <p>
              From {describeSystem(configuration.source).label} to{" "}
              {describeSystem(configuration.destination).label}.
            </p>
            {rename && <p className="text-warning">{rename}</p>}
          </>
        )
      }
      error={footer}
      submitLabel={submitLabel}
      submitVariant={kind === "unreachable" && !sent ? "warning" : "primary"}
      pendingLabel={
        preparing
          ? "Reading the configuration again"
          : isNew
            ? "Registering the configuration"
            : "Registering the version"
      }
      isPending={preparing || create.isPending}
      isDisabled={isDisabled}
      disabledReason={
        unchanged
          ? undefined
          : nameInUse
            ? sameNameHint(nameInUse.name)
            : validating
              ? "Register is available once validation ends."
              : checkFailed
                ? "Validate again to continue."
                : comparisonFailed
                  ? "The configuration could not be read. Cancel and drop the package again."
                  : !comparisonReady
                    ? "Register is available once the newest version is read."
                    : refusal
                      ? "Change the validation branch to validate again."
                      : ((check.data && readFailureHint(check.data.destination_read)) ??
                        "Fix the findings, then drop the package again.")
      }
      dismissLabel={unchanged ? "Close" : "Cancel"}
      submitOnEnter={false}
      onSubmit={() => void register()}
    >
      {content === undefined ? (
        checkFailed ? (
          <CheckFailed onRetry={() => runCheck(checkedBranch)} />
        ) : refusal ? (
          <Notice tone="danger" role="alert">
            <p>
              <InlineCode text={refusal} />
            </p>
          </Notice>
        ) : (
          <output className="block text-foreground-muted text-sm">Reading the package…</output>
        )
      ) : (
        <>
          {!isNew && config.isError && <MutationError error={config.error} />}

          {typeof compared === "number" && (
            <section aria-labelledby="review-comparison" className="flex flex-col gap-2">
              <h3 id="review-comparison" className="font-medium text-foreground text-sm">
                Comparison with version {compared}
              </h3>
              {base.data ? (
                <MappingList
                  key={compared}
                  entries={entries}
                  removed={removed}
                  diff={diff}
                  detail={(entry) => (
                    <MappingDetail
                      entry={entry}
                      against={compared}
                      sourceLabel={sourceLabel}
                      destinationLabel={destinationLabel}
                    />
                  )}
                />
              ) : base.isError ? (
                <MutationError error={base.error} />
              ) : (
                <output className="block text-foreground-muted text-sm">
                  Reading version {compared}…
                </output>
              )}
            </section>
          )}

          <section aria-labelledby="review-credentials" className="flex flex-col gap-1.5">
            <h3 id="review-credentials" className="font-medium text-foreground text-sm">
              Credential variables
            </h3>
            {credentials.length === 0 ? (
              <p className="text-foreground-muted text-sm">
                The package refers to no credential variable.
              </p>
            ) : (
              <ul className="flex flex-wrap gap-1.5">
                {credentials.map((variable) => (
                  <li key={variable}>
                    <code className={CODE_CHIP}>{variable}</code>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <Field
            id="review-branch"
            label="Validation branch"
            hint="The destination schema on this branch is checked. Leave empty to use main."
          >
            <input
              id="review-branch"
              className={cn(INPUT, "font-mono")}
              value={branch}
              // Register reads the branch's check and the reason when it is pressed, so neither
              // changes while the configuration is read again.
              readOnly={sent !== undefined || checkedBranch === null || preparing}
              onChange={(e) => setBranch(e.target.value)}
              autoComplete="off"
              autoCapitalize="none"
              autoCorrect="off"
              spellCheck={false}
              aria-describedby="review-branch-hint"
            />
          </Field>

          <ValidationResult
            check={check.data}
            kind={kind}
            branch={effectiveBranch}
            validating={validating}
            failed={checkFailed}
            refusal={refusal}
            locked={sent !== undefined}
            onRetry={() => runCheck(checkedBranch ?? effectiveBranch)}
          />

          <ReasonField
            id="review-reason"
            value={reason}
            onChange={setReason}
            maxLength={REASON_MAX_LENGTH}
            readOnly={sent !== undefined || preparing}
          />
        </>
      )}
    </DialogFrame>
  );
}

/**
 * The drop area of a page and the review step of the package file dropped on it. A first check
 * the Sync API refuses closes the review step and shows its message under the area.
 */
export function PackageRegistration({
  action,
  target,
}: {
  /** The words before the drop area's instructions: "Add a version". */
  action: string;
  target: ReviewTarget;
}) {
  /**
   * The text of the package file under review, where it goes, and the refusal of the last one.
   * The target is the page's until the review is moved to the configuration that has the name of
   * the package.
   */
  const [dropped, setDropped] = useState<{ text: string; key: number; target: ReviewTarget }>();
  const [message, setMessage] = useState<string>();
  // Only an administrator token can register; the area stays disabled until `/me` answers.
  const administrator = useQuery(meQuery()).data?.administrator === true;
  return (
    <>
      <div className="mb-6">
        <PackageDropArea
          action={action}
          disabled={!administrator}
          message={message}
          onMessage={setMessage}
          onFile={(text) => setDropped({ text, key: Date.now(), target })}
        />
      </div>
      {dropped && (
        <PackageReview
          key={dropped.key}
          target={dropped.target}
          document={dropped.text}
          onClose={() => setDropped(undefined)}
          onRefused={(refusal) => {
            setDropped(undefined);
            setMessage(refusal);
          }}
          onAddAsVersion={(configId) =>
            setDropped({
              text: dropped.text,
              key: Date.now(),
              target: { kind: "version", configId },
            })
          }
        />
      )}
    </>
  );
}

function CheckFailed({ onRetry, locked = false }: { onRetry: () => void; locked?: boolean }) {
  return (
    <Notice
      tone="danger"
      role="alert"
      title={MESSAGES.checkFailed}
      action={
        locked ? undefined : (
          <Button size="xs" variant="ghost" className="-my-1" onPress={onRetry}>
            Validate again
          </Button>
        )
      }
    />
  );
}

/** The result of the latest check, or that it is still running. */
function ValidationResult({
  check,
  kind,
  branch,
  validating,
  failed,
  refusal,
  locked,
  onRetry,
}: {
  check?: PackageCheck;
  kind?: ReturnType<typeof resultKind>;
  branch: string;
  validating: boolean;
  failed: boolean;
  refusal?: string;
  locked: boolean;
  onRetry: () => void;
}) {
  if (failed) return <CheckFailed onRetry={onRetry} locked={locked} />;
  if (refusal) {
    return (
      <Notice tone="danger" role="alert">
        <p>
          <InlineCode text={refusal} />
        </p>
      </Notice>
    );
  }
  if (validating || !check) {
    return (
      <output className="block text-foreground-muted text-sm">{validatingText(branch)}</output>
    );
  }
  const warnings = warningFindings(check);
  if (kind === "valid" || kind === "unreachable" || kind === "unchecked") {
    // The unreachable destination is reported next to Register; warnings never block.
    return (
      <>
        {kind === "valid" && (
          <output className="flex items-center gap-1.5 font-medium text-sm text-success">
            <CheckIcon className="size-4 shrink-0" />
            {validText(branch)}
          </output>
        )}
        {warnings.length > 0 && (
          <Notice
            tone="warning"
            role="status"
            title={`${plural(warnings.length, "warning", "warnings")} against branch ${branch}`}
          >
            <FindingList findings={warnings} />
          </Notice>
        )}
      </>
    );
  }
  if (kind !== "findings") return null;
  const more = moreFindingsText(check);
  return (
    <Notice
      tone="danger"
      role="status"
      title={`${plural(check.total_findings, "finding", "findings")} against branch ${branch}`}
    >
      <FindingList findings={check.findings} />
      {more && (
        <p>
          <InlineCode text={more} />
        </p>
      )}
    </Notice>
  );
}

/** One line per finding: its severity when it is a warning, its location and its message. */
function FindingList({ findings }: { findings: readonly ValidationFinding[] }) {
  return (
    <ul className="flex flex-col gap-1">
      {findings.map((f) => (
        <li key={`${f.code}:${f.location}:${f.message}`} className="[overflow-wrap:anywhere]">
          {f.severity === "warning" && <span className="font-medium">Warning: </span>}
          {f.location && <code className={CODE_CHIP}>{f.location}</code>} {f.message}
        </li>
      ))}
    </ul>
  );
}
