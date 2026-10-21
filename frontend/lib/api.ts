// Thin fetch wrapper around the NexusForge backend.
// - Base URL: NEXT_PUBLIC_API_BASE (default http://localhost:8000)
// - Injects the Bearer access token from localStorage.
// - On a 401, transparently refreshes the token once and retries.
// - Throws ApiError with the server's message on non-2xx.

import type {
  CancelResult,
  Page,
  ProjectOut,
  RepositoryOut,
  TaskDetail,
  TaskOut,
  TaskRunOut,
  TaskRunSummary,
  TokenPair,
  UserOut,
} from "./types";

const ACCESS_KEY = "nexusforge.access_token";
const REFRESH_KEY = "nexusforge.refresh_token";

export function apiBase(): string {
  if (process.env.NEXT_PUBLIC_API_BASE) {
    return process.env.NEXT_PUBLIC_API_BASE.replace(/\/+$/, "");
  }
  return "https://nexusforge-ji53.onrender.com";
}

export class ApiError extends Error {
  status: number;
  code: string;

  constructor(status: number, message: string, code = "error") {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

// --- token storage (browser only) ------------------------------------------

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(ACCESS_KEY);
}

export function getRefreshToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(REFRESH_KEY);
}

export function setTokens(tokens: TokenPair): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(ACCESS_KEY, tokens.access_token);
  window.localStorage.setItem(REFRESH_KEY, tokens.refresh_token);
}

export function clearTokens(): void {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(ACCESS_KEY);
  window.localStorage.removeItem(REFRESH_KEY);
}

export function isLoggedIn(): boolean {
  return !!getToken();
}

// --- core request -----------------------------------------------------------

async function parseError(res: Response): Promise<ApiError> {
  let message = `Request failed (${res.status})`;
  let code = "error";
  try {
    const body = (await res.json()) as Record<string, unknown>;
    const err = body?.error;
    if (err && typeof err === "object" && "message" in err) {
      message = String((err as Record<string, unknown>).message);
      code = String((err as Record<string, unknown>).code || "error");
    } else if (typeof body?.detail === "string") {
      message = body.detail;
    } else if (typeof body?.message === "string") {
      message = body.message;
    }
  } catch {
    // non-JSON body; keep the default message
  }
  return new ApiError(res.status, message, code);
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  signal?: AbortSignal;
  /** Set true for endpoints that should not trigger a token refresh on 401. */
  noAuthRetry?: boolean;
}

let refreshPromise: Promise<TokenPair | null> | null = null;

async function refreshTokens(): Promise<TokenPair | null> {
  if (typeof window === "undefined") return null;
  const refresh = getRefreshToken();
  if (!refresh) return null;
  try {
    const res = await fetch(`${apiBase()}/api/auth/refresh`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Bypass-Tunnel-Reminder": "true",
      },
      body: JSON.stringify({ refresh_token: refresh }),
    });
    if (!res.ok) return null;
    const tokens = (await res.json()) as TokenPair;
    setTokens(tokens);
    return tokens;
  } catch {
    return null;
  }
}

async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const doFetch = (token: string | null): Promise<Response> =>
    fetch(`${apiBase()}${path}`, {
      method: opts.method ?? "GET",
      headers: {
        "Bypass-Tunnel-Reminder": "true",
        ...(opts.body !== undefined ? { "Content-Type": "application/json" } : {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      signal: opts.signal,
    });

  let res = await doFetch(getToken());

  if (res.status === 401 && !opts.noAuthRetry) {
    refreshPromise = refreshPromise ?? refreshTokens().finally(() => (refreshPromise = null));
    const refreshed = await refreshPromise;
    if (refreshed) {
      res = await doFetch(refreshed.access_token);
    }
  }

  if (!res.ok) throw await parseError(res);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

// --- auth -------------------------------------------------------------------

export const authApi = {
  register: (email: string, username: string, password: string) =>
    request<TokenPair>("/api/auth/register", {
      method: "POST",
      body: { email, username, password },
      noAuthRetry: true,
    }),
  login: (email: string, password: string) =>
    request<TokenPair>("/api/auth/login", {
      method: "POST",
      body: { email, password },
      noAuthRetry: true,
    }),
  me: () => request<UserOut>("/api/auth/me"),
  logout: () =>
    request<void>("/api/auth/logout", {
      method: "POST",
      body: { refresh_token: getRefreshToken() },
      noAuthRetry: true,
    }),
};

// --- projects ---------------------------------------------------------------

export const projectsApi = {
  list: (limit = 100, offset = 0) =>
    request<Page<ProjectOut>>(`/api/projects?limit=${limit}&offset=${offset}`),
  get: (id: string) => request<ProjectOut>(`/api/projects/${id}`),
  create: (name: string, description: string) =>
    request<ProjectOut>("/api/projects", {
      method: "POST",
      body: { name, description },
    }),
  update: (id: string, body: { name?: string; description?: string }) =>
    request<ProjectOut>(`/api/projects/${id}`, { method: "PATCH", body }),
  remove: (id: string) => request<void>(`/api/projects/${id}`, { method: "DELETE" }),
  listRepositories: (projectId: string) =>
    request<RepositoryOut[]>(`/api/projects/${projectId}/repositories`),
  importRepository: (
    projectId: string,
    body: { name: string; source_kind: "local" | "upload"; source_ref: string; index: boolean }
  ) =>
    request<RepositoryOut>(`/api/projects/${projectId}/repositories`, {
      method: "POST",
      body,
    }),
  reindexRepository: (projectId: string, repoId: string) =>
    request<RepositoryOut>(`/api/projects/${projectId}/repositories/${repoId}/reindex`, {
      method: "POST",
    }),
  removeRepository: (projectId: string, repoId: string) =>
    request<void>(`/api/projects/${projectId}/repositories/${repoId}`, { method: "DELETE" }),
};

// --- tasks ------------------------------------------------------------------

export const tasksApi = {
  listForProject: (projectId: string, limit = 100, offset = 0) =>
    request<Page<TaskOut>>(`/api/tasks/projects/${projectId}?limit=${limit}&offset=${offset}`),
  create: (
    projectId: string,
    body: { title: string; requirement: string; repository_id?: string | null }
  ) =>
    request<TaskOut>(`/api/tasks/projects/${projectId}`, { method: "POST", body }),
  detail: (taskId: string) => request<TaskDetail>(`/api/tasks/${taskId}`),
  startRun: (taskId: string, resume = false) =>
    request<TaskRunOut>(`/api/tasks/${taskId}/run`, {
      method: "POST",
      body: { resume },
    }),
  cancelRun: (taskId: string, runId: string) =>
    request<CancelResult>(`/api/tasks/${taskId}/runs/${runId}/cancel`, { method: "POST" }),
  runs: (taskId: string) => request<TaskRunSummary[]>(`/api/tasks/${taskId}/runs`),
  events: (taskId: string, runId: string, afterSeq = 0) =>
    request<{ seq: number; type: string; payload: Record<string, unknown>; created_at: string }[]>(
      `/api/tasks/${taskId}/runs/${runId}/events?after_seq=${afterSeq}&limit=2000`
    ),
};

// --- helpers ----------------------------------------------------------------

/** Full URL (with query) for an authenticated resource, e.g. the PDF report. */
export function authedUrl(path: string, params: Record<string, string | number> = {}): string {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) qs.set(k, String(v));
  const token = getToken();
  if (token) qs.set("access_token", token);
  return `${apiBase()}${path}?${qs.toString()}`;
}

/** Absolute URL for an SSE stream (consumed by lib/sse.ts). */
export function streamUrl(taskId: string, runId: string, afterSeq = 0): string {
  return `${apiBase()}/api/tasks/${taskId}/runs/${runId}/stream?after_seq=${afterSeq}`;
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatBytes(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v >= 10 ? 0 : 1)} ${units[i]}`;
}