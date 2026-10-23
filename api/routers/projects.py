"""Project & repository management."""
from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import func, select

from api.deps import CurrentUser, DbSession
from core.audit import audit
from core.errors import ConflictError, NotFoundError, ValidationError
from core.models import Project, Repository, Task, TaskRun
from core.schemas import (
    Page,
    ProjectCreate,
    ProjectOut,
    ProjectUpdate,
    RepositoryImportRequest,
    RepositoryOut,
)
from services.indexing.ingest import import_repository
from services.indexing.indexer import index_repository

router = APIRouter(prefix="/projects", tags=["projects"])


async def _project_out(db, project: Project) -> ProjectOut:
    repo_count = (
        await db.execute(
            select(func.count(Repository.id)).where(Repository.project_id == project.id)
        )
    ).scalar_one()
    task_count = (
        await db.execute(select(func.count(Task.id)).where(Task.project_id == project.id))
    ).scalar_one()
    latest_state = (
        await db.execute(
            select(Task.status).where(Task.project_id == project.id).order_by(Task.updated_at.desc()).limit(1)
        )
    ).scalar_one_or_none()
    return ProjectOut(
        id=project.id,
        name=project.name,
        description=project.description,
        status=project.status,
        created_at=project.created_at,
        updated_at=project.updated_at,
        repository_count=repo_count,
        task_count=task_count,
        latest_task_state=latest_state,
    )


@router.get("", response_model=Page)
async def list_projects(user: CurrentUser, db: DbSession,
                        limit: int = 50, offset: int = 0):
    limit = min(max(limit, 1), 200)
    offset = max(offset, 0)
    total = (
        await db.execute(
            select(func.count(Project.id)).where(Project.owner_id == user.id)
        )
    ).scalar_one()
    rows = (
        (
            await db.execute(
                select(Project)
                .where(Project.owner_id == user.id)
                .order_by(Project.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    items = [await _project_out(db, p) for p in rows]
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.post("", response_model=ProjectOut, status_code=201)
async def create_project(body: ProjectCreate, user: CurrentUser, db: DbSession):
    project = Project(owner_id=user.id, name=body.name, description=body.description)
    db.add(project)
    await db.flush()
    audit("project_created", user_id=user.id, project_id=project.id)
    return await _project_out(db, project)


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(project_id: str, user: CurrentUser, db: DbSession):
    project = (
        await db.execute(
            select(Project).where(Project.id == project_id, Project.owner_id == user.id)
        )
    ).scalar_one_or_none()
    if project is None:
        raise NotFoundError("Project not found")
    return await _project_out(db, project)


@router.patch("/{project_id}", response_model=ProjectOut)
async def update_project(project_id: str, body: ProjectUpdate,
                         user: CurrentUser, db: DbSession):
    project = (
        await db.execute(
            select(Project).where(Project.id == project_id, Project.owner_id == user.id)
        )
    ).scalar_one_or_none()
    if project is None:
        raise NotFoundError("Project not found")
    if body.name is not None:
        project.name = body.name
    if body.description is not None:
        project.description = body.description
    return await _project_out(db, project)


@router.delete("/{project_id}", status_code=204)
async def delete_project(project_id: str, user: CurrentUser, db: DbSession):
    project = (
        await db.execute(
            select(Project).where(Project.id == project_id, Project.owner_id == user.id)
        )
    ).scalar_one_or_none()
    if project is None:
        raise NotFoundError("Project not found")
    await db.delete(project)
    audit("project_deleted", user_id=user.id, project_id=project_id)
    return None


# --- repositories -----------------------------------------------------------


@router.get("/{project_id}/repositories", response_model=list[RepositoryOut])
async def list_repositories(project_id: str, user: CurrentUser, db: DbSession):
    project = (
        await db.execute(
            select(Project).where(Project.id == project_id, Project.owner_id == user.id)
        )
    ).scalar_one_or_none()
    if project is None:
        raise NotFoundError("Project not found")
    rows = (
        (
            await db.execute(
                select(Repository).where(Repository.project_id == project_id)
            )
        )
        .scalars()
        .all()
    )
    return [
        RepositoryOut(
            id=r.id, name=r.name, source_kind=r.source_kind, source_ref=r.source_ref,
            default_branch=r.default_branch, head_commit=r.head_commit,
            indexed=r.indexed, index_version=r.index_version, file_count=r.file_count,
            total_bytes=r.total_bytes, languages=r.languages or {}, created_at=r.created_at,
        )
        for r in rows
    ]


@router.post("/{project_id}/repositories", response_model=RepositoryOut, status_code=201)
async def import_repo(project_id: str, body: RepositoryImportRequest,
                      user: CurrentUser, db: DbSession):
    project = (
        await db.execute(
            select(Project).where(Project.id == project_id, Project.owner_id == user.id)
        )
    ).scalar_one_or_none()
    if project is None:
        raise NotFoundError("Project not found")
    existing = (
        await db.execute(
            select(Repository).where(
                Repository.project_id == project_id, Repository.name == body.name
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        from pathlib import Path
        if not Path(existing.workspace_path).is_dir():
            # Workspace missing on disk (e.g. after container redeploy): replace stale record
            await db.delete(existing)
            await db.flush()
        else:
            raise ConflictError(f"Repository '{body.name}' already exists in this project")

    try:
        repo = await import_repository(
            db, project, name=body.name, source_kind=body.source_kind,
            source_ref=body.source_ref,
        )
    except ValidationError as e:
        raise e
    except Exception as e:  # import_repository raises ValidationError for bad sources
        raise ValidationError(f"Repository import failed: {e}") from e

    if body.index:
        await index_repository(db, repo)
    audit("repository_imported", user_id=user.id, project_id=project_id,
          detail={"repo": repo.name, "files": repo.file_count})
    return RepositoryOut(
        id=repo.id, name=repo.name, source_kind=repo.source_kind,
        source_ref=repo.source_ref, default_branch=repo.default_branch,
        head_commit=repo.head_commit, indexed=repo.indexed,
        index_version=repo.index_version, file_count=repo.file_count,
        total_bytes=repo.total_bytes, languages=repo.languages or {},
        created_at=repo.created_at,
    )


@router.post("/{project_id}/repositories/{repo_id}/reindex", response_model=RepositoryOut)
async def reindex_repo(project_id: str, repo_id: str, user: CurrentUser, db: DbSession):
    project = (
        await db.execute(
            select(Project).where(Project.id == project_id, Project.owner_id == user.id)
        )
    ).scalar_one_or_none()
    if project is None:
        raise NotFoundError("Project not found")
    repo = (
        await db.execute(
            select(Repository).where(
                Repository.id == repo_id, Repository.project_id == project_id
            )
        )
    ).scalar_one_or_none()
    if repo is None:
        raise NotFoundError("Repository not found")
    await index_repository(db, repo)
    audit("repository_reindexed", user_id=user.id, project_id=project_id,
          detail={"repo": repo.name, "files": repo.file_count})
    return RepositoryOut(
        id=repo.id, name=repo.name, source_kind=repo.source_kind,
        source_ref=repo.source_ref, default_branch=repo.default_branch,
        head_commit=repo.head_commit, indexed=repo.indexed,
        index_version=repo.index_version, file_count=repo.file_count,
        total_bytes=repo.total_bytes, languages=repo.languages or {},
        created_at=repo.created_at,
    )


@router.delete("/{project_id}/repositories/{repo_id}", status_code=204)
async def delete_repo(project_id: str, repo_id: str, user: CurrentUser, db: DbSession):
    project = (
        await db.execute(
            select(Project).where(Project.id == project_id, Project.owner_id == user.id)
        )
    ).scalar_one_or_none()
    if project is None:
        raise NotFoundError("Project not found")
    repo = (
        await db.execute(
            select(Repository).where(
                Repository.id == repo_id, Repository.project_id == project_id
            )
        )
    ).scalar_one_or_none()
    if repo is None:
        raise NotFoundError("Repository not found")
    await db.delete(repo)
    audit("repository_deleted", user_id=user.id, project_id=project_id,
          detail={"repo": repo.name})
    return None