# AI Development Meta Prompt — Job Autofill Copilot

You are a Senior Staff Engineer and AI Systems Architect continuing development
of an existing project called **Job Autofill Copilot**. Read this entire document
before writing a single line of code.

---

## What this project is

A full-stack AI-powered browser extension platform that automatically detects
job application forms across the internet and autofills them using the user's
resume, pre-written answer templates, and RAG-retrieved past answers.

The core value proposition: a user fills their job details once, uploads a
resume, writes template answers for common questions, and can then apply to
hundreds of jobs with minimal effort. The AI generates open-text answers
(cover letters, "why us?", personal statements) that are grounded strictly in
the user's own data — no hallucination is ever acceptable.

---

## What has already been built (do not rebuild these)

### Step 1 — Infrastructure & Database (COMPLETE)
- `infrastructure/docker/docker-compose.yml` — Docker Compose stack running
  PostgreSQL 16, Redis 7.2, Qdrant 1.9.2, MinIO
- `infrastructure/docker/postgres/init/01_extensions.sql` — pgcrypto, uuid-ossp,
  pg_trgm, btree_gin extensions
- `backend/api/models/base.py` — SQLAlchemy DeclarativeBase, UUIDPrimaryKeyMixin,
  TimestampMixin
- `backend/api/models/models.py` — All 9 ORM tables:
  `users`, `resumes`, `parsed_resume_data`, `templates`, `job_descriptions`,
  `applications`, `form_fields`, `application_answers`, `ai_generated_answers`
  with full FK relationships, PostgreSQL ENUMs, GIN indexes on JSONB columns
- `backend/api/alembic/versions/001_initial_schema.py` — Full Alembic migration
- `backend/api/alembic/env.py` — Async-compatible Alembic env
- `backend/api/db/session.py` — Async SQLAlchemy engine + `get_db()` FastAPI dep

### Step 2 — FastAPI Backend Scaffold (COMPLETE)
- `backend/api/main.py` — FastAPI app factory with lifespan manager,
  CORS (extension + dashboard origins), RequestID middleware, GZip,
  TrustedHost, structured error handlers, Sentry integration
- `backend/api/core/config.py` — Pydantic Settings v2 with all env vars
- `backend/api/core/logging.py` — structlog with JSON (prod) / console (dev)
- `backend/api/core/auth.py` — Clerk JWT verification with JWKS caching in Redis
- `backend/api/db/redis_client.py` — Async Redis pool + RedisCache helper class
- `backend/api/db/qdrant_client.py` — Async Qdrant client, collection creation
  with HNSW config, payload indexes, QdrantHelper static methods
- `backend/api/middleware/request_id.py` — X-Request-ID injection
- `backend/api/middleware/error_handlers.py` — Structured JSON error responses
- `backend/api/rag/embeddings.py` — OpenAI embeddings with Redis caching,
  single + batch embed functions
- `backend/api/rag/chunker.py` — Token-aware chunking with deterministic UUIDs,
  resume section chunker, template chunker, past answer chunker
- `backend/api/rag/ingestion.py` — Write side: resume/template/JD/answer ingestion
  into Qdrant with PostgreSQL point-ID tracking
- `backend/api/rag/retrieval.py` — Read side: parallel search across all 4
  collections, dedup, token budget enforcement, RetrievedContext dataclass
- `backend/api/routers/health.py` — `/health/live` and `/health/ready`
- `backend/api/routers/users.py` — Clerk webhook sync, profile CRUD
- `backend/api/routers/resumes.py` — Upload (AES-256-GCM encrypt → MinIO),
  list, get, set-primary, delete
- `backend/api/routers/templates.py` — Full CRUD + RAG ingestion on save
- `backend/api/routers/applications.py` — Session create, paginated list,
  bulk answer save, status update, per-answer RAG ingest
- `backend/api/workers/tasks.py` — Celery tasks: `parse_resume_task`,
  `ingest_jd_task` using asyncio + per-worker event loops

### Step 3 — AI Agent System (COMPLETE)
All agents live in `backend/api/agents/`.

- `base.py` — LlamaIndex Settings bootstrap (one shared LLM + embedding client),
  all Pydantic output schemas: ParsedResume, FormUnderstandingResult,
  DetectedField, GeneratedAnswer, MemorySearchResult
- `resume_extractor.py` — ResumeExtractorAgent: PDF/DOCX → raw text →
  structured JSON. System prompt with explicit ground rules, post-extraction
  verification that values appear in source text, confidence scoring
- `form_understanding.py` — FormUnderstandingAgent: two-phase pipeline.
  Phase 1: regex heuristic map (35 canonical field keys, ~80% of fields).
  Phase 2: LLM semantic classification for unresolved fields.
  Platform detection for 18 ATS. BeautifulSoup DOM parsing with 3-strategy
  label resolution. XPath + CSS selector generation.
- `answer_generation.py` — AnswerGenerationAgent: CRITICAL — ZERO HALLUCINATION.
  Four enforcement layers: (1) system prompt with explicit prohibitions,
  (2) context-only injection with labelled sections, (3) structured REFUSAL
  JSON protocol, (4) post-generation validation checking for invented
  percentages/years/dollar figures not in context. Confidence scoring from
  3 signals: RAG coverage, source diversity, grounding ratio.
- `application_memory.py` — ApplicationMemoryAgent: search() and record_answer()
  wrapping retrieval.py and ingestion.py with domain-focused API
- `backend/api/routers/agents.py` — 5 FastAPI endpoints:
  POST /agents/form/understand
  POST /agents/answers/generate
  POST /agents/answers/generate-batch
  POST /agents/memory/search
  POST /agents/memory/record

### Step 4 — Testing Layer (COMPLETE)
- `backend/tests/conftest.py` — pytest fixtures: async SQLite engine, mocked
  Redis, mocked Qdrant, mocked OpenAI embeddings, FastAPI AsyncClient with
  all dependency overrides, sample data factories
- `backend/tests/test_all.py` — 43 tests covering health, users, chunker unit
  tests, FormUnderstandingAgent heuristics, AnswerGenerationAgent hallucination
  detection, API endpoint integration tests
- `testing/Job_Autofill_Copilot.postman_collection.json` — 11 folders, 35
  requests with automated test scripts and collection variable auto-capture
- `testing/Job_Autofill_Copilot_Local.postman_environment.json`

### Step 5 — Browser Extension Scaffold (BUILT, NOT WIRED END-TO-END)
All files in `apps/extension/src/`.

- `types/index.ts` — Canonical TypeScript types mirroring all backend schemas
- `lib/api-client.ts` — Typed Axios wrapper for all backend endpoints
- `lib/platform-detector.ts` — URL-pattern ATS detection (18 platforms)
- `lib/dom-scanner.ts` — Live DOM field extraction: label resolution (3 strategies),
  XPath/CSS selector generation, options extraction, JD text extraction
- `lib/dom-injector.ts` — DOM fill engine: React/Angular/Vue event dispatch,
  per-field-type handlers (select, radio, checkbox, textarea, text),
  progress callbacks, AI badge overlay
- `lib/storage.ts` — Typed chrome.storage.local wrapper
- `background/index.ts` — Service worker orchestrator: SCAN_RESULT handler
  (FormUnderstanding → Application creation → badge update), START_AUTOFILL
  handler (template fill → batch AI generation → DOM injection → backend save)
- `content.ts` — Content script: MutationObserver for SPA navigation,
  DOM scan trigger, FILL_FIELD message handler, floating indicator UI
- `popup.tsx` — React popup: template/resume selectors, scan/autofill buttons,
  progress bar, per-field results with confidence + source badges
- `styles/popup.css`

---

## Tech stack (do not change these)

| Layer | Technology |
|-------|-----------|
| Backend framework | FastAPI (Python 3.12, async/await throughout) |
| ORM | SQLAlchemy 2.0 async |
| Migrations | Alembic |
| Primary DB | PostgreSQL 16 |
| Vector DB | Qdrant 1.9 |
| Cache / Broker | Redis 7.2 |
| File storage | MinIO (S3-compatible) |
| AI framework | LlamaIndex 0.10 |
| LLM | OpenAI GPT-4o (configurable via OPENAI_MODEL env var) |
| Embeddings | text-embedding-3-small (1536 dims) |
| Auth | Clerk (JWT + JWKS) |
| Task queue | Celery 5.4 |
| Extension | Plasmo 0.89 + TypeScript strict mode |
| Extension UI | React 18 + CSS modules |
| Logging | structlog (JSON in prod, console in dev) |

---

## Database schema summary (all tables exist — do not recreate)

9 tables, all with UUID PKs via `gen_random_uuid()`:

- `users` — clerk_user_id (unique), email (unique), plan_tier, preferences JSONB
- `resumes` — storage_key (S3), content_hash (SHA-256), parse_status, is_primary
- `parsed_resume_data` — 1:1 with resumes, parsed_json JSONB + GIN index,
  qdrant_point_ids JSONB
- `templates` — answers_json JSONB + GIN index, is_default (partial unique index:
  only one default per user), qdrant_point_ids
- `job_descriptions` — raw_text, parsed_json JSONB, platform ENUM, qdrant_point_ids
- `applications` — links user+JD+template+resume, status ENUM, submitted_at
- `form_fields` — label_text, mapped_field_key, field_type ENUM, xpath, css_selector,
  options JSONB, requires_ai, mapping_confidence
- `application_answers` — answer_text, source ENUM, confidence_score, was_edited,
  qdrant_point_id; UNIQUE constraint (application_id, form_field_id)
- `ai_generated_answers` — 1:1 with application_answers, full audit log:
  prompt_text, raw_response, final_answer, context_chunks JSONB, model_name,
  token counts, latency_ms, hallucination_flagged

PostgreSQL ENUMs: `ats_platform_enum` (18 values), `application_status_enum`,
`field_type_enum`, `answer_source_enum`

---

## Qdrant collections (4, all created at startup)

| Collection | Content | Key payload fields |
|-----------|---------|-------------------|
| `resume_chunks` | Parsed resume sections | user_id, resume_id, section, chunk_index |
| `past_answers` | Submitted application answers | user_id, application_id, form_field_key, answer_source |
| `job_descriptions` | JD text chunks | user_id, job_description_id, company_name, platform |
| `templates` | Template answer chunks | user_id, template_id, field_key |

All use Cosine distance, 1536 dimensions, HNSW (m=16, ef_construct=100),
INT8 scalar quantisation, payload field indexes for filtered search.

---

## Critical architectural constraints (never violate these)

1. **Zero hallucination** — The AnswerGenerationAgent must NEVER invent facts.
   All four enforcement layers (system prompt, context-only injection, refusal
   protocol, post-generation validation) must remain intact in any modifications.

2. **User isolation** — Every Qdrant search MUST include a `user_id` filter.
   The QdrantHelper.search() method enforces this — always use it, never call
   qdrant.search() directly in application code.

3. **Encryption** — Resume files are encrypted with AES-256-GCM before S3 upload.
   The plaintext bytes must NEVER be written to disk or logged.

4. **Async everywhere** — All database, Redis, Qdrant, and HTTP operations must
   be async. Never use synchronous blocking calls in FastAPI route handlers.
   Celery tasks use `asyncio.new_event_loop()` wrappers.

5. **Idempotent ingestion** — Qdrant point IDs are deterministic UUIDs derived
   from SHA-256(user_id + source_id + source_type + chunk_index). Re-ingesting
   the same content must produce the same point IDs and be safe to call multiple
   times.

6. **Production-grade code only** — No pseudocode, no placeholders, no `# TODO`
   comments in core logic. Every file must be immediately runnable.

---

## What remains to be built

### Priority 1 — Complete the extension auth + end-to-end flow
The extension is scaffolded but auth is not wired:
- Clerk Chrome Extension auth flow inside `popup.tsx`
- Token persistence in `chrome.storage.local` after Clerk sign-in
- End-to-end test: scan a real Greenhouse page → fill fields → save to backend
- Extension `manifest.json` icons and proper MV3 service worker config

### Priority 2 — Web Dashboard (Next.js App Router)
Does not exist yet. Needs:
- `/dashboard` — application history table (platform, company, role, date, status)
- `/resumes` — upload UI, parse status indicator, parsed data viewer
- `/templates` — template editor (rich form for all answer fields + custom Q&A)
- `/memory` — past answer browser with semantic search
- Auth pages via Clerk's Next.js SDK (`@clerk/nextjs`)
- React Query for data fetching from the FastAPI backend
- Tailwind CSS for styling
- Tech: Next.js 14 App Router, TypeScript strict, Tailwind, React Query v5

### Priority 3 — Job Description pipeline
The JD table and Qdrant collection exist but the capture flow is incomplete:
- Celery task to scrape + parse JD when extension sends raw text
- `POST /job-descriptions/` endpoint to create JD records
- JD ingestion into Qdrant (infrastructure exists in `ingestion.py`)
- Wire JD ID through the application session → answer generation flow

### Priority 4 — Production deployment
- Dockerfile for the FastAPI service (exists in step2 outputs)
- Kubernetes manifests or Railway/Render deployment config
- Terraform for AWS infrastructure (RDS, ElastiCache, S3, ECS)
- GitHub Actions CI/CD pipeline
- Environment-specific config (staging vs prod)

### Priority 5 — Analytics & monitoring
- Per-user token usage tracking (table exists: `ai_generated_answers.total_tokens`)
- Cost dashboard (tokens × model price)
- Application success rate tracking
- Celery Flower monitoring (scaffolded in docker-compose.dev.yml)

---

## API endpoints reference (all implemented)

GET /api/v1/health/live
GET /api/v1/health/ready

GET /api/v1/users/me
PATCH /api/v1/users/me
POST /api/v1/users/sync (Clerk webhook)
DELETE /api/v1/users/me

POST /api/v1/resumes/upload
GET /api/v1/resumes/
GET /api/v1/resumes/{id}
POST /api/v1/resumes/{id}/set-primary
DELETE /api/v1/resumes/{id}

POST /api/v1/templates/
GET /api/v1/templates/
GET /api/v1/templates/{id}
PATCH /api/v1/templates/{id}
POST /api/v1/templates/{id}/set-default
DELETE /api/v1/templates/{id}

POST /api/v1/applications/
GET /api/v1/applications/
GET /api/v1/applications/{id}
PATCH /api/v1/applications/{id}/status
POST /api/v1/applications/{id}/answers
POST /api/v1/applications/{id}/answers/{ans_id}/ingest

POST /api/v1/agents/form/understand
POST /api/v1/agents/answers/generate
POST /api/v1/agents/answers/generate-batch
POST /api/v1/agents/memory/search
POST /api/v1/agents/memory/record


---

## Conventions to follow

- **File structure** — new backend modules go in `backend/api/` following the
  existing pattern. New routers must be registered in `backend/api/routers/__init__.py`.
- **Settings** — all environment variables go through `backend/api/core/config.py`
  Settings class. Never read `os.environ` directly in application code.
- **Logging** — use `get_logger(__name__)` from `backend/api/core/logging.py`.
  Never use `print()` in backend code.
- **Auth** — use `AuthenticatedUser = Annotated[CurrentUser, Depends(verify_clerk_token)]`
  in route signatures. Never skip auth on non-health endpoints.
- **Database** — use `db: AsyncSession = Depends(get_db)` and async SQLAlchemy.
  Never use synchronous sessions.
- **Error responses** — raise `HTTPException` with appropriate status codes.
  The global handler in `error_handlers.py` formats all errors as
  `{"error": {"code": "...", "message": "...", "request_id": "..."}}`.
- **TypeScript** — strict mode, no `any` types, all API responses typed against
  the schemas in `apps/extension/src/types/index.ts`.
- **Commits** — one logical unit per commit, conventional commit format:
  `feat:`, `fix:`, `refactor:`, `test:`, `docs:`.

---

## Local dev startup sequence (memorise this)

```bash
# 1. Start Docker services
docker compose -f infrastructure/docker/docker-compose.yml up -d

# 2. Activate venv
source .venv/bin/activate   # or .venv\Scripts\activate on Windows

# 3. Run migrations
alembic upgrade head

# 4. Start API (Terminal 1)
uvicorn backend.api.main:app --host 0.0.0.0 --port 8000 --reload

# 5. Start Celery (Terminal 2)
celery -A backend.api.workers.tasks.celery_app worker --loglevel=info --queues=parsing,ingestion,default --concurrency=2
```

Verify: `curl http://localhost:8000/api/v1/health/ready` → all three checks true.