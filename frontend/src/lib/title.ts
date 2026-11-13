import { useEffect } from "react";

/** Sets the browser tab title to the given parts, most specific first, followed by "Infrahub Sync". */
export function useDocumentTitle(...parts: (string | undefined | false | null)[]): void {
  const title = [...parts.filter(Boolean), "Infrahub Sync"].join(" · ");
  useEffect(() => {
    document.title = title;
  }, [title]);
}
