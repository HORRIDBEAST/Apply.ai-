<div align="center">

# 🪄 Job Autofill Copilot

### AI-powered job application autofill — zero hallucination, fully grounded.

Upload your résumé once, write your standard answers, and let the system detect job forms across the web, map every field, and generate answers grounded in *your own data* — refusing to invent facts it can't source.

[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-async-009688?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?style=flat-square&logo=postgresql&logoColor=white)](https://postgresql.org)
[![Qdrant](https://img.shields.io/badge/Qdrant-vector%20RAG-DC244C?style=flat-square)](https://qdrant.tech)
[![Redis](https://img.shields.io/badge/Redis-cache%20%2B%20broker-DC382D?style=flat-square&logo=redis&logoColor=white)](https://redis.io)
[![OpenAI](https://img.shields.io/badge/OpenAI-GPT--4o-412991?style=flat-square&logo=openai&logoColor=white)](https://platform.openai.com)
[![Plasmo](https://img.shields.io/badge/Plasmo-extension-7C3AED?style=flat-square)](https://plasmo.com)
[![Backend](https://img.shields.io/badge/backend-tested%20%26%20working-22c55e?style=flat-square)]()
[![Extension](https://img.shields.io/badge/extension-in%20progress-f59e0b?style=flat-square)]()

</div>

---

## ✨ What this project does

| | |
|---|---|
| 📄 **Resume parsing** | Upload a PDF or DOCX résumé. `ResumeExtractorAgent` extracts structured data (name, skills, experience, education, projects, coding profiles) using GPT-4o-mini with strict source-only constraints — plus real PDF hyperlink recovery for GitHub/LinkedIn/portfolio links. |
| 🧩 **Form understanding** | `FormUnderstandingAgent` takes raw HTML from any job application page and maps every field to a canonical key (`first_name`, `cover_letter`, `why_us`, etc.) via a two-phase heuristic + LLM pipeline. Detects 18 ATS platforms — Greenhouse, Lever, Workday, Google Forms, and more. |
| 🧠 **AI answer generation** | `AnswerGenerationAgent` retrieves context from your résumé, templates, job description, and past answers via Qdrant RAG, then generates grounded answers — refusing to answer rather than hallucinating when context is insufficient. |
| 🔁 **Application memory** | Every submitted answer is embedded and stored in Qdrant. Future applications retrieve similar past answers as context, improving over time. |
| 📊 **Application history** | Every form session is tracked in PostgreSQL with status, platform, field counts, and all answers with their source and confidence score. |
| 🧷 **Browser extension** | Plasmo + TypeScript extension that scans the DOM, sends HTML to the backend, receives field mappings and AI answers, and injects them into the live page *(built, not yet connected end-to-end with auth)*. |

## 🏗️ Architecture

```mermaid
flowchart TD
    EXT["🧩 Browser Extension<br/><sub>Plasmo + TypeScript</sub>"]

    subgraph API["⚙️ FastAPI Backend — Python 3.12, async"]
        ROUTERS["Routers<br/><sub>users · resumes · templates · applications · agents</sub>"]
    end

    subgraph AGENTS["🧠 AI Agent System — LlamaIndex + GPT-4o"]
        direction LR
        A1["ResumeExtractor<br/>Agent"]
        A2["FormUnderstanding<br/>Agent"]
        A3["AnswerGeneration<br/>Agent"]
        A4["ApplicationMemory<br/>Agent"]
    end

    subgraph DATA["🗄️ Data Layer"]
        direction LR
        PG[("PostgreSQL<br/><sub>relational data</sub>")]
        QD[("Qdrant<br/><sub>vector embeddings — RAG</sub>")]
        RD[("Redis<br/><sub>cache + Celery broker</sub>")]
        MN[("MinIO<br/><sub>encrypted résumé files</sub>")]
    end

    EXT -->|HTTPS + Clerk JWT| ROUTERS
    ROUTERS --> AGENTS
    AGENTS --> PG
    AGENTS --> QD
    ROUTERS --> RD
    ROUTERS --> MN

    classDef client fill:#e0f2fe,stroke:#0284c7,stroke-width:2px,color:#0c4a6e
    classDef api fill:#fef9c3,stroke:#ca8a04,stroke-width:2px,color:#713f12
    classDef agent fill:#ede9fe,stroke:#7c3aed,stroke-width:2px,color:#4c1d95
    classDef data fill:#dcfce7,stroke:#16a34a,stroke-width:2px,color:#14532d

    class EXT client
    class ROUTERS api
    class A1,A2,A3,A4 agent
    class PG,QD,RD,MN data

    style API fill:#fffbea,stroke:#eab308,stroke-width:1.5px
    style AGENTS fill:#f5f3ff,stroke:#8b5cf6,stroke-width:1.5px
    style DATA fill:#f0fdf4,stroke:#22c55e,stroke-width:1.5px
```

## 🌳 Project structure

> 💡 Paths below reflect what's **actually on disk** (confirmed during backend
> testing) — a few differ from older docs/scaffolding comments: `alembic.ini`
> lives under `backend/api/`, not the project root; models live in `db/`, not
> a separate `models/` package; the resumes router file is `resume.py`
> (singular).

```text
job-autofill-copilot/
├── 🔒 .env                           # secrets — never commit (gitignored)
├── pytest.ini
├── Job_Autofill_Copilot.postman_collection.json
├── Job_Autofill_Copilot_Local.postman_environment.json
│
├── 🐳 infrastructure/
│   └── docker/
│       ├── docker-compose.yml       # Postgres · Redis · Qdrant · MinIO
│       ├── .env.example
│       └── postgres/init/01_extensions.sql
│
├── ⚙️ backend/
│   ├── requirements.txt
│   ├── venv/                        # (or project-root .venv — either works)
│   ├── tests/
│   │   ├── conftest.py
│   │   └── test_all.py
│   └── api/
│       ├── main.py                  # FastAPI app factory + lifespan
│       ├── alembic.ini              # ⚠️ lives here, not project root
│       ├── alembic/
│       │   ├── env.py
│       │   └── versions/001_initial_schema.py
│       │
│       ├── 🧠 agents/               # AI reasoning layer
│       │   ├── base.py              # LlamaIndex bootstrap + output schemas
│       │   ├── resume_extractor.py
│       │   ├── form_understanding.py
│       │   ├── answer_generation.py
│       │   └── application_memory.py
│       │
│       ├── core/
│       │   ├── config.py            # Pydantic Settings — all env vars
│       │   ├── logging.py           # structlog structured logging
│       │   └── auth.py              # Clerk JWT verification
│       │
│       ├── 🗄️ db/
│       │   ├── base.py              # DeclarativeBase + mixins
│       │   ├── models.py            # all 9 ORM tables
│       │   ├── session.py           # async SQLAlchemy engine
│       │   ├── redis_client.py
│       │   └── qdrant_client.py     # client + collection setup
│       │
│       ├── middleware/
│       │   ├── request_id.py
│       │   └── error_handlers.py
│       │
│       ├── 🔍 rag/
│       │   ├── chunker.py           # text → embeddable chunks
│       │   ├── embeddings.py        # OpenAI embeddings + Redis cache
│       │   ├── ingestion.py         # write side — embed + store in Qdrant
│       │   └── retrieval.py         # read side — semantic search
│       │
│       ├── routers/
│       │   ├── health.py
│       │   ├── users.py
│       │   ├── resume.py            # (singular — not resumes.py)
│       │   ├── templates.py
│       │   ├── applications.py
│       │   └── agents.py            # all 5 AI agent endpoints
│       │
│       └── workers/
│           └── tasks.py             # Celery async tasks (resume parsing)
│
└── 🧩 apps/
    └── extension/                   # Plasmo browser extension (TypeScript)
        ├── package.json
        └── src/
            ├── background/index.ts  # service worker orchestrator
            ├── content.ts           # DOM scanner + injector trigger
            ├── popup.tsx            # extension popup UI
            ├── lib/
            │   ├── api-client.ts
            │   ├── platform-detector.ts
            │   ├── dom-scanner.ts
            │   ├── dom-injector.ts
            │   └── storage.ts
            └── types/index.ts
```

## 📋 Requirements

| Tool | Version | Install |
|------|---------|---------|
| Python | 3.12+ | https://python.org |
| Docker Desktop | 4.x+ | https://docs.docker.com/get-docker/ |
| Git | any | https://git-scm.com |
| OpenAI API key | — | https://platform.openai.com |

You also need a **Clerk account** for authentication (free tier works):  
https://clerk.com — create an app, copy the Secret Key and Publishable Key.

---

## 🚀 Setup — new machine

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

`alembic.ini` lives at `backend/api/alembic.ini`, and its `env.py` uses
relative imports (`db.base`, `db.models`) that only resolve when your
working directory is `backend/api`. Run migrations from there, not the
project root:

```bash
cd backend/api
alembic upgrade head
cd ../..
```

Also note: `docker-compose.yml` maps Postgres to **host port 5433** (not
5432), so `DATABASE_URL` in your root `.env` must use `:5433`.

Verify all 9 tables were created:

```bash
docker exec -it autofill_postgres psql -U autofill_user -d autofill_db -c "\dt"
```

You should see: `ai_generated_answers`, `application_answers`, `applications`, `form_fields`, `job_descriptions`, `parsed_resume_data`, `resumes`, `templates`, `users`.

### 7. Start the API server

**Terminal 1 — FastAPI:**

Every module in this codebase uses absolute imports rooted at the project
directory (`from backend.api.core.config import settings`, etc.), so `backend`
must be importable as a top-level package. That only works if your **working
directory is the project root** — not `backend/`, even if your venv physically
lives inside `backend/venv`. Activating a venv from a different folder than
the one you `cd`'d into is fine; what matters is where you stand when you run
`uvicorn`:

```bash
# From project root (even if venv is backend/venv, activate it with a
# relative/absolute path from here — do NOT cd into backend/ first)
uvicorn backend.api.main:app --host 0.0.0.0 --port 8000 --reload

docker compose -f infrastructure/docker/docker-compose.yml ps

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
  --concurrency=2 \
  --pool=solo
```

`--pool=solo` is required on Windows — Celery's default `prefork` pool relies
on `os.fork()`, which doesn't exist on Windows and crashes the worker on
startup. `solo` runs tasks in the same process, one at a time; fine for local
dev. Omit it on Mac/Linux, where prefork works normally.

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

## 🧪 Testing with Postman

Import both files from the **project root** (not `backend/tests/` — that's
where this doc originally pointed, but they actually live at the repo root):
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

## 🛠️ Useful commands

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

## 🚧 What is not yet built

- Web dashboard (Next.js) — application history UI, resume upload UI, template editor
- Browser extension auth flow — Clerk sign-in inside the popup
- Extension ↔ backend end-to-end wiring with real auth tokens
- Job description auto-extraction pipeline (endpoint exists, scraper not wired)
- Email/Slack notifications on application status change
- Usage analytics and cost tracking dashboard
- Production deployment config (AWS / GCP Terraform)

---

## 🔑 Environment variables reference

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

## 🛡️ Security notes

- Resume files are encrypted with AES-256-GCM before being stored in MinIO
- Auth tokens are verified against Clerk's JWKS on every request
- All Qdrant searches are scoped to the authenticated user's ID — cross-user data leakage is impossible at the query level
- Never commit `.env` files — they are in `.gitignore`
- The `RESUME_ENCRYPTION_KEY` must be rotated if compromised; the `encryption_key_ref` column in the database tracks which key version was used per file

---

## 🧯 Troubleshooting

### Bugs found and fixed while first booting this backend (2026-09-30)

The backend as originally committed could not start at all — the app and
`pytest` both crashed on import. These are already fixed in the code; this
log exists so that if you ever see the same traceback again (e.g. after
reverting a commit, or copying an older version of a file), you know exactly
what's wrong and why.

1. **`ModuleNotFoundError: No module named 'backend'`**
   Cause: `uvicorn`/`alembic` run from the wrong working directory. Every
   file in `backend/api/` imports things like
   `from backend.api.core.config import settings` — an absolute import that
   only resolves when the **project root** is on `sys.path`, which only
   happens when you run the command *from the project root*. Running from
   inside `backend/` (even with `api.main:app` instead of `backend.api.main:app`)
   fails the same way, because dozens of files internally still say
   `backend.api.xxx`. Fix: always run `uvicorn`/`pytest` from the project
   root; only `alembic` is the exception (see below).

2. **`ModuleNotFoundError: No module named 'sentry_sdk'` (or any other package)**
   Cause: not a code bug — the active venv in that terminal simply didn't
   have `backend/requirements.txt` installed. This repo had *two* unrelated
   venvs floating around (`backend/venv` and a global `myproject_env`); the
   fix is just making sure the venv you activate is the one you installed
   into. `where python` after activating tells you which one is live.

3. **`from .resumes import router` → `ModuleNotFoundError`**
   `backend/api/routers/__init__.py` imported a module named `resumes`
   (plural) that doesn't exist — the real file is `resume.py` (singular).
   Fixed the import. Same stray reference also existed in
   `backend/api/workers/tasks.py` (`from backend.api.routers.resumes import _get_s3_client`).

4. **`ModuleNotFoundError: No module named 'backend.api.models'`**
   Six files (`core/auth.py`, `routers/applications.py`, `routers/resume.py`,
   `routers/templates.py`, `routers/users.py`, `rag/ingestion.py`,
   `workers/tasks.py`) and `backend/tests/conftest.py` imported ORM classes
   from `backend.api.models.models` / `backend.api.models.base`. That
   package never existed — the real files are `backend/api/db/models.py`
   and `backend/api/db/base.py`. Fixed all 8 import sites.

5. **`KeyError: 'DATABASE_URL'` on startup**
   `backend/api/db/session.py` read `os.environ["DATABASE_URL"]` directly,
   bypassing the `Settings` class entirely — but pydantic-settings loads
   `.env` into the `Settings` object, not into the real process environment,
   so this line raised even with a correctly filled-in `.env`. This also
   violated this project's own documented convention ("all environment
   variables go through `core/config.py`, never read `os.environ` directly").
   Fixed to use `settings.DATABASE_URL` / `settings.APP_ENV`.

6. **`AssertionError: Cannot specify Depends in Annotated and default value together`**
   `routers/resume.py`'s `upload_resume` had
   `current_user: AuthenticatedUser = Depends()`. `AuthenticatedUser` is
   already `Annotated[CurrentUser, Depends(verify_clerk_token)]`, so adding
   a second `Depends()` as the parameter default is redundant — newer
   FastAPI versions reject this outright instead of silently accepting it.
   Fixed by dropping the `= Depends()` and reordering the parameter (a
   parameter with no default can't follow ones that have defaults in Python).

7. **`httpx.ConnectError: [SSL: WRONG_VERSION_NUMBER]` connecting to Qdrant**
   `backend/api/db/qdrant_client.py` instantiated `AsyncQdrantClient` with
   an `api_key` but no explicit `https` flag. The `qdrant-client` library
   assumes an API key means Qdrant Cloud and silently defaults to HTTPS,
   which fails a TLS handshake against the local Docker Qdrant container
   (which only serves plain HTTP). Fixed by passing `https=False` explicitly.

8. **`KeyError: 'Cosine'` creating Qdrant collections**
   `qmodels.Distance[settings.QDRANT_DISTANCE]` looks up an enum member by
   **name** (`COSINE`), but `settings.QDRANT_DISTANCE` holds the enum's
   **value** (`"Cosine"`). Fixed by switching to `qmodels.Distance(...)`
   (value-based lookup) instead of `Distance[...]` (name-based lookup).

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