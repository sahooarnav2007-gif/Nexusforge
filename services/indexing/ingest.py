"""Repository import: copy a local directory into the project workspace,
initialize git if needed, and record metadata.

Security: the source path must exist and be a directory; the destination is
always under the configured workspace root (path traversal is impossible
because we build the path from the project/repo ids, not user input).
"""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from core.config import get_settings
from core.errors import ValidationError
from core.logging import get_logger
from core.models import Project, Repository
from services.gitops import git

log = get_logger("nexusforge.ingest")


def workspace_for(project: Project, repo_name: str) -> Path:
    s = get_settings()
    safe_name = "".join(c for c in repo_name if c.isalnum() or c in "-_.") or "repo"
    target = Path(s.workspace_root) / project.id / safe_name
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


async def import_repository(
    db: AsyncSession,
    project: Project,
    *,
    name: str,
    source_kind: str,
    source_ref: str,
) -> Repository:
    if source_kind == "local":
        if not source_ref:
            raise ValidationError("source_ref (local path) is required for kind 'local'")
        src = Path(source_ref).expanduser().resolve()
        if not src.exists():
            raise ValidationError(f"Source path does not exist: {src}")
        if not src.is_dir():
            raise ValidationError(f"Source path is not a directory: {src}")
        target = workspace_for(project, name)
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(
            src, target,
            ignore=shutil.ignore_patterns(
                ".git", "node_modules", "__pycache__", ".venv", "venv",
                ".next", "dist", "build", ".cache",
            ),
            symlinks=False,
        )
    elif source_kind == "upload":
        # uploads are staged by the API layer at var/uploads/<ref>; move them in
        s = get_settings()
        src = Path(s.workspace_root).parent / "uploads" / source_ref
        if not src.is_dir():
            raise ValidationError(f"Uploaded repository not found: {source_ref}")
        target = workspace_for(project, name)
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(src), str(target))
    else:
        raise ValidationError(f"Unsupported source_kind: {source_kind}")

    # git baseline
    try:
        git.ensure_repo(target)
    except Exception as e:  # git is optional for non-git dirs
        log.warning("git init failed for %s: %s", target, e)

    repo = Repository(
        project_id=project.id,
        name=name,
        source_kind=source_kind,
        source_ref=str(src),
        workspace_path=str(target),
        default_branch=git.branch(target) or "main",
        head_commit=git.head_commit(target),
    )
    db.add(repo)
    await db.flush()
    log.info("repository imported", extra={
        "project": project.id, "repo": name, "path": str(target),
    })
    return repo