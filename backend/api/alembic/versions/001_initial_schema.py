"""
backend/api/alembic/versions/001_initial_schema.py
===================================================
Alembic migration: create all tables for Job Autofill Copilot.

Run with:
    alembic upgrade head

Rollback with:
    alembic downgrade -1
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# Revision identifiers
revision = "001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # ENUMS (create before tables that reference them)
    # ------------------------------------------------------------------
    ats_platform_enum = postgresql.ENUM(
        "greenhouse", "lever", "workday", "keka", "oracle_cloud",
        "google_forms", "microsoft_forms", "sensehq", "workable",
        "appdover", "bamboohr", "smartrecruiters", "icims", "ashby",
        "ultipro", "sap_successfactors", "custom", "unknown",
        name="ats_platform_enum",
    )
    ats_platform_enum.create(op.get_bind(), checkfirst=True)

    application_status_enum = postgresql.ENUM(
        "detected", "autofilled", "submitted", "failed", "skipped",
        name="application_status_enum",
    )
    application_status_enum.create(op.get_bind(), checkfirst=True)

    field_type_enum = postgresql.ENUM(
        "text", "textarea", "email", "phone", "url", "date",
        "select", "multiselect", "checkbox", "radio", "file",
        "hidden", "unknown",
        name="field_type_enum",
    )
    field_type_enum.create(op.get_bind(), checkfirst=True)

    answer_source_enum = postgresql.ENUM(
        "template", "resume", "ai_generated", "user_override", "past_answer",
        name="answer_source_enum",
    )
    answer_source_enum.create(op.get_bind(), checkfirst=True)

    # ------------------------------------------------------------------
    # users
    # ------------------------------------------------------------------
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("clerk_user_id", sa.String(256), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("display_name", sa.String(256), nullable=True),
        sa.Column("avatar_url", sa.String(2048), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("plan_tier", sa.String(32), nullable=False, server_default="free"),
        sa.Column("preferences", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_users_id", "users", ["id"])
    op.create_index("ix_users_clerk_user_id", "users", ["clerk_user_id"], unique=True)
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_plan_tier", "users", ["plan_tier"])
    op.create_index("ix_users_is_active_deleted", "users", ["is_active", "is_deleted"])

    # ------------------------------------------------------------------
    # resumes
    # ------------------------------------------------------------------
    op.create_table(
        "resumes",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("file_name", sa.String(512), nullable=False),
        sa.Column("storage_key", sa.String(1024), nullable=False),
        sa.Column("mime_type", sa.String(128), nullable=False, server_default="application/pdf"),
        sa.Column("file_size_bytes", sa.Integer(), nullable=True),
        sa.Column("encryption_key_ref", sa.String(256), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("parse_status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_resumes_id", "resumes", ["id"])
    op.create_index("ix_resumes_user_id", "resumes", ["user_id"])
    op.create_index("ix_resumes_user_id_primary", "resumes", ["user_id", "is_primary"])
    op.create_index("ix_resumes_content_hash", "resumes", ["content_hash"])
    op.create_index("ix_resumes_parse_status", "resumes", ["parse_status"])
    op.create_index("uq_resumes_storage_key", "resumes", ["storage_key"], unique=True)

    # ------------------------------------------------------------------
    # parsed_resume_data
    # ------------------------------------------------------------------
    op.create_table(
        "parsed_resume_data",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("resume_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("resumes.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("parsed_json", postgresql.JSONB(), nullable=False),
        sa.Column("parser_model", sa.String(128), nullable=True),
        sa.Column("parser_version", sa.String(32), nullable=True),
        sa.Column("confidence_score", sa.Float(), nullable=True),
        sa.Column("qdrant_point_ids", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_parsed_resume_data_id", "parsed_resume_data", ["id"])
    op.create_index("ix_parsed_resume_data_resume_id", "parsed_resume_data", ["resume_id"], unique=True)
    op.create_index(
        "ix_parsed_resume_data_json_gin",
        "parsed_resume_data",
        ["parsed_json"],
        postgresql_using="gin",
    )

    # ------------------------------------------------------------------
    # templates
    # ------------------------------------------------------------------
    op.create_table(
        "templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("answers_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("qdrant_point_ids", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_templates_id", "templates", ["id"])
    op.create_index("ix_templates_user_id", "templates", ["user_id"])
    op.create_index(
        "ix_templates_answers_json_gin",
        "templates",
        ["answers_json"],
        postgresql_using="gin",
    )
    # Partial unique index: only one default per user
    op.execute(
        """
        CREATE UNIQUE INDEX uq_templates_user_one_default
        ON templates (user_id)
        WHERE is_default = true;
        """
    )

    # ------------------------------------------------------------------
    # job_descriptions
    # ------------------------------------------------------------------
    op.create_table(
        "job_descriptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("company_name", sa.String(512), nullable=True),
        sa.Column("role_title", sa.String(512), nullable=True),
        sa.Column("source_url", sa.String(2048), nullable=True),
        sa.Column("platform", postgresql.ENUM(name="ats_platform_enum", create_type=False), nullable=False, server_default="unknown"),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("parsed_json", postgresql.JSONB(), nullable=True),
        sa.Column("qdrant_point_ids", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_jd_id", "job_descriptions", ["id"])
    op.create_index("ix_jd_user_id", "job_descriptions", ["user_id"])
    op.create_index("ix_jd_user_id_platform", "job_descriptions", ["user_id", "platform"])
    op.create_index("ix_jd_company_role", "job_descriptions", ["company_name", "role_title"])
    op.create_index("ix_jd_parsed_json_gin", "job_descriptions", ["parsed_json"], postgresql_using="gin")

    # ------------------------------------------------------------------
    # applications
    # ------------------------------------------------------------------
    op.create_table(
        "applications",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_description_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("job_descriptions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("template_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("templates.id", ondelete="SET NULL"), nullable=True),
        sa.Column("resume_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("resumes.id", ondelete="SET NULL"), nullable=True),
        sa.Column("platform", postgresql.ENUM(name="ats_platform_enum", create_type=False), nullable=False, server_default="unknown"),
        sa.Column("apply_url", sa.String(2048), nullable=True),
        sa.Column("status", postgresql.ENUM(name="application_status_enum", create_type=False), nullable=False, server_default="detected"),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fields_filled_count", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("ai_answers_count", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("session_meta", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_applications_id", "applications", ["id"])
    op.create_index("ix_applications_user_id", "applications", ["user_id"])
    op.create_index("ix_applications_user_status", "applications", ["user_id", "status"])
    op.create_index("ix_applications_user_created", "applications", ["user_id", "created_at"])
    op.create_index("ix_applications_platform", "applications", ["platform"])
    op.create_index("ix_applications_submitted_at", "applications", ["submitted_at"])

    # ------------------------------------------------------------------
    # form_fields
    # ------------------------------------------------------------------
    op.create_table(
        "form_fields",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("application_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("applications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("label_text", sa.String(1024), nullable=True),
        sa.Column("mapped_field_key", sa.String(256), nullable=True),
        sa.Column("field_type", postgresql.ENUM(name="field_type_enum", create_type=False), nullable=False, server_default="unknown"),
        sa.Column("html_name", sa.String(512), nullable=True),
        sa.Column("html_id", sa.String(512), nullable=True),
        sa.Column("html_placeholder", sa.String(512), nullable=True),
        sa.Column("aria_label", sa.String(512), nullable=True),
        sa.Column("xpath", sa.Text(), nullable=True),
        sa.Column("css_selector", sa.Text(), nullable=True),
        sa.Column("options", postgresql.JSONB(), nullable=True),
        sa.Column("requires_ai", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_required", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("mapping_confidence", sa.Float(), nullable=True),
        sa.Column("display_order", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_form_fields_id", "form_fields", ["id"])
    op.create_index("ix_form_fields_application_id", "form_fields", ["application_id"])
    op.create_index("ix_form_fields_application_order", "form_fields", ["application_id", "display_order"])
    op.create_index("ix_form_fields_mapped_key", "form_fields", ["mapped_field_key"])
    op.create_index("ix_form_fields_requires_ai", "form_fields", ["requires_ai"])

    # ------------------------------------------------------------------
    # application_answers
    # ------------------------------------------------------------------
    op.create_table(
        "application_answers",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("application_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("applications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("form_field_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("form_fields.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("answer_text", sa.Text(), nullable=True),
        sa.Column("source", postgresql.ENUM(name="answer_source_enum", create_type=False), nullable=False, server_default="template"),
        sa.Column("confidence_score", sa.Float(), nullable=True),
        sa.Column("was_edited", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("qdrant_point_id", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_app_answers_id", "application_answers", ["id"])
    op.create_index("ix_app_answers_application_id", "application_answers", ["application_id"])
    op.create_index("ix_app_answers_form_field_id", "application_answers", ["form_field_id"])
    op.create_index("ix_app_answers_user_id", "application_answers", ["user_id"])
    op.create_index("ix_app_answers_user_source", "application_answers", ["user_id", "source"])
    op.create_index("ix_app_answers_qdrant_point", "application_answers", ["qdrant_point_id"])
    op.create_unique_constraint(
        "uq_answer_per_field",
        "application_answers",
        ["application_id", "form_field_id"],
    )

    # ------------------------------------------------------------------
    # ai_generated_answers
    # ------------------------------------------------------------------
    op.create_table(
        "ai_generated_answers",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("application_answer_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("application_answers.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("prompt_text", sa.Text(), nullable=False),
        sa.Column("raw_response", sa.Text(), nullable=False),
        sa.Column("final_answer", sa.Text(), nullable=False),
        sa.Column("context_chunks", postgresql.JSONB(), nullable=True),
        sa.Column("model_name", sa.String(128), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("confidence_score", sa.Float(), nullable=True),
        sa.Column("hallucination_flagged", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_ai_answers_id", "ai_generated_answers", ["id"])
    op.create_index("ix_ai_answers_app_answer_id", "ai_generated_answers", ["application_answer_id"], unique=True)
    op.create_index("ix_ai_answers_user_id", "ai_generated_answers", ["user_id"])
    op.create_index("ix_ai_answers_model_name", "ai_generated_answers", ["model_name"])
    op.create_index("ix_ai_answers_hallucination_flagged", "ai_generated_answers", ["hallucination_flagged"])
    op.create_index("ix_ai_answers_created_at", "ai_generated_answers", ["created_at"])


def downgrade() -> None:
    # Drop tables in reverse dependency order
    op.drop_table("ai_generated_answers")
    op.drop_table("application_answers")
    op.drop_table("form_fields")
    op.drop_table("applications")
    op.drop_table("job_descriptions")
    op.drop_table("templates")
    op.drop_table("parsed_resume_data")
    op.drop_table("resumes")
    op.drop_table("users")

    # Drop enums
    for enum_name in (
        "answer_source_enum",
        "field_type_enum",
        "application_status_enum",
        "ats_platform_enum",
    ):
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")