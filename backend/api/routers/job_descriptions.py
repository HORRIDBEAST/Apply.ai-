"""
backend/api/routers/job_descriptions.py
=========================================
Job description capture + RAG ingestion.

STATUS: implemented ahead of its frontend — not yet called by any UI.
This is "option B" groundwork from frontend_context.md §5: the extension
will eventually POST the JD text it scrapes off an application page here,
and the website's future "Resume Score" / "Tailor Resume" features will
read from it. `Application.job_description_id` already exists as a
nullable FK, so wiring this in later needs no schema change.

Routes:
  POST   /job-descriptions/      — Create a JD record + ingest into Qdrant
  GET    /job-descriptions/      — List JDs for the user
  GET    /job-descriptions/{id}  — Get a single JD
  DELETE /job-descriptions/{id}  — Delete JD + its Qdrant vectors
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.core.auth import AuthenticatedUser
from backend.api.core.config import settings
from backend.api.core.logging import get_logger
from backend.api.db.qdrant_client import QdrantHelper, get_qdrant
from backend.api.db.redis_client import get_redis
from backend.api.db.session import get_db
from backend.api.db.models import ATSPlatform, JobDescription
from backend.api.rag.ingestion import ingest_job_description

logger = get_logger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class JobDescriptionCreateRequest(BaseModel):
    raw_text: str = Field(..., min_length=1)
    company_name: str | None = None
    role_title: str | None = None
    source_url: str | None = None
    platform: ATSPlatform = ATSPlatform.UNKNOWN


class JobDescriptionResponse(BaseModel):
    id: uuid.UUID
    company_name: str | None
    role_title: str | None
    source_url: str | None
    platform: str
    raw_text: str | None
    parsed_json: dict | None
    created_at: datetime

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/", response_model=JobDescriptionResponse, status_code=status.HTTP_201_CREATED)
async def create_job_description(
    body: JobDescriptionCreateRequest,
    current_user: AuthenticatedUser,
    db: AsyncSession = Depends(get_db),
    qdrant=Depends(get_qdrant),
    redis=Depends(get_redis),
) -> JobDescription:
    """Store a captured job description and ingest it into the RAG pipeline."""
    jd = JobDescription(
        user_id=uuid.UUID(current_user.user_id),
        company_name=body.company_name,
        role_title=body.role_title,
        source_url=body.source_url,
        platform=body.platform,
        raw_text=body.raw_text,
    )
    db.add(jd)
    await db.commit()
    await db.refresh(jd)

    await ingest_job_description(
        raw_text=body.raw_text,
        job_description_id=str(jd.id),
        user_id=current_user.user_id,
        company_name=body.company_name or "",
        role_title=body.role_title or "",
        platform=body.platform.value,
        db=db,
        qdrant=qdrant,
        redis=redis,
        replace_existing=False,
    )

    logger.info("Job description created", job_description_id=str(jd.id), user_id=current_user.user_id)
    # ingest_job_description()'s bulk UPDATE sets qdrant_point_ids + the
    # server-side updated_at via raw SQL — refresh before serialising so
    # we never read a stale in-memory copy (same MissingGreenlet pitfall
    # documented in routers/templates.py).
    await db.refresh(jd)
    return jd


@router.get("/", response_model=list[JobDescriptionResponse])
async def list_job_descriptions(
    current_user: AuthenticatedUser,
    db: AsyncSession = Depends(get_db),
) -> list[JobDescription]:
    result = await db.execute(
        select(JobDescription)
        .where(JobDescription.user_id == uuid.UUID(current_user.user_id))
        .order_by(JobDescription.created_at.desc())
    )
    return list(result.scalars().all())


@router.get("/{job_description_id}", response_model=JobDescriptionResponse)
async def get_job_description(
    job_description_id: uuid.UUID,
    current_user: AuthenticatedUser,
    db: AsyncSession = Depends(get_db),
) -> JobDescription:
    result = await db.execute(
        select(JobDescription).where(
            JobDescription.id == job_description_id,
            JobDescription.user_id == uuid.UUID(current_user.user_id),
        )
    )
    jd = result.scalar_one_or_none()
    if not jd:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job description not found")
    return jd


@router.delete("/{job_description_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_job_description(
    job_description_id: uuid.UUID,
    current_user: AuthenticatedUser,
    db: AsyncSession = Depends(get_db),
    qdrant=Depends(get_qdrant),
) -> None:
    result = await db.execute(
        select(JobDescription).where(
            JobDescription.id == job_description_id,
            JobDescription.user_id == uuid.UUID(current_user.user_id),
        )
    )
    jd = result.scalar_one_or_none()
    if not jd:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job description not found")

    await QdrantHelper.delete_by_source_id(
        qdrant, settings.QDRANT_COLLECTION_JOB_DESCRIPTIONS,
        source_field="job_description_id", source_id=str(job_description_id),
    )
    await db.delete(jd)
    await db.commit()
