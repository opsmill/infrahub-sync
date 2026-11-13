import type { Run } from "#/api/types";

import { countsSentence, failureDetail, runCounts, runState } from "./run-state";

export const DAY_MS = 86_400_000;

/** How a past run ended, for drawing it. */
export type RunKind = "applied" | "unchanged" | "failed" | "review" | "running" | "other";

export function runKind(run: Run): RunKind {
  const state = runState(run);
  if (state === "failed" || state === "review" || state === "running") return state;
  if (state === "insync") {
    return run.phase === "applied" && run.outcome !== "no-change" ? "applied" : "unchanged";
  }
  return "other";
}

/** Runs by local day, the oldest day first; the last entry is today. Each day keeps the order of `runs`. */
export function runsByDay(runs: Run[], now: number, days: number): Run[][] {
  const startOfToday = new Date(now);
  startOfToday.setHours(0, 0, 0, 0);
  const end = startOfToday.getTime() + DAY_MS;
  const buckets: Run[][] = Array.from({ length: days }, () => []);
  for (const run of runs) {
    const back = Math.ceil((end - new Date(run.started_at).getTime()) / DAY_MS) - 1;
    if (back >= 0 && back < days) buckets[days - 1 - back].push(run);
  }
  return buckets;
}

/** The runs of each configuration, in the order of `runs`. */
export function runsByConfig(runs: Run[]): Map<string, Run[]> {
  const byConfig = new Map<string, Run[]>();
  for (const run of runs) {
    if (!run.config_id) continue;
    const list = byConfig.get(run.config_id);
    if (list) list.push(run);
    else byConfig.set(run.config_id, [run]);
  }
  return byConfig;
}

/** What a run did, in a sentence; an empty string when it recorded nothing to say. */
export function runOutcomeText(run: Run): string {
  const kind = runKind(run);
  if (kind === "failed") return failureDetail(run)?.sentence ?? "No error recorded";
  if (kind === "running") return "Still running";
  if (kind === "unchanged") return "Nothing to write";
  const counts = runCounts(run);
  return counts ? countsSentence(counts, run.phase === "applied" ? "done" : "plan") : "";
}
