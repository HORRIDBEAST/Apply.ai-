# Job Autofill Copilot — Complete Testing & Running Guide

## OVERVIEW

This guide walks you through spinning up every service, running the automated
test suite, and manually testing all API endpoints with Postman — in that order.

```
TESTING ORDER:
  Step 1  →  Start infrastructure (Docker)
  Step 2  →  Run database migrations
  Step 3  →  Start FastAPI + Celery
  Step 4  →  Run pytest (automated)
  Step 5  →  Manual Postman testing
```

---

## PREREQUISITES

Install these before anything else:

| Tool | Version | Install |
|------|---------|---------|
| Docker Desktop | 4.x+ | https://docs.docker.com/get-docker/ |
| Python | 3.12+ | https://python.org |
| Node.js | 20+ | https://nodejs.org (for extension later) |
| Postman | Latest | https://postman.com/downloads |

---

## STEP 1 — BUILD THE PROJECT FOLDER STRUCTURE

Merge all generated files into this layout:

```
job-autofill-copilot/           ← project root
├── alembic.ini
├── pytest.ini
├── docker-compose.dev.yml
├── infrastructure/
│   └── docker/
│       ├── docker-compose.yml       ← base infra
│       ├── .env.example
│       ├── postgres/init/01_extensions.sql
│       └── qdrant/config.yaml
└── backend/
    ├── Dockerfile
    ├── requirements.txt
    ├── __init__.py
    ├── tests/
    │   ├── conftest.py
    │   └── test_all.py
    └── api/
        ├── __init__.py
        ├── main.py
        ├── alembic/
        │   ├── env.py
        │   └── versions/001_initial_schema.py
        ├── agents/
        │   ├── __init__.py
        │   ├── base.py
        │   ├── resume_extractor.py
        │   ├── form_understanding.py
        │   ├── answer_generation.py
        │   └── application_memory.py
        ├── core/
        │   ├── config.py
        │   ├── logging.py
        │   └── auth.py
        ├── db/
        │   ├── session.py
        │   ├── redis_client.py
        │   └── qdrant_client.py
        ├── middleware/
        │   ├── request_id.py
        │   └── error_handlers.py
        ├── models/
        │   ├── base.py
        │   └── models.py
        ├── rag/
        │   ├── chunker.py
        │   ├── embeddings.py
        │   ├── ingestion.py
        │   └── retrieval.py
        ├── routers/
        │   ├── __init__.py
        │   ├── health.py
        │   ├── users.py
        │   ├── resumes.py
        │   ├── templates.py
        │   ├── applications.py
        │   └── agents.py
        └── workers/
            └── tasks.py
```

---

## STEP 2 — ENVIRONMENT SETUP

```bash
# 1. Create and activate virtualenv
cd job-autofill-copilot
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# 2. Install Python dependencies
pip install -r backend/requirements.txt

# Additional test deps
pip install pytest pytest-asyncio httpx aiosqlite factory-boy

# 3. Set up .env file
cp infrastructure/docker/.env.example infrastructure/docker/.env
```

Open `infrastructure/docker/.env` and fill in:

```bash
# REQUIRED — get from platform.openai.com
OPENAI_API_KEY=sk-your-real-key-here

# REQUIRED — get from Clerk Dashboard → API Keys
CLERK_SECRET_KEY=sk_test_your_clerk_key

# REQUIRED — generate a 32-byte base64 key:
#   python -c "import os,base64; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
RESUME_ENCRYPTION_KEY=your_generated_key_here

# These can stay as defaults for local dev:
POSTGRES_PASSWORD=autofill_secret
REDIS_PASSWORD=redis_secret
QDRANT_API_KEY=qdrant_secret
MINIO_ROOT_PASSWORD=minio_secret_key
```

Also create a `.env` in the project root (for alembic + pytest):

```bash
# job-autofill-copilot/.env
DATABASE_URL=postgresql+asyncpg://autofill_user:autofill_secret@localhost:5432/autofill_db
REDIS_URL=redis://:redis_secret@localhost:6379/0
QDRANT_HOST=localhost
QDRANT_PORT=6333
QDRANT_API_KEY=qdrant_secret
S3_ENDPOINT_URL=http://localhost:9000
S3_ACCESS_KEY_ID=autofill_minio
S3_SECRET_ACCESS_KEY=minio_secret_key
OPENAI_API_KEY=sk-your-real-key-here
CLERK_SECRET_KEY=sk_test_your_clerk_key
RESUME_ENCRYPTION_KEY=your_generated_key_here
APP_ENV=development
```

---

## STEP 3 — START INFRASTRUCTURE (Docker)

```bash
# From project root — start Postgres, Redis, Qdrant, MinIO
cd infrastructure/docker
docker compose up -d

# Verify all containers are healthy
docker compose ps
```

Expected output — all should show `healthy`:
```
NAME                    STATUS              PORTS
autofill_postgres       Up (healthy)        0.0.0.0:5432->5432/tcp
autofill_redis          Up (healthy)        0.0.0.0:6379->6379/tcp
autofill_qdrant         Up (healthy)        0.0.0.0:6333->6333/tcp
autofill_minio          Up (healthy)        0.0.0.0:9000->9000/tcp
```

Verify each service directly:

```bash
# PostgreSQL
psql postgresql://autofill_user:autofill_secret@localhost:5432/autofill_db -c "SELECT version();"

# Redis
redis-cli -a redis_secret ping
# Expected: PONG

# Qdrant
curl http://localhost:6333/healthz
# Expected: {"title":"qdrant - vector search engine"}

# MinIO
curl http://localhost:9000/minio/health/live
# Expected: 200 OK
```

---

## STEP 4 — RUN DATABASE MIGRATIONS

```bash
# From project root
cd job-autofill-copilot

# Run all migrations (creates all 9 tables + enums)
alembic upgrade head

# Verify tables were created
psql postgresql://autofill_user:autofill_secret@localhost:5432/autofill_db \
  -c "\dt" | grep -E "users|resumes|templates|applications|form_fields"
```

Expected: all 9 tables listed (`users`, `resumes`, `parsed_resume_data`,
`templates`, `job_descriptions`, `applications`, `form_fields`,
`application_answers`, `ai_generated_answers`).

---

## STEP 5 — START THE FastAPI SERVER

```bash
# Terminal 1 — FastAPI with hot-reload
cd job-autofill-copilot
uvicorn backend.api.main:app \
  --host 0.0.0.0 \
  --port 8000 \
  --reload \
  --log-level info

# Expected startup output:
# INFO: Initialising Redis connection pool
# INFO: Redis connection pool ready
# INFO: Connecting to Qdrant
# INFO: Qdrant client connected
# INFO: LlamaIndex Settings configured
# INFO: FastAPI app created
# INFO: Uvicorn running on http://0.0.0.0:8000
```

```bash
# Terminal 2 — Celery worker for resume parsing
cd job-autofill-copilot
celery -A backend.api.workers.tasks.celery_app worker \
  --loglevel=info \
  --queues=parsing,ingestion,default \
  --concurrency=2
```

Verify the API is running:
```bash
curl http://localhost:8000/api/v1/health/live
# Expected: {"status":"alive"}

curl http://localhost:8000/api/v1/health/ready
# Expected: {"status":"ready","checks":{"postgres":true,"redis":true,"qdrant":true},...}
```

View interactive API docs (development mode only):
```
http://localhost:8000/docs
```

---

## STEP 6 — RUN AUTOMATED PYTEST SUITE

```bash
cd job-autofill-copilot

# Run all unit tests (no external services needed)
pytest backend/tests/ -v --tb=short

# Run with coverage report
pytest backend/tests/ -v --cov=backend/api --cov-report=term-missing

# Run only chunker tests (fastest, pure Python)
pytest backend/tests/test_all.py -v -k "chunker"

# Run only agent unit tests
pytest backend/tests/test_all.py -v -k "answer_generation or form_understanding"

# Run API endpoint tests
pytest backend/tests/test_all.py -v -k "api_agents or health"
```

Expected passing tests:

```
PASSED  test_liveness
PASSED  test_readiness_structure
PASSED  test_clerk_webhook_sync
PASSED  test_chunk_text_basic
PASSED  test_chunk_text_empty
PASSED  test_chunk_text_whitespace_only
PASSED  test_chunk_id_deterministic
PASSED  test_chunk_id_different_users
PASSED  test_chunk_parsed_resume_sections
PASSED  test_chunk_parsed_resume_payload
PASSED  test_chunk_template_qa
PASSED  test_chunk_past_answer
PASSED  test_chunk_past_answer_empty
PASSED  test_heuristic_map_first_name
PASSED  test_heuristic_map_email
PASSED  test_heuristic_map_cover_letter_is_ai
PASSED  test_heuristic_map_why_us_is_ai
PASSED  test_detect_platform_greenhouse
PASSED  test_detect_platform_workday
PASSED  test_detect_platform_google_forms
PASSED  test_detect_platform_unknown
PASSED  test_extract_field_manifest
PASSED  test_extract_field_manifest_detects_required
PASSED  test_extract_field_manifest_empty_html
PASSED  test_form_understanding_agent_heuristic_only
PASSED  test_format_context_section_all_sources
PASSED  test_format_context_section_empty
PASSED  test_build_user_prompt
PASSED  test_compute_confidence_with_context
PASSED  test_compute_confidence_refusal
PASSED  test_detect_hallucination_clean_answer
PASSED  test_detect_hallucination_invented_percentage
PASSED  test_detect_hallucination_invented_year
PASSED  test_generate_answer_success
PASSED  test_generate_answer_refusal
PASSED  test_generate_answer_empty_context
PASSED  test_generate_answer_invalid_json
PASSED  test_form_understand_endpoint
PASSED  test_generate_answer_endpoint
PASSED  test_memory_search_endpoint
PASSED  test_memory_record_endpoint
PASSED  test_batch_generate_too_many_questions
PASSED  test_error_handler_404
PASSED  test_error_handler_422_malformed_uuid
```

---

## STEP 7 — POSTMAN MANUAL TESTING

### Import the collection

1. Open Postman
2. Click **Import** → drag in both files:
   - `testing/Job_Autofill_Copilot.postman_collection.json`
   - `testing/Job_Autofill_Copilot_Local.postman_environment.json`
3. Select **"Job Autofill Copilot — Local Dev"** environment from the dropdown

### Get a Clerk JWT token

You need a real Clerk token to test authenticated endpoints.

**Option A — Use Clerk's test token (easiest):**
```
Clerk Dashboard → Your App → Users → Create test user
→ Copy the user's session JWT from the Sessions tab
```

**Option B — Programmatic (for CI):**
```bash
# Install Clerk CLI
npm install -g @clerk/cli

# Generate a test token
clerk sessions create --user-id user_your_id
```

Paste the token into the environment variable `clerk_token`.

**Option C — Skip auth for local testing:**
```
In FastAPI main.py, temporarily override auth:
    app.dependency_overrides[verify_clerk_token] = lambda: CurrentUser(
        user_id="your-test-uuid",
        clerk_user_id="user_test",
        email="test@test.com",
        plan_tier="pro",
    )
```

### Run requests in order

Run each folder top-to-bottom. The test scripts auto-capture IDs:

```
01 Health Checks
  → Liveness probe              [no auth needed]
  → Readiness probe             [no auth needed]

02 Users
  → Sync user (Clerk webhook)   [creates your test user in DB]
  → Get my profile              [verifies auth works]
  → Update profile

03 Resumes
  → Upload resume               ⚠️ SELECT A REAL PDF FILE
                                  → auto-sets {{resume_id}}
  → List all resumes
  → Get resume detail
  → Set as primary

04 Templates
  → Create template             → auto-sets {{template_id}}
  → List all templates
  → Get template by ID
  → Update template
  → Set as default

05 Applications
  → Create application session  → auto-sets {{application_id}}
  → List applications
  → Get application detail
  → Bulk save answers
  → Update status → submitted

06 Form Understanding
  → Greenhouse HTML             ← verify platform=greenhouse,
                                   cover_letter.requires_ai=true
  → Workday HTML
  → Google Forms HTML

07 Answer Generation
  → Why do you want to work here?
  → Cover Letter
  → Tell us about yourself
  → Describe a technical challenge
  → HALLUCINATION TEST          ← verify refusal_reason != null

08 Batch Generation
  → 4 questions at once         ← verify all 4 answered

09 Memory
  → Record answer in memory
  → Search memory (high similarity expected)
  → Search memory (low similarity expected)

10 Error Handling
  → Missing auth → 401/403
  → Non-existent ID → 404
  → Malformed UUID → 422
  → Over-limit batch → 422
  → Wrong file type → 415

11 Cleanup
  → Delete resume
  → Delete template
```

### Run the full collection automatically

```
Postman → Collection → Run Collection
→ Select "Job Autofill Copilot — Full E2E Test Suite"
→ Environment: "Job Autofill Copilot — Local Dev"
→ Iterations: 1
→ Delay: 300ms (gives the API breathing room)
→ Click "Run"
```

---

## STEP 8 — VERIFY KEY BEHAVIOURS

### ✅ Zero-hallucination check

After running `07.5 HALLUCINATION TEST`:
- `confidence_score` should be `0.0`
- `refusal_reason` should be non-null
- `answer` should be empty string

After running `07.1 Why do you want to work here?` with a real resume + template:
- `hallucination_flagged` must be `false`
- `confidence_score` should be `> 0.5`
- `context_sources` should list resume/template chunks

### ✅ RAG pipeline check

After running `03.1 Upload resume` (wait ~30s for Celery parse):
```bash
# Check parse status
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/v1/resumes/<resume_id>

# parse_status should be "complete"
# parsed_data should have name, skills, experience populated
```

Check Qdrant has stored the vectors:
```bash
curl -X POST http://localhost:6333/collections/resume_chunks/points/scroll \
  -H "api-key: qdrant_secret" \
  -H "Content-Type: application/json" \
  -d '{"limit": 5, "with_payload": true}'
```

### ✅ Form understanding check

In the Greenhouse HTML response, verify:
```json
{
  "fields": [
    {"mapped_field": "first_name",   "requires_ai": false, "mapping_confidence": > 0.85},
    {"mapped_field": "last_name",    "requires_ai": false},
    {"mapped_field": "email",        "requires_ai": false},
    {"mapped_field": "cover_letter", "requires_ai": true},
    {"mapped_field": "why_us",       "requires_ai": true}
  ],
  "platform_detected": "greenhouse",
  "ai_required_count": 2
}
```

---

## USEFUL COMMANDS

```bash
# Watch API logs in real time
docker compose -f infrastructure/docker/docker-compose.yml logs -f

# Reset the database (nuclear option)
alembic downgrade base && alembic upgrade head

# Check Qdrant collections
curl http://localhost:6333/collections -H "api-key: qdrant_secret"

# Check Redis cache
redis-cli -a redis_secret keys "embedding:*" | wc -l

# Check MinIO buckets
curl -u autofill_minio:minio_secret_key http://localhost:9000/resumes

# Stop everything
docker compose -f infrastructure/docker/docker-compose.yml down

# Stop + delete all data volumes (full reset)
docker compose -f infrastructure/docker/docker-compose.yml down -v
```

---

## TROUBLESHOOTING

| Symptom | Cause | Fix |
|---------|-------|-----|
| `RESUME_ENCRYPTION_KEY` error on startup | Key not in `.env` | Generate: `python -c "import os,base64; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"` |
| Readiness probe shows `postgres: false` | Postgres not ready | Wait 10s, retry. Check `docker compose ps` |
| `parse_status` stuck at `pending` | Celery worker not running | Start Terminal 2 command above |
| Answer generation returns empty | No resume/template uploaded yet | Complete steps 3-4 in Postman first |
| 401 on all endpoints | Invalid Clerk token | Refresh token from Clerk dashboard |
| Qdrant `connection refused` | Qdrant not started | `docker compose up qdrant -d` |
| `ImportError: No module named 'backend'` | Wrong working directory | Must run pytest/uvicorn from project root |
