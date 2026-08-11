"""
backend/api/agents/base.py
==========================
Shared bootstrap, Pydantic output schemas, and utility types used by all
four agents (ResumeExtractor, FormUnderstanding, AnswerGeneration, ApplicationMemory).

Design decisions
----------------
* One global LlamaIndex Settings object is configured here and imported
  by every agent — avoids re-initialising the OpenAI client per-request.
* All agent *output* types are Pydantic v2 models so FastAPI can serialise
  them directly and the caller always gets a validated, typed result.
* Agents are stateless classes — they hold no per-user data.  All context
  is passed in as arguments to the `run()` method.
"""

from __future__ import annotations

import os
from typing import Any

from llama_index.core import Settings as LlamaSettings
from llama_index.embeddings.openai import OpenAIEmbedding
from llama_index.llms.openai import OpenAI as LlamaOpenAI
from pydantic import BaseModel, Field

from backend.api.core.config import settings
from backend.api.core.logging import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# One-time LlamaIndex global settings bootstrap
# Call configure_llama_settings() once at app startup (from lifespan).
# ---------------------------------------------------------------------------

_llama_configured = False


def configure_llama_settings() -> None:
    """
    Configure LlamaIndex global Settings singleton.

    Must be called once before any agent is used.
    Safe to call multiple times (idempotent).
    """
    global _llama_configured
    if _llama_configured:
        return

    # LLM used for all reasoning / generation
    LlamaSettings.llm = LlamaOpenAI(
        model=settings.OPENAI_MODEL,
        api_key=settings.OPENAI_API_KEY,
        api_base=settings.OPENAI_BASE_URL,
        temperature=0.0,          # Deterministic — no creative hallucination
        max_tokens=settings.MAX_ANSWER_TOKENS,
        timeout=60.0,
        max_retries=3,
    )

    # Embedding model (must match Qdrant collection vector size)
    LlamaSettings.embed_model = OpenAIEmbedding(
        model=settings.OPENAI_EMBEDDING_MODEL,
        api_key=settings.OPENAI_API_KEY,
        api_base=settings.OPENAI_BASE_URL,
        dimensions=settings.QDRANT_VECTOR_SIZE,
    )

    # Chunking defaults (agents that need chunking use our custom chunker,
    # but LlamaIndex internals respect these as a fallback)
    LlamaSettings.chunk_size = settings.CHUNK_SIZE
    LlamaSettings.chunk_overlap = settings.CHUNK_OVERLAP

    _llama_configured = True
    logger.info(
        "LlamaIndex Settings configured",
        llm_model=settings.OPENAI_MODEL,
        embed_model=settings.OPENAI_EMBEDDING_MODEL,
    )


# ---------------------------------------------------------------------------
# ── Output schemas ──────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

# ── ResumeExtractorAgent output ─────────────────────────────────────────────

class ExperienceEntry(BaseModel):
    company: str = ""
    title: str = ""
    start_date: str = ""
    end_date: str | None = None
    description: str = ""
    technologies: list[str] = Field(default_factory=list)


class EducationEntry(BaseModel):
    institution: str = ""
    degree: str = ""
    field: str = ""
    graduation_year: int | None = None
    gpa: str | None = None


class ParsedResume(BaseModel):
    """
    Strict output schema for ResumeExtractorAgent.
    Matches blueprint spec + extended fields for autofill utility.
    All fields optional so partial parses don't break the pipeline.
    """
    name: str = ""
    email: str = ""
    phone: str | None = None
    location: str | None = None
    linkedin_url: str | None = None
    github_url: str | None = None
    portfolio_url: str | None = None
    summary: str | None = None
    skills: list[str] = Field(default_factory=list)
    experience: list[ExperienceEntry] = Field(default_factory=list)
    education: list[EducationEntry] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    # Extraction metadata
    confidence_score: float = Field(default=0.0, ge=0.0, le=1.0)
    extraction_warnings: list[str] = Field(default_factory=list)


# ── FormUnderstandingAgent output ────────────────────────────────────────────

class DetectedField(BaseModel):
    """A single form field extracted from raw HTML."""
    label: str = ""
    html_name: str | None = None
    html_id: str | None = None
    html_placeholder: str | None = None
    aria_label: str | None = None
    field_type: str = "text"      # matches FieldType enum values
    mapped_field: str = ""        # canonical template key (e.g. "first_name")
    is_required: bool = False
    options: list[str] = Field(default_factory=list)   # for select/radio/checkbox
    requires_ai: bool = False     # True for open-text questions
    mapping_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    xpath: str | None = None
    css_selector: str | None = None
    display_order: int = 0


class FormUnderstandingResult(BaseModel):
    """Full output of FormUnderstandingAgent for one page of HTML."""
    fields: list[DetectedField] = Field(default_factory=list)
    platform_detected: str = "unknown"
    total_fields: int = 0
    ai_required_count: int = 0
    parsing_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    warnings: list[str] = Field(default_factory=list)


# ── AnswerGenerationAgent output ─────────────────────────────────────────────

class ContextSource(BaseModel):
    """One retrieved context chunk used in an answer."""
    source_type: str     # "resume" | "past_answer" | "job_description" | "template"
    text_preview: str    # First 200 chars for audit trail
    score: float


class GeneratedAnswer(BaseModel):
    """
    Full output of AnswerGenerationAgent for a single form question.
    Includes the answer text plus full audit trail.
    """
    answer: str
    confidence_score: float = Field(ge=0.0, le=1.0)
    answer_source: str    # "template" | "resume" | "ai_generated" | "past_answer"
    context_sources: list[ContextSource] = Field(default_factory=list)
    hallucination_flagged: bool = False
    refusal_reason: str | None = None   # populated if the agent refused to answer
    token_usage: dict[str, int] = Field(default_factory=dict)
    latency_ms: int = 0


# ── ApplicationMemoryAgent output ────────────────────────────────────────────

class SimilarAnswer(BaseModel):
    """A past answer retrieved from memory."""
    question_label: str
    answer_text: str
    application_id: str
    score: float
    source_type: str


class MemorySearchResult(BaseModel):
    """Output of ApplicationMemoryAgent.search()."""
    similar_answers: list[SimilarAnswer] = Field(default_factory=list)
    total_found: int = 0