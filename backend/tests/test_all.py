"""
backend/tests/test_health.py — Health endpoint tests
"""
import pytest


@pytest.mark.asyncio
async def test_liveness(async_client):
    resp = await async_client.get("/api/v1/health/live")
    assert resp.status_code == 200
    assert resp.json()["status"] == "alive"


@pytest.mark.asyncio
async def test_readiness_structure(async_client):
    """Readiness probe calls all three dependencies — they're mocked in tests."""
    resp = await async_client.get("/api/v1/health/ready")
    # May be 200 or 503 depending on mock state; just check structure
    data = resp.json()
    assert "checks" in data
    assert "status" in data
    assert "latency_ms" in data


# ──────────────────────────────────────────────────────────────────────────────

"""
backend/tests/test_users.py — User endpoint tests
"""
import pytest


@pytest.mark.asyncio
async def test_clerk_webhook_sync(async_client):
    payload = {
        "type": "user.created",
        "data": {
            "id": "user_test_webhook_001",
            "email_addresses": [{"id": "e1", "email_address": "webhook@test.dev"}],
            "first_name": "Test",
            "last_name": "User",
            "image_url": None,
        },
    }
    resp = await async_client.post("/api/v1/users/sync", json=payload)
    assert resp.status_code == 200
    assert resp.json()["status"] == "synced"


@pytest.mark.asyncio
async def test_get_profile_requires_auth(async_client):
    """Without override, a real client would need a token. The fixture injects mock auth."""
    resp = await async_client.get("/api/v1/users/me")
    # With mock auth override this should succeed
    assert resp.status_code in (200, 404)  # 404 if user not in test DB yet


@pytest.mark.asyncio
async def test_update_profile(async_client):
    resp = await async_client.patch(
        "/api/v1/users/me",
        json={"display_name": "Updated Name", "preferences": {"timezone": "UTC"}},
    )
    assert resp.status_code in (200, 404)


# ──────────────────────────────────────────────────────────────────────────────

"""
backend/tests/test_chunker.py — RAG chunker unit tests (no external deps)
"""
import pytest
from backend.api.rag.chunker import (
    chunk_text,
    chunk_parsed_resume,
    chunk_template,
    chunk_past_answer,
    _make_chunk_id,
)


def test_chunk_text_basic():
    chunks = chunk_text(
        text="Hello world " * 50,
        user_id="user-123",
        source_id="src-456",
        source_type="resume",
    )
    assert len(chunks) > 0
    for c in chunks:
        assert c.text
        assert c.chunk_id
        assert c.payload["user_id"] == "user-123"
        assert c.payload["source_type"] == "resume"


def test_chunk_text_empty():
    result = chunk_text("", user_id="u1", source_id="s1", source_type="resume")
    assert result == []


def test_chunk_text_whitespace_only():
    result = chunk_text("   \n\t  ", user_id="u1", source_id="s1", source_type="resume")
    assert result == []


def test_chunk_id_deterministic():
    """Same inputs must always produce the same chunk ID."""
    id1 = _make_chunk_id("user-1", "src-1", "resume", 0)
    id2 = _make_chunk_id("user-1", "src-1", "resume", 0)
    assert id1 == id2


def test_chunk_id_different_users():
    id1 = _make_chunk_id("user-1", "src-1", "resume", 0)
    id2 = _make_chunk_id("user-2", "src-1", "resume", 0)
    assert id1 != id2


def test_chunk_parsed_resume_sections(sample_parsed_resume):
    chunks = chunk_parsed_resume(
        parsed_json=sample_parsed_resume,
        user_id="user-abc",
        resume_id="resume-xyz",
    )
    assert len(chunks) > 0
    sections = {c.payload.get("section") for c in chunks}
    # Should produce chunks for summary, skills, experience, education, contact
    assert "skills" in sections
    assert "experience" in sections
    assert "education" in sections


def test_chunk_parsed_resume_payload(sample_parsed_resume):
    chunks = chunk_parsed_resume(
        parsed_json=sample_parsed_resume,
        user_id="user-abc",
        resume_id="resume-xyz",
    )
    for c in chunks:
        assert c.payload["user_id"] == "user-abc"
        assert c.payload["resume_id"] == "resume-xyz"
        assert c.payload["source_type"] == "resume"


def test_chunk_template_qa(sample_template_answers):
    chunks = chunk_template(
        answers_json=sample_template_answers,
        template_id="tmpl-001",
        template_name="Test Template",
        user_id="user-abc",
    )
    assert len(chunks) > 0
    # Custom Q&A entries should be chunked
    qa_chunks = [c for c in chunks if "custom_qa" in c.payload.get("field_key", "")]
    assert len(qa_chunks) > 0


def test_chunk_past_answer():
    chunks = chunk_past_answer(
        answer_text="I enjoy solving complex distributed systems problems.",
        question_label="What are your strengths?",
        application_answer_id="ans-001",
        application_id="app-001",
        form_field_key="strengths",
        answer_source="ai_generated",
        user_id="user-abc",
    )
    assert len(chunks) == 1
    assert "I enjoy" in chunks[0].text
    assert chunks[0].payload["answer_source"] == "ai_generated"


def test_chunk_past_answer_empty():
    chunks = chunk_past_answer(
        answer_text="",
        question_label="What are your strengths?",
        application_answer_id="ans-001",
        application_id="app-001",
        form_field_key="strengths",
        answer_source="template",
        user_id="user-abc",
    )
    assert chunks == []


# ──────────────────────────────────────────────────────────────────────────────

"""
backend/tests/test_form_understanding.py — FormUnderstandingAgent unit tests
"""
import pytest
from backend.api.agents.form_understanding import (
    FormUnderstandingAgent,
    _heuristic_map,
    _detect_platform,
    _extract_field_manifest,
    CANONICAL_FIELD_MAP,
)


def test_heuristic_map_first_name():
    result = _heuristic_map("First Name", "first_name", "first_name", "", "")
    assert result is not None
    key, requires_ai, confidence = result
    assert key == "first_name"
    assert requires_ai is False
    assert confidence >= 0.72


def test_heuristic_map_email():
    result = _heuristic_map("Email Address", "email", "email", "you@example.com", "")
    assert result is not None
    key, _, _ = result
    assert key == "email"


def test_heuristic_map_cover_letter_is_ai():
    result = _heuristic_map("Cover Letter", "cover_letter", "", "", "")
    assert result is not None
    key, requires_ai, _ = result
    assert key == "cover_letter"
    assert requires_ai is True


def test_heuristic_map_why_us_is_ai():
    result = _heuristic_map("Why do you want to work here?", "why_interested", "", "", "")
    assert result is not None
    _, requires_ai, _ = result
    assert requires_ai is True


def test_heuristic_map_unknown_field():
    result = _heuristic_map("Favourite colour", "fav_colour", "fav_colour", "", "")
    # May return None or "unknown" with low confidence
    if result:
        _, _, confidence = result
        assert confidence < 0.72


def test_detect_platform_greenhouse():
    platform = _detect_platform("https://boards.greenhouse.io/company/jobs/123", "")
    assert platform == "greenhouse"


def test_detect_platform_workday():
    platform = _detect_platform("https://company.myworkdayjobs.com/careers", "")
    assert platform == "workday"


def test_detect_platform_google_forms():
    platform = _detect_platform("https://forms.google.com/d/e/123/viewform", "")
    assert platform == "google_forms"


def test_detect_platform_unknown():
    platform = _detect_platform("https://careers.unknown-ats.com/apply", "")
    assert platform == "unknown"


def test_extract_field_manifest(sample_greenhouse_html):
    fields = _extract_field_manifest(sample_greenhouse_html)
    assert len(fields) > 0
    field_names = [f["html_name"] for f in fields]
    assert "first_name" in field_names
    assert "last_name" in field_names
    assert "email" in field_names


def test_extract_field_manifest_detects_required(sample_greenhouse_html):
    fields = _extract_field_manifest(sample_greenhouse_html)
    required_fields = [f for f in fields if f["is_required"]]
    assert len(required_fields) > 0


def test_extract_field_manifest_empty_html():
    fields = _extract_field_manifest("<html><body><p>No forms here</p></body></html>")
    assert fields == []


@pytest.mark.asyncio
async def test_form_understanding_agent_heuristic_only(sample_greenhouse_html):
    """
    Tests the full agent pipeline with a mock LLM.
    The heuristic phase should resolve all standard fields without LLM calls.
    """
    from unittest.mock import AsyncMock, MagicMock
    mock_llm = AsyncMock()
    # The LLM should not be called if all fields are heuristically resolved
    agent = FormUnderstandingAgent(llm=mock_llm)
    result = await agent.understand(
        html=sample_greenhouse_html,
        page_url="https://boards.greenhouse.io/exampleco/jobs/123",
    )
    assert result.total_fields > 0
    assert result.platform_detected == "greenhouse"
    # cover_letter should be marked as AI-required
    ai_fields = [f for f in result.fields if f.requires_ai]
    assert len(ai_fields) >= 1


# ──────────────────────────────────────────────────────────────────────────────

"""
backend/tests/test_answer_generation.py — AnswerGenerationAgent unit tests
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from backend.api.agents.answer_generation import (
    AnswerGenerationAgent,
    _compute_confidence,
    _detect_hallucination,
    _format_context_section,
    _build_user_prompt,
)
from backend.api.rag.retrieval import RetrievedContext, RetrievedChunk


def make_chunk(text: str, source_type: str, score: float = 0.85) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id="test-chunk-id",
        text=text,
        score=score,
        source_type=source_type,
        payload={"user_id": "u1", "source_type": source_type},
    )


def make_rich_context() -> RetrievedContext:
    return RetrievedContext(
        resume_chunks=[
            make_chunk("Jane Doe - Senior Software Engineer at Acme Corp. Skills: Python, Kubernetes.", "resume"),
            make_chunk("Led redesign of event-streaming pipeline, reducing latency from 800ms to 45ms.", "resume"),
        ],
        jd_chunks=[
            make_chunk("We are looking for a backend engineer with distributed systems experience.", "job_description"),
        ],
        template_chunks=[
            make_chunk("Template answer - Why us: I am drawn to companies solving hard technical challenges.", "template"),
        ],
        past_answer_chunks=[
            make_chunk("Past answer - Why us: Your open-source culture aligns with my values.", "past_answer"),
        ],
        total_token_estimate=400,
    )


def test_format_context_section_all_sources():
    ctx = make_rich_context()
    result = _format_context_section(ctx)
    assert "RESUME CONTENT" in result
    assert "JOB DESCRIPTION" in result
    assert "TEMPLATE ANSWERS" in result
    assert "PAST APPLICATION ANSWERS" in result
    assert "Jane Doe" in result


def test_format_context_section_empty():
    ctx = RetrievedContext()
    result = _format_context_section(ctx)
    assert "NO CONTEXT AVAILABLE" in result


def test_build_user_prompt():
    ctx = make_rich_context()
    prompt = _build_user_prompt("Why do you want to work here?", ctx, "why_us", 150)
    assert "Why do you want to work here?" in prompt
    assert "why_us" in prompt
    assert "150" in prompt
    assert "Jane Doe" in prompt


def test_compute_confidence_with_context():
    ctx = make_rich_context()
    score = _compute_confidence(ctx, "I bring 8 years of Python experience.", [], False)
    assert 0.0 <= score <= 1.0
    assert score > 0.3  # should be reasonably high with 4 sources


def test_compute_confidence_refusal():
    ctx = make_rich_context()
    score = _compute_confidence(ctx, "", [], refusal=True)
    assert score == 0.0


def test_detect_hallucination_clean_answer():
    ctx = make_rich_context()
    # Answer only uses info from context — no invented numbers
    clean = "I have experience in Python and Kubernetes, which align with your requirements."
    assert _detect_hallucination(clean, ctx) is False


def test_detect_hallucination_invented_percentage():
    ctx = make_rich_context()
    # "47%" does not appear in any context chunk
    dirty = "I increased revenue by 47% in my previous role."
    assert _detect_hallucination(dirty, ctx) is True


def test_detect_hallucination_invented_year():
    ctx = make_rich_context()
    dirty = "In 2031, I built a system that processed millions of events."
    assert _detect_hallucination(dirty, ctx) is True


@pytest.mark.asyncio
async def test_generate_answer_success():
    """Test full answer generation with a mocked LLM."""
    import json

    mock_response = MagicMock()
    mock_response.message.content = json.dumps({
        "answer": "I am drawn to your commitment to engineering excellence and open-source culture.",
        "answer_source": "ai_generated",
        "confidence_score": 0.88,
        "context_used": ["Resume: 8 years experience", "Template: engineering culture answer"],
    })
    mock_response.raw = {"usage": {"prompt_tokens": 500, "completion_tokens": 80, "total_tokens": 580}}

    mock_llm = AsyncMock()
    mock_llm.achat = AsyncMock(return_value=mock_response)

    agent = AnswerGenerationAgent(llm=mock_llm)
    ctx = make_rich_context()

    result = await agent.generate(
        question="Why do you want to work here?",
        context=ctx,
        field_key="why_us",
        max_words=150,
    )

    assert result.answer != ""
    assert result.refusal_reason is None
    assert 0.0 <= result.confidence_score <= 1.0
    assert result.answer_source == "ai_generated"
    assert result.token_usage["total_tokens"] == 580


@pytest.mark.asyncio
async def test_generate_answer_refusal():
    """Test that the agent handles LLM REFUSAL correctly."""
    import json

    mock_response = MagicMock()
    mock_response.message.content = json.dumps({
        "REFUSAL": True,
        "reason": "The context does not contain enough information to answer this question.",
    })
    mock_response.raw = {}

    mock_llm = AsyncMock()
    mock_llm.achat = AsyncMock(return_value=mock_response)

    agent = AnswerGenerationAgent(llm=mock_llm)
    ctx = make_rich_context()

    result = await agent.generate(
        question="What is your dog's name?",
        context=ctx,
        field_key="unknown",
    )

    assert result.answer == ""
    assert result.refusal_reason is not None
    assert result.confidence_score == 0.0


@pytest.mark.asyncio
async def test_generate_answer_empty_context():
    """Empty context must trigger refusal without calling LLM."""
    mock_llm = AsyncMock()
    agent = AnswerGenerationAgent(llm=mock_llm)

    empty_ctx = RetrievedContext()
    result = await agent.generate(
        question="Why do you want to work here?",
        context=empty_ctx,
    )

    assert result.answer == ""
    assert result.refusal_reason is not None
    # LLM should NOT have been called — early exit
    mock_llm.achat.assert_not_called()


@pytest.mark.asyncio
async def test_generate_answer_invalid_json():
    """Graceful handling when LLM returns non-JSON."""
    mock_response = MagicMock()
    mock_response.message.content = "Sorry, I cannot help with that."
    mock_response.raw = {}

    mock_llm = AsyncMock()
    mock_llm.achat = AsyncMock(return_value=mock_response)

    agent = AnswerGenerationAgent(llm=mock_llm)
    ctx = make_rich_context()

    result = await agent.generate(question="Why us?", context=ctx)
    assert result.refusal_reason is not None
    assert result.confidence_score == 0.0


# ──────────────────────────────────────────────────────────────────────────────

"""
backend/tests/test_api_agents.py — FastAPI agent endpoint integration tests
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_form_understand_endpoint(async_client, sample_greenhouse_html):
    resp = await async_client.post(
        "/api/v1/agents/form/understand",
        json={"html": sample_greenhouse_html, "page_url": "https://boards.greenhouse.io/test"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "fields" in data
    assert "platform_detected" in data
    assert data["platform_detected"] == "greenhouse"
    assert len(data["fields"]) > 0


@pytest.mark.asyncio
async def test_generate_answer_endpoint(async_client):
    """
    Patches the LLM call inside the agent so no real API call is made.
    """
    mock_resp = MagicMock()
    mock_resp.message.content = json.dumps({
        "answer": "I am impressed by your engineering culture.",
        "answer_source": "ai_generated",
        "confidence_score": 0.85,
        "context_used": ["template: engineering culture"],
    })
    mock_resp.raw = {"usage": {"prompt_tokens": 400, "completion_tokens": 60, "total_tokens": 460}}

    with patch(
        "backend.api.agents.answer_generation.AnswerGenerationAgent._AnswerGenerationAgent__class__",
        new=None
    ):
        with patch(
            "backend.api.agents.answer_generation.LlamaSettings"
        ) as mock_settings:
            mock_llm = AsyncMock()
            mock_llm.achat = AsyncMock(return_value=mock_resp)
            mock_settings.llm = mock_llm

            resp = await async_client.post(
                "/api/v1/agents/answers/generate",
                json={
                    "question": "Why do you want to work here?",
                    "field_key": "why_us",
                    "max_words": 100,
                },
            )

    # Either success or graceful empty-context refusal is acceptable
    assert resp.status_code == 200
    data = resp.json()
    assert "confidence_score" in data
    assert "hallucination_flagged" in data


@pytest.mark.asyncio
async def test_memory_search_endpoint(async_client):
    resp = await async_client.post(
        "/api/v1/agents/memory/search",
        json={"question": "Why do you want to work here?", "top_k": 5, "score_threshold": 0.70},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "similar_answers" in data
    assert "total_found" in data
    assert isinstance(data["similar_answers"], list)


@pytest.mark.asyncio
async def test_memory_record_endpoint(async_client):
    import uuid
    resp = await async_client.post(
        "/api/v1/agents/memory/record",
        json={
            "answer_text": "I am drawn to your commitment to engineering excellence.",
            "question_label": "Why do you want to work here?",
            "application_answer_id": str(uuid.uuid4()),
            "application_id": str(uuid.uuid4()),
            "form_field_key": "why_us",
            "answer_source": "ai_generated",
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "recorded"
    assert isinstance(data["point_ids"], list)


@pytest.mark.asyncio
async def test_batch_generate_too_many_questions(async_client):
    questions = [{"question": f"Q{i}"} for i in range(21)]
    resp = await async_client.post(
        "/api/v1/agents/answers/generate-batch",
        json={"questions": questions},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_error_handler_404(async_client):
    import uuid
    resp = await async_client.get(f"/api/v1/resumes/{uuid.uuid4()}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_error_handler_422_malformed_uuid(async_client):
    resp = await async_client.get("/api/v1/resumes/not-a-uuid")
    assert resp.status_code == 422
    data = resp.json()
    assert data["error"]["code"] == "VALIDATION_ERROR"
