/* PROTOTYPE (spec 027, T074). The three versions of the create form and the change-value form. */
import { Button, Sheet } from "@infrahub/ui";
import { useEffect, useRef } from "react";
import { cn } from "tailwind-variants";

import { CODE_CHIP } from "#/components/classes";
import { DialogFrame } from "#/features/runs/form";

import { FIELD_SETS, fieldSet } from "./data";
import {
  type CredentialForm,
  FormAnswer,
  type FormProps,
  formDescription,
  formTitle,
  IdentityFields,
  submitLabel,
  useCredentialForm,
  ValueFields,
} from "./shared";

const LEGEND = "mb-1.5 p-0 font-medium text-foreground text-sm";
const RADIO_FOCUS =
  "has-focus-visible:outline-2 has-focus-visible:outline-offset-2 has-focus-visible:outline-ring";

/** Where a form version is placed: over the page, or inside the list in place of a row. */
export type FormPlacement = "overlay" | "row";

/* ---------- 1. Dialog: one modal, the field choice as radio buttons. Kept from the first round. ---------- */

function RadioRows({ form }: { form: CredentialForm }) {
  return (
    <fieldset className="m-0 min-w-0 border-0 p-0">
      <legend className={LEGEND}>Fields</legend>
      <div className="flex flex-col">
        {FIELD_SETS.map((set) => (
          <label
            key={set.id}
            className={cn(
              "flex min-h-8 pointer-coarse:min-h-11 cursor-pointer items-center gap-2.5 rounded-md text-sm",
              RADIO_FOCUS
            )}
          >
            <input
              type="radio"
              name="credential-fields"
              className="size-4 accent-accent outline-none"
              checked={form.setId === set.id}
              onChange={() => form.setSetId(set.id)}
            />
            {set.label}
          </label>
        ))}
      </div>
    </fieldset>
  );
}

export function DialogForm({ credential, onClose }: FormProps) {
  const form = useCredentialForm(credential, onClose);
  return (
    <DialogFrame
      isOpen
      onOpenChange={(open) => !open && onClose()}
      title={formTitle(form)}
      ariaLabel={formTitle(form)}
      description={formDescription(form)}
      error={form.problem || form.error ? <FormAnswer form={form} /> : undefined}
      submitLabel={submitLabel(form)}
      pendingLabel={credential ? "Changing the value" : "Creating the credential"}
      isPending={form.pending}
      onSubmit={form.submit}
    >
      {!credential && <IdentityFields form={form} autoFocus />}
      <RadioRows form={form} />
      <ValueFields form={form} autoFocus={Boolean(credential)} />
    </DialogFrame>
  );
}

/* ---------- 2. Side sheet: the list stays in view on the left; the sheet shows what will be stored ---------- */

function Segments({ form }: { form: CredentialForm }) {
  return (
    <fieldset className="m-0 min-w-0 border-0 p-0">
      <legend className={LEGEND}>Fields</legend>
      <div className="flex flex-col gap-1 rounded-xl border bg-content-muted p-1">
        {FIELD_SETS.map((set) => (
          <label
            key={set.id}
            className={cn(
              "flex min-h-8 pointer-coarse:min-h-11 cursor-pointer items-center rounded-lg px-3 text-sm transition-colors duration-150 ease-out motion-reduce:transition-none",
              form.setId === set.id
                ? "bg-content-strong text-foreground shadow-sm"
                : "text-foreground-muted hover:text-foreground",
              RADIO_FOCUS
            )}
          >
            <input
              type="radio"
              name="credential-fields"
              className="sr-only"
              checked={form.setId === set.id}
              onChange={() => form.setSetId(set.id)}
            />
            {set.label}
          </label>
        ))}
      </div>
      <p className="mt-1.5 text-pretty text-subtle text-xs">{fieldSet(form.setId).hint}</p>
    </fieldset>
  );
}

/** What the request will store, built from the form as the person types. Never a value. */
function Outcome({ form }: { form: CredentialForm }) {
  const name = form.credential?.name ?? form.name.trim();
  const revision = form.credential ? form.credential.revision + 1 : 1;
  const fields = fieldSet(form.setId).fields;
  return (
    <section
      aria-labelledby="credential-outcome"
      className="flex flex-col gap-2 rounded-xl border border-dashed px-3.5 py-3 text-[13px]"
    >
      <h3 id="credential-outcome" className="font-medium text-foreground text-xs">
        After you save
      </h3>
      <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1.5">
        <dt className="text-subtle">Stored fields</dt>
        <dd>
          {fields.join(", ")} <span className="text-subtle">(encrypted, never shown again)</span>
        </dd>
        <dt className="text-subtle">Revision</dt>
        <dd className="tabular-nums">
          {revision}
          {form.credential && (
            <span className="text-subtle"> (now {form.credential.revision})</span>
          )}
        </dd>
        <dt className="text-subtle">In a configuration</dt>
        <dd className="min-w-0">
          {name ? (
            <code className={CODE_CHIP}>credential: {name}</code>
          ) : (
            <span className="text-subtle">Type a name first</span>
          )}
        </dd>
      </dl>
      {form.credential && (
        <p className="text-pretty text-foreground-muted">
          Runs that start after the change read revision {revision}. The new fields replace every
          stored field.
        </p>
      )}
    </section>
  );
}

export function SheetForm({ credential, onClose }: FormProps) {
  const form = useCredentialForm(credential, onClose);
  return (
    <Sheet
      isOpen
      onOpenChange={(open) => !open && !form.pending && onClose()}
      aria-label={formTitle(form)}
      style={{ width: "min(26rem, calc(100vw - 16px))" }}
    >
      <form
        className="flex min-h-full flex-col"
        onSubmit={(e) => {
          e.preventDefault();
          if (!form.pending) void form.submit();
        }}
      >
        <div className="flex flex-col gap-1.5 px-2 pt-2 pb-4">
          <h2 className="text-balance font-semibold text-base">{formTitle(form)}</h2>
          <p className="text-pretty text-foreground-muted text-sm">{formDescription(form)}</p>
        </div>
        <fieldset
          disabled={form.pending}
          className="m-0 flex min-w-0 flex-col gap-4 border-0 px-2 pb-4"
        >
          {!credential && <IdentityFields form={form} autoFocus />}
          <Segments form={form} />
          <ValueFields form={form} autoFocus={Boolean(credential)} />
          <Outcome form={form} />
        </fieldset>
        {form.problem || form.error ? (
          <div className="px-2 pb-3">
            <FormAnswer form={form} />
          </div>
        ) : null}
        <div className="sticky bottom-0 mt-auto flex flex-col gap-2 border-t bg-secondary px-2 pt-3 pb-1 sm:flex-row sm:justify-end">
          <Button
            variant="ghost"
            size="sm"
            className="max-sm:h-11"
            isDisabled={form.pending}
            onPress={onClose}
          >
            Cancel
          </Button>
          <Button
            type="submit"
            variant="primary"
            size="sm"
            className="max-sm:h-11"
            isPending={form.pending}
          >
            {submitLabel(form)}
          </Button>
        </div>
        <span className="sr-only" aria-live="polite">
          {form.pending ? "Sending the request" : ""}
        </span>
      </form>
    </Sheet>
  );
}

/* ---------- 3. In the row: the form takes the place of the row it changes, or opens above the first row ---------- */

export function RowForm({ credential, onClose }: FormProps) {
  const form = useCredentialForm(credential, onClose);
  const ref = useRef<HTMLFormElement>(null);
  useEffect(() => {
    ref.current?.scrollIntoView({ block: "nearest" });
  }, []);
  return (
    <form
      ref={ref}
      aria-labelledby="credential-row-title"
      className="flex scroll-mt-20 flex-col gap-4 border-ring border-l-2 px-4 py-4"
      onKeyDown={(e) => {
        if (e.key === "Escape" && !form.pending) {
          e.preventDefault();
          onClose();
        }
      }}
      onSubmit={(e) => {
        e.preventDefault();
        if (!form.pending) void form.submit();
      }}
    >
      <div className="flex flex-col gap-0.5">
        <h2 id="credential-row-title" className="text-balance font-semibold text-sm">
          {credential ? (
            <>
              Change the value of{" "}
              <span className="font-mono [overflow-wrap:anywhere]">{credential.name}</span>
            </>
          ) : (
            "New credential"
          )}
        </h2>
        <p className="max-w-[90ch] text-pretty text-foreground-muted text-xs">
          {credential
            ? `Revision ${credential.revision} becomes ${credential.revision + 1}. The new fields replace every stored field. Escape cancels.`
            : "No page shows the value after you save. Escape cancels."}
        </p>
      </div>
      <fieldset
        disabled={form.pending}
        className="m-0 grid min-w-0 grid-cols-1 gap-x-6 gap-y-4 border-0 p-0 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]"
      >
        {!credential && (
          <div className="flex min-w-0 flex-col gap-4">
            <IdentityFields form={form} autoFocus />
          </div>
        )}
        <div className={cn("flex min-w-0 flex-col gap-4", credential && "md:col-span-2")}>
          <RadioInline form={form} />
          <div className={cn("grid gap-4", credential && "md:grid-cols-2")}>
            <ValueFields form={form} autoFocus={Boolean(credential)} />
          </div>
        </div>
      </fieldset>
      {form.problem || form.error ? <FormAnswer form={form} /> : null}
      <div className="flex flex-col gap-2 sm:flex-row sm:justify-end">
        <Button
          variant="ghost"
          size="sm"
          className="max-sm:h-11"
          isDisabled={form.pending}
          onPress={onClose}
        >
          Cancel
        </Button>
        <Button
          type="submit"
          variant="primary"
          size="sm"
          className="max-sm:h-11"
          isPending={form.pending}
        >
          {submitLabel(form)}
        </Button>
      </div>
      <span className="sr-only" aria-live="polite">
        {form.pending ? "Sending the request" : ""}
      </span>
    </form>
  );
}

/** The field choice on one line, so the row stays short. */
function RadioInline({ form }: { form: CredentialForm }) {
  return (
    <fieldset className="m-0 min-w-0 border-0 p-0">
      <legend className={LEGEND}>Fields</legend>
      <div className="flex flex-wrap gap-x-5 gap-y-1">
        {FIELD_SETS.map((set) => (
          <label
            key={set.id}
            className={cn(
              "flex min-h-8 pointer-coarse:min-h-11 cursor-pointer items-center gap-2 rounded-md text-sm",
              RADIO_FOCUS
            )}
          >
            <input
              type="radio"
              name="credential-fields"
              className="size-4 accent-accent outline-none"
              checked={form.setId === set.id}
              onChange={() => form.setSetId(set.id)}
            />
            {set.label}
          </label>
        ))}
      </div>
    </fieldset>
  );
}
