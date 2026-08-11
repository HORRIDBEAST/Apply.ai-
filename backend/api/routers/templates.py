"""
backend/api/routers/templates.py
==================================
Job application template CRUD.

Routes:
  POST   /templates/           — Create a new template
  GET    /templates/           — List all templates for the user
  GET    /templates/{id}       — Get a single template
  PATCH  /templates/{id}       — Update template answers
  DELETE /templates/{id}       — Delete template + Qdrant vectors
  POST   /templates/{id}/set-default — Make this the default template
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.core.auth import AuthenticatedUser
from backend.api.core.logging import get_logger
from backend.api.db.qdrant_client import QdrantHelper, get_qdrant
from backend.api.db.redis_client import get_redis
from backend.api.db.session import get_db
from backend.api.models.models import Template
from backend.api.rag.ingestion import ingest_template

logger = get_logger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class TemplateCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=256)
    description: str | None = None
    answers_json: dict = Field(default_factory=dict)
    is_default: bool = False


class TemplateUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    answers_json: dict | None = None


class TemplateResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    answers_json: dict
    is_default: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/", response_model=TemplateResponse, status_code=status.HTTP_201_CREATED)
async def create_template(
    body: TemplateCreateRequest,
    current_user: AuthenticatedUser,
    db: AsyncSession = Depends(get_db),
    qdrant=Depends(get_qdrant),
    redis=Depends(get_redis),
) -> Template:
    """Create a new application template and ingest it into the RAG pipeline."""
    user_uuid = uuid.UUID(current_user.user_id)

    # If setting as default, unset any existing default first
    if body.is_default:
        await db.execute(
            update(Template)
            .where(Template.user_id == user_uuid, Template.is_default.is_(True))
            .values(is_default=False)
        )

    template = Template(
        user_id=user_uuid,
        name=body.name,
        description=body.description,
        answers_json=body.answers_json,
        is_default=body.is_default,
    )
    db.add(template)
    await db.commit()
    await db.refresh(template)

    # Ingest into RAG pipeline (non-blocking background would be preferred for large templates)
    await ingest_template(
        answers_json=template.answers_json,
        template_id=str(template.id),
        template_name=template.name,
        user_id=current_user.user_id,
        db=db,
        qdrant=qdrant,
        redis=redis,
        replace_existing=False,
    )

    logger.info("Template created", template_id=str(template.id), user_id=current_user.user_id)
    return template


@router.get("/", response_model=list[TemplateResponse])
async def list_templates(
    current_user: AuthenticatedUser,
    db: AsyncSession = Depends(get_db),
) -> list[Template]:
    result = await db.execute(
        select(Template)
        .where(Template.user_id == uuid.UUID(current_user.user_id))
        .order_by(Template.is_default.desc(), Template.created_at.desc())
    )
    return list(result.scalars().all())


@router.get("/{template_id}", response_model=TemplateResponse)
async def get_template(
    template_id: uuid.UUID,
    current_user: AuthenticatedUser,
    db: AsyncSession = Depends(get_db),
) -> Template:
    result = await db.execute(
        select(Template).where(
            Template.id == template_id,
            Template.user_id == uuid.UUID(current_user.user_id),
        )
    )
    t = result.scalar_one_or_none()
    if not t:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    return t


@router.patch("/{template_id}", response_model=TemplateResponse)
async def update_template(
    template_id: uuid.UUID,
    body: TemplateUpdateRequest,
    current_user: AuthenticatedUser,
    db: AsyncSession = Depends(get_db),
    qdrant=Depends(get_qdrant),
    redis=Depends(get_redis),
) -> Template:
    """Update template fields and re-ingest into Qdrant."""
    result = await db.execute(
        select(Template).where(
            Template.id == template_id,
            Template.user_id == uuid.UUID(current_user.user_id),
        )
    )
    t = result.scalar_one_or_none()
    if not t:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")

    if body.name is not None:
        t.name = body.name
    if body.description is not None:
        t.description = body.description
    if body.answers_json is not None:
        t.answers_json = body.answers_json

    await db.commit()
    await db.refresh(t)

    # Re-ingest with replace_existing=True to refresh stale vectors
    await ingest_template(
        answers_json=t.answers_json,
        template_id=str(t.id),
        template_name=t.name,
        user_id=current_user.user_id,
        db=db,
        qdrant=qdrant,
        redis=redis,
        replace_existing=True,
    )
    return t


@router.post("/{template_id}/set-default", response_model=TemplateResponse)
async def set_default_template(
    template_id: uuid.UUID,
    current_user: AuthenticatedUser,
    db: AsyncSession = Depends(get_db),
) -> Template:
    user_uuid = uuid.UUID(current_user.user_id)

    result = await db.execute(
        select(Template).where(Template.id == template_id, Template.user_id == user_uuid)
    )
    t = result.scalar_one_or_none()
    if not t:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")

    # Clear other defaults
    await db.execute(
        update(Template)
        .where(Template.user_id == user_uuid)
        .values(is_default=False)
    )
    t.is_default = True
    await db.commit()
    await db.refresh(t)
    return t


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(
    template_id: uuid.UUID,
    current_user: AuthenticatedUser,
    db: AsyncSession = Depends(get_db),
    qdrant=Depends(get_qdrant),
) -> None:
    result = await db.execute(
        select(Template).where(
            Template.id == template_id,
            Template.user_id == uuid.UUID(current_user.user_id),
        )
    )
    t = result.scalar_one_or_none()
    if not t:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")

    await QdrantHelper.delete_by_source_id(
        qdrant, settings.QDRANT_COLLECTION_TEMPLATES,
        source_field="template_id", source_id=str(template_id),
    )
    from backend.api.core.config import settings  # noqa: PLC0415
    await db.delete(t)
    await db.commit()