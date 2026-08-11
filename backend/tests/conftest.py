"""
backend/tests/conftest.py
==========================
Pytest fixtures shared across all test modules.

Test strategy:
  - Uses pytest-asyncio with asyncio_mode = "auto"
  - A real (but isolated) test database is used (separate DB schema)
  - Qdrant and Redis are mocked at the client level to avoid needing
    live services for unit/integration tests
  - Use `pytest -m integration` to run tests that require live services
"""

import asyncio
import os
import uuid
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Point at a test database (SQLite for speed, Postgres for full compat)
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "sqlite+aiosqlite:///./test_autofill.db",
)

# Minimal env overrides so Settings doesn't raise on missing vars
os.environ.setdefault("DATABASE_URL", TEST_DATABASE_URL)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("QDRANT_HOST", "localhost")
os.environ.setdefault("QDRANT_API_KEY", "test_key")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-key")
os.environ.setdefault("RESUME_ENCRYPTION_KEY", "dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdGs=")
os.environ.setdefault("CLERK_SECRET_KEY", "sk_test_mock")


# ---------------------------------------------------------------------------
# Async engine + session for tests
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def event_loop():
    """Session-scoped event loop so async fixtures share one loop."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def test_engine():
    from backend.api.models.base import Base

    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(test_engine) -> AsyncGenerator[AsyncSession, None]:
    """Function-scoped DB session that rolls back after each test."""
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        async with session.begin():
            yield session
            await session.rollback()


# ---------------------------------------------------------------------------
# Mock Redis
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_redis():
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)    # cache miss by default
    redis.set = AsyncMock(return_value=True)
    redis.setex = AsyncMock(return_value=True)
    redis.mget = AsyncMock(return_value=[None])
    redis.delete = AsyncMock(return_value=1)
    redis.exists = AsyncMock(return_value=0)
    redis.ping = AsyncMock(return_value=True)
    redis.pipeline = MagicMock(return_value=AsyncMock())
    return redis


# ---------------------------------------------------------------------------
# Mock Qdrant
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_qdrant():
    qdrant = AsyncMock()
    qdrant.get_collections = AsyncMock(return_value=MagicMock(collections=[]))
    qdrant.upsert = AsyncMock(return_value=None)
    qdrant.search = AsyncMock(return_value=[])
    qdrant.delete = AsyncMock(return_value=None)
    qdrant.get_collection = AsyncMock(return_value=MagicMock())
    qdrant.close = AsyncMock(return_value=None)
    return qdrant


# ---------------------------------------------------------------------------
# Mock OpenAI embeddings
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_embed_text():
    """Mock embed_text to return a deterministic zero vector."""
    with patch("backend.api.rag.embeddings.embed_text") as mock:
        mock.return_value = [0.0] * 1536
        yield mock


@pytest.fixture
def mock_embed_batch():
    with patch("backend.api.rag.embeddings.embed_batch") as mock:
        mock.side_effect = lambda texts, redis, **kw: [[0.0] * 1536 for _ in texts]
        yield mock


# ---------------------------------------------------------------------------
# FastAPI test client (with dependency overrides)
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def async_client(db_session, mock_redis, mock_qdrant) -> AsyncGenerator[AsyncClient, None]:
    """Async HTTP client with all external deps mocked."""
    from backend.api.main import create_app
    from backend.api.db.session import get_db
    from backend.api.db.redis_client import get_redis
    from backend.api.db.qdrant_client import get_qdrant
    from backend.api.core.auth import verify_clerk_token, CurrentUser

    app = create_app()

    # Override DB
    async def override_get_db():
        yield db_session

    # Override Redis
    async def override_get_redis():
        return mock_redis

    # Override Qdrant
    async def override_get_qdrant():
        return mock_qdrant

    # Override auth — return a mock user
    async def override_auth():
        return CurrentUser(
            user_id=str(uuid.uuid4()),
            clerk_user_id="user_test_001",
            email="test@autofill.dev",
            plan_tier="pro",
        )

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis] = override_get_redis
    app.dependency_overrides[get_qdrant] = override_get_qdrant
    app.dependency_overrides[verify_clerk_token] = override_auth

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        yield client


# ---------------------------------------------------------------------------
# Sample test data factories
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_parsed_resume() -> dict:
    return {
        "name": "Jane Doe",
        "email": "jane@example.com",
        "phone": "+1-555-0100",
        "location": "San Francisco, CA",
        "linkedin_url": "https://linkedin.com/in/janedoe",
        "github_url": "https://github.com/janedoe",
        "portfolio_url": "https://janedoe.dev",
        "summary": "Experienced software engineer with 8 years building distributed systems.",
        "skills": ["Python", "Go", "Kubernetes", "PostgreSQL", "Redis", "Kafka"],
        "experience": [
            {
                "company": "Acme Corp",
                "title": "Senior Software Engineer",
                "start_date": "Jan 2020",
                "end_date": "Present",
                "description": "Lead backend infrastructure team. Designed microservices architecture.",
                "technologies": ["Python", "Kubernetes", "PostgreSQL"],
            }
        ],
        "education": [
            {
                "institution": "UC Berkeley",
                "degree": "B.S.",
                "field": "Computer Science",
                "graduation_year": 2016,
                "gpa": None,
            }
        ],
        "certifications": ["AWS Solutions Architect"],
        "languages": ["English", "Spanish"],
        "confidence_score": 0.92,
        "extraction_warnings": [],
    }


@pytest.fixture
def sample_template_answers() -> dict:
    return {
        "first_name": "Jane",
        "last_name": "Doe",
        "email": "jane@example.com",
        "phone": "+1-555-0100",
        "linkedin_url": "https://linkedin.com/in/janedoe",
        "salary_expectation": "180000",
        "willing_to_relocate": False,
        "visa_sponsorship_required": False,
        "cover_letter": "I am a passionate engineer with 8 years of experience.",
        "why_us": "Your engineering culture aligns with my values.",
        "custom_qa": [
            {
                "question": "Describe a technical challenge.",
                "answer": "I redesigned our streaming pipeline to handle 10x traffic.",
            }
        ],
    }


@pytest.fixture
def sample_greenhouse_html() -> str:
    return """
    <html><body><form id="application_form">
      <div><label for="first_name">First Name *</label>
        <input type="text" id="first_name" name="first_name" required /></div>
      <div><label for="last_name">Last Name *</label>
        <input type="text" id="last_name" name="last_name" required /></div>
      <div><label for="email">Email Address *</label>
        <input type="email" id="email" name="email" required /></div>
      <div><label for="phone">Phone Number</label>
        <input type="tel" id="phone" name="phone" /></div>
      <div><label for="linkedin_profile">LinkedIn Profile</label>
        <input type="url" id="linkedin_profile" name="linkedin_profile" /></div>
      <div><label for="cover_letter">Cover Letter</label>
        <textarea id="cover_letter" name="cover_letter" rows="8"></textarea></div>
      <div><label for="why_interested">Why are you interested in this role?</label>
        <textarea id="why_interested" name="why_interested" rows="6"></textarea></div>
    </form></body></html>
    """
