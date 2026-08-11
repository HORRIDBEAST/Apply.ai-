"""
backend/api/agents/__init__.py
================================
Agent package — exports the four core agents and the LlamaIndex bootstrap.
Import configure_llama_settings and call it in the FastAPI lifespan.
"""

from .answer_generation import AnswerGenerationAgent
from .application_memory import ApplicationMemoryAgent
from .base import configure_llama_settings
from .form_understanding import FormUnderstandingAgent
from .resume_extractor import ResumeExtractorAgent

__all__ = [
    "ResumeExtractorAgent",
    "FormUnderstandingAgent",
    "AnswerGenerationAgent",
    "ApplicationMemoryAgent",
    "configure_llama_settings",
]