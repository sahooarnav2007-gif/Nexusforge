"use client";

import Link from "next/link";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { projectsApi } from "@/lib/api";
import type { ProjectOut } from "@/lib/types";
import { formatDate } from "@/lib/api";
import { EmptyState, ErrorNote, Modal, Spinner, StateBadge } from "./ui";

export default function Dashboard() {
  const [projects, setProjects] = useState<ProjectOut[] | null>(null);
  const [error, setError] = useState("");
  const [createOpen, setCreateOpen] = useState(false);

  const load = useCallback(async () => {
    try {
      const page = await projectsApi.list();
      setProjects(page.items);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load projects");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div>
      <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-ink">Projects</h1>
          <p className="mt-1 text-sm text-ink-muted">
            Your repositories and AI engineering tasks, in one place.
          </p>
        </div>
        <button type="button" className="btn-primary" onClick={() => setCreateOpen(true)}>
          + New project
        </button>
      </div>

      <ErrorNote message={error} onDismiss={() => setError("")} />

      {projects === null ? (
        <div className="flex items-center justify-center py-24">
          <Spinner className="h-6 w-6 text-brand-600" />
        </div>
      ) : projects.length === 0 ? (
        <EmptyState
          icon="📦"
          title="No projects yet"
          hint="Create a project, import a repository, then hand NexusForge a task."
        />
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {projects.map((p) => (
            <Link
              key={p.id}
              href={`/projects/${p.id}`}
              className="card group flex flex-col gap-3 p-5 transition hover:-translate-y-0.5 hover:shadow-pop"
            >
              <div className="flex items-start justify-between gap-3">
                <h3 className="truncate text-[15px] font-bold text-ink group-hover:text-brand-700">
                  {p.name}
                </h3>
                <StateBadge state={p.latest_task_state} />
              </div>
              <p className="line-clamp-2 min-h-[2.5rem] text-sm text-ink-muted">
                {p.description || "No description"}
              </p>
              <div className="mt-auto flex items-center gap-4 border-t border-slate-100 pt-3 text-xs text-ink-muted">
                <span title="Repositories">
                  <span className="font-semibold text-ink-soft">{p.repository_count}</span> repos
                </span>
                <span title="Tasks">
                  <span className="font-semibold text-ink-soft">{p.task_count}</span> tasks
                </span>
                <span className="ml-auto">{formatDate(p.created_at)}</span>
              </div>
            </Link>
          ))}
        </div>
      )}

      <CreateProjectModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={load}
      />
    </div>
  );
}

function CreateProjectModal({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      const project = await projectsApi.create(name.trim(), description.trim());
      setName("");
      setDescription("");
      onCreated();
      onClose();
      window.location.href = `/projects/${project.id}`;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create project");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal open={open} title="New project" onClose={onClose}>
      <form onSubmit={submit} className="space-y-4">
        <ErrorNote message={error} onDismiss={() => setError("")} />
        <div>
          <label className="label" htmlFor="p-name">
            Name
          </label>
          <input
            id="p-name"
            required
            maxLength={200}
            className="input"
            placeholder="acme-webapp"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </div>
        <div>
          <label className="label" htmlFor="p-desc">
            Description
          </label>
          <textarea
            id="p-desc"
            rows={3}
            className="input resize-none"
            placeholder="What is this project about?"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </div>
        <div className="flex justify-end gap-2 pt-1">
          <button type="button" className="btn-secondary" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" disabled={busy} className="btn-primary">
            {busy && <Spinner />}
            Create project
          </button>
        </div>
      </form>
    </Modal>
  );
}