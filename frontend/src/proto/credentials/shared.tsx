/* PROTOTYPE (spec 027, T074). Parts that every version shares, so the versions differ only on their axis. */
import { type ReactNode, useState } from "react";
import { cn } from "tailwind-variants";

import type { CredentialFields } from "#/api/actions";
import {
  CREDENTIAL_DESCRIPTION_MAX,
  CREDENTIAL_NAME_PATTERN,
  CREDENTIAL_VALUE_MAX,
  type CredentialInUse,
  describeCredentialError,
} from "#/api/queries";
import type { Credential } from "#/api/types";
import { announce } from "#/components/announcer";
import { Notice } from "#/components/page";
import { FormProblem } from "#/features/accounts/shared";
import { DialogFrame, Field, finePointer, INPUT, LINK } from "#/features/runs/form";

import {
  createCredential,
  describeCredential,
  type FieldSetId,
  fieldSet,
  fieldSetOf,
  setCredentialValue,
} from "./data";

export type Action = "value" | "describe" | "delete";

export const ACTION_LABEL: Record<Action, string> = {
  value: "Change value",
  describe: "Edit description",
  delete: "Delete",
};

/** What a list version receives. */
export interface ListProps {
  credentials: Credential[];
  administrator: boolean;
  onAction: (action: Action, credential: Credential) => void;
  /**
   * A form or a confirmation shown inside the list, for the versions that work in the row: in place
   * of the row of `at`, or above the first row when `at` is null.
   */
  slot?: { at: string | null; node: ReactNode };
}

/** What a form version receives: a new credential, or a new value for an existing one. */
export interface FormProps {
  credential?: Credential;
  onClose: () => void;
}

/** What a delete version receives. */
export interface DeleteProps {
  credential: Credential;
  onClose: () => void;
  onDeleted: () => void;
}

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

/**
 * The address of a configuration page of the web interface. The prototype has no router, so this is
 * a plain link; it opens the real page, which needs a signed-in session and a Sync API.
 */
export const configHref = (configId: string) => `/configs/${encodeURIComponent(configId)}`;

/** The configurations and schedules that name a credential. Schedules have no page yet, so they are not links. */
export function InUseList({ inUse }: { inUse: CredentialInUse }) {
  return (
    <div className="flex flex-col gap-2 text-sm">
      {inUse.configurations.length > 0 && (
        <div>
          <p className="text-subtle text-xs">Configurations whose latest version names it</p>
          <ul className="mt-1 flex flex-col">
            {inUse.configurations.map((configId) => (
              <li key={configId} className="flex min-h-6 pointer-coarse:min-h-11 items-center">
                <a href={configHref(configId)} className={cn(LINK, "[overflow-wrap:anywhere]")}>
                  {configId}
                </a>
              </li>
            ))}
          </ul>
        </div>
      )}
      {inUse.schedules.length > 0 && (
        <div>
          <p className="text-subtle text-xs">Schedules that name it</p>
          <ul className="mt-1 flex flex-col">
            {inUse.schedules.map((schedule) => (
              <li
                key={schedule}
                className="flex min-h-6 items-center text-foreground [overflow-wrap:anywhere]"
              >
                {schedule}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

/* ---------- the form state every form version uses ---------- */

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
export function useCredentialForm(credential: Credential | undefined, onDone: () => void) {
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
      if (!values[field]) {
        return { field, text: `Type the ${VALUE_LABEL[field].toLowerCase()}.` };
      }
    }
    return undefined;
  }

  async function submit() {
    setError(undefined);
    const found = check();
    setProblem(found);
    if (found) {
      document.getElementById(`credential-${found.field}`)?.focus();
      return;
    }
    const fields: CredentialFields = {};
    for (const field of fieldSet(setId).fields) fields[field] = values[field];
    setPending(true);
    const result = credential
      ? await setCredentialValue(credential.name, fields)
      : await createCredential(name.trim(), description.trim(), fields);
    // No value is kept after the request, whatever the answer.
    setValues(EMPTY);
    setPending(false);
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

export type CredentialForm = ReturnType<typeof useCredentialForm>;

export const formTitle = (form: CredentialForm) =>
  form.credential ? `Change the value of ${form.credential.name}` : "New credential";

export const formDescription = (form: CredentialForm) =>
  form.credential
    ? `The new fields replace every stored field, and the credential becomes revision ${form.credential.revision + 1}. Runs that start afterwards read the new value. No page shows the stored value.`
    : "The Sync service stores the value encrypted. A configuration names the credential in its source or its destination. No page shows the stored value.";

export const submitLabel = (form: CredentialForm) =>
  form.credential ? "Change value" : "Create credential";

/** The problem the form found, or the refusal of the Sync API, for the place next to the button. */
export function FormAnswer({ form }: { form: CredentialForm }) {
  if (form.problem) return <FormProblem id="credential-problem">{form.problem.text}</FormProblem>;
  if (form.error) return <CredentialError error={form.error} />;
  return null;
}

const describedBy = (form: CredentialForm, field: Problem["field"], hint?: string) =>
  [hint, form.problem?.field === field && "credential-problem"].filter(Boolean).join(" ") ||
  undefined;

/** The name and the description of a new credential. */
export function IdentityFields({ form, autoFocus }: { form: CredentialForm; autoFocus?: boolean }) {
  return (
    <>
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
          autoFocus={autoFocus && finePointer()}
        />
      </Field>
      <DescriptionField
        value={form.description}
        onChange={form.setDescription}
        hint="Optional. Say which system and which account the value belongs to. Do not type the value here."
      />
    </>
  );
}

export function DescriptionField({
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

/** The inputs of the chosen field set. The token and the password are password inputs. */
export function ValueFields({ form, autoFocus }: { form: CredentialForm; autoFocus?: boolean }) {
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

/* ---------- "Edit description": one dialog, the same in every version ---------- */

export function DescribeDialog({
  credential,
  onClose,
}: {
  credential: Credential;
  onClose: () => void;
}) {
  const [description, setDescription] = useState(credential.description);
  const [error, setError] = useState<unknown>();
  const [pending, setPending] = useState(false);

  async function submit() {
    setError(undefined);
    setPending(true);
    const result = await describeCredential(credential.name, description.trim());
    setPending(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    announce(`Description of ${credential.name} changed.`);
    onClose();
  }

  return (
    <DialogFrame
      isOpen
      onOpenChange={(open) => !open && onClose()}
      title={`Edit the description of ${credential.name}`}
      ariaLabel={`Edit the description of ${credential.name}`}
      description="The value and the revision of the credential stay the same."
      error={error ? <CredentialError error={error} /> : undefined}
      submitLabel="Save description"
      pendingLabel="Saving the description"
      isPending={pending}
      onSubmit={submit}
    >
      <DescriptionField
        value={description}
        onChange={(value) => {
          setDescription(value);
          setError(undefined);
        }}
        hint={`Up to ${CREDENTIAL_DESCRIPTION_MAX} characters. Do not type the value here.`}
        autoFocus
      />
    </DialogFrame>
  );
}
