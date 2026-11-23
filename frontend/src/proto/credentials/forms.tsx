/* PROTOTYPE (spec 027, T074). The three versions of the create form and the change-value form. */
import { Button, Card } from "@infrahub/ui";
import { useEffect, useRef, useState } from "react";
import { cn } from "tailwind-variants";

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

/* ---------- 1. Dialog: one modal, the field choice as radio buttons above the inputs ---------- */

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
      error={<FormAnswer form={form} />}
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

/* ---------- 2. On the page: no modal, the list stays in view, the field choice is a segmented control ---------- */

function Segments({ form }: { form: CredentialForm }) {
  return (
    <fieldset className="m-0 min-w-0 border-0 p-0">
      <legend className={LEGEND}>Fields</legend>
      <div className="inline-flex max-w-full flex-wrap gap-1 rounded-xl border bg-content-muted p-1">
        {FIELD_SETS.map((set) => (
          <label
            key={set.id}
            className={cn(
              "flex min-h-8 pointer-coarse:min-h-11 cursor-pointer items-center rounded-lg px-3 text-sm transition-colors duration-150 ease-out",
              form.setId === set.id
                ? "bg-content text-foreground shadow-sm"
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

export function PanelForm({ credential, onClose }: FormProps) {
  const form = useCredentialForm(credential, onClose);
  const ref = useRef<HTMLDivElement>(null);
  // The panel opens above the list, which can be scrolled out of view.
  useEffect(() => {
    ref.current?.scrollIntoView({ block: "nearest" });
  }, []);
  return (
    <Card
      ref={ref}
      role="region"
      aria-labelledby="credential-panel-title"
      className="scroll-mt-20 bg-content bg-none"
    >
      <form
        className="flex flex-col"
        onSubmit={(e) => {
          e.preventDefault();
          if (!form.pending) void form.submit();
        }}
      >
        <div className="flex flex-col gap-1.5 px-5 pt-5 pb-4">
          <h2 id="credential-panel-title" className="text-balance font-semibold text-base">
            {formTitle(form)}
          </h2>
          <p className="max-w-[80ch] text-pretty text-foreground-muted text-sm">
            {formDescription(form)}
          </p>
        </div>
        <fieldset
          disabled={form.pending}
          className="m-0 grid min-w-0 grid-cols-1 gap-x-8 gap-y-4 border-0 px-5 pb-5 lg:grid-cols-2"
        >
          {!credential && (
            <div className="flex min-w-0 flex-col gap-4">
              <IdentityFields form={form} autoFocus />
            </div>
          )}
          <div className="flex min-w-0 flex-col gap-4">
            <Segments form={form} />
            <ValueFields form={form} autoFocus={Boolean(credential)} />
          </div>
        </fieldset>
        {(form.problem || Boolean(form.error)) && (
          <div className="px-5 pb-3">
            <FormAnswer form={form} />
          </div>
        )}
        <div className="flex flex-col gap-2 rounded-b-[inherit] border-t bg-content-muted px-5 py-3 sm:flex-row sm:justify-end">
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
    </Card>
  );
}

/* ---------- 3. Two steps: choose the fields from three described options, then type the values ---------- */

function OptionCards({ form }: { form: CredentialForm }) {
  return (
    <fieldset className="m-0 min-w-0 border-0 p-0">
      <legend className={LEGEND}>Which fields does the system read?</legend>
      <div className="flex flex-col gap-2">
        {FIELD_SETS.map((set) => (
          <label
            key={set.id}
            className={cn(
              "flex cursor-pointer items-start gap-3 rounded-xl border px-3.5 py-3 text-sm transition-[border-color,background-color] duration-150 ease-out",
              form.setId === set.id
                ? "border-ring bg-content-muted"
                : "border-input-border hover:bg-content-muted/60",
              RADIO_FOCUS
            )}
          >
            <input
              type="radio"
              name="credential-fields"
              className="mt-0.5 size-4 accent-accent outline-none"
              checked={form.setId === set.id}
              onChange={() => form.setSetId(set.id)}
            />
            <span className="flex min-w-0 flex-col gap-0.5">
              <span className="font-medium text-foreground">{set.label}</span>
              <span className="text-pretty text-subtle text-xs">{set.hint}</span>
            </span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}

export function StepsForm({ credential, onClose }: FormProps) {
  const form = useCredentialForm(credential, onClose);
  const [step, setStep] = useState<1 | 2>(1);
  const [nameProblem, setNameProblem] = useState(false);
  const title = `${formTitle(form)}: step ${step} of 2`;

  function next() {
    // The name is checked before the second step, where its field is not shown.
    if (!credential && !/^[a-z][a-z0-9-]{0,62}$/.test(form.name.trim())) {
      setNameProblem(true);
      document.getElementById("credential-name")?.focus();
      return;
    }
    setNameProblem(false);
    setStep(2);
  }

  return (
    <DialogFrame
      isOpen
      onOpenChange={(open) => !open && onClose()}
      title={title}
      ariaLabel={title}
      description={
        step === 1
          ? formDescription(form)
          : `${credential ? credential.name : form.name.trim()} · ${fieldSet(form.setId).label}`
      }
      error={
        step === 2 ? (
          <FormAnswer form={form} />
        ) : nameProblem ? (
          <p role="alert" className="text-danger text-sm">
            Give the credential a name that starts with a lowercase letter and holds lowercase
            letters, digits and hyphens: 63 characters at most.
          </p>
        ) : undefined
      }
      submitLabel={step === 1 ? "Continue" : submitLabel(form)}
      pendingLabel={credential ? "Changing the value" : "Creating the credential"}
      isPending={form.pending}
      onSubmit={step === 1 ? next : form.submit}
    >
      {step === 1 ? (
        <>
          {!credential && <IdentityFields form={form} autoFocus />}
          <OptionCards form={form} />
        </>
      ) : (
        <>
          <ValueFields form={form} autoFocus />
          <div>
            <Button size="xs" variant="outline" onPress={() => setStep(1)}>
              Back to the field choice
            </Button>
          </div>
        </>
      )}
    </DialogFrame>
  );
}
