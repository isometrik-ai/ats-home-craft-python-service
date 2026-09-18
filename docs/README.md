# Python Service — Documentation

> **Repo:** `ats-home-craft-python-service`
> **Layout:** Monorepo with shared `libs/` and the FastAPI user service under `apps/user_service/`

This folder is the **top-level documentation index**. Detailed flow guides, API notes, and ADRs live inside [apps/user_service/docs/](../apps/user_service/docs/README.md).

______________________________________________________________________

## Service

| Service          | Port | Purpose                                                                    | Documentation                                                  |
| ---------------- | ---- | -------------------------------------------------------------------------- | -------------------------------------------------------------- |
| **user_service** | 5000 | CRM & resident platform — auth, contacts, membership, passes, fees, events | [apps/user_service/docs/](../apps/user_service/docs/README.md) |

Service README (run/test/Docker quick start): [apps/user_service/README.md](../apps/user_service/README.md)

______________________________________________________________________

## Start here

| If you are working on…                        | Read                                                                                                                                                                                 |
| --------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Resident onboarding, membership, passes, fees | [user_service docs → contact-onboarding-flow](../apps/user_service/docs/contact-onboarding-flow.md), [membership-architecture](../apps/user_service/docs/membership-architecture.md) |
| Cross-cutting auth / project access           | [user_service ADR 0011](../apps/user_service/docs/adr/0011-project-membership.md)                                                                                                    |
| Push notifications                            | [user_service ADR 0009](../apps/user_service/docs/adr/0009-push-notifications-grpc.md)                                                                                               |

______________________________________________________________________

## Monorepo layout

```
ats-home-craft-python-service/
├── apps/
│   └── user_service/          # port 5000 — CRM / resident APIs
│       ├── app/
│       ├── docs/              # flow guides + ADRs
│       └── tests/
├── libs/                      # shared config, DB, middleware, utils
├── docs/                      # this index
└── requirements.txt           # local dev (full install)
```

**Shared libraries (`libs/`):**

| Package             | Role                                                            |
| ------------------- | --------------------------------------------------------------- |
| `shared_config`     | Pydantic settings (env, DB, Supabase, R2)                       |
| `shared_db`         | asyncpg pool, Supabase helpers                                  |
| `shared_middleware` | JWT auth middleware                                             |
| `shared_utils`      | Logging, responses, translations, telemetry, exception handlers |

______________________________________________________________________

## Run locally

Install once from the repo root:

```bash
pip install -r requirements.txt
```

**user_service (5000):**

```bash
uvicorn apps.user_service.app.main:app --host 0.0.0.0 --port 5000 --reload
```

Health check: `GET /health`

______________________________________________________________________

## Tests

```bash
ENVIRONMENT=test PYTHONPATH=. pytest apps/user_service/tests -q
```

______________________________________________________________________

## Docker

| Service      | Build                                                                  | Run            |
| ------------ | ---------------------------------------------------------------------- | -------------- |
| user_service | `docker build -f docker/user_service.Dockerfile -t ats-user-service .` | `-p 5000:5000` |

______________________________________________________________________

## Database & external docs

| Doc                            | Location                                                                                              |
| ------------------------------ | ----------------------------------------------------------------------------------------------------- |
| Supabase schemas (all domains) | [ats-home-craft-supabase/docs/](../../ats-home-craft-supabase/docs/)                                  |
| WOM DB schema                  | [work-order-management-schema.md](../../ats-home-craft-supabase/docs/work-order-management-schema.md) |
| Membership schema              | [membership-schema.md](../../ats-home-craft-supabase/docs/membership-schema.md)                       |

Work order management API docs and service code live in a **separate repository**; this monorepo only hosts the shared Postgres schema reference above.

______________________________________________________________________

## ADR index

Platform ADRs live under [apps/user_service/docs/adr/](../apps/user_service/docs/adr/README.md) — 0001–0014.

When adding a new ADR, place it in the service that owns the domain.
