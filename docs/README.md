# Python Service — Documentation

> **Repo:** `ats-home-craft-python-service`
> **Layout:** Monorepo with shared `libs/` and one FastAPI app per service under `apps/`

This folder is the **top-level documentation index**. Detailed flow guides, API notes, and ADRs live inside each service's `docs/` folder (same pattern for both services).

______________________________________________________________________

## Services

| Service                | Port | Purpose                                                                    | Documentation                                                              |
| ---------------------- | ---- | -------------------------------------------------------------------------- | -------------------------------------------------------------------------- |
| **user_service**       | 5000 | CRM & resident platform — auth, contacts, membership, passes, fees, events | [apps/user_service/docs/](../apps/user_service/docs/README.md)             |
| **work_order_service** | 5001 | Facility management — assets, AMC contracts, work orders, vendor invoices  | [apps/work_order_service/docs/](../apps/work_order_service/docs/README.md) |

Service READMEs (run/test/Docker quick start):

- [apps/user_service/README.md](../apps/user_service/README.md)
- [apps/work_order_service/README.md](../apps/work_order_service/README.md)

______________________________________________________________________

## Start here

| If you are working on…                        | Read                                                                                                                                                                                 |
| --------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Resident onboarding, membership, passes, fees | [user_service docs → contact-onboarding-flow](../apps/user_service/docs/contact-onboarding-flow.md), [membership-architecture](../apps/user_service/docs/membership-architecture.md) |
| Work orders, contracts, vendor portal         | [work_order_service docs → overview](../apps/work_order_service/docs/overview.md), [integration-review](../apps/work_order_service/docs/integration-review.md)                       |
| Cross-cutting auth / project access           | [user_service ADR 0011](../apps/user_service/docs/adr/0011-project-membership.md)                                                                                                    |
| Push notifications from either service        | [user_service ADR 0009](../apps/user_service/docs/adr/0009-push-notifications-grpc.md)                                                                                               |

______________________________________________________________________

## Monorepo layout

```
ats-home-craft-python-service/
├── apps/
│   ├── user_service/          # port 5000 — CRM / resident APIs
│   │   ├── app/
│   │   ├── docs/              # flow guides + ADRs
│   │   └── tests/
│   └── work_order_service/    # port 5001 — WOM APIs
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

## Cross-service dependencies

`work_order_service` imports shared code from `user_service` at runtime (not via HTTP):

| WOM usage                        | user_service module                 |
| -------------------------------- | ----------------------------------- |
| Staff route guards & permissions | `app.utils.common_utils`            |
| Asset custom fields              | `app.services.custom_field_service` |
| Presigned URL OpenAPI model      | `app.schemas.presigned_url`         |

WOM `requirements.txt` includes `-r ../user_service/requirements.txt` for this reason.

Platform ADRs that apply to **both** services (membership, push) live under [user_service/docs/adr/](../apps/user_service/docs/adr/README.md). WOM-specific ADRs live under [work_order_service/docs/adr/](../apps/work_order_service/docs/adr/README.md).

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

**work_order_service (5001):**

```bash
uvicorn apps.work_order_service.app.main:app --host 0.0.0.0 --port 5001 --reload
```

Health checks: `GET /health` on each service.

______________________________________________________________________

## Tests

```bash
# user_service
ENVIRONMENT=test PYTHONPATH=. pytest apps/user_service/tests -q

# work_order_service
ENVIRONMENT=test PYTHONPATH=. pytest apps/work_order_service/tests -q
```

______________________________________________________________________

## Docker

| Service            | Build                                                                              | Run            |
| ------------------ | ---------------------------------------------------------------------------------- | -------------- |
| user_service       | `docker build -f docker/user_service.Dockerfile -t ats-user-service .`             | `-p 5000:5000` |
| work_order_service | `docker build -f docker/work_order_service.Dockerfile -t ats-work-order-service .` | `-p 5001:5001` |

______________________________________________________________________

## Database & external docs

| Doc                            | Location                                                                                              |
| ------------------------------ | ----------------------------------------------------------------------------------------------------- |
| Supabase schemas (all domains) | [ats-home-craft-supabase/docs/](../../ats-home-craft-supabase/docs/)                                  |
| WOM DB schema                  | [work-order-management-schema.md](../../ats-home-craft-supabase/docs/work-order-management-schema.md) |
| Membership schema              | [membership-schema.md](../../ats-home-craft-supabase/docs/membership-schema.md)                       |

______________________________________________________________________

## ADR index (by service)

| Service                  | ADR folder                                                                                     |
| ------------------------ | ---------------------------------------------------------------------------------------------- |
| user_service (platform)  | [apps/user_service/docs/adr/](../apps/user_service/docs/adr/README.md) — 0001–0014             |
| work_order_service (WOM) | [apps/work_order_service/docs/adr/](../apps/work_order_service/docs/adr/README.md) — 0001–0006 |

When adding a new ADR, place it in the **service that owns the domain**. Link platform-wide decisions from the other service's docs when relevant.
