"""
backend/api/workers/tasks.py
==============================
Celery task definitions for CPU/IO-heavy background work.

Current tasks:
  - parse_resume_task     — Full resume parse pipeline (ResumeExtractorAgent)
  - ingest_resume_task    — Embed + store parsed resume into Qdrant
  - ingest_jd_task        — Embed + store job description into Qdrant
  - ingest_answers_task   — Batch-ingest submitted answers into Qdrant

Design:
  - All tasks use Celery's bind=True to access self.retry()
  - Exponential backoff on retryable failures (OpenAI rate limits, S3 errors)
  - Tasks are idempotent — safe to retry on transient failures
  - No user-facing data is generated inside workers — only pipeline calls
"""

from __future__ import annotations

import asyncio
import logging
from functools import wraps

from celery import Celery

from backend.api.core.config import settings
from backend.api.core.logging import configure_logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Celery application
# ---------------------------------------------------------------------------

celery_app = Celery(
    "autofill_worker",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
)

celery_app.conf.update(
    # Serialisation
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # Timezone
    timezone="UTC",
    enable_utc=True,
    # Retry policy
    task_acks_late=True,           # Ack only after task completes (prevents loss on crash)
    task_reject_on_worker_lost=True,
    # Concurrency — use gevent or prefork depending on deployment
    worker_prefetch_multiplier=1,  # fetch one task at a time (fair scheduling)
    # Result expiry — keep results for 1 hour
    result_expires=3600,
    # Route heavy tasks to dedicated queues
    task_routes={
        "backend.api.workers.tasks.parse_resume_task": {"queue": "parsing"},
        "backend.api.workers.tasks.ingest_resume_task": {"queue": "ingestion"},
        "backend.api.workers.tasks.ingest_jd_task": {"queue": "ingestion"},
        "backend.api.workers.tasks.ingest_answers_task": {"queue": "ingestion"},
    },
    task_queues={
        "parsing": {},
        "ingestion": {},
        "default": {},
    },
    task_default_queue="default",
)


# ---------------------------------------------------------------------------
# Helper: run async coroutines from synchronous Celery task context
# ---------------------------------------------------------------------------

def _run_async(coro):
    """Execute an async coroutine from a sync Celery task context."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

@celery_app.task(
    bind=True,
    name="backend.api.workers.tasks.parse_resume_task",
    max_retries=3,
    default_retry_delay=60,   # seconds
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
)
def parse_resume_task(self, resume_id: str, user_id: str) -> dict:
    """
    Background task: parse a resume file and ingest into Qdrant.

    Steps:
      1. Download encrypted file from S3
      2. Decrypt file
      3. Call ResumeExtractorAgent to extract structured JSON
      4. Save ParsedResumeData to PostgreSQL
      5. Call ingest_resume() to embed + store in Qdrant
      6. Update resume.parse_status = "complete"
    """
    configure_logging()
    logger.info("parse_resume_task started", extra={"resume_id": resume_id})

    async def _run():
        from backend.api.db.session import AsyncSessionLocal
        from backend.api.db.redis_client import init_redis_pool, close_redis_pool, get_redis
        from backend.api.db.qdrant_client import init_qdrant_client, close_qdrant_client, get_qdrant
        from backend.api.models.models import Resume, ParsedResumeData
        from backend.api.rag.ingestion import ingest_resume
        from sqlalchemy import select, update
        import uuid

        # Initialise connections (each Celery worker has its own event loop)
        await init_redis_pool()
        await init_qdrant_client()

        redis = await get_redis()
        qdrant = await get_qdrant()

        try:
            async with AsyncSessionLocal() as db:
                # Fetch resume record
                result = await db.execute(
                    select(Resume).where(Resume.id == uuid.UUID(resume_id))
                )
                resume = result.scalar_one_or_none()
                if not resume:
                    logger.error("Resume not found in parse task", extra={"resume_id": resume_id})
                    return {"status": "error", "reason": "resume_not_found"}

                # Mark as processing
                await db.execute(
                    update(Resume)
                    .where(Resume.id == uuid.UUID(resume_id))
                    .values(parse_status="processing")
                )
                await db.commit()

                # --- Phase 1: Download + decrypt file from S3 ---
                from backend.api.routers.resumes import _get_s3_client  # noqa: PLC0415
                from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: PLC0415
                import base64  # noqa: PLC0415

                s3 = _get_s3_client()
                s3_obj = s3.get_object(Bucket=settings.S3_BUCKET_RESUMES, Key=resume.storage_key)
                encrypted_payload = s3_obj["Body"].read()

                nonce = encrypted_payload[:12]
                ciphertext = encrypted_payload[12:]
                key_bytes = base64.urlsafe_b64decode(settings.RESUME_ENCRYPTION_KEY + "==")
                aesgcm = AESGCM(key_bytes)
                plaintext_bytes = aesgcm.decrypt(
                    nonce, ciphertext,
                    associated_data=(resume.content_hash or "").encode(),
                )

                # --- Phase 2: Extract structured data via ResumeExtractorAgent ---
                # (Agent is defined in backend/agents/resume_extractor.py — Step 3)
                from backend.agents.resume_extractor import ResumeExtractorAgent  # noqa: PLC0415
                agent = ResumeExtractorAgent()
                parsed_json = await agent.extract(
                    file_bytes=plaintext_bytes,
                    mime_type=resume.mime_type,
                )

                # --- Phase 3: Save ParsedResumeData ---
                existing_parsed = await db.execute(
                    select(ParsedResumeData).where(
                        ParsedResumeData.resume_id == uuid.UUID(resume_id)
                    )
                )
                parsed_row = existing_parsed.scalar_one_or_none()

                if parsed_row:
                    parsed_row.parsed_json = parsed_json
                    parsed_row.parse_status = "complete"
                else:
                    parsed_row = ParsedResumeData(
                        resume_id=uuid.UUID(resume_id),
                        parsed_json=parsed_json,
                        parser_model=settings.OPENAI_MODEL,
                        parser_version="1.0",
                    )
                    db.add(parsed_row)

                await db.commit()

                # --- Phase 4: Ingest into Qdrant ---
                await ingest_resume(
                    parsed_json=parsed_json,
                    resume_id=resume_id,
                    user_id=user_id,
                    db=db,
                    qdrant=qdrant,
                    redis=redis,
                    replace_existing=True,
                )

                # --- Phase 5: Mark as complete ---
                await db.execute(
                    update(Resume)
                    .where(Resume.id == uuid.UUID(resume_id))
                    .values(parse_status="complete")
                )
                await db.commit()

                logger.info("parse_resume_task complete", extra={"resume_id": resume_id})
                return {"status": "complete", "resume_id": resume_id}

        finally:
            await close_redis_pool()
            await close_qdrant_client()

    return _run_async(_run())


@celery_app.task(
    bind=True,
    name="backend.api.workers.tasks.ingest_jd_task",
    max_retries=3,
    default_retry_delay=30,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def ingest_jd_task(
    self,
    job_description_id: str,
    raw_text: str,
    user_id: str,
    company_name: str,
    role_title: str,
    platform: str,
) -> dict:
    """
    Background task: embed and ingest a job description into Qdrant.
    Called when the extension captures a new JD.
    """
    async def _run():
        from backend.api.db.session import AsyncSessionLocal
        from backend.api.db.redis_client import init_redis_pool, close_redis_pool, get_redis
        from backend.api.db.qdrant_client import init_qdrant_client, close_qdrant_client, get_qdrant
        from backend.api.rag.ingestion import ingest_job_description

        await init_redis_pool()
        await init_qdrant_client()
        redis = await get_redis()
        qdrant = await get_qdrant()

        try:
            async with AsyncSessionLocal() as db:
                await ingest_job_description(
                    raw_text=raw_text,
                    job_description_id=job_description_id,
                    user_id=user_id,
                    company_name=company_name,
                    role_title=role_title,
                    platform=platform,
                    db=db,
                    qdrant=qdrant,
                    redis=redis,
                )
                return {"status": "complete", "job_description_id": job_description_id}
        finally:
            await close_redis_pool()
            await close_qdrant_client()

    return _run_async(_run())