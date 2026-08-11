"""
backend/api/rag/ingestion.py
============================
RAG ingestion pipeline — the write side of the RAG system.

Responsibilities:
  1. Accept parsed content (resume, past answers, JD, template)
  2. Chunk the content via chunker.py
  3. Embed each chunk via embeddings.py (with Redis caching)
  4. Upsert vectors + payloads into Qdrant (idempotent)
  5. Update the PostgreSQL record with the list of Qdrant point IDs

All ingest functions are async and designed to be called from:
  - Celery background tasks (primary path for large uploads)
  - FastAPI route handlers directly (acceptable for small payloads)

Zero-hallucination guarantee:
  Nothing in this module generates or modifies content.
  It only chunks, embeds, and stores exactly what it receives.
"""

from __future__ import annotations

import uuid
from typing import Any

from qdrant_client import AsyncQdrantClient
from qdrant_client import models as qmodels
from redis.asyncio import Redis
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.core.config import settings
from backend.api.core.logging import get_logger
from backend.api.db.qdrant_client import QdrantHelper
from backend.api.models.models import (
    ApplicationAnswer,
    JobDescription,
    ParsedResumeData,
    Template,
)
from backend.api.rag.chunker import (
    TextChunk,
    chunk_parsed_resume,
    chunk_past_answer,
    chunk_template,
    chunk_text,
)
from backend.api.rag.embeddings import embed_batch

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Internal: chunk → embed → upsert (shared by all ingest functions)
# ---------------------------------------------------------------------------

async def _embed_and_upsert(
    chunks: list[TextChunk],
    collection_name: str,
    qdrant: AsyncQdrantClient,
    redis: Redis,
) -> list[str]:
    """
    Embed a list of chunks and upsert them into a Qdrant collection.

    Returns the list of point IDs (as str) that were upserted,
    which the caller stores in PostgreSQL for future reference.

    Args:
        chunks:          Pre-built TextChunk objects from the chunker.
        collection_name: Target Qdrant collection.
        qdrant:          Async Qdrant client.
        redis:           Async Redis client (used by embed_batch for caching).

    Returns:
        List of chunk UUID strings in the same order as `chunks`.
    """
    if not chunks:
        return []

    # 1. Embed all chunk texts in a single batched API call
    texts = [c.text for c in chunks]
    vectors = await embed_batch(texts, redis)

    # 2. Build Qdrant PointStruct objects
    points: list[qmodels.PointStruct] = []
    for chunk, vector in zip(chunks, vectors):
        points.append(
            qmodels.PointStruct(
                id=str(chunk.chunk_id),   # Qdrant accepts str UUIDs
                vector=vector,
                payload=chunk.payload,
            )
        )

    # 3. Upsert into Qdrant (idempotent — same chunk_id = same point ID)
    await QdrantHelper.upsert_vectors(qdrant, collection_name, points)

    point_ids = [str(c.chunk_id) for c in chunks]
    logger.info(
        "Ingestion complete",
        collection=collection_name,
        chunks=len(chunks),
    )
    return point_ids


# ---------------------------------------------------------------------------
# 1. Resume ingestion
# ---------------------------------------------------------------------------

async def ingest_resume(
    parsed_json: dict[str, Any],
    resume_id: str,
    user_id: str,
    db: AsyncSession,
    qdrant: AsyncQdrantClient,
    redis: Redis,
    replace_existing: bool = True,
) -> list[str]:
    """
    Ingest a parsed resume into the RAG pipeline.

    Steps:
      1. Delete stale resume chunks (if replace_existing=True)
      2. Chunk the parsed JSON by section
      3. Embed + upsert into Qdrant (resume_chunks collection)
      4. Update parsed_resume_data.qdrant_point_ids in PostgreSQL

    Args:
        parsed_json:       Output of ResumeExtractorAgent.
        resume_id:         UUID string of the `resumes` table row.
        user_id:           Owner UUID string.
        db:                Async SQLAlchemy session.
        qdrant:            Async Qdrant client.
        redis:             Async Redis client.
        replace_existing:  If True, delete old chunks before inserting new ones.
                           Set to False only during initial import.

    Returns:
        List of Qdrant point ID strings stored for this resume.
    """
    collection = settings.QDRANT_COLLECTION_RESUME

    # 1. Remove stale embeddings so we don't accumulate orphaned vectors
    if replace_existing:
        await QdrantHelper.delete_by_source_id(
            qdrant, collection, source_field="resume_id", source_id=resume_id
        )

    # 2. Chunk the parsed resume
    chunks = chunk_parsed_resume(parsed_json, user_id, resume_id)

    # 3. Embed + upsert
    point_ids = await _embed_and_upsert(chunks, collection, qdrant, redis)

    # 4. Persist point IDs in PostgreSQL so we can reference/delete them later
    await db.execute(
        update(ParsedResumeData)
        .where(ParsedResumeData.resume_id == uuid.UUID(resume_id))
        .values(qdrant_point_ids=point_ids)
    )
    await db.commit()

    logger.info(
        "Resume ingested into RAG",
        resume_id=resume_id,
        user_id=user_id,
        point_count=len(point_ids),
    )
    return point_ids


# ---------------------------------------------------------------------------
# 2. Past answer ingestion
# ---------------------------------------------------------------------------

async def ingest_past_answer(
    answer_text: str,
    question_label: str,
    application_answer_id: str,
    application_id: str,
    form_field_key: str | None,
    answer_source: str,
    user_id: str,
    db: AsyncSession,
    qdrant: AsyncQdrantClient,
    redis: Redis,
) -> list[str]:
    """
    Ingest a single past application answer into the RAG memory store.

    Called after a user submits an application so future fill sessions
    can retrieve this answer as context.

    Args:
        answer_text:            The final submitted answer text.
        question_label:         Raw label of the form field this answered.
        application_answer_id:  UUID of the application_answers row.
        application_id:         UUID of the parent application.
        form_field_key:         Canonical field key (e.g. "why_us"), may be None.
        answer_source:          "template" | "ai_generated" | "user_override" | etc.
        user_id:                Owner UUID string.
        db:                     Async SQLAlchemy session.
        qdrant:                 Async Qdrant client.
        redis:                  Async Redis client.

    Returns:
        List of Qdrant point ID strings (usually a single item).
    """
    collection = settings.QDRANT_COLLECTION_PAST_ANSWERS

    chunks = chunk_past_answer(
        answer_text=answer_text,
        question_label=question_label,
        application_answer_id=application_answer_id,
        application_id=application_id,
        form_field_key=form_field_key,
        answer_source=answer_source,
        user_id=user_id,
    )

    if not chunks:
        return []

    point_ids = await _embed_and_upsert(chunks, collection, qdrant, redis)

    # Persist the Qdrant point IDs back to the application_answers row
    await db.execute(
        update(ApplicationAnswer)
        .where(ApplicationAnswer.id == uuid.UUID(application_answer_id))
        .values(qdrant_point_id=point_ids[0] if point_ids else None)
    )
    await db.commit()

    logger.info(
        "Past answer ingested into RAG",
        application_answer_id=application_answer_id,
        user_id=user_id,
    )
    return point_ids


# ---------------------------------------------------------------------------
# 3. Job description ingestion
# ---------------------------------------------------------------------------

async def ingest_job_description(
    raw_text: str,
    job_description_id: str,
    user_id: str,
    company_name: str,
    role_title: str,
    platform: str,
    db: AsyncSession,
    qdrant: AsyncQdrantClient,
    redis: Redis,
    replace_existing: bool = True,
) -> list[str]:
    """
    Ingest a job description into the RAG pipeline.

    The JD is chunked as plain text (no section-aware splitting needed)
    since JDs have varied formats.

    Args:
        raw_text:             Full plain text of the job description.
        job_description_id:   UUID of the job_descriptions row.
        user_id:              Owner UUID string.
        company_name:         For payload metadata / filtering.
        role_title:           For payload metadata / filtering.
        platform:             ATS platform string.
        db:                   Async SQLAlchemy session.
        qdrant:               Async Qdrant client.
        redis:                Async Redis client.
        replace_existing:     Delete old JD chunks before inserting new ones.

    Returns:
        List of Qdrant point ID strings.
    """
    collection = settings.QDRANT_COLLECTION_JOB_DESCRIPTIONS

    if replace_existing:
        await QdrantHelper.delete_by_source_id(
            qdrant, collection,
            source_field="job_description_id",
            source_id=job_description_id,
        )

    chunks = chunk_text(
        text=raw_text,
        user_id=user_id,
        source_id=job_description_id,
        source_type="job_description",
        base_payload={
            "job_description_id": job_description_id,
            "company_name": company_name,
            "role_title": role_title,
            "platform": platform,
        },
    )

    point_ids = await _embed_and_upsert(chunks, collection, qdrant, redis)

    # Persist point IDs in PostgreSQL
    await db.execute(
        update(JobDescription)
        .where(JobDescription.id == uuid.UUID(job_description_id))
        .values(qdrant_point_ids=point_ids)
    )
    await db.commit()

    logger.info(
        "Job description ingested into RAG",
        job_description_id=job_description_id,
        user_id=user_id,
        company=company_name,
        role=role_title,
        point_count=len(point_ids),
    )
    return point_ids


# ---------------------------------------------------------------------------
# 4. Template ingestion
# ---------------------------------------------------------------------------

async def ingest_template(
    answers_json: dict[str, Any],
    template_id: str,
    template_name: str,
    user_id: str,
    db: AsyncSession,
    qdrant: AsyncQdrantClient,
    redis: Redis,
    replace_existing: bool = True,
) -> list[str]:
    """
    Ingest a job application template into the RAG pipeline.

    Templates are chunked per field/question pair so the retriever can
    surface the most relevant pre-written answer for a specific question.

    Args:
        answers_json:   Template answers dict.
        template_id:    UUID of the templates row.
        template_name:  Display name (for payload metadata).
        user_id:        Owner UUID string.
        db:             Async SQLAlchemy session.
        qdrant:         Async Qdrant client.
        redis:          Async Redis client.
        replace_existing: Delete old template chunks first.

    Returns:
        List of Qdrant point ID strings.
    """
    collection = settings.QDRANT_COLLECTION_TEMPLATES

    if replace_existing:
        await QdrantHelper.delete_by_source_id(
            qdrant, collection,
            source_field="template_id",
            source_id=template_id,
        )

    chunks = chunk_template(
        answers_json=answers_json,
        template_id=template_id,
        template_name=template_name,
        user_id=user_id,
    )

    point_ids = await _embed_and_upsert(chunks, collection, qdrant, redis)

    # Persist point IDs in PostgreSQL
    await db.execute(
        update(Template)
        .where(Template.id == uuid.UUID(template_id))
        .values(qdrant_point_ids=point_ids)
    )
    await db.commit()

    logger.info(
        "Template ingested into RAG",
        template_id=template_id,
        user_id=user_id,
        template_name=template_name,
        point_count=len(point_ids),
    )
    return point_ids


# ---------------------------------------------------------------------------
# 5. GDPR / account deletion: purge all user vectors
# ---------------------------------------------------------------------------

async def purge_user_vectors(
    user_id: str,
    qdrant: AsyncQdrantClient,
) -> None:
    """
    Hard-delete all Qdrant vectors owned by a user across all collections.

    Called during account deletion to comply with GDPR right-to-erasure.
    PostgreSQL records are deleted via CASCADE at the DB level.
    """
    for collection in (
        settings.QDRANT_COLLECTION_RESUME,
        settings.QDRANT_COLLECTION_PAST_ANSWERS,
        settings.QDRANT_COLLECTION_JOB_DESCRIPTIONS,
        settings.QDRANT_COLLECTION_TEMPLATES,
    ):
        await QdrantHelper.delete_by_user(qdrant, collection, user_id)

    logger.info("All user vectors purged from Qdrant", user_id=user_id)