"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import ReactMarkdown from "react-markdown";
import { authedUrl, formatDate, getToken, streamUrl, tasksApi } from "@/lib/api";
import { openSse, type SseEvent } from "@/lib/sse";
import {
  isTerminalState,
  stateLabel,
  type CodeChangeOut,
  type ReportOut,
  type ReviewFindingOut,
  type SecurityFindingOut,
  type TaskDetail as TaskDetailT,
  type TestRunOut,
} from "@/lib/types";
import {
  EmptyState,
  ErrorNote,
  SectionTitle,
  SeverityBadge,
  Spinner,
  StateBadge,
} from "./ui";

// --- local view models ------------------------------------------------------

interface TermLine {
  text: string;
  stream: "out" | "err";
}

interface TimelineStep {
  seq: number;
  agent: string;
  state: string;
  status: "running" | "completed" | "failed";
  summary: string;
  error: string | null;
}

interface LiveTestRun {
  iteration: number;
  passed: number;
  failed: number;
  errors: number;
  skipped: number;
  ok: boolean;
}

interface LiveSecurity {
  total: number;
  blocking: number;
  by_severity: Record<string, number>;
}

interface LiveReview {
  total: number;
  verdict: string;
  summary: string;
}

const MAX_TERM_LINES = 3000;

// payload field helpers (SSE payloads are untyped JSON)
const pstr = (v: unknown): string => (typeof v === "string" ? v : "");
const pnum = (v: unknown): number => (typeof v === "number" ? v : 0);

export default function TaskDetailPage() {
  const params = useParams<{ id: string }>();
  const taskId = params.id;

  const [detail, setDetail] = useState<TaskDetailT | null>(null);
  const [loadError, setLoadError] = useState("");
  const [actionError, setActionError] = useState("");

  // live run tracking
  const [runId, setRunId] = useState<string | null>(null);
  const [runState, setRunState] = useState<string | null>(null);
  const [iteration, setIteration] = useState(0);
  const [llmCalls, setLlmCalls] = useState(0);
  const [tokensUsed, setTokensUsed] = useState(0);
  const [steps, setSteps] = useState<TimelineStep[]>([]);
  const [termLines, setTermLines] = useState<TermLine[]>([]);
  const [changedFiles, setChangedFiles] = useState<CodeChangeOut[]>([]);
  const [liveTests, setLiveTests] = useState<LiveTestRun[]>([]);
  const [liveSecurity, setLiveSecurity] = useState<LiveSecurity | null>(null);
  const [liveReview, setLiveReview] = useState<LiveReview | null>(null);
  const [streamError, setStreamError] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [runBusy, setRunBusy] = useState(false);
  const [cancelBusy, setCancelBusy] = useState(false);
  const [resultsLoaded, setResultsLoaded] = useState(false);

  const stopStreamRef = useRef<(() => void) | null>(null);
  const runIdRef = useRef<string | null>(null);
  const stopStream = useCallback(() => {
    stopStreamRef.current?.();
    stopStreamRef.current = null;
    setStreaming(false);
  }, []);

  // --- terminal (authoritative) refresh --------------------------------------

  const loadDetail = useCallback(
    async (forRunId?: string | null) => {
      try {
        const d = await tasksApi.detail(taskId);
        setDetail(d);
        setLoadError("");
        // If we are tracking a run, seed live state from the persisted detail.
        const tracked = forRunId ?? d.task.latest_run_id;
        if (tracked && tracked === d.latest_run?.id) {
          setRunId(tracked);
          setRunState(d.latest_run.state);
          setIteration(d.latest_run.iteration);
          setLlmCalls(d.latest_run.llm_calls);
          setTokensUsed(d.latest_run.tokens_used);
          setSteps(
            d.steps.map((s) => ({
              seq: s.seq,
              agent: s.agent,
              state: s.state,
              status:
                s.status === "failed" || s.status === "error"
                  ? "failed"
                  : s.status === "running"
                    ? "running"
                    : "completed",
              summary: s.summary ?? "",
              error: s.error,
            }))
          );
          if (isTerminalState(d.latest_run.state)) {
            setResultsLoaded(true);
          }
        }
        return d;
      } catch (err) {
        setLoadError(err instanceof Error ? err.message : "Failed to load task");
        return null;
      }
    },
    [taskId]
  );

  // --- SSE -------------------------------------------------------------------

  const handleEvent = useCallback(
    (ev: SseEvent) => {
      const p = (ev.data ?? {}) as Record<string, unknown>;
      switch (ev.type) {
        case "state_change":
          setRunState(pstr(p.state) || null);
          if (typeof p.iteration === "number") setIteration(p.iteration);
          break;
        case "iteration":
          setIteration(pnum(p.iteration));
          break;
        case "step_started": {
          const seq = pnum(p.seq);
          const agent = pstr(p.agent) || "agent";
          const state = pstr(p.state);
          setSteps((prev) => {
            const next = prev.filter((s) => !(s.seq === seq && s.status === "running"));
            return [...next, { seq, agent, state, status: "running", summary: "", error: null }];
          });
          break;
        }
        case "step_finished": {
          const seq = pnum(p.seq);
          const agent = pstr(p.agent);
          const status = pstr(p.status);
          const summary = pstr(p.summary);
          const error = pstr(p.error) || null;
          setSteps((prev) => {
            const idx = prev.findIndex((s) => s.seq === seq || s.agent === agent);
            const failed = status === "failed" || status === "error" || !!error;
            if (idx === -1) {
              return [
                ...prev,
                {
                  seq,
                  agent: agent || "agent",
                  state: "",
                  status: failed ? "failed" : "completed",
                  summary,
                  error,
                },
              ];
            }
            const next = [...prev];
            next[idx] = {
              ...next[idx],
              agent: agent || next[idx].agent,
              status: failed ? "failed" : "completed",
              summary: summary || next[idx].summary,
              error: error ?? next[idx].error,
            };
            return next;
          });
          break;
        }
        case "llm_call":
          setLlmCalls((n) => n + 1);
          setTokensUsed((n) => n + pnum(p.prompt_tokens) + pnum(p.completion_tokens));
          break;
        case "file_changed": {
          const path = pstr(p.path);
          if (path) {
            setChangedFiles((prev) => {
              const idx = prev.findIndex((c) => c.path === path);
              const entry: CodeChangeOut = {
                path,
                action: pstr(p.action) || "modified",
                diff: pstr(p.diff),
                reasoning: pstr(p.reasoning),
                iteration: pnum(p.iteration),
              };
              if (idx === -1) return [...prev, entry];
              const next = [...prev];
              next[idx] = entry;
              return next;
            });
          }
          break;
        }
        case "terminal":
          setTermLines((prev) => {
            const line: TermLine = {
              text: pstr(p.text),
              stream: pstr(p.stream) === "err" ? "err" : "out",
            };
            const next = [...prev, line];
            return next.length > MAX_TERM_LINES ? next.slice(next.length - MAX_TERM_LINES) : next;
          });
          break;
        case "test_result":
          setLiveTests((prev) => [
            ...prev,
            {
              iteration: pnum(p.iteration),
              passed: pnum(p.passed),
              failed: pnum(p.failed),
              errors: pnum(p.errors),
              skipped: pnum(p.skipped),
              ok: !!p.ok,
            },
          ]);
          break;
        case "security_result":
          setLiveSecurity({
            total: pnum(p.total),
            blocking: pnum(p.blocking),
            by_severity: (p.by_severity as Record<string, number>) ?? {},
          });
          break;
        case "review_result":
          setLiveReview({
            total: pnum(p.total),
            verdict: pstr(p.verdict) || "ok",
            summary: pstr(p.summary),
          });
          break;
        case "run_completed":
        case "run_failed":
        case "run_cancelled":
        case "run_timeout":
        case "run_security_blocked": {
          const state = pstr(p.state) || (ev.type === "run_completed" ? "COMPLETED" : "FAILED");
          setRunState(state);
          if (typeof p.iteration === "number") setIteration(p.iteration);
          if (typeof p.tokens_used === "number") setTokensUsed(p.tokens_used);
          if (typeof p.llm_calls === "number") setLlmCalls(p.llm_calls);
          // Pull the authoritative results (test runs, findings, report).
          void loadDetail(runIdRef.current).then((d) => {
            if (d) setResultsLoaded(true);
          });
          break;
        }
        default:
          break;
      }
    },
    [loadDetail]
  );

  const connectStream = useCallback(
    (id: string, afterSeq = 0) => {
      stopStream();
      setStreamError("");
      setStreaming(true);
      const stop = openSse(streamUrl(taskId, id, afterSeq), {
        token: getToken(),
        onEvent: handleEvent,
        onDone: () => setStreaming(false),
        onError: (err) => {
          setStreaming(false);
          setStreamError(err.message);
        },
      });
      stopStreamRef.current = stop;
    },
    [handleEvent, stopStream, taskId]
  );

  // --- initial load -----------------------------------------------------------

  useEffect(() => {
    setDetail(null);
    runIdRef.current = null;
    setRunId(null);
    setRunState(null);
    setIteration(0);
    setLlmCalls(0);
    setTokensUsed(0);
    setSteps([]);
    setTermLines([]);
    setChangedFiles([]);
    setLiveTests([]);
    setLiveSecurity(null);
    setLiveReview(null);
    setResultsLoaded(false);
    void loadDetail().then((d) => {
      const latest = d?.latest_run ?? null;
      if (d && latest && !isTerminalState(latest.state)) {
        // resume live updates for the in-flight run
        runIdRef.current = latest.id;
        setRunId(latest.id);
        setRunState(latest.state);
        setIteration(latest.iteration);
        setLlmCalls(latest.llm_calls);
        setTokensUsed(latest.tokens_used);
        setSteps(
          d.steps.map((s) => ({
            seq: s.seq,
            agent: s.agent,
            state: s.state,
            status:
              s.status === "failed" || s.status === "error"
                ? "failed"
                : s.status === "running"
                  ? "running"
                  : "completed",
            summary: s.summary ?? "",
            error: s.error,
          }))
        );
        connectStream(latest.id, 0);
      }
    });
    return stopStream;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId]);

  // --- actions -----------------------------------------------------------------

  const startRun = async () => {
    setActionError("");
    setRunBusy(true);
    try {
      const run = await tasksApi.startRun(taskId);
      // reset live state for the fresh run
      runIdRef.current = run.id;
      setRunId(run.id);
      setRunState(run.state);
      setIteration(run.iteration);
      setLlmCalls(run.llm_calls);
      setTokensUsed(run.tokens_used);
      setSteps([]);
      setTermLines([]);
      setChangedFiles([]);
      setLiveTests([]);
      setLiveSecurity(null);
      setLiveReview(null);
      setResultsLoaded(false);
      connectStream(run.id, 0);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Failed to start run");
    } finally {
      setRunBusy(false);
    }
  };

  const cancelRun = async () => {
    if (!runId) return;
    setCancelBusy(true);
    setActionError("");
    try {
      const res = await tasksApi.cancelRun(taskId, runId);
      if (res.message && !res.message.startsWith("Cancellation")) {
        setActionError(res.message);
      }
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Failed to cancel run");
    } finally {
      setCancelBusy(false);
    }
  };

  const active = !!runId && !isTerminalState(runState);

  // results: prefer authoritative detail data once loaded, else live accumulators
  const results = useMemo(() => {
    if (!detail) return null;
    return {
      testRuns: detail.test_runs,
      security: detail.security_findings,
      review: detail.review_findings,
      report: detail.report,
      changes: detail.changes.length ? detail.changes : changedFiles,
      run: detail.latest_run,
      runs: detail.runs,
    };
  }, [detail, changedFiles]);

  if (loadError && !detail) {
    return (
      <div className="py-16 text-center">
        <ErrorNote message={loadError} />
        <Link href="/" className="btn-secondary mt-4">
          ← Back
        </Link>
      </div>
    );
  }

  if (!detail) {
    return (
      <div className="flex items-center justify-center py-24">
        <Spinner className="h-6 w-6 text-brand-600" />
      </div>
    );
  }

  const task = detail.task;

  return (
    <div className="space-y-6">
      {/* header */}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <Link
            href={`/projects/${task.project_id}`}
            className="text-xs font-semibold text-ink-muted hover:text-brand-700"
          >
            ← Back to project
          </Link>
          <div className="mt-1 flex flex-wrap items-center gap-3">
            <h1 className="text-2xl font-bold tracking-tight text-ink">{task.title}</h1>
            <StateBadge state={runState ?? task.status} />
          </div>
          <p className="mt-2 max-w-3xl whitespace-pre-wrap text-sm text-ink-muted">
            {task.requirement}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <button
            type="button"
            className="btn-primary"
            onClick={startRun}
            disabled={active || runBusy}
            title={active ? "A run is already in progress" : "Start an agent run"}
          >
            {runBusy ? <Spinner /> : <span aria-hidden>▶</span>}
            {active ? "Running…" : "Run"}
          </button>
          <button
            type="button"
            className="btn-danger"
            onClick={cancelRun}
            disabled={!active || cancelBusy}
            title="Cancel the current run"
          >
            {cancelBusy && <Spinner />}
            Cancel
          </button>
        </div>
      </div>

      <ErrorNote message={actionError} onDismiss={() => setActionError("")} />
      <ErrorNote message={streamError} onDismiss={() => setStreamError("")} />

      {/* stats */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatCard label="State">
          <StateBadge state={runState ?? task.status} />
        </StatCard>
        <StatCard label="Iteration">
          <span className="text-lg font-bold text-ink">{iteration}</span>
        </StatCard>
        <StatCard label="LLM calls">
          <span className="text-lg font-bold text-ink">{llmCalls}</span>
        </StatCard>
        <StatCard label="Tokens used">
          <span className="text-lg font-bold text-ink">
            {tokensUsed.toLocaleString()}
          </span>
        </StatCard>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        {/* left column: terminal + results */}
        <div className="space-y-6 lg:col-span-2">
          <TerminalPane lines={termLines} streaming={streaming} active={active} />

          {/* live progress chips while running */}
          {active && (liveTests.length > 0 || liveSecurity || liveReview) && (
            <div className="card space-y-2 p-4 text-sm">
              <SectionTitle>Live progress</SectionTitle>
              {liveTests.length > 0 && (
                <p className="text-ink-soft">
                  Latest test run (iter {liveTests[liveTests.length - 1].iteration}):{" "}
                  <span className="font-semibold text-emerald-700">
                    {liveTests[liveTests.length - 1].passed} passed
                  </span>{" "}
                  <span className="font-semibold text-red-600">
                    {liveTests[liveTests.length - 1].failed} failed
                  </span>{" "}
                  <span className="text-ink-muted">
                    {liveTests[liveTests.length - 1].errors} errors
                  </span>
                </p>
              )}
              {liveSecurity && (
                <p className="text-ink-soft">
                  Security: {liveSecurity.total} findings
                  {liveSecurity.blocking > 0 && (
                    <span className="font-semibold text-red-600">
                      {" "}
                      ({liveSecurity.blocking} blocking)
                    </span>
                  )}
                </p>
              )}
              {liveReview && (
                <p className="text-ink-soft">
                  Review: {liveReview.total} findings · verdict{" "}
                  <span className="font-semibold">{liveReview.verdict}</span>
                </p>
              )}
            </div>
          )}

          {/* results */}
          {resultsLoaded && results && (
            <Results
              taskId={taskId}
              runId={runId}
              testRuns={results.testRuns}
              security={results.security}
              review={results.review}
              report={results.report}
              changes={results.changes}
            />
          )}
          {resultsLoaded && results && results.report === null && !active && (
            <EmptyState
              title="No report yet"
              hint="The report is generated when a run completes successfully."
            />
          )}
        </div>

        {/* right column: timeline + run history */}
        <div className="space-y-6">
          <div className="card p-4">
            <SectionTitle
              right={
                streaming && (
                  <span className="flex items-center gap-1.5 text-[11px] font-semibold text-emerald-600">
                    <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-500" />
                    live
                  </span>
                )
              }
            >
              Agent timeline
            </SectionTitle>
            <Timeline steps={steps} current={runState} />
          </div>

          {results && results.runs.length > 0 && (
            <div className="card p-4">
              <SectionTitle>Run history</SectionTitle>
              <ul className="divide-y divide-slate-100">
                {[...results.runs].reverse().map((r) => (
                  <li key={r.id} className="flex items-center gap-3 py-2 text-xs">
                    <StateBadge state={r.state} />
                    <span className="text-ink-muted">iter {r.iteration}</span>
                    <span className="ml-auto text-ink-faint">{formatDate(r.started_at)}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// --- subcomponents -----------------------------------------------------------

function StatCard({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="card px-4 py-3">
      <p className="text-[11px] font-semibold uppercase tracking-wide text-ink-muted">{label}</p>
      <div className="mt-1.5">{children}</div>
    </div>
  );
}

function TerminalPane({
  lines,
  streaming,
  active,
}: {
  lines: TermLine[];
  streaming: boolean;
  active: boolean;
}) {
  const boxRef = useRef<HTMLDivElement>(null);
  const stickRef = useRef(true);

  const onScroll = () => {
    const el = boxRef.current;
    if (!el) return;
    stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
  };

  useEffect(() => {
    const el = boxRef.current;
    if (el && stickRef.current) el.scrollTop = el.scrollHeight;
  }, [lines]);

  return (
    <div className="overflow-hidden rounded-xl border border-term-border bg-term-bg shadow-card">
      <div className="flex items-center gap-2 border-b border-term-border bg-term-panel px-4 py-2">
        <span className="h-2.5 w-2.5 rounded-full bg-red-500/80" />
        <span className="h-2.5 w-2.5 rounded-full bg-amber-400/80" />
        <span className="h-2.5 w-2.5 rounded-full bg-emerald-500/80" />
        <span className="ml-2 text-[11px] font-semibold uppercase tracking-wider text-term-dim">
          Terminal
        </span>
        {active && (
          <span className="ml-auto flex items-center gap-1.5 text-[11px] font-semibold text-emerald-400">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-400" />
            {streaming ? "streaming" : "running"}
          </span>
        )}
      </div>
      <div
        ref={boxRef}
        onScroll={onScroll}
        className="term h-80 overflow-y-auto px-4 py-3"
        aria-label="Live terminal output"
      >
        {lines.length === 0 ? (
          <span className="text-term-dim">
            {active ? "Waiting for output…" : "No output yet. Start a run to see live output."}
          </span>
        ) : (
          lines.map((l, i) => (
            <div key={i} className={l.stream === "err" ? "text-term-err" : undefined}>
              {l.text || " "}
            </div>
          ))
        )}
      </div>
    </div>
  );
}

function Timeline({ steps, current }: { steps: TimelineStep[]; current: string | null }) {
  if (steps.length === 0) {
    return (
      <p className="py-4 text-center text-xs text-ink-muted">
        {current && !isTerminalState(current)
          ? "Waiting for the first agent step…"
          : "No steps recorded yet."}
      </p>
    );
  }
  return (
    <ol className="relative space-y-0">
      {steps.map((s, i) => {
        const isCurrent = s.status === "running";
        const dot =
          s.status === "running" ? (
            <span className="relative flex h-3 w-3">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-brand-400 opacity-70" />
              <span className="relative inline-flex h-3 w-3 rounded-full bg-brand-500" />
            </span>
          ) : s.status === "failed" ? (
            <span className="inline-flex h-3 w-3 rounded-full bg-red-500" />
          ) : (
            <span className="inline-flex h-3 w-3 rounded-full bg-emerald-500" />
          );
        return (
          <li key={`${s.seq}-${i}`} className="relative flex gap-3 pb-4 last:pb-0">
            {i < steps.length - 1 && (
              <span className="absolute left-[5px] top-4 h-full w-px bg-slate-200" aria-hidden />
            )}
            <span className="relative z-10 mt-0.5">{dot}</span>
            <div
              className={
                isCurrent
                  ? "min-w-0 flex-1 rounded-lg border border-brand-200 bg-brand-50 px-3 py-2"
                  : "min-w-0 flex-1 rounded-lg px-3 py-2"
              }
            >
              <div className="flex items-center justify-between gap-2">
                <span className="truncate text-[13px] font-semibold capitalize text-ink">
                  {s.agent}
                </span>
                <span className="shrink-0 text-[10px] font-semibold uppercase tracking-wide text-ink-faint">
                  {s.state ? stateLabel(s.state) : ""}
                </span>
              </div>
              {s.summary && (
                <p className="mt-0.5 line-clamp-2 text-xs text-ink-muted">{s.summary}</p>
              )}
              {s.error && <p className="mt-0.5 text-xs text-red-600">{s.error}</p>}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

function Results({
  taskId,
  runId,
  testRuns,
  security,
  review,
  report,
  changes,
}: {
  taskId: string;
  runId: string | null;
  testRuns: TestRunOut[];
  security: SecurityFindingOut[];
  review: ReviewFindingOut[];
  report: ReportOut | null;
  changes: CodeChangeOut[];
}) {
  return (
    <div className="space-y-6">
      {/* test runs */}
      <section>
        <SectionTitle>Test runs</SectionTitle>
        {testRuns.length === 0 ? (
          <EmptyState title="No test runs recorded" />
        ) : (
          <div className="card overflow-x-auto">
            <table className="w-full min-w-[560px] text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-left text-[11px] font-semibold uppercase tracking-wide text-ink-muted">
                  <th className="px-4 py-2.5">Iter</th>
                  <th className="px-4 py-2.5">Command</th>
                  <th className="px-4 py-2.5 text-right">Passed</th>
                  <th className="px-4 py-2.5 text-right">Failed</th>
                  <th className="px-4 py-2.5 text-right">Errors</th>
                  <th className="px-4 py-2.5 text-right">Skipped</th>
                  <th className="px-4 py-2.5 text-right">Duration</th>
                </tr>
              </thead>
              <tbody>
                {[...testRuns].reverse().map((t) => (
                  <tr key={t.id} className="border-b border-slate-100 last:border-0">
                    <td className="px-4 py-2 font-semibold text-ink">{t.iteration}</td>
                    <td className="max-w-[220px] truncate px-4 py-2 font-mono text-xs text-ink-muted" title={t.command}>
                      {t.command}
                    </td>
                    <td className="px-4 py-2 text-right font-semibold text-emerald-700">
                      {t.passed}
                    </td>
                    <td className="px-4 py-2 text-right font-semibold text-red-600">{t.failed}</td>
                    <td className="px-4 py-2 text-right text-amber-700">{t.errors}</td>
                    <td className="px-4 py-2 text-right text-ink-muted">{t.skipped}</td>
                    <td className="px-4 py-2 text-right text-ink-muted">
                      {(t.duration_ms / 1000).toFixed(1)}s
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* security findings */}
      <section>
        <SectionTitle>Security findings</SectionTitle>
        {security.length === 0 ? (
          <EmptyState title="No security findings" hint="Static analysis found no issues." />
        ) : (
          <div className="card divide-y divide-slate-100">
            {security.map((f, i) => (
              <div key={i} className="flex items-start gap-3 px-4 py-3">
                <SeverityBadge severity={f.severity} />
                <div className="min-w-0 flex-1">
                  <p className="text-sm text-ink-soft">{f.message}</p>
                  <p className="mt-0.5 font-mono text-[11px] text-ink-faint">
                    {f.path}
                    {f.line != null && `:${f.line}`} · {f.rule_id} · {f.source}
                  </p>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* review findings */}
      <section>
        <SectionTitle>Code review</SectionTitle>
        {review.length === 0 ? (
          <EmptyState title="No review findings" hint="The reviewer had no issues to flag." />
        ) : (
          <div className="card divide-y divide-slate-100">
            {review.map((f, i) => (
              <div key={i} className="px-4 py-3">
                <div className="flex items-center gap-2">
                  <SeverityBadge severity={f.severity} />
                  <span className="chip bg-surface-inset text-ink-soft">{f.category}</span>
                  <span className="truncate text-sm font-semibold text-ink">{f.title}</span>
                </div>
                {f.detail && <p className="mt-1 text-sm text-ink-muted">{f.detail}</p>}
                <p className="mt-0.5 font-mono text-[11px] text-ink-faint">
                  {f.path}
                  {f.line != null && `:${f.line}`}
                </p>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* changed files */}
      {changes.length > 0 && (
        <section>
          <SectionTitle>Changed files</SectionTitle>
          <div className="card divide-y divide-slate-100">
            {changes.map((c, i) => (
              <details key={`${c.path}-${i}`} className="group px-4 py-2.5">
                <summary className="flex cursor-pointer list-none items-center gap-2 text-sm">
                  <span
                    className={
                      c.action === "added"
                        ? "chip bg-emerald-100 text-emerald-800"
                        : c.action === "deleted"
                          ? "chip bg-red-100 text-red-700"
                          : "chip bg-sky-100 text-sky-800"
                    }
                  >
                    {c.action}
                  </span>
                  <span className="truncate font-mono text-xs text-ink-soft">{c.path}</span>
                  <span className="ml-auto text-[10px] text-ink-faint">iter {c.iteration}</span>
                </summary>
                {c.reasoning && (
                  <p className="mt-1.5 pl-1 text-xs text-ink-muted">{c.reasoning}</p>
                )}
                {c.diff && (
                  <pre className="term mt-2 max-h-64 overflow-auto rounded-lg p-3 text-[11.5px]">
                    {c.diff}
                  </pre>
                )}
              </details>
            ))}
          </div>
        </section>
      )}

      {/* report */}
      {report && (
        <section>
          <SectionTitle
            right={
              runId && (
                <a
                  href={authedUrl(`/api/tasks/${taskId}/runs/${runId}/report/pdf`)}
                  className="btn-secondary !px-3 !py-1.5 text-xs"
                  target="_blank"
                  rel="noreferrer"
                >
                  ⬇ Download PDF
                </a>
              )
            }
          >
            Report
          </SectionTitle>
          <div className="card p-5">
            <div className="markdown-body">
              <ReactMarkdown>{report.content}</ReactMarkdown>
            </div>
          </div>
        </section>
      )}
    </div>
  );
}

