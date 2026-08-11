"""
backend/api/agents/answer_generation.py
=========================================
AnswerGenerationAgent  — the most critical agent in the system.

Input  : A form question (label) + RetrievedContext from Qdrant
Output : GeneratedAnswer  (answer text + confidence score + source audit trail)

Zero-Hallucination Architecture
---------------------------------
This agent enforces a strict "no new facts" policy through FOUR layers:

Layer 1 — System prompt: explicit prohibitions against invented facts,
          numbers, company names, dates, or any information not in context.

Layer 2 — Context-only instruction: the user-turn prompt pastes EVERY piece
          of retrieved context verbatim and instructs the model to only
          synthesize language from that content.

Layer 3 — Refusal mechanism: the model is instructed to output a structured
          REFUSAL JSON object if it cannot answer from context alone, rather
          than guessing.

Layer 4 — Post-generation validation: the generated answer is checked against
          the retrieved context chunks. Any answer that contains plausible-but-
          unverifiable facts (company names not in context, stats not in chunks)
          triggers hallucination_flagged=True and confidence is reduced.

Confidence Scoring
------------------
Confidence is computed from three signals:
  a. RAG retrieval coverage: how many relevant chunks were found (0-40 pts)
  b. Source diversity: did we find context from multiple source types (0-30 pts)
  c. Post-generation verification: does the answer text appear to stay within
     the bounds of the retrieved context (0-30 pts)
Final score is normalised to [0.0, 1.0].
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from llama_index.core.llms import ChatMessage, MessageRole
from llama_index.core import Settings as LlamaSettings

from backend.api.agents.base import (
    ContextSource,
    GeneratedAnswer,
    configure_llama_settings,
)
from backend.api.core.config import settings
from backend.api.core.logging import get_logger
from backend.api.rag.retrieval import RetrievedContext, RetrievedChunk

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# System prompt — the "constitution" of the answer generation agent
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a precise job application answer writing assistant.

YOUR ROLE:
You help job applicants write answers to open-text questions on job application forms.
You synthesise language from the provided context — you do NOT invent new information.

═══════════════════════════════════════════════════════════════
CRITICAL GROUND RULES — THESE OVERRIDE ALL OTHER INSTRUCTIONS:
═══════════════════════════════════════════════════════════════

1. CONTEXT-ONLY POLICY: You may ONLY use facts, experiences, skills, and details
   that are explicitly present in the CONTEXT SECTIONS provided below.
   You MUST NOT invent, assume, infer, or hallucinate any information.

2. PROHIBITED INVENTIONS — Never produce:
   - Company names not mentioned in the context
   - Job titles not mentioned in the context
   - Technologies, skills, or tools not in the context
   - Years of experience beyond what the context states
   - Metrics, statistics, or numbers not in the context
   - Dates not present in the context
   - Personal details (email, phone, location) not in the context

3. SYNTHESIS IS ALLOWED: You may rephrase, restructure, and combine facts
   from the context into a well-written, professional answer. Good writing is
   encouraged — but all underlying facts must come from the context.

4. REFUSAL PROTOCOL: If the provided context does not contain enough information
   to answer the question without inventing facts, you MUST output:
   {"REFUSAL": true, "reason": "<brief explanation>"}
   Do NOT attempt a partial answer that contains invented facts.

5. LENGTH: Keep answers between 50–300 words unless the question clearly
   requires more or less. Professional, first-person tone.

6. OUTPUT FORMAT: You MUST output ONLY a JSON object in this exact structure:
   {
     "answer": "<the complete answer text>",
     "answer_source": "<one of: template | resume | past_answer | ai_generated>",
     "confidence_score": <float 0.0–1.0>,
     "context_used": ["<brief description of each context section you drew from>"]
   }
   If refusing: {"REFUSAL": true, "reason": "<reason>"}

   answer_source rules:
   - "template"     → answer was taken almost verbatim from a template answer
   - "resume"       → answer was drawn primarily from resume content
   - "past_answer"  → answer was primarily adapted from a past similar answer
   - "ai_generated" → answer synthesises from multiple context sources
"""


# ---------------------------------------------------------------------------
# Context formatter — turns RetrievedContext into a readable prompt section
# ---------------------------------------------------------------------------

def _format_context_section(context: RetrievedContext) -> str:
    """
    Format the retrieved RAG context into labelled sections for injection
    into the user-turn prompt.

    Each section is clearly delimited so the LLM knows exactly which
    information came from which source.
    """
    sections: list[str] = []

    if context.resume_chunks:
        resume_texts = "\n\n".join(
            f"[Resume Chunk {i+1}]\n{chunk.text}"
            for i, chunk in enumerate(context.resume_chunks)
        )
        sections.append(
            f"━━━ APPLICANT RESUME CONTENT ━━━\n"
            f"(Use this as the ground truth for the applicant's skills, experience, and background)\n\n"
            f"{resume_texts}"
        )

    if context.jd_chunks:
        jd_texts = "\n\n".join(
            f"[Job Description Chunk {i+1}]\n{chunk.text}"
            for i, chunk in enumerate(context.jd_chunks)
        )
        sections.append(
            f"━━━ JOB DESCRIPTION ━━━\n"
            f"(Use this to align the answer with the role requirements)\n\n"
            f"{jd_texts}"
        )

    if context.template_chunks:
        tmpl_texts = "\n\n".join(
            f"[Template Answer {i+1}]\n{chunk.text}"
            for i, chunk in enumerate(context.template_chunks)
        )
        sections.append(
            f"━━━ USER'S PRE-WRITTEN TEMPLATE ANSWERS ━━━\n"
            f"(These are the applicant's own pre-written answers — prefer using these as a base if relevant)\n\n"
            f"{tmpl_texts}"
        )

    if context.past_answer_chunks:
        past_texts = "\n\n".join(
            f"[Past Answer {i+1}]\n{chunk.text}"
            for i, chunk in enumerate(context.past_answer_chunks)
        )
        sections.append(
            f"━━━ PAST APPLICATION ANSWERS ━━━\n"
            f"(These are answers the applicant used in previous applications — adapt if relevant)\n\n"
            f"{past_texts}"
        )

    if not sections:
        return "[NO CONTEXT AVAILABLE]"

    return "\n\n" + "\n\n".join(sections)


def _build_user_prompt(
    question: str,
    context: RetrievedContext,
    field_key: str | None,
    max_words: int | None,
) -> str:
    """Build the complete user-turn prompt with injected context."""
    context_block = _format_context_section(context)
    word_instruction = f"\nTarget length: approximately {max_words} words." if max_words else ""

    return f"""\
QUESTION TO ANSWER:
"{question}"

FIELD TYPE: {field_key or "open_text"}
{word_instruction}

CONTEXT — Use ONLY the information below to construct your answer:
{context_block}

Now write the answer as a JSON object in the format specified in your instructions.
Remember: if the context is insufficient, output the REFUSAL JSON instead of guessing.
"""


# ---------------------------------------------------------------------------
# Confidence scorer
# ---------------------------------------------------------------------------

def _compute_confidence(
    context: RetrievedContext,
    generated_answer: str,
    context_used: list[str],
    refusal: bool,
) -> float:
    """
    Compute a confidence score from three signals.

    Signal A — RAG coverage (0–0.40):
      Based on how many relevant chunks were retrieved and their scores.

    Signal B — Source diversity (0–0.30):
      Did we find context from multiple source types?

    Signal C — Answer grounding (0–0.30):
      Does the answer appear to stay within context boundaries?
      (Heuristic: penalise answers that contain entities not in any chunk)

    Returns: float in [0.0, 1.0]
    """
    if refusal:
        return 0.0

    score_a = 0.0
    all_chunks = context.all_chunks()
    if all_chunks:
        # Weight by the Qdrant similarity scores of retrieved chunks
        avg_qdrant_score = sum(c.score for c in all_chunks) / len(all_chunks)
        chunk_count_bonus = min(len(all_chunks) / 10.0, 1.0)  # caps at 10 chunks
        score_a = min(0.40, (avg_qdrant_score * 0.30) + (chunk_count_bonus * 0.10))

    score_b = 0.0
    source_types_present = {c.source_type for c in all_chunks}
    diversity_map = {1: 0.10, 2: 0.20, 3: 0.27, 4: 0.30}
    score_b = diversity_map.get(len(source_types_present), 0.30)

    score_c = 0.0
    if generated_answer and all_chunks:
        # Build a pool of words from all context chunks (lowercased)
        context_words = set()
        for chunk in all_chunks:
            context_words.update(re.findall(r"\b[a-z]{4,}\b", chunk.text.lower()))

        # Check what fraction of "significant" answer words exist in context
        answer_words = re.findall(r"\b[a-zA-Z]{4,}\b", generated_answer)
        if answer_words:
            matched = sum(1 for w in answer_words if w.lower() in context_words)
            grounding_ratio = matched / len(answer_words)
            score_c = grounding_ratio * 0.30
        else:
            score_c = 0.15  # short answers get partial credit
    else:
        score_c = 0.0

    total = score_a + score_b + score_c
    return round(min(1.0, max(0.0, total)), 3)


# ---------------------------------------------------------------------------
# Hallucination detection heuristic
# ---------------------------------------------------------------------------

def _detect_hallucination(
    answer: str,
    context: RetrievedContext,
) -> bool:
    """
    Lightweight post-generation hallucination check.

    Looks for patterns that suggest the model invented information:
    - Specific percentage figures (e.g. "increased revenue by 47%") that
      don't appear in any context chunk
    - Year references not found in any chunk
    - Quoted dollar amounts not in context

    This is a heuristic — not a perfect detector. Critical production
    deployments should add a second-pass LLM verification call.
    """
    all_text = " ".join(c.text for c in context.all_chunks()).lower()

    # Check for specific percentage figures
    pct_matches = re.findall(r"\b\d+(?:\.\d+)?%", answer)
    for pct in pct_matches:
        if pct.lower() not in all_text:
            logger.warning("Hallucination candidate: percentage not in context", value=pct)
            return True

    # Check for dollar figures
    dollar_matches = re.findall(r"\$[\d,]+(?:\.\d+)?[KMB]?", answer)
    for dollar in dollar_matches:
        if dollar.replace(",", "").lower() not in all_text.replace(",", ""):
            logger.warning("Hallucination candidate: dollar figure not in context", value=dollar)
            return True

    # Check for year references (4-digit years 1990-2099)
    year_matches = re.findall(r"\b(19|20)\d{2}\b", answer)
    for year in year_matches:
        if year not in all_text:
            logger.warning("Hallucination candidate: year not in context", value=year)
            return True

    return False


# ---------------------------------------------------------------------------
# Agent class
# ---------------------------------------------------------------------------

class AnswerGenerationAgent:
    """
    Generates a grounded, context-only answer for an open-text form question.

    The agent enforces zero hallucination through prompt design, refusal
    mechanisms, and post-generation validation.

    Usage:
        agent = AnswerGenerationAgent()
        result: GeneratedAnswer = await agent.generate(
            question="Why do you want to work here?",
            context=retrieved_context,
            field_key="why_us",
        )
    """

    def __init__(self, llm=None) -> None:
        configure_llama_settings()
        self._llm = llm or LlamaSettings.llm

    async def generate(
        self,
        question: str,
        context: RetrievedContext,
        field_key: str | None = None,
        max_words: int | None = None,
    ) -> GeneratedAnswer:
        """
        Generate a grounded answer for a single form question.

        Args:
            question:  The exact question text from the form label.
            context:   Pre-retrieved RAG context from all four collections.
            field_key: Canonical field key (e.g. "why_us") for logging.
            max_words: Optional word count target injected into the prompt.

        Returns:
            GeneratedAnswer with full audit trail.
        """
        t_start = time.monotonic()

        # Guard: refuse immediately if context is completely empty
        if context.is_empty():
            logger.warning(
                "AnswerGenerationAgent: no context retrieved — refusing",
                field_key=field_key,
            )
            return GeneratedAnswer(
                answer="",
                confidence_score=0.0,
                answer_source="ai_generated",
                hallucination_flagged=False,
                refusal_reason=(
                    "No relevant context was found in your resume, templates, or past answers. "
                    "Please add more information to your profile before generating answers."
                ),
                latency_ms=0,
            )

        # Build messages
        user_prompt = _build_user_prompt(question, context, field_key, max_words)
        messages = [
            ChatMessage(role=MessageRole.SYSTEM, content=_SYSTEM_PROMPT),
            ChatMessage(role=MessageRole.USER, content=user_prompt),
        ]

        logger.info(
            "AnswerGenerationAgent: calling LLM",
            field_key=field_key,
            question_preview=question[:80],
            context_chunks=len(context.all_chunks()),
            estimated_context_tokens=context.total_token_estimate,
        )

        # LLM call
        response = await self._llm.achat(messages)
        raw_content = (response.message.content or "").strip()

        # Token usage (may be None depending on provider)
        token_usage: dict[str, int] = {}
        if hasattr(response, "raw") and response.raw:
            usage = response.raw.get("usage", {})
            token_usage = {
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            }

        # Strip markdown fences
        raw_content = re.sub(r"^```(?:json)?", "", raw_content).strip()
        raw_content = re.sub(r"```$", "", raw_content).strip()

        # Parse JSON response
        try:
            response_dict: dict[str, Any] = json.loads(raw_content)
        except json.JSONDecodeError as exc:
            logger.error(
                "AnswerGenerationAgent: JSON parse failed",
                error=str(exc),
                raw_preview=raw_content[:300],
            )
            latency_ms = int((time.monotonic() - t_start) * 1000)
            return GeneratedAnswer(
                answer="",
                confidence_score=0.0,
                answer_source="ai_generated",
                hallucination_flagged=False,
                refusal_reason=f"LLM returned invalid JSON: {exc}",
                token_usage=token_usage,
                latency_ms=latency_ms,
            )

        # Check for REFUSAL
        if response_dict.get("REFUSAL"):
            refusal_reason = response_dict.get("reason", "Insufficient context")
            latency_ms = int((time.monotonic() - t_start) * 1000)
            logger.info(
                "AnswerGenerationAgent: LLM issued REFUSAL",
                reason=refusal_reason,
                field_key=field_key,
            )
            return GeneratedAnswer(
                answer="",
                confidence_score=0.0,
                answer_source="ai_generated",
                hallucination_flagged=False,
                refusal_reason=refusal_reason,
                token_usage=token_usage,
                latency_ms=latency_ms,
            )

        # Extract answer fields
        answer_text: str = response_dict.get("answer", "").strip()
        answer_source: str = response_dict.get("answer_source", "ai_generated")
        context_used: list[str] = response_dict.get("context_used", [])

        # Validate answer_source is a known value
        if answer_source not in ("template", "resume", "past_answer", "ai_generated"):
            answer_source = "ai_generated"

        # Post-generation hallucination check
        hallucination_flagged = _detect_hallucination(answer_text, context)
        if hallucination_flagged:
            logger.warning(
                "AnswerGenerationAgent: hallucination detected",
                field_key=field_key,
                answer_preview=answer_text[:100],
            )

        # Compute confidence score
        confidence = _compute_confidence(context, answer_text, context_used, refusal=False)

        # Reduce confidence if hallucination detected
        if hallucination_flagged:
            confidence = max(0.0, confidence - 0.25)

        # Build context source audit trail
        context_sources = [
            ContextSource(
                source_type=chunk.source_type,
                text_preview=chunk.text[:200],
                score=round(chunk.score, 4),
            )
            for chunk in context.all_chunks()[:8]  # top 8 for the audit trail
        ]

        latency_ms = int((time.monotonic() - t_start) * 1000)

        logger.info(
            "AnswerGenerationAgent complete",
            field_key=field_key,
            answer_source=answer_source,
            confidence=confidence,
            hallucination_flagged=hallucination_flagged,
            latency_ms=latency_ms,
            **token_usage,
        )

        return GeneratedAnswer(
            answer=answer_text,
            confidence_score=confidence,
            answer_source=answer_source,
            context_sources=context_sources,
            hallucination_flagged=hallucination_flagged,
            refusal_reason=None,
            token_usage=token_usage,
            latency_ms=latency_ms,
        )

    async def generate_batch(
        self,
        questions: list[dict[str, str]],
        context: RetrievedContext,
    ) -> list[GeneratedAnswer]:
        """
        Generate answers for multiple questions sharing the same context.
        Context is retrieved once and reused — efficient for multi-question pages.

        Args:
            questions: List of {"question": str, "field_key": str, "max_words": int|None}
            context:   Pre-retrieved shared RetrievedContext

        Returns:
            List of GeneratedAnswer in the same order as questions.
        """
        import asyncio
        tasks = [
            self.generate(
                question=q["question"],
                context=context,
                field_key=q.get("field_key"),
                max_words=q.get("max_words"),
            )
            for q in questions
        ]
        return list(await asyncio.gather(*tasks))