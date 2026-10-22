"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { projectsApi, tasksApi, formatDate, formatBytes } from "@/lib/api";
import type { ProjectOut, RepositoryOut, TaskOut } from "@/lib/types";
import { EmptyState, ErrorNote, SectionTitle, Spinner, StateBadge } from "./ui";

export default function ProjectDetail() {
  const params = useParams<{ id: string }>();
  const projectId = params.id;
  const router = useRouter();

  const [project, setProject] = useState<ProjectOut | null>(null);
  const [repos, setRepos] = useState<RepositoryOut[] | null>(null);
  const [tasks, setTasks] = useState<TaskOut[] | null>(null);
  const [error, setError] = useState("");
  const [loadError, setLoadError] = useState("");

  const load = useCallback(async () => {
    try {
      const [p, r, t] = await Promise.all([
        projectsApi.get(projectId),
        projectsApi.listRepositories(projectId),
        tasksApi.listForProject(projectId),
      ]);
      setProject(p);
      setRepos(r);
      setTasks(t.items);
      setLoadError("");
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "Failed to load project");
    }
  }, [projectId]);

  useEffect(() => {
    load();
  }, [load]);

  const deleteProject = async () => {
    if (!project) return;
    if (!window.confirm(`Delete project "${project.name}" and all of its tasks?`)) return;
    try {
      await projectsApi.remove(project.id);
      router.replace("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete project");
    }
  };

  if (loadError && !project) {
    return (
      <div className="py-16 text-center">
        <ErrorNote message={loadError} />
        <Link href="/" className="btn-secondary mt-4">
          ← Back to projects
        </Link>
      </div>
    );
  }

  if (!project) {
    return (
      <div className="flex items-center justify-center py-24">
        <Spinner className="h-6 w-6 text-brand-600" />
      </div>
    );
  }

  return (
    <div className="space-y-8">
      {/* header */}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <Link href="/" className="text-xs font-semibold text-ink-muted hover:text-brand-700">
            ← All projects
          </Link>
          <h1 className="mt-1 truncate text-2xl font-bold tracking-tight text-ink">
            {project.name}
          </h1>
          {project.description && (
            <p className="mt-1 max-w-2xl text-sm text-ink-muted">{project.description}</p>
          )}
          <p className="mt-2 text-xs text-ink-faint">
            Created {formatDate(project.created_at)} · updated {formatDate(project.updated_at)}
          </p>
        </div>
        <button type="button" className="btn-danger" onClick={deleteProject}>
          Delete project
        </button>
      </div>

      <ErrorNote message={error} onDismiss={() => setError("")} />

      {/* repositories */}
      <section>
        <SectionTitle>Repositories</SectionTitle>
        <div className="space-y-3">
          {repos === null ? (
            <div className="flex justify-center py-8">
              <Spinner className="text-brand-600" />
            </div>
          ) : repos.length === 0 ? (
            <EmptyState
              icon="🗂️"
              title="No repositories imported"
              hint="Import a local repository so agents have code to work on."
            />
          ) : (
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
              {repos.map((repo) => (
                <RepoCard
                  key={repo.id}
                  projectId={projectId}
                  repo={repo}
                  onDone={load}
                  onError={(m) => setError(m)}
                />
              ))}
            </div>
          )}
        </div>
        <div className="mt-4">
          <ImportRepoForm
            projectId={projectId}
            onDone={load}
            onError={(m) => setError(m)}
          />
        </div>
      </section>

      {/* tasks */}
      <section>
        <SectionTitle
          right={
            tasks && (
              <span className="text-xs text-ink-muted">
                {tasks.length} task{tasks.length === 1 ? "" : "s"}
              </span>
            )
          }
        >
          Tasks
        </SectionTitle>
        {tasks === null ? (
          <div className="flex justify-center py-8">
            <Spinner className="text-brand-600" />
          </div>
        ) : tasks.length === 0 ? (
          <EmptyState
            icon="🎯"
            title="No tasks yet"
            hint="Describe what you want built or changed, then run the agent."
          />
        ) : (
          <div className="card divide-y divide-slate-100 overflow-hidden">
            {tasks.map((task) => (
              <Link
                key={task.id}
                href={`/tasks/${task.id}`}
                className="flex items-center gap-4 px-4 py-3 transition hover:bg-surface-inset/60"
              >
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-semibold text-ink">{task.title}</p>
                  <p className="mt-0.5 truncate text-xs text-ink-muted">
                    {task.requirement}
                  </p>
                </div>
                <span className="hidden text-xs text-ink-faint sm:inline">
                  {formatDate(task.created_at)}
                </span>
                <StateBadge state={task.status} />
              </Link>
            ))}
          </div>
        )}
        <div className="mt-4">
          <CreateTaskForm
            projectId={projectId}
            repos={repos ?? []}
            onDone={load}
            onError={(m) => setError(m)}
          />
        </div>
      </section>
    </div>
  );
}

// --- repository card --------------------------------------------------------

function RepoCard({
  projectId,
  repo,
  onDone,
  onError,
}: {
  projectId: string;
  repo: RepositoryOut;
  onDone: () => void;
  onError: (msg: string) => void;
}) {
  const languages = Object.entries(repo.languages || {})
    .sort((a, b) => b[1] - a[1])
    .slice(0, 5);

  return (
    <div className="card p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h3 className="truncate text-sm font-bold text-ink">{repo.name}</h3>
            {repo.indexed ? (
              <span className="chip bg-emerald-100 text-emerald-800">indexed</span>
            ) : (
              <span className="chip bg-slate-100 text-slate-500">not indexed</span>
            )}
          </div>
          <p className="mt-1 truncate font-mono text-[11px] text-ink-faint" title={repo.source_ref}>
            {repo.source_kind} · {repo.source_ref}
          </p>
        </div>
        <RepoActions projectId={projectId} repo={repo} onDone={onDone} onError={onError} />
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink-muted">
        <span>
          <span className="font-semibold text-ink-soft">{repo.file_count.toLocaleString()}</span>{" "}
          files
        </span>
        <span>{formatBytes(repo.total_bytes)}</span>
        {repo.default_branch && (
          <span className="font-mono">
            branch <span className="text-ink-soft">{repo.default_branch}</span>
          </span>
        )}
        {repo.head_commit && (
          <span className="font-mono" title={repo.head_commit}>
            {repo.head_commit.slice(0, 7)}
          </span>
        )}
      </div>
      {languages.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {languages.map(([lang, count]) => (
            <span key={lang} className="chip bg-surface-inset text-ink-soft">
              {lang} <span className="text-ink-faint">{count}</span>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function RepoActions({
  projectId,
  repo,
  onDone,
  onError,
}: {
  projectId: string;
  repo: RepositoryOut;
  onDone: () => void;
  onError: (msg: string) => void;
}) {
  const [busy, setBusy] = useState(false);

  const act = async (kind: "reindex" | "delete") => {
    if (kind === "delete" && !window.confirm(`Remove repository "${repo.name}"?`)) return;
    setBusy(true);
    try {
      if (kind === "reindex") await projectsApi.reindexRepository(projectId, repo.id);
      else await projectsApi.removeRepository(projectId, repo.id);
      onDone();
    } catch (err) {
      onError(err instanceof Error ? err.message : "Action failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex shrink-0 gap-1.5">
      <button
        type="button"
        className="btn-secondary !px-2.5 !py-1 text-xs"
        disabled={busy}
        onClick={() => act("reindex")}
        title="Re-index this repository"
      >
        {busy ? <Spinner className="h-3 w-3" /> : "Reindex"}
      </button>
      <button
        type="button"
        className="btn-danger !px-2.5 !py-1 text-xs"
        disabled={busy}
        onClick={() => act("delete")}
        title="Remove this repository"
      >
        Remove
      </button>
    </div>
  );
}

// --- import repository form -------------------------------------------------

function ImportRepoForm({
  projectId,
  onDone,
  onError,
}: {
  projectId: string;
  onDone: () => void;
  onError: (msg: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [sourceRef, setSourceRef] = useState("");
  const [index, setIndex] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      const trimmedRef = sourceRef.trim();
      const isGit = trimmedRef.startsWith("http://") || trimmedRef.startsWith("https://") || trimmedRef.startsWith("git@");
      await projectsApi.importRepository(projectId, {
        name: name.trim(),
        source_kind: isGit ? "git" : "local",
        source_ref: trimmedRef,
        index,
      });
      setName("");
      setSourceRef("");
      setIndex(true);
      setOpen(false);
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to import repository");
    } finally {
      setBusy(false);
    }
  };

  if (!open) {
    return (
      <button type="button" className="btn-secondary" onClick={() => setOpen(true)}>
        + Import repository
      </button>
    );
  }

  return (
    <form onSubmit={submit} className="card space-y-3 p-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-bold text-ink">Import repository</h3>
        <button
          type="button"
          className="text-xs font-medium text-brand-600 hover:text-brand-700 hover:underline"
          onClick={() => {
            setName("demo-service");
            setSourceRef("demo/repo");
          }}
        >
          ✨ Load Built-in Demo Repo
        </button>
      </div>
      <ErrorNote message={error} onDismiss={() => setError("")} />
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div>
          <label className="label" htmlFor="r-name">
            Name
          </label>
          <input
            id="r-name"
            required
            maxLength={200}
            className="input"
            placeholder="demo-service"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </div>
        <div>
          <label className="label" htmlFor="r-ref">
            Git Repository URL or Demo Path
          </label>
          <input
            id="r-ref"
            required
            maxLength={500}
            className="input font-mono text-xs"
            placeholder="https://github.com/owner/repo or demo/repo"
            value={sourceRef}
            onChange={(e) => {
              const val = e.target.value;
              setSourceRef(val);
              if (!name && val.includes("/")) {
                const parts = val.replace(/\.git$/, "").split("/").filter(Boolean);
                if (parts.length > 0) setName(parts[parts.length - 1]);
              }
            }}
          />
          <p className="mt-1 text-[11px] text-ink-faint">
            Enter a public Git URL or click &quot;Load Built-in Demo Repo&quot; above.
          </p>
        </div>
      </div>
      <label className="flex items-center gap-2 text-sm text-ink-soft">
        <input
          type="checkbox"
          className="h-4 w-4 rounded border-slate-300 text-brand-600 focus:ring-brand-500"
          checked={index}
          onChange={(e) => setIndex(e.target.checked)}
        />
        Index files for agent retrieval
      </label>
      <div className="flex justify-end gap-2">
        <button type="button" className="btn-secondary" onClick={() => setOpen(false)}>
          Cancel
        </button>
        <button type="submit" disabled={busy} className="btn-primary">
          {busy && <Spinner />}
          Import
        </button>
      </div>
    </form>
  );
}

// --- create task form -------------------------------------------------------

function CreateTaskForm({
  projectId,
  repos,
  onDone,
  onError,
}: {
  projectId: string;
  repos: RepositoryOut[];
  onDone: () => void;
  onError: (msg: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [requirement, setRequirement] = useState("");
  const [repoId, setRepoId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      const task = await tasksApi.create(projectId, {
        title: title.trim(),
        requirement: requirement.trim(),
        repository_id: repoId || null,
      });
      setOpen(false);
      window.location.href = `/tasks/${task.id}`;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create task");
      onError(err instanceof Error ? err.message : "Failed to create task");
    } finally {
      setBusy(false);
    }
  };

  if (!open) {
    return (
      <button type="button" className="btn-primary" onClick={() => setOpen(true)}>
        + New task
      </button>
    );
  }

  return (
    <form onSubmit={submit} className="card space-y-3 p-4">
      <h3 className="text-sm font-bold text-ink">New task</h3>
      <ErrorNote message={error} onDismiss={() => setError("")} />
      <div>
        <label className="label" htmlFor="t-title">
          Title
        </label>
        <input
          id="t-title"
          required
          maxLength={300}
          className="input"
          placeholder="Add rate limiting to the API"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />
      </div>
      <div>
        <label className="label" htmlFor="t-req">
          Requirement
        </label>
        <textarea
          id="t-req"
          required
          minLength={3}
          rows={4}
          className="input resize-y"
          placeholder="Describe exactly what should be built or changed, including acceptance criteria."
          value={requirement}
          onChange={(e) => setRequirement(e.target.value)}
        />
      </div>
      <div>
        <label className="label" htmlFor="t-repo">
          Repository <span className="normal-case text-ink-faint">(optional)</span>
        </label>
        <select
          id="t-repo"
          className="input"
          value={repoId}
          onChange={(e) => setRepoId(e.target.value)}
        >
          <option value="">— project default —</option>
          {repos.map((r) => (
            <option key={r.id} value={r.id}>
              {r.name}
            </option>
          ))}
        </select>
      </div>
      <div className="flex justify-end gap-2">
        <button type="button" className="btn-secondary" onClick={() => setOpen(false)}>
          Cancel
        </button>
        <button type="submit" disabled={busy} className="btn-primary">
          {busy && <Spinner />}
          Create task
        </button>
      </div>
    </form>
  );
}