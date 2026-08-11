"""
backend/api/routers/applications.py
=====================================
Application history and answer endpoints.

Routes:
  POST   /applications/                         — Create an application session record
  GET    /applications/                         — List applications (paginated + filtered)
  GET    /applications/{id}                     — Get application detail with answers
  PATCH  /applications/{id}/status              — Update application status
  POST   /applications/{id}/answers             — Bulk-save field answers
  POST   /applications/{id}/answers/{ans_id}/ingest — Ingest answer into RAG memory
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.api.core.auth import AuthenticatedUser
from backend.api.core.logging import get_logger
from backend.api.db.qdrant_client import get_qdrant
from backend.api.db.redis_client import get_redis
from backend.api.db.session import get_db
from backend.api.models.models import (
    Application,
    ApplicationAnswer,
    ApplicationStatus,
    FormField,
)
from backend.api.rag.ingestion import ingest_past_answer

logger = get_logger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class CreateApplicationRequest(BaseModel):
    job_description_id: uuid.UUID | None = None
    template_id: uuid.UUID | None = None
    resume_id: uuid.UUID | None = None
    platform: str = "unknown"
    apply_url: str | None = None
    session_meta: dict | None = None


class ApplicationSummaryResponse(BaseModel):
    id: uuid.UUID
    platform: str
    apply_url: str | None
    status: str
    fields_filled_count: int
    ai_answers_count: int
    created_at: datetime
    submitted_at: datetime | None

    class Config:
        from_attributes = True


class UpdateStatusRequest(BaseModel):
    status: ApplicationStatus


class SaveAnswerRequest(BaseModel):
    form_field_id: uuid.UUID
    answer_text: str | None
    source: str = "template"
    confidence_score: float | None = None
    was_edited: bool = False


class BulkSaveAnswersRequest(BaseModel):
    answers: list[SaveAnswerRequest]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/", response_model=ApplicationSummaryResponse, status_code=status.HTTP_201_CREATED)
async def create_application(
    body: CreateApplicationRequest,
    current_user: AuthenticatedUser,
    db: AsyncSession = Depends(get_db),
) -> Application:
    """
    Create an application session record when the extension detects a form.
    Called immediately on form detection — before any fields are filled.
    """
    app = Application(
        user_id=uuid.UUID(current_user.user_id),
        job_description_id=body.job_description_id,
        template_id=body.template_id,
        resume_id=body.resume_id,
        platform=body.platform,
        apply_url=body.apply_url,
        session_meta=body.session_meta,
        status=ApplicationStatus.DETECTED,
    )
    db.add(app)
    await db.commit()
    await db.refresh(app)
    return app


@router.get("/", response_model=dict)
async def list_applications(
    current_user: AuthenticatedUser,
    db: AsyncSession = Depends(get_db),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status_filter: ApplicationStatus | None = Query(default=None, alias="status"),
    platform_filter: str | None = Query(default=None, alias="platform"),
) -> dict:
    """
    Paginated application history.
    Supports filtering by status and ATS platform.

    Returns:
        {
            "total": int,
            "page": int,
            "page_size": int,
            "items": [ApplicationSummaryResponse, ...]
        }
    """
    user_uuid = uuid.UUID(current_user.user_id)
    base_query = select(Application).where(Application.user_id == user_uuid)

    if status_filter:
        base_query = base_query.where(Application.status == status_filter)
    if platform_filter:
        base_query = base_query.where(Application.platform == platform_filter)

    # Total count (without pagination)
    count_result = await db.execute(
        select(func.count()).select_from(base_query.subquery())
    )
    total = count_result.scalar_one()

    # Paginated results
    result = await db.execute(
        base_query
        .order_by(Application.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = list(result.scalars().all())

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [ApplicationSummaryResponse.model_validate(a).model_dump() for a in items],
    }


@router.get("/{application_id}")
async def get_application(
    application_id: uuid.UUID,
    current_user: AuthenticatedUser,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get an application with all its answers eagerly loaded."""
    result = await db.execute(
        select(Application)
        .where(
            Application.id == application_id,
            Application.user_id == uuid.UUID(current_user.user_id),
        )
        .options(
            selectinload(Application.answers),
            selectinload(Application.form_fields),
        )
    )
    app = result.scalar_one_or_none()
    if not app:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found")

    return {
        "id": str(app.id),
        "platform": app.platform,
        "apply_url": app.apply_url,
        "status": app.status,
        "fields_filled_count": app.fields_filled_count,
        "ai_answers_count": app.ai_answers_count,
        "notes": app.notes,
        "created_at": app.created_at.isoformat(),
        "submitted_at": app.submitted_at.isoformat() if app.submitted_at else None,
        "answers": [
            {
                "id": str(a.id),
                "form_field_id": str(a.form_field_id),
                "answer_text": a.answer_text,
                "source": a.source,
                "confidence_score": a.confidence_score,
                "was_edited": a.was_edited,
            }
            for a in app.answers
        ],
    }


@router.patch("/{application_id}/status", response_model=ApplicationSummaryResponse)
async def update_application_status(
    application_id: uuid.UUID,
    body: UpdateStatusRequest,
    current_user: AuthenticatedUser,
    db: AsyncSession = Depends(get_db),
) -> Application:
    """Update the application lifecycle status."""
    result = await db.execute(
        select(Application).where(
            Application.id == application_id,
            Application.user_id == uuid.UUID(current_user.user_id),
        )
    )
    app = result.scalar_one_or_none()
    if not app:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found")

    app.status = body.status
    if body.status == ApplicationStatus.SUBMITTED:
        from datetime import datetime, timezone  # noqa: PLC0415
        app.submitted_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(app)
    return app


@router.post("/{application_id}/answers", status_code=status.HTTP_201_CREATED)
async def bulk_save_answers(
    application_id: uuid.UUID,
    body: BulkSaveAnswersRequest,
    current_user: AuthenticatedUser,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Bulk-upsert field answers for an application session.
    Called by the extension after autofill completes.
    """
    user_uuid = uuid.UUID(current_user.user_id)

    # Verify application ownership
    app_result = await db.execute(
        select(Application).where(
            Application.id == application_id,
            Application.user_id == user_uuid,
        )
    )
    app = app_result.scalar_one_or_none()
    if not app:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found")

    saved_count = 0
    ai_count = 0

    for answer_req in body.answers:
        # Check for existing answer to this field (upsert semantics)
        existing_result = await db.execute(
            select(ApplicationAnswer).where(
                ApplicationAnswer.application_id == application_id,
                ApplicationAnswer.form_field_id == answer_req.form_field_id,
            )
        )
        answer = existing_result.scalar_one_or_none()

        if answer:
            answer.answer_text = answer_req.answer_text
            answer.source = answer_req.source
            answer.confidence_score = answer_req.confidence_score
            answer.was_edited = answer_req.was_edited
        else:
            answer = ApplicationAnswer(
                application_id=application_id,
                form_field_id=answer_req.form_field_id,
                user_id=user_uuid,
                answer_text=answer_req.answer_text,
                source=answer_req.source,
                confidence_score=answer_req.confidence_score,
                was_edited=answer_req.was_edited,
            )
            db.add(answer)

        saved_count += 1
        if answer_req.source == "ai_generated":
            ai_count += 1

    # Update aggregated counters on the application row
    app.fields_filled_count = saved_count
    app.ai_answers_count = ai_count
    app.status = ApplicationStatus.AUTOFILLED

    await db.commit()
    return {"saved": saved_count, "ai_generated": ai_count}


@router.post("/{application_id}/answers/{answer_id}/ingest", status_code=status.HTTP_202_ACCEPTED)
async def ingest_answer_into_rag(
    application_id: uuid.UUID,
    answer_id: uuid.UUID,
    current_user: AuthenticatedUser,
    db: AsyncSession = Depends(get_db),
    qdrant=Depends(get_qdrant),
    redis=Depends(get_redis),
) -> dict:
    """
    Ingest a specific answer into the RAG past-answers collection.
    Called after application submission to build up the memory store.
    """
    result = await db.execute(
        select(ApplicationAnswer)
        .where(
            ApplicationAnswer.id == answer_id,
            ApplicationAnswer.application_id == application_id,
            ApplicationAnswer.user_id == uuid.UUID(current_user.user_id),
        )
        .options(selectinload(ApplicationAnswer.form_field))
    )
    answer = result.scalar_one_or_none()
    if not answer:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Answer not found")

    if not answer.answer_text:
        return {"status": "skipped", "reason": "empty answer text"}

    label = answer.form_field.label_text if answer.form_field else "Unknown question"

    point_ids = await ingest_past_answer(
        answer_text=answer.answer_text,
        question_label=label or "Unknown question",
        application_answer_id=str(answer.id),
        application_id=str(application_id),
        form_field_key=answer.form_field.mapped_field_key if answer.form_field else None,
        answer_source=answer.source,
        user_id=current_user.user_id,
        db=db,
        qdrant=qdrant,
        redis=redis,
    )

    return {"status": "ingested", "point_ids": point_ids}