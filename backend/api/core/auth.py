"""
backend/api/core/auth.py
========================
FastAPI dependency for verifying Clerk JWTs on every protected route.

Flow:
  1. Client sends Bearer token (Clerk session JWT) in Authorization header.
  2. `verify_clerk_token()` fetches Clerk's JWKS and validates the JWT.
  3. On success it returns a `CurrentUser` dataclass with the resolved
     internal user UUID and Clerk subject ID.
  4. The resolved user_id is pushed into the structlog context so every
     subsequent log line in that request automatically carries it.

JWKS caching: Clerk's public keys rarely rotate, so we cache the JWKS
response in Redis (TTL = 1 hour) to avoid a round-trip on every request.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Annotated

import httpx
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import ExpiredSignatureError, JWTError, jwk, jwt

from backend.api.core.config import settings
from backend.api.core.logging import get_logger, user_id_var
from backend.api.db.redis_client import get_redis

logger = get_logger(__name__)

# Clerk JWKS endpoint (contains RSA public keys for JWT verification)
_CLERK_JWKS_URL = "https://api.clerk.dev/v1/jwks"
_JWKS_CACHE_KEY = "clerk:jwks"
_JWKS_CACHE_TTL = 3600  # 1 hour

bearer_scheme = HTTPBearer(auto_error=True)


@dataclass(frozen=True, slots=True)
class CurrentUser:
    """Resolved identity after successful JWT verification."""
    user_id: str           # Internal PostgreSQL UUID (from users table)
    clerk_user_id: str     # Clerk subject (sub claim)
    email: str
    plan_tier: str


async def _fetch_jwks(redis) -> dict:
    """
    Fetch Clerk's JWKS, with Redis caching so we don't hit Clerk's API
    on every single request.
    """
    cached = await redis.get(_JWKS_CACHE_KEY)
    if cached:
        return json.loads(cached)

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(_CLERK_JWKS_URL)
        resp.raise_for_status()
        jwks_data = resp.json()

    await redis.setex(_JWKS_CACHE_KEY, _JWKS_CACHE_TTL, json.dumps(jwks_data))
    return jwks_data


def _decode_jwt(token: str, jwks_data: dict) -> dict:
    """
    Validate the JWT signature against Clerk's JWKS and return the claims.
    Raises HTTPException on any validation failure.
    """
    try:
        # Extract the key ID from the unverified header
        unverified_header = jwt.get_unverified_header(token)
        kid = unverified_header.get("kid")

        # Find the matching public key in JWKS
        matching_key = None
        for key_data in jwks_data.get("keys", []):
            if key_data.get("kid") == kid:
                matching_key = key_data
                break

        if matching_key is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="JWT signing key not found in JWKS",
            )

        public_key = jwk.construct(matching_key)

        claims = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            options={
                "verify_exp": True,
                "verify_nbf": True,
                "verify_iat": True,
                "verify_aud": False,  # Clerk tokens may not have aud
            },
        )
        return claims

    except ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
        )
    except JWTError as exc:
        logger.warning("JWT validation failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )


async def verify_clerk_token(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer_scheme)],
    redis=Depends(get_redis),
) -> CurrentUser:
    """
    FastAPI dependency that:
    1. Extracts Bearer token from Authorization header
    2. Fetches/caches Clerk JWKS
    3. Validates JWT and returns CurrentUser

    Raises HTTP 401 on any auth failure.
    """
    from backend.api.db.session import AsyncSessionLocal
    from sqlalchemy import select
    from backend.api.models.models import User

    token = credentials.credentials
    jwks_data = await _fetch_jwks(redis)
    claims = _decode_jwt(token, jwks_data)

    clerk_user_id: str = claims.get("sub", "")
    email: str = claims.get("email", "")

    if not clerk_user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing subject claim",
        )

    # Resolve the internal user record from the Clerk subject ID
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(User).where(
                User.clerk_user_id == clerk_user_id,
                User.is_deleted.is_(False),
                User.is_active.is_(True),
            )
        )
        user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account not found or inactive",
        )

    current_user = CurrentUser(
        user_id=str(user.id),
        clerk_user_id=clerk_user_id,
        email=email or user.email,
        plan_tier=user.plan_tier,
    )

    # Push user_id into structlog context for automatic log enrichment
    user_id_var.set(current_user.user_id)

    return current_user


# ---------------------------------------------------------------------------
# Type alias — use in route signatures for cleaner code
# ---------------------------------------------------------------------------
AuthenticatedUser = Annotated[CurrentUser, Depends(verify_clerk_token)]