import { useCallback, useSyncExternalStore } from "react";

const relativeFormat = new Intl.RelativeTimeFormat("en", { numeric: "auto" });

const UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ["year", 365 * 24 * 60 * 60],
  ["month", 30 * 24 * 60 * 60],
  ["week", 7 * 24 * 60 * 60],
  ["day", 24 * 60 * 60],
  ["hour", 60 * 60],
  ["minute", 60],
];

/** "3 days ago", "yesterday", "just now". */
export function formatRelative(iso: string, now = Date.now()): string {
  const seconds = (new Date(iso).getTime() - now) / 1000;
  // A time slightly ahead of this browser's clock comes from a server clock that differs a little.
  if (seconds > 0 && seconds <= 120) return "just now";
  for (const [unit, size] of UNITS) {
    if (Math.abs(seconds) >= size) return relativeFormat.format(Math.round(seconds / size), unit);
  }
  return "just now";
}

// `dateStyle` and `timeStyle` cannot be combined with `timeZoneName`, so the parts are listed.
const absoluteFormat = new Intl.DateTimeFormat("en", {
  year: "numeric",
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
  second: "2-digit",
  timeZoneName: "short",
});

/** The full date and time in the viewer's time zone, with the zone: "Nov 6, 2026, 1:13:36 AM GMT+1". */
export function formatAbsolute(iso: string): string {
  return absoluteFormat.format(new Date(iso));
}

const NBSP = " ";

/** "under 1 s", "4 s", "2 min 5 s", "1 h 12 min", with a no-break space between number and unit. */
export function formatDuration(fromIso: string, toIso: string | number): string {
  const ms =
    (typeof toIso === "number" ? toIso : new Date(toIso).getTime()) - new Date(fromIso).getTime();
  if (ms < 1000) return `under 1${NBSP}s`;
  const s = Math.round(ms / 1000);
  const part = (n: number, unit: string) => `${n}${NBSP}${unit}`;
  if (s < 60) return part(s, "s");
  const m = Math.floor(s / 60);
  if (m < 60) return s % 60 ? `${part(m, "min")} ${part(s % 60, "s")}` : part(m, "min");
  const h = Math.floor(m / 60);
  return m % 60 ? `${part(h, "h")} ${part(m % 60, "min")}` : part(h, "h");
}

/** `formatDuration`, with days past 24 hours: "2 d 3 h" reads faster than "51 h". */
export function formatSpan(fromIso: string, toIso: string | number): string {
  const ms =
    (typeof toIso === "number" ? toIso : new Date(toIso).getTime()) - new Date(fromIso).getTime();
  if (ms < 86_400_000) return formatDuration(fromIso, toIso);
  const d = Math.floor(ms / 86_400_000);
  const h = Math.floor((ms % 86_400_000) / 3_600_000);
  return h ? `${d}${NBSP}d ${h}${NBSP}h` : `${d}${NBSP}d`;
}

/* ---------- shared clock ---------- */

interface Clock {
  now: number;
  listeners: Set<() => void>;
  timer?: number;
}

/** One clock per interval, shared by every component that reads it. */
const clocks = new Map<number, Clock>();

function clockFor(intervalMs: number): Clock {
  let clock = clocks.get(intervalMs);
  if (!clock) {
    clock = { now: Date.now(), listeners: new Set() };
    clocks.set(intervalMs, clock);
  }
  return clock;
}

function tick(clock: Clock) {
  clock.now = Date.now();
  for (const listener of clock.listeners) listener();
}

function start(clock: Clock, intervalMs: number) {
  if (clock.timer === undefined) clock.timer = window.setInterval(() => tick(clock), intervalMs);
}

function stop(clock: Clock) {
  if (clock.timer === undefined) return;
  window.clearInterval(clock.timer);
  clock.timer = undefined;
}

let watchingVisibility = false;

// A hidden tab does not tick; it shows the current time again as soon as it is visible.
function onVisibilityChange() {
  for (const [intervalMs, clock] of clocks) {
    if (clock.listeners.size === 0) continue;
    if (document.hidden) {
      stop(clock);
    } else {
      tick(clock);
      start(clock, intervalMs);
    }
  }
}

function subscribe(intervalMs: number, listener: () => void) {
  const clock = clockFor(intervalMs);
  clock.listeners.add(listener);
  if (clock.listeners.size === 1) {
    clock.now = Date.now();
    if (!document.hidden) start(clock, intervalMs);
  }
  if (!watchingVisibility) {
    document.addEventListener("visibilitychange", onVisibilityChange);
    watchingVisibility = true;
  }
  return () => {
    clock.listeners.delete(listener);
    if (clock.listeners.size === 0) stop(clock);
  };
}

/** The current time, updated every `intervalMs` by one clock shared per interval, so relative times stay true on an open page. */
export function useNow(intervalMs = 30_000): number {
  const subscribeToClock = useCallback(
    (listener: () => void) => subscribe(intervalMs, listener),
    [intervalMs]
  );
  return useSyncExternalStore(subscribeToClock, () => clockFor(intervalMs).now);
}
