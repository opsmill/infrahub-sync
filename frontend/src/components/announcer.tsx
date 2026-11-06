import { useEffect, useState } from "react";

const listeners = new Set<(message: string) => void>();

/** Reads a sentence to screen reader users through the one polite live region of the app. */
export function announce(message: string) {
  for (const listener of listeners) listener(message);
}

/**
 * The live region `announce` writes to. It stays mounted, because most screen readers do not
 * read a region that is inserted with its text already in it.
 */
export function LiveRegion() {
  const [message, setMessage] = useState("");
  useEffect(() => {
    let timer: number | undefined;
    const listener = (next: string) => {
      // Emptying the region first makes a repeated sentence a change that is read again.
      setMessage("");
      window.clearTimeout(timer);
      timer = window.setTimeout(() => setMessage(next), 100);
    };
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
      window.clearTimeout(timer);
    };
  }, []);
  return (
    <p aria-live="polite" className="sr-only">
      {message}
    </p>
  );
}
