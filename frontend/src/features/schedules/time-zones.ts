/**
 * The time zones the schedule form offers (research R7): the IANA names the browser knows, which
 * the Sync API's time zone check accepts.
 */

/**
 * The time zones to choose from, sorted, with `UTC` and `stored` added when the browser's list
 * does not have them (`stored` can be an alias such as `Europe/Kiev`, which the list leaves out).
 * `null` when the browser cannot list its time zones; the form then shows a text field.
 */
export function timeZoneChoices(stored?: string): string[] | null {
  if (typeof Intl.supportedValuesOf !== "function") return null;
  const zones = new Set(Intl.supportedValuesOf("timeZone"));
  zones.add("UTC");
  if (stored) zones.add(stored);
  return [...zones].sort((a, b) => a.localeCompare(b, "en"));
}

/** The browser's time zone, or `UTC` when the browser does not give one. */
export function browserTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}
