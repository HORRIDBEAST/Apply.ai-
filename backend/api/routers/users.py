"""
backend/api/routers/users.py
==============================
User account management endpoints.

Routes:
  POST   /users/sync      — Clerk webhook: create or update user on sign-up
  GET    /users/me        — Return the current authenticated user's profile
  PATCH  /users/me        — Update display name / preferences
  DELETE /users/me        — Soft-delete account + purge all Qdrant vectors
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.core.auth import AuthenticatedUser
from backend.api.core.config import settings
from backend.api.core.logging import get_logger
from backend.api.db.qdrant_client import get_qdrant
from backend.api.db.session import get_db
from backend.api.models.models import User
from backend.api.rag.ingestion import purge_user_vectors

logger = get_logger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic schemas (request / response)
# ---------------------------------------------------------------------------

class ClerkWebhookUserData(BaseModel):
    id: str
    email_addresses: list[dict]
    first_name: str | None = None
    last_name: str | None = None
    image_url: str | None = None


class ClerkWebhookPayload(BaseModel):
    type: str
    data: ClerkWebhookUserData


class UserProfileResponse(BaseModel):
    id: uuid.UUID
    clerk_user_id: str
    email: str
    display_name: str | None
    avatar_url: str | None
    plan_tier: str
    created_at: datetime

    class Config:
        from_attributes = True


class UpdateProfileRequest(BaseModel):
    display_name: str | None = None
    preferences: dict | None = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/sync", status_code=status.HTTP_200_OK, include_in_schema=False)
async def clerk_webhook_sync(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Clerk webhook handler — called by Clerk on user.created / user.updated.
    Creates or updates the internal user record.

    In production, validate the Svix webhook signature using
    settings.CLERK_WEBHOOK_SECRET before processing.
    """
    payload = ClerkWebhookPayload.model_validate(await request.json())
    data = payload.data

    primary_email = next(
        (e["email_address"] for e in data.email_addresses if e.get("id")),
        None,
    )
    if not primary_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No email address in Clerk payload",
        )

    display_name = " ".join(filter(None, [data.first_name, data.last_name])) or None

    # Upsert: update if exists, create if not
    result = await db.execute(select(User).where(User.clerk_user_id == data.id))
    user = result.scalar_one_or_none()

    if user:
        user.email = primary_email
        user.display_name = display_name
        user.avatar_url = data.image_url
    else:
        user = User(
            clerk_user_id=data.id,
            email=primary_email,
            display_name=display_name,
            avatar_url=data.image_url,
        )
        db.add(user)

    await db.commit()
    logger.info("User synced from Clerk", clerk_user_id=data.id, event=payload.type)
    return {"status": "synced"}


@router.get("/me", response_model=UserProfileResponse)
async def get_current_user_profile(
    current_user: AuthenticatedUser,
    db: AsyncSession = Depends(get_db),
) -> User:
    """Return the authenticated user's profile."""
    result = await db.execute(
        select(User).where(User.id == uuid.UUID(current_user.user_id))
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


@router.patch("/me", response_model=UserProfileResponse)
async def update_user_profile(
    body: UpdateProfileRequest,
    current_user: AuthenticatedUser,
    db: AsyncSession = Depends(get_db),
) -> User:
    """Update the current user's display name or preferences."""
    result = await db.execute(
        select(User).where(User.id == uuid.UUID(current_user.user_id))
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if body.display_name is not None:
        user.display_name = body.display_name
    if body.preferences is not None:
        user.preferences = body.preferences

    await db.commit()
    await db.refresh(user)
    return user


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    current_user: AuthenticatedUser,
    db: AsyncSession = Depends(get_db),
    qdrant=Depends(get_qdrant),
) -> None:
    """
    Soft-delete the user account and hard-delete all Qdrant vectors.
    PostgreSQL CASCADE will clean up child rows when the user is eventually
    hard-deleted by a scheduled job.
    """
    result = await db.execute(
        select(User).where(User.id == uuid.UUID(current_user.user_id))
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    user.is_deleted = True
    user.is_active = False
    await db.commit()

    # GDPR: purge all vector data immediately
    await purge_user_vectors(current_user.user_id, qdrant)
    logger.info("User account deleted", user_id=current_user.user_id)