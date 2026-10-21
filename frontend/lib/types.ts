// Shared API types mirroring the NexusForge backend schemas (core/schemas.py).

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

export interface UserOut {
  id: string;
  email: string;
  username: string;
  is_active: boolean;
  created_at: string;
}

export interface ProjectOut {
  id: string;
  name: string;
  description: string;
  status: string;
  created_at: string;
  updated_at: string;
  repository_count: number;
  task_count: number;
  latest_task_state: string | null;
}

export interface RepositoryOut {
  id: string;
  name: string;
  source_kind: string;
  source_ref: string;
  default_branch: string;
  head_commit: string;
  indexed: boolean;
  index_version: number;
  file_count: number;
  total_bytes: number;
  languages: Record<string, number>;
  created_at: string;
}

export interface TaskOut {
  id: string;
  project_id: string;
  repository_id: string | null;
  title: string;
  requirement: string;
  status: string;
  created_at: string;
  updated_at: string;
  latest_run_id: string | null;
}

export interface TaskRunOut {
  id: string;
  task_id: string;
  project_id: string;
  state: string;
  iteration: number;
  llm_calls: number;
  tokens_used: number;
  cost_estimate: number;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  created_at: string;
}

export interface CodeChangeOut {
  path: string;
  action: string;
  diff: string;
  reasoning: string;
  iteration: number;
}

export interface TestRunOut {
  id: string;
  iteration: number;
  command: string;
  exit_code: number | null;
  passed: number;
  failed: number;
  errors: number;
  skipped: number;
  failed_tests: string[];
  duration_ms: number;
  created_at: string;
}

export interface SecurityFindingOut {
  severity: string;
  rule_id: string;
  path: string;
  line: number | null;
  message: string;
  source: string;
}

export interface ReviewFindingOut {
  severity: string;
  category: string;
  path: string;
  line: number | null;
  title: string;
  detail: string;
  source: string;
}

export interface ReportOut {
  id: string;
  format: string;
  content: string;
  created_at: string;
}

export interface StepOut {
  seq: number;
  agent: string;
  state: string;
  status: string;
  summary: string | null;
  error: string | null;
  tokens_used: number;
  started_at: string | null;
  finished_at: string | null;
  output: Record<string, unknown>;
}

export interface TaskDetail {
  task: TaskOut;
  runs: TaskRunOut[];
  latest_run: TaskRunOut | null;
  steps: StepOut[];
  changes: CodeChangeOut[];
  test_runs: TestRunOut[];
  security_findings: SecurityFindingOut[];
  review_findings: ReviewFindingOut[];
  report: ReportOut | null;
}

export interface TaskRunSummary {
  id: string;
  state: string;
  iteration: number;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface CancelResult {
  run_id: string;
  state: string;
  message: string;
}

export const RUN_STATES = [
  "IDLE",
  "QUEUED",
  "ANALYZING",
  "PLANNING",
  "IMPLEMENTING",
  "TESTING",
  "DEBUGGING",
  "RETESTING",
  "SECURITY_ANALYSIS",
  "CODE_REVIEW",
  "COMPLETED",
  "FAILED",
  "CANCELLED",
  "TIMEOUT",
  "SECURITY_BLOCKED",
] as const;

export type RunState = (typeof RUN_STATES)[number];

export const TERMINAL_STATES: ReadonlySet<string> = new Set([
  "COMPLETED",
  "FAILED",
  "CANCELLED",
  "TIMEOUT",
  "SECURITY_BLOCKED",
]);

export function isTerminalState(state: string | null | undefined): boolean {
  return !!state && TERMINAL_STATES.has(state);
}

export function isRunningState(state: string | null | undefined): boolean {
  return !!state && state !== "IDLE" && !TERMINAL_STATES.has(state);
}

/** Human-friendly label for a run state (e.g. "SECURITY_ANALYSIS" -> "Security analysis"). */
export function stateLabel(state: string | null | undefined): string {
  if (!state) return "—";
  return state
    .toLowerCase()
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}