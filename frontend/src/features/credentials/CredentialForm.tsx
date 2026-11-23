import { Button } from "@infrahub/ui";
import { useEffect, useRef, useState } from "react";
import { cn } from "tailwind-variants";

import {
  type CredentialFields,
  createCredential,
  setCredentialValue,
  useDescribeCredential,
  useIdempotencyKey,
  useInvalidateCredentials,
} from "#/api/actions";
import {
  CREDENTIAL_DESCRIPTION_MAX,
  CREDENTIAL_NAME_PATTERN,
  CREDENTIAL_VALUE_MAX,
  describeCredentialError,
} from "#/api/queries";
import type { Credential } from "#/api/types";
import { announce } from "#/components/announcer";
import { Notice } from "#/components/page";
import { FormProblem } from "#/features/accounts/shared";
import { DialogFrame, Field, finePointer, INPUT } from "#/features/runs/form";

import { FIELD_SETS, type FieldSetId, fieldSet, fieldSetOf } from "./fields";

/** The refusal of a credential request, next to the button that sent it. */
export function CredentialError({ error, id }: { error: unknown; id?: string }) {
  if (!error) return null;
  const d = describeCredentialError(error);
  return (
    <Notice tone="danger" role="alert" title={d.title} id={id}>
      {d.detail && <p className="text-pretty text-foreground-muted">{d.detail}</p>}
      {d.codeLine && <p className="font-mono text-subtle text-xs">{d.codeLine}</p>}
    </Notice>
  );
}

type ValueName = "username" | "password" | "token";
type Problem = { field: "name" | ValueName; text: string };

const EMPTY: Record<ValueName, string> = { username: "", password: "", token: "" };

const VALUE_LABEL: Record<ValueName, string> = {
  username: "Username",
  password: "Password",
  token: "Token",
};

/**
 * The state of the create form and of the change-value form. After each request, success or
 * refusal, the token, the password and the username are cleared; the name, the description and
 * the field choice stay, so a person can correct a refused request.
 */
function useCredentialForm(credential: Credential | undefined, onDone: () => void) {
  const invalidate = useInvalidateCredentials();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [setId, setSetId] = useState<FieldSetId>(
    credential ? fieldSetOf(credential.fields) : "token"
  );
  const [values, setValues] = useState(EMPTY);
  const [problem, setProblem] = useState<Problem>();
  const [error, setError] = useState<unknown>();
  const [pending, setPending] = useState(false);

  const touch = () => {
    setProblem(undefined);
    setError(undefined);
  };

  function check(): Problem | undefined {
    if (!credential) {
      if (!name.trim()) return { field: "name", text: "Give the credential a name." };
      if (!CREDENTIAL_NAME_PATTERN.test(name.trim())) {
        return {
          field: "name",
          text: "Start the name with a lowercase letter, then use lowercase letters, digits and hyphens: 63 characters at most.",
        };
      }
    }
    for (const field of fieldSet(setId).fields) {
      if (!values[field]) return { field, text: `Type the ${VALUE_LABEL[field].toLowerCase()}.` };
    }
    return undefined;
  }

  async function submit() {
    if (pending) return;
    setError(undefined);
    const found = check();
    setProblem(found);
    if (found) {
      document.getElementById(`credential-${found.field}`)?.focus();
      return;
    }
    const fields: CredentialFields = {};
    for (const field of fieldSet(setId).fields) fields[field] = values[field];
    // The values are cleared after every answer, so each request is a new intent with its own key.
    const key = crypto.randomUUID();
    setPending(true);
    const result = credential
      ? await setCredentialValue(credential.name, fields, key)
      : await createCredential(name, description, fields, key);
    // No value is kept after the request, whatever the answer.
    setValues(EMPTY);
    setPending(false);
    void invalidate();
    if (!result.ok) {
      setError(result.error);
      return;
    }
    announce(
      credential
        ? `Value of ${result.data.name} changed. It is now revision ${result.data.revision}.`
        : `Credential ${result.data.name} created.`
    );
    onDone();
  }

  return {
    credential,
    name,
    description,
    setId,
    values,
    problem,
    error,
    pending,
    submit,
    setName: (value: string) => {
      setName(value);
      touch();
    },
    setDescription: (value: string) => {
      setDescription(value);
      touch();
    },
    /** A new field choice empties the values, so a hidden field never holds one. */
    setSetId: (value: FieldSetId) => {
      setSetId(value);
      setValues(EMPTY);
      touch();
    },
    setValue: (field: ValueName, value: string) => {
      setValues((current) => ({ ...current, [field]: value }));
      touch();
    },
  };
}

type CredentialFormState = ReturnType<typeof useCredentialForm>;

const describedBy = (form: CredentialFormState, field: Problem["field"], hint?: string) =>
  [hint, form.problem?.field === field && "credential-problem"].filter(Boolean).join(" ") ||
  undefined;

function DescriptionField({
  value,
  onChange,
  hint,
  autoFocus,
}: {
  value: string;
  onChange: (value: string) => void;
  hint: string;
  autoFocus?: boolean;
}) {
  return (
    <Field id="credential-description" label="Description" hint={hint}>
      <input
        id="credential-description"
        className={INPUT}
        value={value}
        maxLength={CREDENTIAL_DESCRIPTION_MAX}
        onChange={(e) => onChange(e.target.value)}
        autoComplete="off"
        aria-describedby="credential-description-hint"
        // biome-ignore lint/a11y/noAutofocus: only with a fine pointer, in a form that just opened.
        autoFocus={autoFocus && finePointer()}
      />
    </Field>
  );
}

const RADIO_FOCUS =
  "has-focus-visible:outline-2 has-focus-visible:outline-offset-2 has-focus-visible:outline-ring";

/** The field choice on one line, so the row stays short. */
function FieldSetChoice({ form }: { form: CredentialFormState }) {
  return (
    <fieldset className="m-0 min-w-0 border-0 p-0">
      <legend className="mb-1.5 p-0 font-medium text-foreground text-sm">Fields</legend>
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

/** The inputs of the chosen field set. The token and the password are password inputs. */
function ValueFields({ form, autoFocus }: { form: CredentialFormState; autoFocus?: boolean }) {
  return (
    <>
      {fieldSet(form.setId).fields.map((field, index) => (
        <Field key={field} id={`credential-${field}`} label={VALUE_LABEL[field]}>
          <input
            id={`credential-${field}`}
            type={field === "username" ? "text" : "password"}
            className={cn(INPUT, "font-mono")}
            value={form.values[field]}
            maxLength={CREDENTIAL_VALUE_MAX}
            onChange={(e) => form.setValue(field, e.target.value)}
            // A stored credential of another system is not the sign-in of this page.
            autoComplete={field === "username" ? "off" : "new-password"}
            autoCapitalize="none"
            spellCheck={false}
            data-1p-ignore
            data-lpignore="true"
            aria-describedby={describedBy(form, field)}
            aria-invalid={form.problem?.field === field ? true : undefined}
            // biome-ignore lint/a11y/noAutofocus: only with a fine pointer, in a form that just opened.
            autoFocus={autoFocus && index === 0 && finePointer()}
          />
        </Field>
      ))}
    </>
  );
}

/**
 * "New credential" or "Change value", in place of the row it changes, or above the first row for
 * a new credential. Escape cancels.
 */
export function CredentialRowForm({
  credential,
  onClose,
}: {
  /** The credential whose value changes; none for a new credential. */
  credential?: Credential;
  onClose: () => void;
}) {
  const form = useCredentialForm(credential, onClose);
  const ref = useRef<HTMLFormElement>(null);
  useEffect(() => {
    ref.current?.scrollIntoView({ block: "nearest" });
  }, []);
  return (
    <form
      ref={ref}
      aria-labelledby="credential-form-title"
      className="flex scroll-mt-20 flex-col gap-4 border-ring border-l-2 px-4 py-4"
      onKeyDown={(e) => {
        if (e.key === "Escape" && !form.pending) {
          e.preventDefault();
          onClose();
        }
      }}
      onSubmit={(e) => {
        e.preventDefault();
        void form.submit();
      }}
    >
      <div className="flex flex-col gap-0.5">
        <h2 id="credential-form-title" className="text-balance font-semibold text-sm">
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
            ? `Revision ${credential.revision} becomes ${credential.revision + 1}. The new fields replace every stored field, and runs that start afterwards read the new value. Escape cancels.`
            : "The Sync service stores the value encrypted, and no page shows it after you save. Escape cancels."}
        </p>
      </div>
      <fieldset
        disabled={form.pending}
        className="m-0 grid min-w-0 grid-cols-1 gap-x-6 gap-y-4 border-0 p-0 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]"
      >
        {!credential && (
          <div className="flex min-w-0 flex-col gap-4">
            <Field
              id="credential-name"
              label="Name"
              hint="Lowercase letters, digits and hyphens, for example netbox-lab. The name cannot be changed later."
            >
              <input
                id="credential-name"
                className={cn(INPUT, "font-mono")}
                value={form.name}
                maxLength={63}
                onChange={(e) => form.setName(e.target.value)}
                autoComplete="off"
                autoCapitalize="none"
                spellCheck={false}
                data-1p-ignore
                data-lpignore="true"
                aria-describedby={describedBy(form, "name", "credential-name-hint")}
                aria-invalid={form.problem?.field === "name" ? true : undefined}
                // biome-ignore lint/a11y/noAutofocus: only with a fine pointer, in a form that just opened.
                autoFocus={finePointer()}
              />
            </Field>
            <DescriptionField
              value={form.description}
              onChange={form.setDescription}
              hint="Optional. Say which system and which account the value belongs to. Do not type the value here."
            />
          </div>
        )}
        <div className={cn("flex min-w-0 flex-col gap-4", credential && "md:col-span-2")}>
          <FieldSetChoice form={form} />
          <div className={cn("grid gap-4", credential && "md:grid-cols-2")}>
            <ValueFields form={form} autoFocus={Boolean(credential)} />
          </div>
        </div>
      </fieldset>
      {form.problem ? (
        <FormProblem id="credential-problem">{form.problem.text}</FormProblem>
      ) : (
        <CredentialError error={form.error} />
      )}
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
          {credential ? "Change value" : "Create credential"}
        </Button>
      </div>
      <span className="sr-only" aria-live="polite">
        {form.pending ? "Sending the request" : ""}
      </span>
    </form>
  );
}

/** "Edit description": the value and the revision of the credential stay the same. */
export function DescribeCredentialDialog({
  credential,
  onClose,
}: {
  credential: Credential;
  onClose: () => void;
}) {
  const describe = useDescribeCredential();
  const [key, renew] = useIdempotencyKey();
  const [description, setDescription] = useState(credential.description);
  const title = `Edit the description of ${credential.name}`;
  return (
    <DialogFrame
      isOpen
      onOpenChange={(open) => !open && onClose()}
      title={title}
      ariaLabel={title}
      description="The value and the revision of the credential stay the same."
      error={describe.error ? <CredentialError error={describe.error} /> : undefined}
      submitLabel="Save description"
      pendingLabel="Saving the description"
      isPending={describe.isPending}
      onSubmit={() =>
        describe.mutate(
          {
            params: { path: { name: credential.name }, header: { "Idempotency-Key": key } },
            body: { description: description.trim() },
          },
          {
            onSuccess: () => {
              announce(`Description of ${credential.name} changed.`);
              onClose();
            },
          }
        )
      }
    >
      <DescriptionField
        value={description}
        onChange={(value) => {
          setDescription(value);
          renew();
          describe.reset();
        }}
        hint={`Up to ${CREDENTIAL_DESCRIPTION_MAX} characters. Do not type the value here.`}
        autoFocus
      />
    </DialogFrame>
  );
}
