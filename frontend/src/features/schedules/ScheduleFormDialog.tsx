import { Button, Checkbox } from "@infrahub/ui";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { type ReactNode, useEffect, useId, useMemo, useState } from "react";
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
import { Notice } from "#/components/page";
import {
  branchKeptOpen,
  changeRequest,
  createRequest,
  differsFromOpening,
  formValuesOf,
  overlapPolicyLabel,
  type ScheduleFormValues,
  writeTargetLabel,
} from "#/domain/schedules";
import {
  DialogFrame,
  Field,
  finePointer,
  INPUT,
  LINK,
  MutationError,
  ReasonField,
  resendHint,
} from "#/features/runs/form";

import { CadenceField, useCadencePreview } from "./CadenceField";
import { FieldMessage, FormSelect, RefusalText } from "./form-parts";
import {
  type FieldRefusal,
  fieldRefusal,
  LATE_START_MAX_MINUTES,
  LATE_START_MIN_MINUTES,
  lateStartMinutesText,
  lateStartProblem,
  lateStartSeconds,
  NAME_HINT,
  nameAccepted,
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
 * Create a schedule, or change one: a version, a cadence with its preview, and the settings. The
 * change form shows the name and the configuration as text and sends only the settings that
 * differ from the values it opened with (spec FR-018, FR-023).
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

  const configId = fixedConfig?.configId ?? chosenConfig;
  const configs = useQuery({ ...configsQuery(), enabled: !fixedConfig });
  const versions = useQuery({ ...versionsQuery(configId ?? ""), enabled: Boolean(configId) });
  // The page reads the schedule every 10 seconds; the form compares that read with its opening values.
  const polled = useQuery({
    ...scheduleQuery(changing?.schedule_id ?? ""),
    enabled: Boolean(changing),
  }).data;
  const latest = polled ?? changing;
  const me = useQuery(meQuery()).data;
  const approvalRequired = me?.approval_required === true;
  const browserZone = useMemo(browserTimeZone, []);
  const storedZone = changing?.timezone;
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
  const problem: string | undefined =
    !changing && !name.trim()
      ? "Enter a name."
      : !changing && !nameAccepted(name)
        ? "Change the name: it has a character the Sync API does not accept."
        : !configId
          ? "Choose a configuration."
          : version === undefined
            ? versions.isPending
              ? "Loading the versions of this configuration."
              : "This configuration has no registered version."
            : form.cron === null
              ? "Complete the cadence."
              : !form.timezone.trim()
                ? "Enter a time zone."
                : lateProblem;

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
    if (problem || !configId || version === undefined || form.cron === null) return;
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

  return (
    <DialogFrame
      isOpen
      onOpenChange={(o) => !o && onClose()}
      title={changing ? "Change the schedule" : "Create a schedule"}
      ariaLabel={
        changing
          ? `Change the schedule ${changing.name}`
          : fixedConfig
            ? `Create a schedule for ${fixedConfig.name}`
            : "Create a schedule"
      }
      description={
        changing ? (
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
        )
      }
      notice={changedNotice || undefined}
      error={
        nothingChanged ? (
          <Notice tone="neutral" role="status" title="Nothing changed">
            <p>Change a setting before you save. No request was sent.</p>
          </Notice>
        ) : mutation.error && !refusal ? (
          <MutationError
            error={mutation.error}
            describe={describeScheduleError}
            retryHint={resendHint(submitLabel, { check: schedulesOfConfig })}
          />
        ) : undefined
      }
      submitLabel={submitLabel}
      pendingLabel={changing ? "Saving the changes" : "Creating the schedule"}
      isPending={mutation.isPending || finding}
      isDisabled={problem !== undefined}
      disabledReason={problem}
      onSubmit={submit}
    >
      {changing ? (
        <div className="flex flex-col gap-1.5">
          <p className="font-medium text-foreground text-sm">Name</p>
          <p className="text-sm [overflow-wrap:anywhere]">{changing.name}</p>
        </div>
      ) : (
        <Field id={`${id}-name`} label="Name" hint={NAME_HINT}>
          <input
            id={`${id}-name`}
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
            aria-invalid={Boolean(refusalFor("name"))}
            aria-describedby={[`${id}-name-hint`, refusalFor("name") && errorId("name")]
              .filter(Boolean)
              .join(" ")}
          />
          {refusalBelow("name")}
        </Field>
      )}

      <div className="flex flex-col gap-1.5">
        {fixedConfig ? (
          <>
            <p className="font-medium text-foreground text-sm">Configuration</p>
            <p className="text-sm [overflow-wrap:anywhere]">{fixedConfig.name}</p>
          </>
        ) : (
          <FormSelect
            label="Configuration"
            placeholder={configs.isPending ? "Loading configurations…" : "Choose a configuration"}
            value={chosenConfig}
            options={(configs.data?.items ?? []).map((c) => ({
              id: c.config_id,
              label: c.name ?? c.config_id,
              detail: c.name && c.name !== c.config_id ? c.config_id : undefined,
            }))}
            onChange={(v) => {
              setChosenConfig(v);
              set({ registry_version: undefined });
            }}
            isInvalid={Boolean(refusalFor("config_id"))}
            describedBy={refusalFor("config_id") ? errorId("config_id") : undefined}
          />
        )}
        {!fixedConfig && configs.data?.items.length === 0 && (
          <p className="text-pretty text-subtle text-xs">
            No configuration is registered. Register a configuration first, then create its
            schedule.
          </p>
        )}
        {!fixedConfig && configs.isError && !configs.data && (
          <p className="text-pretty text-danger text-xs">The configurations could not be loaded.</p>
        )}
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

      {configId && (
        <div className="flex flex-col gap-1.5">
          <FormSelect
            label="Version"
            className="sm:w-64"
            placeholder={versions.isPending ? "Loading versions…" : "No registered version"}
            value={version}
            options={numbers.map((n) => ({
              id: n,
              label: n === newestVersion ? `Version ${n}, the latest` : `Version ${n}`,
            }))}
            onChange={(n) => set({ registry_version: n })}
            isInvalid={Boolean(refusalFor("registry_version"))}
            describedBy={refusalFor("registry_version") ? errorId("registry_version") : undefined}
          />
          {versions.isError && !versions.data && (
            <p className="text-pretty text-danger text-xs">
              The versions of this configuration could not be loaded.
            </p>
          )}
          {refusalBelow("registry_version")}
        </div>
      )}

      <CadenceField
        key={cadenceKey}
        initialCron={opening?.cron ?? DEFAULT_CRON}
        onChange={(cron) => set({ cron })}
        preview={preview}
        saveRefusal={refusalFor("cron")}
        refusalId={errorId("cron")}
      />

      <div className="flex flex-col gap-1.5">
        {zones ? (
          <FormSelect
            label="Time zone"
            className="sm:w-80"
            value={form.timezone}
            options={zones.map((z) => ({ id: z, label: z }))}
            onChange={(timezone) => set({ timezone })}
            isInvalid={Boolean(timezoneRefusal || refusalFor("timezone"))}
            describedBy={zoneDescribedBy}
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
              aria-describedby={zoneDescribedBy}
            />
          </>
        )}
        {showZoneNotice && (
          <p id={`${id}-zone-hint`} className="text-pretty text-subtle text-xs">
            {UTC_NOTICE}
          </p>
        )}
        {timezoneRefusal ? (
          <FieldMessage id={errorId("timezone")}>
            <p>{timezoneRefusal.detail ?? timezoneRefusal.title}</p>
          </FieldMessage>
        ) : (
          refusalBelow("timezone")
        )}
      </div>

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
                aria-describedby={[hintId, refusalFor("write_target") && errorId("write_target")]
                  .filter(Boolean)
                  .join(" ")}
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
                <span id={hintId} className="text-pretty text-[13px] text-foreground-muted">
                  {unavailable ? APPROVAL_REASON : option.hint}
                </span>
              </span>
            </label>
          );
        })}
        {keptBranch && (
          <p className="text-pretty text-foreground-muted text-xs">
            The open review branch{" "}
            <span className="font-mono text-foreground [overflow-wrap:anywhere]">{keptBranch}</span>{" "}
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
        id={`${id}-late`}
        label="How late a missed start may begin, in minutes"
        hint={`From ${LATE_START_MIN_MINUTES} to ${LATE_START_MAX_MINUTES} (7 days). When the worker runs again later than this after a missed start time, no run starts.${opening?.start_late_by_at_most_seconds === null ? " This schedule has no limit; leave the field empty to keep it without one." : ""}`}
      >
        <input
          id={`${id}-late`}
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
              opening?.start_late_by_at_most_seconds === null && e.target.value.trim() === "";
            set(
              seconds !== null
                ? { start_late_by_at_most_seconds: seconds }
                : kept
                  ? { start_late_by_at_most_seconds: null }
                  : {}
            );
          }}
          aria-invalid={lateProblem !== undefined}
          aria-describedby={`${id}-late-hint`}
        />
      </Field>

      <ReasonField
        id={`${id}-reason`}
        value={reason}
        onChange={(v) => {
          setReason(v);
          edited();
        }}
      />
    </DialogFrame>
  );
}
