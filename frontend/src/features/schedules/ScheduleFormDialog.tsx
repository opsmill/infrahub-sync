import { Button, Checkbox, Modal } from "@infrahub/ui";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import {
  type CSSProperties,
  type ReactNode,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { cn } from "tailwind-variants";

import { useChangeSchedule, useCreateSchedule, useIdempotencyKey } from "#/api/actions";
import {
  configScheduleQuery,
  configsQuery,
  describeScheduleError,
  meQuery,
  scheduleQuery,
  versionsQuery,
} from "#/api/queries";
import type { Schedule } from "#/api/types";
import { announce } from "#/components/announcer";
import { ChevronRightIcon } from "#/components/icons";
import { FOCUS, FOCUS_INSET, Notice } from "#/components/page";
import {
  branchKeptOpen,
  changedFields,
  changeRequest,
  createRequest,
  differsFromOpening,
  formValuesOf,
  overlapPolicyLabel,
  type ScheduleFormValues,
  writeTargetLabel,
} from "#/domain/schedules";
import {
  Field,
  finePointer,
  INPUT,
  LINK,
  MutationError,
  ReasonField,
  resendHint,
} from "#/features/runs/form";

import { CadenceField, PREVIEW_COUNT, useCadencePreview } from "./CadenceField";
import { CadenceTimeline, NextStartLine } from "./CadenceTimeline";
import { configVersionChoices, configVersionKey, parseConfigVersionKey } from "./config-choices";
import { FieldMessage, FormSelect, RefusalText } from "./form-parts";
import {
  type FieldRefusal,
  fieldRefusal,
  formProblems,
  LATE_START_MAX_MINUTES,
  LATE_START_MIN_MINUTES,
  lateStartMinutesText,
  lateStartProblem,
  lateStartSeconds,
  NAME_HINT,
  type ProblemField,
} from "./form-rules";
import { browserTimeZone, timeZoneChoices } from "./time-zones";

/**
 * Where the form was opened: the create form, with or without a configuration chosen, or the
 * change form of one schedule.
 */
export type ScheduleFormTarget =
  | {
      mode: "create";
      /** The configuration of the page the form was opened from; the form offers a choice without it. */
      config?: { configId: string; name: string; latestVersion?: number | null };
    }
  | {
      mode: "change";
      /** The schedule as the page read it when the form opened. */
      schedule: Schedule;
      configName: string;
      /** Opened from the newer-version notice: the newest version is filled in. */
      newestVersion?: boolean;
    };

/** The cadence a new schedule starts with (tasks T035): every day at 02:00. */
const DEFAULT_CRON = "0 2 * * *";

const UTC_NOTICE = "The CLI fills in UTC. Schedules of your team can be in different time zones.";
const APPROVAL_REASON =
  "This Sync API requires an approved plan before every write to the default branch.";
const LEFT_OUT_REASON =
  "A run with branch review always writes the remaining records, and its proposed change states the count left out.";

const WRITE_TARGETS = [
  {
    value: "branch-review",
    hint: "Each run writes to a new Infrahub branch and opens a proposed change for review.",
  },
  {
    value: "default-branch",
    hint: "Each run writes to the default branch without a review.",
  },
] as const;

/** Below the `sm` breakpoint the dialog fills the screen and folds the preview into one line. */
const NARROW = "(max-width: 639px)";
const subscribeNarrow = (onChange: () => void) => {
  const query = window.matchMedia(NARROW);
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
};
const useNarrow = () =>
  useSyncExternalStore(subscribeNarrow, () => window.matchMedia(NARROW).matches);

/** A large dialog: 1100 px wide at most, 90 % of the viewport high, anchored 5 % from the top. */
const WIDE_FRAME: CSSProperties = {
  top: "5vh",
  translate: "-50% 0",
  width: "min(1100px, 94vw)",
  maxWidth: "94vw",
  height: "90vh",
  maxHeight: "90vh",
};
/** The full-screen sheet of a narrow screen. */
const SHEET_FRAME: CSSProperties = {
  top: 0,
  left: 0,
  translate: "none",
  width: "100vw",
  maxWidth: "100vw",
  height: "var(--visual-viewport-height, 100dvh)",
  maxHeight: "none",
  borderRadius: 0,
  borderWidth: 0,
  padding: 0,
};

/** One part of the form: a heading and a sentence above its fields. */
function FormSection({
  title,
  hint,
  children,
}: {
  title: string;
  hint: string;
  children: ReactNode;
}) {
  const id = useId();
  return (
    <section
      aria-labelledby={id}
      className="flex flex-col gap-4 border-t py-6 first:border-t-0 first:pt-0"
    >
      <div className="flex flex-col gap-1">
        <h3 id={id} className="font-semibold text-base tracking-tight">
          {title}
        </h3>
        <p className="text-pretty text-foreground-muted text-sm">{hint}</p>
      </div>
      <div className="flex min-w-0 flex-col gap-4">{children}</div>
    </section>
  );
}

/** The values of the form while the person edits them; the version is chosen or the latest. */
interface FormState extends Omit<ScheduleFormValues, "registry_version" | "cron"> {
  /** `undefined` until the person chooses one; the latest version is used meanwhile. */
  registry_version: number | undefined;
  /** `null` while the cadence helper misses a value. */
  cron: string | null;
}

const createValues = (): FormState => ({
  registry_version: undefined,
  cron: DEFAULT_CRON,
  timezone: browserTimeZone(),
  write_target: "branch-review",
  write_when_left_out: false,
  overlap_policy: "reject",
  start_late_by_at_most_seconds: 3600,
});

/**
 * Create a schedule, or change one, in a wide dialog (design-schedule-form.md): the form in
 * sections on the left, the next starts of the cadence entered on the right, and a footer with
 * the number of settings changed. The change form shows the name and the configuration as text
 * and sends only the settings that differ from the values it opened with (spec FR-018, FR-023).
 * A save with a problem lists the problems, shows each under its field and focuses the first.
 */
export function ScheduleFormDialog({
  target,
  onClose,
}: {
  target: ScheduleFormTarget;
  onClose: () => void;
}) {
  const id = useId();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const create = useCreateSchedule();
  const change = useChangeSchedule();
  const [key, renewKey] = useIdempotencyKey();

  const changing = target.mode === "change" ? target.schedule : undefined;
  const fixedConfig =
    target.mode === "create"
      ? target.config
      : { configId: target.schedule.config_id, name: target.configName };
  const mutation = changing ? change : create;

  // The change form keeps the values it opened with until the person reloads them.
  const [opening, setOpening] = useState(() => (changing ? formValuesOf(changing) : undefined));
  const [name, setName] = useState("");
  const [chosenConfig, setChosenConfig] = useState<string | undefined>(undefined);
  const [form, setForm] = useState<FormState>(() =>
    changing
      ? {
          ...formValuesOf(changing),
          registry_version:
            target.mode === "change" && target.newestVersion && changing.newer_version != null
              ? changing.newer_version
              : changing.registry_version,
        }
      : createValues()
  );
  const [lateText, setLateText] = useState(() =>
    lateStartMinutesText(form.start_late_by_at_most_seconds)
  );
  const [reason, setReason] = useState("");
  /** Renewed on a reload, so the cadence helper chooses its form again from the reloaded value. */
  const [cadenceKey, setCadenceKey] = useState(0);
  const [confirmReload, setConfirmReload] = useState(false);
  const [nothingChanged, setNothingChanged] = useState(false);
  /** Set while the stored schedule is read after `orchestration-unavailable`. */
  const [finding, setFinding] = useState(false);
  /** Set by the first save: from then on, each problem shows under its field. */
  const [attempted, setAttempted] = useState(false);
  /** The narrow screen's preview, folded into one line above the footer until opened. */
  const [previewOpen, setPreviewOpen] = useState(false);
  const narrow = useNarrow();
  const submitRef = useRef<HTMLButtonElement>(null);

  const configId = fixedConfig?.configId ?? chosenConfig;
  const configs = useQuery({ ...configsQuery(), enabled: !fixedConfig });
  // A fixed configuration has its own version choice; otherwise one choice holds both.
  const versions = useQuery({
    ...versionsQuery(fixedConfig?.configId ?? ""),
    enabled: Boolean(fixedConfig?.configId),
  });
  // The page reads the schedule every 10 seconds; the form compares that read with its opening values.
  const polled = useQuery({
    ...scheduleQuery(changing?.schedule_id ?? ""),
    enabled: Boolean(changing),
  }).data;
  const latest = polled ?? changing;
  const me = useQuery(meQuery()).data;
  const approvalRequired = me?.approval_required === true;
  const browserZone = useMemo(browserTimeZone, []);
  const storedZone = opening?.timezone;
  const zones = useMemo(
    () => timeZoneChoices(storedZone ?? browserZone),
    [storedZone, browserZone]
  );
  const preview = useCadencePreview(form.cron, form.timezone);

  const numbers = useMemo(
    () => (versions.data?.items ?? []).map((v) => v.registry_version).sort((a, b) => b - a),
    [versions.data]
  );
  const summary = configs.data?.items.find((c) => c.config_id === configId);
  const configOptions = useMemo(
    () => configVersionChoices(configs.data?.items ?? []),
    [configs.data]
  );
  const zoneOptions = useMemo(() => zones?.map((z) => ({ id: z, label: z })), [zones]);
  const newestVersion =
    numbers[0] ??
    (target.mode === "create" ? target.config?.latestVersion : undefined) ??
    summary?.latest_version ??
    undefined;
  const version = form.registry_version ?? newestVersion;

  /** Any change by the person makes it a new request; the answer to the last one no longer applies. */
  const edited = () => {
    renewKey();
    mutation.reset();
    setNothingChanged(false);
  };
  const set = (patch: Partial<FormState>) => {
    setForm((f) => ({ ...f, ...patch }));
    edited();
  };

  const changedSinceOpening =
    opening !== undefined && latest !== undefined && differsFromOpening(opening, latest);
  const keptBranch = opening && latest ? branchKeptOpen(opening, form.write_target, latest) : null;

  /** Replace the entered values with the schedule as last read; they become the opening values. */
  const reload = () => {
    if (!latest) return;
    const values = formValuesOf(latest);
    setOpening(values);
    setForm({ ...values });
    setLateText(lateStartMinutesText(values.start_late_by_at_most_seconds));
    setReason("");
    setCadenceKey((k) => k + 1);
    setConfirmReload(false);
    edited();
  };
  const askReload = () => {
    if (!opening) return;
    const values = {
      ...form,
      registry_version: form.registry_version ?? opening.registry_version,
      cron: form.cron ?? "",
    };
    const entered =
      reason.trim() !== "" ||
      lateText !== lateStartMinutesText(opening.start_late_by_at_most_seconds) ||
      changeRequest(opening, values, "") !== null;
    if (entered) setConfirmReload(true);
    else reload();
  };

  const refusal = fieldRefusal(mutation.error);
  const refusalFor = (field: FieldRefusal["field"]) => (refusal?.field === field ? refusal : null);
  const errorId = (field: string) => `${id}-${field}-refusal`;

  // The body scrolls: bring a refusal shown under a field into view when it arrives.
  const refusedField = refusal?.field;
  useEffect(() => {
    if (refusedField) {
      document
        .getElementById(`${id}-${refusedField}-refusal`)
        ?.scrollIntoView({ block: "nearest" });
    }
  }, [id, refusedField, mutation.error]);

  const lateProblem = lateStartProblem(lateText, opening?.start_late_by_at_most_seconds);
  const problems = formProblems({
    changing: Boolean(changing),
    name,
    configId,
    version,
    versionsPending: versions.isPending,
    cron: form.cron,
    timezone: form.timezone,
    lateProblem,
  });
  const problemOf = (field: ProblemField) =>
    attempted ? problems.find((p) => p.field === field)?.text : undefined;
  const fieldId = (field: ProblemField) => `${id}-${field}`;
  /** Moves to a field with a problem; a select's id is on its wrapper, so its trigger is focused. */
  const focusField = (field: ProblemField) => {
    const el = document.getElementById(fieldId(field));
    const target = el?.matches("input,button,select,textarea")
      ? el
      : el?.querySelector<HTMLElement>("input,button,select,textarea");
    target?.focus();
    target?.scrollIntoView({ block: "nearest" });
  };
  const changeCount =
    opening === undefined
      ? 0
      : changedFields(opening, {
          ...form,
          registry_version: version ?? opening.registry_version,
        }).length;

  const open = (schedule: Schedule) => {
    onClose();
    announce("Schedule created");
    navigate({ to: "/schedules/$scheduleId", params: { scheduleId: schedule.schedule_id } });
  };

  const submitChange = (schedule: Schedule, values: ScheduleFormValues) => {
    if (!opening) return;
    const body = changeRequest(opening, values, reason);
    if (body === null) {
      setNothingChanged(true);
      return;
    }
    change.mutate(
      {
        params: { path: { schedule_id: schedule.schedule_id }, header: { "Idempotency-Key": key } },
        body,
      },
      {
        onSuccess: () => {
          onClose();
          announce("Schedule changed");
        },
      }
    );
  };

  const submit = () => {
    setAttempted(true);
    if (problems.length > 0) {
      focusField(problems[0].field);
      return;
    }
    if (!configId || version === undefined || form.cron === null) return;
    const values: ScheduleFormValues = { ...form, registry_version: version, cron: form.cron };
    if (changing) {
      submitChange(changing, values);
      return;
    }
    create.mutate(
      {
        params: { header: { "Idempotency-Key": key } },
        body: createRequest(values, name, configId, reason),
      },
      {
        onSuccess: open,
        onError: async (error) => {
          if (describeScheduleError(error).code !== "orchestration-unavailable") return;
          // The Sync API stored the schedule; its answer holds none, so the stored one is read.
          setFinding(true);
          try {
            const page = await queryClient.fetchQuery(configScheduleQuery(configId));
            const stored = page.items[0];
            if (stored) open(stored);
          } catch {
            // The refusal stays shown, and says to open the schedule page.
          } finally {
            setFinding(false);
          }
        },
      }
    );
  };

  const schedulesOfConfig = changing ? (
    "close this form and view the settings of this schedule"
  ) : configId ? (
    <Link to="/schedules" search={{ config: configId }} className={LINK} onClick={onClose}>
      view the schedule of this configuration
    </Link>
  ) : (
    "view the Schedules page"
  );
  const submitLabel = changing ? "Save changes" : "Create schedule";
  const refusalBelow = (field: FieldRefusal["field"], extra?: ReactNode) => {
    const r = refusalFor(field);
    return (
      r && (
        <FieldMessage id={errorId(field)}>
          <RefusalText refusal={r}>{extra}</RefusalText>
        </FieldMessage>
      )
    );
  };

  const showZoneNotice = !changing && browserZone !== "UTC";
  const timezoneRefusal = preview.refusal?.field === "timezone" ? preview.refusal : null;
  const zoneDescribedBy =
    [
      showZoneNotice ? `${id}-zone-hint` : undefined,
      timezoneRefusal || refusalFor("timezone") ? errorId("timezone") : undefined,
    ]
      .filter(Boolean)
      .join(" ") || undefined;

  const changedNotice = changedSinceOpening && (
    <Notice
      tone="warning"
      role="status"
      title="This schedule changed since you opened this form."
      action={
        confirmReload ? undefined : (
          <Button variant="outline" size="sm" onPress={askReload}>
            Reload
          </Button>
        )
      }
    >
      {confirmReload ? (
        <div className="flex flex-col gap-2">
          <p>Replace the values you entered with the current values of this schedule?</p>
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="outline" size="sm" onPress={reload}>
              Replace my values
            </Button>
            <Button variant="ghost" size="sm" onPress={() => setConfirmReload(false)}>
              Keep my values
            </Button>
          </div>
        </div>
      ) : (
        <p>
          Saving sends only the settings you changed, so the other changes are kept. Reload to view
          the current values.
        </p>
      )}
    </Notice>
  );

  const isPending = mutation.isPending || finding;
  // While the request is in flight the dialog cannot be closed, so its answer stays in view.
  const close = () => {
    if (!isPending) onClose();
  };
  const titleId = `${id}-title`;
  const descId = `${id}-desc`;
  const noticeId = `${id}-notice`;
  const formRef = useRef<HTMLFormElement>(null);
  // The vendor Modal forwards only aria-label to the dialog element.
  const describedBy = [descId, changedNotice && noticeId].filter(Boolean).join(" ");
  useLayoutEffect(() => {
    formRef.current?.closest("[role=dialog]")?.setAttribute("aria-describedby", describedBy);
  }, [describedBy]);

  const problemLine = (field: ProblemField) => {
    const text = problemOf(field);
    return (
      text && (
        <FieldMessage id={`${fieldId(field)}-problem`}>
          <p>{text}</p>
        </FieldMessage>
      )
    );
  };
  const describe = (...ids: (string | false | null | undefined)[]) =>
    ids.filter(Boolean).join(" ") || undefined;

  const footerMessages =
    nothingChanged || (mutation.error && !refusal) || (attempted && problems.length > 0) ? (
      <div className="flex max-h-[30vh] shrink-0 flex-col gap-2 overflow-y-auto border-t px-6 py-3 max-sm:px-4">
        {nothingChanged && (
          <Notice tone="neutral" role="status" title="Nothing changed">
            <p>Change a setting before you save. No request was sent.</p>
          </Notice>
        )}
        {mutation.error && !refusal && (
          <MutationError
            error={mutation.error}
            describe={describeScheduleError}
            retryHint={resendHint(submitLabel, { check: schedulesOfConfig })}
          />
        )}
        {attempted && problems.length > 0 && (
          <div role="alert" className="flex flex-col gap-1 text-sm">
            <p className="font-medium text-danger">
              {problems.length === 1
                ? "One value needs a change before you save:"
                : `${problems.length} values need a change before you save:`}
            </p>
            <ul className="flex flex-col gap-0.5">
              {problems.map((p) => (
                <li key={p.field}>
                  <button
                    type="button"
                    onClick={() => focusField(p.field)}
                    className={cn(
                      "rounded-sm text-left text-foreground-muted underline decoration-[0.09em] underline-offset-[0.2em] hover:text-foreground",
                      FOCUS
                    )}
                  >
                    {p.text}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    ) : null;

  return (
    <Modal
      isOpen
      onOpenChange={(o) => !o && close()}
      isDismissable={!isPending}
      aria-label={
        changing
          ? `Change the schedule ${changing.name}`
          : fixedConfig
            ? `Create a schedule for ${fixedConfig.name}`
            : "Create a schedule"
      }
      style={narrow ? SHEET_FRAME : WIDE_FRAME}
    >
      <form
        ref={formRef}
        noValidate
        aria-labelledby={titleId}
        className="flex h-full min-h-0 flex-col"
        onSubmit={(e) => {
          e.preventDefault();
          if (isPending) return;
          // The body turns inert during the request; focus waits on the pending button instead.
          if (problems.length === 0) submitRef.current?.focus();
          submit();
        }}
      >
        <div className="flex shrink-0 flex-col gap-1 border-b px-6 pt-5 pb-4 max-sm:px-4">
          <h2 id={titleId} className="text-balance font-semibold text-lg tracking-tight">
            {changing ? "Change the schedule" : "Create a schedule"}
          </h2>
          <div id={descId} className="text-pretty text-foreground-muted text-sm">
            {changing ? (
              <p>
                Changes the settings of{" "}
                <span className="font-medium text-foreground [overflow-wrap:anywhere]">
                  {changing.name}
                </span>
                . Only the settings you change are sent.
              </p>
            ) : fixedConfig ? (
              <p>
                Starts sync runs of{" "}
                <span className="font-medium text-foreground [overflow-wrap:anywhere]">
                  {fixedConfig.name}
                </span>{" "}
                on a cadence.
              </p>
            ) : (
              <p>Starts sync runs of a configuration on a cadence.</p>
            )}
          </div>
        </div>

        <div className="flex min-h-0 flex-1">
          <div
            className="flex min-w-0 flex-1 flex-col gap-6 overflow-y-auto overscroll-contain px-6 py-6 max-sm:px-4"
            inert={isPending}
          >
            {changedNotice && <div id={noticeId}>{changedNotice}</div>}
            <fieldset disabled={isPending} className="m-0 flex min-w-0 flex-col border-0 p-0">
              <FormSection title="Configuration" hint="The configuration version each run uses.">
                {changing ? (
                  <div className="flex flex-col gap-1.5">
                    <p className="font-medium text-foreground text-sm">Name</p>
                    <p className="text-sm [overflow-wrap:anywhere]">{changing.name}</p>
                  </div>
                ) : (
                  <Field id={fieldId("name")} label="Name" hint={NAME_HINT}>
                    <input
                      id={fieldId("name")}
                      className={INPUT}
                      value={name}
                      onChange={(e) => {
                        setName(e.target.value);
                        edited();
                      }}
                      autoComplete="off"
                      autoCapitalize="none"
                      autoCorrect="off"
                      spellCheck={false}
                      // biome-ignore lint/a11y/noAutofocus: only with a fine pointer, inside a modal that just opened.
                      autoFocus={finePointer()}
                      aria-invalid={Boolean(problemOf("name") || refusalFor("name"))}
                      aria-describedby={describe(
                        `${fieldId("name")}-hint`,
                        problemOf("name") && `${fieldId("name")}-problem`,
                        refusalFor("name") && errorId("name")
                      )}
                    />
                    {problemLine("name")}
                    {refusalBelow("name")}
                  </Field>
                )}

                <div
                  id={fixedConfig ? undefined : fieldId("config")}
                  className="flex flex-col gap-1.5"
                >
                  {fixedConfig ? (
                    <>
                      <p className="font-medium text-foreground text-sm">Configuration</p>
                      <p className="text-sm [overflow-wrap:anywhere]">{fixedConfig.name}</p>
                    </>
                  ) : (
                    <FormSelect
                      label="Configuration and version"
                      placeholder={
                        configs.isPending
                          ? "Loading configurations…"
                          : "Choose a configuration version"
                      }
                      value={
                        chosenConfig && form.registry_version !== undefined
                          ? configVersionKey(chosenConfig, form.registry_version)
                          : undefined
                      }
                      options={configOptions}
                      searchable
                      noMatch="No configuration version matches"
                      onChange={(key) => {
                        const chosen = parseConfigVersionKey(key);
                        if (!chosen) return;
                        setChosenConfig(chosen.configId);
                        set({ registry_version: chosen.version });
                      }}
                      isInvalid={Boolean(
                        problemOf("config") ||
                          refusalFor("config_id") ||
                          refusalFor("registry_version")
                      )}
                      describedBy={describe(
                        problemOf("config") && `${fieldId("config")}-problem`,
                        refusalFor("config_id") && errorId("config_id"),
                        refusalFor("registry_version") && errorId("registry_version")
                      )}
                    />
                  )}
                  {!fixedConfig && configs.data?.items.length === 0 && (
                    <p className="text-pretty text-subtle text-xs">
                      No configuration is registered. Register a configuration first, then create
                      its schedule.
                    </p>
                  )}
                  {!fixedConfig && configs.isError && !configs.data && (
                    <p className="text-pretty text-danger text-xs">
                      The configurations could not be loaded.
                    </p>
                  )}
                  {!fixedConfig && problemLine("config")}
                  {!fixedConfig && refusalBelow("registry_version")}
                  {refusalBelow(
                    "config_id",
                    configId && (
                      <p>
                        <Link
                          to="/schedules"
                          search={{ config: configId }}
                          className={LINK}
                          onClick={onClose}
                        >
                          View the schedule of this configuration
                        </Link>
                      </p>
                    )
                  )}
                </div>

                {fixedConfig && (
                  <div id={fieldId("config")} className="flex flex-col gap-1.5">
                    <FormSelect
                      label="Version"
                      className="sm:w-64"
                      placeholder={
                        versions.isPending ? "Loading versions…" : "No registered version"
                      }
                      value={version}
                      options={numbers.map((n) => ({
                        id: n,
                        label: n === newestVersion ? `Version ${n}, the latest` : `Version ${n}`,
                      }))}
                      onChange={(n) => set({ registry_version: n })}
                      isInvalid={Boolean(problemOf("config") || refusalFor("registry_version"))}
                      describedBy={describe(
                        problemOf("config") && `${fieldId("config")}-problem`,
                        refusalFor("registry_version") && errorId("registry_version")
                      )}
                    />
                    {versions.isError && !versions.data && (
                      <p className="text-pretty text-danger text-xs">
                        The versions of this configuration could not be loaded.
                      </p>
                    )}
                    {problemLine("config")}
                    {refusalBelow("registry_version")}
                  </div>
                )}
              </FormSection>

              <FormSection title="Cadence" hint="When runs start.">
                <div id={fieldId("cron")} className="flex flex-col gap-1.5">
                  <CadenceField
                    key={cadenceKey}
                    initialCron={opening?.cron ?? DEFAULT_CRON}
                    onChange={(cron) => set({ cron })}
                    preview={preview}
                    saveRefusal={refusalFor("cron")}
                    refusalId={errorId("cron")}
                  />
                </div>

                <div id={fieldId("timezone")} className="flex flex-col gap-1.5">
                  {zones ? (
                    <FormSelect
                      label="Time zone"
                      className="sm:w-80"
                      value={form.timezone}
                      options={zoneOptions ?? []}
                      searchable
                      noMatch="No time zone matches"
                      onChange={(timezone) => set({ timezone })}
                      isInvalid={Boolean(
                        problemOf("timezone") || timezoneRefusal || refusalFor("timezone")
                      )}
                      describedBy={describe(
                        zoneDescribedBy,
                        problemOf("timezone") && `${fieldId("timezone")}-problem`
                      )}
                    />
                  ) : (
                    <>
                      <label htmlFor={`${id}-zone`} className="font-medium text-foreground text-sm">
                        Time zone
                      </label>
                      <input
                        id={`${id}-zone`}
                        className={cn(INPUT, "font-mono sm:w-80")}
                        value={form.timezone}
                        onChange={(e) => set({ timezone: e.target.value })}
                        autoComplete="off"
                        autoCapitalize="none"
                        autoCorrect="off"
                        spellCheck={false}
                        aria-invalid={Boolean(problemOf("timezone"))}
                        aria-describedby={describe(
                          zoneDescribedBy,
                          problemOf("timezone") && `${fieldId("timezone")}-problem`
                        )}
                      />
                    </>
                  )}
                  {showZoneNotice && (
                    <p id={`${id}-zone-hint`} className="text-pretty text-subtle text-xs">
                      {UTC_NOTICE}
                    </p>
                  )}
                  {problemLine("timezone")}
                  {timezoneRefusal ? (
                    <FieldMessage id={errorId("timezone")}>
                      <p>{timezoneRefusal.detail ?? timezoneRefusal.title}</p>
                    </FieldMessage>
                  ) : (
                    refusalBelow("timezone")
                  )}
                </div>
              </FormSection>

              <FormSection
                title="Writing"
                hint="Where each run writes, and what it does in special cases."
              >
                <fieldset className="m-0 flex min-w-0 flex-col gap-2 border-0 p-0">
                  <legend className="mb-2 font-medium text-foreground text-sm">Write target</legend>
                  {WRITE_TARGETS.map((option) => {
                    const unavailable = option.value === "default-branch" && approvalRequired;
                    const hintId = `${id}-target-${option.value}`;
                    return (
                      <label
                        key={option.value}
                        className={cn(
                          "flex items-start gap-3 rounded-xl border px-4 py-2.5 has-checked:border-ring",
                          unavailable
                            ? "cursor-not-allowed opacity-70"
                            : "cursor-pointer hover:bg-content-muted"
                        )}
                      >
                        <input
                          type="radio"
                          name={`${id}-target`}
                          value={option.value}
                          className="mt-0.5 size-4 accent-cyan-700"
                          checked={form.write_target === option.value}
                          disabled={unavailable}
                          aria-describedby={describe(
                            hintId,
                            refusalFor("write_target") && errorId("write_target")
                          )}
                          onChange={() =>
                            set(
                              option.value === "branch-review"
                                ? { write_target: option.value, write_when_left_out: false }
                                : { write_target: option.value }
                            )
                          }
                        />
                        <span className="flex min-w-0 flex-col gap-0.5">
                          <span className="font-medium text-foreground text-sm">
                            {writeTargetLabel(option.value)}
                          </span>
                          <span
                            id={hintId}
                            className="text-pretty text-[13px] text-foreground-muted"
                          >
                            {unavailable ? APPROVAL_REASON : option.hint}
                          </span>
                        </span>
                      </label>
                    );
                  })}
                  {keptBranch && (
                    <p className="text-pretty text-foreground-muted text-xs">
                      The open review branch{" "}
                      <span className="font-mono text-foreground [overflow-wrap:anywhere]">
                        {keptBranch}
                      </span>{" "}
                      and its proposed change stay in Infrahub.
                    </p>
                  )}
                  {refusalBelow("write_target")}
                </fieldset>

                <div className="flex flex-col gap-1.5">
                  <Checkbox
                    isSelected={form.write_when_left_out}
                    isDisabled={form.write_target === "branch-review"}
                    onChange={(v) => set({ write_when_left_out: v })}
                    aria-describedby={`${id}-left-out-hint`}
                    className="items-start gap-2.5 text-foreground"
                  >
                    Write when records are left out
                  </Checkbox>
                  <p id={`${id}-left-out-hint`} className="text-pretty text-subtle text-xs">
                    {form.write_target === "branch-review"
                      ? LEFT_OUT_REASON
                      : "When a plan leaves out records, the run writes the other records. Off, the run stops after the plan and writes nothing."}
                  </p>
                  {refusalBelow("write_when_left_out")}
                </div>

                <FormSelect
                  label="When a run of this configuration is in progress"
                  className="sm:w-80"
                  value={form.overlap_policy}
                  options={(["reject", "queue"] as const).map((p) => ({
                    id: p,
                    label: overlapPolicyLabel(p),
                  }))}
                  onChange={(overlap_policy) => set({ overlap_policy })}
                />

                <Field
                  id={fieldId("late")}
                  label="Start late by at most, in minutes"
                  hint={`From ${LATE_START_MIN_MINUTES} to ${LATE_START_MAX_MINUTES} (7 days). When the worker runs again later than this after a missed start time, no run starts.${opening?.start_late_by_at_most_seconds === null ? " This schedule has no limit; leave the field empty to keep it without one." : ""}`}
                >
                  <input
                    id={fieldId("late")}
                    type="number"
                    inputMode="numeric"
                    min={LATE_START_MIN_MINUTES}
                    max={LATE_START_MAX_MINUTES}
                    className={cn(INPUT, "w-32 tabular-nums")}
                    value={lateText}
                    onChange={(e) => {
                      setLateText(e.target.value);
                      const seconds = lateStartSeconds(e.target.value);
                      // An emptied input keeps a schedule without a limit as it opened.
                      const kept =
                        opening?.start_late_by_at_most_seconds === null &&
                        e.target.value.trim() === "";
                      set(
                        seconds !== null
                          ? { start_late_by_at_most_seconds: seconds }
                          : kept
                            ? { start_late_by_at_most_seconds: null }
                            : {}
                      );
                    }}
                    aria-invalid={lateProblem !== undefined}
                    aria-describedby={describe(
                      `${fieldId("late")}-hint`,
                      problemOf("late") && `${fieldId("late")}-problem`
                    )}
                  />
                  {problemLine("late")}
                </Field>
              </FormSection>

              <div className="border-t pt-6">
                <ReasonField
                  id={`${id}-reason`}
                  value={reason}
                  onChange={(v) => {
                    setReason(v);
                    edited();
                  }}
                />
              </div>
            </fieldset>
          </div>

          {!narrow && (
            <aside
              aria-labelledby={`${id}-starts`}
              className="flex w-[22rem] shrink-0 flex-col gap-2 overflow-y-auto overscroll-contain border-l bg-content-muted px-5 py-6 max-lg:w-[18rem]"
            >
              <h3 id={`${id}-starts`} className="font-semibold text-sm tracking-tight">
                Next starts
              </h3>
              <CadenceTimeline cron={form.cron} preview={preview} count={PREVIEW_COUNT} />
            </aside>
          )}
        </div>

        {narrow && (
          <div className="shrink-0 border-t bg-content-muted">
            <button
              type="button"
              aria-expanded={previewOpen}
              aria-controls={`${id}-preview`}
              onClick={() => setPreviewOpen((o) => !o)}
              className={cn(
                "flex min-h-11 w-full items-center justify-between gap-3 px-4 py-2 text-left text-foreground-muted text-sm",
                FOCUS_INSET
              )}
            >
              <NextStartLine
                cron={form.cron}
                preview={preview}
                className="block min-w-0 flex-1 truncate"
              />
              <ChevronRightIcon
                className={cn(
                  "size-4 shrink-0 text-subtle transition-transform duration-150 ease-out motion-reduce:transition-none",
                  previewOpen ? "rotate-90" : "-rotate-90"
                )}
              />
              <span className="sr-only">
                {previewOpen ? "Hide the next starts" : "Show the next starts"}
              </span>
            </button>
            <div
              id={`${id}-preview`}
              hidden={!previewOpen}
              className="max-h-[50vh] overflow-y-auto overscroll-contain px-4 pb-4"
            >
              <CadenceTimeline cron={form.cron} preview={preview} count={PREVIEW_COUNT} />
            </div>
          </div>
        )}

        {footerMessages}

        <div className="flex shrink-0 flex-col gap-2 border-t bg-content-muted px-6 pt-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] max-sm:px-4 sm:flex-row sm:items-center sm:justify-end sm:gap-4">
          {changing && changeCount > 0 && (
            <output className="text-subtle text-sm tabular-nums max-sm:text-center">
              {changeCount} setting{changeCount === 1 ? "" : "s"} changed
            </output>
          )}
          <div className="flex flex-col-reverse gap-2 sm:flex-row">
            <Button
              variant="ghost"
              size="sm"
              className="max-sm:h-11 max-sm:w-full"
              isDisabled={isPending}
              onPress={close}
            >
              Cancel
            </Button>
            <Button
              ref={submitRef}
              type="submit"
              variant="primary"
              size="sm"
              className="max-sm:h-11 max-sm:w-full"
              isPending={isPending}
            >
              {submitLabel}
            </Button>
          </div>
        </div>
        <span className="sr-only" aria-live="polite">
          {isPending ? (changing ? "Saving the changes" : "Creating the schedule") : ""}
        </span>
      </form>
    </Modal>
  );
}
