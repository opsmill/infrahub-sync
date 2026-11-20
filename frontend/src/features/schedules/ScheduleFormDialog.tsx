import { Checkbox } from "@infrahub/ui";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { type ReactNode, useEffect, useId, useMemo, useState } from "react";
import { cn } from "tailwind-variants";

import { useCreateSchedule, useIdempotencyKey } from "#/api/actions";
import {
  configScheduleQuery,
  configsQuery,
  describeScheduleError,
  meQuery,
  versionsQuery,
} from "#/api/queries";
import type { Schedule } from "#/api/types";
import { announce } from "#/components/announcer";
import {
  createRequest,
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
  lateStartSeconds,
  NAME_HINT,
  nameAccepted,
} from "./form-rules";
import { browserTimeZone, timeZoneChoices } from "./time-zones";

/** Where the form was opened: the create form, with or without a configuration chosen. */
export type ScheduleFormTarget = {
  mode: "create";
  /** The configuration of the page the form was opened from; the form offers a choice without it. */
  config?: { configId: string; name: string; latestVersion?: number | null };
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

/** Create a schedule: a name, a configuration version, a cadence with its preview, and its settings. */
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
  const [key, renewKey] = useIdempotencyKey();

  const [name, setName] = useState("");
  const [chosenConfig, setChosenConfig] = useState<string | undefined>(undefined);
  const [form, setForm] = useState<FormState>(createValues);
  const [lateText, setLateText] = useState(() =>
    lateStartMinutesText(form.start_late_by_at_most_seconds)
  );
  const [reason, setReason] = useState("");
  /** Set while the stored schedule is read after `orchestration-unavailable`. */
  const [finding, setFinding] = useState(false);

  const configId = target.config?.configId ?? chosenConfig;
  const configs = useQuery({ ...configsQuery(), enabled: !target.config });
  const versions = useQuery({ ...versionsQuery(configId ?? ""), enabled: Boolean(configId) });
  const me = useQuery(meQuery()).data;
  const approvalRequired = me?.approval_required === true;
  const browserZone = useMemo(browserTimeZone, []);
  const zones = useMemo(() => timeZoneChoices(browserZone), [browserZone]);
  const preview = useCadencePreview(form.cron, form.timezone);

  const numbers = useMemo(
    () => (versions.data?.items ?? []).map((v) => v.registry_version).sort((a, b) => b - a),
    [versions.data]
  );
  const summary = configs.data?.items.find((c) => c.config_id === configId);
  const latest = numbers[0] ?? target.config?.latestVersion ?? summary?.latest_version ?? undefined;
  const version = form.registry_version ?? latest;

  /** Any change by the person makes it a new request; the answer to the last one no longer applies. */
  const edited = () => {
    renewKey();
    create.reset();
  };
  const set = (patch: Partial<FormState>) => {
    setForm((f) => ({ ...f, ...patch }));
    edited();
  };

  const refusal = fieldRefusal(create.error);
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
  }, [id, refusedField, create.error]);

  const lateSeconds = lateStartSeconds(lateText);
  const problem: string | undefined = !name.trim()
    ? "Enter a name."
    : !nameAccepted(name)
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
              : lateSeconds === null
                ? "Enter how late a missed start may begin."
                : undefined;

  const open = (schedule: Schedule) => {
    onClose();
    announce("Schedule created");
    navigate({ to: "/schedules/$scheduleId", params: { scheduleId: schedule.schedule_id } });
  };

  const submit = () => {
    if (problem || !configId || version === undefined || form.cron === null) return;
    const values: ScheduleFormValues = { ...form, registry_version: version, cron: form.cron };
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

  const schedulesOfConfig = configId ? (
    <Link to="/schedules" search={{ config: configId }} className={LINK} onClick={onClose}>
      view the schedule of this configuration
    </Link>
  ) : (
    "view the Schedules page"
  );
  const submitLabel = "Create schedule";
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

  const timezoneRefusal = preview.refusal?.field === "timezone" ? preview.refusal : null;
  const zoneDescribedBy =
    [
      browserZone !== "UTC" ? `${id}-zone-hint` : undefined,
      timezoneRefusal || refusalFor("timezone") ? errorId("timezone") : undefined,
    ]
      .filter(Boolean)
      .join(" ") || undefined;

  return (
    <DialogFrame
      isOpen
      onOpenChange={(o) => !o && onClose()}
      title="Create a schedule"
      ariaLabel={
        target.config ? `Create a schedule for ${target.config.name}` : "Create a schedule"
      }
      description={
        target.config ? (
          <p>
            Starts sync runs of{" "}
            <span className="font-medium text-foreground [overflow-wrap:anywhere]">
              {target.config.name}
            </span>{" "}
            on a cadence.
          </p>
        ) : (
          <p>Starts sync runs of a configuration on a cadence.</p>
        )
      }
      error={
        create.error && !refusal ? (
          <MutationError
            error={create.error}
            describe={describeScheduleError}
            retryHint={resendHint(submitLabel, { check: schedulesOfConfig })}
          />
        ) : undefined
      }
      submitLabel={submitLabel}
      pendingLabel="Creating the schedule"
      isPending={create.isPending || finding}
      isDisabled={problem !== undefined}
      disabledReason={problem}
      onSubmit={submit}
    >
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

      <div className="flex flex-col gap-1.5">
        {target.config ? (
          <>
            <p className="font-medium text-foreground text-sm">Configuration</p>
            <p className="text-sm [overflow-wrap:anywhere]">{target.config.name}</p>
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
        {!target.config && configs.isError && !configs.data && (
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
              label: n === latest ? `Version ${n}, the latest` : `Version ${n}`,
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
        initialCron={DEFAULT_CRON}
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
        {browserZone !== "UTC" && (
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
        hint={`From ${LATE_START_MIN_MINUTES} to ${LATE_START_MAX_MINUTES} (7 days). When the worker runs again later than this after a missed start time, no run starts.`}
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
            set(seconds === null ? {} : { start_late_by_at_most_seconds: seconds });
          }}
          aria-invalid={lateSeconds === null}
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
