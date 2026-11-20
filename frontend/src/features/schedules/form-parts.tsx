import {
  Autocomplete,
  Label,
  ListBox,
  Popover,
  Select,
  SelectItem,
  SelectList,
  SelectTrigger,
} from "@infrahub/ui";
import type { ReactNode } from "react";
import { cn } from "tailwind-variants";

import type { FieldRefusal } from "./form-rules";

export interface SelectOption<K extends string | number> {
  id: K;
  label: string;
  /** A second line in the list, such as an identifier; hidden in the closed select. */
  detail?: string;
}

const LIST_CLASS = "max-h-80 max-w-[min(32rem,calc(100vw-2rem))] bg-content";

/** The text a searchable list matches, case-insensitively, against what the person types. */
const optionText = (o: SelectOption<string | number>) =>
  o.detail ? `${o.label} ${o.detail}` : o.label;

/**
 * A labelled select; the value is one of the options' `id`. A searchable select opens with a
 * search field that narrows the list to the options containing the typed text.
 */
export function FormSelect<K extends string | number>({
  label,
  value,
  options,
  onChange,
  placeholder,
  isDisabled,
  describedBy,
  isInvalid,
  className,
  labelClassName = "font-medium text-foreground text-sm",
  searchable,
  noMatch = "No match",
}: {
  label: ReactNode;
  value: K | undefined;
  options: SelectOption<K>[];
  onChange: (value: K) => void;
  placeholder?: string;
  isDisabled?: boolean;
  describedBy?: string;
  isInvalid?: boolean;
  className?: string;
  labelClassName?: string;
  searchable?: boolean;
  /** The text of a searchable list when no option contains the typed text. */
  noMatch?: string;
}) {
  const items = options.map((o) => (
    <SelectItem key={String(o.id)} id={o.id} textValue={optionText(o)}>
      <span className="flex min-w-0 flex-col">
        <span className="truncate">{o.label}</span>
        {o.detail && (
          <span className="truncate font-mono text-subtle text-xs in-[button]:hidden">
            {o.detail}
          </span>
        )}
      </span>
    </SelectItem>
  ));
  return (
    <Select
      className={cn("flex min-w-0 flex-col gap-1.5", className)}
      selectedKey={value ?? null}
      placeholder={placeholder}
      isDisabled={isDisabled}
      isInvalid={isInvalid}
      aria-describedby={describedBy}
      onSelectionChange={(key) => {
        const option = options.find((o) => String(o.id) === String(key));
        if (option) onChange(option.id);
      }}
    >
      <Label className={labelClassName}>{label}</Label>
      <SelectTrigger size="md" />
      {searchable ? (
        <Popover width="min-trigger" className={LIST_CLASS}>
          <Autocomplete>
            <ListBox selectionMode="single" className="max-h-72" emptyMessage={noMatch}>
              {items}
            </ListBox>
          </Autocomplete>
        </Popover>
      ) : (
        <SelectList width="min-trigger" className={LIST_CLASS}>
          {items}
        </SelectList>
      )}
    </Select>
  );
}

/** A problem with a value, or a refusal of the Sync API, under the field it concerns. */
export function FieldMessage({ id, children }: { id: string; children: ReactNode }) {
  return (
    <div id={id} role="alert" className="flex flex-col gap-0.5 text-pretty text-danger text-xs">
      {children}
    </div>
  );
}

/** The words of a refusal under its field: the title, then the detail when there is one. */
export function RefusalText({
  refusal,
  children,
}: {
  refusal: FieldRefusal;
  children?: ReactNode;
}) {
  return (
    <>
      <p className="font-medium">{refusal.title}</p>
      {refusal.detail && <p>{refusal.detail}</p>}
      {children}
    </>
  );
}
