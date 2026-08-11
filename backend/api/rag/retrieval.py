"""
backend/api/rag/retrieval.py
============================
RAG retrieval pipeline — the read side of the RAG system.

Responsibilities:
  1. Accept a natural language question + user context
  2. Embed the question
  3. Retrieve top-K chunks from each relevant Qdrant collection
  4. Rank / de-duplicate results
  5. Return structured RetrievedContext ready for the answer generation prompt

Design principles:
  - User isolation is enforced at the Qdrant filter level (every search
    is scoped to a specific user_id)
  - Collections are searched in parallel using asyncio.gather()
  - Results are deduplicated by chunk_id before ranking
  - The caller (AnswerGenerationAgent) decides which context sources to include
  - No content is generated here — pure retrieval

Token budget:
  We enforce settings.MAX_CONTEXT_TOKENS across all retrieved chunks
  so the downstream prompt never exceeds the LLM's context window.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from qdrant_client import AsyncQdrantClient
from qdrant_client import models as qmodels
from redis.asyncio import Redis

from backend.api.core.config import settings
from backend.api.core.logging import get_logger
from backend.api.db.qdrant_client import QdrantHelper
from backend.api.rag.embeddings import embed_text

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class RetrievedChunk:
    """A single chunk returned from Qdrant with its score and metadata."""
    chunk_id: str
    text: str
    score: float
    source_type: str        # "resume" | "past_answer" | "job_description" | "template"
    payload: dict


@dataclass
class RetrievedContext:
    """
    Aggregated retrieval result passed to the answer generation prompt.

    Attributes:
        resume_chunks:        Chunks from the user's parsed resume.
        past_answer_chunks:   Chunks from previous application answers.
        jd_chunks:            Chunks from the current job description.
        template_chunks:      Chunks from the user's template.
        total_token_estimate: Approximate token count across all chunks.
    """
    resume_chunks: list[RetrievedChunk] = field(default_factory=list)
    past_answer_chunks: list[RetrievedChunk] = field(default_factory=list)
    jd_chunks: list[RetrievedChunk] = field(default_factory=list)
    template_chunks: list[RetrievedChunk] = field(default_factory=list)
    total_token_estimate: int = 0

    def all_chunks(self) -> list[RetrievedChunk]:
        """Flat list of all retrieved chunks, ordered by score descending."""
        all_chunks = (
            self.resume_chunks
            + self.past_answer_chunks
            + self.jd_chunks
            + self.template_chunks
        )
        return sorted(all_chunks, key=lambda c: c.score, reverse=True)

    def is_empty(self) -> bool:
        return not any([
            self.resume_chunks,
            self.past_answer_chunks,
            self.jd_chunks,
            self.template_chunks,
        ])


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _qdrant_results_to_chunks(
    results: list[qmodels.ScoredPoint],
    source_type: str,
) -> list[RetrievedChunk]:
    """Convert raw Qdrant ScoredPoint results to RetrievedChunk objects."""
    chunks = []
    for point in results:
        payload = point.payload or {}
        text = payload.get("text", "")  # fallback — text may not be in payload
        # Prefer the 'text' payload field; fall back to chunk_index hint
        if not text:
            text = f"[Chunk {payload.get('chunk_index', '?')} from {source_type}]"
        chunks.append(
            RetrievedChunk(
                chunk_id=str(point.id),
                text=text,
                score=point.score,
                source_type=source_type,
                payload=payload,
            )
        )
    return chunks


def _deduplicate_chunks(chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
    """Remove duplicate chunk IDs, keeping the highest-scoring copy."""
    seen: dict[str, RetrievedChunk] = {}
    for chunk in chunks:
        existing = seen.get(chunk.chunk_id)
        if existing is None or chunk.score > existing.score:
            seen[chunk.chunk_id] = chunk
    return list(seen.values())


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: 4 characters ≈ 1 token (conservative)."""
    return max(1, len(text) // 4)


def _apply_token_budget(
    chunks: list[RetrievedChunk],
    max_tokens: int,
) -> list[RetrievedChunk]:
    """
    Greedily select chunks in descending score order until the token budget
    is exhausted.  This ensures we never overflow the LLM context window.
    """
    selected = []
    used_tokens = 0
    for chunk in sorted(chunks, key=lambda c: c.score, reverse=True):
        chunk_tokens = _estimate_tokens(chunk.text)
        if used_tokens + chunk_tokens > max_tokens:
            break
        selected.append(chunk)
        used_tokens += chunk_tokens
    return selected


# ---------------------------------------------------------------------------
# Main retrieval function
# ---------------------------------------------------------------------------

async def retrieve_context(
    question: str,
    user_id: str,
    qdrant: AsyncQdrantClient,
    redis: Redis,
    job_description_id: str | None = None,
    template_id: str | None = None,
    resume_id: str | None = None,
    top_k: int = settings.RAG_TOP_K,
    score_threshold: float = settings.RAG_SIMILARITY_THRESHOLD,
    max_context_tokens: int = settings.MAX_CONTEXT_TOKENS,
    include_collections: list[str] | None = None,
) -> RetrievedContext:
    """
    Retrieve semantically relevant context for an application question.

    Searches all four Qdrant collections in parallel (asyncio.gather),
    deduplicated and token-budgeted before returning.

    Args:
        question:           The form question to answer (e.g. "Why us?").
        user_id:            Owner UUID string — enforced as Qdrant filter.
        qdrant:             Async Qdrant client.
        redis:              Async Redis client (for embedding cache).
        job_description_id: If provided, additionally filter JD search to this ID.
        template_id:        If provided, additionally filter template search to this ID.
        resume_id:          If provided, additionally filter resume search to this ID.
        top_k:              Max chunks to retrieve per collection.
        score_threshold:    Minimum cosine similarity score.
        max_context_tokens: Total token budget across all chunks.
        include_collections: If set, only search these collection names.
                             Useful for targeted retrieval.

    Returns:
        RetrievedContext with populated chunk lists.
    """
    if not question.strip():
        return RetrievedContext()

    # 1. Embed the question
    query_vector = await embed_text(question, redis)

    # 2. Build collection-specific filters
    resume_filter = None
    if resume_id:
        resume_filter = qmodels.Filter(
            must=[qmodels.FieldCondition(
                key="resume_id", match=qmodels.MatchValue(value=resume_id)
            )]
        )

    jd_filter = None
    if job_description_id:
        jd_filter = qmodels.Filter(
            must=[qmodels.FieldCondition(
                key="job_description_id", match=qmodels.MatchValue(value=job_description_id)
            )]
        )

    template_filter = None
    if template_id:
        template_filter = qmodels.Filter(
            must=[qmodels.FieldCondition(
                key="template_id", match=qmodels.MatchValue(value=template_id)
            )]
        )

    # 3. Determine which collections to search
    all_collections = {
        settings.QDRANT_COLLECTION_RESUME,
        settings.QDRANT_COLLECTION_PAST_ANSWERS,
        settings.QDRANT_COLLECTION_JOB_DESCRIPTIONS,
        settings.QDRANT_COLLECTION_TEMPLATES,
    }
    target_collections = (
        set(include_collections) & all_collections
        if include_collections
        else all_collections
    )

    # 4. Fire searches in parallel
    search_tasks = {}

    if settings.QDRANT_COLLECTION_RESUME in target_collections:
        search_tasks["resume"] = QdrantHelper.search(
            qdrant, settings.QDRANT_COLLECTION_RESUME,
            query_vector, user_id, top_k, score_threshold, resume_filter,
        )
    if settings.QDRANT_COLLECTION_PAST_ANSWERS in target_collections:
        search_tasks["past_answer"] = QdrantHelper.search(
            qdrant, settings.QDRANT_COLLECTION_PAST_ANSWERS,
            query_vector, user_id, top_k, score_threshold,
        )
    if settings.QDRANT_COLLECTION_JOB_DESCRIPTIONS in target_collections:
        search_tasks["job_description"] = QdrantHelper.search(
            qdrant, settings.QDRANT_COLLECTION_JOB_DESCRIPTIONS,
            query_vector, user_id, top_k, score_threshold, jd_filter,
        )
    if settings.QDRANT_COLLECTION_TEMPLATES in target_collections:
        search_tasks["template"] = QdrantHelper.search(
            qdrant, settings.QDRANT_COLLECTION_TEMPLATES,
            query_vector, user_id, top_k, score_threshold, template_filter,
        )

    # Execute all searches concurrently
    keys = list(search_tasks.keys())
    results_list = await asyncio.gather(*[search_tasks[k] for k in keys])
    raw_results: dict[str, list[qmodels.ScoredPoint]] = dict(zip(keys, results_list))

    # 5. Convert to RetrievedChunk objects
    resume_chunks = _deduplicate_chunks(
        _qdrant_results_to_chunks(raw_results.get("resume", []), "resume")
    )
    past_answer_chunks = _deduplicate_chunks(
        _qdrant_results_to_chunks(raw_results.get("past_answer", []), "past_answer")
    )
    jd_chunks = _deduplicate_chunks(
        _qdrant_results_to_chunks(raw_results.get("job_description", []), "job_description")
    )
    template_chunks = _deduplicate_chunks(
        _qdrant_results_to_chunks(raw_results.get("template", []), "template")
    )

    # 6. Apply per-collection token budgets (proportional allocation)
    #    Budget split: 30% resume, 30% past answers, 25% JD, 15% template
    budget_splits = {
        "resume": int(max_context_tokens * 0.30),
        "past_answer": int(max_context_tokens * 0.30),
        "job_description": int(max_context_tokens * 0.25),
        "template": int(max_context_tokens * 0.15),
    }
    resume_chunks = _apply_token_budget(resume_chunks, budget_splits["resume"])
    past_answer_chunks = _apply_token_budget(past_answer_chunks, budget_splits["past_answer"])
    jd_chunks = _apply_token_budget(jd_chunks, budget_splits["job_description"])
    template_chunks = _apply_token_budget(template_chunks, budget_splits["template"])

    # 7. Compute total token estimate for the caller
    all_chunks = resume_chunks + past_answer_chunks + jd_chunks + template_chunks
    total_tokens = sum(_estimate_tokens(c.text) for c in all_chunks)

    context = RetrievedContext(
        resume_chunks=resume_chunks,
        past_answer_chunks=past_answer_chunks,
        jd_chunks=jd_chunks,
        template_chunks=template_chunks,
        total_token_estimate=total_tokens,
    )

    logger.info(
        "RAG retrieval complete",
        user_id=user_id,
        question_preview=question[:80],
        resume_chunks=len(resume_chunks),
        past_answer_chunks=len(past_answer_chunks),
        jd_chunks=len(jd_chunks),
        template_chunks=len(template_chunks),
        total_tokens=total_tokens,
    )
    return context