import type { RunLogLine } from "#/api/types";
import type { RunStep } from "#/domain/run-steps";

export type LogTone = "info" | "warning" | "error";

/** A level this page does not know reads as information: the Sync API can add levels. */
export const logTone = (level: string): LogTone =>
  level === "WARNING" ? "warning" : level === "ERROR" || level === "CRITICAL" ? "error" : "info";

export const countTone = (lines: RunLogLine[], tone: LogTone) =>
  lines.filter((l) => logTone(l.level) === tone).length;

/** Levels in the order they are offered; a level this list does not know comes first. */
const ORDER = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"];

/** "WARNING" as "Warning". */
export const levelName = (level: string) => level.charAt(0) + level.slice(1).toLowerCase();

/** The levels present in the lines, least severe first, with the number of lines of each. */
export function logLevels(lines: RunLogLine[]): { level: string; count: number }[] {
  const counts = new Map<string, number>();
  for (const l of lines) counts.set(l.level, (counts.get(l.level) ?? 0) + 1);
  return [...counts]
    .sort(([a], [b]) => ORDER.indexOf(a) - ORDER.indexOf(b))
    .map(([level, count]) => ({ level, count }));
}

/** The lines of each attempt, in the order the attempts ran. */
export function logAttempts(lines: RunLogLine[]): { attempt: number; lines: RunLogLine[] }[] {
  const out: { attempt: number; lines: RunLogLine[] }[] = [];
  for (const l of lines) {
    const last = out.at(-1);
    if (last?.attempt === l.attempt) last.lines.push(l);
    else out.push({ attempt: l.attempt, lines: [l] });
  }
  return out;
}

/** The lines as text, one each, for the clipboard. */
export const logText = (lines: RunLogLine[]) =>
  lines.map((l) => `${l.timestamp} ${l.level} ${l.source} ${l.message}`).join("\n");

/**
 * The stage whose lines a step shows, or null when it shows none. A step that never ran has no
 * lines, and a sync without review is one execution, whose lines sit under Plan.
 */
export const logStage = (s: RunStep): string | null =>
  s.shared && s.kind === "apply" ? null : (s.execs.at(-1)?.purpose ?? null);

/** The execution that wrote a line, for its result and its link to Prefect. */
export const logExecution = (s: RunStep, l: RunLogLine) =>
  s.execs.find((o) => o.purpose === l.stage && o.attempt === l.attempt);
