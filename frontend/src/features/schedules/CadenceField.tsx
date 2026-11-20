import { useQuery } from "@tanstack/react-query";
import { type ReactNode, useEffect, useId, useState } from "react";
import { cn } from "tailwind-variants";

import { nextStartsQuery } from "#/api/queries";
import { CODE_CHIP } from "#/components/page";
import {
  cadenceWords,
  cronFields,
  cronFromForm,
  DAY_NAMES,
  HOUR_INTERVALS,
  MINUTE_INTERVALS,
} from "#/domain/cadence";
import { INPUT } from "#/features/runs/form";

import {
  type CadenceDraft,
  type CadenceKind,
  draftOf,
  formOfDraft,
  KIND_LABELS,
} from "./cadence-draft";
import { FieldMessage, FormSelect, RefusalText } from "./form-parts";
import { type FieldRefusal, fieldRefusal } from "./form-rules";
import { NextStarts, type NextStartsRead } from "./NextStarts";

/** The wait after the last change before the preview is asked for (research R12). */
export const PREVIEW_DELAY_MS = 400;
/** The number of start times the preview lists. */
export const PREVIEW_COUNT = 5;

/** Monday first and Sunday last, as cron numbers them: 1 … 6, then 0. */
const WEEK = [1, 2, 3, 4, 5, 6, 0] as const;

const NOT_FIVE_FIELDS =
  "A cron expression has five fields: minute, hour, day of month, month, day of week.";

/** `value`, once it has not changed for `delay` milliseconds. */
function useDebounced<T>(value: T, delay: number): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setSettled(value), delay);
    return () => window.clearTimeout(timer);
  }, [value, delay]);
  return settled;
}

/** The preview of the next start times, as the form reads it. */
export interface CadencePreview {
  read: NextStartsRead;
  /** The time zone the answer is for, in which the starts are shown. */
  timezone: string;
  /** The values changed since the last request: the answer shown would be for older values. */
  waiting: boolean;
  /** A refusal of the cron expression or the time zone, shown under that field. */
  refusal: FieldRefusal | null;
}

/**
 * The next start times of the cron expression and the time zone entered, asked for 400 ms after
 * the last change. The query is keyed by both values, so only the answer to the latest values is
 * shown. `cron` is `null` while the helper's inputs cannot write an expression: nothing is asked.
 */
export function useCadencePreview(cron: string | null, timezone: string): CadencePreview {
  const wanted = (cron ?? "").trim();
  const settledCron = useDebounced(wanted, PREVIEW_DELAY_MS);
  const settledZone = useDebounced(timezone, PREVIEW_DELAY_MS);
  const read = useQuery(nextStartsQuery(settledCron, settledZone, PREVIEW_COUNT));
  const waiting = settledCron !== wanted || settledZone !== timezone;
  const refusal = read.data || waiting ? null : fieldRefusal(read.error);
  return {
    read,
    timezone: settledZone,
    waiting,
    refusal: refusal?.field === "cron" || refusal?.field === "timezone" ? refusal : null,
  };
}

/**
 * The cadence of a schedule: a helper with seven forms that writes the cron expression, and the
 * next start times from the Sync API. The form is chosen once, from `initialCron`, so a custom
 * expression that matches a form while the person types stays "Custom". `onChange` receives the
 * expression, or `null` while an input misses a value.
 */
export function CadenceField({
  initialCron,
  onChange,
  preview,
  saveRefusal,
  refusalId: givenRefusalId,
}: {
  initialCron: string;
  onChange: (cron: string | null) => void;
  preview: CadencePreview;
  /** The Sync API's refusal of the expression on the last save. */
  saveRefusal?: FieldRefusal | null;
  /** The id of the refusal shown under the cadence, so the form can bring it into view. */
  refusalId?: string;
}) {
  const id = useId();
  const [draft, setDraft] = useState<CadenceDraft>(() => draftOf(initialCron));
  const result = formOfDraft(draft);
  const cron = result.form ? cronFromForm(result.form) : null;

  const update = (patch: Partial<CadenceDraft>) => {
    const next = { ...draft, ...patch };
    setDraft(next);
    const built = formOfDraft(next);
    onChange(built.form ? cronFromForm(built.form) : null);
  };
  const choose = (kind: CadenceKind) => {
    // "Custom" starts from the expression the helper wrote, so the person edits it from there.
    update(
      kind === "custom" && cron !== null && draft.kind !== "custom"
        ? { kind, custom: cron }
        : { kind }
    );
  };

  const problemId = `${id}-problem`;
  const refusalId = givenRefusalId ?? `${id}-refusal`;
  const problem = (input: NonNullable<typeof result.input>) =>
    result.input === input ? problemId : undefined;
  const describe = (input: NonNullable<typeof result.input>) =>
    [problem(input), preview.refusal?.field === "cron" || saveRefusal ? refusalId : undefined]
      .filter(Boolean)
      .join(" ") || undefined;
  const problemLine = (input: NonNullable<typeof result.input>) =>
    result.input === input && <FieldMessage id={problemId}>{result.problem}</FieldMessage>;

  const minuteInput = (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={`${id}-minute`} className="font-medium text-foreground text-sm">
        Minute of the hour
      </label>
      <input
        id={`${id}-minute`}
        type="number"
        inputMode="numeric"
        min={0}
        max={59}
        className={cn(INPUT, "w-28 tabular-nums")}
        value={draft.minute}
        onChange={(e) => update({ minute: e.target.value })}
        aria-invalid={result.input === "minute"}
        aria-describedby={describe("minute")}
      />
      {problemLine("minute")}
    </div>
  );
  const timeInput = (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={`${id}-time`} className="font-medium text-foreground text-sm">
        Time
      </label>
      <input
        id={`${id}-time`}
        type="time"
        className={cn(INPUT, "w-36 tabular-nums")}
        value={draft.time}
        onChange={(e) => update({ time: e.target.value })}
        aria-invalid={result.input === "time"}
        aria-describedby={describe("time")}
      />
      {problemLine("time")}
    </div>
  );

  let inputs: ReactNode;
  switch (draft.kind) {
    case "minutes":
      inputs = (
        <FormSelect
          label="Interval"
          className="w-40"
          value={draft.everyMinutes}
          options={MINUTE_INTERVALS.map((n) => ({ id: n, label: `${n} minutes` }))}
          onChange={(everyMinutes) => update({ everyMinutes })}
        />
      );
      break;
    case "hourly":
      inputs = minuteInput;
      break;
    case "hours":
      inputs = (
        <>
          <FormSelect
            label="Interval"
            className="w-40"
            value={draft.everyHours}
            options={HOUR_INTERVALS.map((n) => ({ id: n, label: `${n} hours` }))}
            onChange={(everyHours) => update({ everyHours })}
          />
          {minuteInput}
        </>
      );
      break;
    case "daily":
      inputs = timeInput;
      break;
    case "weekly":
      inputs = (
        <>
          <fieldset
            className="m-0 flex min-w-0 flex-col gap-1.5 border-0 p-0"
            aria-describedby={describe("days")}
          >
            <legend className="mb-1.5 font-medium text-foreground text-sm">Days</legend>
            <div className="flex flex-wrap gap-x-4 gap-y-2">
              {WEEK.map((day) => (
                <label key={day} className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    className="size-4 accent-cyan-700"
                    checked={draft.days.includes(day)}
                    onChange={(e) =>
                      update({
                        days: e.target.checked
                          ? [...draft.days, day]
                          : draft.days.filter((d) => d !== day),
                      })
                    }
                  />
                  {DAY_NAMES[day]}
                </label>
              ))}
            </div>
            {problemLine("days")}
          </fieldset>
          {timeInput}
        </>
      );
      break;
    case "monthly":
      inputs = (
        <>
          <div className="flex flex-col gap-1.5">
            <label htmlFor={`${id}-day`} className="font-medium text-foreground text-sm">
              Day of the month
            </label>
            <input
              id={`${id}-day`}
              type="number"
              inputMode="numeric"
              min={1}
              max={31}
              className={cn(INPUT, "w-28 tabular-nums")}
              value={draft.day}
              onChange={(e) => update({ day: e.target.value })}
              aria-invalid={result.input === "day"}
              aria-describedby={describe("day")}
            />
            {problemLine("day")}
          </div>
          {timeInput}
        </>
      );
      break;
    case "custom": {
      const fields = cronFields(draft.custom);
      inputs = (
        <div className="flex flex-col gap-1.5">
          <label htmlFor={`${id}-custom`} className="font-medium text-foreground text-sm">
            Cron expression
          </label>
          <input
            id={`${id}-custom`}
            className={cn(INPUT, "font-mono")}
            value={draft.custom}
            onChange={(e) => update({ custom: e.target.value })}
            autoComplete="off"
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            aria-invalid={result.input === "custom" || preview.refusal?.field === "cron"}
            aria-describedby={[`${id}-fields`, describe("custom")].filter(Boolean).join(" ")}
          />
          {problemLine("custom")}
          <div id={`${id}-fields`}>
            {draft.custom.trim() === "" ? null : fields ? (
              <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-0.5 text-xs">
                {fields.map((f) => (
                  <div key={f.label} className="contents">
                    <dt className="text-subtle">{f.label}</dt>
                    <dd className="font-mono [overflow-wrap:anywhere]">{f.value}</dd>
                  </div>
                ))}
              </dl>
            ) : (
              <p className="text-pretty text-foreground-muted text-xs">{NOT_FIVE_FIELDS}</p>
            )}
          </div>
        </div>
      );
      break;
    }
  }

  const words = result.form ? cadenceWords(result.form) : null;
  const shownRefusal = preview.refusal?.field === "cron" ? preview.refusal : (saveRefusal ?? null);
  const showPreview = cron !== null && cronFields(cron) !== null && preview.refusal === null;
  const read: NextStartsRead = preview.waiting
    ? { data: undefined, error: null, isPending: true, fetchStatus: "fetching" }
    : preview.read;

  return (
    <div className="flex flex-col gap-3">
      <FormSelect
        label="Cadence"
        className="sm:w-64"
        value={draft.kind}
        options={(Object.keys(KIND_LABELS) as CadenceKind[]).map((k) => ({
          id: k,
          label: KIND_LABELS[k],
        }))}
        onChange={choose}
      />
      <div className="flex flex-wrap items-start gap-x-4 gap-y-3">{inputs}</div>
      {draft.kind !== "custom" && cron !== null && (
        <p className="flex flex-col gap-0.5 text-sm">
          {words && <span className="text-foreground">{words}</span>}
          <span className="text-subtle text-xs">
            Cron expression: <code className={CODE_CHIP}>{cron}</code>
          </span>
        </p>
      )}
      {shownRefusal && (
        <FieldMessage id={refusalId}>
          {preview.refusal?.field === "cron" ? (
            <p>{shownRefusal.detail ?? shownRefusal.title}</p>
          ) : (
            <RefusalText refusal={shownRefusal} />
          )}
        </FieldMessage>
      )}
      {showPreview && (
        <section aria-labelledby={`${id}-preview`} className="flex flex-col gap-2">
          <h3 id={`${id}-preview`} className="font-medium text-foreground text-sm">
            Next start times
          </h3>
          <NextStarts read={read} timezone={preview.timezone} count={PREVIEW_COUNT} />
        </section>
      )}
    </div>
  );
}
