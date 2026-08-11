"""
backend/api/routers/agents.py
================================
FastAPI endpoints that expose the four AI agents.

Endpoints
---------
POST /api/v1/agents/form/understand
    FormUnderstandingAgent — parse raw HTML → field map

POST /api/v1/agents/answers/generate
    AnswerGenerationAgent — generate a single grounded answer

POST /api/v1/agents/answers/generate-batch
    AnswerGenerationAgent — generate answers for multiple questions at once

POST /api/v1/agents/memory/search
    ApplicationMemoryAgent — find similar past answers

POST /api/v1/agents/memory/record
    ApplicationMemoryAgent — store a submitted answer in memory

All endpoints:
  - Require Bearer authentication (AuthenticatedUser)
  - Are fully async (no blocking IO)
  - Return structured Pydantic response models
  - Log timing and token usage for observability
"""

from __future__ import annotations

import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from qdrant_client import AsyncQdrantClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.agents.answer_generation import AnswerGenerationAgent
from backend.api.agents.application_memory import ApplicationMemoryAgent
from backend.api.agents.base import (
    FormUnderstandingResult,
    GeneratedAnswer,
    MemorySearchResult,
)
from backend.api.agents.form_understanding import FormUnderstandingAgent
from backend.api.core.auth import AuthenticatedUser
from backend.api.core.logging import get_logger
from backend.api.db.qdrant_client import get_qdrant
from backend.api.db.redis_client import get_redis
from backend.api.db.session import get_db
from backend.api.rag.retrieval import retrieve_context

logger = get_logger(__name__)
router = APIRouter()

# ---------------------------------------------------------------------------
# Agent singletons — instantiated once, shared across requests
# (Agents are stateless — they hold no per-user data)
# ---------------------------------------------------------------------------
_form_agent = FormUnderstandingAgent()
_answer_agent = AnswerGenerationAgent()
_memory_agent = ApplicationMemoryAgent()


# ===========================================================================
# 1. FormUnderstandingAgent endpoint
# ===========================================================================

class FormUnderstandRequest(BaseModel):
    html: str = Field(..., min_length=1, description="Raw HTML of the job application page")
    page_url: str = Field(default="", description="URL of the page (used for ATS platform detection)")


@router.post(
    "/form/understand",
    response_model=FormUnderstandingResult,
    summary="Detect and classify form fields from raw HTML",
    description=(
        "Takes raw HTML from the browser extension and returns a structured list "
        "of detected form fields with canonical mappings, field types, and AI flags."
    ),
)
async def understand_form(
    body: FormUnderstandRequest,
    current_user: AuthenticatedUser,
) -> FormUnderstandingResult:
    """
    Parse a job application page HTML and map fields to canonical keys.

    - Phase 1 (heuristic): resolves ~80% of fields instantly with regex
    - Phase 2 (LLM): sends ambiguous fields to GPT-4o for semantic mapping

    Returns `requires_ai=true` for open-text questions that need AI generation.
    """
    t_start = time.monotonic()

    result = await _form_agent.understand(
        html=body.html,
        page_url=body.page_url,
    )

    logger.info(
        "Form understanding request complete",
        user_id=current_user.user_id,
        platform=result.platform_detected,
        total_fields=result.total_fields,
        ai_required=result.ai_required_count,
        latency_ms=int((time.monotonic() - t_start) * 1000),
    )
    return result


# ===========================================================================
# 2. AnswerGenerationAgent — single question
# ===========================================================================

class GenerateAnswerRequest(BaseModel):
    question: str = Field(..., min_length=1, description="The exact question text from the form label")
    field_key: str | None = Field(None, description="Canonical field key (e.g. 'why_us')")
    max_words: int | None = Field(None, ge=20, le=500, description="Optional target word count")
    # Context sources — all optional so the agent degrades gracefully
    job_description_id: uuid.UUID | None = Field(None, description="UUID of the job_descriptions row")
    template_id: uuid.UUID | None = Field(None, description="UUID of the template to use")
    resume_id: uuid.UUID | None = Field(None, description="UUID of the resume to use")


@router.post(
    "/answers/generate",
    response_model=GeneratedAnswer,
    summary="Generate a grounded answer for a single open-text question",
    description=(
        "Uses RAG retrieval from resume, templates, job description, and past answers "
        "to generate a context-only answer. Returns confidence score and full audit trail. "
        "Will return a refusal_reason instead of an answer if context is insufficient."
    ),
)
async def generate_answer(
    body: GenerateAnswerRequest,
    current_user: AuthenticatedUser,
    qdrant: AsyncQdrantClient = Depends(get_qdrant),
    redis: Redis = Depends(get_redis),
) -> GeneratedAnswer:
    """
    Full pipeline:
      1. Retrieve context from Qdrant (resume + JD + template + past answers)
      2. Build prompt with all retrieved context
      3. Generate grounded answer with LLM
      4. Validate for hallucination
      5. Return GeneratedAnswer with confidence score
    """
    t_start = time.monotonic()

    # 1. RAG retrieval — parallel search across all four collections
    context = await retrieve_context(
        question=body.question,
        user_id=current_user.user_id,
        qdrant=qdrant,
        redis=redis,
        job_description_id=str(body.job_description_id) if body.job_description_id else None,
        template_id=str(body.template_id) if body.template_id else None,
        resume_id=str(body.resume_id) if body.resume_id else None,
    )

    # 2. Generate answer
    result = await _answer_agent.generate(
        question=body.question,
        context=context,
        field_key=body.field_key,
        max_words=body.max_words,
    )

    logger.info(
        "Answer generation request complete",
        user_id=current_user.user_id,
        field_key=body.field_key,
        confidence=result.confidence_score,
        hallucination_flagged=result.hallucination_flagged,
        refusal=bool(result.refusal_reason),
        latency_ms=int((time.monotonic() - t_start) * 1000),
    )
    return result


# ===========================================================================
# 3. AnswerGenerationAgent — batch (multiple questions, one context fetch)
# ===========================================================================

class BatchQuestion(BaseModel):
    question: str
    field_key: str | None = None
    max_words: int | None = None


class GenerateBatchRequest(BaseModel):
    questions: list[BatchQuestion] = Field(..., min_length=1, max_length=20)
    job_description_id: uuid.UUID | None = None
    template_id: uuid.UUID | None = None
    resume_id: uuid.UUID | None = None


class BatchAnswerItem(BaseModel):
    question: str
    field_key: str | None
    result: GeneratedAnswer


class GenerateBatchResponse(BaseModel):
    answers: list[BatchAnswerItem]
    total_latency_ms: int
    total_tokens: int


@router.post(
    "/answers/generate-batch",
    response_model=GenerateBatchResponse,
    summary="Generate answers for multiple questions in one API call",
    description=(
        "Retrieves context once and generates answers for all questions concurrently. "
        "Much more efficient than calling /answers/generate repeatedly for the same form. "
        "Maximum 20 questions per batch."
    ),
)
async def generate_batch_answers(
    body: GenerateBatchRequest,
    current_user: AuthenticatedUser,
    qdrant: AsyncQdrantClient = Depends(get_qdrant),
    redis: Redis = Depends(get_redis),
) -> GenerateBatchResponse:
    """
    Optimal path for answering a full form's open-text questions:
      1. Combine all questions into a single RAG retrieval query
      2. Generate all answers in parallel with shared context
    """
    t_start = time.monotonic()

    if not body.questions:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="At least one question is required",
        )

    # Build a combined query for retrieval (union of all question texts)
    combined_query = " ".join(q.question for q in body.questions)

    # 1. Single RAG retrieval for all questions
    context = await retrieve_context(
        question=combined_query,
        user_id=current_user.user_id,
        qdrant=qdrant,
        redis=redis,
        job_description_id=str(body.job_description_id) if body.job_description_id else None,
        template_id=str(body.template_id) if body.template_id else None,
        resume_id=str(body.resume_id) if body.resume_id else None,
        # Increase top_k for batch to ensure coverage across all questions
        top_k=min(settings.RAG_TOP_K * 2, 20),
    )

    # 2. Generate all answers concurrently with the shared context
    question_dicts = [
        {"question": q.question, "field_key": q.field_key, "max_words": q.max_words}
        for q in body.questions
    ]
    results = await _answer_agent.generate_batch(question_dicts, context)

    total_tokens = sum(
        r.token_usage.get("total_tokens", 0) for r in results
    )
    total_latency = int((time.monotonic() - t_start) * 1000)

    logger.info(
        "Batch answer generation complete",
        user_id=current_user.user_id,
        question_count=len(body.questions),
        total_tokens=total_tokens,
        total_latency_ms=total_latency,
    )

    return GenerateBatchResponse(
        answers=[
            BatchAnswerItem(
                question=q.question,
                field_key=q.field_key,
                result=result,
            )
            for q, result in zip(body.questions, results)
        ],
        total_latency_ms=total_latency,
        total_tokens=total_tokens,
    )


# ===========================================================================
# 4. ApplicationMemoryAgent — search
# ===========================================================================

class MemorySearchRequest(BaseModel):
    question: str = Field(..., min_length=1, description="Question to find similar past answers for")
    top_k: int = Field(default=5, ge=1, le=20)
    score_threshold: float = Field(default=0.70, ge=0.0, le=1.0)
    exclude_application_id: uuid.UUID | None = Field(
        None,
        description="Exclude answers from this application (e.g. the current one)",
    )


@router.post(
    "/memory/search",
    response_model=MemorySearchResult,
    summary="Search past application answers for similar questions",
    description=(
        "Embeds the question and searches the past_answers Qdrant collection "
        "to surface the most semantically similar answers the user has written before."
    ),
)
async def search_memory(
    body: MemorySearchRequest,
    current_user: AuthenticatedUser,
    qdrant: AsyncQdrantClient = Depends(get_qdrant),
    redis: Redis = Depends(get_redis),
) -> MemorySearchResult:
    """Find past answers semantically similar to the current question."""
    return await _memory_agent.search(
        question=body.question,
        user_id=current_user.user_id,
        qdrant=qdrant,
        redis=redis,
        top_k=body.top_k,
        score_threshold=body.score_threshold,
        application_id_filter=str(body.exclude_application_id) if body.exclude_application_id else None,
    )


# ===========================================================================
# 5. ApplicationMemoryAgent — record answer
# ===========================================================================

class RecordAnswerRequest(BaseModel):
    answer_text: str = Field(..., min_length=1)
    question_label: str = Field(..., min_length=1)
    application_answer_id: uuid.UUID
    application_id: uuid.UUID
    form_field_key: str | None = None
    answer_source: str = "ai_generated"


class RecordAnswerResponse(BaseModel):
    status: str
    point_ids: list[str]


@router.post(
    "/memory/record",
    response_model=RecordAnswerResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Store a submitted answer in the RAG memory store",
    description=(
        "Called after application submission to persist the final answer. "
        "Future form-fill sessions can retrieve this as past-answer context."
    ),
)
async def record_memory(
    body: RecordAnswerRequest,
    current_user: AuthenticatedUser,
    db: AsyncSession = Depends(get_db),
    qdrant: AsyncQdrantClient = Depends(get_qdrant),
    redis: Redis = Depends(get_redis),
) -> RecordAnswerResponse:
    """Ingest a submitted answer into the past_answers Qdrant collection."""
    point_ids = await _memory_agent.record_answer(
        answer_text=body.answer_text,
        question_label=body.question_label,
        application_answer_id=str(body.application_answer_id),
        application_id=str(body.application_id),
        form_field_key=body.form_field_key,
        answer_source=body.answer_source,
        user_id=current_user.user_id,
        db=db,
        qdrant=qdrant,
        redis=redis,
    )
    return RecordAnswerResponse(status="recorded", point_ids=point_ids)