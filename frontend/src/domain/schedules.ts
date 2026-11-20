import type {
  ChangeScheduleRequest,
  CreateScheduleRequest,
  Principal,
  Schedule,
} from "#/api/types";

/* ---------- permissions ---------- */

export type ScheduleAction = "change" | "pause" | "resume" | "delete" | "take-over";

export interface ActionAccess {
  /** The page offers the action at all. */
  shown: boolean;
  /** The action can be used; when shown and not available, `unavailableReason` says why. */
  available: boolean;
  unavailableReason?: string;
}

export const OWNER_ONLY_TEXT = "Only the owner of this schedule or an administrator can do this.";

/**
 * What the signed-in account can do with a schedule (data-model "Permissions").
 * While `me` is undefined (`GET /me` has not answered), every owner action the state allows is
 * shown and available, and the Sync API decides. Take-over is shown only once `me` names an
 * administrator who does not own the schedule (FR-014).
 */
export function scheduleActions(
  me: Principal | undefined,
  schedule: Schedule
): Record<ScheduleAction, ActionAccess> {
  const live = schedule.state !== "deleted";
  const isOwner = me !== undefined && me.actor === schedule.owner;
  const mayAct = me === undefined || me.administrator || isOwner;
  const ownerAction = (shown: boolean): ActionAccess => {
    if (!shown) return { shown: false, available: false };
    return mayAct
      ? { shown: true, available: true }
      : { shown: true, available: false, unavailableReason: OWNER_ONLY_TEXT };
  };
  const takeOver = live && me !== undefined && me.administrator && !isOwner;
  return {
    change: ownerAction(live),
    pause: ownerAction(schedule.state === "active"),
    resume: ownerAction(schedule.state === "paused" || schedule.state === "stopped"),
    delete: ownerAction(live),
    "take-over": { shown: takeOver, available: takeOver },
  };
}

/* ---------- state and stop reason ---------- */

/** Set in `MAX_CONSECUTIVE_FAILURES` (infrahub_sync/product_store/models.py): a schedule stops after this many. */
export const MAX_CONSECUTIVE_FAILURES = 5;

/** What a stop reason means; an unknown reason is returned as given. */
export function stopReasonText(reason: string, failures = MAX_CONSECUTIVE_FAILURES): string {
  switch (reason) {
    case "owner-not-found":
      return "The account that owns this schedule is disabled or deleted.";
    case "approval-required":
      return "This schedule writes to the default branch, and this Sync API requires an approved plan before every write to the default branch.";
    case "repeated-failures":
      return `${failures} runs in a row failed, were interrupted or ended with an unknown outcome.`;
    default:
      return reason;
  }
}

/** A step the state panel offers: an action on the schedule, or the schedule's runs. */
export type PanelOffer = "resume" | "take-over" | "change" | "runs";

export interface StateView {
  /** The text of the state badge. */
  label: string;
  panel?: {
    text: string;
    /** What the person can do about it. */
    action?: string;
    offers: PanelOffer[];
  };
}

/** The badge and the explanation panel of a schedule's state (data-model "State and stop reason"). */
export function stateView(schedule: Schedule): StateView {
  switch (schedule.state) {
    case "active":
      return { label: "Active" };
    case "paused":
      return {
        label: "Paused",
        panel: { text: "This schedule starts no run until it is resumed.", offers: ["resume"] },
      };
    case "deleted":
      return {
        label: "Deleted",
        panel: { text: "This schedule was deleted. Its runs are kept.", offers: [] },
      };
    case "stopped":
      return { label: "Stopped", panel: stoppedPanel(schedule) };
    default:
      return { label: schedule.state };
  }
}

function stoppedPanel(schedule: Schedule): StateView["panel"] {
  const reason = schedule.state_reason;
  if (!reason) return undefined;
  const text = stopReasonText(reason, schedule.consecutive_failures);
  switch (reason) {
    case "owner-not-found":
      return {
        text,
        action:
          "An administrator takes the schedule over, or enables the owner's account and resumes the schedule. A deleted account cannot be enabled again.",
        offers: ["take-over"],
      };
    case "approval-required":
      return {
        text,
        action: "Change the write target to branch review. The schedule is then active again.",
        offers: ["change"],
      };
    case "repeated-failures":
      return {
        text,
        action: "Check the last runs of the schedule, then resume it.",
        offers: ["runs", "resume"],
      };
    default:
      return { text, offers: [] };
  }
}

/* ---------- labels ---------- */

export type WriteTarget = CreateScheduleRequest["write_target"];
export type OverlapPolicy = CreateScheduleRequest["overlap_policy"];

export function writeTargetLabel(target: string): string {
  if (target === "branch-review") return "Branch review";
  if (target === "default-branch") return "Default branch";
  return target;
}

/** What a start does while a run of the configuration is in progress. */
export function overlapPolicyLabel(policy: string): string {
  if (policy === "reject") return "Skip the start";
  if (policy === "queue") return "Wait for it";
  return policy;
}

const LATE_UNITS: [number, string][] = [
  [86_400, "day"],
  [3_600, "hour"],
  [60, "minute"],
  [1, "second"],
];

/** "Not set", "1 hour", "1 hour 30 minutes", "2 days". */
export function lateStartLabel(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "Not set";
  if (seconds === 0) return "0 seconds";
  const parts: string[] = [];
  let rest = seconds;
  for (const [size, unit] of LATE_UNITS) {
    const n = Math.floor(rest / size);
    if (n > 0) parts.push(`${n} ${unit}${n === 1 ? "" : "s"}`);
    rest -= n * size;
  }
  return parts.join(" ");
}

/* ---------- form values and requests ---------- */

/** The fields of the create and change forms that a change can send. */
export interface ScheduleFormValues {
  registry_version: number;
  cron: string;
  timezone: string;
  /** `string`, as the resource types it; narrowed when a request is built. */
  write_target: string;
  write_when_left_out: boolean;
  overlap_policy: string;
  start_late_by_at_most_seconds: number | null;
}

const FORM_FIELDS = [
  "registry_version",
  "cron",
  "timezone",
  "write_target",
  "write_when_left_out",
  "overlap_policy",
  "start_late_by_at_most_seconds",
] as const satisfies readonly (keyof ScheduleFormValues)[];

export function formValuesOf(schedule: Schedule): ScheduleFormValues {
  return {
    registry_version: schedule.registry_version,
    cron: schedule.cron,
    timezone: schedule.timezone,
    write_target: schedule.write_target,
    write_when_left_out: schedule.write_when_left_out,
    overlap_policy: schedule.overlap_policy,
    start_late_by_at_most_seconds: schedule.start_late_by_at_most_seconds ?? null,
  };
}

// The Sync API only stores these values; a value it does not know is refused on save.
const asWriteTarget = (value: string) => value as WriteTarget;
const asOverlapPolicy = (value: string) => value as OverlapPolicy;

/**
 * The change request: only the fields whose value differs from the values the form opened with,
 * plus the reason when not empty; `null` when no field differs (FR-018). It is never computed
 * against a later read of the schedule, so a field another person changed meanwhile is not sent
 * back with its old value.
 *
 * The Sync API reads `null` as "not changed", so clearing the late-start limit cannot be sent and
 * is left out of the request.
 */
export function changeRequest(
  opening: ScheduleFormValues,
  form: ScheduleFormValues,
  reason: string
): ChangeScheduleRequest | null {
  const request: ChangeScheduleRequest = {};
  for (const field of FORM_FIELDS) {
    if (form[field] === opening[field] || form[field] === null) continue;
    // A custom expression is sent trimmed, as on create; spaces alone are not a change.
    if (field === "cron" && form.cron.trim() === opening.cron.trim()) continue;
    switch (field) {
      case "write_target":
        request.write_target = asWriteTarget(form.write_target);
        break;
      case "overlap_policy":
        request.overlap_policy = asOverlapPolicy(form.overlap_policy);
        break;
      case "registry_version":
        request.registry_version = form.registry_version;
        break;
      case "cron":
        request.cron = form.cron.trim();
        break;
      case "timezone":
        request.timezone = form.timezone;
        break;
      case "write_when_left_out":
        request.write_when_left_out = form.write_when_left_out;
        break;
      case "start_late_by_at_most_seconds":
        request.start_late_by_at_most_seconds = form.start_late_by_at_most_seconds;
        break;
    }
  }
  if (Object.keys(request).length === 0) return null;
  const trimmed = reason.trim();
  if (trimmed) request.reason = trimmed;
  return request;
}

export function createRequest(
  form: ScheduleFormValues,
  name: string,
  configId: string,
  reason: string
): CreateScheduleRequest {
  const request: CreateScheduleRequest = {
    name: name.trim(),
    config_id: configId,
    registry_version: form.registry_version,
    cron: form.cron.trim(),
    timezone: form.timezone,
    write_target: asWriteTarget(form.write_target),
    write_when_left_out: form.write_when_left_out,
    overlap_policy: asOverlapPolicy(form.overlap_policy),
    start_late_by_at_most_seconds: form.start_late_by_at_most_seconds,
  };
  const trimmed = reason.trim();
  if (trimmed) request.reason = trimmed;
  return request;
}

/** Whether a later read of the schedule differs from the values the form opened with. */
export function differsFromOpening(opening: ScheduleFormValues, schedule: Schedule): boolean {
  const current = formValuesOf(schedule);
  return FORM_FIELDS.some((field) => current[field] !== opening[field]);
}

/**
 * The review branch that stays open in Infrahub when the form moves the write target to the
 * default branch (spec User Story 5, scenario 6); `null` when the form makes no such change or no
 * review branch is open.
 */
export function branchKeptOpen(
  opening: ScheduleFormValues,
  writeTarget: string,
  schedule: Schedule
): string | null {
  if (writeTarget !== "default-branch" || opening.write_target === "default-branch") return null;
  return schedule.open_branch?.branch ?? null;
}

/* ---------- refusals mapped to a field ---------- */

export type ScheduleField = keyof ScheduleFormValues | "name" | "config_id";

const ERROR_FIELDS: Record<string, ScheduleField> = {
  "schedule-cron-invalid": "cron",
  "schedule-timezone-invalid": "timezone",
  "schedule-name-in-use": "name",
  "schedule-exists-for-configuration": "config_id",
  "approval-required-for-schedule": "write_target",
  "schedule-branch-declared": "write_target",
  "schedule-left-out-not-applicable": "write_when_left_out",
  "configuration-version-not-found": "registry_version",
};

/** The form field a refusal belongs under; `null` for a refusal shown above the form's actions. */
export function scheduleErrorField(code: string): ScheduleField | null {
  return Object.hasOwn(ERROR_FIELDS, code) ? ERROR_FIELDS[code] : null;
}
