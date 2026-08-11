"""
backend/api/routers/resumes.py
================================
Resume management endpoints.

Routes:
  POST   /resumes/upload      — Upload + encrypt resume file → S3, trigger parse
  GET    /resumes/            — List user's resumes
  GET    /resumes/{resume_id} — Get a single resume's metadata + parsed data
  DELETE /resumes/{resume_id} — Delete resume file + Qdrant vectors
  POST   /resumes/{resume_id}/set-primary — Mark a resume as the active one
"""

from __future__ import annotations

import hashlib
import io
import uuid
from datetime import datetime

import boto3
from botocore.config import Config as BotoConfig
from celery import Celery
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.core.auth import AuthenticatedUser
from backend.api.core.config import settings
from backend.api.core.logging import get_logger
from backend.api.db.qdrant_client import QdrantHelper, get_qdrant
from backend.api.db.session import get_db
from backend.api.models.models import ParsedResumeData, Resume

logger = get_logger(__name__)
router = APIRouter()

# Allowed MIME types for resume uploads
_ALLOWED_MIME_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",
}
_MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB


# ---------------------------------------------------------------------------
# S3 client (lazy singleton)
# ---------------------------------------------------------------------------

def _get_s3_client():
    return boto3.client(
        "s3",
        endpoint_url=settings.S3_ENDPOINT_URL,
        aws_access_key_id=settings.S3_ACCESS_KEY_ID,
        aws_secret_access_key=settings.S3_SECRET_ACCESS_KEY,
        region_name=settings.S3_REGION,
        config=BotoConfig(signature_version="s3v4"),
    )


def _build_s3_key(user_id: str, resume_id: str) -> str:
    """Construct the S3 object key for an encrypted resume file."""
    return f"resumes/{user_id}/{resume_id}.enc"


def _encrypt_and_upload(
    file_bytes: bytes,
    s3_key: str,
    content_hash: str,
) -> None:
    """
    Encrypt the file with AES-256-GCM and upload to S3.

    In production this would use AWS KMS-managed keys with server-side
    encryption (SSE-KMS). For now we use app-layer AES via cryptography lib.
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    import base64
    import os

    key_bytes = base64.urlsafe_b64decode(settings.RESUME_ENCRYPTION_KEY + "==")
    nonce = os.urandom(12)  # 96-bit nonce for GCM
    aesgcm = AESGCM(key_bytes)
    ciphertext = aesgcm.encrypt(nonce, file_bytes, associated_data=content_hash.encode())

    # Prepend nonce so decryption can recover it: [12 bytes nonce | ciphertext + 16 byte tag]
    payload = nonce + ciphertext

    s3 = _get_s3_client()
    s3.put_object(
        Bucket=settings.S3_BUCKET_RESUMES,
        Key=s3_key,
        Body=payload,
        ContentType="application/octet-stream",
        ServerSideEncryption="AES256",  # additional S3-layer encryption
        Metadata={"content_hash": content_hash},
    )


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class ResumeMetaResponse(BaseModel):
    id: uuid.UUID
    file_name: str
    mime_type: str
    file_size_bytes: int | None
    is_primary: bool
    parse_status: str
    created_at: datetime

    class Config:
        from_attributes = True


class ResumeDetailResponse(ResumeMetaResponse):
    parsed_data: dict | None = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/upload", response_model=ResumeMetaResponse, status_code=status.HTTP_201_CREATED)
async def upload_resume(
    file: UploadFile = File(...),
    current_user: AuthenticatedUser = Depends(),
    db: AsyncSession = Depends(get_db),
) -> Resume:
    """
    Upload a resume file.

    Steps:
      1. Validate file type and size
      2. Compute SHA-256 content hash (deduplication)
      3. Encrypt with AES-256-GCM and upload to S3
      4. Create Resume row in PostgreSQL (parse_status = "pending")
      5. Dispatch Celery task to parse the resume asynchronously
    """
    # 1. Validate MIME type
    if file.content_type not in _ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported file type: {file.content_type}. "
                   f"Allowed: PDF, DOCX",
        )

    # 2. Read and validate file size
    file_bytes = await file.read()
    if len(file_bytes) > _MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds maximum size of {_MAX_FILE_SIZE_BYTES // 1024 // 1024} MB",
        )

    # 3. Compute SHA-256 for deduplication
    content_hash = hashlib.sha256(file_bytes).hexdigest()

    # Check for exact duplicate (same file content already uploaded by this user)
    dup_result = await db.execute(
        select(Resume).where(
            Resume.user_id == uuid.UUID(current_user.user_id),
            Resume.content_hash == content_hash,
        )
    )
    existing = dup_result.scalar_one_or_none()
    if existing:
        logger.info("Duplicate resume upload detected", resume_id=str(existing.id))
        return existing

    # 4. Generate resume ID and S3 key
    resume_id = uuid.uuid4()
    s3_key = _build_s3_key(current_user.user_id, str(resume_id))

    # 5. Encrypt and upload (runs synchronously here; for large files use async S3)
    try:
        _encrypt_and_upload(file_bytes, s3_key, content_hash)
    except Exception as exc:
        logger.error("S3 upload failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="File storage failed. Please try again.",
        )

    # 6. Persist resume metadata in PostgreSQL
    resume = Resume(
        id=resume_id,
        user_id=uuid.UUID(current_user.user_id),
        file_name=file.filename or "resume",
        storage_key=s3_key,
        mime_type=file.content_type,
        file_size_bytes=len(file_bytes),
        content_hash=content_hash,
        parse_status="pending",
    )
    db.add(resume)
    await db.commit()
    await db.refresh(resume)

    # 7. Dispatch async parse task (Celery)
    # Imported here to avoid circular imports at module level
    from backend.api.workers.tasks import parse_resume_task  # noqa: PLC0415
    parse_resume_task.delay(str(resume_id), current_user.user_id)

    logger.info(
        "Resume uploaded and parse task dispatched",
        resume_id=str(resume_id),
        user_id=current_user.user_id,
    )
    return resume


@router.get("/", response_model=list[ResumeMetaResponse])
async def list_resumes(
    current_user: AuthenticatedUser,
    db: AsyncSession = Depends(get_db),
) -> list[Resume]:
    """Return all resumes for the authenticated user."""
    result = await db.execute(
        select(Resume)
        .where(Resume.user_id == uuid.UUID(current_user.user_id))
        .order_by(Resume.created_at.desc())
    )
    return list(result.scalars().all())


@router.get("/{resume_id}", response_model=ResumeDetailResponse)
async def get_resume(
    resume_id: uuid.UUID,
    current_user: AuthenticatedUser,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Return a resume's metadata and its parsed structured data."""
    result = await db.execute(
        select(Resume).where(
            Resume.id == resume_id,
            Resume.user_id == uuid.UUID(current_user.user_id),
        )
    )
    resume = result.scalar_one_or_none()
    if not resume:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resume not found")

    # Fetch parsed data separately (1:1 relationship)
    parsed_result = await db.execute(
        select(ParsedResumeData).where(ParsedResumeData.resume_id == resume_id)
    )
    parsed = parsed_result.scalar_one_or_none()

    return {
        **ResumeMetaResponse.model_validate(resume).model_dump(),
        "parsed_data": parsed.parsed_json if parsed else None,
    }


@router.post("/{resume_id}/set-primary", response_model=ResumeMetaResponse)
async def set_primary_resume(
    resume_id: uuid.UUID,
    current_user: AuthenticatedUser,
    db: AsyncSession = Depends(get_db),
) -> Resume:
    """Mark a resume as the user's active (primary) resume."""
    user_uuid = uuid.UUID(current_user.user_id)

    # Verify ownership
    result = await db.execute(
        select(Resume).where(Resume.id == resume_id, Resume.user_id == user_uuid)
    )
    resume = result.scalar_one_or_none()
    if not resume:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resume not found")

    # Clear all existing primary flags for this user
    await db.execute(
        update(Resume)
        .where(Resume.user_id == user_uuid)
        .values(is_primary=False)
    )
    resume.is_primary = True
    await db.commit()
    await db.refresh(resume)
    return resume


@router.delete("/{resume_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_resume(
    resume_id: uuid.UUID,
    current_user: AuthenticatedUser,
    db: AsyncSession = Depends(get_db),
    qdrant=Depends(get_qdrant),
) -> None:
    """Delete a resume's S3 file, Qdrant vectors, and PostgreSQL rows."""
    user_uuid = uuid.UUID(current_user.user_id)
    result = await db.execute(
        select(Resume).where(Resume.id == resume_id, Resume.user_id == user_uuid)
    )
    resume = result.scalar_one_or_none()
    if not resume:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resume not found")

    # Delete S3 object
    try:
        s3 = _get_s3_client()
        s3.delete_object(Bucket=settings.S3_BUCKET_RESUMES, Key=resume.storage_key)
    except Exception as exc:
        logger.warning("S3 delete failed", error=str(exc), key=resume.storage_key)

    # Delete Qdrant vectors for this resume
    await QdrantHelper.delete_by_source_id(
        qdrant,
        settings.QDRANT_COLLECTION_RESUME,
        source_field="resume_id",
        source_id=str(resume_id),
    )

    # PostgreSQL CASCADE will delete ParsedResumeData row automatically
    await db.delete(resume)
    await db.commit()
    logger.info("Resume deleted", resume_id=str(resume_id), user_id=current_user.user_id)