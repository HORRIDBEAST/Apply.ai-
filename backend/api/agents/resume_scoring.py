"""
backend/api/agents/resume_scoring.py
======================================
ResumeScoringAgent — scores how well a parsed resume matches a specific
job description, and surfaces matched/missing skills.

STATUS: implemented ahead of its frontend — not yet called by any UI.
This is "option B" groundwork from frontend_context.md §5: the extension's
"Resume Score" tab and the website will eventually call
POST /api/v1/agents/resume/score, which wraps this agent.

Grounding policy
-----------------
This is an evaluative task, not open-text generation, so the zero-
hallucination "refusal protocol" used by AnswerGenerationAgent doesn't
directly apply. The agent is still constrained to only ever cite skills
and resume content that are literally present in the structured resume
JSON it's given — it must not invent resume content to justify a score.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from llama_index.core.llms import ChatMessage, MessageRole
from llama_index.core import Settings as LlamaSettings

from backend.api.agents.base import ResumeScoreResult, configure_llama_settings
from backend.api.core.logging import get_logger

logger = get_logger(__name__)


_SYSTEM_PROMPT = """\
You are a precise resume-to-job-description match scorer.

YOUR ROLE:
Given a candidate's structured resume data and a job description, you assess
how well the resume matches the role and return a structured score.

GROUND RULES:
1. Base matched_skills, strengths, and your reasoning ONLY on content that is
   literally present in the RESUME DATA section below. Do not credit the
   candidate with skills or experience that aren't there.
2. Base missing_skills and gaps ONLY on requirements literally present in the
   JOB DESCRIPTION section below. Do not invent requirements the JD doesn't
   state.
3. overall_score is 0-100, reflecting realistic match quality — do not
   default to a flattering score. A resume with little relevant overlap
   should score low.
4. Output ONLY a JSON object in this exact structure:
   {
     "overall_score": <float 0-100>,
     "matched_skills": ["<skill present in both resume and JD>", ...],
     "missing_skills": ["<JD requirement not evidenced in resume>", ...],
     "strengths": ["<concrete resume point that aligns with the JD>", ...],
     "gaps": ["<concrete JD requirement the resume doesn't address>", ...],
     "summary": "<2-4 sentence plain-language summary of the match>"
   }
"""


def _build_user_prompt(resume_json: dict, jd_text: str) -> str:
    return f"""\
RESUME DATA (structured JSON — the only source of truth for the candidate):
{json.dumps(resume_json, indent=2)}

JOB DESCRIPTION (the only source of truth for role requirements):
{jd_text}

Now score the match as the JSON object specified in your instructions.
"""


class ResumeScoringAgent:
    """
    Scores a parsed resume against a job description.

    Usage:
        agent = ResumeScoringAgent()
        result: ResumeScoreResult = await agent.score(
            resume_json=parsed_resume_dict,
            jd_text=job_description_raw_text,
        )
    """

    def __init__(self, llm=None) -> None:
        configure_llama_settings()
        self._llm = llm or LlamaSettings.llm

    async def score(self, resume_json: dict, jd_text: str) -> ResumeScoreResult:
        t_start = time.monotonic()

        if not jd_text.strip() or not resume_json:
            return ResumeScoreResult(
                overall_score=0.0,
                summary="Cannot score: resume data or job description text is missing.",
                latency_ms=0,
            )

        messages = [
            ChatMessage(role=MessageRole.SYSTEM, content=_SYSTEM_PROMPT),
            ChatMessage(role=MessageRole.USER, content=_build_user_prompt(resume_json, jd_text)),
        ]

        logger.info("ResumeScoringAgent: calling LLM", jd_length=len(jd_text))

        response = await self._llm.achat(messages)
        raw_content = (response.message.content or "").strip()

        token_usage: dict[str, int] = {}
        if hasattr(response, "raw") and response.raw:
            usage = getattr(response.raw, "usage", None)
            if usage is not None:
                token_usage = {
                    "prompt_tokens": getattr(usage, "prompt_tokens", 0),
                    "completion_tokens": getattr(usage, "completion_tokens", 0),
                    "total_tokens": getattr(usage, "total_tokens", 0),
                }

        raw_content = re.sub(r"^```(?:json)?", "", raw_content).strip()
        raw_content = re.sub(r"```$", "", raw_content).strip()

        latency_ms = int((time.monotonic() - t_start) * 1000)

        try:
            parsed: dict[str, Any] = json.loads(raw_content)
        except json.JSONDecodeError as exc:
            logger.error("ResumeScoringAgent: JSON parse failed", error=str(exc), raw_preview=raw_content[:300])
            return ResumeScoreResult(
                overall_score=0.0,
                summary=f"Scoring failed: LLM returned invalid JSON ({exc}).",
                token_usage=token_usage,
                latency_ms=latency_ms,
            )

        result = ResumeScoreResult(
            overall_score=max(0.0, min(100.0, float(parsed.get("overall_score", 0.0)))),
            matched_skills=parsed.get("matched_skills", []),
            missing_skills=parsed.get("missing_skills", []),
            strengths=parsed.get("strengths", []),
            gaps=parsed.get("gaps", []),
            summary=parsed.get("summary", ""),
            token_usage=token_usage,
            latency_ms=latency_ms,
        )

        logger.info(
            "ResumeScoringAgent complete",
            overall_score=result.overall_score,
            matched_count=len(result.matched_skills),
            missing_count=len(result.missing_skills),
            latency_ms=latency_ms,
            **token_usage,
        )
        return result
