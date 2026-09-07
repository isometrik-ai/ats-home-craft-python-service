# ATS HomeKraft (Monorepo)

FastAPI-based backend for the ATS HomeKraft platform. The repo is organized as a monorepo with two services under `apps/` and shared libraries in `libs/`.

**Documentation index:** [docs/README.md](docs/README.md)

## Monorepo layout

- `docs/` — top-level index for both services ([docs/README.md](docs/README.md))
- `apps/`
  - `user_service/` — FastAPI service (port **5000**): auth, contacts, membership, passes, fees, events
    - [docs/](apps/user_service/docs/README.md) — flow guides, API notes, ADRs
    - [README](apps/user_service/README.md) — run, test, Docker
  - `work_order_service/` — Work order management API (port **5001**): assets, contracts, work orders, invoices
    - [docs/](apps/work_order_service/docs/README.md) — flow guides, ADRs
    - [README](apps/work_order_service/README.md) — run, test, Docker
- `libs/`
  - `shared_config/` — Pydantic-based settings (env, logging, DB, Supabase, Isometrik, R2)
  - `shared_db/` — asyncpg pool/connection helpers, Supabase client helpers
  - `shared_middleware/` — JWT auth middleware
  - `shared_utils/` — FastAPI app factory (rate limiting), exception handlers, logging, translations, response helpers, common queries/status codes, super-admin utilities
- Root tooling — `docker/`, `docker-compose.yml`, `ruff.toml`, `coverage.xml`, `sonar-project.properties`

## Tech stack

- Python 3.13, FastAPI, Pydantic Settings
- asyncpg + PostgreSQL, optional Supabase Auth/DB
- SlowAPI for rate limiting, JWT middleware, Datadog tracing (`ddtrace`)
- OpenAI client, Typesense, pgvector; AWS S3 / Cloudflare R2 via `boto3`
- Testing: pytest, pytest-asyncio, pytest-cov (coverage target 85%+)

## Run locally (without Docker)

1. Install Python 3.13 and create a virtual env:

```bash
python -m venv .venv
.\.venv\Scripts\activate  # Windows
# or: source .venv/bin/activate
```

2. Install dependencies (monorepo uses a single requirements file):

```bash
pip install -r requirements.txt
```

3. Provide environment variables (see “Environment” below). For local dev you can place them in a `.env` at repo root.
1. Start a service with auto-reload:

**user_service (5000):**

```bash
uvicorn apps.user_service.app.main:app --host 0.0.0.0 --port 5000 --reload
```

**work_order_service (5001):**

```bash
uvicorn apps.work_order_service.app.main:app --host 0.0.0.0 --port 5001 --reload
```

5. Health and status:

- `GET /health` — service health
- `GET /v1/status` — router availability

## Run with Docker

Build from the repo root (required so `libs/` is included in the build context):

```bash
docker build -f docker/user_service.Dockerfile -t ats-user-service .
docker build -f docker/work_order_service.Dockerfile -t ats-work-order-service .

docker run --env-file .env -p 5000:5000 ats-user-service
docker run --env-file .env -p 5001:5001 ats-work-order-service
```

Or pull and run pre-built images from Docker Hub (expects `.env` at repo root):

```bash
docker compose pull
docker compose up -d
```

## Environment

Key variables (pulled by `libs/shared_config/app_settings.py` and `apps/user_service/app/config/app_settings.py`):

- Core app: `ENVIRONMENT` (local|development|staging|production), `LOG_LEVEL` (DEBUG|INFO|...), `APP_NAME`, `APP_VERSION`, `APP_DESCRIPTION`
- Database: `DATABASE_URL` **or** `DB_HOST`, `DB_PORT`, `DB_DATABASE`, `DB_USER`, `DB_PASSWORD`, `DB_SSL_MODE`, `DB_SSL_ROOT_CERT`, `DB_MIN_POOL`, `DB_MAX_POOL`, `DB_COMMAND_TIMEOUT`, `DB_STATEMENT_TIMEOUT_MS`
- Supabase: `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_KEY`, `SUPABASE_JWT_SECRET`
- Auth/JWT: `JWT_SECRET` (for middleware), optional 2FA flags `EMAIL_OTP_ENABLED`, `PHONE_OTP_ENABLED`, defaults in `EMAIL_DEFAULT_OTP`, `PHONE_DEFAULT_OTP`
- Storage: `R2_ACCOUNT_ID`, `R2_ACCESS_KEY`, `R2_SECRET_KEY`, `R2_BUCKET_NAME`
- Isometrik: `ISOMETRIK_ENABLED`, `ISOMETRIK_ADMIN_API_URL`, `ISOMETRIK_API_URL`, `ISOMETRIK_CLIENT_NAME`, `ISOMETRIK_REGION_ID`, `ISOMETRIK_AUTH_TOKEN`
- Misc: `WEBSITE_URL`, `COMPANY_*` metadata, `OPENAI_API_KEY`, Typesense/pgvector settings as needed by services

## Testing

```bash
# user_service
pytest apps/user_service/tests -q

# work_order_service
pytest apps/work_order_service/tests -q
```

Example with coverage (user_service):
pytest apps/user_service/tests -q \
--cov=apps.user_service \
--cov-report=term-missing --cov-report=html --cov-fail-under=85

```

Coverage HTML is written to `htmlcov/index.html`.

## Observability & middleware

- Datadog tracing via `ddtrace` middleware
- JWT validation via `libs/shared_middleware.jwt_auth.JWTAuthMiddleware`
- Rate limiting via `SlowAPI` (see `libs/shared_utils.fastapi_app.create_fastapi_app`)
- Structured logging (`libs/shared_utils.logger`) with request IDs; JSON in production
- Unified exception handling and translation support via `libs/shared_utils`

## API surface (high level)

All routes are under `/v1`. Domain routers include:

- `auth` (login, signup, refresh, password flows, validate, delete)
- `users` (list/profile/update, email update, ban/unban)
- `organization`, `roles`, `permissions`, `sessions`, `teams`
- `invites`, `verification_codes`, `audit_logs`, `presigned_url`

## Contributing

- Format/lint with Ruff (`ruff.toml`)
- Keep tests passing and coverage ≥ 85% for touched areas
- Favor shared code in `libs/` to keep services thin and consistent
```
