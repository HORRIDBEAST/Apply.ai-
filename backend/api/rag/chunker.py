"""
backend/api/rag/chunker.py
==========================
Text chunking utilities for RAG ingestion.

Why custom chunking instead of LlamaIndex's built-in splitter?
  - We need deterministic chunk IDs (UUIDs derived from content hash)
    so upserts are idempotent and we can track point IDs in PostgreSQL.
  - We need structured metadata (source_type, section, chunk_index)
    attached to each chunk before embedding.
  - We operate on pre-parsed dicts (resume JSON, template JSON) and
    on plain text (job descriptions, raw answers) — so we need both
    a structured-dict chunker and a plain-text chunker.

Chunk ID strategy:
  Each chunk gets a deterministic UUID derived from:
    SHA-256(user_id + source_id + source_type + chunk_index)
  This means re-ingesting the same content produces the same point IDs,
  making Qdrant upsert idempotent and the PostgreSQL point-ID list stable.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from typing import Any

import tiktoken

from backend.api.core.config import settings
from backend.api.core.logging import get_logger

logger = get_logger(__name__)

# Tokeniser used for chunk-size measurement (matches text-embedding-3-small)
_TOKENISER = tiktoken.get_encoding("cl100k_base")


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class TextChunk:
    """
    A single chunk ready for embedding and Qdrant upsert.

    Attributes:
        chunk_id:     Deterministic UUID (used as Qdrant point ID).
        text:         Raw text content to embed.
        token_count:  Number of tokens in `text`.
        payload:      Metadata dict stored in Qdrant alongside the vector.
                      Always includes: user_id, source_type, chunk_index.
    """
    chunk_id: uuid.UUID
    text: str
    token_count: int
    payload: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Deterministic chunk ID helper
# ---------------------------------------------------------------------------

def _make_chunk_id(
    user_id: str,
    source_id: str,
    source_type: str,
    chunk_index: int,
) -> uuid.UUID:
    """
    Generate a deterministic UUID for a chunk using SHA-256.
    Produces the same UUID for the same (user, source, index) triple,
    making Qdrant upserts idempotent.
    """
    raw = f"{user_id}::{source_type}::{source_id}::{chunk_index}"
    digest = hashlib.sha256(raw.encode()).digest()[:16]
    # Overlay UUID version 5 bits manually
    return uuid.UUID(bytes=digest, version=5)


# ---------------------------------------------------------------------------
# Plain-text chunker
# ---------------------------------------------------------------------------

def chunk_text(
    text: str,
    user_id: str,
    source_id: str,
    source_type: str,
    base_payload: dict[str, Any] | None = None,
    chunk_size: int = settings.CHUNK_SIZE,
    chunk_overlap: int = settings.CHUNK_OVERLAP,
) -> list[TextChunk]:
    """
    Split `text` into overlapping token-bounded chunks.

    Uses tiktoken for accurate token counting (same tokeniser as the
    embedding model), ensuring we never exceed the model's input limit.

    Args:
        text:         The raw text to chunk.
        user_id:      Owner's user UUID string (stored in payload for filtering).
        source_id:    ID of the source document (resume_id, answer_id, etc.).
        source_type:  One of: "resume", "past_answer", "job_description", "template".
        base_payload: Additional payload fields merged into every chunk's payload.
        chunk_size:   Max tokens per chunk.
        chunk_overlap: Token overlap between consecutive chunks.

    Returns:
        List of TextChunk objects ready for embedding.
    """
    if not text or not text.strip():
        return []

    tokens: list[int] = _TOKENISER.encode(text)
    total_tokens = len(tokens)

    if total_tokens == 0:
        return []

    chunks: list[TextChunk] = []
    start = 0
    chunk_index = 0

    while start < total_tokens:
        end = min(start + chunk_size, total_tokens)
        chunk_tokens = tokens[start:end]
        chunk_text_str = _TOKENISER.decode(chunk_tokens)

        # Build payload
        payload: dict[str, Any] = {
            "user_id": user_id,
            "source_id": source_id,
            "source_type": source_type,
            "chunk_index": chunk_index,
            "token_count": len(chunk_tokens),
        }
        if base_payload:
            payload.update(base_payload)

        chunks.append(
            TextChunk(
                chunk_id=_make_chunk_id(user_id, source_id, source_type, chunk_index),
                text=chunk_text_str,
                token_count=len(chunk_tokens),
                payload=payload,
            )
        )

        chunk_index += 1
        # Advance by (chunk_size - overlap) to create sliding window
        advance = max(1, chunk_size - chunk_overlap)
        start += advance

    logger.debug(
        "Text chunked",
        source_type=source_type,
        source_id=source_id,
        total_tokens=total_tokens,
        chunks_produced=len(chunks),
    )
    return chunks


# ---------------------------------------------------------------------------
# Structured resume chunker
# ---------------------------------------------------------------------------

def chunk_parsed_resume(
    parsed_json: dict[str, Any],
    user_id: str,
    resume_id: str,
) -> list[TextChunk]:
    """
    Convert a parsed resume dict into semantically meaningful chunks.

    Strategy: chunk by section (summary, each experience entry, each
    education entry, skills block) rather than blindly splitting raw text.
    This preserves semantic coherence — an experience entry stays whole
    so the retriever can return the full context of a job role.

    Args:
        parsed_json:  Output of ResumeExtractorAgent (validated Pydantic dict).
        user_id:      Owner's UUID string.
        resume_id:    UUID of the `resumes` table row.

    Returns:
        List of TextChunk objects.
    """
    chunks: list[TextChunk] = []
    base_payload = {
        "resume_id": resume_id,
        "source_type": "resume",
    }

    # ---- Summary block -------------------------------------------------
    summary = parsed_json.get("summary", "")
    if summary:
        name = parsed_json.get("name", "")
        combined = f"Professional Summary for {name}:\n{summary}"
        chunks.extend(
            chunk_text(
                text=combined,
                user_id=user_id,
                source_id=resume_id,
                source_type="resume",
                base_payload={**base_payload, "section": "summary"},
            )
        )

    # ---- Skills block --------------------------------------------------
    skills: list[str] = parsed_json.get("skills", [])
    if skills:
        skills_text = "Technical Skills and Competencies:\n" + ", ".join(skills)
        chunks.extend(
            chunk_text(
                text=skills_text,
                user_id=user_id,
                source_id=resume_id,
                source_type="resume",
                base_payload={**base_payload, "section": "skills"},
            )
        )

    # ---- Experience entries (one chunk per role) -----------------------
    for i, exp in enumerate(parsed_json.get("experience", [])):
        company = exp.get("company", "")
        title = exp.get("title", "")
        start = exp.get("start_date", "")
        end = exp.get("end_date", "Present")
        description = exp.get("description", "")

        exp_text = (
            f"Work Experience — {title} at {company} "
            f"({start} to {end}):\n{description}"
        )
        chunks.extend(
            chunk_text(
                text=exp_text,
                user_id=user_id,
                source_id=resume_id,
                source_type="resume",
                base_payload={
                    **base_payload,
                    "section": "experience",
                    "experience_index": i,
                    "company": company,
                    "title": title,
                },
            )
        )

    # ---- Education entries (one chunk per institution) -----------------
    for i, edu in enumerate(parsed_json.get("education", [])):
        institution = edu.get("institution", "")
        degree = edu.get("degree", "")
        field_of_study = edu.get("field", "")
        grad_year = edu.get("graduation_year", "")

        edu_text = (
            f"Education — {degree} in {field_of_study} "
            f"from {institution} (graduated {grad_year})"
        )
        chunks.extend(
            chunk_text(
                text=edu_text,
                user_id=user_id,
                source_id=resume_id,
                source_type="resume",
                base_payload={
                    **base_payload,
                    "section": "education",
                    "education_index": i,
                    "institution": institution,
                },
            )
        )

    # ---- Certifications -----------------------------------------------
    certs: list[str] = parsed_json.get("certifications", [])
    if certs:
        cert_text = "Certifications:\n" + "\n".join(f"- {c}" for c in certs)
        chunks.extend(
            chunk_text(
                text=cert_text,
                user_id=user_id,
                source_id=resume_id,
                source_type="resume",
                base_payload={**base_payload, "section": "certifications"},
            )
        )

    # ---- Contact / identity block (useful for autofill field mapping) --
    contact_parts = []
    for field_name in ("name", "email", "phone", "location", "linkedin_url", "github_url", "portfolio_url"):
        value = parsed_json.get(field_name)
        if value:
            contact_parts.append(f"{field_name}: {value}")
    if contact_parts:
        contact_text = "Contact Information:\n" + "\n".join(contact_parts)
        chunks.extend(
            chunk_text(
                text=contact_text,
                user_id=user_id,
                source_id=resume_id,
                source_type="resume",
                base_payload={**base_payload, "section": "contact"},
            )
        )

    logger.info(
        "Resume chunked",
        resume_id=resume_id,
        user_id=user_id,
        total_chunks=len(chunks),
    )
    return chunks


# ---------------------------------------------------------------------------
# Template chunker
# ---------------------------------------------------------------------------

def chunk_template(
    answers_json: dict[str, Any],
    template_id: str,
    template_name: str,
    user_id: str,
) -> list[TextChunk]:
    """
    Convert a template's answers_json into chunks.

    Each question-answer pair becomes a standalone chunk so the retriever
    can surface the most relevant pre-written answer for a given question.

    Args:
        answers_json:   Template answers dict (from templates.answers_json).
        template_id:    UUID of the template row.
        template_name:  Display name (added to payload for provenance).
        user_id:        Owner's UUID string.

    Returns:
        List of TextChunk objects.
    """
    chunks: list[TextChunk] = []
    base_payload = {
        "template_id": template_id,
        "template_name": template_name,
        "source_type": "template",
    }

    # Iterate over all answer fields
    for key, value in answers_json.items():
        if not value:
            continue

        # Handle the custom Q&A array: [{"question": ..., "answer": ...}, ...]
        if key == "custom_qa" and isinstance(value, list):
            for i, qa in enumerate(value):
                question = qa.get("question", "")
                answer = qa.get("answer", "")
                if question and answer:
                    qa_text = f"Application Question: {question}\nTemplate Answer: {answer}"
                    chunks.extend(
                        chunk_text(
                            text=qa_text,
                            user_id=user_id,
                            source_id=template_id,
                            source_type="template",
                            base_payload={
                                **base_payload,
                                "field_key": f"custom_qa_{i}",
                                "question_preview": question[:100],
                            },
                        )
                    )
        elif isinstance(value, str) and len(value) > 20:
            # Only chunk text fields long enough to be meaningful
            field_text = f"Field '{key}':\n{value}"
            chunks.extend(
                chunk_text(
                    text=field_text,
                    user_id=user_id,
                    source_id=template_id,
                    source_type="template",
                    base_payload={**base_payload, "field_key": key},
                )
            )

    logger.info(
        "Template chunked",
        template_id=template_id,
        user_id=user_id,
        total_chunks=len(chunks),
    )
    return chunks


# ---------------------------------------------------------------------------
# Past answer chunker
# ---------------------------------------------------------------------------

def chunk_past_answer(
    answer_text: str,
    question_label: str,
    application_answer_id: str,
    application_id: str,
    form_field_key: str | None,
    answer_source: str,
    user_id: str,
) -> list[TextChunk]:
    """
    Chunk a single past application answer for RAG memory.

    Each answer is typically short enough to fit in one chunk,
    but we use chunk_text() for consistent handling.

    Args:
        answer_text:            The answer the user submitted.
        question_label:         The form field label (e.g. "Why do you want to work here?").
        application_answer_id:  UUID of the application_answers row.
        application_id:         UUID of the parent application.
        form_field_key:         Canonical field key (may be None for open questions).
        answer_source:          How the answer was produced (template / ai_generated / user_override).
        user_id:                Owner's UUID string.

    Returns:
        List of TextChunk objects (usually just one).
    """
    if not answer_text or not answer_text.strip():
        return []

    # Combine question + answer so the retriever can match on question semantics
    combined_text = (
        f"Application Question: {question_label}\n"
        f"Answer Used: {answer_text}"
    )

    return chunk_text(
        text=combined_text,
        user_id=user_id,
        source_id=application_answer_id,
        source_type="past_answer",
        base_payload={
            "application_answer_id": application_answer_id,
            "application_id": application_id,
            "form_field_key": form_field_key or "",
            "answer_source": answer_source,
            "question_label": question_label[:200],  # truncate for payload size
        },
    )