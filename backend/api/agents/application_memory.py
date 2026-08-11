"""
backend/api/agents/application_memory.py
==========================================
ApplicationMemoryAgent

Input  : A form question + user_id
Output : MemorySearchResult — ranked list of past answers from Qdrant

Responsibilities
----------------
* Provides a high-level interface for querying the `past_answers` Qdrant
  collection — the long-term memory store of everything the user has ever
  submitted on a job application form.
* Results are fed directly into the AnswerGenerationAgent's context so
  the model can adapt existing answers rather than generating from scratch.
* Also exposes a `record_answer()` method called after application submission
  to add the answer to memory (write side).

This agent wraps `backend/api/rag/retrieval.py` and `ingestion.py` with a
domain-focused API that the routers and AnswerGenerationAgent can use
without knowing about Qdrant internals.
"""

from __future__ import annotations

import time

from qdrant_client import AsyncQdrantClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.agents.base import MemorySearchResult, SimilarAnswer, configure_llama_settings
from backend.api.core.config import settings
from backend.api.core.logging import get_logger
from backend.api.rag.embeddings import embed_text
from backend.api.db.qdrant_client import QdrantHelper
from backend.api.rag.ingestion import ingest_past_answer

logger = get_logger(__name__)


class ApplicationMemoryAgent:
    """
    Manages the long-term answer memory store for a user.

    Usage:
        agent = ApplicationMemoryAgent()

        # Search
        result = await agent.search(
            question="Why do you want to work here?",
            user_id=user_id,
            qdrant=qdrant_client,
            redis=redis_client,
        )

        # Record after submission
        await agent.record_answer(
            answer_text="...",
            question_label="Why do you want to work here?",
            ...
        )
    """

    def __init__(self) -> None:
        configure_llama_settings()

    async def search(
        self,
        question: str,
        user_id: str,
        qdrant: AsyncQdrantClient,
        redis: Redis,
        top_k: int = settings.RAG_TOP_K,
        score_threshold: float = settings.RAG_SIMILARITY_THRESHOLD,
        application_id_filter: str | None = None,
    ) -> MemorySearchResult:
        """
        Retrieve the most semantically similar past answers for a question.

        Args:
            question:             The current form question to match.
            user_id:              Owner UUID — enforced as Qdrant filter.
            qdrant:               Async Qdrant client.
            redis:                Async Redis client (for embedding cache).
            top_k:                Number of results to return.
            score_threshold:      Minimum cosine similarity.
            application_id_filter: Optionally exclude answers from a specific
                                   application (e.g. the current one being filled).

        Returns:
            MemorySearchResult with ranked SimilarAnswer list.
        """
        t_start = time.monotonic()

        if not question.strip():
            return MemorySearchResult()

        # 1. Embed the question
        query_vector = await embed_text(question, redis)

        # 2. Build optional extra filter (exclude current application)
        extra_filter = None
        if application_id_filter:
            from qdrant_client import models as qmodels  # noqa: PLC0415
            extra_filter = qmodels.Filter(
                must_not=[
                    qmodels.FieldCondition(
                        key="application_id",
                        match=qmodels.MatchValue(value=application_id_filter),
                    )
                ]
            )

        # 3. Search the past_answers collection
        results = await QdrantHelper.search(
            qdrant,
            settings.QDRANT_COLLECTION_PAST_ANSWERS,
            query_vector,
            user_id,
            top_k=top_k,
            score_threshold=score_threshold,
            extra_filter=extra_filter,
        )

        # 4. Convert to SimilarAnswer objects
        similar: list[SimilarAnswer] = []
        for point in results:
            payload = point.payload or {}
            # Reconstruct the answer text from the stored chunk text
            # The chunk stores "Application Question: ...\nAnswer Used: ..."
            chunk_text = payload.get("text", "")
            answer_text = ""
            question_label = payload.get("question_label", "Unknown question")

            # Parse the answer out of the chunk format
            if "Answer Used:" in chunk_text:
                parts = chunk_text.split("Answer Used:", 1)
                answer_text = parts[1].strip() if len(parts) > 1 else chunk_text
            else:
                answer_text = chunk_text

            similar.append(SimilarAnswer(
                question_label=question_label,
                answer_text=answer_text[:1000],   # cap for safety
                application_id=payload.get("application_id", ""),
                score=round(point.score, 4),
                source_type=payload.get("answer_source", "unknown"),
            ))

        latency_ms = int((time.monotonic() - t_start) * 1000)
        logger.info(
            "ApplicationMemoryAgent.search complete",
            user_id=user_id,
            question_preview=question[:60],
            results_found=len(similar),
            latency_ms=latency_ms,
        )

        return MemorySearchResult(
            similar_answers=similar,
            total_found=len(similar),
        )

    async def record_answer(
        self,
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
        Persist a submitted answer into the RAG memory store.

        Called after application submission so future fill sessions
        can retrieve this answer as relevant context.

        Returns list of Qdrant point IDs that were stored.
        """
        point_ids = await ingest_past_answer(
            answer_text=answer_text,
            question_label=question_label,
            application_answer_id=application_answer_id,
            application_id=application_id,
            form_field_key=form_field_key,
            answer_source=answer_source,
            user_id=user_id,
            db=db,
            qdrant=qdrant,
            redis=redis,
        )
        logger.info(
            "ApplicationMemoryAgent.record_answer complete",
            application_answer_id=application_answer_id,
            user_id=user_id,
            point_ids=point_ids,
        )
        return point_ids

    async def clear_user_memory(
        self,
        user_id: str,
        qdrant: AsyncQdrantClient,
    ) -> None:
        """
        Hard-delete all past answer vectors for a user.
        Called during account deletion (GDPR).
        """
        await QdrantHelper.delete_by_user(
            qdrant,
            settings.QDRANT_COLLECTION_PAST_ANSWERS,
            user_id,
        )
        logger.info("ApplicationMemoryAgent: cleared all memory for user", user_id=user_id)