import type { Orchestration } from "#/api/types";
import { PLAN_ARTIFACT_ID, type RunStep } from "#/domain/run-steps";
import type { System } from "#/domain/systems";

type ApiRead = NonNullable<NonNullable<Orchestration["reads"]>["source"]>;

export type ReadState =
  | "waiting"
  | "reading"
  | "succeeded"
  | "failed"
  | "not_started"
  | "interrupted"
  /** The step completed and the Sync API returned no state for this side. */
  | "unrecorded";

const STATES = new Set<string>([
  "waiting",
  "reading",
  "succeeded",
  "failed",
  "not_started",
  "interrupted",
]);

export interface SideRead {
  side: "source" | "destination";
  label: "Source" | "Destination";
  /** The system's name, or the side's label until the configuration version is read. */
  system: string;
  state: ReadState;
  startedAt?: string;
  endedAt?: string;
  /** `full` or `incremental` today. */
  mode?: string;
  nodes: number;
  /** Largest kind first. */
  kinds: [string, number][];
  errorClass?: string;
}

export interface PlanReads {
  sides: [SideRead, SideRead];
  /**
   * From the end of the later read to the saved plan file: the comparison and the plan write. The
   * Sync API records neither, so this is the difference of two times it does record. No `to`
   * while the step still runs.
   */
  compare?: { from: string; to?: string };
  /** When the plan file was saved. */
  savedAt?: string;
}

function sideRead(
  side: SideRead["side"],
  read: ApiRead | null | undefined,
  system?: System
): SideRead {
  const label = side === "source" ? "Source" : "Destination";
  const kinds = Object.entries(read?.nodes_by_kind ?? {}).sort(
    (a, b) => b[1] - a[1] || a[0].localeCompare(b[0])
  );
  return {
    side,
    label,
    system: system?.label ?? label,
    state: read && STATES.has(read.state) ? (read.state as ReadState) : "unrecorded",
    startedAt: read?.started_at ?? undefined,
    endedAt: read?.ended_at ?? undefined,
    mode: read?.read_mode ?? undefined,
    nodes: kinds.reduce((total, [, n]) => total + n, 0),
    kinds,
    errorClass: read?.error_class ?? undefined,
  };
}

/** The reads of the Plan step's execution; undefined when the Sync API recorded none. */
export function planReads(
  s: RunStep,
  source?: System,
  destination?: System
): PlanReads | undefined {
  const reads = s.exec?.reads;
  if (s.kind !== "plan" || !reads) return undefined;
  const sides: [SideRead, SideRead] = [
    sideRead("source", reads.source, source),
    sideRead("destination", reads.destination, destination),
  ];
  const ends = sides.map((v) => v.endedAt).filter((t): t is string => Boolean(t));
  const readsEnded = sides.every((v) => v.state === "succeeded") ? ends.sort().at(-1) : undefined;
  const savedAt = s.files.find((a) => a.artifact_id === PLAN_ARTIFACT_ID)?.created_at;
  const active = s.state === "active";
  return {
    sides,
    compare:
      readsEnded && (active || (savedAt && savedAt >= readsEnded))
        ? { from: readsEnded, to: active ? undefined : savedAt }
        : undefined,
    savedAt,
  };
}
