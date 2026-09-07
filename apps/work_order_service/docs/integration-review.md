# Work Order Management — External Project Review & Integration Guide

|                  |                                                                                                                                                                                                                                                                                                                     |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Status**       | V1 implemented                                                                                                                                                                                                                                                                                                      |
| **Date**         | 2026-09-02                                                                                                                                                                                                                                                                                                          |
| **Source**       | `/Users/3embed/Downloads/order-management` (Cloud AI prototype)                                                                                                                                                                                                                                                     |
| **Target**       | ATS-Home-Craft (`ats-home-craft-python-service`)                                                                                                                                                                                                                                                                    |
| **Approach**     | `apps/work_order_service/` — mirror `user_service` structure                                                                                                                                                                                                                                                        |
| **Related docs** | [overview.md](./overview.md), [adr/README.md](./adr/README.md), [README.md](./README.md), [work-order-management-schema.md](../../../../ats-home-craft-supabase/docs/work-order-management-schema.md), [fee-flow.md](../../user_service/docs/fee-flow.md), [events-flow.md](../../user_service/docs/events-flow.md) |

______________________________________________________________________

## 1. Executive Summary

The **order-management** project is a **Work Order Management System (WOM)** for Indian property/facility management — not e-commerce orders. It covers:

- **Assets** (HVAC, DG sets, fire pumps, gym equipment)
- **Maintenance contracts (AMCs)** with automatic work-order generation
- **Work orders** assigned to vendors
- **Vendor invoices & payments**
- **Vendor portal** (token-based, frontend-only today)
- **Webhooks, audit logs, public API, MCP**

It was built as a **feature-rich MVP/prototype** with a React frontend and a standalone FastAPI backend. The domain logic is thoughtful and aligns well with facility operations for gated communities — a natural extension of ATS-Home-Craft.

**Verdict:** Worth adopting as a foundation, but **not production-ready as-is**. Before integration, address authentication, migrations, tests, and alignment with Home Craft's tenancy/auth/event patterns.

**Chosen approach:** Add WOM as a **separate deployable service** (`apps/work_order_service/`, port 5001) alongside `user_service`, following the **same folder structure and patterns** as `user_service` (api → services → repositories → schemas). Reuse existing `libs/shared_*` for config, DB, middleware, and logging. Extract to a standalone package later only if a second project needs it.

______________________________________________________________________

## 2. What Exists in the Prototype

### 2.1 Repository layout

```
order-management/
├── backend/                 # FastAPI + SQLAlchemy async (port 8000)
│   ├── app/
│   │   ├── main.py          # Lifespan, seeding, scheduler workers
│   │   ├── core/            # config, db, deps, router_factory, crud
│   │   ├── models/models.py # All ORM entities (~15 tables)
│   │   ├── schemas/         # Pydantic v2 request/response
│   │   ├── routers/         # 12 router modules
│   │   └── services/        # scheduler, events, hoa (CRM)
│   ├── alembic/             # Scaffold only — no version migrations
│   └── pyproject.toml
├── src/                     # React 19 + Vite + Zustand + Tailwind/shadcn
├── conductor/               # Product specs & implementation plans
└── dist/                    # Built frontend
```

### 2.2 Tech stack comparison

| Aspect        | order-management (prototype)                              | ATS-Home-Craft (current)                  |
| ------------- | --------------------------------------------------------- | ----------------------------------------- |
| Framework     | FastAPI                                                   | FastAPI                                   |
| Python        | ≥3.11                                                     | 3.13                                      |
| ORM           | SQLAlchemy 2.0 async                                      | asyncpg (raw SQL via repositories)        |
| Database      | PostgreSQL (prod) / SQLite (dev default)                  | PostgreSQL via Supabase                   |
| Migrations    | Alembic installed, **unused**; startup `ALTER TABLE` hack | Supabase SQL migrations                   |
| Auth          | **None on internal `/api/v1/*`**                          | Supabase JWT + Isometrik integration auth |
| Multi-tenancy | `tenant_id` query param, hardcoded default                | `organization_id` + `project_id`          |
| Events        | In-process webhooks + audit table                         | Kafka outbox + `events` table             |
| Notifications | None                                                      | gRPC push via notification-service        |
| File storage  | Data-URL JSON blobs                                       | R2/S3 presigned URLs                      |
| Tests         | **Zero**                                                  | Extensive unit + integration tests        |
| Deployment    | `start.sh` only                                           | Docker Compose, Datadog tracing           |
| External CRM  | House of Apps SDK (`houseofapps`)                         | Isometrik, Typesense, OpenAI              |

### 2.3 Domain entities

All entities use `TimestampMixin`, `SoftDeleteMixin`, and `TenantMixin`.

| Entity                  | Purpose                                                   |
| ----------------------- | --------------------------------------------------------- |
| `asset_categories`      | Hierarchical asset taxonomy (HVAC, Electrical, etc.)      |
| `custom_fields`         | Global or category-scoped dynamic fields                  |
| `assets`                | Physical equipment with warranty, location, custom values |
| `form_templates`        | Formily JSON Schema inspection checklists                 |
| `maintenance_contracts` | AMC terms → drives auto work-order generation             |
| `work_orders`           | Vendor tasks with state machine, timeline, recurrence     |
| `vendor_invoices`       | Line items, status lifecycle, document attachments        |
| `payments`              | Payment against approved invoices                         |
| `business_profile`      | Singleton company identity for PDFs                       |
| `pdf_templates`         | pdfme templates for work orders / receipts                |
| `trigger_configs`       | Webhook rules per entity/event                            |
| `audit_events`          | Field-level mutation history                              |
| `webhook_deliveries`    | HTTP delivery log with retries                            |
| `api_keys`              | SHA-256 hashed keys for public API                        |

### 2.4 Core business logic (strengths)

1. **Contract-driven scheduling** (`backend/app/services/scheduler.py`)

   - `visit_frequency` (Monthly, Quarterly, etc.) → auto-generates work orders
   - Configurable `auto_generate_lead_days`
   - Reconciles work orders on contract PATCH/DELETE
   - Recurring ad-hoc work orders with child cancellation

1. **Event pipeline** (`backend/app/services/events.py`)

   - Every mutation can emit audit events + webhook deliveries
   - Async retry with backoff (in-process queue)

1. **Generic CRUD factory** (`backend/app/core/router_factory.py`)

   - DRY list/get/create/update/delete for simpler entities
   - Custom routers override where business rules matter (contracts, work orders, invoices)

1. **Public integration surface**

   - `/api/public/v1/*` — API key auth (`Authorization: Bearer ats_...`)
   - `/api/mcp` — JSON-RPC 2.0 for AI agent integrations

1. **Vendor CRM integration** (`backend/app/services/hoa.py`)

   - House of Apps SDK with 5-minute cache + local fallback
   - Vendors are external entities (no local vendor table)

1. **Frontend**

   - Full FM dashboard: assets, contracts, work orders, invoices, payments, settings
   - Vendor portal with timeline, invoice submission, file uploads
   - Dynamic forms via Formily

______________________________________________________________________

## 3. API Surface Summary

### Internal API — `/api/v1/*` (no auth today)

| Prefix                                                   | Operations                   |
| -------------------------------------------------------- | ---------------------------- |
| `/asset-categories`                                      | CRUD                         |
| `/custom-fields`                                         | CRUD + scope filters         |
| `/assets`                                                | CRUD + search/filter         |
| `/form-templates`                                        | CRUD                         |
| `/vendors`                                               | List/search/create (HoA CRM) |
| `/business-profile`                                      | GET/PUT singleton            |
| `/pdf-templates`                                         | CRUD                         |
| `/contracts`                                             | CRUD + auto WO generation    |
| `/work-orders`                                           | CRUD + timeline append       |
| `/invoices`                                              | CRUD + timeline append       |
| `/payments`                                              | CRUD                         |
| `/scheduler/run`                                         | Manual scheduler trigger     |
| `/triggers`                                              | Webhook rule CRUD + test     |
| `/audit-events`, `/webhook-deliveries`, `/api-call-logs` | Read logs                    |
| `/settings/api-keys`                                     | Create/list/revoke keys      |
| `/test-echo`                                             | Dev webhook receiver         |

### Public API — `/api/public/v1/*` (API key required)

Work orders, contracts, invoices, payments — read/write for external systems.

### Health

`GET /health` — does not verify DB connectivity.

______________________________________________________________________

## 4. Critical Gaps & Issues

### 4.1 Security (must fix before any deployment)

| Severity     | Issue                                                                  | Recommendation                                                    |
| ------------ | ---------------------------------------------------------------------- | ----------------------------------------------------------------- |
| **Critical** | No auth on `/api/v1/*` — anyone can read/write all data                | Wire Supabase JWT (staff) + vendor token middleware               |
| **Critical** | Vendor portal not enforced server-side — `vendor_token` is client-only | Add `/api/v1/vendor/*` routes gated by token; filter WOs by token |
| **High**     | CORS `allow_origins=["*"]` + `allow_credentials=True`                  | Restrict to known frontend origins                                |
| **High**     | API key management unprotected on internal API                         | Require admin auth for `/settings/api-keys`                       |
| **High**     | Scheduler trigger unprotected                                          | Require internal service token or admin role                      |
| **High**     | Secrets in checked-in `.env` (HoA license key, app secret)             | Rotate credentials; never commit secrets                          |
| **Medium**   | Tenant isolation bypassed via `?tenant_id=`                            | Derive tenant from JWT claims, not query params                   |
| **Medium**   | Vendor tokens short/predictable (`tok-{12 hex}`)                       | Use `secrets.token_urlsafe(32)`; store hashed                     |
| **Medium**   | Webhook SSRF — user-supplied URLs with no blocklist                    | Block private IP ranges; allowlist domains                        |
| **Low**      | Audit headers (`X-ATS-Actor`, `X-ATS-Source`) are spoofable            | Derive actor from authenticated identity                          |

### 4.2 Production readiness

| Gap                                   | Detail                                                                    |
| ------------------------------------- | ------------------------------------------------------------------------- |
| **No tests**                          | Pytest configured but zero test files                                     |
| **No real migrations**                | Alembic has no `versions/`; schema drift handled by startup `ALTER TABLE` |
| **No Docker/K8s**                     | `start.sh` hardcodes a foreign path (`/home/user/workspaces/...`)         |
| **No file storage**                   | Invoice PDFs stored as data-URL JSON — not scalable                       |
| **No notifications**                  | No email/push on WO status changes                                        |
| **No rate limiting**                  | Public and internal APIs unprotected                                      |
| **No observability**                  | Basic logging only; no metrics/tracing                                    |
| **Scheduler not multi-instance safe** | In-process asyncio loop — duplicate WOs if scaled horizontally            |
| **Webhook queue not durable**         | Lost on process restart                                                   |
| **Health check incomplete**           | Does not ping database                                                    |

### 4.3 Code quality bugs (fix during port)

| Issue                  | Location                                                                                | Fix                              |
| ---------------------- | --------------------------------------------------------------------------------------- | -------------------------------- |
| Duplicate model field  | `VendorInvoiceModel.timeline` declared twice (lines 242 & 247)                          | Remove duplicate                 |
| Duplicate schema field | `WorkOrderCreate.recurring_end_date` twice in `crud_schemas.py`                         | Remove duplicate                 |
| Unused model           | `TimelineEventModel` / `work_order_timeline` table                                      | Use table or remove model        |
| Dead code              | `ApiCallLogModel` — read endpoint exists, nothing writes                                | Implement middleware or remove   |
| Missing dependencies   | `httpx`, `python-dateutil`, `houseofapps`, `aiosqlite` used but not in `pyproject.toml` | Add to dependencies              |
| Unused dependency      | `procrastinate` listed but never wired                                                  | Remove or implement job queue    |
| Hardcoded actor        | `FM_ACTOR = "Rohit Sharma"` in events service                                           | Derive from auth context         |
| Inconsistent audit     | Assets CRUD doesn't emit audit events                                                   | Standardize via event service    |
| MCP status update      | Bypasses timeline route, raw SQL update                                                 | Route through same service layer |

### 4.4 Dependency declaration gap

Fresh install from `pyproject.toml` alone will fail. Add:

```toml
"httpx>=0.27.0",
"python-dateutil>=2.9.0",
"houseofapps",  # pin version once known
"aiosqlite>=0.20.0",  # dev/SQLite fallback
```

______________________________________________________________________

## 5. Fit with ATS-Home-Craft

### 5.1 What Home Craft already has (do not duplicate)

| Feature             | Home Craft module                | WOM overlap                                      |
| ------------------- | -------------------------------- | ------------------------------------------------ |
| Maintenance billing | Fee invoices + scheduler         | Different domain — resident fees, not vendor AMC |
| Companies/contacts  | CRM (`contacts`, `companies`)    | Vendors could map to `companies`                 |
| Custom fields       | Entity custom fields in CRM      | Similar concept — could unify later              |
| Audit logs          | `audit_logs` service + decorator | Same pattern — reuse existing infra              |
| Webhooks            | `/v1/webhooks/*` inbound         | WOM adds outbound webhook delivery               |
| File uploads        | R2 presigned URLs                | WOM needs migration off data-URLs                |

### 5.2 What Home Craft lacks (WOM fills the gap)

- Asset register & categories
- AMC / maintenance contract lifecycle
- Work order state machine (Upcoming → In Progress → In Review → Completed)
- Vendor portal for field work + invoice submission
- Contract-driven preventive maintenance scheduling
- Vendor invoice approval workflow
- Payment tracking against vendor invoices

### 5.3 Tenancy mapping

| WOM concept                             | Home Craft equivalent                                       |
| --------------------------------------- | ----------------------------------------------------------- |
| `tenant_id` (e.g. `ats-greens-village`) | `organization_id`                                           |
| (not present)                           | `project_id` — scope assets/WOs per gated community         |
| `vendor_id` (HoA CRM)                   | `company_id` in CRM, or new `vendors` table                 |
| `location_id` on assets                 | `facility_id` or free-text location within project          |
| FM staff actor                          | `organization_members` with facility-management permissions |

**Recommendation:** Add `project_id` to all WOM tables. A single organization may manage multiple projects (communities).

______________________________________________________________________

## 6. Chosen Architecture — Separate Service (Mirror `user_service`)

WOM lives entirely in **`apps/work_order_service/`**, structured the same way as `user_service`. No separate domain lib at launch — keep it simple and consistent with the rest of the monorepo.

### 6.1 Monorepo layout

```
ats-home-craft-python-service/
├── apps/
│   ├── user_service/              # existing (port 5000)
│   └── work_order_service/        # NEW (port 5001)
│       ├── app/
│       │   ├── api/               # FastAPI routers
│       │   ├── services/          # business logic (scheduler, invoice lifecycle, events)
│       │   ├── db/repositories/   # asyncpg SQL
│       │   ├── schemas/           # Pydantic + enums
│       │   ├── dependencies/      # auth, db, project access
│       │   ├── config/            # app_settings.py
│       │   ├── main.py
│       │   ├── app_instance.py
│       │   ├── lifespan.py        # scheduler + webhook workers
│       │   └── locales/
│       ├── tests/
│       │   ├── unit/
│       │   └── integration/
│       ├── Dockerfile
│       └── requirements.txt
│
├── libs/
│   ├── shared_config/             # reused by work_order_service
│   ├── shared_db/
│   ├── shared_middleware/
│   └── shared_utils/
│
ats-home-craft-supabase/
└── supabase/migrations/           # WOM tables (work_order schema)
```

### 6.2 Layer responsibilities (same pattern as `user_service`)

| Layer            | Location               | Example                                                                 |
| ---------------- | ---------------------- | ----------------------------------------------------------------------- |
| **API**          | `app/api/`             | `work_orders.py`, `contracts.py`, `invoices.py`                         |
| **Services**     | `app/services/`        | `scheduler_service.py`, `work_orders_service.py`, `invoices_service.py` |
| **Repositories** | `app/db/repositories/` | `work_orders_repository.py`, `contracts_repository.py`                  |
| **Schemas**      | `app/schemas/`         | Request/response models, enums                                          |
| **Dependencies** | `app/dependencies/`    | JWT context, DB conn, staff project access                              |
| **Shared infra** | `libs/shared_*`        | Config, asyncpg pool, JWT middleware, logging                           |

### 6.3 Service boundaries

```
┌──────────────────┐         REST / Kafka         ┌──────────────────────┐
│  user_service    │ ◄──────────────────────────► │  work_order_service  │
│  (port 5000)     │                               │  (port 5001)         │
└────────┬─────────┘                               └──────────┬───────────┘
         │                                                     │
         ▼                                                     ▼
   public schema                                    work_order schema
   (orgs, contacts, units, companies)               (assets, contracts, WOs, invoices)
```

| Concern                                            | Owner                                                       |
| -------------------------------------------------- | ----------------------------------------------------------- |
| Orgs, users, JWT, permissions                      | `user_service`                                              |
| Assets, contracts, work orders, invoices, payments | `work_order_service`                                        |
| Vendors (companies)                                | `user_service` CRM — WOM stores `company_id` reference only |
| Push notifications                                 | WOM calls shared gRPC client via `libs/shared_utils`        |
| File uploads                                       | R2 presigned URLs via shared utils (same as `user_service`) |

**No cross-service DB reads.** `work_order_service` stores `organization_id`, `project_id`, and foreign refs (`company_id`, `contact_id`) — it does not join into `user_service` tables at query time.

### 6.4 Alternatives considered (not chosen)

| Option                              | Why not chosen                                                               |
| ----------------------------------- | ---------------------------------------------------------------------------- |
| Module inside `user_service`        | Bloats an already large service; harder to scale/deploy independently        |
| `libs/work_order/` + app from day 1 | Extra complexity before reuse is needed; can extract later                   |
| Fully separate repo from day 1      | Slower iteration; loses shared `libs/shared_*`                               |
| Copy Cloud AI backend as-is         | SQLAlchemy + no auth + no migrations — does not match Home Craft conventions |

### 6.5 Optional: extract for reuse later

If WOM is needed in a **second project**, extract portable code without a upfront split:

| Step   | Action                                                                                      |
| ------ | ------------------------------------------------------------------------------------------- |
| 1      | Copy `apps/work_order_service/` to the new project                                          |
| 2      | Adapt auth, config, and integrations for that host                                          |
| **Or** | Refactor `app/services/` + `app/schemas/` into `libs/work_order/` and publish/copy that lib |

No need to design ports/adapters until reuse is a concrete requirement.

______________________________________________________________________

## 7. Integration Design

### 7.1 Authentication

| Caller              | Auth mechanism                                                                                           |
| ------------------- | -------------------------------------------------------------------------------------------------------- |
| FM staff (admin UI) | Shared `JWTAuthMiddleware` from `libs/shared_middleware` → `organization_id` + `project_id` from session |
| Vendor portal       | Dedicated middleware: `?token=` or `X-Vendor-Token` → scoped to single work order                        |
| External systems    | Isometrik headers (`licenseKey`, `appSecret`) → `organization_id` (mirror `external_clients.py`)         |
| Service-to-service  | Internal API key header (`X-Internal-Service-Key`)                                                       |
| Public API / MCP    | API keys (from prototype; key management requires admin auth)                                            |
| Scheduler / workers | Internal service token (env var)                                                                         |

### 7.2 Database

**Recommended:** Same Postgres instance, **separate schema**:

```sql
CREATE SCHEMA work_order;
-- work_order.asset_categories, work_order.work_orders, etc.
```

- Migrations live in `ats-home-craft-supabase/supabase/migrations/`
- `work_order_service` uses **asyncpg** repositories (consistent with `user_service`)
- Port prototype business logic into `apps/work_order_service/app/services/`

**Alternative (not default):** Dedicated database — only if hard isolation or independent storage scaling is required.

### 7.3 Cross-service integration with `user_service`

Loose coupling first; tighten only where needed.

| Phase                     | Integration                                                                                                         |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| **1 — Independent**       | WOM runs on port 5001; frontend calls WOM directly; JWT validated locally (same Supabase JWT secret)                |
| **2 — Reference data**    | Vendor list via `GET user_service/v1/companies`; WOM stores `company_id` only                                       |
| **3 — Events (optional)** | WOM publishes `work_orders.*`, `invoices.*` via Kafka outbox; `user_service` subscribes for dashboard/notifications |
| **4 — Proxy (optional)**  | BFF routes under `user_service/v1/projects/{id}/work-orders` proxy to WOM — only if a single API gateway is needed  |

### 7.4 Event integration

Publish WOM lifecycle events via existing Kafka outbox:

| Event type                   | When                           |
| ---------------------------- | ------------------------------ |
| `work_orders.created`        | New WO from contract or ad-hoc |
| `work_orders.status_changed` | State transition               |
| `invoices.submitted`         | Vendor submits invoice         |
| `invoices.approved`          | FM approves                    |
| `payments.completed`         | Payment recorded               |

Subscribe to CRM events:

| Event type          | Action                                        |
| ------------------- | --------------------------------------------- |
| `companies.updated` | Refresh vendor cache                          |
| `contacts.created`  | (future) link resident-reported issues to WOs |

### 7.5 Notifications

On key transitions, call existing `PushNotificationService`:

| Trigger                               | Recipients                                         |
| ------------------------------------- | -------------------------------------------------- |
| WO assigned / due soon                | FM staff with `WORK_ORDER_MANAGEMENT_*` permission |
| Invoice submitted                     | FM finance team                                    |
| Invoice approved / revision requested | Vendor (email/SMS — new channel)                   |
| Payment completed                     | Vendor                                             |

### 7.6 File storage migration

Replace data-URL JSON with R2 presigned upload flow (same as tenant requests, move events):

1. Client requests presigned URL from Home Craft
1. Uploads file directly to R2
1. Stores `storage_path` on invoice/work order record

### 7.7 Permission model (new RBAC codes)

| Permission                      | Scope                                |
| ------------------------------- | ------------------------------------ |
| `WORK_ORDER_MANAGEMENT_VIEW`    | Read assets, contracts, WOs          |
| `WORK_ORDER_MANAGEMENT_EDIT`    | Create/update assets, contracts, WOs |
| `WORK_ORDER_MANAGEMENT_APPROVE` | Approve/reject vendor invoices       |
| `WORK_ORDER_MANAGEMENT_PAY`     | Record payments                      |

Vendor portal remains token-scoped — no RBAC, single-WO access only.

### 7.8 Prototype → monorepo port mapping

| From prototype                      | Destination                                                            |
| ----------------------------------- | ---------------------------------------------------------------------- |
| `backend/app/services/scheduler.py` | `app/services/scheduler_service.py`                                    |
| Invoice lifecycle, event rules      | `app/services/`                                                        |
| Pydantic schemas                    | `app/schemas/`                                                         |
| ORM models                          | Supabase migrations + `app/db/repositories/`                           |
| `router_factory.py`, routers        | `app/api/`                                                             |
| `hoa.py` vendor CRM                 | `app/services/vendors_service.py` → calls `user_service` companies API |
| In-process webhook worker           | `app/lifespan.py` (Phase 1) → Kafka/outbox later                       |
| React frontend (`src/`)             | Separate SPA — points at `:5001`; embed in admin UI later              |

### 7.9 Deployment

Match the existing `user_service` Docker pattern:

```yaml
# deployment/backend-services/work-order-service/docker-compose.yml
services:
  work-order-service:
    container_name: ats-home-craft-work-order-service
    image: appscrip007/ats-home-craft-work-order-service:latest
    ports:
      - "5001:5001"
    env_file:
      - .env
    networks:
      - ats_home_craft_network
```

- Own `Dockerfile` at `apps/work_order_service/Dockerfile`
- Entrypoint: `uvicorn apps.work_order_service.app.main:app --host 0.0.0.0 --port 5001`
- `PYTHONPATH=/app` (same as root monorepo Dockerfile)
- Health: `GET /health` with DB connectivity check
- Shared network: `ats_home_craft_network`

Local dev:

```bash
uvicorn apps.work_order_service.app.main:app --host 0.0.0.0 --port 5001 --reload
```

______________________________________________________________________

## 8. Schema Migration Notes

Full table definitions, enums, and JSONB shapes: [work-order-management-schema.md](../../../../ats-home-craft-supabase/docs/work-order-management-schema.md).

When porting to Supabase migrations, follow Home Craft conventions:

- Tables live under the **`work_order`** Postgres schema
- `organization_id UUID NOT NULL REFERENCES public.organizations(id)`
- `project_id UUID NOT NULL REFERENCES public.projects(id)` — **add this** (missing in prototype)
- `record_status` enum for soft delete (instead of `deleted` boolean)
- `created_at`, `updated_at` with timezone
- RLS enabled, policies deferred (backend `service_role`)
- Use `jsonb` for timeline, line_items, custom_field_values
- Store file paths as `text[]`, not blobs

**Suggested migration order:**

1. `asset_categories`, `custom_fields`
1. `assets`
1. `form_templates`
1. `maintenance_contracts`
1. `work_orders`
1. `vendor_invoices`, `payments`
1. `trigger_configs`, `audit_events`, `webhook_deliveries`
1. `business_profile`, `pdf_templates`, `api_keys`

______________________________________________________________________

## 9. Implementation Phases

| Phase                 | Deliverable                                                                             | Duration  |
| --------------------- | --------------------------------------------------------------------------------------- | --------- |
| **0 — Scaffold**      | `apps/work_order_service/` skeleton (mirror `user_service`), health, config, Dockerfile | 2–3 days  |
| **1 — Schema**        | Supabase migrations for core tables (assets → contracts → WOs → invoices)               | 3–5 days  |
| **2 — Core services** | Port scheduler + WO/invoice lifecycle into `app/services/` + unit tests                 | 1–2 weeks |
| **3 — API**           | CRUD routes, JWT auth, vendor token middleware                                          | 1 week    |
| **4 — Hardening**     | R2 files, audit, webhooks, scheduler lock, integration tests                            | 1 week    |
| **5 — Integrate**     | Vendor sync via `user_service`, push notifications, optional Kafka                      | 1 week    |
| **6 — Frontend**      | Point Cloud AI React UI at new service (or embed in admin later)                        | parallel  |

### Open decisions (confirm before Phase 0)

| Decision      | Default                                             |
| ------------- | --------------------------------------------------- |
| Service name  | `work_order_service`                                |
| Port          | `5001`                                              |
| DB access     | asyncpg (match Home Craft)                          |
| Frontend      | Keep Cloud AI React as separate SPA initially       |
| Vendor master | Reference `user_service` companies via `company_id` |

______________________________________________________________________

## 10. Pre-Integration Checklist

Use this before copying any code into Home Craft:

### Must do (blockers)

- [ ] Scaffold `apps/work_order_service/` (mirror `user_service` layout)
- [ ] Add authentication to all internal routes
- [ ] Implement server-side vendor token gating
- [ ] Rotate/remove secrets from prototype `.env`
- [ ] Fix duplicate model/schema fields from prototype
- [ ] Replace startup `ALTER TABLE` hack with Supabase migrations (`work_order` schema)
- [ ] Add unit tests for scheduler + WO lifecycle in `apps/work_order_service/tests/`
- [ ] Dockerize `work_order_service` (port 5001)

### Should do (before production)

- [ ] Add `project_id` to all entities
- [ ] Migrate file storage to R2
- [ ] Wire audit events to Home Craft audit logger
- [ ] Add Kafka outbox for WOM events
- [ ] Restrict CORS origins
- [ ] Add rate limiting on public API
- [ ] Health check with DB ping
- [ ] Scheduler locking for multi-instance (Postgres advisory lock or Redis)

### Nice to have

- [ ] Unify vendors with CRM `companies`
- [ ] Push notifications on WO/invoice status
- [ ] Replace HoA SDK with Home Craft companies API
- [ ] Extract `app/services/` + `app/schemas/` to standalone package if second project needs WOM
- [ ] Port frontend into Home Craft admin or ship as separate SPA

______________________________________________________________________

## 11. Key Files to Reference During Port

### Prototype (source)

| File                                  | Why                                               |
| ------------------------------------- | ------------------------------------------------- |
| `backend/app/services/scheduler.py`   | Core contract → WO generation logic               |
| `backend/app/services/events.py`      | Audit + webhook pipeline                          |
| `backend/app/core/router_factory.py`  | Generic CRUD pattern                              |
| `backend/app/routers/crud_routers.py` | Custom business routes for contracts/WOs/invoices |
| `backend/app/models/models.py`        | Full entity definitions                           |
| `backend/app/schemas/crud_schemas.py` | Request/response shapes                           |
| `conductor/index.md`                  | Product vision and terminology                    |
| `src/lib/store.ts`                    | Frontend state + API call patterns                |

### Home Craft (integration targets)

| File                                                  | Why                                                  |
| ----------------------------------------------------- | ---------------------------------------------------- |
| `apps/user_service/app/main.py`                       | Service bootstrap pattern (middleware, health, CORS) |
| `apps/user_service/app/app_instance.py`               | FastAPI app factory + lifespan wiring                |
| `apps/user_service/app/lifespan.py`                   | Background worker startup/shutdown pattern           |
| `apps/user_service/app/api/external_clients.py`       | Isometrik integration auth pattern                   |
| `apps/user_service/app/services/event_service.py`     | Kafka outbox pattern                                 |
| `apps/user_service/app/dependencies/external_auth.py` | Org resolution from external credentials             |
| `libs/shared_middleware/jwt_auth.py`                  | Shared JWT middleware for WOM                        |
| `libs/shared_utils/fastapi_app.py`                    | Rate limiting + app factory                          |
| `apps/user_service/docs/events-flow.md`               | Event publishing conventions                         |
| `apps/user_service/docs/fee-flow.md`                  | Closest existing billing/scheduler pattern           |
| `apps/user_service/docs/adr/0007-tenant-requests.md`  | ADR template for new module                          |

### New files (to create)

| Path                                                        | Purpose                        |
| ----------------------------------------------------------- | ------------------------------ |
| `apps/work_order_service/app/main.py`                       | Service entrypoint             |
| `apps/work_order_service/app/app_instance.py`               | FastAPI app factory + lifespan |
| `apps/work_order_service/app/lifespan.py`                   | Scheduler + webhook workers    |
| `apps/work_order_service/app/services/scheduler_service.py` | Contract → WO generation       |
| `apps/work_order_service/app/api/routes.py`                 | Router aggregation             |
| `apps/work_order_service/Dockerfile`                        | Container build                |
| `apps/work_order_service/requirements.txt`                  | Service dependencies           |

______________________________________________________________________

## 12. Risks & Mitigations

| Risk                                       | Impact                               | Mitigation                                               |
| ------------------------------------------ | ------------------------------------ | -------------------------------------------------------- |
| Deploying prototype without auth           | Data breach                          | Do not deploy standalone until auth is wired             |
| Schema drift during port                   | Migration failures                   | Write Supabase migrations first; no `create_all` in prod |
| Vendor data in external CRM only           | Broken references if HoA unavailable | Local vendor cache table with sync job                   |
| Scheduler duplicates at scale              | Duplicate work orders                | Postgres advisory lock or dedicated worker               |
| Frontend/backend API mismatch              | Broken UI after port                 | Version API or adapt frontend incrementally              |
| Scope creep (unify custom fields with CRM) | Delays launch                        | Defer unification to later phase                         |

______________________________________________________________________

## 13. Decision Summary

| Question                                  | Recommendation                                                                  |
| ----------------------------------------- | ------------------------------------------------------------------------------- |
| Is the prototype worth using?             | **Yes** — domain logic and UI are a strong starting point                       |
| Use as-is?                                | **No** — security and infra gaps are blockers                                   |
| Where does it live?                       | **`apps/work_order_service/`** — separate deployable service (port 5001)        |
| Structure?                                | **Mirror `user_service`** — `api/`, `services/`, `db/repositories/`, `schemas/` |
| Separate domain lib (`libs/work_order/`)? | **Not at launch** — extract later if a second project needs it                  |
| Merge into `user_service`?                | **No** — keep separate for independent scaling and deployment                   |
| Reuse WOM frontend?                       | **Yes initially** — separate SPA pointing at `:5001`                            |
| Reuse fee invoice module?                 | **No** — different domain (resident fees vs vendor AMC)                         |
| Map vendors to CRM companies?             | **Yes** — WOM stores `company_id`; master data in `user_service`                |
| Database                                  | Same Postgres, **`work_order` schema**, asyncpg repositories                    |

______________________________________________________________________

## Appendix A — Prototype Environment Variables

| Variable                     | Purpose                 | Default                            |
| ---------------------------- | ----------------------- | ---------------------------------- |
| `DATABASE_URL`               | Async DB connection     | `sqlite+aiosqlite:///./ats_dev.db` |
| `DATABASE_URL_SYNC`          | Sync URL for Alembic    | `sqlite:///./ats_dev.db`           |
| `DEBUG`                      | SQL echo + `create_all` | `true`                             |
| `DEFAULT_TENANT_ID`          | Fallback tenant         | `ats-greens-village`               |
| `SCHEDULER_ENABLED`          | Background scheduler    | `true`                             |
| `SCHEDULER_INTERVAL_MINUTES` | Tick interval           | `60`                               |
| `HOA_LICENSE_KEY`            | House of Apps CRM       | `""`                               |
| `HOA_APP_SECRET`             | HoA auth                | `""`                               |
| `HOA_BASE_URL`               | HoA API                 | `https://api.houseofapps.ai`       |

## Appendix B — Work Order State Machine

```
Upcoming → In Progress → In Review → Completed
                ↓              ↓
           Terminated      Terminated
```

Invoice lifecycle: `submitted → revision_requested → resubmitted → approved → paid` (or `rejected`).

Contract statuses: `active`, `expired`, `terminated`.

## Appendix C — Conductor Tracks (prototype specs)

The prototype's best internal documentation lives in `conductor/`:

- `contract-lifecycle` — Auto WO generation from contracts
- `contract-forms` — Form template inheritance
- `vendor-portal-live` — Vendor portal backend integration
- `invoice-lifecycle-ux` — Invoice approval workflow
- `platform-triggers-api` — Webhook triggers

Read these before implementing each phase.
