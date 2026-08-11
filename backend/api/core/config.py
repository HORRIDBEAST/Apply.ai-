"""
backend/api/core/config.py
==========================
Centralised application settings using Pydantic-Settings v2.

All environment variables are read ONCE at startup and validated.
Downstream modules import `settings` — never os.environ directly.

Usage:
    from backend.api.core.config import settings
    print(settings.DATABASE_URL)
"""

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import AnyHttpUrl, Field, RedisDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application configuration.  All values are sourced from environment
    variables (or a .env file in development).  Annotated types give us
    free validation + clear error messages on misconfiguration.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        # Allow extra env vars so Docker / CI don't break on unrecognised keys
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # Application
    # ------------------------------------------------------------------
    APP_ENV: Literal["development", "staging", "production"] = "development"
    APP_NAME: str = "Job Autofill Copilot API"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = False

    # ------------------------------------------------------------------
    # CORS — origins that may call the API
    # Browser extension uses chrome-extension:// scheme;
    # dashboard runs on localhost:3000 in dev and its production domain.
    # ------------------------------------------------------------------
    CORS_ORIGINS: list[str] = [
        "http://localhost:3000",          # Next.js dashboard (dev)
        "http://localhost:3001",          # Next.js dashboard (alt port)
        "chrome-extension://*",           # Plasmo extension (dev build)
    ]
    CORS_ALLOW_CREDENTIALS: bool = True

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: str | list[str]) -> list[str]:
        """Support comma-separated string from env vars."""
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v

    # ------------------------------------------------------------------
    # PostgreSQL (asyncpg driver)
    # ------------------------------------------------------------------
    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://autofill_user:autofill_secret@localhost:5432/autofill_db",
        description="Full async SQLAlchemy connection string",
    )

    # Connection pool sizing  (per worker process)
    DB_POOL_SIZE: int = 20
    DB_MAX_OVERFLOW: int = 40
    DB_POOL_TIMEOUT: int = 30       # seconds
    DB_POOL_RECYCLE: int = 1800     # seconds (30 min)

    # ------------------------------------------------------------------
    # Redis
    # ------------------------------------------------------------------
    REDIS_URL: str = Field(
        default="redis://:redis_secret@localhost:6379/0",
        description="Redis connection URL including password",
    )
    REDIS_CACHE_TTL_SECONDS: int = 300      # default 5-minute TTL for cached values
    REDIS_EMBEDDING_CACHE_TTL: int = 86400  # embeddings cached for 24 h

    # ------------------------------------------------------------------
    # Qdrant
    # ------------------------------------------------------------------
    QDRANT_HOST: str = "localhost"
    QDRANT_PORT: int = 6333
    QDRANT_API_KEY: str = Field(default="qdrant_secret")
    QDRANT_GRPC_PORT: int = 6334
    QDRANT_USE_GRPC: bool = False   # flip to True in prod for lower latency

    # Collection names — one per content type (mirrors master prompt)
    QDRANT_COLLECTION_RESUME: str = "resume_chunks"
    QDRANT_COLLECTION_PAST_ANSWERS: str = "past_answers"
    QDRANT_COLLECTION_JOB_DESCRIPTIONS: str = "job_descriptions"
    QDRANT_COLLECTION_TEMPLATES: str = "templates"

    # HNSW index parameters (must match collection creation params)
    QDRANT_VECTOR_SIZE: int = 1536      # text-embedding-3-small output dim
    QDRANT_DISTANCE: str = "Cosine"     # Cosine | Euclid | Dot

    # ------------------------------------------------------------------
    # S3-compatible object storage (MinIO in dev, AWS S3 in prod)
    # ------------------------------------------------------------------
    S3_ENDPOINT_URL: str = "http://localhost:9000"
    S3_ACCESS_KEY_ID: str = "autofill_minio"
    S3_SECRET_ACCESS_KEY: str = Field(default="minio_secret_key")
    S3_BUCKET_RESUMES: str = "resumes"
    S3_BUCKET_EXPORTS: str = "exports"
    S3_REGION: str = "us-east-1"

    # ------------------------------------------------------------------
    # Resume encryption
    # AES-256-GCM key encoded as URL-safe base64 (32 raw bytes → 44 chars)
    # ------------------------------------------------------------------
    RESUME_ENCRYPTION_KEY: str = Field(
        description="AES-256 key for encrypting resumes at rest (32 bytes, base64-encoded)"
    )

    # ------------------------------------------------------------------
    # Authentication (Clerk)
    # ------------------------------------------------------------------
    CLERK_SECRET_KEY: str = Field(description="Clerk secret key for server-side JWT verification")
    CLERK_PUBLISHABLE_KEY: str = ""
    CLERK_WEBHOOK_SECRET: str = ""

    # ------------------------------------------------------------------
    # AI / LLM
    # ------------------------------------------------------------------
    OPENAI_API_KEY: str = Field(description="OpenAI API key (or compatible provider key)")
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    OPENAI_MODEL: str = "gpt-4o"
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-small"

    # Chunking parameters for RAG ingestion
    CHUNK_SIZE: int = 512           # tokens per chunk
    CHUNK_OVERLAP: int = 64         # token overlap between adjacent chunks
    RAG_TOP_K: int = 6              # top-K chunks retrieved per query
    RAG_SIMILARITY_THRESHOLD: float = 0.72  # minimum cosine similarity to include

    # Token budget guards
    MAX_CONTEXT_TOKENS: int = 12_000   # max tokens injected into answer gen prompt
    MAX_ANSWER_TOKENS: int = 800       # max tokens in a single generated answer

    # ------------------------------------------------------------------
    # Celery (async task queue)
    # ------------------------------------------------------------------
    CELERY_BROKER_URL: str = Field(
        default="redis://:redis_secret@localhost:6379/1",
        description="Celery broker — separate Redis DB from cache",
    )
    CELERY_RESULT_BACKEND: str = Field(
        default="redis://:redis_secret@localhost:6379/2",
    )

    # ------------------------------------------------------------------
    # Observability
    # ------------------------------------------------------------------
    LOG_LEVEL: str = "INFO"
    SENTRY_DSN: str = ""    # empty string disables Sentry


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Return a cached Settings singleton.
    The lru_cache ensures we parse env vars only once per process.
    """
    return Settings()


# Module-level convenience alias used everywhere in the codebase
settings: Settings = get_settings()