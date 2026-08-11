"""
backend/api/db/qdrant_client.py
================================
Qdrant async client wrapper and collection initialisation.

Responsibilities:
  1. Open / close the async Qdrant client (called from app lifespan)
  2. Create the four named vector collections if they don't exist yet:
       - resume_chunks
       - past_answers
       - job_descriptions
       - templates
  3. Provide `get_qdrant()` FastAPI dependency
  4. Expose helper methods used by the RAG embedding pipeline

Collection schema design:
  - All collections share the same HNSW index config (m=16, ef=100)
  - Payload fields are indexed to support server-side filtered retrieval
    (e.g. "give me resume chunks belonging to user X")
  - Distance metric: Cosine (normalised OpenAI embeddings)
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from qdrant_client import AsyncQdrantClient, models as qmodels
from qdrant_client.http.exceptions import UnexpectedResponse

from backend.api.core.config import settings
from backend.api.core.logging import get_logger

logger = get_logger(__name__)

# Module-level singleton — initialised in lifespan
_qdrant_client: AsyncQdrantClient | None = None


# ---------------------------------------------------------------------------
# Lifecycle helpers
# ---------------------------------------------------------------------------

async def init_qdrant_client() -> None:
    """
    Instantiate the async Qdrant client and ensure all collections exist.
    Called once at application startup.
    """
    global _qdrant_client

    logger.info(
        "Connecting to Qdrant",
        host=settings.QDRANT_HOST,
        port=settings.QDRANT_PORT,
        grpc=settings.QDRANT_USE_GRPC,
    )

    _qdrant_client = AsyncQdrantClient(
        host=settings.QDRANT_HOST,
        port=settings.QDRANT_PORT,
        api_key=settings.QDRANT_API_KEY,
        # Use gRPC for lower latency in production if enabled
        prefer_grpc=settings.QDRANT_USE_GRPC,
        grpc_port=settings.QDRANT_GRPC_PORT,
        # Connection-level timeouts
        timeout=30.0,
    )

    # Verify connectivity
    await _qdrant_client.get_collections()
    logger.info("Qdrant client connected")

    # Ensure all four collections exist
    await _ensure_collections(_qdrant_client)


async def close_qdrant_client() -> None:
    """Close the Qdrant client gracefully on app shutdown."""
    global _qdrant_client
    if _qdrant_client:
        await _qdrant_client.close()
        logger.info("Qdrant client closed")


async def get_qdrant() -> AsyncQdrantClient:
    """
    FastAPI dependency — yields the shared Qdrant async client.

    Usage:
        @router.post("/embed")
        async def embed(qdrant: AsyncQdrantClient = Depends(get_qdrant)):
            ...
    """
    if _qdrant_client is None:
        raise RuntimeError(
            "Qdrant client not initialised. "
            "Ensure init_qdrant_client() is called in app lifespan."
        )
    return _qdrant_client


# ---------------------------------------------------------------------------
# Collection schema constants
# ---------------------------------------------------------------------------

# Shared HNSW index config
_HNSW_CONFIG = qmodels.HnswConfigDiff(
    m=16,               # number of bi-directional links per node
    ef_construct=100,   # size of dynamic candidate list during construction
    full_scan_threshold=10_000,
    on_disk=False,      # keep index in RAM for lowest latency
)

# Shared optimiser config — tune for write throughput during bulk ingestion
_OPTIMIZER_CONFIG = qmodels.OptimizersConfigDiff(
    indexing_threshold=20_000,   # flush to disk after N vectors
    memmap_threshold=50_000,
)

# Shared quantisation — scalar int8 saves ~75 % RAM with minimal accuracy loss
_QUANTISATION_CONFIG = qmodels.ScalarQuantization(
    scalar=qmodels.ScalarQuantizationConfig(
        type=qmodels.ScalarType.INT8,
        quantile=0.99,
        always_ram=True,
    )
)

# Payload field indexes — allow server-side filtering without scanning all vectors
# Format: {field_name: field_schema_type}
_COMMON_PAYLOAD_INDEXES: dict[str, qmodels.PayloadSchemaType] = {
    "user_id": qmodels.PayloadSchemaType.KEYWORD,
    "source_type": qmodels.PayloadSchemaType.KEYWORD,
    "created_at": qmodels.PayloadSchemaType.DATETIME,
}

_COLLECTION_DEFINITIONS: dict[str, dict[str, Any]] = {
    settings.QDRANT_COLLECTION_RESUME: {
        "extra_indexes": {
            "resume_id": qmodels.PayloadSchemaType.KEYWORD,
            "chunk_index": qmodels.PayloadSchemaType.INTEGER,
        }
    },
    settings.QDRANT_COLLECTION_PAST_ANSWERS: {
        "extra_indexes": {
            "application_id": qmodels.PayloadSchemaType.KEYWORD,
            "form_field_key": qmodels.PayloadSchemaType.KEYWORD,
            "answer_source": qmodels.PayloadSchemaType.KEYWORD,
        }
    },
    settings.QDRANT_COLLECTION_JOB_DESCRIPTIONS: {
        "extra_indexes": {
            "job_description_id": qmodels.PayloadSchemaType.KEYWORD,
            "company_name": qmodels.PayloadSchemaType.KEYWORD,
            "platform": qmodels.PayloadSchemaType.KEYWORD,
        }
    },
    settings.QDRANT_COLLECTION_TEMPLATES: {
        "extra_indexes": {
            "template_id": qmodels.PayloadSchemaType.KEYWORD,
            "is_default": qmodels.PayloadSchemaType.BOOL,
        }
    },
}


async def _ensure_collections(client: AsyncQdrantClient) -> None:
    """
    Create all four Qdrant collections with full schema if they don't exist.
    Safe to call repeatedly (idempotent).
    """
    for collection_name, definition in _COLLECTION_DEFINITIONS.items():
        try:
            await client.get_collection(collection_name)
            logger.info("Qdrant collection already exists", collection=collection_name)
        except (UnexpectedResponse, Exception):
            # Collection does not exist — create it
            logger.info("Creating Qdrant collection", collection=collection_name)

            await client.create_collection(
                collection_name=collection_name,
                vectors_config=qmodels.VectorParams(
                    size=settings.QDRANT_VECTOR_SIZE,
                    distance=qmodels.Distance[settings.QDRANT_DISTANCE],
                    on_disk=False,
                ),
                hnsw_config=_HNSW_CONFIG,
                optimizers_config=_OPTIMIZER_CONFIG,
                quantization_config=_QUANTISATION_CONFIG,
            )

            # Create payload field indexes for fast filtered search
            all_indexes = {**_COMMON_PAYLOAD_INDEXES, **definition.get("extra_indexes", {})}
            for field_name, field_type in all_indexes.items():
                await client.create_payload_index(
                    collection_name=collection_name,
                    field_name=field_name,
                    field_schema=field_type,
                )

            logger.info("Qdrant collection created", collection=collection_name)


# ---------------------------------------------------------------------------
# Qdrant helper utilities used by the RAG pipeline
# ---------------------------------------------------------------------------

class QdrantHelper:
    """
    Stateless helper methods that wrap the async Qdrant client.
    All methods accept the client explicitly so they remain testable.
    """

    @staticmethod
    async def upsert_vectors(
        client: AsyncQdrantClient,
        collection_name: str,
        points: list[qmodels.PointStruct],
    ) -> None:
        """
        Upsert a batch of points into a collection.
        Qdrant upsert is idempotent by point ID — safe to retry.
        """
        await client.upsert(
            collection_name=collection_name,
            points=points,
            wait=True,   # wait for indexing to complete before returning
        )
        logger.debug(
            "Upserted vectors",
            collection=collection_name,
            count=len(points),
        )

    @staticmethod
    async def search(
        client: AsyncQdrantClient,
        collection_name: str,
        query_vector: list[float],
        user_id: str,
        top_k: int = settings.RAG_TOP_K,
        score_threshold: float = settings.RAG_SIMILARITY_THRESHOLD,
        extra_filter: qmodels.Filter | None = None,
    ) -> list[qmodels.ScoredPoint]:
        """
        Perform a filtered semantic search scoped to a single user.

        The `user_id` filter is mandatory — we must NEVER return another
        user's data from any retrieval path.
        """
        # Always scope to the requesting user
        user_filter = qmodels.FieldCondition(
            key="user_id",
            match=qmodels.MatchValue(value=user_id),
        )

        if extra_filter is not None:
            must_conditions = [user_filter]
            # Merge any caller-supplied conditions
            if extra_filter.must:
                must_conditions.extend(extra_filter.must)
            combined_filter = qmodels.Filter(must=must_conditions)
        else:
            combined_filter = qmodels.Filter(must=[user_filter])

        results = await client.search(
            collection_name=collection_name,
            query_vector=query_vector,
            limit=top_k,
            score_threshold=score_threshold,
            query_filter=combined_filter,
            with_payload=True,
            with_vectors=False,   # no need to return vectors in retrieval
        )
        return results

    @staticmethod
    async def delete_by_user(
        client: AsyncQdrantClient,
        collection_name: str,
        user_id: str,
    ) -> None:
        """
        Hard-delete all vectors owned by a user from a collection.
        Used when a user deletes their account (GDPR compliance).
        """
        await client.delete(
            collection_name=collection_name,
            points_selector=qmodels.FilterSelector(
                filter=qmodels.Filter(
                    must=[
                        qmodels.FieldCondition(
                            key="user_id",
                            match=qmodels.MatchValue(value=user_id),
                        )
                    ]
                )
            ),
        )
        logger.info(
            "Deleted all vectors for user",
            collection=collection_name,
            user_id=user_id,
        )

    @staticmethod
    async def delete_by_source_id(
        client: AsyncQdrantClient,
        collection_name: str,
        source_field: str,
        source_id: str,
    ) -> None:
        """
        Delete all vectors where payload[source_field] == source_id.
        Used when a resume or template is replaced (stale chunks cleanup).

        Example:
            await QdrantHelper.delete_by_source_id(
                client, COLLECTION_RESUME, "resume_id", str(resume.id)
            )
        """
        await client.delete(
            collection_name=collection_name,
            points_selector=qmodels.FilterSelector(
                filter=qmodels.Filter(
                    must=[
                        qmodels.FieldCondition(
                            key=source_field,
                            match=qmodels.MatchValue(value=source_id),
                        )
                    ]
                )
            ),
        )
        logger.info(
            "Deleted vectors by source",
            collection=collection_name,
            field=source_field,
            id=source_id,
        )