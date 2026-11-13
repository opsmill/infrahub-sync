import { Button, Card, CardHeader, CheckboxCard, LinkButton, ScrollArea } from "@infrahub/ui";
import { useQuery } from "@tanstack/react-query";
import {
  type ReactNode,
  useDeferredValue,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { cn } from "tailwind-variants";

import { LOG_REFRESH_MS, runLogQuery } from "#/api/queries";
import type { Orchestration, Run, RunLogLine } from "#/api/types";
import { FOCUS } from "#/components/classes";
import {
  ChevronRightIcon,
  ExternalLinkIcon,
  InfoIcon,
  SearchIcon,
  StateIcon,
  WarningIcon,
  XIcon,
} from "#/components/icons";
import { CopyButton, expandChevron, InlineCode } from "#/components/page";
import { StateLabel } from "#/components/run-status";
import {
  countTone,
  type LogTone,
  levelName,
  logAttempts,
  logExecution,
  logLevels,
  logStage,
  logText,
  logTone,
} from "#/domain/run-log";
import { fmt, plural } from "#/domain/run-state";
import { executionResult, type RunStep } from "#/domain/run-steps";
import { formatAbsolute } from "#/lib/time";

import { clockMillis } from "./step-time";

/* ---------- a line ---------- */

/** The columns of a line: the time, the mark, the message. The time column is as wide as "00:00:00.000". */
const LINE_GRID = "grid grid-cols-[5.25rem_0.875rem_minmax(0,1fr)] gap-x-2";

/** A bar on the left edge of a warning or an error, so it is found while scrolling past. */
const EDGE: Record<LogTone, string> = {
  info: "",
  warning: "before:absolute before:inset-y-0 before:left-0 before:w-0.5 before:bg-warning-strong",
  error: "before:absolute before:inset-y-0 before:left-0 before:w-0.5 before:bg-danger-strong",
};

function LevelIcon({ level }: { level: string }) {
  const tone = logTone(level);
  if (tone === "warning") return <WarningIcon className="size-3.5 text-warning" />;
  if (tone === "error") return <StateIcon state="failed" className="size-3.5 text-danger" />;
  return <InfoIcon className="size-3.5 text-subtle" />;
}

/**
 * Time, a mark for a warning or an error, the message. The mark has its own column so it can be
 * scanned; the milliseconds are quieter than the second they belong to.
 */
function LogLine({ line }: { line: RunLogLine }) {
  const tone = logTone(line.level);
  const [second, ms] = clockMillis(line.timestamp);
  return (
    <li
      className={cn(
        LINE_GRID,
        "relative items-baseline px-4 py-[3px] text-[13px] leading-5 transition-colors duration-100 ease-out hover:bg-content-muted motion-reduce:transition-none [contain-intrinsic-size:auto_26px] [content-visibility:auto]",
        EDGE[tone]
      )}
    >
      <time
        dateTime={line.timestamp}
        title={formatAbsolute(line.timestamp)}
        className="text-subtle text-xs tabular-nums"
      >
        {second}
        <span className="text-subtle-muted">.{ms}</span>
      </time>
      <span className="self-start pt-[3px]">
        {tone !== "info" && (
          <>
            <LevelIcon level={line.level} />
            <span className="sr-only">{levelName(line.level)}: </span>
          </>
        )}
      </span>
      <span
        className={cn(
          "min-w-0 whitespace-pre-wrap tabular-nums [overflow-wrap:anywhere]",
          tone === "info" ? "text-foreground-muted" : "text-foreground"
        )}
      >
        <InlineCode text={line.message} />
      </span>
    </li>
  );
}

/** "34 lines", then the warnings and errors in their colours when there are some. */
function Tally({ lines }: { lines: RunLogLine[] }) {
  const warnings = countTone(lines, "warning");
  const errors = countTone(lines, "error");
  return (
    <span className="text-subtle tabular-nums">
      {plural(lines.length, "line", "lines")}
      {warnings > 0 && (
        <>
          {" · "}
          <span className="text-warning">{plural(warnings, "warning", "warnings")}</span>
        </>
      )}
      {errors > 0 && (
        <>
          {" · "}
          <span className="text-danger">{plural(errors, "error", "errors")}</span>
        </>
      )}
    </span>
  );
}

/* ---------- Prefect ---------- */

/** Opens the execution's flow run in Prefect, which holds every line, Sync's and Prefect's own. */
function PrefectLink({ o }: { o?: Orchestration }) {
  if (!o?.prefect_flow_run_url) return null;
  return (
    <LinkButton
      variant="ghost"
      size="xs"
      href={o.prefect_flow_run_url}
      target="_blank"
      rel="noreferrer"
      className="text-foreground-muted text-xs pointer-coarse:h-11"
    >
      Open in Prefect
      <ExternalLinkIcon />
      <span className="sr-only">(opens in a new tab)</span>
    </LinkButton>
  );
}

/* ---------- the lines ---------- */

/**
 * Scrolls to the last line. Lines off screen are sized by estimate until they are drawn, so the
 * end moves as they are; a few frames settle it.
 */
function toEnd(el: HTMLElement) {
  let frames = 0;
  const tick = () => {
    el.scrollTop = el.scrollHeight;
    if (++frames < 6) requestAnimationFrame(tick);
  };
  tick();
}

/** The scrolling element of the design system's scroll area, which takes no props for it. */
const VIEWPORT =
  "[&>[data-radix-scroll-area-viewport]]:max-h-[min(28rem,60vh)] [&>[data-radix-scroll-area-viewport]]:overscroll-contain [&>[data-radix-scroll-area-viewport]]:outline-none [&>[data-radix-scroll-area-viewport]]:focus-visible:ring-2 [&>[data-radix-scroll-area-viewport]]:focus-visible:ring-ring-halo [&>[data-radix-scroll-area-viewport]]:focus-visible:ring-inset";

/**
 * The area the lines scroll in. It opens at the last line when `atEnd`, and while the step runs it
 * stays on the last line unless the reader scrolled up.
 */
function LogScroller({
  live,
  atEnd,
  size,
  children,
}: {
  live: boolean;
  atEnd: boolean;
  /** Changes when lines are added. */
  size: number;
  children: ReactNode;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [pinned, setPinned] = useState(true);
  const follow = live || atEnd;
  const opened = useRef(false);

  // Runs when lines are added, not when the reader scrolls.
  useLayoutEffect(() => {
    const el = box.current;
    if (!el || !follow) return;
    if (!opened.current || (live && pinned)) toEnd(el);
    opened.current = size > 0;
  }, [size, follow, live]);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    // A scrolling area must be reachable with the keyboard.
    el.tabIndex = 0;
    el.setAttribute("role", "log");
    el.setAttribute("aria-label", "Lines of the log");
    el.setAttribute("aria-live", "off");
    const onScroll = () => setPinned(el.scrollHeight - el.scrollTop - el.clientHeight < 32);
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, []);

  return (
    <div className="relative">
      <ScrollArea ref={box} className={VIEWPORT}>
        {children}
      </ScrollArea>
      {live && !pinned && (
        <Button
          variant="outline"
          size="xs"
          className="absolute right-3 bottom-3 shadow-md"
          onPress={() => {
            if (box.current) toEnd(box.current);
          }}
        >
          Go to the last line
        </Button>
      )}
    </div>
  );
}

/** The lines, under a heading per attempt when the step ran more than once. */
function Lines({
  lines,
  attempts,
  step,
}: {
  lines: RunLogLine[];
  attempts: number;
  step: RunStep;
}) {
  return (
    <>
      {logAttempts(lines).map((group) => {
        const o = logExecution(step, group.lines[0]);
        const result = o && executionResult(o);
        return (
          <section
            key={group.attempt}
            aria-label={`Attempt ${group.attempt}`}
            className="border-t first:border-t-0"
          >
            {attempts > 1 && (
              <div className="sticky top-0 z-10 flex min-h-9 flex-wrap items-center gap-x-3 border-b bg-card px-4 py-1 text-xs">
                <h4 className="font-medium">Attempt {group.attempt}</h4>
                {result && (
                  <StateLabel quiet state={result.state} label={result.label} className="gap-1" />
                )}
                <Tally lines={group.lines} />
                <span className="ml-auto">
                  <PrefectLink o={o} />
                </span>
              </div>
            )}
            <ol className="py-1.5">
              {group.lines.map((l, i) => (
                <LogLine key={`${l.timestamp}-${i}`} line={l} />
              ))}
            </ol>
          </section>
        );
      })}
    </>
  );
}

/* ---------- the card ---------- */

type LogStatus = "loading" | "ready" | "expired" | "unavailable";

interface View {
  /** The levels switched off; every level is shown until the reader switches one off. */
  hidden: Set<string>;
  setHidden: (next: Set<string>) => void;
  query: string;
  setQuery: (next: string) => void;
}

const PULSE = "animate-pulse rounded bg-content-strong motion-reduce:animate-none";
const PLACEHOLDER = ["w-2/3", "w-1/2", "w-5/6", "w-3/5", "w-3/4"];
const MESSAGE =
  "flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-4 text-[13px] text-foreground-muted";

/** The card of an open log: the tools, then the lines or what stands in for them. */
function LogCard({
  id,
  status,
  lines,
  live,
  onRetry,
  step,
  view,
}: {
  id: string;
  status: LogStatus;
  lines: RunLogLine[];
  /** The step still runs: new lines can come. */
  live: boolean;
  onRetry: () => void;
  step: RunStep;
  view: View;
}) {
  const { hidden, setHidden, query, setQuery } = view;
  // Typing stays quick on a long log: the lines are filtered after the field has updated.
  const q = useDeferredValue(query).trim().toLowerCase();
  const lowered = useMemo(() => lines.map((l) => l.message.toLowerCase()), [lines]);
  const levels = useMemo(() => logLevels(lines), [lines]);
  const shown = useMemo(
    () => lines.filter((l, i) => !hidden.has(l.level) && (q === "" || lowered[i].includes(q))),
    [lines, lowered, hidden, q]
  );
  const ready = status === "ready";
  const filtered = shown.length !== lines.length;
  const attempts = logAttempts(lines).length;
  const show = (level: string, on: boolean) => {
    const next = new Set(hidden);
    if (on) next.delete(level);
    else next.add(level);
    setHidden(next);
  };
  return (
    <Card id={id} className="overflow-hidden">
      {(status === "loading" || (ready && lines.length > 0)) && (
        <CardHeader className="flex min-h-11 flex-wrap items-center gap-x-2 gap-y-2 font-normal tracking-normal">
          {ready ? (
            <fieldset className="flex flex-wrap items-center gap-1.5">
              <legend className="sr-only">Levels shown</legend>
              {levels.map(({ level, count }) => (
                <CheckboxCard
                  key={level}
                  isSelected={!hidden.has(level)}
                  onChange={(on) => show(level, on)}
                  className="h-7 gap-1.5 px-2 py-0 text-xs pointer-coarse:h-11"
                >
                  <span className="inline-flex items-center gap-1.5 align-middle">
                    <LevelIcon level={level} />
                    {levelName(level)}
                    <span className="text-subtle tabular-nums">{fmt(count)}</span>
                  </span>
                </CheckboxCard>
              ))}
            </fieldset>
          ) : (
            <span aria-hidden="true" className={cn(PULSE, "h-7 w-24 rounded-lg")} />
          )}
          {/* The second row on a phone, where it takes the full width. */}
          <label className="relative order-3 flex h-7 min-w-0 basis-full items-center pointer-coarse:h-11 sm:order-none sm:basis-56">
            <SearchIcon className="pointer-events-none absolute left-2 size-3.5 text-subtle-muted" />
            <span className="sr-only">Search the log of {step.name}</span>
            <input
              type="search"
              value={query}
              disabled={!ready}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Escape" && query) {
                  e.preventDefault();
                  setQuery("");
                }
              }}
              placeholder="Search the log"
              autoComplete="off"
              spellCheck={false}
              className="size-full rounded-lg border border-input-border bg-input pr-7 pl-7 text-base shadow-input transition-[border-color,box-shadow] duration-150 ease-out placeholder:text-subtle-muted focus-visible:border-ring focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring-halo disabled:opacity-60 motion-reduce:transition-none md:text-[13px] [&::-webkit-search-cancel-button]:hidden"
            />
            {query && (
              <button
                type="button"
                aria-label="Clear the search"
                onClick={() => setQuery("")}
                className={cn(
                  "absolute right-1 flex size-5 cursor-pointer items-center justify-center rounded-md text-subtle before:absolute before:-inset-1.5 hover:bg-highlight hover:text-foreground",
                  FOCUS
                )}
              >
                <XIcon className="size-3.5" />
              </button>
            )}
          </label>
          {ready && filtered && shown.length > 0 && (
            <output className="order-4 text-subtle text-xs tabular-nums sm:order-none">
              {fmt(shown.length)} of {plural(lines.length, "line", "lines")}
            </output>
          )}
          {ready && (
            <span className="ml-auto flex items-center gap-0.5">
              {attempts === 1 && <PrefectLink o={logExecution(step, lines[0])} />}
              <CopyButton
                value={logText(shown)}
                label={
                  filtered
                    ? `Copy the ${plural(shown.length, "line", "lines")} shown`
                    : "Copy the log"
                }
              />
            </span>
          )}
        </CardHeader>
      )}
      {status === "loading" && (
        <output aria-label="Loading the log" className="flex flex-col gap-3 px-4 py-3.5">
          {PLACEHOLDER.map((w) => (
            <span key={w} className={cn(PULSE, "h-2.5", w)} />
          ))}
        </output>
      )}
      {status === "expired" && (
        <p className={cn(MESSAGE, "text-pretty")}>
          The log of this step has expired: the Sync service no longer holds its lines.
        </p>
      )}
      {status === "unavailable" && (
        <div className={MESSAGE}>
          The log of this step cannot be read now.
          <Button variant="outline" size="xxs" onPress={onRetry} className="pointer-coarse:h-11">
            Try again
          </Button>
        </div>
      )}
      {ready && lines.length === 0 && (
        <p className={MESSAGE}>
          {live
            ? "No lines yet. They appear here as Sync writes them."
            : "Sync wrote no line during this step."}
        </p>
      )}
      {ready && lines.length > 0 && shown.length === 0 && (
        <div className={MESSAGE}>
          {hidden.size === levels.length ? (
            <>
              No level is selected.
              <Button variant="outline" size="xxs" onPress={() => setHidden(new Set())}>
                Show every level
              </Button>
            </>
          ) : (
            <>
              No line{hidden.size > 0 && " at the selected levels"} contains “{query.trim()}”.
              <Button variant="outline" size="xxs" onPress={() => setQuery("")}>
                Clear the search
              </Button>
            </>
          )}
        </div>
      )}
      {ready && shown.length > 0 && (
        <LogScroller
          live={live}
          atEnd={countTone(lines, "error") > 0 && !filtered}
          size={shown.length}
        >
          <Lines lines={shown} attempts={attempts} step={step} />
        </LogScroller>
      )}
    </Card>
  );
}

/* ---------- the log of a step ---------- */

const TOGGLE =
  "relative -ml-1 inline-flex min-h-5 cursor-pointer items-center gap-1.5 rounded-md px-1 text-foreground-muted transition-colors duration-150 ease-out before:absolute before:inset-x-0 before:-inset-y-2 hover:text-foreground pointer-coarse:min-h-11 motion-reduce:transition-none";
/** A count in the label that opens the log on its level. */
const COUNT = "-mx-1 h-5 px-1 text-xs tabular-nums pointer-coarse:h-11";
const FAILED = new Set(["failed", "ambiguous"]);

const Dot = () => (
  <span aria-hidden="true" className="text-subtle">
    ·
  </span>
);

/**
 * The lines Sync wrote during a step, behind one line of the step. The log is read when the
 * reader opens it, or reaches its label with the pointer or the keyboard; what it holds is shown
 * in the label once it has been read.
 */
export function StepLog({ run, step }: { run: Run; step: RunStep }) {
  const stage = logStage(step);
  const live = step.state === "active";
  // A step that failed opens on its log: that is where the reader looks next. The run says so,
  // so the log is not read to decide it.
  const failed = step.state === "failed" || FAILED.has(step.exec?.terminal_outcome ?? "");
  const [toggled, setToggled] = useState<boolean | null>(null);
  const [primed, setPrimed] = useState(false);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState("");
  const id = useId();
  const open = toggled ?? failed;
  const log = useQuery({
    ...runLogQuery(run.run_id, stage ?? ""),
    enabled: stage !== null && (open || primed),
    refetchInterval: open && live ? LOG_REFRESH_MS : false,
  });
  const { refetch } = log;
  // The step's last lines are written after the last poll: one more read when it ends.
  const wasLive = useRef(live);
  useEffect(() => {
    if (wasLive.current && !live && open) refetch();
    wasLive.current = live;
  }, [live, open, refetch]);

  if (stage === null) return null;
  const lines = log.data?.lines ?? [];
  const status: LogStatus = log.data?.expired
    ? "expired"
    : log.data
      ? "ready"
      : log.isError
        ? "unavailable"
        : "loading";
  const read = status === "ready" && lines.length > 0;
  const warnings = countTone(lines, "warning");
  const errors = countTone(lines, "error");
  /** Opens the log on the warnings alone, or on the errors alone. */
  const only = (tone: LogTone) => {
    setHidden(
      new Set(
        logLevels(lines)
          .map(({ level }) => level)
          .filter((level) => logTone(level) !== tone)
      )
    );
    setToggled(true);
  };
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <div className="flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-1 text-xs">
        <button
          type="button"
          aria-expanded={open}
          aria-controls={id}
          onClick={() => setToggled(!open)}
          onPointerEnter={() => setPrimed(true)}
          onFocus={() => setPrimed(true)}
          className={cn(TOGGLE, FOCUS)}
        >
          <ChevronRightIcon className={expandChevron({ open })} />
          Log
        </button>
        {read && (
          <span className="text-subtle tabular-nums">{plural(lines.length, "line", "lines")}</span>
        )}
        {read && warnings > 0 && (
          <>
            <Dot />
            <Button
              variant="ghost"
              size="xxs"
              onPress={() => only("warning")}
              className={cn(COUNT, "text-warning")}
            >
              {plural(warnings, "warning", "warnings")}
            </Button>
          </>
        )}
        {read && errors > 0 && (
          <>
            <Dot />
            <Button
              variant="ghost"
              size="xxs"
              onPress={() => only("error")}
              className={cn(COUNT, "text-danger")}
            >
              {plural(errors, "error", "errors")}
            </Button>
          </>
        )}
      </div>
      {open && (
        <LogCard
          id={id}
          status={status}
          lines={lines}
          live={live}
          onRetry={() => refetch()}
          step={step}
          view={{ hidden, setHidden, query, setQuery }}
        />
      )}
    </div>
  );
}
