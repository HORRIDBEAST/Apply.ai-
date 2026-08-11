"""
backend/api/routers/__init__.py
================================
Router registry — collects all sub-routers into a single list
imported by main.py.  Adding a new router here is all it takes to
mount it at the correct path prefix.
"""

from fastapi import APIRouter

from .agents import router as agents_router
from .applications import router as applications_router
from .health import router as health_router
from .resumes import router as resumes_router
from .templates import router as templates_router
from .users import router as users_router

# All routers in mount order
ALL_ROUTERS: list[tuple[APIRouter, str, list[str]]] = [
    # (router, prefix, tags)
    (health_router,       "/health",       ["Health"]),
    (users_router,        "/users",        ["Users"]),
    (resumes_router,      "/resumes",      ["Resumes"]),
    (templates_router,    "/templates",    ["Templates"]),
    (applications_router, "/applications", ["Applications"]),
    (agents_router,       "/agents",       ["AI Agents"]),
]