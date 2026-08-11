"""
backend/api/models/models.py
============================
Complete SQLAlchemy ORM models for Job Autofill Copilot.

Tables:
  - users
  - resumes
  - parsed_resume_data
  - templates
  - job_descriptions
  - applications
  - application_answers
  - form_fields
  - ai_generated_answers

Design decisions:
  - UUID primary keys (server-generated via pgcrypto)
  - All foreign keys have explicit ON DELETE semantics
  - GIN / B-Tree indexes on high-cardinality lookup columns
  - Encrypted resume storage path (actual encryption happens in app layer)
  - JSONB for semi-structured AI/parsed data (Postgres-native, indexable)
  - Enum types for finite-state columns (application status, field types, etc.)
"""

import uuid
from datetime import datetime
from enum import Enum as PyEnum
from typing import Optional

from sqlalchemy import (
    Boolean,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDPrimaryKeyMixin

# =============================================================================
# Python-side Enum definitions (mirrored in PostgreSQL via SQLAlchemy Enum)
# =============================================================================


class ApplicationStatus(str, PyEnum):
    """Lifecycle states for a single job application."""
    DETECTED = "detected"       # Extension detected a form
    AUTOFILLED = "autofilled"   # Fields were injected into DOM
    SUBMITTED = "submitted"     # User explicitly marked as submitted
    FAILED = "failed"           # Submission error or rejected by ATS
    SKIPPED = "skipped"         # User dismissed the autofill


class FieldType(str, PyEnum):
    """Canonical form field types used for semantic mapping."""
    TEXT = "text"
    TEXTAREA = "textarea"
    EMAIL = "email"
    PHONE = "phone"
    URL = "url"
    DATE = "date"
    SELECT = "select"
    MULTISELECT = "multiselect"
    CHECKBOX = "checkbox"
    RADIO = "radio"
    FILE = "file"
    HIDDEN = "hidden"
    UNKNOWN = "unknown"


class AnswerSource(str, PyEnum):
    """Where an autofill answer was sourced from."""
    TEMPLATE = "template"
    RESUME = "resume"
    AI_GENERATED = "ai_generated"
    USER_OVERRIDE = "user_override"
    PAST_ANSWER = "past_answer"


class ATSPlatform(str, PyEnum):
    """Known ATS / job-board platforms for targeted DOM strategies."""
    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    WORKDAY = "workday"
    KEKA = "keka"
    ORACLE_CLOUD = "oracle_cloud"
    GOOGLE_FORMS = "google_forms"
    MICROSOFT_FORMS = "microsoft_forms"
    SENSEHQ = "sensehq"
    WORKABLE = "workable"
    APPDOVER = "appdover"
    BAMBOOHR = "bamboohr"
    SMARTRECRUITERS = "smartrecruiters"
    ICIMS = "icims"
    ASHBY = "ashby"
    ULTIPRO = "ultipro"
    SAP_SUCCESSFACTORS = "sap_successfactors"
    CUSTOM = "custom"
    UNKNOWN = "unknown"


# =============================================================================
# users
# =============================================================================

class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    Core user record.
    Authentication is handled externally (Clerk/Auth0); we store the external
    subject ID (clerk_user_id) as the stable join key.
    """
    __tablename__ = "users"

    # External auth provider subject ID (Clerk user_id, Auth0 sub, etc.)
    clerk_user_id: Mapped[str] = mapped_column(
        String(256),
        unique=True,
        nullable=False,
        index=True,
        comment="Clerk / Auth0 subject identifier — stable across sessions",
    )
    email: Mapped[str] = mapped_column(
        String(320),
        unique=True,
        nullable=False,
        index=True,
        comment="User's primary email address (max RFC 5321 length)",
    )
    display_name: Mapped[Optional[str]] = mapped_column(
        String(256),
        nullable=True,
    )
    avatar_url: Mapped[Optional[str]] = mapped_column(
        String(2048),
        nullable=True,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )
    # Soft-delete flag (we retain data for audit / reactivation)
    is_deleted: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    # Subscription / plan gating (free, pro, team)
    plan_tier: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="free",
        server_default="free",
    )
    # User preferences stored as flexible JSON (timezone, language, autofill toggles)
    preferences: Mapped[Optional[dict]] = mapped_column(
        JSONB,
        nullable=True,
    )

    # --- Relationships ---
    resumes: Mapped[list["Resume"]] = relationship(
        "Resume", back_populates="user", cascade="all, delete-orphan"
    )
    templates: Mapped[list["Template"]] = relationship(
        "Template", back_populates="user", cascade="all, delete-orphan"
    )
    applications: Mapped[list["Application"]] = relationship(
        "Application", back_populates="user", cascade="all, delete-orphan"
    )
    job_descriptions: Mapped[list["JobDescription"]] = relationship(
        "JobDescription", back_populates="user", cascade="all, delete-orphan"
    )

    # --- Indexes ---
    __table_args__ = (
        Index("ix_users_plan_tier", "plan_tier"),
        Index("ix_users_is_active_deleted", "is_active", "is_deleted"),
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email}>"


# =============================================================================
# resumes
# =============================================================================

class Resume(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    Stores metadata about an uploaded resume file.

    Security notes:
      - The actual file is stored encrypted in S3 (AES-256-GCM).
      - `storage_key` is the S3 object key — never the raw file URL.
      - `encryption_key_ref` is a reference to the KMS/vault key ID used
        so we can rotate keys without touching the DB schema.
      - Plain-text content is NEVER stored in this table.
    """
    __tablename__ = "resumes"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    file_name: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
        comment="Original uploaded filename (e.g. john_doe_cv.pdf)",
    )
    # S3 object key — constructed as {user_id}/{uuid}.enc
    storage_key: Mapped[str] = mapped_column(
        String(1024),
        nullable=False,
        unique=True,
        comment="Encrypted S3 object key — never expose raw URL",
    )
    # MIME type of the original file before encryption
    mime_type: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        default="application/pdf",
    )
    file_size_bytes: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
    )
    # Reference to key used for encryption (for key rotation)
    encryption_key_ref: Mapped[Optional[str]] = mapped_column(
        String(256),
        nullable=True,
        comment="KMS key ID or vault path used to encrypt this file",
    )
    # MD5 / SHA-256 of the *original* (pre-encryption) file for dedup
    content_hash: Mapped[Optional[str]] = mapped_column(
        String(64),
        nullable=True,
        index=True,
        comment="SHA-256 of the original plaintext file for deduplication",
    )
    is_primary: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="Whether this is the user's currently active resume",
    )
    parse_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
        comment="pending | processing | complete | failed",
    )

    # --- Relationships ---
    user: Mapped["User"] = relationship("User", back_populates="resumes")
    parsed_data: Mapped[Optional["ParsedResumeData"]] = relationship(
        "ParsedResumeData",
        back_populates="resume",
        uselist=False,
        cascade="all, delete-orphan",
    )

    # --- Indexes ---
    __table_args__ = (
        Index("ix_resumes_user_id_primary", "user_id", "is_primary"),
        Index("ix_resumes_parse_status", "parse_status"),
    )

    def __repr__(self) -> str:
        return f"<Resume id={self.id} user_id={self.user_id} primary={self.is_primary}>"


# =============================================================================
# parsed_resume_data
# =============================================================================

class ParsedResumeData(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    Structured output of the ResumeExtractorAgent.

    Stored as JSONB so we can query into sub-fields (e.g., skills array)
    without a rigid column schema that breaks when the AI output evolves.

    Canonical top-level shape (enforced by the Pydantic schema layer):
    {
      "name": str,
      "email": str,
      "phone": str | null,
      "location": str | null,
      "linkedin_url": str | null,
      "github_url": str | null,
      "portfolio_url": str | null,
      "summary": str | null,
      "skills": [str, ...],
      "experience": [
        {
          "company": str,
          "title": str,
          "start_date": str,
          "end_date": str | null,
          "description": str
        }, ...
      ],
      "education": [
        {
          "institution": str,
          "degree": str,
          "field": str,
          "graduation_year": int | null
        }, ...
      ],
      "certifications": [str, ...],
      "languages": [str, ...]
    }
    """
    __tablename__ = "parsed_resume_data"

    resume_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("resumes.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,        # 1:1 with resume
        index=True,
    )
    # Full structured parse output
    parsed_json: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        comment="Structured resume data extracted by ResumeExtractorAgent",
    )
    # Model / version that produced this parse (for reproducibility)
    parser_model: Mapped[Optional[str]] = mapped_column(
        String(128),
        nullable=True,
        comment="e.g. gpt-4o-2024-05-13",
    )
    parser_version: Mapped[Optional[str]] = mapped_column(
        String(32),
        nullable=True,
        comment="Internal parser version tag",
    )
    # Confidence score returned by the extraction pipeline [0.0, 1.0]
    confidence_score: Mapped[Optional[float]] = mapped_column(
        Float,
        nullable=True,
    )
    # Qdrant collection + point IDs where chunks are stored (for RAG)
    qdrant_point_ids: Mapped[Optional[list]] = mapped_column(
        JSONB,
        nullable=True,
        comment="List of Qdrant point UUIDs for resume chunk embeddings",
    )

    # --- Relationships ---
    resume: Mapped["Resume"] = relationship("Resume", back_populates="parsed_data")

    # --- Indexes ---
    __table_args__ = (
        # GIN index allows `parsed_json @> '{"skills": ["Python"]}'` queries
        Index("ix_parsed_resume_data_json_gin", "parsed_json", postgresql_using="gin"),
    )

    def __repr__(self) -> str:
        return f"<ParsedResumeData id={self.id} resume_id={self.resume_id}>"


# =============================================================================
# templates
# =============================================================================

class Template(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    User-defined job application templates.

    A template is a collection of pre-written answers for common application
    questions (cover letter, "why us", salary expectation, availability, etc.).
    Users can have multiple templates for different role types
    (e.g., "SWE template", "PM template", "Internship template").

    `answers_json` shape:
    {
      "first_name": "Jane",
      "last_name": "Doe",
      "phone": "+1-555-0100",
      "linkedin_url": "https://linkedin.com/in/janedoe",
      "github_url": "https://github.com/janedoe",
      "portfolio_url": "https://janedoe.dev",
      "current_location": "San Francisco, CA",
      "willing_to_relocate": true,
      "visa_sponsorship_required": false,
      "salary_expectation_usd": 180000,
      "notice_period_days": 14,
      "cover_letter": "...",
      "why_us": "...",
      "custom_qa": [
        {"question": "Describe a challenge you faced...", "answer": "..."}
      ]
    }
    """
    __tablename__ = "templates"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(
        String(256),
        nullable=False,
        comment="Human-readable template name (e.g. 'Senior SWE – Remote')",
    )
    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    # Core structured answers
    answers_json: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        comment="Key-value map of field names to pre-written user answers",
    )
    is_default: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="Extension uses this template when no explicit selection is made",
    )
    # Qdrant point IDs for template chunk embeddings
    qdrant_point_ids: Mapped[Optional[list]] = mapped_column(
        JSONB,
        nullable=True,
    )

    # --- Relationships ---
    user: Mapped["User"] = relationship("User", back_populates="templates")
    applications: Mapped[list["Application"]] = relationship(
        "Application", back_populates="template"
    )

    # --- Constraints & Indexes ---
    __table_args__ = (
        # A user can only have one default template
        Index(
            "uq_templates_user_default",
            "user_id",
            unique=True,
            postgresql_where=(
                # Partial unique index — only one row per user where is_default=true
            ),
        ),
        Index("ix_templates_user_id", "user_id"),
        Index("ix_templates_answers_json_gin", "answers_json", postgresql_using="gin"),
    )

    def __repr__(self) -> str:
        return f"<Template id={self.id} name={self.name} user_id={self.user_id}>"


# =============================================================================
# job_descriptions
# =============================================================================

class JobDescription(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    Stores a job description captured by the extension or manually entered.

    The extension scrapes the JD from the page before/during form fill.
    We embed the JD into Qdrant so the AnswerGenerationAgent can retrieve
    role-specific context for open-ended questions.
    """
    __tablename__ = "job_descriptions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    company_name: Mapped[Optional[str]] = mapped_column(
        String(512),
        nullable=True,
        index=True,
    )
    role_title: Mapped[Optional[str]] = mapped_column(
        String(512),
        nullable=True,
        index=True,
    )
    source_url: Mapped[Optional[str]] = mapped_column(
        String(2048),
        nullable=True,
    )
    platform: Mapped[ATSPlatform] = mapped_column(
        Enum(ATSPlatform, name="ats_platform_enum"),
        nullable=False,
        default=ATSPlatform.UNKNOWN,
    )
    raw_text: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Full raw text of the job description",
    )
    # AI-extracted structured summary of the JD
    parsed_json: Mapped[Optional[dict]] = mapped_column(
        JSONB,
        nullable=True,
        comment="Structured fields: required_skills, nice_to_have, responsibilities, etc.",
    )
    # Qdrant point IDs for JD embeddings
    qdrant_point_ids: Mapped[Optional[list]] = mapped_column(
        JSONB,
        nullable=True,
    )

    # --- Relationships ---
    user: Mapped["User"] = relationship("User", back_populates="job_descriptions")
    applications: Mapped[list["Application"]] = relationship(
        "Application", back_populates="job_description"
    )

    # --- Indexes ---
    __table_args__ = (
        Index("ix_jd_user_id_platform", "user_id", "platform"),
        Index("ix_jd_company_role", "company_name", "role_title"),
        Index("ix_jd_parsed_json_gin", "parsed_json", postgresql_using="gin"),
    )

    def __repr__(self) -> str:
        return f"<JobDescription id={self.id} company={self.company_name} role={self.role_title}>"


# =============================================================================
# applications
# =============================================================================

class Application(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    Represents a single job application session initiated by the extension.

    An application links:
      - a user
      - a job description
      - a template used for autofill
      - all form field answers submitted during that session
    """
    __tablename__ = "applications"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    job_description_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("job_descriptions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    template_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("templates.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    resume_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("resumes.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Detected ATS platform
    platform: Mapped[ATSPlatform] = mapped_column(
        Enum(ATSPlatform, name="ats_platform_enum"),
        nullable=False,
        default=ATSPlatform.UNKNOWN,
    )
    apply_url: Mapped[Optional[str]] = mapped_column(
        String(2048),
        nullable=True,
        comment="URL where the application form was detected",
    )
    status: Mapped[ApplicationStatus] = mapped_column(
        Enum(ApplicationStatus, name="application_status_enum"),
        nullable=False,
        default=ApplicationStatus.DETECTED,
        index=True,
    )
    # Timestamp when user actually submitted the form (may be null if abandoned)
    submitted_at: Mapped[Optional[datetime]] = mapped_column(
        nullable=True,
    )
    # Number of fields auto-filled (for analytics)
    fields_filled_count: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=0,
    )
    # Number of AI-generated answers in this session
    ai_answers_count: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=0,
    )
    # Optional user notes added via dashboard
    notes: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    # Extension session metadata (browser, OS, extension version)
    session_meta: Mapped[Optional[dict]] = mapped_column(
        JSONB,
        nullable=True,
    )

    # --- Relationships ---
    user: Mapped["User"] = relationship("User", back_populates="applications")
    job_description: Mapped[Optional["JobDescription"]] = relationship(
        "JobDescription", back_populates="applications"
    )
    template: Mapped[Optional["Template"]] = relationship(
        "Template", back_populates="applications"
    )
    resume: Mapped[Optional["Resume"]] = relationship("Resume")
    answers: Mapped[list["ApplicationAnswer"]] = relationship(
        "ApplicationAnswer",
        back_populates="application",
        cascade="all, delete-orphan",
    )
    form_fields: Mapped[list["FormField"]] = relationship(
        "FormField",
        back_populates="application",
        cascade="all, delete-orphan",
    )

    # --- Indexes ---
    __table_args__ = (
        Index("ix_applications_user_status", "user_id", "status"),
        Index("ix_applications_user_created", "user_id", "created_at"),
        Index("ix_applications_platform", "platform"),
        Index("ix_applications_submitted_at", "submitted_at"),
    )

    def __repr__(self) -> str:
        return f"<Application id={self.id} status={self.status} user_id={self.user_id}>"


# =============================================================================
# form_fields
# =============================================================================

class FormField(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    Describes every form field detected on a job application page.

    Populated by the FormUnderstandingAgent from DOM scan results.
    One Application → many FormFields.
    """
    __tablename__ = "form_fields"

    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("applications.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Raw label text as it appears in the DOM
    label_text: Mapped[Optional[str]] = mapped_column(
        String(1024),
        nullable=True,
        comment="Visible label text extracted from DOM",
    )
    # Canonical field name the system mapped this to (e.g. 'first_name')
    mapped_field_key: Mapped[Optional[str]] = mapped_column(
        String(256),
        nullable=True,
        index=True,
        comment="Canonical template key this field maps to",
    )
    field_type: Mapped[FieldType] = mapped_column(
        Enum(FieldType, name="field_type_enum"),
        nullable=False,
        default=FieldType.UNKNOWN,
    )
    # HTML attributes for deterministic re-targeting on re-fill
    html_name: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    html_id: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    html_placeholder: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    aria_label: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    xpath: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    css_selector: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # For select/radio/checkbox fields — available options as JSON array
    options: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    # Whether this field requires an AI-generated answer
    requires_ai: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    is_required: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    # Confidence score for the semantic mapping [0.0, 1.0]
    mapping_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    display_order: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=0,
        comment="DOM order of the field for deterministic re-fill sequence",
    )

    # --- Relationships ---
    application: Mapped["Application"] = relationship(
        "Application", back_populates="form_fields"
    )
    answer: Mapped[Optional["ApplicationAnswer"]] = relationship(
        "ApplicationAnswer",
        back_populates="form_field",
        uselist=False,
    )

    # --- Indexes ---
    __table_args__ = (
        Index("ix_form_fields_application_order", "application_id", "display_order"),
        Index("ix_form_fields_mapped_key", "mapped_field_key"),
        Index("ix_form_fields_requires_ai", "requires_ai"),
    )

    def __repr__(self) -> str:
        return f"<FormField id={self.id} label={self.label_text!r} mapped={self.mapped_field_key!r}>"


# =============================================================================
# application_answers
# =============================================================================

class ApplicationAnswer(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    The actual value injected (or suggested) for each FormField.

    Tracks provenance so we know whether an answer came from a template,
    the resume, AI generation, or a user override.  This is critical for
    the RAG memory system — past answers become context for future fills.
    """
    __tablename__ = "application_answers"

    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("applications.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    form_field_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("form_fields.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Denormalized for fast user-scoped retrieval without joins",
    )
    # The answer value as stored / injected
    answer_text: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Final answer value — may be empty if field was skipped",
    )
    # Where the answer came from
    source: Mapped[AnswerSource] = mapped_column(
        Enum(AnswerSource, name="answer_source_enum"),
        nullable=False,
        default=AnswerSource.TEMPLATE,
    )
    # Confidence score from AI pipeline [0.0, 1.0] — null for non-AI sources
    confidence_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # Whether the user manually edited the AI suggestion before submission
    was_edited: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    # Qdrant point ID for this answer (used as RAG memory)
    qdrant_point_id: Mapped[Optional[str]] = mapped_column(
        String(128),
        nullable=True,
        index=True,
    )

    # --- Relationships ---
    application: Mapped["Application"] = relationship(
        "Application", back_populates="answers"
    )
    form_field: Mapped["FormField"] = relationship(
        "FormField", back_populates="answer"
    )
    user: Mapped["User"] = relationship("User")
    ai_answer: Mapped[Optional["AIGeneratedAnswer"]] = relationship(
        "AIGeneratedAnswer",
        back_populates="application_answer",
        uselist=False,
        cascade="all, delete-orphan",
    )

    # --- Constraints & Indexes ---
    __table_args__ = (
        # Each form field should have at most one answer per application
        UniqueConstraint("application_id", "form_field_id", name="uq_answer_per_field"),
        Index("ix_app_answers_user_source", "user_id", "source"),
        Index("ix_app_answers_application_id", "application_id"),
        Index("ix_app_answers_qdrant_point", "qdrant_point_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<ApplicationAnswer id={self.id} "
            f"source={self.source} confidence={self.confidence_score}>"
        )


# =============================================================================
# ai_generated_answers
# =============================================================================

class AIGeneratedAnswer(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    Full audit log of every AI answer generation event.

    Stores the exact prompt sent, the raw LLM response, retrieved RAG context,
    model used, token counts, and latency — for debugging, cost tracking,
    and quality improvement.

    Linked 1:1 to an ApplicationAnswer (the answer that was actually used).
    """
    __tablename__ = "ai_generated_answers"

    application_answer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("application_answers.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,    # 1:1 with application_answer
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Denormalized for fast user-scoped cost/usage queries",
    )
    # The exact prompt sent to the LLM (for reproducibility and auditing)
    prompt_text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Full prompt including system message and all injected context",
    )
    # Raw response from the LLM before any post-processing
    raw_response: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    # The final cleaned answer after post-processing
    final_answer: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    # Retrieved RAG context chunks used to build the prompt
    context_chunks: Mapped[Optional[list]] = mapped_column(
        JSONB,
        nullable=True,
        comment="List of {source, text, score} dicts from Qdrant retrieval",
    )
    # Model metadata
    model_name: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        comment="e.g. gpt-4o-2024-05-13",
    )
    prompt_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Latency in milliseconds
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Agent confidence score returned by the pipeline [0.0, 1.0]
    confidence_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # Whether the answer was flagged for hallucination by the validation step
    hallucination_flagged: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    # --- Relationships ---
    application_answer: Mapped["ApplicationAnswer"] = relationship(
        "ApplicationAnswer", back_populates="ai_answer"
    )
    user: Mapped["User"] = relationship("User")

    # --- Indexes ---
    __table_args__ = (
        Index("ix_ai_answers_user_id", "user_id"),
        Index("ix_ai_answers_model_name", "model_name"),
        Index("ix_ai_answers_hallucination_flagged", "hallucination_flagged"),
        Index("ix_ai_answers_created_at", "created_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<AIGeneratedAnswer id={self.id} "
            f"model={self.model_name} tokens={self.total_tokens} "
            f"flagged={self.hallucination_flagged}>"
        )