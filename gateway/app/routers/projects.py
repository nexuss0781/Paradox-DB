"""Projects CRUD — list, create, get, update, delete."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user
from ..database import get_db
from ..models import (
    Project, ParadoxDB, DatabaseVersion, DatabaseBackup, User,
    ProjectCreate, ProjectUpdate, ProjectResponse,
)

router = APIRouter(prefix="/v1/projects", tags=["projects"])


def _project_response(p, db_count=0) -> ProjectResponse:
    return ProjectResponse(
        id=str(p.id),
        name=p.name,
        description=p.description,
        database_count=db_count,
        created_at=p.created_at.isoformat() if p.created_at else "",
        updated_at=p.updated_at.isoformat() if p.updated_at else "",
    )


@router.get("", response_model=list[ProjectResponse])
async def list_projects(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Project).where(Project.user_id == user.id).order_by(Project.created_at.desc())
    )
    projects = result.scalars().all()
    responses = []
    for p in projects:
        count_result = await db.execute(
            select(func.count()).select_from(ParadoxDB).where(ParadoxDB.project_id == p.id)
        )
        db_count = count_result.scalar() or 0
        responses.append(_project_response(p, db_count))
    return responses


@router.post("", response_model=ProjectResponse, status_code=201)
async def create_project(
    body: ProjectCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project = Project(
        id=uuid.uuid4(),
        user_id=user.id,
        name=body.name,
        description=body.description,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(project)
    await db.flush()
    return _project_response(project, 0)


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Project).where(Project.id == project_id, Project.user_id == user.id)
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    count_result = await db.execute(
        select(func.count()).select_from(ParadoxDB).where(ParadoxDB.project_id == project.id)
    )
    db_count = count_result.scalar() or 0
    return _project_response(project, db_count)


@router.put("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: str,
    body: ProjectUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Project).where(Project.id == project_id, Project.user_id == user.id)
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if body.name is not None:
        project.name = body.name
    if body.description is not None:
        project.description = body.description
    project.updated_at = datetime.utcnow()
    await db.flush()

    count_result = await db.execute(
        select(func.count()).select_from(ParadoxDB).where(ParadoxDB.project_id == project.id)
    )
    db_count = count_result.scalar() or 0
    return _project_response(project, db_count)


@router.delete("/{project_id}")
async def delete_project(
    project_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Project).where(Project.id == project_id, Project.user_id == user.id)
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    dbs = await db.execute(
        select(ParadoxDB).where(ParadoxDB.project_id == project.id)
    )
    for db_rec in dbs.scalars().all():
        await db.execute(delete(DatabaseVersion).where(DatabaseVersion.db_id == db_rec.id))
        await db.execute(delete(DatabaseBackup).where(DatabaseBackup.db_id == db_rec.id))
        await db.delete(db_rec)

    await db.delete(project)
    return {"detail": "Project deleted"}
