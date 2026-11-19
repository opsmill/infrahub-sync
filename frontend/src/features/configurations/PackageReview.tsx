import { Button } from "@infrahub/ui";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { cn } from "tailwind-variants";

import {
  useCheckPackage,
  useCreateConfigVersion,
  useIdempotencyKey,
  useRegisterConfig,
} from "#/api/actions";
import { configQuery, configsNamedQuery, configVersionQuery } from "#/api/queries";
import type {
  ConfigMutationRequest,
  PackageCheck,
  RegisteredConfiguration,
  RegisteredVersion,
} from "#/api/types";
import { CheckIcon } from "#/components/icons";
import { CODE_CHIP, InlineCode, Notice } from "#/components/page";
import { diffMappings, mappingEntries, removedEntries } from "#/domain/mapping";
import { plural } from "#/domain/run-state";
import { describeSystem } from "#/domain/systems";
import { DialogFrame, Field, INPUT, LINK, MutationError, ReasonField } from "#/features/runs/form";

import { MappingList } from "./detail-mapping";
import {
  credentialVariables,
  declaredContent,
  defaultBranch,
  isAbort,
  isNoAnswer,
  MESSAGES,
  moreFindingsText,
  NEW_CONFIGURATION_TITLE,
  REASON_MAX_LENGTH,
  REVIEW_FINDINGS_LIMIT,
  refusalMessage,
  registeredMeanwhileText,
  renameText,
  resultKind,
  reviewTitle,
  sameNameText,
  unchangedText,
  validatingText,
  validText,
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
 * The request body. `registered_without_validation` is sent only as `true`, when Register runs
 * while the destination cannot be reached (FR-021); otherwise it is left out. It is part of the
 * locked body, so a retry sends the same request with the same idempotency key.
 */
type Body = {
  package: Record<string, unknown>;
  reason: string | null;
  registered_without_validation?: true;
};
type Outcome =
  | { kind: "unchanged"; version: number }
  | { kind: "meanwhile"; version: number }
  | { kind: "read-failed"; error: unknown };

/**
 * The review step for one dropped package file: what it declares, how its mapping differs from
 * the newest version (for a new version) or whether its name is in use (for a new
 * configuration), and the result of validating it against a destination branch. Register is
 * enabled only for a valid package, or, as "Register without validation", when the destination
 * cannot be reached.
 */
export function PackageReview({
  target,
  document,
  onClose,
  onRefused,
}: {
  target: ReviewTarget;
  /** The text of the dropped file; the Sync API parses it. */
  document: string;
  onClose: () => void;
  /** A first check the Sync API refused: the file is not a package it accepts. */
  onRefused: (message: string) => void;
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
  // Names are not unique: a configuration with the same name is reported, never refused (FR-013).
  const packageName = content?.configuration.name ?? "";
  const namesake = useQuery({ ...configsNamedQuery(packageName), enabled: isNew && !!packageName });
  const existing = isNew ? namesake.data?.items[0] : undefined;
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

  const unchanged = outcome?.kind === "unchanged";
  const noAnswer = create.isError && isNoAnswer(create.error);
  const registrable =
    sent !== undefined || (!validating && (kind === "valid" || kind === "unreachable"));
  const isDisabled = unchanged || !registrable || !comparisonReady;
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

  const bodyFor = (declared: Record<string, unknown>): Body => ({
    package: declared,
    reason: reason.trim() || null,
    ...(kind === "unreachable" ? { registered_without_validation: true } : {}),
  });

  const send = (sentBody: Body) => {
    // The mark is left out unless it is true, so the body matches the generated type only by a
    // cast; the Sync API reads a missing mark as false.
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
      const body = bodyFor(lastCheck.declared_content);
      setSent(body);
      send(body);
      return;
    }
    // The configuration is read again, not taken from the cache, so a version registered
    // elsewhere since the review opened is found (FR-018).
    setPreparing(true);
    let latest: number | null;
    try {
      const fresh = await queryClient.fetchQuery({ ...configQuery(configId), staleTime: 0 });
      latest = fresh.latest_version ?? null;
    } catch (error) {
      setOutcome({ kind: "read-failed", error });
      return;
    } finally {
      setPreparing(false);
    }
    if (latest !== compared) {
      if (latest !== null) setOutcome({ kind: "meanwhile", version: latest });
      setCompared(latest);
      runCheck(effectiveBranch);
      return;
    }
    const body = bodyFor(lastCheck.declared_content);
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
  const entries = mappingEntries(nextMappings, diff);
  const removed = removedEntries(baseMappings, diff);

  // The unreachable warning stays next to Register whatever else the footer reports (FR-020).
  const unreachableWarning =
    kind === "unreachable" && !validating ? (
      <Notice tone="warning" role="note" title={MESSAGES.unreachable}>
        {check.data?.findings.map((f) => (
          <p key={`${f.location}:${f.message}`}>{f.message}</p>
        ))}
      </Notice>
    ) : undefined;
  const outcomeNotice = create.isError ? (
    noAnswer ? (
      <Notice tone="danger" role="alert" title={MESSAGES.requestFailed}>
        <p>
          Retrying sends the same request. No second {isNew ? "configuration" : "version"} is
          registered.
        </p>
      </Notice>
    ) : refusalMessage(create.error) === `${MESSAGES.administratorRequired}.` ? (
      <Notice tone="danger" role="alert" title={`${MESSAGES.administratorRequired}.`} />
    ) : (
      <MutationError error={create.error} />
    )
  ) : outcome?.kind === "unchanged" ? (
    <Notice role="status" title={unchangedText(outcome.version)} />
  ) : outcome?.kind === "meanwhile" ? (
    <Notice tone="warning" role="alert" title={registeredMeanwhileText(outcome.version)}>
      <p>The comparison below is with version {outcome.version} now.</p>
    </Notice>
  ) : outcome?.kind === "read-failed" ? (
    <MutationError error={outcome.error} />
  ) : undefined;
  const footer =
    outcomeNotice || unreachableWarning ? (
      <div className="flex flex-col gap-2">
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
          : validating
            ? "Register is available once validation ends."
            : checkFailed
              ? "Validate again to continue."
              : !comparisonReady
                ? "Register is available once the newest version is read."
                : "Fix the findings, then drop the package again."
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
          {existing && (
            <Notice
              tone="warning"
              role="note"
              title={sameNameText(existing.name ?? configuration?.name ?? "")}
            >
              <p>
                <Link
                  to="/configs/$configId"
                  params={{ configId: existing.config_id }}
                  search={{}}
                  className={LINK}
                  onClick={onClose}
                >
                  Add a version to it instead
                </Link>
              </p>
            </Notice>
          )}

          {typeof compared === "number" && (
            <section aria-labelledby="review-comparison" className="flex flex-col gap-2">
              <h3 id="review-comparison" className="font-medium text-foreground text-sm">
                Comparison with version {compared}
              </h3>
              {base.data ? (
                <MappingList entries={entries} removed={removed} diff={diff} />
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
              readOnly={sent !== undefined || checkedBranch === null}
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
            readOnly={sent !== undefined}
          />
        </>
      )}
    </DialogFrame>
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
  if (kind === "valid") {
    return (
      <output className="flex items-center gap-1.5 font-medium text-sm text-success">
        <CheckIcon className="size-4 shrink-0" />
        {validText(branch)}
      </output>
    );
  }
  // The unreachable destination is reported next to Register.
  if (kind !== "findings") return null;
  const more = moreFindingsText(check);
  return (
    <Notice
      tone="danger"
      role="status"
      title={`${plural(check.total_findings, "finding", "findings")} against branch ${branch}`}
    >
      <ul className="flex flex-col gap-1">
        {check.findings.map((f) => (
          <li key={`${f.code}:${f.location}:${f.message}`} className="[overflow-wrap:anywhere]">
            {f.location && <code className={CODE_CHIP}>{f.location}</code>} {f.message}
          </li>
        ))}
      </ul>
      {more && (
        <p>
          <InlineCode text={more} />
        </p>
      )}
    </Notice>
  );
}
