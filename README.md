# Job Autofill Copilot

An AI-powered job application autofill system. Install a browser extension, upload your resume once, build a template of your standard answers, and the system automatically detects job application forms across the web, maps every field, and generates grounded answers using your own resume and past responses — with zero hallucination enforcement.

## What this project does

- **Resume parsing** — Upload a PDF or DOCX resume. The ResumeExtractorAgent extracts structured data (name, skills, experience, education) using GPT-4o with strict source-only constraints.
- **Form understanding** — The FormUnderstandingAgent takes raw HTML from any job application page and maps every field to a canonical key (`first_name`, `cover_letter`, `why_us`, etc.) using a two-phase heuristic + LLM pipeline. Detects 18 ATS platforms including Greenhouse, Lever, Workday, and Google Forms.
- **AI answer generation** — The AnswerGenerationAgent retrieves context from your resume, templates, job description, and past answers via Qdrant RAG, then generates grounded answers. It refuses to answer if context is insufficient rather than hallucinating.
- **Application memory** — Every submitted answer is embedded and stored in Qdrant. Future applications retrieve similar past answers as context, improving over time.
- **Application history** — Every form session is tracked in PostgreSQL with status, platform, field counts, and all answers with their source and confidence score.
- **Browser extension** — Plasmo + TypeScript extension that scans the DOM, sends HTML to the backend, receives field mappings and AI answers, and injects them into the live page (built, not yet connected end-to-end with auth).

## Architecture

Browser Extension (Plasmo + TypeScript)
↓
FastAPI Backend (Python 3.12, async)
↓
┌──────────────────────────────────────────┐
│ AI Agent System (LlamaIndex + GPT-4o) │
│ - ResumeExtractorAgent │
│ - FormUnderstandingAgent │
│ - AnswerGenerationAgent │
│ - ApplicationMemoryAgent │
└──────────────────────────────────────────┘
↓
┌──────────────────────────────────────────┐
│ Data Layer │
│ PostgreSQL — relational data │
│ Qdrant — vector embeddings (RAG) │
│ Redis — cache + Celery broker │
│ MinIO — encrypted resume files │
└──────────────────────────────────────────┘

## Project structure

job-autofill-copilot/
├── .env ← secrets (never commit)
├── alembic.ini ← database migration config
├── pytest.ini
├── infrastructure/
│ └── docker/
│ ├── docker-compose.yml ← Postgres, Redis, Qdrant, MinIO
│ ├── .env.example
│ └── postgres/init/
│ └── 01_extensions.sql
├── backend/
│ ├── requirements.txt
│ ├── init.py
│ └── api/
│ ├── main.py ← FastAPI app factory + lifespan
│ ├── alembic/
│ │ ├── env.py
│ │ └── versions/
│ │ └── 001_initial_schema.py
│ ├── agents/ ← AI reasoning layer
│ │ ├── base.py ← LlamaIndex bootstrap + output schemas
│ │ ├── resume_extractor.py
│ │ ├── form_understanding.py
│ │ ├── answer_generation.py
│ │ └── application_memory.py
│ ├── core/
│ │ ├── config.py ← Pydantic Settings (all env vars)
│ │ ├── logging.py ← structlog structured logging
│ │ └── auth.py ← Clerk JWT verification
│ ├── db/
│ │ ├── session.py ← async SQLAlchemy engine
│ │ ├── redis_client.py ← async Redis pool
│ │ └── qdrant_client.py ← Qdrant client + collection setup
│ ├── middleware/
│ │ ├── request_id.py
│ │ └── error_handlers.py
│ ├── models/
│ │ ├── base.py ← DeclarativeBase + mixins
│ │ └── models.py ← all 9 ORM tables
│ ├── rag/
│ │ ├── chunker.py ← text chunking for embeddings
│ │ ├── embeddings.py ← OpenAI embedding with Redis cache
│ │ ├── ingestion.py ← write side (embed + store in Qdrant)
│ │ └── retrieval.py ← read side (semantic search)
│ ├── routers/
│ │ ├── health.py
│ │ ├── users.py
│ │ ├── resumes.py
│ │ ├── templates.py
│ │ ├── applications.py
│ │ └── agents.py ← all 5 AI agent endpoints
│ └── workers/
│ └── tasks.py ← Celery async tasks (resume parsing)
├── apps/
│ └── extension/ ← Plasmo browser extension (TypeScript)
│ ├── src/
│ │ ├── background/index.ts ← service worker orchestrator
│ │ ├── content.ts ← DOM scanner + injector trigger
│ │ ├── popup.tsx ← extension popup UI
│ │ ├── lib/
│ │ │ ├── api-client.ts
│ │ │ ├── platform-detector.ts
│ │ │ ├── dom-scanner.ts
│ │ │ ├── dom-injector.ts
│ │ │ └── storage.ts
│ │ └── types/index.ts
│ └── package.json
└── backend/tests/
├── conftest.py
└── test_all.py

## Requirements

| Tool | Version | Install |
|------|---------|---------|
| Python | 3.12+ | https://python.org |
| Docker Desktop | 4.x+ | https://docs.docker.com/get-docker/ |
| Git | any | https://git-scm.com |
| OpenAI API key | — | https://platform.openai.com |

You also need a **Clerk account** for authentication (free tier works):  
https://clerk.com — create an app, copy the Secret Key and Publishable Key.

---

## Setup — new machine

### 1. Clone

```bash
git clone <your-repo-url>
cd job-autofill-copilot
```

### 2. Create Python virtual environment

```bash
python -m venv .venv

# Mac / Linux
source .venv/bin/activate

# Windows CMD
.venv\Scripts\activate.bat

# Windows PowerShell
.venv\Scripts\Activate.ps1
```

### 3. Install Python dependencies

```bash
pip install --upgrade pip

pip install \
  fastapi==0.111.* uvicorn[standard]==0.30.* \
  sqlalchemy[asyncio]==2.0.* asyncpg==0.29.* \
  alembic==1.13.* psycopg2-binary==2.9.* \
  redis[hiredis]==5.0.* qdrant-client==1.9.* \
  openai==1.35.* tiktoken==0.7.* \
  "llama-index-core==0.10.*" \
  "llama-index-llms-openai==0.1.*" \
  "llama-index-embeddings-openai==0.1.*" \
  celery[redis]==5.4.* \
  python-jose[cryptography]==3.3.* httpx==0.27.* \
  boto3==1.34.* python-multipart==0.0.9 \
  pypdf==4.2.* python-docx==1.1.* \
  cryptography==42.* pydantic==2.7.* \
  pydantic-settings==2.3.* structlog==24.2.* \
  beautifulsoup4==4.12.* sentry-sdk[fastapi]==2.5.*
```

### 4. Create the `.env` file

```bash
cp infrastructure/docker/.env.example .env
```

Open `.env` and fill in the required values:

```bash
# REQUIRED — get from platform.openai.com/api-keys
OPENAI_API_KEY=sk-your-real-key-here

# REQUIRED — get from Clerk Dashboard → API Keys
CLERK_SECRET_KEY=sk_test_your_clerk_secret
CLERK_PUBLISHABLE_KEY=pk_test_your_clerk_publishable

# REQUIRED — generate a random 32-byte base64 key for resume encryption
# Run this command to generate one:
# python -c "import os,base64; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
RESUME_ENCRYPTION_KEY=paste-generated-key-here

# These defaults work as-is for local Docker setup
DATABASE_URL=postgresql+asyncpg://autofill_user:autofill_secret@localhost:5432/autofill_db
REDIS_URL=redis://:redis_secret@localhost:6379/0
QDRANT_HOST=localhost
QDRANT_API_KEY=qdrant_secret
APP_ENV=development
```

Everything else in `.env.example` can stay at its default for local development.

### 5. Start Docker services

```bash
cd infrastructure/docker
docker compose up -d
cd ../..
```

Verify all four services are healthy:

```bash
docker compose -f infrastructure/docker/docker-compose.yml ps
```

All should show `Up` or `Up (healthy)`. If any shows `starting`, wait 15 seconds and check again.

Quick health checks:

```bash
# Postgres
docker exec autofill_postgres pg_isready -U autofill_user -d autofill_db

# Redis
docker exec autofill_redis redis-cli -a redis_secret ping

# Qdrant
curl -s http://localhost:6333/healthz

# MinIO
curl -s http://localhost:9000/minio/health/live
```

### 6. Run database migrations

```bash
# From project root
alembic upgrade head
```

Verify all 9 tables were created:

```bash
docker exec -it autofill_postgres psql -U autofill_user -d autofill_db -c "\dt"
```

You should see: `ai_generated_answers`, `application_answers`, `applications`, `form_fields`, `job_descriptions`, `parsed_resume_data`, `resumes`, `templates`, `users`.

### 7. Start the API server

**Terminal 1 — FastAPI:**

```bash
# From project root, with venv active
uvicorn backend.api.main:app --host 0.0.0.0 --port 8000 --reload
```

Expected output:
INFO: Redis connection pool ready
INFO: Qdrant client connected
INFO: LlamaIndex Settings configured
INFO: Uvicorn running on http://0.0.0.0:8000

**Terminal 2 — Celery worker** (needed for resume parsing):

```bash
# From project root, with venv active
celery -A backend.api.workers.tasks.celery_app worker \
  --loglevel=info \
  --queues=parsing,ingestion,default \
  --concurrency=2
```

### 8. Verify the API is running

```bash
curl http://localhost:8000/api/v1/health/live
# → {"status":"alive"}

curl http://localhost:8000/api/v1/health/ready
# → {"status":"ready","checks":{"postgres":true,"redis":true,"qdrant":true},...}
```

Interactive API docs (dev mode only):  
http://localhost:8000/docs

---

## Testing with Postman

Import both files from `backend/tests/`:
- `Job_Autofill_Copilot.postman_collection.json`
- `Job_Autofill_Copilot_Local.postman_environment.json`

Select the **"Job Autofill Copilot — Local Dev"** environment.

Before running authenticated endpoints, add a test user to bypass Clerk locally. Add this block at the bottom of `backend/api/main.py` temporarily:

```python
# LOCAL TESTING ONLY — remove before production
from backend.api.core.auth import verify_clerk_token, CurrentUser
async def _mock_auth():
    return CurrentUser(
        user_id="00000000-0000-0000-0000-000000000001",
        clerk_user_id="user_test_local",
        email="test@autofill.dev",
        plan_tier="pro",
    )
app.dependency_overrides[verify_clerk_token] = _mock_auth
```

Seed the test user into Postgres:

```bash
docker exec -it autofill_postgres psql -U autofill_user -d autofill_db -c "
INSERT INTO users (id, clerk_user_id, email, display_name, plan_tier, is_active, is_deleted)
VALUES (
  '00000000-0000-0000-0000-000000000001',
  'user_test_local',
  'test@autofill.dev',
  'Test User',
  'pro', true, false
) ON CONFLICT (clerk_user_id) DO NOTHING;"
```

Run the collection folders in order: Health → Users → Resumes → Templates → Applications → Form Understanding → Answer Generation → Batch Answers → Memory → Error Handling → Cleanup.

---

## Useful commands

```bash
# Docker
docker compose -f infrastructure/docker/docker-compose.yml up -d
docker compose -f infrastructure/docker/docker-compose.yml down
docker compose -f infrastructure/docker/docker-compose.yml ps
docker compose -f infrastructure/docker/docker-compose.yml logs -f

# Database migrations
alembic upgrade head
alembic downgrade -1
alembic revision --autogenerate -m "describe your change"

# Check Qdrant collections
curl http://localhost:6333/collections -H "api-key: qdrant_secret"

# Check Redis embedding cache
docker exec autofill_redis redis-cli -a redis_secret --scan --pattern "embedding:*" | wc -l

# Full database reset
alembic downgrade base && alembic upgrade head
```

---

## What is not yet built

- Web dashboard (Next.js) — application history UI, resume upload UI, template editor
- Browser extension auth flow — Clerk sign-in inside the popup
- Extension ↔ backend end-to-end wiring with real auth tokens
- Job description auto-extraction pipeline (endpoint exists, scraper not wired)
- Email/Slack notifications on application status change
- Usage analytics and cost tracking dashboard
- Production deployment config (AWS / GCP Terraform)

---

## Environment variables reference

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENAI_API_KEY` | ✅ | Powers all AI agents and embeddings |
| `CLERK_SECRET_KEY` | ✅ | JWT verification for all API requests |
| `RESUME_ENCRYPTION_KEY` | ✅ | AES-256 key for resume file encryption |
| `DATABASE_URL` | ✅ | PostgreSQL async connection string |
| `REDIS_URL` | ✅ | Redis connection (cache + Celery broker) |
| `QDRANT_HOST` | ✅ | Qdrant vector database host |
| `OPENAI_MODEL` | ❌ | Default: `gpt-4o` |
| `OPENAI_EMBEDDING_MODEL` | ❌ | Default: `text-embedding-3-small` |
| `APP_ENV` | ❌ | `development` / `production` |
| `LOG_LEVEL` | ❌ | Default: `INFO` |
| `SENTRY_DSN` | ❌ | Leave empty to disable Sentry |

---

## Security notes

- Resume files are encrypted with AES-256-GCM before being stored in MinIO
- Auth tokens are verified against Clerk's JWKS on every request
- All Qdrant searches are scoped to the authenticated user's ID — cross-user data leakage is impossible at the query level
- Never commit `.env` files — they are in `.gitignore`
- The `RESUME_ENCRYPTION_KEY` must be rotated if compromised; the `encryption_key_ref` column in the database tracks which key version was used per file

---

## Troubleshooting

**`ModuleNotFoundError: No module named 'backend'`**  
Run uvicorn and alembic from the project root (`job-autofill-copilot/`), not from inside `backend/`.

**`uvicorn backend.api.main:app` — import error on startup**  
Run `python -c "from backend.api.main import app; print('OK')"` to see the exact error.

**Readiness check shows `postgres: false`**  
Postgres container is still starting. Wait 15 seconds and retry. Check with `docker compose ps`.

**`parse_status` stays `pending` after resume upload**  
The Celery worker is not running. Start Terminal 2 with the celery command above.

**Answer generation returns empty `answer` with `refusal_reason`**  
No context was found. This means either no resume has been parsed yet (`parse_status` must be `complete`) or no template has been created. Run the Resumes and Templates Postman folders first.

**OpenAI rate limit errors**  
The model is being called too frequently. Add `OPENAI_MODEL=gpt-4o-mini` to `.env` and restart to use the cheaper/faster model for development.