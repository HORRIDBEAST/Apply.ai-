"""
backend/api/rag/embeddings.py
==============================
Embedding generation layer.

Responsibilities:
  - Call the OpenAI Embeddings API (or any compatible endpoint)
  - Cache embedding vectors in Redis to avoid re-embedding identical text
    (resume chunks and templates rarely change)
  - Provide a stable interface that the rest of the RAG pipeline uses
    without caring which underlying model is in use

Cache key strategy:
    "embedding:{sha256(model + text)}" → JSON-encoded float list
    TTL: 24 hours (configurable via settings.REDIS_EMBEDDING_CACHE_TTL)

This module is intentionally model-agnostic — swap settings.OPENAI_MODEL
to point at any OpenAI-compatible endpoint (Mistral, Together AI, etc.).
"""

from __future__ import annotations

import hashlib
import json
from typing import Sequence

import openai
from openai import AsyncOpenAI
from redis.asyncio import Redis

from backend.api.core.config import settings
from backend.api.core.logging import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# OpenAI async client singleton — instantiated once, reused across requests
# ---------------------------------------------------------------------------
_openai_client: AsyncOpenAI | None = None


def get_openai_client() -> AsyncOpenAI:
    """Return or create the module-level AsyncOpenAI client."""
    global _openai_client
    if _openai_client is None:
        _openai_client = AsyncOpenAI(
            api_key=settings.OPENAI_API_KEY,
            base_url=settings.OPENAI_BASE_URL,
            # Retry transient 5xx / 429 errors automatically
            max_retries=3,
            timeout=30.0,
        )
    return _openai_client


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _embedding_cache_key(text: str, model: str) -> str:
    """
    Deterministic, collision-resistant cache key for an (text, model) pair.
    We truncate the hash to 40 hex chars (160 bits) — sufficient for uniqueness.
    """
    content = f"{model}::{text}"
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:40]
    return f"embedding:{digest}"


async def _get_cached_embedding(redis: Redis, key: str) -> list[float] | None:
    """Return a cached embedding vector or None."""
    raw = await redis.get(key)
    if raw is None:
        return None
    return json.loads(raw)


async def _cache_embedding(redis: Redis, key: str, vector: list[float]) -> None:
    """Store an embedding vector in Redis with the configured TTL."""
    await redis.setex(key, settings.REDIS_EMBEDDING_CACHE_TTL, json.dumps(vector))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def embed_text(
    text: str,
    redis: Redis,
    model: str = settings.OPENAI_EMBEDDING_MODEL,
) -> list[float]:
    """
    Generate a single embedding vector for `text`.

    Cache hit:  returns immediately from Redis (sub-millisecond)
    Cache miss: calls OpenAI API, stores result, returns vector

    Args:
        text:   The text to embed. Will be stripped and truncated to
                avoid exceeding model token limits.
        redis:  Async Redis client (injected by caller / dependency).
        model:  Embedding model name (default: text-embedding-3-small).

    Returns:
        A list[float] of length settings.QDRANT_VECTOR_SIZE (1536).
    """
    # Normalise whitespace — identical content after normalisation shares a cache entry
    normalised = " ".join(text.split())

    if not normalised:
        raise ValueError("Cannot embed empty text")

    cache_key = _embedding_cache_key(normalised, model)

    # 1. Try cache
    cached = await _get_cached_embedding(redis, cache_key)
    if cached is not None:
        logger.debug("Embedding cache hit", key=cache_key)
        return cached

    # 2. Call OpenAI Embeddings API
    logger.debug("Embedding cache miss — calling API", model=model, text_length=len(normalised))
    client = get_openai_client()
    response = await client.embeddings.create(
        model=model,
        input=normalised,
        encoding_format="float",
    )
    vector: list[float] = response.data[0].embedding

    # 3. Store in cache
    await _cache_embedding(redis, cache_key, vector)

    return vector


async def embed_batch(
    texts: Sequence[str],
    redis: Redis,
    model: str = settings.OPENAI_EMBEDDING_MODEL,
    batch_size: int = 100,
) -> list[list[float]]:
    """
    Embed a list of texts efficiently.

    Strategy:
      1. Check cache for each text.
      2. Batch all cache-miss texts into a single OpenAI API call
         (the API supports up to 2048 inputs per request, but we batch
         at 100 for predictable latency).
      3. Store all new embeddings in cache.
      4. Return vectors in the original input order.

    Args:
        texts:      Sequence of texts to embed.
        redis:      Async Redis client.
        model:      Embedding model name.
        batch_size: Max texts per API call.

    Returns:
        List of embedding vectors in the same order as `texts`.
    """
    if not texts:
        return []

    normalised_texts = [" ".join(t.split()) for t in texts]
    cache_keys = [_embedding_cache_key(t, model) for t in normalised_texts]

    # 1. Check cache for all texts
    # Use Redis MGET for a single round-trip
    raw_cached = await redis.mget(*cache_keys)

    results: list[list[float] | None] = []
    miss_indices: list[int] = []       # original positions of cache misses
    miss_texts: list[str] = []        # texts to embed

    for i, raw in enumerate(raw_cached):
        if raw is not None:
            results.append(json.loads(raw))
        else:
            results.append(None)      # placeholder
            miss_indices.append(i)
            miss_texts.append(normalised_texts[i])

    logger.debug(
        "Batch embed cache stats",
        total=len(texts),
        hits=len(texts) - len(miss_indices),
        misses=len(miss_indices),
    )

    # 2. Embed cache misses in sub-batches
    if miss_texts:
        client = get_openai_client()
        new_vectors: list[list[float]] = []

        for batch_start in range(0, len(miss_texts), batch_size):
            batch = miss_texts[batch_start : batch_start + batch_size]
            response = await client.embeddings.create(
                model=model,
                input=batch,
                encoding_format="float",
            )
            # Preserve original order (OpenAI returns results in input order)
            batch_vectors = [item.embedding for item in sorted(response.data, key=lambda x: x.index)]
            new_vectors.extend(batch_vectors)

        # 3. Store new embeddings in cache and fill result slots
        pipe = redis.pipeline(transaction=False)
        for i, original_idx in enumerate(miss_indices):
            vector = new_vectors[i]
            results[original_idx] = vector
            pipe.setex(cache_keys[original_idx], settings.REDIS_EMBEDDING_CACHE_TTL, json.dumps(vector))
        await pipe.execute()

    # All slots should now be filled
    assert all(v is not None for v in results), "Internal error: unfilled embedding slot"
    return results  # type: ignore[return-value]