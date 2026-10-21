"use client";

import { clsx } from "clsx";
import type { ReactNode } from "react";
import { isTerminalState, stateLabel } from "@/lib/types";

// --- badges -----------------------------------------------------------------

const STATE_STYLES: Record<string, string> = {
  COMPLETED: "bg-emerald-100 text-emerald-800",
  FAILED: "bg-red-100 text-red-700",
  CANCELLED: "bg-slate-200 text-slate-600",
  TIMEOUT: "bg-amber-100 text-amber-800",
  SECURITY_BLOCKED: "bg-purple-100 text-purple-800",
  QUEUED: "bg-slate-100 text-slate-600",
  IDLE: "bg-slate-100 text-slate-500",
};

const ACTIVE_STYLES: Record<string, string> = {
  ANALYZING: "bg-sky-100 text-sky-800",
  PLANNING: "bg-indigo-100 text-indigo-800",
  IMPLEMENTING: "bg-brand-100 text-brand-800",
  TESTING: "bg-teal-100 text-teal-800",
  DEBUGGING: "bg-orange-100 text-orange-800",
  RETESTING: "bg-teal-100 text-teal-800",
  SECURITY_ANALYSIS: "bg-purple-100 text-purple-800",
  CODE_REVIEW: "bg-violet-100 text-violet-800",
};

export function StateBadge({ state, className }: { state: string | null; className?: string }) {
  if (!state) return <span className={clsx("chip bg-slate-100 text-slate-500", className)}>—</span>;
  const style = STATE_STYLES[state] ?? ACTIVE_STYLES[state] ?? "bg-slate-100 text-slate-600";
  const active = !isTerminalState(state) && state !== "IDLE";
  return (
    <span className={clsx("chip", style, className)}>
      {active && (
        <span className="relative flex h-1.5 w-1.5">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-current opacity-60" />
          <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-current" />
        </span>
      )}
      {stateLabel(state)}
    </span>
  );
}

const SEVERITY_STYLES: Record<string, string> = {
  critical: "bg-red-100 text-red-700",
  high: "bg-red-100 text-red-700",
  medium: "bg-amber-100 text-amber-800",
  low: "bg-sky-100 text-sky-800",
  info: "bg-slate-100 text-slate-600",
  minor: "bg-slate-100 text-slate-600",
  blocking: "bg-red-100 text-red-700",
};

export function SeverityBadge({ severity }: { severity: string }) {
  const key = severity.toLowerCase();
  return (
    <span className={clsx("chip capitalize", SEVERITY_STYLES[key] ?? "bg-slate-100 text-slate-600")}>
      {severity}
    </span>
  );
}

// --- misc -------------------------------------------------------------------

export function Spinner({ className }: { className?: string }) {
  return (
    <svg
      className={clsx("h-4 w-4 animate-spin text-current", className)}
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden
    >
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path
        className="opacity-75"
        fill="currentColor"
        d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z"
      />
    </svg>
  );
}

export function ErrorNote({ message, onDismiss }: { message: string; onDismiss?: () => void }) {
  if (!message) return null;
  return (
    <div className="flex items-start justify-between gap-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
      <span className="break-words">{message}</span>
      {onDismiss && (
        <button
          type="button"
          onClick={onDismiss}
          className="shrink-0 text-red-400 hover:text-red-600"
          aria-label="Dismiss"
        >
          ✕
        </button>
      )}
    </div>
  );
}

export function EmptyState({
  title,
  hint,
  icon,
}: {
  title: string;
  hint?: string;
  icon?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-1 rounded-xl border border-dashed border-slate-300 bg-surface-inset/50 px-6 py-10 text-center">
      {icon && <div className="mb-1 text-2xl opacity-60">{icon}</div>}
      <p className="text-sm font-medium text-ink-soft">{title}</p>
      {hint && <p className="text-xs text-ink-muted">{hint}</p>}
    </div>
  );
}

export function SectionTitle({ children, right }: { children: ReactNode; right?: ReactNode }) {
  return (
    <div className="mb-3 flex items-center justify-between gap-3">
      <h2 className="text-sm font-bold uppercase tracking-wide text-ink-muted">{children}</h2>
      {right}
    </div>
  );
}

export function Modal({
  open,
  title,
  onClose,
  children,
}: {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-slate-900/40 p-4 pt-[10vh] backdrop-blur-[2px]"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      <div className="card w-full max-w-lg shadow-pop">
        <div className="flex items-center justify-between border-b border-slate-200 px-5 py-3">
          <h3 className="text-sm font-bold text-ink">{title}</h3>
          <button
            type="button"
            onClick={onClose}
            className="text-ink-faint hover:text-ink"
            aria-label="Close"
          >
            ✕
          </button>
        </div>
        <div className="px-5 py-4">{children}</div>
      </div>
    </div>
  );
}