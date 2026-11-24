import { Button } from "@infrahub/ui";
import { type DragEvent, type ReactNode, useId, useRef, useState } from "react";
import { cn } from "tailwind-variants";

import { InlineCode, Notice } from "#/components/page";

import { MESSAGES } from "./package-review";

/**
 * A region that takes one package file, dropped or chosen with the "choose a file" button, and
 * passes its text on. The button keeps the action usable without a mouse.
 */
export function PackageDropArea({
  action,
  disabled = false,
  message,
  onFile,
  onMessage,
}: {
  /** The words before the instructions: "Add a version". */
  action: string;
  /** Shown with "Registering needs an administrator token" when the token cannot register. */
  disabled?: boolean;
  /** A refusal to show under the area, such as a check the Sync API refused. */
  message?: ReactNode;
  onFile: (text: string, fileName: string) => void;
  /** Reports a refusal the area makes itself, or clears the message with `undefined`. */
  onMessage: (message: string | undefined) => void;
}) {
  const inputId = useId();
  const messageId = `${inputId}-message`;
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);

  const take = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    if (files.length > 1) {
      onMessage(MESSAGES.moreThanOneFile);
      return;
    }
    const file = files[0];
    try {
      const text = await file.text();
      onMessage(undefined);
      onFile(text, file.name);
    } catch {
      onMessage(MESSAGES.unreadableFile);
    }
  };

  // The browser opens a dropped file itself unless the drag events are taken, even when disabled.
  const onDragOver = (e: DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = disabled ? "none" : "copy";
    if (!disabled) setOver(true);
  };
  const onDragLeave = (e: DragEvent<HTMLElement>) => {
    if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setOver(false);
  };
  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setOver(false);
    if (!disabled) void take(e.dataTransfer.files);
  };

  return (
    <div className="flex flex-col gap-2">
      <section
        aria-label={action}
        aria-describedby={message ? messageId : undefined}
        onDragEnter={onDragOver}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
        className={cn(
          "flex flex-wrap items-center justify-center gap-x-2 gap-y-1 rounded-2xl border border-dashed px-4 py-5 text-center text-[13px] transition-colors duration-150 ease-out motion-reduce:transition-none",
          disabled
            ? "border-border bg-content-muted text-subtle"
            : "border-border-strong text-foreground-muted",
          over && "border-ring bg-content-muted"
        )}
      >
        <span>{action}: drop a package file here, or</span>
        <Button
          variant="outline"
          size="xs"
          isDisabled={disabled}
          onPress={() => input.current?.click()}
        >
          choose a file
        </Button>
        <input
          ref={input}
          id={inputId}
          type="file"
          accept=".yml,.yaml,.json,application/json,application/yaml,text/yaml"
          className="sr-only"
          tabIndex={-1}
          aria-hidden="true"
          disabled={disabled}
          onChange={(e) => {
            void take(e.target.files);
            // The same file can be chosen again after a refusal.
            e.target.value = "";
          }}
        />
        {disabled && (
          <p className="basis-full text-subtle text-xs">{MESSAGES.administratorRequired}</p>
        )}
      </section>
      {message && (
        <div id={messageId}>
          <Notice tone="danger" role="alert">
            <p>{typeof message === "string" ? <InlineCode text={message} /> : message}</p>
          </Notice>
        </div>
      )}
    </div>
  );
}
