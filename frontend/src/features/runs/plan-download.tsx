import { Button } from "@infrahub/ui";
import { useEffect, useSyncExternalStore } from "react";
import { cn } from "tailwind-variants";

import { isEnvelope, SyncApiUnavailable } from "#/api/errors";
import { describeError } from "#/api/queries";
import type { Plan, Run } from "#/api/types";
import { PLAN_ARTIFACT_ID } from "#/domain/run-steps";

/*
 * A run's saved plan holds every operation and can weigh over 100 MB, so the run page never loads
 * it on its own. It is downloaded once per run, when someone opens the changes or the Apply
 * dialog, and kept for the rest of the session: a saved plan never changes.
 */

export interface DownloadState {
  status: "idle" | "downloading" | "reading" | "ready" | "error";
  /** Bytes received so far. */
  loaded: number;
  /** Size of the plan in bytes, when known. */
  total: number;
  startedAt?: number;
  plan?: Plan;
  error?: unknown;
}

const IDLE: DownloadState = { status: "idle", loaded: 0, total: 0 };

class PlanDownload {
  state: DownloadState = IDLE;
  private listeners = new Set<() => void>();
  private abort?: AbortController;

  constructor(
    private runId: string,
    private expectedBytes: number
  ) {}

  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  private set(next: Partial<DownloadState>) {
    this.state = { ...this.state, ...next };
    for (const listener of this.listeners) listener();
  }

  start = () => {
    if (this.state.status !== "idle" && this.state.status !== "error") return;
    this.abort = new AbortController();
    this.set({
      status: "downloading",
      loaded: 0,
      total: this.expectedBytes,
      startedAt: performance.now(),
      error: undefined,
    });
    void this.run(this.abort.signal);
  };

  private async run(signal: AbortSignal) {
    try {
      const response = await fetch(`/api/runs/${encodeURIComponent(this.runId)}/plan`, { signal });
      if (!response.ok) {
        let body: unknown;
        try {
          body = await response.json();
        } catch {
          body = undefined;
        }
        throw isEnvelope(body) ? body : new SyncApiUnavailable(response.status);
      }
      // A compressed response states its compressed size, so the plan file's size is used then.
      const length = Number(response.headers.get("Content-Length"));
      const total =
        length > 0 && !response.headers.get("Content-Encoding") ? length : this.expectedBytes;
      this.set({ total });
      const chunks: Uint8Array[] = [];
      let loaded = 0;
      if (response.body) {
        const reader = response.body.getReader();
        let shown = 0;
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          chunks.push(value);
          loaded += value.length;
          const now = performance.now();
          if (now - shown > 80) {
            shown = now;
            this.set({ loaded });
          }
        }
      }
      this.set({ status: "reading", loaded });
      // Give the page one frame to show "Reading" before parsing blocks it.
      await new Promise((resolve) => requestAnimationFrame(resolve));
      const bytes = new Uint8Array(loaded);
      let at = 0;
      for (const chunk of chunks) {
        bytes.set(chunk, at);
        at += chunk.length;
      }
      const plan = JSON.parse(new TextDecoder().decode(bytes)) as Plan;
      this.set({ status: "ready", plan });
    } catch (error) {
      if (signal.aborted) return;
      this.set({ status: "error", error });
    }
  }
}

const downloads = new Map<string, PlanDownload>();

const NONE = new PlanDownload("", 0);

function downloadOf(run: Run | undefined): PlanDownload {
  if (!run) return NONE;
  let d = downloads.get(run.run_id);
  if (!d) {
    const size = run.artifact_refs.find((a) => a.artifact_id === PLAN_ARTIFACT_ID)?.size ?? 0;
    d = new PlanDownload(run.run_id, size);
    downloads.set(run.run_id, d);
  }
  return d;
}

/** The run's plan download; `start` begins it when it has not run yet. */
export function usePlanDownload(run: Run | undefined, { start = false } = {}) {
  const d = downloadOf(run);
  const state = useSyncExternalStore(d.subscribe, () => d.state);
  useEffect(() => {
    if (start) d.start();
  }, [start, d]);
  return { ...state, start: d.start };
}

/** The plan if this session has already downloaded it, without starting a download. */
/* ---------- progress ---------- */

function megabytes(n: number): string {
  const mb = n / 1e6;
  return mb < 10 ? `${mb.toFixed(1)} MB` : `${Math.round(mb)} MB`;
}

function secondsLeft(s: DownloadState): string | undefined {
  if (!s.startedAt || s.loaded === 0 || s.total <= s.loaded) return undefined;
  const ms = ((s.total - s.loaded) / s.loaded) * (performance.now() - s.startedAt);
  return ms < 10_000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms / 1000)} s`;
}

/** Where the download is, in words and as a bar; an error says why and offers to try again. */
export function DownloadProgress({
  state,
  retry,
  className,
}: {
  state: DownloadState;
  retry: () => void;
  className?: string;
}) {
  if (state.status === "error") {
    const d = describeError(state.error);
    return (
      <div className={cn("flex flex-col items-start gap-2 text-sm", className)} role="alert">
        <p className="text-foreground-muted">
          {d.status === 410
            ? "The saved plan of this run has expired. Create a new plan to review the changes again."
            : `The plan could not be downloaded: ${d.title}.`}
        </p>
        {d.codeLine && <p className="font-mono text-subtle text-xs">{d.codeLine}</p>}
        {d.status !== 410 && (
          <Button variant="outline" size="sm" onPress={retry}>
            Try again
          </Button>
        )}
      </div>
    );
  }
  const share = state.total ? Math.min(0.99, state.loaded / state.total) : 0;
  const left = secondsLeft(state);
  const text =
    state.status === "reading" || state.status === "ready"
      ? "Reading the plan"
      : state.total
        ? `Downloading the plan: ${megabytes(state.loaded)} of ${megabytes(state.total)}${left ? `, about ${left} left` : ""}`
        : `Downloading the plan: ${megabytes(state.loaded)}`;
  return (
    <div className={cn("flex w-full max-w-[420px] flex-col gap-1.5", className)}>
      <span className="text-foreground-muted text-xs tabular-nums">{text}</span>
      <span
        role="progressbar"
        aria-label="Downloading the plan"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round((state.status === "reading" ? 1 : share) * 100)}
        className="block h-1 overflow-hidden rounded-full bg-content-strong"
      >
        <span
          className={cn(
            "block h-full origin-left bg-accent transition-transform duration-150 ease-linear motion-reduce:transition-none",
            state.status === "reading" && "animate-pulse"
          )}
          style={{ transform: `scaleX(${state.status === "reading" ? 1 : share})` }}
        />
      </span>
    </div>
  );
}
