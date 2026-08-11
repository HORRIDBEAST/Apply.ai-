# Apply.ai-

Apply.ai- is a local-first job application assistant for automating resume-based job applications. The project combines a FastAPI backend, PostgreSQL, Redis, Qdrant, MinIO, and Docker Compose to support resume parsing, AI-assisted form filling, and application tracking.

## What this project does

This repository is intended to help you:
- upload and store resumes
- parse resume content
- generate or suggest answers for job application forms
- track application progress
- run the full stack locally with Docker

## Project structure

- apps/: frontend or client application (to be expanded)
- backend/: FastAPI backend and database models
- infrastructure/: Docker and environment configuration

## Requirements

Before starting, make sure you have installed:
- Python 3.13
- Docker Desktop
- Docker Compose
- Git

## Clone and setup

```bash
git clone <your-repo-url>
cd Apply.ai-
```

### 1. Create the Python environment

```bash
cd backend
python -m venv venv
.
venv\Scripts\activate
pip install -r requirements.txt
```

If you are using PowerShell on Windows, use:

```powershell
cd backend
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. Start the infrastructure services

```bash
cd ..\infrastructure\docker
docker compose up -d
```

This starts:
- PostgreSQL
- Redis
- Qdrant
- MinIO
- pgAdmin
- Redis Commander

### 3. Configure environment variables

Copy the example environment file if needed:

```bash
copy .env.example .env
```

Then review the values in the file and adjust them as needed.

### 4. Run database migrations

```bash
cd ..\..\backend\api
alembic upgrade head
```

## Run the backend

```bash
cd backend\api
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

If the app entrypoint is different in your setup, use the appropriate module path for your project.

## Useful commands

### Docker

```bash
docker compose up -d
docker compose down
docker compose ps
docker compose logs -f
```

### Database migrations

```bash
alembic revision --autogenerate -m "describe change"
alembic upgrade head
alembic downgrade -1
```

## Development notes

- Keep local secrets in .env files and do not commit them.
- Use Docker for the shared services so the environment is consistent across laptops.
- If you change the database port or credentials, update both Docker Compose and your local Alembic configuration.
- This project is still under active development, so expect changes to structure and setup steps.

## GitHub usage

When pushing to GitHub:
- commit only source files and configuration
- do not commit local environment files or database data
- use the repository README as the main onboarding guide for future contributors

## Troubleshooting

If the backend cannot connect to Postgres:
- confirm Docker containers are running
- verify the PostgreSQL port in Docker Compose
- check that the Alembic connection string matches the running container

If dependencies fail to install:
- verify Python version
- recreate the virtual environment
- reinstall requirements from the backend folder
